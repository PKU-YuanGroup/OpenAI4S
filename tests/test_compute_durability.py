"""Compute jobs must survive the process that submitted them.

A remote job outlives this daemon: an ssh job keeps running under `nohup`, a
byoc sandbox keeps billing. `ComputeManager` held jobs in a plain dict, so a
restart stranded every one of them — the work carried on remotely while
`result()` answered "no such job", `cancel()` had no handle to kill it with, and
`_live_count()` reset to zero so the session would cheerfully oversubscribe a
provider that was still busy.

Two properties carry the design:

  * the row is written **before** the submit is attempted. A row written only on
    success is missing for exactly the case that matters — the provider took the
    work and the response never came back.
  * reconciliation **never resubmits**. A job in `submitted` may or may not be
    running, and guessing wrong costs either a duplicate charge or a lost
    result. The honest move is to surface it with its receipt.
"""
import subprocess
import time
import types
from pathlib import Path

import pytest

from openai4s.compute.manager import ComputeError, ComputeManager
from openai4s.config import Config
from openai4s.store import get_store


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "skills").mkdir()
    return types.SimpleNamespace(
        data_dir=tmp_path,
        skills_dir=tmp_path / "skills",
        db_path=Config(data_dir=tmp_path).db_path,
    )


class _Proc:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _ack(pid, pgid=None):
    """The tagged submit acknowledgement the remote launcher now prints.

    Tagged because an untagged `echo $!` meant the first line of a chatty
    `.bashrc` or a login banner became the "pid" the host later signalled.
    Both fields, because `$!` is a pid and the process *group* is what cancel
    has to reach.
    """
    return f"OPENAI4S_JOB {pid} {pid if pgid is None else pgid}\n".encode()


@pytest.fixture
def submitted(cfg, monkeypatch):
    """One ssh job, submitted and running."""
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _Proc(0, _ack("31337")), raising=True
    )
    manager = ComputeManager(cfg)
    out = manager.submit(
        {"provider": "ssh:lab", "command": "sleep 600", "idempotency_key": "run-42"}
    )
    return manager, out["job_id"]


# --------------------------------------------------------------------------
# surviving a restart
# --------------------------------------------------------------------------


def test_a_job_survives_a_restart(cfg, submitted):
    """The headline regression: a fresh manager is a daemon restart."""
    _, job_id = submitted
    restarted = ComputeManager(cfg)
    assert job_id in restarted._jobs


def test_the_handles_needed_to_reach_the_job_survive(cfg, submitted):
    """Recovering the id is useless without what it takes to poll, harvest, or
    kill it."""
    _, job_id = submitted
    job = ComputeManager(cfg)._jobs[job_id]
    assert job["alias"] == "lab"
    assert job["pid"] == "31337"
    assert job["workdir"].endswith(job_id)
    assert job["receipt"] == "31337"


def test_a_recovered_job_still_occupies_its_concurrency_slot(cfg, submitted):
    """_live_count() reset to zero on restart while the provider was still
    busy, so the session would oversubscribe work it had forgotten."""
    _, _ = submitted
    assert ComputeManager(cfg)._live_count() == 1


def _ssh_poll(rc: bytes):
    """A poll makes two ssh round trips: the status probe, then the harvest
    staging. They must not be answered with the same bytes."""

    def fake_run(argv, **kw):
        if argv[0] != "ssh":
            return _Proc(0)
        if "OPENAI4S_HARVEST" in argv[2]:
            return _Proc(0, b"OPENAI4S_HARVEST empty\n")
        return _Proc(0, rc)

    return fake_run


def test_a_recovered_job_can_still_be_polled(cfg, submitted, monkeypatch):
    _, job_id = submitted
    restarted = ComputeManager(cfg)

    monkeypatch.setattr(subprocess, "run", _ssh_poll(b"0\n"), raising=True)
    assert restarted.result({"job_id": job_id})["status"] == "succeeded"


def test_a_recovered_job_can_still_be_cancelled(cfg, submitted, monkeypatch):
    """Without the pid there is nothing to kill, and the remote work runs to
    completion regardless of the user pressing stop."""
    _, job_id = submitted
    restarted = ComputeManager(cfg)
    killed = {}

    def fake_run(argv, **kw):
        killed["cmd"] = argv[2]
        return _Proc(0)

    monkeypatch.setattr(subprocess, "run", fake_run, raising=True)
    assert restarted.cancel({"job_id": job_id})["status"] == "cancelled"
    assert "31337" in killed["cmd"]


