"""End-to-end contracts for class-based session tool composition."""

from __future__ import annotations

from openai4s.config import Config
from openai4s.host_dispatch import build_dispatcher
from openai4s.tools.catalog import SessionToolCatalog
from openai4s.tools.dynamic import DynamicToolRegistry
from openai4s.tools.registry import execute_tool_call


class _Worker:
    def invoke(self, manifest, arguments):
        del manifest
        return {"total": sum(arguments["values"])}


def _definition():
    return {
        "name": "sum_values",
        "description": "Sum measured values.",
        "input_schema": {
            "type": "object",
            "properties": {"values": {"type": "array", "items": {"type": "number"}}},
            "required": ["values"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {"total": {"type": "number"}},
            "required": ["total"],
            "additionalProperties": False,
        },
        "implementation": "def execute(args):\n    return {'total': sum(args['values'])}\n",
        "smoke_args": {"values": [1, 2]},
        "ttl_s": 60,
    }


def test_host_gates_dynamic_lifecycle_then_executes_proxy_in_same_catalog(tmp_path):
    config = Config(data_dir=tmp_path / "data")
    dispatcher = build_dispatcher(config, workspace=tmp_path)
    dispatcher.frame_id = dispatcher.store.new_frame(project_id="project-tools")
    registry = DynamicToolRegistry(
        dispatcher.frame_id,
        tmp_path,
        tmp_path / "manifests",
        worker=_Worker(),
    )
    catalog = SessionToolCatalog(registry)
    dispatcher.tool_catalog = lambda: catalog

    dispatcher.store.set_permission_rule(
        scope="global",
        scope_id="",
        tool="dynamic_tool_define",
        pattern="*",
        decision="deny",
    )
    denied, denied_ok = execute_tool_call(
        dispatcher,
        {"name": "define_dynamic_tool", "arguments": _definition()},
        catalog,
    )
    assert denied_ok is False
    assert "Permission denied" in denied
    assert registry.get("sum_values") is None

    for method in ("dynamic_tool_define", "dynamic_tool_promote"):
        dispatcher.store.set_permission_rule(
            scope="global",
            scope_id="",
            tool=method,
            pattern="*",
            decision="allow",
        )
    defined, defined_ok = execute_tool_call(
        dispatcher,
        {"name": "define_dynamic_tool", "arguments": _definition()},
        catalog,
    )
    assert defined_ok is True
    assert "sum_values" in defined
    assert "sum_values" in {spec.name for spec in catalog.specs()}

    output, output_ok = execute_tool_call(
        dispatcher,
        {"name": "sum_values", "arguments": {"values": [3, 4]}},
        catalog,
    )
    assert output_ok is True
    assert '"total": 7' in output

    promoted, promoted_ok = execute_tool_call(
        dispatcher,
        {
            "name": "promote_dynamic_tool",
            "arguments": {"name": "sum_values", "scope": "project"},
        },
        catalog,
    )
    assert promoted_ok is True
    assert '"scope": "project"' in promoted


def test_progressive_specs_keep_core_dynamic_and_activate_relevant_groups(tmp_path):
    registry = DynamicToolRegistry(
        "session-progressive",
        tmp_path,
        tmp_path / "progressive-manifests",
        worker=_Worker(),
    )
    catalog = SessionToolCatalog(registry)
    registry.define(_definition())

    base = {
        spec.name
        for spec in catalog.specs_for(
            [
                {"role": "system", "content": "MCP artifact GPU plan delegate"},
                {"role": "user", "content": "Read a local note."},
            ]
        )
    }
    assert {
        "list_dir",
        "search_skills",
        "define_dynamic_tool",
        "sum_values",
    } <= base
    assert base.isdisjoint(
        {"list_artifacts", "delegate_task", "call_mcp_tool", "read_plan"}
    )

    mcp_and_artifacts = {
        spec.name
        for spec in catalog.specs_for(
            [
                {
                    "role": "user",
                    "content": "Use the MCP connector and save the report artifact.",
                }
            ]
        )
    }
    assert {"call_mcp_tool", "list_artifacts", "save_artifact"} <= mcp_and_artifacts
    # Activation is monotonic for the session, so a later terse observation
    # cannot make provider-visible tools disappear mid-protocol.
    assert {"call_mcp_tool", "list_artifacts"} <= {
        spec.name for spec in catalog.specs_for([{"role": "tool", "content": "done"}])
    }

    catalog.activate_groups("remote")
    assert "remote_gpu_status" in {spec.name for spec in catalog.specs_for([])}
    groups = {item["id"]: item for item in catalog.group_metadata()}
    assert groups["core"]["always"] is True
    assert groups["remote"]["active"] is True
