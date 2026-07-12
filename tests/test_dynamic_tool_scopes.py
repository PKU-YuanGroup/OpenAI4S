"""Project/global Dynamic Tool consumption, activation, and rollback contracts."""

from __future__ import annotations

import json

import pytest

from openai4s.config import Config
from openai4s.host_dispatch import build_dispatcher
from openai4s.tools.catalog import SessionToolCatalog
from openai4s.tools.dynamic import DynamicToolRegistry
from openai4s.tools.registry import execute_tool_call


class _Worker:
    def __init__(self) -> None:
        self.calls = []

    def invoke(self, manifest, arguments):
        self.calls.append((manifest.manifest_id, manifest.scope, dict(arguments)))
        return {"value": manifest.description}


def _definition(name: str, marker: str) -> dict:
    return {
        "name": name,
        "description": marker,
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        "implementation": (
            "def execute(args):\n" f"    return {{'value': {marker!r}}}\n"
        ),
        "smoke_args": {},
        "ttl_s": 600,
    }


def _registry(
    tmp_path,
    root: str,
    project: str,
    *,
    worker: _Worker | None = None,
) -> DynamicToolRegistry:
    return DynamicToolRegistry(
        root,
        tmp_path / "workspace",
        tmp_path / "sessions" / root,
        project_id=project,
        scope_storage_dir=tmp_path / "shared",
        worker=worker or _Worker(),
    )


def test_promoted_versions_are_consumed_only_in_their_bound_scope(tmp_path):
    author = _registry(tmp_path, "root-author", "project-a")
    author.define(_definition("project_method", "project-a-version"))
    project_version = author.promote("project_method", "project", approved=True)
    author.define(_definition("global_method", "global-version"))
    global_version = author.promote("global_method", "global", approved=True)

    same_project_worker = _Worker()
    same_project = _registry(
        tmp_path,
        "root-same-project",
        "project-a",
        worker=same_project_worker,
    )
    other_project = _registry(tmp_path, "root-other-project", "project-b")

    assert same_project.get("project_method").manifest_id == project_version.manifest_id
    assert same_project.get("global_method").manifest_id == global_version.manifest_id
    assert other_project.get("project_method") is None
    assert other_project.get("global_method").manifest_id == global_version.manifest_id
    assert same_project.invoke("project_method", {}) == {"value": "project-a-version"}
    assert same_project_worker.calls[-1][1] == "project"

    project_record = next(
        item
        for item in author.versions(name="project_method", scope="project")["versions"]
        if item["manifest_id"] == project_version.manifest_id
    )
    assert project_record["scope_id"] == "project-a"
    assert project_record["source_project_id"] == "project-a"
    assert project_record["source_root_frame_id"] == "root-author"

    cross_project_global = other_project.versions(name="global_method", scope="global")
    assert cross_project_global["versions"][0]["source_project_id"] is None
    assert cross_project_global["versions"][0]["source_root_frame_id"] is None
    assert cross_project_global["events"][0]["actor_project_id"] is None
    assert cross_project_global["events"][0]["actor_root_frame_id"] is None


def test_dispatcher_binds_catalog_to_canonical_project_and_root(tmp_path):
    dispatcher = build_dispatcher(
        Config(data_dir=tmp_path / "data"), workspace=tmp_path
    )
    first_root = dispatcher.store.new_frame(project_id="project-a")
    dispatcher.frame_id = first_root
    first = dispatcher.tool_catalog().dynamic_registry
    assert first.session_id == first_root
    assert first.project_id == "project-a"

    second_root = dispatcher.store.new_frame(project_id="project-b")
    dispatcher.frame_id = second_root
    second = dispatcher.tool_catalog().dynamic_registry
    assert second is not first
    assert second.session_id == second_root
    assert second.project_id == "project-b"
    assert first.scope_store.root == second.scope_store.root


def test_resolution_priority_is_session_then_project_then_global(tmp_path):
    global_author = _registry(tmp_path, "root-global", "project-a")
    global_author.define(_definition("priority_method", "global"))
    global_author.promote("priority_method", "global", approved=True)

    project_author = _registry(tmp_path, "root-project", "project-a")
    assert project_author.get("priority_method").description == "global"
    # A session definition may intentionally shadow a lower scope.
    project_author.define(_definition("priority_method", "project"))
    project_author.promote("priority_method", "project", approved=True)

    local = _registry(tmp_path, "root-local", "project-a")
    assert local.get("priority_method").description == "project"
    local.define(_definition("priority_method", "session"))
    assert local.get("priority_method").scope == "session"
    assert local.get("priority_method").description == "session"

    other_project = _registry(tmp_path, "root-other", "project-b")
    assert other_project.get("priority_method").description == "global"