def test_terminal_jobs_are_not_rehydrated(cfg, submitted, monkeypatch):
    """Only work that may still be consuming a remote resource comes back."""
    manager, job_id = submitted

    def fake_run(argv, **kw):
        return _Proc(0, b"0\n") if argv[0] == "ssh" else _Proc(0)

    monkeypatch.setattr(subprocess, "run", fake_run, raising=True)
    manager.result({"job_id": job_id})

    restarted = ComputeManager(cfg)
    assert job_id not in restarted._jobs
    assert restarted._live_count() == 0


# --------------------------------------------------------------------------
# reconcile
# --------------------------------------------------------------------------


def test_reconcile_surfaces_recovered_jobs_with_their_receipt(cfg, submitted):
    _, job_id = submitted
    report = ComputeManager(cfg).reconcile()
    assert report["count"] == 1
    assert report["recovered"][0]["job_id"] == job_id
    assert report["recovered"][0]["receipt"] == "31337"


def test_reconcile_does_not_resubmit(cfg, submitted, monkeypatch):
    """Guessing wrong costs a duplicate charge or a lost result. Report, do not
    act."""
    _, _ = submitted
    restarted = ComputeManager(cfg)

    def forbidden(*a, **k):
        raise AssertionError("reconcile must not touch the provider")

    monkeypatch.setattr(subprocess, "run", forbidden, raising=True)
    assert restarted.reconcile()["count"] == 1


def test_reconcile_is_empty_with_nothing_in_flight(cfg):
    assert ComputeManager(cfg).reconcile() == {
        "recovered": [],
        "count": 0,
        "orphan_risk_count": 0,
    }


# --------------------------------------------------------------------------
# idempotency
# --------------------------------------------------------------------------


def test_a_duplicate_idempotency_key_is_refused(cfg, submitted, monkeypatch):
    """The point of recording the key before submitting: a retry of the same
    logical work must not become a second remote job."""
    manager, job_id = submitted
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _Proc(0, _ack("99999")), raising=True
    )
    with pytest.raises(ComputeError) as e:
        manager.submit(
            {"provider": "ssh:lab", "command": "sleep 600", "idempotency_key": "run-42"}
        )
    assert e.value.error_kind == "duplicate_request"
    assert job_id in str(e.value)


def test_the_key_survives_a_restart(cfg, submitted, monkeypatch):
    """A crash is exactly when a client retries, so this is the case the guard
    exists for."""
    _, _ = submitted
    restarted = ComputeManager(cfg)
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _Proc(0, _ack("99999")), raising=True
    )
    with pytest.raises(ComputeError, match="duplicate|already exists"):
        restarted.submit(
            {"provider": "ssh:lab", "command": "sleep 600", "idempotency_key": "run-42"}
        )


def test_jobs_without_a_key_are_not_deduplicated(cfg, monkeypatch):
    """Absent a key there is no basis to call two submits the same work."""
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _Proc(0, _ack("1")), raising=True
    )
    manager = ComputeManager(cfg)
    a = manager.submit({"provider": "ssh:lab", "command": "x"})
    b = manager.submit({"provider": "ssh:lab", "command": "x"})
    assert a["job_id"] != b["job_id"]


def test_a_concurrent_same_key_submit_is_refused_not_duplicated(cfg, monkeypatch):
    """The serial pre-check cannot see a same-key row a concurrent submitter has
    not committed yet. Both read None, both INSERT, and the UNIQUE idempotency
    index is the only thing that stops the second billable remote run. Swallowing
    that IntegrityError raced a duplicate job with no durable row (unrecoverable
    after a restart); the loser must be refused, naming the winner.
    """
    submits: list = []

    def fake_run(*a, **k):
        submits.append(a)
        return _Proc(0, _ack("31337"))

    monkeypatch.setattr(subprocess, "run", fake_run, raising=True)
    manager = ComputeManager(cfg)

    # The winner's row is already committed, so the UNIQUE index will reject the
    # loser's INSERT — exactly as a concurrent submitter's committed row would.
    manager._store.create_compute_job(
        job_id="job-winner",
        provider="ssh:lab",
        status="staging",
        idempotency_key="dup-key",
    )

    # ...but the loser's serial pre-check races ahead of that row being visible
    # to it: force the first lookup to miss, the post-IntegrityError re-read to
    # find the winner.
    real_lookup = manager._store.compute_job_by_idempotency_key
    calls = {"n": 0}

    def racing_lookup(key):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return real_lookup(key)

    monkeypatch.setattr(manager._store, "compute_job_by_idempotency_key", racing_lookup)

    with pytest.raises(ComputeError) as e:
        manager.submit(
            {
                "provider": "ssh:lab",
                "command": "sleep 600",
                "idempotency_key": "dup-key",
            }
        )
    assert e.value.error_kind == "duplicate_request"
    assert "job-winner" in str(e.value)
    assert submits == [], "a duplicate remote job was launched under the same key"


