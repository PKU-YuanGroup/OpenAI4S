from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from openai4s.server.session_branching import (
    RevertRecoveryRequiredError,
    SessionBranchingService,
)
from openai4s.storage.snapshots import (
    SessionSnapshotRepository,
    WorkspaceCAS,
    revert_recovery_setting_key,
)


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
    branch_root = tmp_path / "branches"
    state = {
        "action_cursor": 0,
        "message_cursor": 0,
        "cell_cursor": 0,
        "auto_event_cursor": 0,
        "artifact_versions": [],
        "environment_pins": {"python": "science"},
        "generation_refs": {"python": "gen-0"},
        "capability_state": {"skills": ["literature"]},
        "permission_state": {"network": "ask"},
        "recovery_recipe": {"required_symbols": ["data"]},
    }
    events = []
    settings: dict[str, str] = {}
    recovery_events: list[dict] = []
    service = SessionBranchingService(
        repository,
        WorkspaceCAS(tmp_path / "cas"),
        workspace=lambda _root, branch: (
            workspace if branch == "root" else branch_root / branch
        ),
        read_state=lambda _root, _branch: state,
        event_sink=events.append,
        get_setting=lambda key: settings.get(key),
        set_setting=lambda key, value: settings.__setitem__(key, value),
        delete_setting=lambda key: settings.pop(key, None),
        recovery_event_sink=recovery_events.append,
    )
    service._test_settings = settings
    service._test_recovery_events = recovery_events
    return connection, repository, service, workspace, state, events


def test_checkpoint_projection_and_fork_keep_original_branch_immutable(tmp_path):
    connection, repository, service, workspace, state, events = _service(tmp_path)
    try:
        (workspace / "analysis.txt").write_text("v1", encoding="utf-8")
        first = service.create_checkpoint("root", reason="turn_complete")
        state.update(
            action_cursor=3,
            message_cursor=2,
            cell_cursor=1,
            auto_event_cursor=4,
        )
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

        assert (
            repository.get_branch("root")["head_checkpoint_id"]
            == second["checkpoint_id"]
        )
        assert fork["head_checkpoint_id"] == first["checkpoint_id"]
        assert (
            repository.get_checkpoint(first["checkpoint_id"])["auto_event_cursor"] == 0
        )
        assert (
            repository.get_checkpoint(second["checkpoint_id"])["auto_event_cursor"] == 4
        )
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
        state.update(
            action_cursor=7,
            message_cursor=5,
            cell_cursor=4,
            auto_event_cursor=6,
        )
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
        assert preview["auto_mode_events"] == {"from": 6, "to": 0, "delta": -6}
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
        assert (workspace / "analysis.txt").read_text(
            encoding="utf-8"
        ) == "researcher edit"
        # A rejected preview does not append an undo/revert checkpoint.
        assert len(repository.list_checkpoints("root")) == 2
    finally:
        connection.close()


def test_revert_and_undo_append_history_and_preserve_untracked_files(tmp_path):
    connection, repository, service, workspace, state, events = _service(tmp_path)
    try:
        (workspace / "analysis.txt").write_text("v1", encoding="utf-8")
        first = service.create_checkpoint("root", reason="turn_complete")
        state.update(
            action_cursor=4,
            message_cursor=3,
            cell_cursor=2,
            auto_event_cursor=5,
        )
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
        assert reverted["checkpoint"]["auto_event_cursor"] == 0
        assert (
            reverted["checkpoint"]["metadata"]["history_projection"]["resume_cursors"][
                "auto_event_cursor"
            ]
            == 5
        )
        assert reverted["requires_kernel_recovery"] is True

        undo_target = reverted["undo_checkpoint_id"]
        assert (
            repository.get_checkpoint(undo_target)["parent_checkpoint_id"]
            == current["checkpoint_id"]
        )
        undone = service.undo_revert(
            "root",
            branch_id="root",
            revert_checkpoint_id=reverted["checkpoint"]["checkpoint_id"],
        )
        assert undone["ok"] is True
        assert undone["checkpoint"]["auto_event_cursor"] == 5
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


