from __future__ import annotations

import hashlib
import platform

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod


class _Hub:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.dropped: list[str] = []

    def emitter(self, root_frame_id):
        def emit(event):
            event.setdefault("root_frame_id", root_frame_id)
            self.events.append(event)

        return emit

    def broadcast(self, root_frame_id, event):
        self.emitter(root_frame_id)(event)

    def has_subscriber(self, root_frame_id):
        del root_frame_id
        return False

    def drop_frame(self, root_frame_id):
        self.dropped.append(root_frame_id)


class _InspectableKernel:
    def __init__(self, variables) -> None:
        self.variables = variables
        self.live = True
        self.pid = 8127
        self.python = "/env/bin/python"
        self.env_name = "base"
        self.env_root = "/env"
        self.cwd = "/workspace"

    def is_alive(self):
        return self.live

    def inspect_variables(self, *, limit=200):
        return {
            "variables": self.variables[:limit],
            "truncated": len(self.variables) > limit,
        }

    def shutdown(self):
        self.live = False


def _setup(tmp_path):
    config = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    hub = _Hub()
    runner = gateway_mod.SessionRunner(config, hub, start_idle_sweeper=False)
    frame_id = runner.store.new_frame(
        kind="turn", project_id="project-domain", status="ready"
    )
    handler_class = gateway_mod.make_handler(config, hub, runner)
    handler = object.__new__(handler_class)
    return runner, handler, frame_id


def _call(handler, method, path, *, body=None, query=None):
    replies = []
    handler._query = lambda: query or {}
    handler._body = lambda: body or {}
    handler._json = lambda value, code=200: replies.append((code, value))
    handler._send = lambda code, data, content_type, extra=None: replies.append(
        (code, data, content_type, extra or {})
    )
    handler._api(method, path)
    assert replies
    return replies[-1]


def test_delete_frame_drops_its_websocket_resume_window(tmp_path):
    runner, handler, frame_id = _setup(tmp_path)
    try:
        code, result = _call(handler, "DELETE", f"/frames/{frame_id}")

        assert code == 200 and result == {"ok": True}
        assert runner.store.get_frame(frame_id) is None
        assert runner.hub.dropped == [frame_id]
    finally:
        runner.close()


def test_checkpoint_fork_and_workbench_routes_share_domain_service(tmp_path):
    runner, handler, frame_id = _setup(tmp_path)
    workspace = runner.workspace_for(frame_id)
    (workspace / "analysis.txt").write_text("checkpointed\n", "utf-8")

    code, branches = _call(handler, "GET", f"/frames/{frame_id}/branches")
    assert code == 200
    assert branches["current_branch_id"] == frame_id
    assert branches["capabilities"]["checkpoint"]["enabled"] is True
    assert branches["capabilities"]["promote"]["enabled"] is True

    code, checkpoint = _call(
        handler,
        "POST",
        f"/frames/{frame_id}/branches/checkpoints",
        body={"reason": "browser"},
    )
    assert code == 200
    checkpoint_id = checkpoint["checkpoint_id"]

    code, forked = _call(
        handler,
        "POST",
        f"/frames/{frame_id}/branches/fork",
        body={"from_checkpoint_id": checkpoint_id, "name": "alternative"},
    )
    assert code == 200
    branch_id = forked["branch_id"]
    branch_workspace = runner.workspace_for_branch(frame_id, branch_id)
    assert branch_workspace != workspace
    assert (branch_workspace / "analysis.txt").read_text("utf-8") == "checkpointed\n"

    code, timeline = _call(handler, "GET", f"/frames/{frame_id}/action-timeline")
    assert code == 200
    assert "checkpoint" in {group["kind"] for group in timeline["groups"]}
    code, branch_timeline = _call(
        handler,
        "GET",
        f"/frames/{frame_id}/action-timeline",
        query={"branch_id": [branch_id]},
    )
    assert code == 200
    assert "branch" in {group["kind"] for group in branch_timeline["groups"]}
    assert "canonical_arguments" not in repr(timeline)

    code, context = _call(handler, "GET", f"/frames/{frame_id}/context")
    assert code == 200 and "layers" in context
    code, security = _call(handler, "GET", f"/frames/{frame_id}/security")
    assert code == 200 and security["sandbox"]["state"] == "not_started"
    code, delegations = _call(handler, "GET", f"/frames/{frame_id}/delegations")
    assert code == 200
    assert delegations["root_frame_id"] == frame_id
    assert delegations["children"] == []
    code, recovery = _call(handler, "GET", f"/frames/{frame_id}/recovery")
    assert code == 200 and recovery["root_frame_id"] == frame_id
    runner.close()


