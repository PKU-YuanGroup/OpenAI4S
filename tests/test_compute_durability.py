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
import types

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


@pytest.fixture
def submitted(cfg, monkeypatch):
    """One ssh job, submitted and running."""
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _Proc(0, b"31337\n"), raising=True
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


def test_a_recovered_job_can_still_be_polled(cfg, submitted, monkeypatch):
    _, job_id = submitted
    restarted = ComputeManager(cfg)

    def fake_run(argv, **kw):
        return _Proc(0, b"0\n") if argv[0] == "ssh" else _Proc(0)

    monkeypatch.setattr(subprocess, "run", fake_run, raising=True)
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
    assert ComputeManager(cfg).reconcile() == {"recovered": [], "count": 0}


# --------------------------------------------------------------------------
# idempotency
# --------------------------------------------------------------------------


def test_a_duplicate_idempotency_key_is_refused(cfg, submitted, monkeypatch):
    """The point of recording the key before submitting: a retry of the same
    logical work must not become a second remote job."""
    manager, job_id = submitted
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _Proc(0, b"99999\n"), raising=True
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
        subprocess, "run", lambda *a, **k: _Proc(0, b"99999\n"), raising=True
    )
    with pytest.raises(ComputeError, match="duplicate|already exists"):
        restarted.submit(
            {"provider": "ssh:lab", "command": "sleep 600", "idempotency_key": "run-42"}
        )


def test_jobs_without_a_key_are_not_deduplicated(cfg, monkeypatch):
    """Absent a key there is no basis to call two submits the same work."""
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _Proc(0, b"1\n"), raising=True
    )
    manager = ComputeManager(cfg)
    a = manager.submit({"provider": "ssh:lab", "command": "x"})
    b = manager.submit({"provider": "ssh:lab", "command": "x"})
    assert a["job_id"] != b["job_id"]


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
        subprocess, "run", lambda *a, **k: _Proc(0, b"5\n"), raising=True
    )
    manager = ComputeManager(cfg, store=None)
    manager._store = None
    out = manager.submit({"provider": "ssh:lab", "command": "x"})
    assert out["status"] == "running"
    assert manager.reconcile()["count"] == 0
