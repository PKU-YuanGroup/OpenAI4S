from __future__ import annotations

import io
import types

import pytest

from openai4s.compute import registry, states
from openai4s.compute.manager import ComputeError, ComputeManager
from openai4s.config import Config
from openai4s.rl import ResourceAdmission, ResourceCapacity, ResourceRequest
from openai4s.store import get_store


class _Proc:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.stdin = io.BytesIO()

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def _ack(pid: str = "31337") -> bytes:
    return f"OPENAI4S_JOB {pid} {pid}\n".encode()


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "skills").mkdir()
    registry.add_host("lab", data_dir=tmp_path)
    return types.SimpleNamespace(
        data_dir=tmp_path,
        skills_dir=tmp_path / "skills",
        db_path=Config(data_dir=tmp_path).db_path,
    )


def test_compute_submit_binds_and_finishes_resource_admission(cfg, monkeypatch):
    admission = ResourceAdmission(ResourceCapacity(4, 1, 80), clock_ms=lambda: 100)
    manager = ComputeManager(cfg, resource_admission=admission)
    monkeypatch.setattr(
        "openai4s.compute.manager.subprocess.Popen",
        lambda *args, **kwargs: _Proc(stdout=_ack()),
    )
    submitted = manager.submit(
        {
            "provider": "ssh:lab",
            "command": "sleep 1",
            "resource_request": {
                "cpu_cores": 2,
                "gpu_count": 1,
                "gpu_memory_gb": 40,
            },
        }
    )
    record = admission.get(submitted["job_id"])
    assert record is not None
    assert record.state == "running"
    assert record.provider_receipt == "31337"

    def poll(argv, **kwargs):
        if "OPENAI4S_HARVEST" in argv[2]:
            return _Proc(stdout=b"OPENAI4S_HARVEST empty\n")
        return _Proc(stdout=b"0\n")

    monkeypatch.setattr("openai4s.compute.manager.subprocess.Popen", poll)
    assert manager.result({"job_id": submitted["job_id"]})["status"] == states.SUCCEEDED
    assert admission.get(submitted["job_id"]).state == "completed"


def test_resource_wait_is_refused_before_provider_submit(cfg, monkeypatch):
    admission = ResourceAdmission(ResourceCapacity(1, 0, 0), clock_ms=lambda: 100)
    manager = ComputeManager(cfg, resource_admission=admission)
    first = admission.admit("held", ResourceRequest(cpu_cores=1))
    assert first.status == "admitted"
    calls = []

    def provider(*args, **kwargs):
        calls.append((args, kwargs))
        return _Proc(stdout=_ack())

    monkeypatch.setattr("openai4s.compute.manager.subprocess.Popen", provider)
    with pytest.raises(ComputeError) as error:
        manager.submit(
            {
                "provider": "ssh:lab",
                "command": "sleep 1",
                "resource_request": {"cpu_cores": 1},
            }
        )
    assert error.value.error_kind == "resource_admission_waiting"
    assert calls == []
    rows = get_store(cfg.db_path).list_compute_jobs()
    assert rows[0]["status"] == states.FAILED


def test_provider_rejection_releases_reserved_resource(cfg, monkeypatch):
    admission = ResourceAdmission(ResourceCapacity(1, 0, 0), clock_ms=lambda: 100)
    manager = ComputeManager(cfg, resource_admission=admission)
    monkeypatch.setattr(
        "openai4s.compute.manager.subprocess.Popen",
        lambda *args, **kwargs: _Proc(returncode=1, stderr=b"denied"),
    )
    with pytest.raises(ComputeError) as error:
        manager.submit(
            {
                "provider": "ssh:lab",
                "command": "sleep 1",
                "resource_request": {"cpu_cores": 1},
            }
        )
    assert error.value.indeterminate is False
    row = get_store(cfg.db_path).list_compute_jobs()[0]
    record = admission.get(row["job_id"])
    assert record is not None and record.state == "released"


