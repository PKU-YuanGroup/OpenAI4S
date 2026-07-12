from __future__ import annotations

import sqlite3

import pytest

from openai4s.storage.snapshots import SessionSnapshotRepository, WorkspaceCAS


def _repository(tmp_path):
    connection = sqlite3.connect(tmp_path / "snapshots.sqlite")
    connection.row_factory = sqlite3.Row
    return connection, SessionSnapshotRepository(
        connection,
        __import__("threading").RLock(),
        clock_ms=lambda: 1234,
    )


def test_workspace_cas_is_deterministic_and_excludes_secrets_symlinks_and_large_files(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "analysis.txt").write_text("science", encoding="utf-8")
    (workspace / ".env").write_text("OPENAI4S_KEY=secret", encoding="utf-8")
    (workspace / "private.pem").write_text("SECRET", encoding="utf-8")
    (workspace / "large.bin").write_bytes(b"x" * 33)
    (workspace / "outside.txt").write_text("outside", encoding="utf-8")
    (workspace / "link.txt").symlink_to(workspace / "outside.txt")

    cas = WorkspaceCAS(tmp_path / "cas", max_file_bytes=32)
    first = cas.capture(workspace, exclude=("outside.txt",))
    second = cas.capture(workspace, exclude=("outside.txt",))

    assert first["tree_id"] == second["tree_id"]
    assert [entry["path"] for entry in first["entries"]] == ["analysis.txt"]
    reasons = {item["path"]: item["reason"] for item in first["skipped"]}
    assert reasons[".env"] == "secret_or_excluded"
    assert reasons["private.pem"] == "secret_or_excluded"
    assert reasons["large.bin"] == "too_large"
    assert reasons["link.txt"] == "not_regular_file"
    assert cas.get_blob(first["entries"][0]["blob"]) == b"science"


def test_workspace_restore_refuses_external_changes_and_preserves_untracked_files(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "result.txt").write_text("old", encoding="utf-8")
    (workspace / "remove.txt").write_text("managed", encoding="utf-8")
    cas = WorkspaceCAS(tmp_path / "cas")
    target = cas.capture(workspace)

    (workspace / "result.txt").write_text("new", encoding="utf-8")
    (workspace / "remove.txt").unlink()
    baseline = cas.capture(workspace)
    (workspace / "result.txt").write_text("external edit", encoding="utf-8")
    (workspace / "researcher-note.txt").write_text("keep me", encoding="utf-8")

    refused = cas.restore(
        target["tree_id"], workspace, baseline_tree_id=baseline["tree_id"]
    )
    assert refused["applied"] is False
    assert [item["path"] for item in refused["conflicts"]] == ["result.txt"]
    assert (workspace / "result.txt").read_text(encoding="utf-8") == "external edit"
    assert not (workspace / "remove.txt").exists()

    (workspace / "result.txt").write_text("new", encoding="utf-8")
    restored = cas.restore(
        target["tree_id"], workspace, baseline_tree_id=baseline["tree_id"]
    )
    assert restored["applied"] is True
    assert (workspace / "result.txt").read_text(encoding="utf-8") == "old"
    assert (workspace / "remove.txt").read_text(encoding="utf-8") == "managed"
    assert (workspace / "researcher-note.txt").read_text(encoding="utf-8") == "keep me"


def test_workspace_restore_applies_first_class_deletes(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "keep.txt").write_text("v1", encoding="utf-8")
    (workspace / "delete.txt").write_text("v1", encoding="utf-8")
    cas = WorkspaceCAS(tmp_path / "cas")
    baseline = cas.capture(workspace)

    (workspace / "keep.txt").write_text("v2", encoding="utf-8")
    (workspace / "delete.txt").unlink()
    target = cas.capture(workspace)

    # Return to baseline, then apply the forward transition to prove deletes
    # are explicit and conflict checked rather than inferred from os.walk.
    (workspace / "keep.txt").write_text("v1", encoding="utf-8")
    (workspace / "delete.txt").write_text("v1", encoding="utf-8")
    result = cas.restore(
        target["tree_id"], workspace, baseline_tree_id=baseline["tree_id"]
    )

    assert result["applied"] is True
    assert result["deletes"] == ["delete.txt"]
    assert (workspace / "keep.txt").read_text(encoding="utf-8") == "v2"
    assert not (workspace / "delete.txt").exists()