def test_promote_route_freezes_scientific_cell_as_markdown_artifact(tmp_path):
    runner, handler, frame_id = _setup(tmp_path)
    cell_id = runner.store.log_cell(
        frame_id=frame_id,
        code="print('hi')\ndf.to_csv('out.csv')",
        result={"stdout": "hi", "stderr": "", "error": None},
        root_frame_id=frame_id,
        project_id="project-domain",
        cell_index=1,
        visibility="scientific",
        files_written=["out.csv"],
    )

    # The cell must surface in the scientific execution log the route reads —
    # this is the exact contract (entries[].producing_cell_id) the handler hangs
    # on, so assert it before promoting.
    code, log = _call(handler, "GET", f"/frames/{frame_id}/execution-log")
    assert code == 200
    assert any(entry["producing_cell_id"] == cell_id for entry in log["entries"])

    code, meta = _call(
        handler,
        "POST",
        f"/frames/{frame_id}/artifacts/promote",
        body={"cell_id": cell_id},
    )
    assert code == 200
    assert meta["filename"].endswith(".md")
    promoted = list((runner.workspace_for(frame_id) / "promoted").glob("*.md"))
    assert len(promoted) == 1
    text = promoted[0].read_text("utf-8")
    assert "print('hi')" in text  # cell source frozen
    assert "hi" in text  # stdout preserved
    assert "`out.csv`" in text  # produced-file pointer preserved
    runner.close()


def test_promote_route_rejects_unknown_cell(tmp_path):
    runner, handler, frame_id = _setup(tmp_path)
    with pytest.raises(gateway_mod.GatewayError) as excinfo:
        _call(
            handler,
            "POST",
            f"/frames/{frame_id}/artifacts/promote",
            body={"cell_id": "does-not-exist"},
        )
    assert excinfo.value.code == 404
    runner.close()


