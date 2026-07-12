"""Focused contracts for append-only Revert/Undo history projection."""

from __future__ import annotations

import hashlib
import sqlite3

from openai4s.agent.ledger import restore_action_history
from openai4s.server.execution_views import ExecutionViewService
from openai4s.server.session_domain import SessionDomainService
from openai4s.store import Store


def _append_provider_user(store: Store, root: str, branch: str, text: str) -> None:
    store.append_action_group(
        root_frame_id=root,
        branch_id=branch,
        turn_id=f"turn-{text}",
        kind="user",
        assistant_message={"role": "user", "content": text},
    )


def _log_cell(store: Store, root: str, revision: int, code: str) -> None:
    store.log_cell(
        frame_id=root,
        root_frame_id=root,
        project_id="project-revert",
        cell_index=revision,
        state_revision=revision,
        language="python",
        code=code,
        result={"id": f"cell-{revision}", "stdout": "", "stderr": ""},
    )


def _save_text_artifact(
    store: Store,
    root: str,
    path,
    *,
    artifact_id: str | None = None,
) -> dict:
    return store.save_artifact(
        path=str(path),
        filename=path.name,
        content_type="text/plain",
        size_bytes=path.stat().st_size,
        checksum=hashlib.sha256(path.read_bytes()).hexdigest(),
        frame_id=root,
        root_frame_id=root,
        project_id="project-revert",
        artifact_id=artifact_id,
    )


def _texts(messages: list[dict]) -> list[str]:
    return [str(message.get("content") or "") for message in messages]


def _provider_texts(store: Store, root: str) -> list[str]:
    return [
        str(message.get("content") or "")
        for message in restore_action_history(store, root, branch_id=root)
        if message.get("role") == "user"
    ]


def _cell_sources(store: Store, root: str) -> list[str]:
    view = ExecutionViewService(store=store, format_timestamp=lambda value: str(value))
    return [entry["source"] for entry in view.execution_log(root)["entries"]]


def test_revert_restart_and_undo_restore_history_data_and_policy(tmp_path):
    database = tmp_path / "openai4s.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    managed = workspace / "analysis.txt"
    store = Store(database)
    store.create_project(name="Revert", project_id="project-revert")
    root = store.new_frame(project_id="project-revert", kind="turn", status="ready")
    service = SessionDomainService(
        store,
        data_dir=tmp_path,
        workspace=lambda _root, _branch: workspace,
    )

    managed.write_text("version one\n", encoding="utf-8")
    store.update_frame(root, runtime_env="base")
    store.set_capability_enabled(
        "skill", "revert-skill", False, scope="session", scope_id=root
    )
    store.set_permission_rule(
        scope="conversation",
        scope_id=root,
        tool="web_fetch",
        pattern="example.org/*",
        decision="allow",
    )
    version_one = _save_text_artifact(store, root, managed)
    store.add_message(
        root_frame_id=root,
        branch_id=root,
        frame_id=root,
        role="user",
        content="kept before revert",
    )
    _append_provider_user(store, root, root, "kept before revert")
    _log_cell(store, root, 1, "kept_cell = 1")
    first = service.create_checkpoint(root, reason="first")

    managed.write_text("version two\n", encoding="utf-8")
    store.update_frame(root, runtime_env="science")
    store.set_capability_enabled(
        "skill", "revert-skill", True, scope="session", scope_id=root
    )
    store.set_permission_rule(
        scope="conversation",
        scope_id=root,
        tool="web_fetch",
        pattern="example.org/*",
        decision="deny",
    )
    version_two = _save_text_artifact(
        store,
        root,
        managed,
        artifact_id=version_one["artifact_id"],
    )
    store.add_message(
        root_frame_id=root,
        branch_id=root,
        frame_id=root,
        role="user",
        content="abandoned middle",
    )
    _append_provider_user(store, root, root, "abandoned middle")
    _log_cell(store, root, 2, "abandoned_cell = 2")
    service.create_checkpoint(root, reason="second")

    reverted = service.revert_apply(
        root,
        target_checkpoint_id=first["checkpoint_id"],
    )
    assert reverted["ok"] is True
    revert_checkpoint_id = reverted["checkpoint"]["checkpoint_id"]
    assert _texts(store.list_branch_messages(root, branch_id=root, limit=None)) == [
        "kept before revert"
    ]
    assert _provider_texts(store, root) == ["kept before revert"]
    assert _cell_sources(store, root) == ["kept_cell = 1"]
    assert store.get_artifact(version_one["artifact_id"])["latest_version_id"] == (
        version_one["version_id"]
    )
    assert store.get_frame(root)["runtime_env"] == "base"
    assert (
        store.capability_state(project_id="project-revert", session_id=root).is_enabled(
            "skill", "revert-skill"
        )
        is False
    )
    assert (
        store.resolve_permission(
            root_frame_id=root,
            project_id="project-revert",
            tool="web_fetch",
            pattern_input="example.org/item",
        )
        == "allow"
    )

    # Continue after the revert.  Physical ordinals/revisions remain above the
    # abandoned interval, but only this new tail joins the target prefix.
    store.add_message(
        root_frame_id=root,
        branch_id=root,
        frame_id=root,
        role="user",
        content="continued after revert",
    )
    _append_provider_user(store, root, root, "continued after revert")
    _log_cell(store, root, 3, "continued_cell = 3")
    service.create_checkpoint(root, reason="continued")
    expected_after_revert = ["kept before revert", "continued after revert"]
    assert _texts(store.list_branch_messages(root, branch_id=root, limit=None)) == (
        expected_after_revert
    )
    assert _provider_texts(store, root) == expected_after_revert
    assert _cell_sources(store, root) == ["kept_cell = 1", "continued_cell = 3"]

    # A daemon restart must derive the same history solely from durable rows.
    store.close()
    store = Store(database)
    service = SessionDomainService(
        store,
        data_dir=tmp_path,
        workspace=lambda _root, _branch: workspace,
    )
    assert _provider_texts(store, root) == expected_after_revert
    assert _texts(store.list_branch_messages(root, branch_id=root, limit=None)) == (
        expected_after_revert
    )
    assert _cell_sources(store, root) == ["kept_cell = 1", "continued_cell = 3"]

    undone = service.revert_undo(
        root,
        revert_checkpoint_id=revert_checkpoint_id,
    )
    assert undone["ok"] is True
    assert _texts(store.list_branch_messages(root, branch_id=root, limit=None)) == [
        "kept before revert",
        "abandoned middle",
    ]
    assert _provider_texts(store, root) == [
        "kept before revert",
        "abandoned middle",
    ]
    assert _cell_sources(store, root) == ["kept_cell = 1", "abandoned_cell = 2"]
    assert store.get_artifact(version_one["artifact_id"])["latest_version_id"] == (
        version_two["version_id"]
    )
    assert store.get_frame(root)["runtime_env"] == "science"
    assert (
        store.capability_state(project_id="project-revert", session_id=root).is_enabled(
            "skill", "revert-skill"
        )
        is True
    )
    assert (
        store.resolve_permission(
            root_frame_id=root,
            project_id="project-revert",
            tool="web_fetch",
            pattern_input="example.org/item",
        )
        == "deny"
    )
    store.close()


