"""Offline contracts for class-based remote-compute job orchestration."""

from __future__ import annotations

from typing import Any

from openai4s.config import Config
from openai4s.host_dispatch import build_dispatcher
from openai4s.tools.catalog import SessionToolCatalog
from openai4s.tools.registry import get_tool, get_tool_by_host_method
from openai4s.tools.remote_compute import (
    CancelRemoteComputeJobTool,
    CloseRemoteComputeTool,
    GetRemoteComputeJobResultTool,
    RemoteComputeStatusTool,
    SubmitRemoteComputeJobTool,
)


class RecordingRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def invoke(self, method: str, *arguments: Any) -> Any:
        self.calls.append((method, arguments))
        return {"method": method, "arguments": list(arguments)}


class FakeComputeManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def submit(self, spec: dict) -> dict:
        self.calls.append(("submit", dict(spec)))
        return {"job_id": "job-1", "status": "running"}

    def status(self, spec: dict) -> dict:
        self.calls.append(("status", dict(spec)))
        return {"live": 1, "limit": 2, "daemon_live": True}

    def result(self, spec: dict) -> dict:
        self.calls.append(("result", dict(spec)))
        return {"job_id": spec["job_id"], "status": "running"}

    def cancel(self, spec: dict) -> dict:
        self.calls.append(("cancel", dict(spec)))
        return {"job_id": spec["job_id"], "status": "cancelled"}

    def close(self, spec: dict) -> dict:
        self.calls.append(("close", dict(spec)))
        return {"provider": spec["provider"], "status": "closed"}


def test_registry_exposes_named_remote_compute_job_tool_classes():
    expected = {
        "compute_submit": SubmitRemoteComputeJobTool,
        "compute_status": RemoteComputeStatusTool,
        "compute_result": GetRemoteComputeJobResultTool,
        "compute_cancel": CancelRemoteComputeJobTool,
        "compute_close": CloseRemoteComputeTool,
    }

    for name, tool_type in expected.items():
        tool = get_tool(name)
        assert type(tool) is tool_type
        assert get_tool_by_host_method(tool.host_method) is tool

    assert get_tool("bash") is None
    assert get_tool("submit_output") is None


def test_classes_forward_existing_compute_arguments_without_changing_sdk_behavior():
    runtime = RecordingRuntime()
    submit = {
        "provider": "byoc:nvidia",
        "command": "python run.py",
        "intent": "score one structure",
        "timeout_seconds": 900,
        # Not provider-visible in the native schema, but still part of the
        # established in-kernel host.compute SDK payload.
        "inputs": [{"src": "input.csv", "dst_filename": "input.csv"}],
        "provider_params": {"nvidia": {"gpu": "A100"}},
    }
    SubmitRemoteComputeJobTool().execute(
        runtime,
        submit,
    )
    RemoteComputeStatusTool().execute(runtime, {})
    GetRemoteComputeJobResultTool().execute(
        runtime,
        {"provider": "byoc:nvidia", "job_id": "job-1"},
    )
    CancelRemoteComputeJobTool().execute(
        runtime,
        {"provider": "byoc:nvidia", "job_id": "job-1"},
    )
    CloseRemoteComputeTool().execute(
        runtime,
        {"provider": "byoc:nvidia", "job_ids": ["job-1"]},
    )

    assert runtime.calls == [
        (
            "compute_submit",
            (submit,),
        ),
        ("compute_status", ({},)),
        (
            "compute_result",
            ({"provider": "byoc:nvidia", "job_id": "job-1"},),
        ),
        (
            "compute_cancel",
            ({"provider": "byoc:nvidia", "job_id": "job-1"},),
        ),
        (
            "compute_close",
            ({"provider": "byoc:nvidia", "job_ids": ["job-1"]},),
        ),
    ]


def test_schema_rejects_sdk_only_or_unbounded_remote_compute_fields():
    submit = get_tool("compute_submit")
    valid = {
        "provider": "ssh:lab",
        "command": "python run.py",
        "intent": "run a bounded analysis",
    }
    assert submit.validation_error(valid) is None
    assert "unknown property" in submit.validation_error(
        {**valid, "credentials": ["HF_TOKEN"]}
    )
    assert "unknown property" in submit.validation_error(
        {**valid, "provider_params": {"nvidia": {"gpu": "H100"}}}
    )
    assert "must be <= 604800" in submit.validation_error(
        {**valid, "timeout_seconds": 604801}
    )
    assert "required property" in get_tool("compute_result").validation_error(
        {"job_id": "job-1"}
    )
    assert "item count must be <= 100" in get_tool("compute_close").validation_error(
        {"provider": "ssh:lab", "job_ids": [f"job-{i}" for i in range(101)]}
    )