def test_branch_activation_restores_checkpoint_projection_and_runtime_binding(
    tmp_path,
):
    runner, handler, frame_id = _setup(tmp_path)
    workspace = runner.workspace_for(frame_id)
    managed = workspace / "analysis.txt"
    try:
        managed.write_text("baseline\n", encoding="utf-8")
        runner.store.update_frame(frame_id, runtime_env="base")
        runner.store.set_capability_enabled(
            "skill",
            "branch-skill",
            False,
            scope="session",
            scope_id=frame_id,
        )
        runner.store.set_permission_rule(
            scope="conversation",
            scope_id=frame_id,
            tool="web_fetch",
            pattern="example.org/*",
            decision="allow",
        )
        baseline_version = runner.store.save_artifact(
            path=str(managed),
            filename="analysis.txt",
            content_type="text/plain",
            size_bytes=managed.stat().st_size,
            checksum=hashlib.sha256(managed.read_bytes()).hexdigest(),
            frame_id=frame_id,
            root_frame_id=frame_id,
            project_id="project-domain",
        )
        code, checkpoint = _call(
            handler,
            "POST",
            f"/frames/{frame_id}/branches/checkpoints",
            body={"reason": "branch baseline"},
        )
        assert code == 200
        code, forked = _call(
            handler,
            "POST",
            f"/frames/{frame_id}/branches/fork",
            body={"from_checkpoint_id": checkpoint["checkpoint_id"]},
        )
        assert code == 200
        branch_id = forked["branch_id"]

        managed.write_text("root advanced\n", encoding="utf-8")
        runner.store.set_capability_enabled(
            "skill",
            "branch-skill",
            True,
            scope="session",
            scope_id=frame_id,
        )
        runner.store.set_permission_rule(
            scope="conversation",
            scope_id=frame_id,
            tool="web_fetch",
            pattern="example.org/*",
            decision="deny",
        )
        runner.store.save_artifact(
            path=str(managed),
            filename="analysis.txt",
            content_type="text/plain",
            size_bytes=managed.stat().st_size,
            checksum=hashlib.sha256(managed.read_bytes()).hexdigest(),
            frame_id=frame_id,
            root_frame_id=frame_id,
            project_id="project-domain",
            artifact_id=baseline_version["artifact_id"],
        )
        _call(
            handler,
            "POST",
            f"/frames/{frame_id}/branches/checkpoints",
            body={"reason": "root advanced"},
        )

        code, activated = _call(
            handler,
            "POST",
            f"/frames/{frame_id}/branches/{branch_id}/activate",
        )

        assert code == 200 and activated["status"] == "active"
        assert runner.store.active_session_branch(frame_id) == branch_id
        state = runner._existing_state(frame_id)
        assert state is not None and state.branch_id == branch_id
        assert state.workspace == runner.workspace_for_branch(frame_id, branch_id)
        assert (state.workspace / "analysis.txt").read_text("utf-8") == "baseline\n"
        assert (
            runner.store.get_artifact(baseline_version["artifact_id"])[
                "latest_version_id"
            ]
            == baseline_version["version_id"]
        )
        assert (
            runner.store.capability_state(
                project_id="project-domain", session_id=frame_id
            ).is_enabled("skill", "branch-skill")
            is False
        )
        assert (
            runner.store.resolve_permission(
                root_frame_id=frame_id,
                project_id="project-domain",
                tool="web_fetch",
                pattern_input="example.org/item",
            )
            == "allow"
        )
        assert activated["dimensions"]["provider_history"]["applied"] is True

        code, branches = _call(handler, "GET", f"/frames/{frame_id}/branches")
        assert code == 200 and branches["current_branch_id"] == branch_id
        active = next(
            item for item in branches["branches"] if item["branch_id"] == branch_id
        )
        assert active["active"] is True and active["view_only"] is False
    finally:
        runner.close()


def test_branch_revert_and_undo_routes_complete_the_workbench_contract(tmp_path):
    runner, handler, frame_id = _setup(tmp_path)
    workspace = runner.workspace_for(frame_id)
    managed = workspace / "analysis.txt"
    try:
        managed.write_text("first\n", "utf-8")
        runner.store.add_message(
            root_frame_id=frame_id,
            branch_id=frame_id,
            role="user",
            content="provider prefix",
        )
        runner.store.append_action_group(
            root_frame_id=frame_id,
            branch_id=frame_id,
            turn_id="turn-provider-prefix",
            kind="user",
            assistant_message={"role": "user", "content": "provider prefix"},
        )
        code, first = _call(
            handler,
            "POST",
            f"/frames/{frame_id}/branches/checkpoints",
            body={"reason": "first"},
        )
        assert code == 200

        managed.write_text("second\n", "utf-8")
        runner.store.add_message(
            root_frame_id=frame_id,
            branch_id=frame_id,
            role="user",
            content="provider abandoned",
        )
        runner.store.append_action_group(
            root_frame_id=frame_id,
            branch_id=frame_id,
            turn_id="turn-provider-abandoned",
            kind="user",
            assistant_message={"role": "user", "content": "provider abandoned"},
        )
        code, _second = _call(
            handler,
            "POST",
            f"/frames/{frame_id}/branches/checkpoints",
            body={"reason": "second"},
        )
        assert code == 200
        state = runner._state(frame_id, "project-domain")
        runner._seed_messages(state)
        assert "provider abandoned" in {
            message.get("content") for message in state.messages
        }

        code, preview = _call(
            handler,
            "POST",
            f"/frames/{frame_id}/branches/revert-preview",
            body={
                "branch_id": frame_id,
                "target_checkpoint_id": first["checkpoint_id"],
            },
        )
        assert code == 200
        assert preview["preview"]["can_apply"] is True

        code, reverted = _call(
            handler,
            "POST",
            f"/frames/{frame_id}/branches/revert",
            body={
                "branch_id": frame_id,
                "target_checkpoint_id": first["checkpoint_id"],
            },
        )
        assert code == 200 and reverted["ok"] is True
        assert managed.read_text("utf-8") == "first\n"
        assert "provider abandoned" not in {
            message.get("content") for message in state.messages
        }
        code, visible = _call(handler, "GET", f"/frames/{frame_id}/messages")
        assert code == 200
        assert [message["content"] for message in visible["messages"]] == [
            "provider prefix"
        ]
        revert_checkpoint_id = reverted["checkpoint"]["checkpoint_id"]

        code, undone = _call(
            handler,
            "POST",
            f"/frames/{frame_id}/revert/undo",
            body={
                "branch_id": frame_id,
                "revert_checkpoint_id": revert_checkpoint_id,
            },
        )
        assert code == 200 and undone["ok"] is True
        assert managed.read_text("utf-8") == "second\n"
        assert "provider abandoned" in {
            message.get("content") for message in state.messages
        }
    finally:
        runner.close()


