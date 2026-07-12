"""Direct contracts for the worker-side ``host.compute`` namespace."""

from __future__ import annotations

import os
import random
import time

import pytest

from openai4s.sdk import host as host_facade
from openai4s.sdk.compute import (
    SessionConcurrencyFull,
    _Compute,
    _compute_call,
    _ComputeInstance,
    _ComputeJob,
    _normalize_provider_params,
    _relativize_local,
)


class _Recorder:
    def __init__(self, responses=()):
        self.calls: list[tuple[str, list]] = []
        self.responses = list(responses)

    def __call__(self, method: str, args: list):
        self.calls.append((method, args))
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response
        return {"ok": True}


def test_legacy_host_module_reexports_compute_types_and_helpers():
    assert host_facade.SessionConcurrencyFull is SessionConcurrencyFull
    assert host_facade._Compute is _Compute
    assert host_facade._ComputeInstance is _ComputeInstance
    assert host_facade._ComputeJob is _ComputeJob
    assert host_facade._compute_call is _compute_call
    assert host_facade._normalize_provider_params is _normalize_provider_params
    assert host_facade._relativize_local is _relativize_local


def test_concurrency_exception_and_compute_call_soft_error_contract():
    full = SessionConcurrencyFull(3, 3)
    assert full.live == 3
    assert full.limit == 3
    assert str(full) == "session concurrency limit reached (live=3/3)"

    recorder = _Recorder([{"ok": True}])
    assert _compute_call(
        recorder,
        "probe",
        {"keep": 0, "drop": None, "self": "ignored"},
    ) == {"ok": True}
    assert recorder.calls == [("compute_probe", [{"keep": 0}])]

    recorder = _Recorder(
        [
            {
                "error": "at capacity",
                "error_kind": "session_concurrency_full",
                "concurrency": {"live": 2, "limit": 2},
            }
        ]
    )
    with pytest.raises(RuntimeError, match="host.compute.submit: at capacity") as exc:
        _compute_call(recorder, "submit", {})
    assert exc.value.error_kind == "session_concurrency_full"
    assert exc.value.concurrency == {"live": 2, "limit": 2}

    status_error = {"status": "failed", "error": "remote failed", "exit_code": 1}
    assert _compute_call(_Recorder([status_error]), "result", {}) is status_error
    assert _compute_call(_Recorder(["raw"]), "status", {}) == "raw"


def test_provider_parameter_normalization_preserves_strays_and_errors():
    for falsy in (None, {}, [], "", 0):
        assert _normalize_provider_params("byoc:nvidia", falsy) is None
    assert _normalize_provider_params(
        "byoc:nvidia",
        {
            "nvidia": {"gpu": "A100", "model": None, "count": 0},
            "timeout": 60,
            "ignored": None,
        },
    ) == {
        "nvidia": {"gpu": "A100", "count": 0},
        "timeout": 60,
    }
    assert _normalize_provider_params(
        "byoc:nvidia", {"nvidia": {}, "gpu": "flat-is-stray"}
    ) == {"gpu": "flat-is-stray"}
    assert _normalize_provider_params(
        "byoc:nvidia", {"nvidia": None, "timeout": 0}
    ) == {"timeout": 0}

    with pytest.raises(TypeError, match="provider_params must be a dict"):
        _normalize_provider_params("byoc:nvidia", ["not-empty"])
    with pytest.raises(
        TypeError,
        match=r"provider_params\['nvidia'\] must be a dict",
    ):
        _normalize_provider_params("byoc:nvidia", {"nvidia": "A100"})


def test_local_path_relativization_allowlist_and_absolute_rejection(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    local = workspace / "inputs" / "data.csv"
    local.parent.mkdir()
    local.write_text("data")
    artifact_root = tmp_path / "artifact-root"
    artifact_root.mkdir()
    artifact = artifact_root / "version.bin"
    artifact.write_bytes(b"x")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"y")
    marker = object()

    monkeypatch.chdir(workspace)
    monkeypatch.setenv("OPENAI4S_ARTIFACTS_ROOTS", str(artifact_root))
    assert _relativize_local(str(local)) == "inputs/data.csv"
    assert _relativize_local("inputs/data.csv") == "inputs/data.csv"
    assert _relativize_local(marker) is marker
    assert _relativize_local(str(artifact)) == str(artifact)
    assert _relativize_local("/legacy/mnt/artifacts/version.bin") == (
        "/legacy/mnt/artifacts/version.bin"
    )
    with pytest.raises(ValueError, match="local path must be workspace-relative"):
        _relativize_local(str(outside))
    # The exact cwd lacks the trailing slash required by the legacy prefix rule.
    with pytest.raises(ValueError, match="local path must be workspace-relative"):
        _relativize_local(os.getcwd())