# --------------------------------------------------------------------------
# the row is written before the submit
# --------------------------------------------------------------------------


def test_an_indeterminate_submit_leaves_a_reconcilable_row(cfg, monkeypatch):
    """The case the ordering exists for: we do not know whether the remote
    shell ran. A row written only on success would be absent here, and the job
    — if it started — would bill forever with nothing that could find it."""

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="ssh", timeout=60)

    monkeypatch.setattr(subprocess, "run", boom, raising=True)
    manager = ComputeManager(cfg)
    with pytest.raises(ComputeError) as e:
        manager.submit({"provider": "ssh:lab", "command": "x"})
    assert e.value.error_kind == "unknown_state"

    store = get_store(cfg.db_path)
    rows = store.list_compute_jobs()
    assert len(rows) == 1
    assert rows[0]["status"] == "unknown"
    assert rows[0]["workdir"], "the workdir is what makes it findable by hand"


def test_a_dropped_submit_receipt_degrades_to_reconcilable_not_clean_running(
    cfg, monkeypatch
):
    """The submit-success asymmetry the earlier round left open.

    The failure path was hardened to never report a definite state the ledger
    did not record, but the *success* path still wrote the RUNNING receipt (the
    pid / sandbox_id that names a now-billing resource) through the
    swallow-everything `_persist` — and only after the in-memory write. A dropped
    receipt write left submit returning a clean `running` over a row stuck at
    `staging` with no receipt: a live job unrecoverable after a restart. The
    receipt must be a checked write; a drop degrades to the same
    UNKNOWN/reconcilable state, never a clean `running`.
    """
    import sqlite3

    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _Proc(0, _ack("31337")), raising=True
    )
    manager = ComputeManager(cfg)

    # Drop exactly the durable RUNNING receipt write, as a locked/full SQLite
    # would at that instant. Every other durable write still lands.
    real_update = manager._store.update_compute_job

    def failing_receipt_write(job_id, **fields):
        if fields.get("status") == "running":
            raise sqlite3.OperationalError("database is locked")
        return real_update(job_id, **fields)

    monkeypatch.setattr(manager._store, "update_compute_job", failing_receipt_write)

    out = manager.submit({"provider": "ssh:lab", "command": "sleep 600"})

    # Not a clean `running`: an indeterminate, reconcilable state.
    assert (
        out["status"] == "unknown"
    ), "submit reported a clean 'running' though the receipt write was dropped"
    job_id = out["job_id"]

    store = get_store(cfg.db_path)
    row = store.get_compute_job(job_id)
    assert row is not None
    assert row["status"] == "unknown", "the durable row was left at staging/running"
    assert row["receipt"] == "31337", "the pid receipt was not preserved for recovery"
    assert row["termination_reason"] == "submit_indeterminate"

    # It still occupies a concurrency slot in-process...
    assert manager._live_count() == 1
    # ...and after a restart it is recoverable *with its receipt* — the live job
    # is nameable, not an orphan reported as cleanly running then lost.
    report = ComputeManager(cfg).reconcile()
    recovered = {j["job_id"]: j for j in report["recovered"]}
    assert job_id in recovered
    assert recovered[job_id]["receipt"] == "31337"