def test_notebook_export_and_renderer_routes_return_immutable_descriptors(tmp_path):
    runner, handler, frame_id = _setup(tmp_path)
    code, error = _call(
        handler,
        "GET",
        f"/frames/{frame_id}/notebook/export",
        query={"language": ["javascript"]},
    )
    assert code == 400
    assert error == {"error": "notebook language must be python, r, or bundle"}

    binary = _call(
        handler,
        "GET",
        f"/frames/{frame_id}/notebook/export",
        query={"language": ["python"]},
    )
    assert binary[0] == 200
    assert binary[2] == "application/x-ipynb+json"
    assert b'"nbformat": 4' in binary[1]
    assert len(binary[3]["X-Content-SHA256"]) == 64

    image = runner.workspace_for(frame_id) / "plot.png"
    image.write_bytes(b"not-a-rendered-image")
    artifact = runner.store.save_artifact(
        path=str(image),
        filename="plot.png",
        content_type="image/png",
        size_bytes=image.stat().st_size,
        checksum=hashlib.sha256(image.read_bytes()).hexdigest(),
        frame_id=frame_id,
        root_frame_id=frame_id,
        project_id="project-domain",
    )
    code, descriptor = _call(
        handler,
        "GET",
        f"/artifacts/{artifact['artifact_id']}/renderer",
        query={"root_frame_id": [frame_id]},
    )
    assert code == 200
    assert descriptor["renderer"]["renderer_id"] == "image"
    stored = runner.store.get_artifact(artifact["artifact_id"])
    assert descriptor["version_id"] == stored["latest_version_id"]
    assert descriptor["trusted_html"] is False
    runner.close()


def test_unknown_session_workbench_routes_fail_with_one_404_contract(tmp_path):
    runner, handler, _frame_id = _setup(tmp_path)
    routes = (
        "/frames/missing/action-timeline",
        "/frames/missing/execution-queue",
        "/frames/missing/context",
        "/frames/missing/security",
        "/frames/missing/recovery",
        "/frames/missing/recovery/actions",
        "/frames/missing/kernel/variables",
        "/frames/missing/branches",
        "/frames/missing/checkpoints",
        "/frames/missing/branches/checkpoints",
        "/frames/missing/revert/operations",
        "/frames/missing/notebook/export",
        "/frames/missing/execution",
    )
    try:
        for route in routes:
            with pytest.raises(gateway_mod.GatewayError) as caught:
                _call(handler, "GET", route)
            assert caught.value.code == 404
            assert caught.value.message == "session not found"
    finally:
        runner.close()


