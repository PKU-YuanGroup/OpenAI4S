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
    assert restarted.result({"job_id": job_id})["status"] == "done"


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
