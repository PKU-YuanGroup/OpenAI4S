"""Native current-session orchestration and capability discovery contracts."""

from __future__ import annotations

from typing import Any

from openai4s.config import Config
from openai4s.host_dispatch import build_dispatcher
from openai4s.server.session_domain import SessionDomainService
from openai4s.tools.capabilities import SearchCapabilitiesTool
from openai4s.tools.registry import get_tool, get_tool_by_host_method
from openai4s.tools.session import (
    CreateCheckpointTool,
    ForkSessionTool,
    PendingPermissionsTool,
    RevertPreviewTool,
    SessionStatusTool,
)


class RecordingRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def invoke(self, method: str, *arguments: Any) -> dict:
        self.calls.append((method, arguments))
        return {"method": method}


def test_registry_exposes_named_session_and_capability_classes():
    expected = {
        "search_capabilities": SearchCapabilitiesTool,
        "session_status": SessionStatusTool,
        "create_checkpoint": CreateCheckpointTool,
        "fork_session": ForkSessionTool,
        "revert_preview": RevertPreviewTool,
        "pending_permissions": PendingPermissionsTool,
    }

    for name, tool_type in expected.items():
        tool = get_tool(name)
        assert type(tool) is tool_type
        assert get_tool_by_host_method(tool.host_method) is tool

    # The two completion channels remain deliberately outside this catalogue.
    assert get_tool("submit_output") is None
    assert get_tool("bash") is None


def test_classes_keep_argument_normalization_with_their_behavior():
    runtime = RecordingRuntime()

    SearchCapabilitiesTool().execute(runtime, {"query": "checkpoint"})
    SessionStatusTool().execute(runtime, {"checkpoint_limit": 7})
    CreateCheckpointTool().execute(runtime, {"reason": "before analysis"})
    ForkSessionTool().execute(runtime, {"from_checkpoint_id": "cp-1"})
    RevertPreviewTool().execute(runtime, {"checkpoint_id": "cp-1"})
    PendingPermissionsTool().execute(runtime, {"limit": 10})

    assert runtime.calls == [
        ("search_capabilities", ({"query": "checkpoint"},)),
        ("session_status", ({"checkpoint_limit": 7},)),
        ("session_create_checkpoint", ({"reason": "before analysis"},)),
        ("session_fork", ({"from_checkpoint_id": "cp-1"},)),
        ("session_revert_preview", ({"checkpoint_id": "cp-1"},)),
        ("session_pending_permissions", ({"limit": 10},)),
    ]


def test_session_tool_policy_resources_and_exact_fork_source_precheck():
    for name in ("session_status", "revert_preview", "pending_permissions"):
        tool = get_tool(name)
        assert tool.read_only is True
        assert tool.requires_approval is False
        assert tool.side_effect_class == "read_only"

    for name in ("search_capabilities", "create_checkpoint", "fork_session"):
        tool = get_tool(name)
        assert tool.read_only is False
        assert tool.requires_approval is False
        assert tool.side_effect_class == "runtime_mutation"

    assert get_tool("search_capabilities").resource_keys({"query": "branch"}) == (
        "capability:catalog",
    )
    assert get_tool("create_checkpoint").resource_keys({}) == (
        "session:current",
        "checkpoint:head",
    )
    assert get_tool("fork_session").resource_keys({"from_message_id": "message-1"}) == (
        "session:current",
        "message:message-1",
        "branch:new",
    )
    assert get_tool("revert_preview").resource_keys({"checkpoint_id": "cp-1"}) == (
        "session:current",
        "checkpoint:cp-1",
    )

    fork = ForkSessionTool()
    assert fork.native_precheck({}) is not None
    assert (
        fork.native_precheck({"from_checkpoint_id": "cp-1", "from_cell_id": "cell-1"})
        is not None
    )
    assert fork.native_precheck({"from_cell_id": "cell-1"}) is None