def test_a_receipt_write_to_a_missing_row_degrades_not_clean_running(cfg, monkeypatch):
    """The gap in the receipt fix that Codex caught: when `_claim` degraded to
    in-memory on a *transient* DB error, no row exists, so the RUNNING receipt
    UPDATE matches nothing and returns None. Treating a None (no-row) update as a
    landed write let submit report a clean 'running' over a job with no durable
    row at all. A missing-row write is a failed write: degrade to indeterminate.
    """
    import sqlite3

    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _Proc(0, _ack("31337")), raising=True
    )
    manager = ComputeManager(cfg)

    # `_claim` degrades to in-memory on a transient create failure: no row.
    def transient_create(**_kw):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(manager._store, "create_compute_job", transient_create)

    out = manager.submit({"provider": "ssh:lab", "command": "sleep 600"})
    assert (
        out["status"] == "unknown"
    ), "submit reported a clean 'running' though no durable row was ever written"
    # No orphaned durable row claims a clean running state.
    assert get_store(cfg.db_path).get_compute_job(out["job_id"]) is None
    # ...but it stays nameable in-process so this session can still reach it.
    assert manager._live_count() == 1


def test_a_rejected_submit_is_recorded_as_failed(cfg, monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: _Proc(255, b"", b"host key changed"),
        raising=True,
    )
    manager = ComputeManager(cfg)
    with pytest.raises(ComputeError):
        manager.submit({"provider": "ssh:lab", "command": "x"})
    rows = get_store(cfg.db_path).list_compute_jobs()
    assert rows[0]["status"] == "failed"
    # A rejected submit never started remote work, so it holds no slot.
    assert manager._live_count() == 0


# --------------------------------------------------------------------------
# the event stream
# --------------------------------------------------------------------------


def test_events_are_sequenced(cfg, submitted):
    manager, job_id = submitted
    events = manager.job_history({"job_id": job_id})["events"]
    assert [e["seq"] for e in events] == [1, 2]
    assert [e["kind"] for e in events] == ["created", "submitted"]


def test_the_stream_records_how_a_job_reached_its_terminal_state(
    cfg, submitted, monkeypatch
):
    """A status column says where a job is; the stream says how it got there —
    which is what tells "never submitted" from "submitted, response lost"."""
    manager, job_id = submitted

    def fake_run(argv, **kw):
        return _Proc(0, b"7\n") if argv[0] == "ssh" else _Proc(0)

    monkeypatch.setattr(subprocess, "run", fake_run, raising=True)
    manager.result({"job_id": job_id})

    events = manager.job_history({"job_id": job_id})["events"]
    assert [e["kind"] for e in events] == ["created", "submitted", "failed"]
    assert events[-1]["payload"] == {"exit_code": 7}


def test_the_submitted_event_carries_the_receipt(cfg, submitted):
    manager, job_id = submitted
    events = manager.job_history({"job_id": job_id})["events"]
    assert events[1]["payload"]["pid"] == "31337"


# --------------------------------------------------------------------------
# degradation
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# the byoc arm: same discipline as ssh
# --------------------------------------------------------------------------