def _two_file_revert_history(service, workspace):
    (workspace / "a.txt").write_text("a-v1", encoding="utf-8")
    (workspace / "b.txt").write_text("b-v1", encoding="utf-8")
    target = service.create_checkpoint("root", reason="turn_complete")
    (workspace / "a.txt").write_text("a-v2", encoding="utf-8")
    (workspace / "b.txt").write_text("b-v2", encoding="utf-8")
    service.create_checkpoint("root", reason="turn_complete")
    return target


def test_revert_partial_tree_failure_compensates_before_unlock(tmp_path, monkeypatch):
    connection, repository, service, workspace, _state, _events = _service(tmp_path)
    try:
        target = _two_file_revert_history(service, workspace)
        original_atomic = service.cas._atomic_write
        workspace_writes = 0
        injected = False

        def fail_second_workspace_write(path, data, *, mode):
            nonlocal workspace_writes, injected
            if workspace in path.parents and not injected:
                workspace_writes += 1
                if workspace_writes == 2:
                    injected = True
                    raise OSError("injected second-file restore failure")
            return original_atomic(path, data, mode=mode)

        monkeypatch.setattr(service.cas, "_atomic_write", fail_second_workspace_write)
        with pytest.raises(OSError, match="second-file"):
            service.revert_and_continue(
                "root",
                branch_id="root",
                target_checkpoint_id=target["checkpoint_id"],
            )

        assert (workspace / "a.txt").read_text(encoding="utf-8") == "a-v2"
        assert (workspace / "b.txt").read_text(encoding="utf-8") == "b-v2"
        head = repository.get_branch("root")["head_checkpoint_id"]
        assert repository.get_checkpoint(head)["reason"] == "before_revert"
        assert revert_recovery_setting_key("root") not in service._test_settings
        assert [event["status"] for event in service._test_recovery_events] == [
            "started",
            "cancelled",
        ]
    finally:
        connection.close()


def test_revert_checkpoint_failure_compensates_to_undo_tree(tmp_path, monkeypatch):
    connection, repository, service, workspace, _state, _events = _service(tmp_path)
    try:
        target = _two_file_revert_history(service, workspace)
        original_create = repository.create_checkpoint

        def fail_revert_checkpoint(**fields):
            if fields.get("reason") == "revert_continue":
                raise RuntimeError("injected checkpoint failure")
            return original_create(**fields)

        monkeypatch.setattr(repository, "create_checkpoint", fail_revert_checkpoint)
        with pytest.raises(RuntimeError, match="checkpoint failure"):
            service.revert_and_continue(
                "root",
                branch_id="root",
                target_checkpoint_id=target["checkpoint_id"],
            )

        assert (workspace / "a.txt").read_text(encoding="utf-8") == "a-v2"
        assert (workspace / "b.txt").read_text(encoding="utf-8") == "b-v2"
        assert revert_recovery_setting_key("root") not in service._test_settings
    finally:
        connection.close()


def test_revert_commit_response_loss_never_compensates_committed_head(
    tmp_path, monkeypatch
):
    connection, repository, service, workspace, _state, _events = _service(tmp_path)
    try:
        target = _two_file_revert_history(service, workspace)
        original_create = repository.create_checkpoint

        def lose_committed_response(**fields):
            created = original_create(**fields)
            if fields.get("reason") == "revert_continue":
                raise RuntimeError("injected committed response loss")
            return created

        monkeypatch.setattr(repository, "create_checkpoint", lose_committed_response)
        result = service.revert_and_continue(
            "root",
            branch_id="root",
            target_checkpoint_id=target["checkpoint_id"],
        )

        assert result["ok"] is True
        assert (workspace / "a.txt").read_text(encoding="utf-8") == "a-v1"
        assert (workspace / "b.txt").read_text(encoding="utf-8") == "b-v1"
        head = repository.get_branch("root")["head_checkpoint_id"]
        assert head == result["checkpoint"]["checkpoint_id"]
        # This focused service has no live runtime and therefore owns its own
        # terminal unlock. SessionDomain uses deferred unlock so Gateway can
        # invalidate kernels before clearing the same marker.
        assert revert_recovery_setting_key("root") not in service._test_settings
    finally:
        connection.close()