def test_branch_message_projection_never_mixes_sibling_conversations(tmp_path):
    store = Store(tmp_path / "openai4s.db")
    store.create_project(name="Branches", project_id="project-revert")
    root = store.new_frame(project_id="project-revert", kind="turn", status="ready")
    workspaces = tmp_path / "workspaces"

    def workspace(_root: str, branch: str):
        path = workspaces / branch
        path.mkdir(parents=True, exist_ok=True)
        return path

    service = SessionDomainService(
        store,
        data_dir=tmp_path,
        workspace=workspace,
    )
    store.add_message(
        root_frame_id=root,
        branch_id=root,
        role="user",
        content="shared prefix",
    )
    checkpoint = service.create_checkpoint(root, reason="branch base")
    branch = service.fork_branch(root, from_checkpoint_id=checkpoint["checkpoint_id"])
    child = branch["branch_id"]
    store.add_message(
        root_frame_id=root,
        branch_id=root,
        role="assistant",
        content="root only",
    )
    store.add_message(
        root_frame_id=root,
        branch_id=child,
        role="assistant",
        content="child only",
    )

    assert _texts(store.list_branch_messages(root, branch_id=root, limit=None)) == [
        "shared prefix",
        "root only",
    ]
    assert _texts(store.list_branch_messages(root, branch_id=child, limit=None)) == [
        "shared prefix",
        "child only",
    ]
    # The append-only audit source still retains all three physical rows.
    assert set(_texts(store.list_messages(root, limit=None))) == {
        "shared prefix",
        "root only",
        "child only",
    }
    store.close()


def test_legacy_messages_are_backfilled_to_the_canonical_root_branch(tmp_path):
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE messages(message_id TEXT PRIMARY KEY,root_frame_id TEXT "
            "NOT NULL,frame_id TEXT,seq INTEGER NOT NULL,role TEXT NOT NULL,"
            "content TEXT,metadata TEXT,created_at INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)",
            ("m-legacy", "root-legacy", "root-legacy", 0, "user", "legacy", None, 1),
        )
        connection.commit()

    store = Store(database)
    row = store._conn.execute(  # noqa: SLF001 - migration contract assertion
        "SELECT branch_id FROM messages WHERE message_id='m-legacy'"
    ).fetchone()
    assert row["branch_id"] == "root-legacy"
    store.close()