@pytest.fixture
def byoc(cfg, tmp_path):
    """A discoverable byoc provider. The helper itself is always stubbed."""
    d = cfg.skills_dir / "remote-compute-fake"
    d.mkdir()
    (d / "provider.json").write_text('{"id": "fake"}', encoding="utf-8")
    (d / "provider.py").write_text("PROVIDER = object()\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return ComputeManager(cfg, workspace=workspace)


def _helper(manager, monkeypatch, behaviour):
    monkeypatch.setattr(
        ComputeManager, "_run_helper", lambda self, prov, op, *a, **k: behaviour(op)
    )


def test_a_byoc_create_that_may_have_landed_is_reconcilable(byoc, monkeypatch, cfg):
    """A killed helper is not proof that nothing was created. Recording this as
    `failed` would be a claim we cannot support; leaving it at `staging` — the
    old behaviour — is worse still, because nothing ever revisits it."""

    def behaviour(op):
        raise ComputeError("helper exceeded the host deadline", "unknown_state")

    _helper(byoc, monkeypatch, behaviour)
    with pytest.raises(ComputeError):
        byoc.submit({"provider": "byoc:fake", "command": "train.py"})

    rows = get_store(cfg.db_path).list_compute_jobs()
    assert len(rows) == 1
    assert rows[0]["status"] == "unknown"


def test_a_byoc_sandbox_survives_a_submit_that_failed_after_it(byoc, monkeypatch, cfg):
    """The expensive case. `create` succeeded, so a sandbox is billing; the
    later `submit` blew up. The id lived only in the in-memory `_sandboxes`
    map, so a restart left a running sandbox nobody could name."""

    def behaviour(op):
        if op == "create":
            return {"sandbox_id": "sbx-777"}
        raise ComputeError("submit exploded", "transient")

    _helper(byoc, monkeypatch, behaviour)
    with pytest.raises(ComputeError):
        byoc.submit({"provider": "byoc:fake", "command": "train.py"})

    row = get_store(cfg.db_path).list_compute_jobs()[0]
    assert row["status"] == "unknown", "a live sandbox is not a clean failure"
    assert row["sandbox_id"] == "sbx-777"
    assert row["receipt"] == "sbx-777", "the receipt is what terminate needs"


def test_a_byoc_submit_the_provider_refused_is_failed(byoc, monkeypatch, cfg):
    """No sandbox was created and the provider said so explicitly, so this one
    really is terminal — and holds no concurrency slot."""

    def behaviour(op):
        raise ComputeError("quota exceeded", "invalid_request")

    _helper(byoc, monkeypatch, behaviour)
    with pytest.raises(ComputeError):
        byoc.submit({"provider": "byoc:fake", "command": "train.py"})

    row = get_store(cfg.db_path).list_compute_jobs()[0]
    assert row["status"] == "failed"
    assert row["terminal_at"]
    assert byoc._live_count() == 0


def test_a_closed_job_does_not_come_back_to_life(cfg, submitted):
    """close() only mutated the in-memory dict, so a restart rehydrated the job
    as live: it held a concurrency slot and was reconciled against a provider
    that had already released it."""
    manager, job_id = submitted
    manager.close({"provider": "ssh:lab", "job_ids": [job_id]})

    row = get_store(cfg.db_path).list_compute_jobs()[0]
    assert row["status"] == "cancelled"
    assert row["termination_reason"] == "handle_closed"
    assert ComputeManager(cfg)._live_count() == 0
    kinds = [e["kind"] for e in manager.job_history({"job_id": job_id})["events"]]
    assert "closed" in kinds


def test_close_keeps_the_sandbox_id_when_terminate_fails(byoc, monkeypatch):
    """The id was popped *before* the terminate attempt, so a provider that
    refused to release the sandbox left it billing with nothing able to name
    it. Losing the handle is the one outcome worse than a failed terminate."""

    def behaviour(op):
        if op == "create":
            return {"sandbox_id": "sbx-9"}
        if op == "terminate":
            raise ComputeError("provider unreachable", "transient")
        return {}

    _helper(byoc, monkeypatch, behaviour)
    byoc.submit({"provider": "byoc:fake", "command": "x"})
    assert byoc._sandboxes["fake"] == "sbx-9"

    out = byoc.close({"provider": "byoc:fake"})
    assert out["sandbox_released"] is False
    assert byoc._sandboxes.get("fake") == "sbx-9", "the handle must survive"


def test_close_releases_the_sandbox_once_the_provider_confirms(byoc, monkeypatch):
    def behaviour(op):
        return {"sandbox_id": "sbx-9"} if op == "create" else {}

    _helper(byoc, monkeypatch, behaviour)
    byoc.submit({"provider": "byoc:fake", "command": "x"})

    out = byoc.close({"provider": "byoc:fake"})
    assert out["sandbox_released"] is True
    assert "fake" not in byoc._sandboxes


# --------------------------------------------------------------------------
# staging inputs
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dst",
    [
        "/etc/cron.d/openai4s",  # `work / dst` discards `work` entirely
        "../../escaped.txt",
        "nested/dir.txt",  # inputs are flat; a subdir is not a name
        "..",
    ],
)
def test_a_staged_input_cannot_choose_where_it_lands(byoc, monkeypatch, tmp_path, dst):
    src = tmp_path / "workspace" / "payload.txt"
    src.write_text("data", encoding="utf-8")
    _helper(byoc, monkeypatch, lambda op: {"sandbox_id": "sbx-1"})

    with pytest.raises(ComputeError) as e:
        byoc.submit(
            {
                "provider": "byoc:fake",
                "command": "x",
                "inputs": [{"src": str(src), "dst_filename": dst}],
            }
        )
    assert e.value.error_kind == "invalid_request"


def test_a_missing_input_fails_the_job_instead_of_running_without_it(
    byoc, monkeypatch, tmp_path
):
    """Silently skipping a missing input is how a job runs to completion
    against data that was never there and reports success."""
    _helper(byoc, monkeypatch, lambda op: {"sandbox_id": "sbx-1"})

    with pytest.raises(ComputeError) as e:
        byoc.submit(
            {
                "provider": "byoc:fake",
                "command": "x",
                "inputs": [{"src": str(tmp_path / "workspace" / "absent.csv")}],
            }
        )
    assert e.value.error_kind == "invalid_request"