def test_revert_compensation_failure_retains_recovery_barrier(tmp_path, monkeypatch):
    connection, repository, service, workspace, _state, _events = _service(tmp_path)
    try:
        target = _two_file_revert_history(service, workspace)
        original_create = repository.create_checkpoint
        original_restore = service.cas.restore
        restore_calls = 0

        def fail_revert_checkpoint(**fields):
            if fields.get("reason") == "revert_continue":
                raise RuntimeError("injected checkpoint failure")
            return original_create(**fields)

        def fail_compensation(*args, **kwargs):
            nonlocal restore_calls
            restore_calls += 1
            if restore_calls == 2:
                raise OSError("injected compensation failure")
            return original_restore(*args, **kwargs)

        monkeypatch.setattr(repository, "create_checkpoint", fail_revert_checkpoint)
        monkeypatch.setattr(service.cas, "restore", fail_compensation)
        with pytest.raises(RevertRecoveryRequiredError, match="locked for recovery"):
            service.revert_and_continue(
                "root",
                branch_id="root",
                target_checkpoint_id=target["checkpoint_id"],
            )

        marker = service._test_settings[revert_recovery_setting_key("root")]
        assert '"state":"recovery_required"' in marker
        head = repository.get_branch("root")["head_checkpoint_id"]
        assert repository.get_checkpoint(head)["reason"] == "before_revert"
    finally:
        connection.close()


def test_restart_reconciler_compensates_reverting_marker_to_undo_tree(
    tmp_path, monkeypatch
):
    connection, repository, service, workspace, _state, _events = _service(tmp_path)
    try:
        target = _two_file_revert_history(service, workspace)
        original_create = repository.create_checkpoint

        def process_dies_before_revert_checkpoint(**fields):
            if fields.get("reason") == "revert_continue":
                raise KeyboardInterrupt("simulated process loss")
            return original_create(**fields)

        monkeypatch.setattr(
            repository, "create_checkpoint", process_dies_before_revert_checkpoint
        )
        with pytest.raises(KeyboardInterrupt, match="process loss"):
            service.revert_and_continue(
                "root",
                branch_id="root",
                target_checkpoint_id=target["checkpoint_id"],
            )
        monkeypatch.setattr(repository, "create_checkpoint", original_create)

        marker_key = revert_recovery_setting_key("root")
        assert json.loads(service._test_settings[marker_key])["state"] == "reverting"
        assert (workspace / "a.txt").read_text(encoding="utf-8") == "a-v1"

        reconciled = service.reconcile_revert("root")

        assert reconciled["resolved"] is True
        assert reconciled["state"] == "compensated"
        assert (workspace / "a.txt").read_text(encoding="utf-8") == "a-v2"
        assert (workspace / "b.txt").read_text(encoding="utf-8") == "b-v2"
        assert marker_key not in service._test_settings
        operation = repository.get_operation(reconciled["operation_id"])
        assert operation["status"] == "failed_compensated"
    finally:
        connection.close()


def test_restart_reconciler_finishes_committed_head_before_exact_unlock(tmp_path):
    connection, repository, service, workspace, _state, _events = _service(tmp_path)
    try:
        target = _two_file_revert_history(service, workspace)
        service._defer_revert_unlock = True
        reverted = service.revert_and_continue(
            "root",
            branch_id="root",
            target_checkpoint_id=target["checkpoint_id"],
        )
        marker_key = revert_recovery_setting_key("root")
        assert marker_key in service._test_settings
        # Model a crash/restart after commit followed by a workspace that still
        # reflects the undo tree. The authoritative committed head selects the
        # target direction deterministically.
        (workspace / "a.txt").write_text("a-v2", encoding="utf-8")
        (workspace / "b.txt").write_text("b-v2", encoding="utf-8")

        reconciled = service.reconcile_revert("root")

        assert reconciled["resolved"] is True
        assert reconciled["state"] == "committed"
        assert (
            reconciled["checkpoint"]["checkpoint_id"]
            == reverted["checkpoint"]["checkpoint_id"]
        )
        assert (workspace / "a.txt").read_text(encoding="utf-8") == "a-v1"
        assert json.loads(service._test_settings[marker_key])["state"] == (
            "committed_reconciled"
        )
        service.finalize_revert_unlock("root", operation_id=reconciled["operation_id"])
        assert marker_key not in service._test_settings
    finally:
        connection.close()