def test_compute_namespace_create_status_concurrency_and_repr():
    recorder = _Recorder(
        [
            {"limit": 4},
            {"live": 1, "limit": 4},
        ]
    )
    compute = _Compute(recorder)
    instance = compute.create(
        "byoc:nvidia",
        {"nvidia": {"image": "science:1", "gpu": "A100", "model": None}},
    )
    assert isinstance(instance, _ComputeInstance)
    assert repr(instance) == ("<host.compute byoc:nvidia image='science:1' gpu='A100'>")
    assert repr(compute) == (
        "<host.compute — create(target) -> ComputeInstance; "
        "set_concurrency_limit(n); status(); help(host.compute) for the lifecycle>"
    )
    assert compute.set_concurrency_limit("4") == {"limit": 4}
    assert compute.status() == {"live": 1, "limit": 4}
    assert recorder.calls == [
        ("compute_set_concurrency", [{"max_concurrent": 4}]),
        ("compute_status", [{}]),
    ]
    with pytest.raises(ValueError):
        compute.set_concurrency_limit("not-an-int")


def test_instance_command_transfer_attach_close_and_context_cleanup(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    local = tmp_path / "input.txt"
    local.write_text("input")
    recorder = _Recorder(
        [
            {"stdout": "ok", "stderr": "", "exit_code": 0},
            {"path": "downloaded.txt"},
            {"ok": True},
            {"closed": True},
        ]
    )
    instance = _ComputeInstance(recorder, "ssh:lab")
    assert (
        instance.call_command(
            "nvidia-smi",
            intent="inspect gpu",
            login_shell=True,
            timeout_seconds=7,
        )["exit_code"]
        == 0
    )
    assert instance.download("/scratch/result.csv") == {"path": "downloaded.txt"}
    assert instance.upload(str(local), "/scratch/input.txt") == {"ok": True}
    attached = instance.attach_job("job-old")
    assert isinstance(attached, _ComputeJob)
    assert attached.job_id == "job-old"
    assert attached.id == "job-old"
    assert attached.workdir is None
    assert instance._reuse_via == "job-old"
    assert instance.close() == {"closed": True}
    assert instance._reuse_via is None
    assert recorder.calls == [
        (
            "compute_ssh",
            [
                {
                    "provider": "ssh:lab",
                    "command": "nvidia-smi",
                    "intent": "inspect gpu",
                    "login_shell": True,
                    "timeout_seconds": 7,
                }
            ],
        ),
        (
            "compute_scp",
            [
                {
                    "provider": "ssh:lab",
                    "direction": "down",
                    "remote": "/scratch/result.csv",
                }
            ],
        ),
        (
            "compute_scp",
            [
                {
                    "provider": "ssh:lab",
                    "direction": "up",
                    "local": "input.txt",
                    "remote": "/scratch/input.txt",
                }
            ],
        ),
        (
            "compute_close",
            [{"provider": "ssh:lab", "job_ids": ["job-old"]}],
        ),
    ]

    failing = _ComputeInstance(
        _Recorder([RuntimeError("cleanup failed")]),
        "ssh:lab",
    )
    with failing as entered:
        assert entered is failing


def test_submit_normalizes_aliases_inputs_reuse_and_prints_handle(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    local = tmp_path / "data.csv"
    local.write_text("x")
    recorder = _Recorder(
        [
            {
                "job_id": "job-1",
                "remote_workdir": "/scratch/job-1",
                "concurrency": {"live": 1, "limit": 2},
                "system_note": "container created",
                "egress": "allowlist (2 domains)",
            },
            {"job_id": "job-2"},
        ]
    )
    instance = _ComputeInstance(
        recorder,
        "byoc:nvidia",
        {"nvidia": {"gpu": "A100", "model": None}},
    )
    job = instance.submit_job(
        command="python train.py",
        intent="train model",
        inputs={"src": str(local), "dst_filename": "data.csv"},
        outputs=["model.pt"],
        timeout=120,
        env="science",
        credentials=["HF_TOKEN"],
    )
    assert job.job_id == "job-1"
    assert job.workdir == "/scratch/job-1"
    assert job.egress == "allowlist (2 domains)"
    assert job._concurrency == {"live": 1, "limit": 2}
    assert instance._reuse_via == "job-1"
    assert recorder.calls[0] == (
        "compute_submit",
        [
            {
                "provider": "byoc:nvidia",
                "command": "python train.py",
                "intent": "train model",
                "inputs": [{"src": "data.csv", "dst_filename": "data.csv"}],
                "outputs": ["model.pt"],
                "environment": "science",
                "timeout_seconds": 120,
                "credentials": ["HF_TOKEN"],
                "provider_params": {"nvidia": {"gpu": "A100"}},
            }
        ],
    )
    output = capsys.readouterr().out
    assert "[concurrency] live=1/2" in output
    assert "[note] container created" in output
    assert "[egress] sandbox egress at creation: allowlist (2 domains)" in output
    assert "host.compute.Job byoc:nvidia/job-1" in output

    second = instance.submit_job(
        command="python evaluate.py",
        intent="evaluate model",
        inputs="{{artifact:abc}}",
        timeout_seconds=30,
        timeout=999,
        environment="runtime",
        env="ignored",
    )
    assert second.job_id == "job-2"
    assert recorder.calls[1][1][0]["reuse_job_id"] == "job-1"
    assert recorder.calls[1][1][0]["inputs"] == [{"src": "{{artifact:abc}}"}]
    assert recorder.calls[1][1][0]["timeout_seconds"] == 30
    assert recorder.calls[1][1][0]["environment"] == "runtime"

    cancelled = _ComputeInstance(_Recorder([{"message": "user declined"}]), "ssh:x")
    with pytest.raises(RuntimeError, match="user declined"):
        cancelled.submit_job(command="true", intent="test")
    default_cancel = _ComputeInstance(_Recorder([{}]), "ssh:x")
    with pytest.raises(RuntimeError, match="submit cancelled"):
        default_cancel.submit_job(command="true", intent="test")


def test_submit_concurrency_raise_wait_retry_and_reuse_reset(monkeypatch, capsys):
    full_response = {
        "error": "full",
        "error_kind": "session_concurrency_full",
        "concurrency": {"live": 2, "limit": 2},
    }
    immediate = _ComputeInstance(_Recorder([full_response]), "ssh:lab")
    with pytest.raises(SessionConcurrencyFull) as exc:
        immediate.submit_job(command="run", intent="test", on_full="raise")
    assert (exc.value.live, exc.value.limit) == (2, 2)
    assert exc.value.__cause__ is None

    sleeps = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    monkeypatch.setattr(random, "uniform", lambda _start, _end: 0.5)
    waiting_recorder = _Recorder([full_response, {"job_id": "job-open"}])
    waiting = _ComputeInstance(waiting_recorder, "ssh:lab")
    assert waiting.submit_job(command="run", intent="test").job_id == "job-open"
    assert sleeps == [2.5]
    assert capsys.readouterr().out.count("waiting for a slot") == 1

    waiting.attach_job("warm-job")
    retained = {
        "error": "declined",
        "error_kind": "approval_denied",
    }
    waiting._call = _Recorder([retained])
    with pytest.raises(RuntimeError):
        waiting.submit_job(command="run", intent="test", on_full="raise")
    assert waiting._reuse_via == "warm-job"

    reset = {"error": "gone", "error_kind": "reuse_not_found"}
    waiting._call = _Recorder([reset])
    with pytest.raises(RuntimeError):
        waiting.submit_job(command="run", intent="test", on_full="raise")
    assert waiting._reuse_via is None


def test_job_result_cache_warning_egress_hint_and_cancel(capsys):
    recorder = _Recorder(
        [
            {
                "status": "running",
                "output_files": ["partial.log"],
                "featured_files": [],
                "remote_workdir": "/scratch/job",
                "egress": "blocked (no outbound network)",
                "egress_hint": "network fence blocked the request",
            },
            {
                "status": "completed",
                "exit_code": 0,
                "output_files": ["result.csv"],
                "featured_files": ["result.csv"],
                "remote_workdir": "/different/workdir",
                "egress_hint": "do not print twice",
            },
            {"cancelled": True},
        ]
    )
    job = _ComputeJob(recorder, "byoc:nvidia", "job-1")
    assert job.status == "submitted"
    assert job.exit_code is None
    assert job.exit_code is None
    assert capsys.readouterr().err.count(".exit_code is None") == 1

    running = job.result()
    assert running["status"] == "running"
    assert job.output_files == ["partial.log"]
    assert job.featured_files == []
    assert job.workdir == "/scratch/job"
    assert job.egress == "blocked (no outbound network)"
    assert capsys.readouterr().out == "network fence blocked the request\n"

    completed = job.result()
    assert completed["status"] == "completed"
    assert job.status == "completed"
    assert job.exit_code == 0
    assert job.output_files == ["result.csv"]
    assert job.featured_files == ["result.csv"]
    assert job.workdir == "/scratch/job"
    assert capsys.readouterr().out == ""
    assert job.result() is completed
    assert len(recorder.calls) == 2
    assert "state=completed" in repr(job)
    assert "egress='blocked (no outbound network)'" in repr(job)

    assert job.cancel() == {"cancelled": True}
    assert recorder.calls[-1] == (
        "compute_cancel",
        [{"job_id": "job-1", "provider": "byoc:nvidia"}],
    )