def test_restart_permission_route_requires_explicit_continuation(tmp_path):
    runner, _handler, frame_id = _setup(tmp_path)
    payload = {
        "type": "await_permission",
        "frame_id": frame_id,
        "decision_id": "perm-route-restart",
        "tool": "mcp_call",
        "target": "lab/send",
    }
    runner.store.append_tool_action_group(
        root_frame_id=frame_id,
        turn_id="turn-before-restart",
        assistant_message={
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-before-restart",
                    "name": "mcp_call",
                    "arguments": {"server": "lab", "tool": "send"},
                }
            ],
        },
        events=[
            {
                "type": "proposed",
                "tool_call_id": "call-before-restart",
                "canonical_arguments": {
                    "name": "mcp_call",
                    "arguments": {"server": "lab", "tool": "send"},
                },
            }
        ],
    )
    runner.store.create_permission_request(
        decision_id="perm-route-restart",
        root_frame_id=frame_id,
        frame_id=frame_id,
        project_id="project-domain",
        tool="mcp_call",
        target="lab/send",
        payload=payload,
    )
    runner.close()
    runner.store.close()

    config = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    hub = _Hub()
    restarted = gateway_mod.SessionRunner(config, hub, start_idle_sweeper=False)
    handler_class = gateway_mod.make_handler(config, hub, restarted)
    handler = object.__new__(handler_class)
    try:
        assert restarted._sessions == {}
        code, security = _call(handler, "GET", f"/frames/{frame_id}/security")
        assert code == 200
        assert security["permission"]["pending_count"] == 1

        code, resolution = _call(
            handler,
            "POST",
            f"/frames/{frame_id}/decision",
            body={
                "decision_id": "perm-route-restart",
                "allow": True,
                "scope": "once",
            },
        )
        assert code == 200
        assert resolution["ok"] is True
        assert resolution["decision_id"] == "perm-route-restart"
        assert resolution["resolution_context"] == "after_restart"
        assert resolution["requires_continue"] is True
        assert resolution["original_action_executed"] is False
        assert resolution["continuation_authorization"] == "once"
        assert resolution["continuation_expires_at"] is not None
        request = restarted.store.get_permission_request("perm-route-restart")
        assert request["state"] == "allowed"
        assert request["continuation_consumed_at"] is None
        marker = restarted.store.list_action_groups(frame_id)[-1]
        assert marker["kind"] == "permission_resolution"
        assert "arguments" not in repr(marker["events"][0]["result"])

        events = [
            event for event in hub.events if event.get("type") == "permission_resolved"
        ]
        assert len(events) == 1
        assert events[0]["requires_continue"] is True
        assert events[0]["original_action_executed"] is False
    finally:
        restarted.close()
        restarted.store.close()