def test_confirmed_cancel_finishes_resource_admission(cfg):
    admission = ResourceAdmission(ResourceCapacity(1, 0, 0), clock_ms=lambda: 100)
    manager = ComputeManager(cfg, resource_admission=admission)
    store = get_store(cfg.db_path)
    store.create_compute_job(
        job_id="job-cancel", provider="ssh:lab", status=states.RUNNING
    )
    admission.admit("job-cancel", ResourceRequest(cpu_cores=1))
    admission.bind_receipt("job-cancel", "31337")
    admission.start("job-cancel")
    manager._jobs["job-cancel"] = {
        "job_id": "job-cancel",
        "provider": "ssh:lab",
        "alias": "lab",
        "workdir": "~/.openai4s-jobs/job-cancel",
        "pid": "31337",
        "pgid": "31337",
        "status": states.RUNNING,
    }
    manager._terminate_ssh_job = lambda job: None
    assert manager.cancel({"job_id": "job-cancel"})["status"] == states.CANCELLED
    assert admission.get("job-cancel").state == "completed"


def test_indeterminate_submit_keeps_resource_reserved(cfg, monkeypatch):
    admission = ResourceAdmission(ResourceCapacity(1, 0, 0), clock_ms=lambda: 100)
    manager = ComputeManager(cfg, resource_admission=admission)

    def fail(*args, **kwargs):
        raise TimeoutError("provider did not answer")

    monkeypatch.setattr("openai4s.compute.manager.subprocess.Popen", fail)
    with pytest.raises(ComputeError) as error:
        manager.submit(
            {
                "provider": "ssh:lab",
                "command": "sleep 1",
                "resource_request": {"cpu_cores": 1},
            }
        )
    assert error.value.indeterminate is True
    rows = get_store(cfg.db_path).list_compute_jobs()
    record = admission.get(rows[0]["job_id"])
    assert record is not None and record.state == "reserved"


def test_restart_reconciles_terminal_compute_rows_before_new_submits(cfg):
    admission = ResourceAdmission(ResourceCapacity(1, 0, 0), clock_ms=lambda: 100)
    store = get_store(cfg.db_path)
    store.create_compute_job(
        job_id="job-restart", provider="ssh:lab", status=states.RUNNING
    )
    admission.admit("job-restart", ResourceRequest(cpu_cores=1))
    admission.bind_receipt("job-restart", "31337")
    admission.start("job-restart")
    store.update_compute_job("job-restart", status=states.SUCCEEDED)

    ComputeManager(cfg, resource_admission=admission)
    assert admission.get("job-restart").state == "completed"


def test_restart_settles_receipt_bound_reservation_without_inventing_time(cfg):
    admission = ResourceAdmission(ResourceCapacity(1, 0, 0), clock_ms=lambda: 100)
    store = get_store(cfg.db_path)
    store.create_compute_job(
        job_id="job-bound", provider="ssh:lab", status=states.RUNNING
    )
    admission.admit("job-bound", ResourceRequest(cpu_cores=1))
    admission.bind_receipt("job-bound", "receipt-bound")
    store.update_compute_job("job-bound", status=states.SUCCEEDED, terminal_at=200)
    ComputeManager(cfg, resource_admission=admission)
    record = admission.get("job-bound")
    assert record.state == "completed"
    assert record.actual_compute_time_ms is None
    assert admission.admit("next", ResourceRequest(cpu_cores=1)).status == "admitted"


def test_restart_reuses_durable_compute_terminal_time_for_admission(cfg):
    admission = ResourceAdmission(ResourceCapacity(1, 0, 0), clock_ms=lambda: 10_000)
    store = get_store(cfg.db_path)
    store.create_compute_job(
        job_id="job-terminal-time", provider="ssh:lab", status=states.RUNNING
    )
    admission.admit("job-terminal-time", ResourceRequest(cpu_cores=1), reserved_at=100)
    admission.bind_receipt("job-terminal-time", "31337")
    admission.start("job-terminal-time", started_at=250)
    store.update_compute_job(
        "job-terminal-time", status=states.SUCCEEDED, terminal_at=500
    )

    ComputeManager(cfg, resource_admission=admission)
    assert admission.get("job-terminal-time").actual_compute_time_ms == 250
