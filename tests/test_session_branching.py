from __future__ import annotations

import sqlite3
import threading

from openai4s.server.session_branching import SessionBranchingService
from openai4s.storage.snapshots import SessionSnapshotRepository, WorkspaceCAS


def _service(tmp_path):
    connection = sqlite3.connect(tmp_path / "branching.sqlite")
    connection.row_factory = sqlite3.Row
    repository = SessionSnapshotRepository(
        connection,
        threading.RLock(),
        clock_ms=lambda: 1000,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = {
        "action_cursor": 0,
        "message_cursor": 0,
        "cell_cursor": 0,
        "artifact_versions": [],
        "environment_pins": {"python": "science"},
        "generation_refs": {"python": "gen-0"},
        "capability_state": {"skills": ["literature"]},
        "permission_state": {"network": "ask"},
        "recovery_recipe": {"required_symbols": ["data"]},
    }
    events = []
    service = SessionBranchingService(
        repository,
        WorkspaceCAS(tmp_path / "cas"),
        workspace=lambda _root, _branch: workspace,
        read_state=lambda _root, _branch: state,
        event_sink=events.append,
    )
    return connection, repository, service, workspace, state, events


def test_checkpoint_projection_and_fork_keep_original_branch_immutable(tmp_path):
    connection, repository, service, workspace, state, events = _service(tmp_path)
    try:
        (workspace / "analysis.txt").write_text("v1", encoding="utf-8")
        first = service.create_checkpoint("root", reason="turn_complete")
        state.update(action_cursor=3, message_cursor=2, cell_cursor=1)
        state["artifact_versions"] = ["v-artifact"]
        (workspace / "analysis.txt").write_text("v2", encoding="utf-8")
        second = service.create_checkpoint(
            "root", reason="turn_complete", expected_head=first["checkpoint_id"]
        )

        fork = service.fork(
            "root",
            from_checkpoint_id=first["checkpoint_id"],
            branch_id="branch-experiment",
            name="alternative",
        )
        projection = service.projection("root")

        assert repository.get_branch("root")["head_checkpoint_id"] == second[
            "checkpoint_id"
        ]
        assert fork["head_checkpoint_id"] == first["checkpoint_id"]
        assert {item["branch_id"] for item in projection["branches"]} == {
            "root",
            "branch-experiment",
        }
        assert [event["type"] for event in events] == [
            "checkpoint_created",
            "checkpoint_created",
            "branch_created",
        ]
    finally:
        connection.close()


def test_revert_preview_reports_all_state_dimensions_without_writing(tmp_path):
    connection, _repository, service, workspace, state, _events = _service(tmp_path)
    try:
        (workspace / "analysis.txt").write_text("v1", encoding="utf-8")
        (workspace / "old.txt").write_text("old", encoding="utf-8")
        first = service.create_checkpoint("root", reason="turn_complete")
        state.update(action_cursor=7, message_cursor=5, cell_cursor=4)
        state["artifact_versions"] = ["v-new"]
        state["environment_pins"] = {"python": "gpu"}
        state["capability_state"] = {"skills": []}
        state["permission_state"] = {"network": "allow"}
        (workspace / "analysis.txt").write_text("v2", encoding="utf-8")
        (workspace / "old.txt").unlink()
        second = service.create_checkpoint("root", reason="turn_complete")

        preview = service.preview_revert(
            "root", branch_id="root", target_checkpoint_id=first["checkpoint_id"]
        )

        assert preview["current_checkpoint_id"] == second["checkpoint_id"]
        assert preview["can_apply"] is True
        assert {item["path"] for item in preview["workspace"]["writes"]} == {
            "analysis.txt",
            "old.txt",
        }
        assert preview["messages"] == {"from": 5, "to": 0, "delta": -5}
        assert preview["actions"]["delta"] == -7
        assert preview["notebook"]["delta"] == -4
        assert preview["artifacts"] == {"added": [], "removed": ["v-new"]}
        assert preview["environment"]["has_changes"] is True
        assert preview["capabilities"]["has_changes"] is True
        assert preview["permissions"]["has_changes"] is True
        assert (workspace / "analysis.txt").read_text(encoding="utf-8") == "v2"
    finally:
        connection.close()


def test_external_edit_blocks_revert_and_is_recorded(tmp_path):
    connection, repository, service, workspace, state, _events = _service(tmp_path)
    try:
        (workspace / "analysis.txt").write_text("v1", encoding="utf-8")
        first = service.create_checkpoint("root", reason="turn_complete")
        state["message_cursor"] = 1
        (workspace / "analysis.txt").write_text("v2", encoding="utf-8")
        service.create_checkpoint("root", reason="turn_complete")
        (workspace / "analysis.txt").write_text("researcher edit", encoding="utf-8")

        result = service.revert_and_continue(
            "root", branch_id="root", target_checkpoint_id=first["checkpoint_id"]
        )

        assert result["ok"] is False
        assert result["operation"]["status"] == "conflict"
        assert result["preview"]["workspace"]["conflicts"][0]["path"] == "analysis.txt"
        assert (
            workspace / "analysis.txt"
        ).read_text(encoding="utf-8") == "researcher edit"
        # A rejected preview does not append an undo/revert checkpoint.
        assert len(repository.list_checkpoints("root")) == 2
    finally:
        connection.close()


def test_revert_and_undo_append_history_and_preserve_untracked_files(tmp_path):
    connection, repository, service, workspace, state, events = _service(tmp_path)
    try:
        (workspace / "analysis.txt").write_text("v1", encoding="utf-8")
        first = service.create_checkpoint("root", reason="turn_complete")
        state.update(action_cursor=4, message_cursor=3, cell_cursor=2)
        (workspace / "analysis.txt").write_text("v2", encoding="utf-8")
        current = service.create_checkpoint("root", reason="turn_complete")
        (workspace / "note-untracked.txt").write_text("keep", encoding="utf-8")

        reverted = service.revert_and_continue(
            "root", branch_id="root", target_checkpoint_id=first["checkpoint_id"]
        )
        assert reverted["ok"] is True
        assert (workspace / "analysis.txt").read_text(encoding="utf-8") == "v1"
        assert (workspace / "note-untracked.txt").read_text(encoding="utf-8") == "keep"
        assert reverted["checkpoint"]["message_cursor"] == 0
        assert reverted["requires_kernel_recovery"] is True

        undo_target = reverted["undo_checkpoint_id"]
        assert repository.get_checkpoint(undo_target)["parent_checkpoint_id"] == current[
            "checkpoint_id"
        ]
        undone = service.undo_revert(
            "root",
            branch_id="root",
            revert_checkpoint_id=reverted["checkpoint"]["checkpoint_id"],
        )
        assert undone["ok"] is True
        assert (workspace / "analysis.txt").read_text(encoding="utf-8") == "v2"
        assert len(repository.list_checkpoints("root")) == 6
        assert [event["type"] for event in events].count("branch_reverted") == 2
        revert_events = [
            event for event in events if event["type"] == "branch_reverted"
        ]
        assert all(event["root_frame_id"] == "root" for event in revert_events)
        assert all(event["branch_id"] == "root" for event in revert_events)
        assert all("operation" not in event for event in revert_events)
        assert all("checkpoint" not in event for event in revert_events)
        assert all("preview" not in event for event in revert_events)
    finally:
        connection.close()