def test_restart_reconciler_cancels_preparing_marker_without_touching_workspace(
    tmp_path,
):
    connection, repository, service, workspace, _state, _events = _service(tmp_path)
    try:
        target = _two_file_revert_history(service, workspace)
        head = repository.get_branch("root")["head_checkpoint_id"]
        marker_key = revert_recovery_setting_key("root")
        service._test_settings[marker_key] = json.dumps(
            {
                "schema_version": 1,
                "state": "preparing",
                "operation_id": "so-restart-prepare",
                "branch_id": "root",
                "current_checkpoint_id": head,
                "target_checkpoint_id": target["checkpoint_id"],
            }
        )

        reconciled = service.reconcile_revert("root")

        assert reconciled["state"] == "cancelled"
        assert (workspace / "a.txt").read_text(encoding="utf-8") == "a-v2"
        assert marker_key not in service._test_settings
    finally:
        connection.close()


@pytest.mark.parametrize("failure_point", ["audit", "marker_clear"])
def test_restart_reconciler_replays_preparing_conflict_after_terminal_loss(
    tmp_path, monkeypatch, failure_point
):
    connection, repository, service, workspace, _state, _events = _service(tmp_path)
    try:
        target = _two_file_revert_history(service, workspace)
        original_preview = service.cas.preview_restore
        preview_calls = 0

        def conflict_after_undo_capture(*args, **kwargs):
            nonlocal preview_calls
            preview_calls += 1
            preview = original_preview(*args, **kwargs)
            if preview_calls == 2:
                return {**preview, "conflicts": [{"path": "a.txt"}]}
            return preview

        original_recovery_record = service._record_revert_recovery
        cancelled_lost = False

        def lose_first_cancelled_audit(**fields):
            nonlocal cancelled_lost
            if fields.get("status") == "cancelled" and not cancelled_lost:
                cancelled_lost = True
                raise OSError("injected conflict audit loss")
            return original_recovery_record(**fields)

        monkeypatch.setattr(service.cas, "preview_restore", conflict_after_undo_capture)
        if failure_point == "audit":
            monkeypatch.setattr(
                service, "_record_revert_recovery", lose_first_cancelled_audit
            )
        else:
            original_delete = service._delete_setting
            clear_lost = False

            def lose_first_marker_clear(key):
                nonlocal clear_lost
                if not clear_lost:
                    clear_lost = True
                    raise OSError("injected conflict marker clear loss")
                assert original_delete is not None
                return original_delete(key)

            monkeypatch.setattr(service, "_delete_setting", lose_first_marker_clear)
        with pytest.raises(OSError, match="conflict (?:audit|marker clear) loss"):
            service.revert_and_continue(
                "root",
                branch_id="root",
                target_checkpoint_id=target["checkpoint_id"],
            )

        head_before = repository.get_branch("root")["head_checkpoint_id"]
        operation = repository.list_operations("root")[0]
        assert operation["status"] == "conflict"
        assert (
            json.loads(service._test_settings[revert_recovery_setting_key("root")])[
                "state"
            ]
            == "preparing"
        )

        reconciled = service.reconcile_revert("root")

        assert reconciled["resolved"] is True
        assert reconciled["state"] == "conflict"
        assert repository.get_branch("root")["head_checkpoint_id"] == head_before
        assert (workspace / "a.txt").read_text(encoding="utf-8") == "a-v2"
        assert (workspace / "b.txt").read_text(encoding="utf-8") == "b-v2"
        assert revert_recovery_setting_key("root") not in service._test_settings
        statuses = [event["status"] for event in service._test_recovery_events]
        assert statuses == (
            ["started", "cancelled"]
            if failure_point == "audit"
            else ["started", "cancelled", "cancelled"]
        )
        assert service._test_recovery_events[-1]["detail"]["operation_status"] == (
            "conflict"
        )
    finally:
        connection.close()