def test_capability_search_is_always_visible_and_activates_session_group(tmp_path):
    dispatcher = build_dispatcher(
        Config(data_dir=tmp_path / "data"),
        workspace=tmp_path,
    )
    dispatcher.frame_id = dispatcher.store.new_frame(
        project_id="science",
        status="ready",
    )
    catalog = dispatcher.tool_catalog()

    initially_visible = {
        spec.name
        for spec in catalog.specs_for(
            [{"role": "user", "content": "Read the local notes."}]
        )
    }
    assert "search_capabilities" in initially_visible
    assert "create_checkpoint" not in initially_visible

    result = dispatcher(
        "search_capabilities",
        [{"query": "checkpoint branch pending approval"}],
    )

    assert "session" in {item["id"] for item in result["matched_groups"]}
    assert "session" in result["active_group_ids"]
    now_visible = {spec.name for spec in catalog.specs_for([])}
    assert {
        "session_status",
        "create_checkpoint",
        "fork_session",
        "revert_preview",
        "pending_permissions",
    } <= now_visible
    metadata = {item["id"]: item for item in catalog.group_metadata()}
    assert metadata["capabilities"]["always"] is True
    assert "checkpoint" in metadata["session"]["keywords"]


def test_dispatcher_routes_current_session_domain_and_redacts_approval_inputs(
    tmp_path,
):
    config = Config(data_dir=tmp_path / "data")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    analysis = workspace / "analysis.txt"
    analysis.write_text("version one", encoding="utf-8")
    dispatcher = build_dispatcher(config, workspace=workspace)
    root = dispatcher.store.new_frame(
        project_id="science",
        status="ready",
        name="Protein design",
    )
    dispatcher.frame_id = root
    branch_root = tmp_path / "branches"
    domain = SessionDomainService(
        dispatcher.store,
        data_dir=config.data_dir,
        workspace=lambda _root, branch: (
            workspace if branch == root else branch_root / branch
        ),
    )
    dispatcher.set_session_domain(domain)

    first_result = dispatcher(
        "session_create_checkpoint",
        [{"reason": "before analysis"}],
    )
    first_id = first_result["checkpoint"]["checkpoint_id"]
    analysis.write_text("version two", encoding="utf-8")
    second_result = dispatcher(
        "session_create_checkpoint",
        [{"reason": "after analysis", "expected_head": first_id}],
    )
    assert second_result["checkpoint"]["checkpoint_id"] != first_id

    preview = dispatcher(
        "session_revert_preview",
        [{"checkpoint_id": first_id}],
    )["preview"]
    assert preview["can_apply"] is True
    assert preview["workspace"]["writes"][0]["path"] == "analysis.txt"

    forked = dispatcher(
        "session_fork",
        [{"from_checkpoint_id": first_id, "name": "Alternative"}],
    )
    assert forked["ok"] is True
    assert forked["view_only"] is True
    assert forked["workspace_isolated"] is True
    assert forked["branch"]["source_kind"] == "checkpoint"

    dispatcher.store.create_permission_request(
        decision_id="perm-secret",
        root_frame_id=root,
        frame_id=root,
        project_id="science",
        tool="mcp_call",
        target="lab/send",
        side_effect_class="external_write",
        resource_keys=["mcp:lab"],
        payload={
            "type": "await_permission",
            "kind": "mcp",
            "title": "Call laboratory service",
            "input": {"api_key": "must-never-leak"},
        },
    )
    pending = dispatcher("session_pending_permissions", [{"limit": 10}])
    assert pending["count"] == 1
    assert pending["pending"][0]["decision_id"] == "perm-secret"
    assert "payload" not in pending["pending"][0]
    assert "input" not in pending["pending"][0]
    assert "must-never-leak" not in repr(pending)

    status = dispatcher("session_status", [{"checkpoint_limit": 10}])
    assert status["root_frame_id"] == root
    assert status["project_id"] == "science"
    assert status["checkpoint_count"] == 2
    assert status["pending_permission_count"] == 1
    assert status["capabilities"]["fork"]["enabled"] is True


def test_mutations_fail_closed_without_filesystem_aware_domain(tmp_path):
    dispatcher = build_dispatcher(
        Config(data_dir=tmp_path / "data"),
        workspace=tmp_path,
    )
    dispatcher.frame_id = dispatcher.store.new_frame(status="ready")

    result = dispatcher("session_create_checkpoint", [{"reason": "manual"}])

    assert set(result) == {"error"}
    assert "unavailable" in result["error"]
    status = dispatcher("session_status", [{}])
    assert status["recovery"]["state"] == "unavailable"
