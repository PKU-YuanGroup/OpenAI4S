from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from openai4s.tools.catalog import SessionToolCatalog
from openai4s.tools.dynamic import (
    DynamicToolManifest,
    DynamicToolRegistry,
    DynamicToolWorker,
    ProxyDynamicTool,
    validate_dynamic_source,
)
from openai4s.tools.registry import execute_tool_call


class _Sandbox:
    def __init__(self) -> None:
        self.status = SimpleNamespace(enforced=True)
        self.closed = False

    def wrap_command(self, command):
        return list(command)

    def apply_environment(self, environment):
        return dict(environment)

    def close(self):
        self.closed = True


def _worker(tmp_path):
    return DynamicToolWorker(
        tmp_path,
        sandbox_factory=lambda _workspace: _Sandbox(),
        timeout_s=2,
    )


def _definition(**overrides):
    value = {
        "name": "sum_values",
        "description": "Sum a list of measured values.",
        "input_schema": {
            "type": "object",
            "properties": {
                "values": {
                    "type": "array",
                    "items": {"type": "number"},
                }
            },
            "required": ["values"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {"total": {"type": "number"}},
            "required": ["total"],
            "additionalProperties": False,
        },
        "implementation": (
            "def execute(args):\n"
            "    return {'total': sum(args['values'])}\n"
        ),
        "smoke_args": {"values": [1, 2]},
        "ttl_s": 60,
    }
    value.update(overrides)
    return value


def test_dynamic_tool_runs_in_one_shot_worker_and_proxy_keeps_behavior_visible(
    tmp_path,
):
    registry = DynamicToolRegistry(
        "session-1",
        tmp_path,
        tmp_path / "manifests",
        worker=_worker(tmp_path),
    )
    manifest = registry.define(_definition())
    proxy = registry.tools()[0]

    assert manifest.manifest_id.startswith("dyn-")
    assert isinstance(proxy, ProxyDynamicTool)
    assert proxy.execute(None, {"values": [2.5, 3.5]}) == {"total": 6.0}
    assert proxy.input_schema() == _definition()["input_schema"]
    stored = json.loads(
        (tmp_path / "manifests" / f"{manifest.manifest_id}.json").read_text()
    )
    assert stored["implementation"] == manifest.implementation
    assert stored["scope"] == "session"


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("import os\ndef execute(args): return {}", "imports are not allowed"),
        ("def execute(args): return open('secret')", "call is forbidden"),
        (
            "def execute(args): return getattr(args, '__class__')",
            "call is forbidden",
        ),
        ("def other(args): return args", "exactly one synchronous execute"),
        ("async def execute(args): return args", "synchronous execute"),
    ],
)
def test_dynamic_source_gate_rejects_host_escape_surfaces(source, message):
    with pytest.raises(ValueError, match=message):
        validate_dynamic_source(source)


def test_dynamic_worker_environment_does_not_inherit_host_secret(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENAI4S_LLM_API_KEY", "must-not-cross")
    observed = {}

    def run(command, **kwargs):
        observed.update(kwargs)
        return SimpleNamespace(returncode=0, stdout='{"total":3}', stderr="")

    monkeypatch.setattr("openai4s.tools.dynamic.subprocess.run", run)
    manifest = DynamicToolManifest(
        name="sum_values",
        description="sum",
        input_schema=_definition()["input_schema"],
        output_schema=_definition()["output_schema"],
        implementation=_definition()["implementation"],
        imports=(),
        permissions=(),
        scope="session",
        session_id="session-1",
        ttl_s=60,
        created_at=1,
        expires_at=100,
        manifest_id="dyn-test",
    )

    result = _worker(tmp_path).invoke(manifest, {"values": [1, 2]})

    assert result == {"total": 3}
    assert "OPENAI4S_LLM_API_KEY" not in observed["env"]


def test_dynamic_tools_fail_closed_without_enforced_os_sandbox(tmp_path):
    sandbox = _Sandbox()
    sandbox.status.enforced = False
    worker = DynamicToolWorker(
        tmp_path,
        sandbox_factory=lambda _workspace: sandbox,
    )
    manifest = DynamicToolManifest(
        name="sum_values",
        description="sum",
        input_schema=_definition()["input_schema"],
        output_schema=_definition()["output_schema"],
        implementation=_definition()["implementation"],
        imports=(),
        permissions=(),
        scope="session",
        session_id="session-1",
        ttl_s=60,
        created_at=1,
        expires_at=100,
        manifest_id="dyn-test",
    )

    with pytest.raises(RuntimeError, match="enforced OS sandbox"):
        worker.invoke(manifest, {"values": [1, 2]})
    assert sandbox.closed is True


def test_schema_validation_ttl_and_permissions_are_enforced(tmp_path):
    now = {"value": 10.0}
    registry = DynamicToolRegistry(
        "session-1",
        tmp_path,
        tmp_path / "manifests",
        worker=_worker(tmp_path),
        clock=lambda: now["value"],
    )
    registry.define(_definition(ttl_s=2))
    with pytest.raises(ValueError, match="arguments"):
        registry.invoke("sum_values", {"values": [1], "unknown": True})
    with pytest.raises(ValueError, match="cannot request"):
        registry.define(_definition(name="networked", permissions=["network"]))

    now["value"] = 12.1
    assert registry.get("sum_values") is None
    with pytest.raises(KeyError, match="expired"):
        registry.invoke("sum_values", {"values": [1]})


def test_worker_timeout_is_a_canonical_error(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "openai4s.tools.dynamic.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(args[0], kwargs.get("timeout"))
        ),
    )
    registry = DynamicToolRegistry(
        "session-1",
        tmp_path,
        tmp_path / "manifests",
        worker=_worker(tmp_path),
    )
    with pytest.raises(RuntimeError, match="timed out"):
        registry.define(_definition())