def test_a_legitimate_input_still_stages(byoc, monkeypatch, tmp_path):
    src = tmp_path / "workspace" / "payload.txt"
    src.write_text("data", encoding="utf-8")
    _helper(byoc, monkeypatch, lambda op: {"sandbox_id": "sbx-1"})

    out = byoc.submit(
        {
            "provider": "byoc:fake",
            "command": "x",
            "inputs": [{"src": str(src), "dst_filename": "renamed.txt"}],
        }
    )
    assert out["status"] == "running"


def test_a_manager_without_a_store_still_runs_jobs(cfg, monkeypatch):
    """Bookkeeping that cannot reach the database must not refuse to run work —
    that is the old behaviour, which is worse but not nothing."""
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _Proc(0, _ack("5")), raising=True
    )
    manager = ComputeManager(cfg, store=None)
    manager._store = None
    out = manager.submit({"provider": "ssh:lab", "command": "x"})
    assert out["status"] == "running"
    assert manager.reconcile()["count"] == 0


# --------------------------------------------------------------------------
# a job is not successful until its outputs are accounted for
# --------------------------------------------------------------------------


def test_a_job_that_never_produced_its_declared_outputs_is_not_a_success(
    byoc, monkeypatch, cfg
):
    """The `outputs` globs were persisted and never read back, so a job that
    promised `model.pt` and wrote nothing still reported succeeded with an
    empty file list."""

    def behaviour(op):
        if op == "create":
            return {"sandbox_id": "sbx-1"}
        if op == "wait":
            return {"ready": True, "job_exit_code": 0}
        return {}

    _helper(byoc, monkeypatch, behaviour)
    out = byoc.submit(
        {"provider": "byoc:fake", "command": "train.py", "outputs": ["model.pt"]}
    )
    result = byoc.result({"job_id": out["job_id"]})

    assert result["status"] == "failed", "rc==0 with no outputs is not a success"
    assert result["exit_code"] == 0, "the job's own verdict is still reported"

    row = get_store(cfg.db_path).list_compute_jobs()[0]
    assert row["termination_reason"] == "outputs_unverified"
    assert "model.pt" in (row["reason"] or "")


def test_a_harvest_is_recorded_with_hashes(byoc, monkeypatch, cfg):
    """Nothing in the compute package hashed anything, so a transfer that
    stopped halfway was indistinguishable from a complete one."""

    def behaviour(op):
        if op == "create":
            return {"sandbox_id": "sbx-1"}
        if op == "wait":
            return {"ready": True, "job_exit_code": 0}
        return {}

    _helper(byoc, monkeypatch, behaviour)

    # Stand in for the archive extraction with a real file on disk.
    def fake_harvest(job_id, _stage):
        from openai4s.compute import manifest as _manifest

        staging = byoc._host_staging_dir(job_id)
        (staging / "model.pt").write_bytes(b"weights")
        return _manifest.build_manifest(staging), staging

    monkeypatch.setattr(byoc, "_harvest", fake_harvest, raising=True)

    out = byoc.submit(
        {"provider": "byoc:fake", "command": "train.py", "outputs": ["model.pt"]}
    )
    result = byoc.result({"job_id": out["job_id"]})

    assert result["status"] == "succeeded"
    entry = result["artifact_manifest"][0]
    assert entry["path"] == "model.pt"
    assert entry["size"] == len(b"weights")
    assert len(entry["sha256"]) == 64
    assert result["integrity_sha256"]

    row = get_store(cfg.db_path).list_compute_jobs()[0]
    assert row["artifact_manifest"][0]["sha256"] == entry["sha256"]
    assert row["integrity_sha256"] == result["integrity_sha256"]