def test_remote_compute_policy_taxonomy_resources_and_progressive_group():
    submit = get_tool("compute_submit")
    assert submit.read_only is False
    assert submit.requires_approval is True
    assert submit.dangerous is True
    assert submit.side_effect_class == "external_write"
    assert submit.permission_target({"provider": "ssh:lab"}) == "ssh:lab"
    assert submit.resource_keys({"provider": "ssh:lab"}) == (
        "remote_compute_provider:ssh%3Alab",
    )

    status = get_tool("compute_status")
    assert status.read_only is True
    assert status.requires_approval is False
    assert status.side_effect_class == "read_only"
    assert status.resource_keys({}) == ("remote_compute:session",)

    result = get_tool("compute_result")
    assert result.read_only is False
    assert result.requires_approval is False
    assert result.side_effect_class == "runtime_mutation"
    assert result.resource_keys({"provider": "byoc:nvidia", "job_id": "job-1"}) == (
        "remote_compute_provider:byoc%3Anvidia",
        "remote_compute_job:job-1",
    )

    for name in ("compute_cancel", "compute_close"):
        tool = get_tool(name)
        assert tool.read_only is False
        assert tool.requires_approval is False
        assert tool.side_effect_class == "external_write"

    close = get_tool("compute_close")
    assert close.resource_keys(
        {"provider": "byoc:nvidia", "job_ids": ["job-1", "job-2"]}
    ) == (
        "remote_compute_provider:byoc%3Anvidia",
        "remote_compute_job:job-1",
        "remote_compute_job:job-2",
    )

    catalog = SessionToolCatalog()
    assert "compute_submit" not in {spec.name for spec in catalog.specs_for([])}
    catalog.activate_groups("remote")
    assert {
        "compute_submit",
        "compute_status",
        "compute_result",
        "compute_cancel",
        "compute_close",
    } <= {spec.name for spec in catalog.specs_for([])}


def test_dispatcher_gates_submit_but_keeps_exact_job_cleanup_available(tmp_path):
    dispatcher = build_dispatcher(
        Config(data_dir=tmp_path / "data"),
        workspace=tmp_path,
    )
    compute = FakeComputeManager()
    dispatcher._compute = compute
    submit = {
        "provider": "byoc:nvidia",
        "command": "python run.py",
        "intent": "score one structure",
    }
    dispatcher.store.set_permission_rule(
        scope="global",
        scope_id="",
        tool="compute_submit",
        pattern="*",
        decision="deny",
    )

    denied = dispatcher("compute_submit", [submit])
    assert set(denied) == {"error"}
    assert compute.calls == []

    dispatcher.store.set_permission_rule(
        scope="global",
        scope_id="",
        tool="compute_submit",
        pattern="*",
        decision="allow",
    )
    assert dispatcher("compute_submit", [submit]) == {
        "job_id": "job-1",
        "status": "running",
    }

    # A stale deny rule must not prevent inspection or release of work that
    # has already been submitted; these exact-handle operations cannot create
    # new work or widen authority.
    for method in (
        "compute_status",
        "compute_result",
        "compute_cancel",
        "compute_close",
    ):
        dispatcher.store.set_permission_rule(
            scope="global",
            scope_id="",
            tool=method,
            pattern="*",
            decision="deny",
        )

    assert dispatcher("compute_status", [{}])["live"] == 1
    assert (
        dispatcher(
            "compute_result",
            [{"provider": "byoc:nvidia", "job_id": "job-1"}],
        )["status"]
        == "running"
    )
    assert (
        dispatcher(
            "compute_cancel",
            [{"provider": "byoc:nvidia", "job_id": "job-1"}],
        )["status"]
        == "cancelled"
    )
    assert (
        dispatcher(
            "compute_close",
            [{"provider": "byoc:nvidia", "job_ids": ["job-1"]}],
        )["status"]
        == "closed"
    )

    assert compute.calls == [
        ("submit", submit),
        ("status", {}),
        ("result", {"provider": "byoc:nvidia", "job_id": "job-1"}),
        ("cancel", {"provider": "byoc:nvidia", "job_id": "job-1"}),
        ("close", {"provider": "byoc:nvidia", "job_ids": ["job-1"]}),
    ]