def test_variable_inspector_route_never_starts_workers_and_is_idle_only(tmp_path):
    runner, handler, frame_id = _setup(tmp_path)
    try:
        code, never_started = _call(
            handler,
            "GET",
            f"/frames/{frame_id}/kernel/variables",
            query={"language": ["python"]},
        )
        assert code == 200
        assert never_started["available"] is False
        assert never_started["state"] == "not_started"
        assert frame_id not in runner._sessions

        state = runner._state(frame_id, "project-domain")
        kernel = _InspectableKernel(
            [
                {
                    "name": "score",
                    "type": "float",
                    "preview": 0.93,
                    "fingerprint": "b" * 64,
                }
            ]
        )
        lease = state.kernels.ensure("python", "base", lambda: kernel)
        attempts_before = runner.store.list_execution_attempts(root_frame_id=frame_id)
        cells_before = runner.store.cell_count(frame_id)

        code, active = _call(
            handler,
            "GET",
            f"/frames/{frame_id}/kernel/variables",
            query={"language": ["python"]},
        )
        assert code == 200
        assert active["available"] is True
        assert active["generation_id"] == lease.generation_id
        assert active["variables"][0]["name"] == "score"
        assert active["state_revision"] == 0
        assert runner.store.cell_count(frame_id) == cells_before
        assert (
            runner.store.list_execution_attempts(root_frame_id=frame_id)
            == attempts_before
        )

        r_kernel = _InspectableKernel(
            [{"name": "samples", "type": "integer", "length": 3}]
        )
        r_lease = state.kernels.ensure("r", "r", lambda: r_kernel)
        code, r_active = _call(
            handler,
            "GET",
            f"/frames/{frame_id}/kernel/variables",
            query={"language": ["r"]},
        )
        assert code == 200
        assert r_active["available"] is True
        assert r_active["generation_id"] == r_lease.generation_id
        assert r_active["variables"] == [
            {"name": "samples", "type": "integer", "length": 3}
        ]

        state.turn_lock.acquire()
        try:
            code, busy = _call(
                handler,
                "GET",
                f"/frames/{frame_id}/kernel/variables",
                query={"language": ["python"]},
            )
        finally:
            state.turn_lock.release()
        assert code == 200
        assert busy["available"] is False and busy["state"] == "busy"

        recovering = runner.variables._recovering
        runner.variables._recovering = lambda _root: True
        try:
            code, restoring = _call(
                handler,
                "GET",
                f"/frames/{frame_id}/kernel/variables",
                query={"language": ["python"]},
            )
        finally:
            runner.variables._recovering = recovering
        assert code == 200
        assert restoring["available"] is False
        assert restoring["state"] == "restoring"

        state.kernels.stop("python", manual=True)
        code, ended = _call(
            handler,
            "GET",
            f"/frames/{frame_id}/kernel/variables",
            query={"language": ["python"]},
        )
        assert code == 200
        assert ended["available"] is False and ended["state"] == "ended"

        code, invalid = _call(
            handler,
            "GET",
            f"/frames/{frame_id}/kernel/variables",
            query={"language": ["javascript"]},
        )
        assert code == 400
        assert invalid == {"error": "language must be python or r"}

        child_id = runner.store.new_frame(
            parent_id=frame_id,
            project_id="project-domain",
            kind="turn",
        )
        with pytest.raises(gateway_mod.GatewayError) as child_error:
            _call(
                handler,
                "GET",
                f"/frames/{child_id}/kernel/variables",
                query={"language": ["python"]},
            )
        assert child_error.value.code == 409
    finally:
        runner.close()


def test_fork_from_cell_route_fails_closed_until_supported(tmp_path):
    runner, handler, frame_id = _setup(tmp_path)
    try:
        _call(
            handler,
            "POST",
            f"/frames/{frame_id}/branches/fork",
            body={"from_cell_id": "cell-1"},
        )
    except gateway_mod.GatewayError as error:
        assert error.code == 409
        assert "checkpoint" in error.message
    else:
        raise AssertionError("fork-from-cell must not claim success")
    runner.close()