def test_featured_files_is_the_declared_subset(byoc, monkeypatch):
    """Documented as the subset matching the declared globs; it was in fact
    every harvested file."""

    def behaviour(op):
        if op == "create":
            return {"sandbox_id": "sbx-1"}
        if op == "wait":
            return {"ready": True, "job_exit_code": 0}
        return {}

    _helper(byoc, monkeypatch, behaviour)

    def fake_harvest(job_id, _stage):
        from openai4s.compute import manifest as _manifest

        staging = byoc._host_staging_dir(job_id)
        (staging / "scores.csv").write_text("a\n1\n", encoding="utf-8")
        (staging / "stdout.log").write_text("noise\n", encoding="utf-8")
        return _manifest.build_manifest(staging), staging

    monkeypatch.setattr(byoc, "_harvest", fake_harvest, raising=True)

    out = byoc.submit({"provider": "byoc:fake", "command": "x", "outputs": ["*.csv"]})
    result = byoc.result({"job_id": out["job_id"]})

    assert [Path(p).name for p in result["featured_files"]] == ["scores.csv"]
    assert len(result["output_files"]) == 2


# --------------------------------------------------------------------------
# the container deadline the wrapper has always been able to enforce
# --------------------------------------------------------------------------


def _capture_submits(manager, monkeypatch, behaviour):
    """Record every helper request so the submit payload can be inspected."""
    seen = []

    def run_helper(self, prov, op, req, *a, **k):
        seen.append((op, dict(req)))
        return behaviour(op)

    monkeypatch.setattr(ComputeManager, "_run_helper", run_helper)
    return seen


def _sandbox_behaviour(op):
    if op == "create":
        return {"sandbox_id": "sbx-1"}
    if op == "wait":
        return {"ready": True, "job_exit_code": 0}
    return {}


def test_a_declared_container_lifetime_arms_the_wrapper_watchdog(byoc, monkeypatch):
    """`sandbox_deadline_epoch`, `harvest_margin_s` and `term_grace_s` are
    read by the helper and consumed by the wrapper, and the host produced none
    of them — so the watchdog was never armed and a container could be
    reclaimed mid-job, taking the outputs with it."""
    seen = _capture_submits(byoc, monkeypatch, _sandbox_behaviour)

    byoc.submit(
        {
            "provider": "byoc:fake",
            "command": "train.py",
            "provider_params": {"fake": {"timeout": 3600}},
        }
    )

    submit = next(req for op, req in seen if op == "submit")
    assert submit["harvest_margin_s"] > 0
    assert submit["term_grace_s"] > 0
    # An absolute epoch roughly an hour out, not a relative duration.
    assert submit["sandbox_deadline_epoch"] > time.time() + 3000


def test_a_reused_sandbox_inherits_the_time_it_has_already_spent(byoc, monkeypatch):
    """The case an absolute deadline exists for. A second job entering a warm
    container must not be handed a fresh lifetime — the container expires when
    it expires, regardless of when the job started."""
    seen = _capture_submits(byoc, monkeypatch, _sandbox_behaviour)
    params = {"fake": {"timeout": 3600}}

    byoc.submit({"provider": "byoc:fake", "command": "a", "provider_params": params})
    first = next(req for op, req in seen if op == "submit")

    # The container is warm now; time passes before the next job.
    byoc._sandbox_deadlines["fake"] -= 1800
    byoc.submit({"provider": "byoc:fake", "command": "b", "provider_params": params})
    second = [req for op, req in seen if op == "submit"][-1]

    assert [op for op, _ in seen].count("create") == 1, "the sandbox was reused"
    assert (
        second["sandbox_deadline_epoch"] < first["sandbox_deadline_epoch"]
    ), "a reused container must not be handed a fresh hour"


def test_no_declared_lifetime_leaves_the_watchdog_unarmed(byoc, monkeypatch):
    """Absent a lifetime the host has nothing to compute a deadline from, and
    guessing one could kill a job early. The wrapper falls back to no
    watchdog, which is what it did before."""
    seen = _capture_submits(byoc, monkeypatch, _sandbox_behaviour)

    byoc.submit({"provider": "byoc:fake", "command": "train.py"})

    submit = next(req for op, req in seen if op == "submit")
    assert "sandbox_deadline_epoch" not in submit
    # The margins are still sent: they are policy, not a deadline.
    assert submit["harvest_margin_s"] > 0


def test_closing_a_sandbox_forgets_its_deadline(byoc, monkeypatch):
    _capture_submits(byoc, monkeypatch, _sandbox_behaviour)
    byoc.submit(
        {
            "provider": "byoc:fake",
            "command": "x",
            "provider_params": {"fake": {"timeout": 3600}},
        }
    )
    assert "fake" in byoc._sandbox_deadlines

    byoc.close({"provider": "byoc:fake"})
    assert (
        "fake" not in byoc._sandbox_deadlines
    ), "a stale deadline would be applied to the next container"