def test_checkpoint_branches_are_append_only_and_head_updates_are_cas_guarded(tmp_path):
    connection, repository = _repository(tmp_path)
    try:
        first = repository.create_checkpoint(
            root_frame_id="root-1",
            reason="turn_complete",
            workspace_tree_id="a" * 64,
            action_cursor=4,
            artifact_versions=["v-1"],
            generation_refs={"python": "gen-1"},
            recovery_recipe={"symbols": ["data"]},
        )
        assert first["branch_id"] == "root-1"
        assert first["artifact_versions"] == ["v-1"]
        assert (
            repository.get_branch("root-1")["head_checkpoint_id"]
            == first["checkpoint_id"]
        )

        fork = repository.fork_branch(
            root_frame_id="root-1",
            from_checkpoint_id=first["checkpoint_id"],
            branch_id="branch-b",
            name="alternative",
        )
        assert fork["head_checkpoint_id"] == first["checkpoint_id"]
        second = repository.create_checkpoint(
            root_frame_id="root-1",
            branch_id="branch-b",
            reason="fork_continue",
            workspace_tree_id="b" * 64,
            expected_head=first["checkpoint_id"],
        )
        assert second["parent_checkpoint_id"] == first["checkpoint_id"]
        assert (
            repository.get_branch("root-1")["head_checkpoint_id"]
            == first["checkpoint_id"]
        )
        assert (
            repository.get_branch("branch-b")["head_checkpoint_id"]
            == second["checkpoint_id"]
        )

        with pytest.raises(RuntimeError, match="branch head changed"):
            repository.create_checkpoint(
                root_frame_id="root-1",
                branch_id="branch-b",
                reason="stale writer",
                workspace_tree_id="c" * 64,
                expected_head=first["checkpoint_id"],
            )
        assert [
            item["checkpoint_id"]
            for item in repository.list_checkpoints("root-1", branch_id="branch-b")
        ] == [second["checkpoint_id"]]
    finally:
        connection.close()


def test_cursor_checkpoint_binding_is_exact_internal_and_idempotent(tmp_path):
    connection, repository = _repository(tmp_path)
    try:
        first = repository.create_checkpoint(
            root_frame_id="root-cursor",
            reason="cursor_cell",
            workspace_tree_id="a" * 64,
            source_kind="cell",
            source_id="cell-1",
            internal=True,
            cell_cursor=1,
        )
        repeated = repository.create_checkpoint(
            root_frame_id="root-cursor",
            reason="cursor_cell",
            workspace_tree_id="b" * 64,
            source_kind="cell",
            source_id="cell-1",
            internal=True,
            cell_cursor=99,
        )

        assert repeated["checkpoint_id"] == first["checkpoint_id"]
        assert repeated["workspace_tree_id"] == "a" * 64
        assert repeated["cell_cursor"] == 1
        assert repeated["source_kind"] == "cell"
        assert repeated["source_id"] == "cell-1"
        assert repeated["internal"] is True
        assert (
            repository.get_checkpoint_for_source(
                "root-cursor", source_kind="cell", source_id="cell-1"
            )["checkpoint_id"]
            == first["checkpoint_id"]
        )
        assert repository.checkpoint_source_map("root-cursor", source_kind="cell") == {
            "cell-1": first["checkpoint_id"]
        }
        assert len(repository.list_checkpoints("root-cursor")) == 1
    finally:
        connection.close()


def test_existing_checkpoint_table_gets_additive_cursor_migration(tmp_path):
    database = tmp_path / "legacy.sqlite"
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE session_checkpoints ("
        "checkpoint_id TEXT PRIMARY KEY,root_frame_id TEXT NOT NULL,"
        "branch_id TEXT NOT NULL,parent_checkpoint_id TEXT,reason TEXT NOT NULL,"
        "action_cursor INTEGER,message_cursor INTEGER,cell_cursor INTEGER,"
        "workspace_tree_id TEXT,artifact_versions TEXT NOT NULL,"
        "environment_pins TEXT NOT NULL,generation_refs TEXT NOT NULL,"
        "capability_state TEXT NOT NULL,permission_state TEXT NOT NULL,"
        "recovery_recipe TEXT NOT NULL,metadata TEXT NOT NULL,created_at INTEGER NOT NULL)"
    )
    connection.commit()
    try:
        SessionSnapshotRepository(
            connection,
            __import__("threading").RLock(),
            clock_ms=lambda: 1234,
        )
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(session_checkpoints)"
            ).fetchall()
        }
        assert {"source_kind", "source_id", "internal"} <= columns
    finally:
        connection.close()


def test_imported_tree_rejects_path_traversal(tmp_path):
    cas = WorkspaceCAS(tmp_path / "cas")
    with pytest.raises(ValueError, match="unsafe snapshot path"):
        cas.put_tree(
            [{"path": "../escape", "blob": "a" * 64, "size": 1, "mode": 0o600}]
        )