def test_exact_cell_and_message_cursor_forks_are_isolated_and_view_only(tmp_path):
    runner, handler, frame_id = _setup(tmp_path)
    workspace = runner.workspace_for(frame_id)
    try:
        state_file = workspace / "state.txt"
        state_file.write_text("cell-boundary", encoding="utf-8")
        cell_id = runner._record_cell_with_cursor_checkpoint(
            frame_id=frame_id,
            root_frame_id=frame_id,
            project_id="project-domain",
            code="state = 'cell-boundary'",
            result={
                "id": "cell-boundary",
                "stdout": "",
                "stderr": "",
                "error": None,
            },
            origin="agent",
            cell_index=1,
            state_revision=1,
        )
        state_file.write_text("current", encoding="utf-8")

        code, execution_log = _call(
            handler,
            "GET",
            f"/frames/{frame_id}/execution-log",
        )
        assert code == 200
        persisted_cell = execution_log["entries"][0]
        assert persisted_cell["producing_cell_id"] == cell_id
        assert persisted_cell["fork_checkpoint_id"]

        code, cell_fork = _call(
            handler,
            "POST",
            f"/frames/{frame_id}/branches/fork",
            body={"from_cell_id": cell_id},
        )
        assert code == 200
        assert cell_fork["source_kind"] == "cell"
        assert cell_fork["active"] is False and cell_fork["view_only"] is True
        branch_groups = runner.store.list_action_groups(
            frame_id,
            branch_id=cell_fork["branch_id"],
            include_events=True,
        )
        fork_arguments = branch_groups[-1]["events"][-1]["canonical_arguments"]
        assert fork_arguments["source_id"] == cell_id
        assert fork_arguments["from_checkpoint_id"] == cell_fork["from_checkpoint_id"]
        assert "name" not in fork_arguments and "workspace" not in fork_arguments
        assert (
            runner.workspace_for_branch(frame_id, cell_fork["branch_id"])
            .joinpath("state.txt")
            .read_text(encoding="utf-8")
            == "cell-boundary"
        )
        assert state_file.read_text(encoding="utf-8") == "current"

        runner.store.update_frame(frame_id, name="cursor test")

        def loop_after_message(st, _emit, _visible):
            (st.workspace / "after-message.txt").write_text("later", encoding="utf-8")
            st.dispatcher.last_output = {"output": "done"}
            return "submitted"

        runner._loop = loop_after_message
        response = runner.run_message(
            frame_id,
            "project-domain",
            "branch this message",
        )
        assert response["status"] == "completed"
        code, messages = _call(
            handler,
            "GET",
            f"/frames/{frame_id}/messages",
        )
        assert code == 200
        user_message = next(
            item for item in messages["messages"] if item["role"] == "user"
        )
        assert user_message["message_id"]
        assert user_message["fork_checkpoint_id"]

        code, message_fork = _call(
            handler,
            "POST",
            f"/frames/{frame_id}/branches/fork",
            body={"from_message_id": user_message["message_id"]},
        )
        assert code == 200
        message_workspace = runner.workspace_for_branch(
            frame_id, message_fork["branch_id"]
        )
        assert not (message_workspace / "after-message.txt").exists()
        assert (workspace / "after-message.txt").read_text(encoding="utf-8") == "later"
    finally:
        runner.close()


def test_message_snapshot_failure_does_not_fail_message_or_advertise_fork(tmp_path):
    runner, handler, frame_id = _setup(tmp_path)
    runner.store.update_frame(frame_id, name="snapshot failure")
    original_capture = runner.session_domain.cas.capture

    def fail_capture(*_args, **_kwargs):
        raise OSError("private snapshot failure detail")

    def successful_loop(st, _emit, _visible):
        st.dispatcher.last_output = {"output": "done"}
        return "submitted"

    runner.session_domain.cas.capture = fail_capture
    runner._loop = successful_loop
    try:
        response = runner.run_message(
            frame_id,
            "project-domain",
            "persist even if the snapshot fails",
        )
        assert response["status"] == "completed"
        code, messages = _call(
            handler,
            "GET",
            f"/frames/{frame_id}/messages",
        )
        assert code == 200
        user_message = next(
            item for item in messages["messages"] if item["role"] == "user"
        )
        assert user_message["fork_checkpoint_id"] is None
        with pytest.raises(gateway_mod.GatewayError) as unavailable:
            _call(
                handler,
                "POST",
                f"/frames/{frame_id}/branches/fork",
                body={"from_message_id": user_message["message_id"]},
            )
        assert unavailable.value.code == 409
        groups = runner.store.list_action_groups(frame_id, include_events=True)
        warning = next(
            event
            for group in groups
            for event in group.get("events") or []
            if event.get("type") == "failed"
            and (event.get("canonical_arguments") or {}).get("source_kind") == "message"
        )
        assert "private snapshot failure detail" not in repr(warning)
    finally:
        runner.session_domain.cas.capture = original_capture
        runner.close()