def test_project_or_global_promotion_requires_explicit_approval(tmp_path):
    denied = DynamicToolRegistry(
        "session-1",
        tmp_path,
        tmp_path / "denied",
        worker=_worker(tmp_path),
    )
    denied.define(_definition())
    with pytest.raises(PermissionError, match="human approval"):
        denied.promote("sum_values", "project")

    approved = DynamicToolRegistry(
        "session-2",
        tmp_path,
        tmp_path / "approved",
        worker=_worker(tmp_path),
        approval=lambda manifest, operation: operation == "promote:project",
    )
    approved.define(_definition())
    promoted = approved.promote("sum_values", "project")
    assert promoted.scope == "project"
    assert promoted.manifest_id != approved.get("sum_values").manifest_id


def test_session_catalog_define_list_execute_and_promote_are_host_gated(tmp_path):
    registry = DynamicToolRegistry(
        "session-catalog",
        tmp_path,
        tmp_path / "catalog-manifests",
        worker=_worker(tmp_path),
    )
    catalog = SessionToolCatalog(registry)

    with pytest.raises(PermissionError, match="Host approval"):
        catalog.define(_definition())
    manifest = catalog.define(_definition(), approved=True)
    assert manifest["name"] == "sum_values"
    assert "implementation" not in manifest
    assert catalog.list_dynamic() == [manifest]
    assert "sum_values" in {spec.name for spec in catalog.specs()}

    class Dispatcher:
        def __call__(self, method, args):
            tool = catalog.get_by_host_method(method)
            assert tool is not None
            return catalog.execute(tool.name, None, args[0])

    text, ok = execute_tool_call(
        Dispatcher(),
        {"name": "sum_values", "arguments": {"values": [2, 4]}},
        catalog,
    )
    assert ok is True
    assert '"total": 6' in text

    with pytest.raises(PermissionError, match="Host approval"):
        catalog.promote("sum_values", "project")
    promoted = catalog.promote("sum_values", "project", approved=True)
    assert promoted["scope"] == "project"


def test_session_manifests_restore_by_hash_and_tampering_stays_inert(tmp_path):
    storage = tmp_path / "restore-manifests"
    first = DynamicToolRegistry(
        "session-restore",
        tmp_path,
        storage,
        worker=_worker(tmp_path),
    )
    manifest = first.define(_definition(ttl_s=600))

    restored = DynamicToolRegistry(
        "session-restore",
        tmp_path,
        storage,
        worker=_worker(tmp_path),
    )
    assert restored.get("sum_values").manifest_id == manifest.manifest_id

    path = storage / f"{manifest.manifest_id}.json"
    record = json.loads(path.read_text("utf-8"))
    record["description"] = "tampered"
    path.write_text(json.dumps(record), "utf-8")
    rejected = DynamicToolRegistry(
        "session-restore",
        tmp_path,
        storage,
        worker=_worker(tmp_path),
    )
    assert rejected.get("sum_values") is None
    assert rejected.load_errors and "hash mismatch" in rejected.load_errors[0]
