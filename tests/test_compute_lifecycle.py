"""A remote job's fate is a claim, and every claim here needs evidence.

Four false claims this file pins down, each of which the manager used to make
without anything to back it:

  * **"the provider refused"** — the helper died before writing a reply, which
    says nothing at all about whether the remote op landed. It was classified
    `transient`, read as an explicit rejection, and persisted as terminal
    `failed`. Reconcile skips terminal rows, so a job the provider had already
    accepted became invisible while it kept running and kept billing.
  * **"it is cancelled"** — `close()` wrote `cancelled` over every live job
    without signalling an ssh job at all, and after a restart without even
    attempting the byoc terminate, because the sandbox map is in-memory.
  * **"this is its terminal state"** — two threads could both read `running`,
    both pass the transition check, and then overwrite each other, so a
    `cancelled` could land on top of a `succeeded`.
  * **"it timed out"** — inferred from exit code 124, which a command is free
    to return with no deadline armed at all.

The last one is proved against a real shell in
``test_compute_trust_boundary.py``, where the harness lives.
"""
import sqlite3
import threading
import types
from pathlib import Path

import pytest

from openai4s.compute import states
from openai4s.compute.manager import ComputeError, ComputeManager
from openai4s.config import Config
from openai4s.storage.compute_jobs import IllegalTransition
from openai4s.store import get_store


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "skills").mkdir()
    return types.SimpleNamespace(
        data_dir=tmp_path,
        skills_dir=tmp_path / "skills",
        db_path=Config(data_dir=tmp_path).db_path,
    )