def test_versions_activate_rollback_and_exact_proxy_are_auditable(tmp_path):
    first_author = _registry(tmp_path, "root-v1", "project-a")
    first_author.define(_definition("versioned_method", "version-one"))
    first = first_author.promote("versioned_method", "project", approved=True)

    second_author = _registry(tmp_path, "root-v2", "project-a")
    second_author.define(_definition("versioned_method", "version-two"))
    second = second_author.promote("versioned_method", "project", approved=True)

    worker = _Worker()
    consumer = _registry(
        tmp_path,
        "root-consumer",
        "project-a",
        worker=worker,
    )
    old_proxy = consumer.tools()[0]
    assert old_proxy.manifest.manifest_id == second.manifest_id

    activated = consumer.activate(
        "versioned_method",
        "project",
        first.manifest_id,
        approved=True,
    )
    assert activated.manifest_id == first.manifest_id
    assert consumer.get("versioned_method").manifest_id == first.manifest_id

    # A ProxyTool executes the exact immutable version it advertised even if an
    # activation changes between provider declaration and invocation.
    assert old_proxy.execute(None, {}) == {"value": "version-two"}
    assert worker.calls[-1][0] == second.manifest_id

    rolled_back = consumer.rollback("versioned_method", "project", approved=True)
    assert rolled_back.manifest_id == second.manifest_id
    assert consumer.get("versioned_method").manifest_id == second.manifest_id

    history = consumer.versions(name="versioned_method", scope="project")
    assert {item["manifest_id"] for item in history["versions"]} == {
        first.manifest_id,
        second.manifest_id,
    }
    assert [event["operation"] for event in history["events"]] == [
        "promote",
        "promote",
        "activate",
        "rollback",
    ]
    assert sum(bool(item["active"]) for item in history["versions"]) == 1
    assert "implementation" not in repr(history)


def test_repromoting_identical_content_reuses_version_but_audits_operation(tmp_path):
    registry = _registry(tmp_path, "root-repeat", "project-a")
    registry.define(_definition("repeat_method", "same-content"))
    first = registry.promote("repeat_method", "project", approved=True)
    second = registry.promote("repeat_method", "project", approved=True)

    assert second.manifest_id == first.manifest_id
    history = registry.versions(name="repeat_method", scope="project")
    assert len(history["versions"]) == 1
    assert [event["operation"] for event in history["events"]] == [
        "promote",
        "promote",
    ]


def test_cross_project_activation_and_tampered_scoped_manifest_fail_closed(tmp_path):
    author = _registry(tmp_path, "root-author", "project-a")
    author.define(_definition("isolated_method", "private-to-a"))
    version = author.promote("isolated_method", "project", approved=True)

    other = _registry(tmp_path, "root-other", "project-b")
    with pytest.raises(KeyError, match="not a project version"):
        other.activate(
            "isolated_method",
            "project",
            version.manifest_id,
            approved=True,
        )

    path = tmp_path / "shared" / "manifests" / f"{version.manifest_id}.json"
    record = json.loads(path.read_text("utf-8"))
    record["source_project_id"] = "project-b"
    path.write_text(json.dumps(record), encoding="utf-8")
    reopened = _registry(tmp_path, "root-reopened", "project-a")
    assert reopened.get("isolated_method") is None
    assert any("mismatch" in error for error in reopened.load_errors)


def test_class_tools_gate_activation_and_rollback_through_host(tmp_path):
    registry = _registry(tmp_path, "root-host", "project-a")
    registry.define(_definition("host_versioned", "one"))
    first = registry.promote("host_versioned", "project", approved=True)

    second_registry = _registry(tmp_path, "root-host-v2", "project-a")
    second_registry.define(_definition("host_versioned", "two"))
    second = second_registry.promote("host_versioned", "project", approved=True)

    consumer_registry = _registry(tmp_path, "root-host-consumer", "project-a")
    catalog = SessionToolCatalog(consumer_registry)
    dispatcher = build_dispatcher(
        Config(data_dir=tmp_path / "data"), workspace=tmp_path
    )
    dispatcher.frame_id = dispatcher.store.new_frame(project_id="project-a")
    dispatcher.tool_catalog = lambda: catalog

    dispatcher.store.set_permission_rule(
        scope="global",
        scope_id="",
        tool="dynamic_tool_activate",
        pattern="*",
        decision="deny",
    )
    denied, ok = execute_tool_call(
        dispatcher,
        {
            "name": "activate_dynamic_tool_version",
            "arguments": {
                "name": "host_versioned",
                "scope": "project",
                "manifest_id": first.manifest_id,
            },
        },
        catalog,
    )
    assert ok is False and "Permission denied" in denied
    assert consumer_registry.get("host_versioned").manifest_id == second.manifest_id

    for method in ("dynamic_tool_activate", "dynamic_tool_rollback"):
        dispatcher.store.set_permission_rule(
            scope="global",
            scope_id="",
            tool=method,
            pattern="*",
            decision="allow",
        )
    activated, ok = execute_tool_call(
        dispatcher,
        {
            "name": "activate_dynamic_tool_version",
            "arguments": {
                "name": "host_versioned",
                "scope": "project",
                "manifest_id": first.manifest_id,
            },
        },
        catalog,
    )
    assert ok is True and first.manifest_id in activated

    rolled_back, ok = execute_tool_call(
        dispatcher,
        {
            "name": "rollback_dynamic_tool_version",
            "arguments": {"name": "host_versioned", "scope": "project"},
        },
        catalog,
    )
    assert ok is True and second.manifest_id in rolled_back

    listed, ok = execute_tool_call(
        dispatcher,
        {
            "name": "list_dynamic_tool_versions",
            "arguments": {"name": "host_versioned", "scope": "project"},
        },
        catalog,
    )
    assert ok is True
    assert '"count": 2' in listed
    assert '"operation": "rollback"' in listed