@pytest.mark.parametrize("tamper_operation", [False, True])
def test_restart_reconciler_replays_only_exact_materialized_conflict(
    tmp_path, monkeypatch, tamper_operation
):
    connection, repository, service, workspace, _state, _events = _service(tmp_path)
    try:
        target = _two_file_revert_history(service, workspace)
        original_restore = service.cas.restore
        restore_calls = 0

        def reject_initial_restore(*args, **kwargs):
            nonlocal restore_calls
            restore_calls += 1
            if restore_calls == 1:
                return {
                    "applied": False,
                    "conflicts": [{"path": "a.txt"}],
                    "reason": "injected conflict",
                }
            return original_restore(*args, **kwargs)

        original_recovery_record = service._record_revert_recovery
        cancelled_lost = False

        def lose_first_cancelled_audit(**fields):
            nonlocal cancelled_lost
            if fields.get("status") == "cancelled" and not cancelled_lost:
                cancelled_lost = True
                raise OSError("injected conflict audit loss")
            return original_recovery_record(**fields)

        monkeypatch.setattr(service.cas, "restore", reject_initial_restore)
        monkeypatch.setattr(
            service, "_record_revert_recovery", lose_first_cancelled_audit
        )
        with pytest.raises(RevertRecoveryRequiredError, match="locked for recovery"):
            service.revert_and_continue(
                "root",
                branch_id="root",
                target_checkpoint_id=target["checkpoint_id"],
            )

        operation = repository.list_operations("root")[0]
        assert operation["status"] == "conflict"
        head_before = repository.get_branch("root")["head_checkpoint_id"]
        marker_key = revert_recovery_setting_key("root")
        assert json.loads(service._test_settings[marker_key])["state"] == (
            "recovery_required"
        )
        restore_calls_before_reconcile = restore_calls
        if tamper_operation:
            connection.execute(
                "UPDATE snapshot_operations SET source_checkpoint_id=? "
                "WHERE operation_id=?",
                (target["checkpoint_id"], operation["operation_id"]),
            )
            connection.commit()

        reconciled = service.reconcile_revert("root")

        assert repository.get_branch("root")["head_checkpoint_id"] == head_before
        assert (workspace / "a.txt").read_text(encoding="utf-8") == "a-v2"
        assert (workspace / "b.txt").read_text(encoding="utf-8") == "b-v2"
        # A recorded conflict is proof that the rejected restore wrote no bytes;
        # reconciliation must not reinterpret it as a compensation request.
        assert restore_calls == restore_calls_before_reconcile
        if tamper_operation:
            assert reconciled["resolved"] is False
            assert reconciled["state"] == "recovery_required"
            assert marker_key in service._test_settings
        else:
            assert reconciled["resolved"] is True
            assert reconciled["state"] == "conflict"
            assert marker_key not in service._test_settings
            assert service._test_recovery_events[-1]["status"] == "cancelled"
            assert (
                service._test_recovery_events[-1]["detail"]["operation_status"]
                == "conflict"
            )
    finally:
        connection.close()


def test_restart_reconciler_keeps_barrier_on_third_party_conflict(
    tmp_path, monkeypatch
):
    connection, repository, service, workspace, _state, _events = _service(tmp_path)
    try:
        target = _two_file_revert_history(service, workspace)
        original_create = repository.create_checkpoint

        def process_dies_before_revert_checkpoint(**fields):
            if fields.get("reason") == "revert_continue":
                raise KeyboardInterrupt("simulated process loss")
            return original_create(**fields)

        monkeypatch.setattr(
            repository, "create_checkpoint", process_dies_before_revert_checkpoint
        )
        with pytest.raises(KeyboardInterrupt):
            service.revert_and_continue(
                "root",
                branch_id="root",
                target_checkpoint_id=target["checkpoint_id"],
            )
        monkeypatch.setattr(repository, "create_checkpoint", original_create)
        (workspace / "a.txt").write_text("third-party", encoding="utf-8")

        reconciled = service.reconcile_revert("root")

        assert reconciled["resolved"] is False
        assert reconciled["state"] == "recovery_required"
        assert (workspace / "a.txt").read_text(encoding="utf-8") == "third-party"
        marker = json.loads(service._test_settings[revert_recovery_setting_key("root")])
        assert marker["state"] == "recovery_required"
        assert marker["error"].startswith("RuntimeError:")
    finally:
        connection.close()