@pytest.fixture
def byoc(cfg, tmp_path):
    d = cfg.skills_dir / "remote-compute-fake"
    d.mkdir()
    (d / "provider.json").write_text('{"id": "fake"}', encoding="utf-8")
    (d / "provider.py").write_text("PROVIDER = object()\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return ComputeManager(cfg, workspace=workspace)


class _Proc:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# --------------------------------------------------------------------------
# the helper transport, driven for real rather than stubbed at _run_helper
# --------------------------------------------------------------------------


class _Stdin:
    """Popen.stdin is written to and closed; nothing reads it back."""

    def write(self, _data):
        return None

    def close(self):
        return None


def _fake_popen(behaviour):
    """Stand in for the confined helper process.

    ``behaviour(op, stage)`` does whatever that helper would have done to the
    stage dir — write ``reply.json``, write ``sandbox_id``, or nothing at all —
    and returns the process exit code. Stubbing at ``subprocess.Popen`` rather
    than at ``_run_helper`` is deliberate: the whole defect lived in how
    ``_run_helper`` *classifies* what the stage dir does and does not contain.
    """

    class _Helper:
        def __init__(self, argv, **_kw):
            self.stdin = _Stdin()
            # ... "oneshot" <provider_py> <op> <stage> <expect_confined>, with
            # an OS-confinement wrapper possibly prepended. Located by the
            # marker rather than by index, so applying a boundary does not
            # silently shift what this stub thinks it is being asked to do.
            marker = argv.index("oneshot")
            self._op, self._stage = argv[marker + 2], Path(argv[marker + 3])
            self.returncode = 0

        def wait(self, timeout=None):
            self.returncode = behaviour(self._op, self._stage)
            return self.returncode

        def kill(self):
            return None

    return _Helper


def _reply(stage: Path, payload: dict) -> None:
    import json

    (stage / "reply.json").write_text(json.dumps(payload), encoding="utf-8")


def test_a_helper_that_died_before_replying_is_unknown_not_failed(
    byoc, monkeypatch, cfg
):
    """The headline regression.

    A helper that exits without a reply may have created the sandbox, may have
    submitted the job, or may have done neither — the one thing it did not do
    is tell us. Recording that as terminal `failed` is a claim about the remote
    that nothing supports, and it is the *expensive* direction to be wrong in:
    reconcile only revisits live rows, so a job the provider accepted keeps
    running and keeps billing with nothing left that will ever look for it.
    """

    def behaviour(op, stage):
        return 1  # exits without writing reply.json

    monkeypatch.setattr(
        "openai4s.compute.manager.subprocess.Popen", _fake_popen(behaviour)
    )
    with pytest.raises(ComputeError) as error:
        byoc.submit({"provider": "byoc:fake", "command": "train.py"})
    assert error.value.error_kind == "unknown_state"
    assert error.value.indeterminate is True

    row = get_store(cfg.db_path).list_compute_jobs()[0]
    assert row["status"] == states.UNKNOWN
    assert row["termination_reason"] == states.REASON_SUBMIT_INDETERMINATE
    # Live, so it holds a concurrency slot and reconcile keeps surfacing it.
    assert byoc._live_count() == 1


def test_an_explicit_provider_refusal_stays_a_definite_failure(byoc, monkeypatch, cfg):
    """The other direction matters just as much.

    A written reply is the helper speaking for itself: it reached the provider
    and the provider said no. Turning every failure into `unknown` would fill
    reconcile with rows that need no reconciling and bury the ones that do.
    """

    def behaviour(op, stage):
        _reply(stage, {"ok": False, "kind": "invalid_request", "msg": "no such gpu"})
        return 0

    monkeypatch.setattr(
        "openai4s.compute.manager.subprocess.Popen", _fake_popen(behaviour)
    )
    with pytest.raises(ComputeError) as error:
        byoc.submit({"provider": "byoc:fake", "command": "train.py"})
    assert error.value.indeterminate is False

    row = get_store(cfg.db_path).list_compute_jobs()[0]
    assert row["status"] == states.FAILED
    assert row["termination_reason"] == states.REASON_SUBMIT_REJECTED
    assert byoc._live_count() == 0


def test_a_sandbox_the_dying_helper_named_is_recorded(byoc, monkeypatch, cfg):
    """`_op_create` writes `stage/sandbox_id` the instant the provider returns
    one — before the ownership read-back, before `reply.json` — precisely so a
    helper that dies mid-op still leaves the host able to name what it created.

    Nothing read that file, so a create that landed and then crashed produced
    an unnameable, unterminatable, billing sandbox.
    """

    def behaviour(op, stage):
        (stage / "sandbox_id").write_text("sbx-orphan", encoding="utf-8")
        return 1  # dies before reply.json

    monkeypatch.setattr(
        "openai4s.compute.manager.subprocess.Popen", _fake_popen(behaviour)
    )
    with pytest.raises(ComputeError) as error:
        byoc.submit({"provider": "byoc:fake", "command": "train.py"})
    assert error.value.sandbox_id == "sbx-orphan"

    row = get_store(cfg.db_path).list_compute_jobs()[0]
    assert row["status"] == states.UNKNOWN
    assert row["sandbox_id"] == "sbx-orphan"
    assert row["receipt"] == "sbx-orphan", "the receipt is what terminate needs"


def test_reconcile_flags_an_indeterminate_submit_as_an_orphan_risk(
    byoc, monkeypatch, cfg
):
    """A submit whose outcome was never established is the row that costs
    money. It must not read like an ordinary running job."""

    def behaviour(op, stage):
        (stage / "sandbox_id").write_text("sbx-orphan", encoding="utf-8")
        return 1

    monkeypatch.setattr(
        "openai4s.compute.manager.subprocess.Popen", _fake_popen(behaviour)
    )
    with pytest.raises(ComputeError):
        byoc.submit({"provider": "byoc:fake", "command": "train.py"})

    report = ComputeManager(cfg).reconcile()
    assert report["orphan_risk_count"] == 1
    entry = report["recovered"][0]
    assert entry["orphan_risk"] is True
    assert entry["sandbox_id"] == "sbx-orphan"
    assert "never established" in entry["hint"]


# --------------------------------------------------------------------------
# close(): a handle you release is not a job you stopped
# --------------------------------------------------------------------------


@pytest.fixture
def ssh_job(cfg, monkeypatch):
    monkeypatch.setattr(
        "openai4s.compute.manager.subprocess.run",
        lambda *a, **k: _Proc(0, b"OPENAI4S_JOB 4242 4200\n"),
        raising=True,
    )
    manager = ComputeManager(cfg)
    out = manager.submit({"provider": "ssh:lab", "command": "sleep 600"})
    return manager, out["job_id"]


def test_close_signals_the_ssh_job_it_claims_to_have_cancelled(
    ssh_job, monkeypatch, cfg
):
    """`close()` never sent anything to an ssh host at all — it just wrote
    `cancelled`. The job kept running on the user's allocation."""
    manager, job_id = ssh_job
    seen = {}

    def fake_run(argv, **kw):
        seen["script"] = argv[2]
        return _Proc(0)

    monkeypatch.setattr("openai4s.compute.manager.subprocess.run", fake_run)
    out = manager.close({"provider": "ssh:lab", "job_ids": [job_id]})

    assert out["released"] == [job_id]
    assert "script" in seen, "close must actually signal the remote"
    assert "4200" in seen["script"], "the recorded process group is what we signal"
    assert get_store(cfg.db_path).get_compute_job(job_id)["status"] == states.CANCELLED


def test_close_does_not_claim_a_cancel_it_could_not_deliver(ssh_job, monkeypatch, cfg):
    """`cancelled` is terminal and reconcile skips terminal rows, so writing it
    over a job we failed to stop does not merely mislabel the job — it deletes
    it from everything that would ever have found it again."""
    manager, job_id = ssh_job
    monkeypatch.setattr(
        "openai4s.compute.manager.subprocess.run",
        lambda *a, **k: _Proc(1, b"", b"process group survived SIGKILL"),
    )
    out = manager.close({"provider": "ssh:lab", "job_ids": [job_id]})

    assert out["released"] == []
    assert out["unreleased"][0]["job_id"] == job_id
    assert out["error_kind"] == "unknown_state"
    assert out["sandbox_released"] is False

    row = get_store(cfg.db_path).get_compute_job(job_id)
    assert row["status"] == states.RUNNING, "still live, still reconcilable"
    assert "could not confirm" in (row["reason"] or "")
    assert manager._live_count() == 1, "and it still holds its slot"


def test_close_is_honest_when_the_host_is_unreachable(ssh_job, monkeypatch, cfg):
    import subprocess as _subprocess

    manager, job_id = ssh_job

    def boom(*a, **k):
        raise _subprocess.TimeoutExpired(cmd="ssh", timeout=45)

    monkeypatch.setattr("openai4s.compute.manager.subprocess.run", boom)
    out = manager.close({"provider": "ssh:lab", "job_ids": [job_id]})
    assert out["unreleased"][0]["job_id"] == job_id
    assert get_store(cfg.db_path).get_compute_job(job_id)["status"] == states.RUNNING


def test_close_terminates_a_sandbox_it_only_knows_from_the_job_row(
    byoc, monkeypatch, cfg
):
    """After a restart `_sandboxes` is empty, so `close()` had nothing to
    terminate and released nothing — while writing `cancelled` over the jobs,
    which is how a container bills unnoticed with the ledger saying it was
    stopped. The durable job row is the surviving name."""

    def submit_ok(op, stage):
        if op == "create":
            _reply(stage, {"ok": True, "sandbox_id": "sbx-9"})
        else:
            _reply(stage, {"ok": True})
        return 0

    monkeypatch.setattr(
        "openai4s.compute.manager.subprocess.Popen", _fake_popen(submit_ok)
    )
    out = byoc.submit({"provider": "byoc:fake", "command": "train.py"})
    job_id = out["job_id"]

    restarted = ComputeManager(cfg)
    assert restarted._sandboxes == {}, "the warm map does not survive a restart"

    terminated = []

    def terminate_ok(op, stage):
        import json

        terminated.append(json.loads((stage / "req.json").read_text())["sandbox_id"])
        _reply(stage, {"ok": True})
        return 0

    monkeypatch.setattr(
        "openai4s.compute.manager.subprocess.Popen", _fake_popen(terminate_ok)
    )
    result = restarted.close({"provider": "byoc:fake", "job_ids": [job_id]})

    assert terminated == ["sbx-9"]
    assert result["released"] == [job_id]
    assert get_store(cfg.db_path).get_compute_job(job_id)["status"] == states.CANCELLED


def test_close_transitions_every_live_job_on_the_sandbox_it_terminated(
    byoc, monkeypatch, cfg
):
    """Codex P1: byoc jobs share one sandbox per provider, and close releases
    every sandbox the manager can name — including one carrying live jobs the
    caller never listed (a restart, or a second handle, leaves such rows).
    Transitioning only the named targets left the omitted job live in memory and
    in the ledger: it kept a concurrency slot and later polls addressed a sandbox
    that no longer exists."""

    def submit_ok(op, stage):
        if op == "create":
            _reply(stage, {"ok": True, "sandbox_id": "sbx-shared"})
        else:
            _reply(stage, {"ok": True})
        return 0

    monkeypatch.setattr(
        "openai4s.compute.manager.subprocess.Popen", _fake_popen(submit_ok)
    )
    first = byoc.submit({"provider": "byoc:fake", "command": "a.py"})["job_id"]
    second = byoc.submit({"provider": "byoc:fake", "command": "b.py"})["job_id"]
    # Both ride the same warm sandbox.
    assert byoc._jobs[first]["sandbox_id"] == byoc._jobs[second]["sandbox_id"]
    assert byoc._live_count() == 2

    def terminate_ok(op, stage):
        _reply(stage, {"ok": True})
        return 0

    monkeypatch.setattr(
        "openai4s.compute.manager.subprocess.Popen", _fake_popen(terminate_ok)
    )
    # Close naming ONLY the first job — but the shared sandbox is destroyed.
    result = byoc.close({"provider": "byoc:fake", "job_ids": [first]})

    store = get_store(cfg.db_path)
    assert store.get_compute_job(first)["status"] == states.CANCELLED
    assert (
        store.get_compute_job(second)["status"] == states.CANCELLED
    ), "a live job riding the terminated sandbox was left live in the ledger"
    assert second in result["released"]
    assert byoc._live_count() == 0, "the omitted job kept its concurrency slot"


def test_cancel_refuses_a_byoc_job_with_no_sandbox_to_terminate(byoc, cfg):
    """Nothing to signal is not the same as nothing running."""
    store = get_store(cfg.db_path)
    store.create_compute_job(
        job_id="job-nameless", provider="byoc:fake", status=states.UNKNOWN
    )
    manager = ComputeManager(cfg)
    with pytest.raises(ComputeError) as error:
        manager.cancel({"job_id": "job-nameless"})
    assert error.value.error_kind == "unknown_state"
    assert store.get_compute_job("job-nameless")["status"] == states.UNKNOWN


# --------------------------------------------------------------------------
# the status write itself
# --------------------------------------------------------------------------


def test_a_terminal_state_cannot_be_clobbered_by_a_racing_writer(cfg, monkeypatch):
    """The read that validates a transition and the write that performs it are
    one critical section, and the write is conditional on what the read saw.

    Simulated precisely: another writer lands on the row *between* our check
    and our UPDATE. Before, the UPDATE had no predicate on status, so it
    overwrote whatever it found — a `cancelled` could bury a `succeeded`, and
    the ledger would claim an outcome that never happened.
    """
    store = get_store(cfg.db_path)
    store.create_compute_job(
        job_id="job-race", provider="ssh:lab", status=states.RUNNING
    )

    from openai4s.storage import compute_jobs as repo_module

    real_check = repo_module.check_transition
    interleaved = {"done": False}

    def check_then_interleave(job_id, current, requested):
        real_check(job_id, current, requested)
        if not interleaved["done"]:
            interleaved["done"] = True
            other = sqlite3.connect(str(cfg.db_path))
            try:
                other.execute(
                    "UPDATE compute_jobs SET status=? WHERE job_id=?",
                    (states.SUCCEEDED, "job-race"),
                )
                other.commit()
            finally:
                other.close()

    monkeypatch.setattr(repo_module, "check_transition", check_then_interleave)

    with pytest.raises(IllegalTransition):
        store.update_compute_job("job-race", status=states.CANCELLED)
    assert store.get_compute_job("job-race")["status"] == states.SUCCEEDED


def test_only_one_of_two_concurrent_terminal_writes_wins(cfg):
    """The broader invariant, under real threads: a result and a cancel racing
    to finish the same job must not both succeed."""
    store = get_store(cfg.db_path)
    store.create_compute_job(
        job_id="job-both", provider="ssh:lab", status=states.RUNNING
    )

    start = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def write(status):
        start.wait(timeout=5)
        try:
            store.update_compute_job("job-both", status=status)
        except IllegalTransition:
            with lock:
                outcomes.append(f"refused:{status}")
        else:
            with lock:
                outcomes.append(f"wrote:{status}")

    threads = [
        threading.Thread(target=write, args=(states.SUCCEEDED,)),
        threading.Thread(target=write, args=(states.CANCELLED,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    wrote = [o for o in outcomes if o.startswith("wrote:")]
    assert len(wrote) == 1, f"exactly one terminal write may land, got {outcomes}"
    assert store.get_compute_job("job-both")["status"] == wrote[0].split(":", 1)[1]


def test_a_status_write_on_a_missing_job_is_a_no_op(cfg):
    store = get_store(cfg.db_path)
    assert store.update_compute_job("job-gone", status=states.CANCELLED) is None


# --------------------------------------------------------------------------
# the harvest: where it lands, and what counts as delivered
# --------------------------------------------------------------------------


def test_the_harvest_root_is_the_session_workspace(byoc, tmp_path):
    """`host.save_artifact` resolves through the Host file service, which only
    accepts paths inside the session workspace. Harvesting to the global data
    dir made the documented next step — publish the featured file — fail with
    "path escapes the workspace" on every normal job."""
    assert byoc._hpc_root == tmp_path / "workspace" / "hpc"


def test_a_manager_without_a_workspace_still_harvests(cfg):
    """The CLI has no session workspace; the data dir remains right for it."""
    assert ComputeManager(cfg)._hpc_root == Path(cfg.data_dir) / "hpc"


def test_a_harvested_file_that_cannot_be_read_is_not_a_delivered_output(
    byoc, monkeypatch, cfg
):
    """rc==0, the file is present, and nothing can vouch for a single byte of
    it. `reconcile` matched on path alone, so this reported `succeeded`."""
    import os

    if os.geteuid() == 0:
        pytest.skip("root bypasses the permission bits this test relies on")

    def behaviour(op, stage):
        if op == "create":
            _reply(stage, {"ok": True, "sandbox_id": "sbx-1"})
        elif op == "wait":
            _reply(stage, {"ok": True, "ready": True, "job_exit_code": 0})
        else:
            _reply(stage, {"ok": True})
        return 0

    monkeypatch.setattr(
        "openai4s.compute.manager.subprocess.Popen", _fake_popen(behaviour)
    )

    blocked: list[Path] = []

    def fake_harvest(job_id, _stage):
        from openai4s.compute import manifest

        # Build in a host-owned staging dir, as the real harvest now does, and
        # return (entries, staging) so the caller can publish it to the
        # workspace.
        staging = byoc._host_staging_dir(job_id)
        target = staging / "model.pt"
        target.write_bytes(b"weights")
        target.chmod(0o000)
        blocked.append(target)
        return manifest.build_manifest(staging), staging

    monkeypatch.setattr(byoc, "_harvest", fake_harvest, raising=True)
    try:
        out = byoc.submit(
            {"provider": "byoc:fake", "command": "train.py", "outputs": ["model.pt"]}
        )
        result = byoc.result({"job_id": out["job_id"]})

        assert result["status"] == states.FAILED
        assert result["exit_code"] == 0, "the job's own verdict is still reported"
        assert result["unverified_files"] == ["model.pt"]
        assert result["unharvested_outputs"] == ["model.pt"]

        row = get_store(cfg.db_path).list_compute_jobs()[0]
        assert row["termination_reason"] == states.REASON_OUTPUTS_UNVERIFIED
    finally:
        # The harvest published the staging tree into the workspace, so the
        # blocked file now lives under dest; chmod whichever copy still exists
        # so tmp cleanup can remove it.
        for path in blocked:
            for candidate in (path, byoc._hpc_root / out["job_id"] / path.name):
                try:
                    candidate.chmod(0o600)
                except OSError:
                    pass


# --------------------------------------------------------------------------
# a transient reply is uncertain, not a definite rejection
# --------------------------------------------------------------------------


def test_a_transient_helper_reply_is_indeterminate_not_terminal(byoc, monkeypatch, cfg):
    """The resident protocol carries only ok/kind/msg, never `indeterminate`.

    A `transient` failure — a `docker run` that timed out — may already have
    created a container. Recording it as terminal `failed` makes reconcile skip
    a resource that may still be billing. It must stay live and reconcilable.
    """

    def behaviour(op, stage):
        _reply(stage, {"ok": False, "kind": "transient", "msg": "docker run timed out"})
        return 0

    monkeypatch.setattr(
        "openai4s.compute.manager.subprocess.Popen", _fake_popen(behaviour)
    )
    with pytest.raises(ComputeError) as error:
        byoc.submit({"provider": "byoc:fake", "command": "train.py"})
    assert error.value.indeterminate is True

    row = get_store(cfg.db_path).list_compute_jobs()[0]
    assert row["status"] == states.UNKNOWN
    assert byoc._live_count() == 1, "a possibly-billing container stays tracked"


def test_a_rejection_that_cannot_be_persisted_is_not_a_clean_rejection(
    byoc, monkeypatch, cfg
):
    """`_persist` swallows storage errors. A locked SQLite let submit report a
    definite `failed` while the durable claim stayed `staging`, rehydrated as a
    live job on restart. A rejection the ledger never recorded is not clean."""

    def behaviour(op, stage):
        _reply(stage, {"ok": False, "kind": "invalid_request", "msg": "no such gpu"})
        return 0

    monkeypatch.setattr(
        "openai4s.compute.manager.subprocess.Popen", _fake_popen(behaviour)
    )

    real_update = byoc._store.update_compute_job

    def flaky_update(job_id, **fields):
        if fields.get("status") == states.FAILED:
            raise sqlite3.OperationalError("database is locked")
        return real_update(job_id, **fields)

    monkeypatch.setattr(byoc._store, "update_compute_job", flaky_update)

    with pytest.raises(ComputeError):
        byoc.submit({"provider": "byoc:fake", "command": "train.py"})

    row = get_store(cfg.db_path).list_compute_jobs()[0]
    assert row["status"] != states.STAGING, (
        "a rejection whose terminal write failed must not leave the row at "
        "staging to be rehydrated as live"
    )
    # It falls through to the indeterminate path, which is reconcilable.
    assert row["status"] == states.UNKNOWN