def test_real_runner_checkpoint_can_restore_through_mutation_route(tmp_path):
    runner, handler, frame_id = _setup(tmp_path)
    workspace = runner.workspace_for(frame_id)
    (workspace / "analysis.txt").write_text("checkpoint bytes\n", "utf-8")
    nested = workspace / "results" / "out.csv"
    nested.parent.mkdir()
    nested.write_text("score\n0.93\n", "utf-8")
    nested_artifact = runner.store.save_artifact(
        path=str(nested),
        filename="display-name.csv",
        content_type="text/csv",
        size_bytes=nested.stat().st_size,
        checksum=hashlib.sha256(nested.read_bytes()).hexdigest(),
        frame_id=frame_id,
        root_frame_id=frame_id,
        project_id="project-domain",
    )
    try:
        started = runner.start_kernel(frame_id, "project-domain")
        assert started["state"] == "running"
        generation = runner.store.get_kernel_generation(started["generation_id"])
        assert generation["bootstrap"]["version"] == 2
        assert generation["bootstrap"]["runtime_version"] == platform.python_version()

        code, checkpoint = _call(
            handler,
            "POST",
            f"/frames/{frame_id}/checkpoints",
            body={"reason": "recovery-test"},
        )
        assert code == 200
        assert checkpoint["generation_refs"]["python"]["bootstrap"]["version"] == 2
        assert checkpoint["recovery_recipe"]["artifact_hashes"] == {
            "results/out.csv": nested_artifact["checksum"]
        }

        runner.stop_kernel(frame_id, "project-domain")
        code, actions = _call(handler, "GET", f"/frames/{frame_id}/recovery/actions")
        assert code == 200
        restore = next(item for item in actions["actions"] if item["id"] == "restore")
        assert restore["enabled"] is True

        code, restored = _call(
            handler,
            "POST",
            f"/frames/{frame_id}/recovery/actions/restore",
        )
        assert code == 200
        assert restored["ok"] is True
        assert restored["status"] == "active"
        assert restored["owner"]["kind"] == "recovery"
        state = runner._sessions[frame_id]
        assert state.kernels.alive("python")
        latest = runner.store.latest_kernel_generation(frame_id, "python")
        assert latest["recovered_from_generation_id"] == started["generation_id"]
        assert latest["bootstrap"]["version"] == 2
        assert nested.read_text("utf-8") == "score\n0.93\n"
        assert runner.session_domain.recovery_status(frame_id)["state"] == "active"
        assert any(event.get("type") == "recovery_state" for event in runner.hub.events)
    finally:
        runner.close()


def test_failed_fresh_recovery_keeps_exact_current_generation(monkeypatch, tmp_path):
    runner, handler, frame_id = _setup(tmp_path)
    try:
        runner.start_kernel(frame_id, "project-domain")
        state = runner._sessions[frame_id]
        before = state.kernels.lease("python")

        with pytest.raises(gateway_mod.GatewayError) as confirmation:
            _call(
                handler,
                "POST",
                f"/frames/{frame_id}/recovery/actions/restart_fresh",
            )
        assert confirmation.value.code == 409
        assert "confirmation" in confirmation.value.message

        def fail_bootstrap(_runtime, _candidate, _manifest):
            raise RuntimeError("candidate dependency missing")

        monkeypatch.setattr(
            gateway_mod.SessionRecoveryRuntime,
            "_bootstrap_candidate",
            fail_bootstrap,
        )
        code, failed = _call(
            handler,
            "POST",
            f"/frames/{frame_id}/recovery/actions/restart_fresh",
            body={"confirm": True},
        )

        assert code == 409
        assert failed["status"] == "failed"
        current = state.kernels.lease("python")
        assert current == before
        assert current.kernel.is_alive()
        assert (
            runner.store.latest_kernel_generation(frame_id, "python")["generation_id"]
            == before.generation_id
        )
        assert runner.session_domain.recovery_status(frame_id)["state"] == "failed"
    finally:
        runner.close()
