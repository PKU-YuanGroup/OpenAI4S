"""Durable plan/review/memory state bound to immutable checkpoints."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading

import pytest

from openai4s.server.session_domain import SessionDomainService
from openai4s.storage.checkpoint_state import CheckpointStateRepository
from openai4s.store import Store


def _session(tmp_path):
    database = tmp_path / "openai4s.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    store = Store(database)
    store.create_project(name="Checkpoint state", project_id="science")
    root = store.new_frame(project_id="science", kind="turn", status="ready")
    service = SessionDomainService(
        store,
        data_dir=tmp_path,
        workspace=lambda _root, _branch: workspace,
    )
    return database, workspace, store, root, service


def _review_step(store: Store, root: str, step_id: str, verdict: str) -> None:
    store.add_step(
        step_id=step_id,
        frame_id=root,
        kind="review",
        title="Reviewer",
        input={"mode": "manual", "model": "review-model"},
        status="running",
    )
    store.update_step(
        step_id,
        status="done",
        output={"verdict": verdict, "issues": []},
        summary=f"Review {verdict}",
    )


def _annotation(store: Store, root: str, workspace, body: str) -> dict:
    path = workspace / "review.png"
    path.write_bytes(b"review-image")
    artifact = store.save_artifact(
        path=str(path),
        filename=path.name,
        content_type="image/png",
        size_bytes=path.stat().st_size,
        checksum=hashlib.sha256(path.read_bytes()).hexdigest(),
        frame_id=root,
        root_frame_id=root,
        project_id="science",
    )
    return store.add_annotation(
        root_frame_id=root,
        artifact_id=artifact["artifact_id"],
        artifact_name=path.name,
        rel_x=0.25,
        rel_y=0.75,
        body=body,
    )


def test_checkpoint_state_schema_migrates_pre_quarantine_table(tmp_path):
    connection = sqlite3.connect(tmp_path / "legacy-state.db")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE checkpoint_state_snapshots("
        "checkpoint_id TEXT PRIMARY KEY,root_frame_id TEXT NOT NULL,"
        "branch_id TEXT NOT NULL,project_id TEXT NOT NULL,"
        "schema_version INTEGER NOT NULL,state_json TEXT NOT NULL,"
        "state_sha256 TEXT NOT NULL,source_checkpoint_id TEXT,"
        "created_at INTEGER NOT NULL)"
    )
    connection.commit()

    CheckpointStateRepository(
        connection,
        threading.RLock(),
        clock_ms=lambda: 1,
    )

    columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(checkpoint_state_snapshots)"
        ).fetchall()
    }
    assert {"trust_state", "import_source_sha256"} <= columns
    connection.close()


def test_checkpoint_captures_full_structured_state_and_survives_reopen(tmp_path):
    database, workspace, store, root, service = _session(tmp_path)
    plan = store.create_plan(
        frame_id=root,
        project_id="science",
        title="Protein design",
        rationale="Preserve the complete rationale",
        confidence="high",
        steps=[
            {
                "id": "s1",
                "title": "Generate",
                "detail": "Generate candidates",
                "deliverables": ["sequences.csv"],
            }
        ],
    )
    store.set_plan_step_status(plan["plan_id"], "s1", "completed", "done")
    memory = store.add_memory(
        project_id="science",
        block="facts",
        content="The active scaffold is 2XYZ.",
    )
    _review_step(store, root, "review-first", "pass")
    store.set_setting(f"review:auto:{root}", "1")
    store.set_setting(f"review:model:{root}", "review-model")
    annotation = _annotation(store, root, workspace, "Inspect this active site")

    checkpoint = service.create_checkpoint(root, reason="full_state")
    snapshot = store.get_checkpoint_state_snapshot(
        checkpoint["checkpoint_id"], include_state=True
    )

    assert snapshot is not None
    assert snapshot["counts"] == {
        "plans": 1,
        "review_steps": 1,
        "annotations": 1,
        "memories": 1,
    }
    state = snapshot["state"]
    assert state["plans"][0]["rationale"] == "Preserve the complete rationale"
    assert state["plans"][0]["steps"][0]["deliverables"] == ["sequences.csv"]
    assert state["plans"][0]["step_status"]["s1"]["note"] == "done"
    assert state["review"]["steps"][0]["output"]["verdict"] == "pass"
    assert state["review"]["settings"]["auto_review"]["value"] == "1"
    assert (
        state["review"]["annotations"][0]["annotation_id"]
        == annotation["annotation_id"]
    )
    assert state["memory"]["entries"] == [memory]
    digest = snapshot["state_sha256"]
    store.close()

    reopened = Store(database)
    durable = reopened.get_checkpoint_state_snapshot(
        checkpoint["checkpoint_id"], include_state=True
    )
    assert durable is not None
    assert durable["state_sha256"] == digest
    assert durable["state"] == state
    with pytest.raises(PermissionError, match="checkpoint_state_snapshots"):
        reopened.query("SELECT * FROM checkpoint_state_snapshots")
    reopened.close()


def test_revert_restart_and_undo_restore_plan_review_and_memory(tmp_path):
    database, workspace, store, root, service = _session(tmp_path)
    plan = store.create_plan(
        frame_id=root,
        project_id="science",
        title="First plan",
        rationale="first rationale",
        confidence="medium",
        steps=[{"id": "s1", "title": "First step"}],
    )
    first_memory = store.add_memory(
        project_id="science", block="facts", content="first memory"
    )
    _review_step(store, root, "review-first", "pass")
    store.set_setting(f"review:auto:{root}", "0")
    store.set_setting(f"review:model:{root}", "first-reviewer")
    annotation = _annotation(store, root, workspace, "first annotation")
    first = service.create_checkpoint(root, reason="first")

    store.update_plan(
        plan["plan_id"],
        title="Second plan",
        rationale="second rationale",
        steps=[{"id": "s2", "title": "Second step"}],
        status="completed",
        step_status={"s2": {"status": "completed", "note": "second"}},
    )
    store.delete_memory(first_memory["memory_id"])
    second_memory = store.add_memory(
        project_id="science", block="facts", content="second memory"
    )
    store.update_step(
        "review-first",
        status="error",
        output={"verdict": "issues", "issues": [{"title": "second"}]},
        summary="Second review state",
    )
    _review_step(store, root, "review-second", "issues")
    store.set_setting(f"review:auto:{root}", "1")
    store.set_setting(f"review:model:{root}", "second-reviewer")
    store.update_annotation(annotation["annotation_id"], body="second annotation")
    service.create_checkpoint(root, reason="second")

    reverted = service.revert_apply(
        root,
        target_checkpoint_id=first["checkpoint_id"],
    )
    assert reverted["ok"] is True
    assert reverted["projection"]["session_state"]["applied"] is True
    assert reverted["projection"]["partial"] is False
    revert_checkpoint_id = reverted["checkpoint"]["checkpoint_id"]
    cloned = store.get_checkpoint_state_snapshot(revert_checkpoint_id)
    assert cloned["source_checkpoint_id"] == first["checkpoint_id"]
    assert store.get_plan(plan["plan_id"])["title"] == "First plan"
    assert store.get_plan(plan["plan_id"])["steps"][0]["id"] == "s1"
    assert store.list_memories(project_id="science") == [first_memory]
    reviews = [item for item in store.list_steps(root) if item["kind"] == "review"]
    assert [item["step_id"] for item in reviews] == ["review-first"]
    assert reviews[0]["output"]["verdict"] == "pass"
    assert store.get_setting(f"review:auto:{root}") == "0"
    assert store.get_setting(f"review:model:{root}") == "first-reviewer"
    assert store.list_annotations(root)[0]["body"] == "first annotation"

    store.close()
    store = Store(database)
    service = SessionDomainService(
        store,
        data_dir=tmp_path,
        workspace=lambda _root, _branch: workspace,
    )
    assert store.get_plan(plan["plan_id"])["title"] == "First plan"
    assert store.list_memories(project_id="science") == [first_memory]

    undone = service.revert_undo(
        root,
        revert_checkpoint_id=revert_checkpoint_id,
    )
    assert undone["ok"] is True
    assert undone["projection"]["session_state"]["applied"] is True
    restored_plan = store.get_plan(plan["plan_id"])
    assert restored_plan["title"] == "Second plan"
    assert restored_plan["steps"][0]["id"] == "s2"
    assert store.list_memories(project_id="science") == [second_memory]
    reviews = [item for item in store.list_steps(root) if item["kind"] == "review"]
    assert [item["step_id"] for item in reviews] == [
        "review-first",
        "review-second",
    ]
    assert reviews[0]["output"]["verdict"] == "issues"
    assert store.get_setting(f"review:auto:{root}") == "1"
    assert store.get_setting(f"review:model:{root}") == "second-reviewer"
    assert store.list_annotations(root)[0]["body"] == "second annotation"
    assert (
        undone["checkpoint"]["metadata"]["reverted_to"]
        == reverted["undo_checkpoint_id"]
    )
    undo_state = store.get_checkpoint_state_snapshot(
        undone["checkpoint"]["checkpoint_id"]
    )
    assert undo_state["source_checkpoint_id"] == reverted["undo_checkpoint_id"]
    store.close()


def test_legacy_checkpoint_is_partial_and_preserves_live_state(tmp_path):
    _database, _workspace, store, root, service = _session(tmp_path)
    plan = store.create_plan(
        frame_id=root,
        project_id="science",
        title="At checkpoint",
        rationale="",
        confidence="low",
        steps=[{"id": "s1", "title": "One"}],
    )
    checkpoint = service.create_checkpoint(root, reason="legacy")
    with store._lock:
        store._conn.execute(
            "DELETE FROM checkpoint_state_snapshots WHERE checkpoint_id=?",
            (checkpoint["checkpoint_id"],),
        )
        store._conn.commit()
    store.update_plan(plan["plan_id"], title="Live state must survive")
    live_memory = store.add_memory(project_id="science", content="live memory")

    projection = store.activate_session_branch_checkpoint(
        root_frame_id=root,
        branch_id=root,
        checkpoint_id=checkpoint["checkpoint_id"],
        expected_current_branch_id=root,
    )

    assert projection["partial"] is True
    assert projection["session_state"]["available"] is False
    assert projection["session_state"]["reason"] == (
        "legacy_checkpoint_without_domain_state_snapshot"
    )
    assert store.get_plan(plan["plan_id"])["title"] == "Live state must survive"
    assert store.list_memories(project_id="science") == [live_memory]
    store.close()


def test_branch_activation_restores_an_exact_empty_domain_state(tmp_path):
    _database, _workspace, store, root, service = _session(tmp_path)
    empty = service.create_checkpoint(root, reason="empty")

    store.create_plan(
        frame_id=root,
        project_id="science",
        title="Later plan",
        rationale="",
        confidence="high",
        steps=[{"id": "s1", "title": "Later"}],
    )
    store.add_memory(project_id="science", content="later memory")
    _review_step(store, root, "review-later", "pass")
    store.set_setting(f"review:auto:{root}", "1")
    service.create_checkpoint(root, reason="later")
    branch = store.fork_session_branch(
        root_frame_id=root,
        from_checkpoint_id=empty["checkpoint_id"],
        branch_id="branch-empty",
    )

    projection = store.activate_session_branch_checkpoint(
        root_frame_id=root,
        branch_id=branch["branch_id"],
        checkpoint_id=empty["checkpoint_id"],
        expected_current_branch_id=root,
    )

    assert projection["current_branch_id"] == "branch-empty"
    assert projection["session_state"]["plans"]["count"] == 0
    assert projection["session_state"]["review"]["step_count"] == 0
    assert projection["session_state"]["memory"]["count"] == 0
    assert store.get_plan_by_frame(root) is None
    assert store.list_memories(project_id="science") == []
    assert [step for step in store.list_steps(root) if step["kind"] == "review"] == []
    assert store.get_setting(f"review:auto:{root}") is None
    store.close()


def test_corrupt_state_fails_closed_and_checkpoint_insert_is_atomic(
    monkeypatch, tmp_path
):
    _database, _workspace, store, root, service = _session(tmp_path)
    plan = store.create_plan(
        frame_id=root,
        project_id="science",
        title="Captured",
        rationale="",
        confidence="high",
        steps=[{"id": "s1", "title": "One"}],
    )
    checkpoint = service.create_checkpoint(root, reason="captured")
    store.update_plan(plan["plan_id"], title="Live mutation")
    with store._lock:
        store._conn.execute(
            "UPDATE checkpoint_state_snapshots SET state_json=? "
            "WHERE checkpoint_id=?",
            ('{"version":1,"plans":[]}', checkpoint["checkpoint_id"]),
        )
        store._conn.commit()

    with pytest.raises(ValueError, match="checksum|invalid"):
        store.activate_session_branch_checkpoint(
            root_frame_id=root,
            branch_id=root,
            checkpoint_id=checkpoint["checkpoint_id"],
            expected_current_branch_id=root,
        )
    assert store.get_plan(plan["plan_id"])["title"] == "Live mutation"

    original = store._checkpoint_states.capture_checkpoint

    def fail_capture(**_fields):
        raise RuntimeError("state capture failed")

    monkeypatch.setattr(store._checkpoint_states, "capture_checkpoint", fail_capture)
    with pytest.raises(RuntimeError, match="state capture failed"):
        service.create_checkpoint(root, reason="must_rollback")
    monkeypatch.setattr(store._checkpoint_states, "capture_checkpoint", original)
    assert [item["checkpoint_id"] for item in store.list_session_checkpoints(root)] == [
        checkpoint["checkpoint_id"]
    ]
    store.close()


def test_quarantined_import_remaps_all_identities_and_forces_review_off(tmp_path):
    _database, workspace, store, root, service = _session(tmp_path)
    source_plan = store.create_plan(
        frame_id=root,
        project_id="science",
        title="Exported plan",
        rationale="untrusted but structured",
        confidence="high",
        steps=[{"id": "s1", "title": "One"}],
    )
    source_memory = store.add_memory(
        project_id="science", block="facts", content="imported memory"
    )
    _review_step(store, root, "review-source", "pass")
    store.set_setting(f"review:auto:{root}", "1")
    store.set_setting(f"review:model:{root}", "source-reviewer")
    source_annotation = _annotation(store, root, workspace, "source annotation")
    source_artifact = store.get_artifact(source_annotation["artifact_id"])
    store.update_plan(
        source_plan["plan_id"], artifact_id=source_artifact["artifact_id"]
    )
    source_checkpoint = service.create_checkpoint(root, reason="export")
    envelope = store.get_checkpoint_state_snapshot(
        source_checkpoint["checkpoint_id"], include_state=True
    )
    changes_before_preflight = store._conn.total_changes
    preflight = store.validate_checkpoint_state_import(envelope)
    assert preflight["valid"] is True
    assert preflight["contains_bodies"] is False
    assert preflight["artifact_ids"] == [source_artifact["artifact_id"]]
    assert "state" not in preflight
    assert store._conn.total_changes == changes_before_preflight

    store.create_project(name="Imported", project_id="imported-project")
    imported_root = store.new_frame(
        project_id="imported-project", kind="turn", status="ready"
    )
    imported_path = workspace / "imported-review.png"
    imported_path.write_bytes(b"imported-review-image")
    imported_artifact = store.save_artifact(
        path=str(imported_path),
        filename=imported_path.name,
        content_type="image/png",
        size_bytes=imported_path.stat().st_size,
        checksum=hashlib.sha256(imported_path.read_bytes()).hexdigest(),
        frame_id=imported_root,
        root_frame_id=imported_root,
        project_id="imported-project",
    )
    imported_checkpoint = store.create_session_checkpoint(
        root_frame_id=imported_root,
        branch_id=imported_root,
        reason="imported",
        workspace_tree_id="b" * 64,
        capability_state={"version": 1, "states": []},
        permission_state={"conversation": []},
        metadata={"imported": True, "trust_state": "quarantined"},
    )

    projection = store.import_quarantined_checkpoint_state(
        envelope,
        checkpoint_id=imported_checkpoint["checkpoint_id"],
        root_frame_id=imported_root,
        branch_id=imported_root,
        project_id="imported-project",
        artifact_id_map={
            source_artifact["artifact_id"]: imported_artifact["artifact_id"]
        },
    )

    assert projection["trust_state"] == "quarantined_import"
    assert projection["quarantined"] is True
    assert projection["contains_bodies"] is False
    assert "state" not in projection
    listed = store.list_checkpoint_state_snapshots(imported_root)
    assert listed == [projection]
    imported = store.get_checkpoint_state_snapshot(
        imported_checkpoint["checkpoint_id"], include_state=True
    )
    state = imported["state"]
    imported_plan = state["plans"][0]
    imported_review = state["review"]["steps"][0]
    imported_annotation = state["review"]["annotations"][0]
    imported_memory = state["memory"]["entries"][0]
    assert imported_plan["plan_id"] != source_plan["plan_id"]
    assert imported_plan["frame_id"] == imported_root
    assert imported_plan["project_id"] == "imported-project"
    assert imported_plan["artifact_id"] == imported_artifact["artifact_id"]
    assert imported_review["step_id"] != "review-source"
    assert imported_review["frame_id"] == imported_root
    assert imported_annotation["annotation_id"] != source_annotation["annotation_id"]
    assert imported_annotation["artifact_id"] == imported_artifact["artifact_id"]
    assert imported_memory["memory_id"] != source_memory["memory_id"]
    assert imported_memory["project_id"] == "imported-project"
    assert state["review"]["settings"]["auto_review"] == {
        "present": True,
        "value": "0",
        "updated_at": state["review"]["settings"]["auto_review"]["updated_at"],
    }
    assert state["review"]["settings"]["reviewer_model"]["present"] is False

    activated = store.activate_session_branch_checkpoint(
        root_frame_id=imported_root,
        branch_id=imported_root,
        checkpoint_id=imported_checkpoint["checkpoint_id"],
        expected_current_branch_id=imported_root,
    )
    assert activated["session_state"]["trust_state"] == "quarantined_import"
    assert store.get_setting(f"review:auto:{imported_root}") == "0"
    assert store.get_setting(f"review:model:{imported_root}") is None
    assert store.get_plan_by_frame(imported_root)["plan_id"] == imported_plan["plan_id"]
    assert (
        store.list_memories(project_id="imported-project")[0]["memory_id"]
        == imported_memory["memory_id"]
    )
    store.close()


def test_quarantined_import_rejects_corruption_without_partial_state(tmp_path):
    _database, _workspace, store, root, service = _session(tmp_path)
    source = service.create_checkpoint(root, reason="source")
    envelope = store.get_checkpoint_state_snapshot(
        source["checkpoint_id"], include_state=True
    )
    envelope = {**envelope, "state_sha256": "0" * 64}

    store.create_project(name="Imported", project_id="imported-project")
    imported_root = store.new_frame(
        project_id="imported-project", kind="turn", status="ready"
    )
    target = store.create_session_checkpoint(
        root_frame_id=imported_root,
        branch_id=imported_root,
        reason="imported",
        workspace_tree_id="c" * 64,
        metadata={"imported": True},
    )

    with pytest.raises(ValueError, match="checksum"):
        store.import_quarantined_checkpoint_state(
            envelope,
            checkpoint_id=target["checkpoint_id"],
            root_frame_id=imported_root,
            branch_id=imported_root,
            project_id="imported-project",
        )
    assert store.get_checkpoint_state_snapshot(target["checkpoint_id"]) is None
    assert store.list_checkpoint_state_snapshots(imported_root) == []

    scoped = store.get_checkpoint_state_snapshot(
        source["checkpoint_id"], include_state=True
    )
    scoped_state = scoped["state"]
    scoped_state["memory"]["project_id"] = "another-project"
    scoped_body = json.dumps(
        scoped_state,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    scoped = {
        **scoped,
        "state": scoped_state,
        "state_sha256": hashlib.sha256(scoped_body).hexdigest(),
    }
    before = store._conn.total_changes
    with pytest.raises(ValueError, match="project scope"):
        store.validate_checkpoint_state_import(scoped)
    assert store._conn.total_changes == before
    store.close()
