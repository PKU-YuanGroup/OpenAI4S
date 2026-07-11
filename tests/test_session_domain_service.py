"""Store facade and route-friendly session-domain composition contracts."""

from __future__ import annotations

import hashlib
import uuid

import pytest

from openai4s.kernel.recovery import BootstrapManifest, RecoveryRecipe
from openai4s.server.session_domain import SessionDomainService
from openai4s.store import Store


def test_store_wires_immutable_snapshot_and_recovery_repositories(tmp_path):
    path = tmp_path / "openai4s.db"
    store = Store(path)
    with pytest.raises(ValueError, match="SHA-256"):
        store.create_session_checkpoint(
            root_frame_id="root",
            reason="invalid",
            workspace_tree_id="not-a-tree",
        )
    checkpoint = store.create_session_checkpoint(
        root_frame_id="root",
        reason="manual",
        workspace_tree_id="a" * 64,
        recovery_recipe={"version": 1, "steps": []},
    )
    operation = store.record_snapshot_operation(
        root_frame_id="root",
        branch_id="root",
        kind="revert",
        status="completed",
        preview={"writes": ["analysis.csv"]},
        target_checkpoint_id=checkpoint["checkpoint_id"],
        finished=True,
    )
    other = store.create_session_checkpoint(
        root_frame_id="other-root",
        reason="manual",
        workspace_tree_id="b" * 64,
    )
    with pytest.raises(ValueError, match="checkpoint mismatch"):
        store.record_snapshot_operation(
            root_frame_id="root",
            branch_id="root",
            kind="revert",
            status="failed",
            preview={},
            target_checkpoint_id=other["checkpoint_id"],
            finished=True,
        )
    store.append_recovery_event(
        recovery_id="recovery-1",
        root_frame_id="root",
        branch_id="root",
        phase="validate",
        status="partial",
        detail={"missing": ["model"]},
    )
    for table in (
        "session_branches",
        "session_checkpoints",
        "snapshot_operations",
        "recovery_journal",
    ):
        with pytest.raises(PermissionError, match=table):
            store.query(f"select * from {table}")
    store.close()

    reopened = Store(path)
    assert reopened.get_session_checkpoint(checkpoint["checkpoint_id"]) is not None
    assert reopened.get_snapshot_operation(operation["operation_id"])["preview"] == {
        "writes": ["analysis.csv"]
    }
    assert reopened.list_snapshot_operations("root")[0]["operation_id"] == operation[
        "operation_id"
    ]
    journal = reopened.list_recovery_events(
        root_frame_id="root", newest=True
    )
    assert journal[0]["detail"] == {"missing": ["model"]}
    reopened.close()


def test_empty_branch_projection_keeps_checkpoint_enabled_without_mutating(tmp_path):
    store = Store(tmp_path / "openai4s.db")
    root = store.new_frame(project_id="default", kind="turn", status="ready")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = SessionDomainService(
        store,
        data_dir=tmp_path,
        workspace=lambda _root, _branch: workspace,
    )

    projection = service.branches(root)

    assert projection["current_branch_id"] == root
    assert projection["branches"] == []
    assert projection["capabilities"]["checkpoint"]["enabled"] is True
    assert projection["capabilities"]["fork"]["enabled"] is False
    assert projection["capabilities"]["fork"]["fork_from_cell"] is False
    # GET/projection must not manufacture a branch row.
    assert store.list_session_branches(root) == []
    store.close()


def test_fork_materializes_an_isolated_workspace_from_checkpoint(tmp_path):
    store = Store(tmp_path / "openai4s.db")
    root = store.new_frame(project_id="default", kind="turn", status="ready")
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    (canonical / "analysis.txt").write_text("checkpoint bytes", encoding="utf-8")
    branch_root = tmp_path / "branches"

    def workspace(_root, branch_id):
        return canonical if branch_id == root else branch_root / branch_id

    service = SessionDomainService(
        store,
        data_dir=tmp_path,
        workspace=workspace,
    )
    checkpoint = service.create_checkpoint(root)
    fork = service.fork_branch(
        root,
        from_checkpoint_id=checkpoint["checkpoint_id"],
        branch_id="branch-isolated",
    )

    assert fork["workspace_isolated"] is True
    assert fork["workspace_materialized"] is True
    assert (branch_root / "branch-isolated" / "analysis.txt").read_text(
        encoding="utf-8"
    ) == "checkpoint bytes"
    assert (canonical / "analysis.txt").read_text(encoding="utf-8") == (
        "checkpoint bytes"
    )
    store.close()


def test_session_domain_composes_checkpoint_branch_timeline_export_and_renderer(
    tmp_path,
):
    store = Store(tmp_path / "openai4s.db")
    root = store.new_frame(
        project_id="project-a", kind="turn", status="ready"
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    analysis = workspace / "analysis.txt"
    analysis.write_text("version one", encoding="utf-8")

    store.append_action_group(
        root_frame_id=root,
        turn_id="turn-1",
        kind="user",
        assistant_message={"role": "user", "content": "Analyze data"},
    )
    cell_id = store.log_cell(
        frame_id=root,
        root_frame_id=root,
        project_id="project-a",
        code="print(42)",
        result={"stdout": "42\n", "stderr": "", "error": None},
        cell_index=1,
        language="python",
    )
    plot = workspace / "plot.png"
    plot.write_bytes(b"real-png-bytes")
    artifact = store.save_artifact(
        path=str(plot),
        filename="plot.png",
        content_type="image/png",
        size_bytes=plot.stat().st_size,
        checksum=hashlib.sha256(plot.read_bytes()).hexdigest(),
        producing_cell_id=cell_id,
        frame_id=root,
        root_frame_id=root,
        project_id="project-a",
    )
    manifest = BootstrapManifest(
        language="python",
        interpreter="/env/bin/python",
        runtime_version="3.12",
        working_directory=str(workspace),
    )
    generation_id = str(uuid.uuid4())
    store.create_kernel_generation(
        root_frame_id=root,
        branch_id=root,
        language="python",
        generation_id=generation_id,
        environment={"environment_name": "science"},
        bootstrap=manifest.record(),
        state="active",
    )

    events: list[dict] = []
    service = SessionDomainService(
        store,
        data_dir=tmp_path,
        workspace=lambda _root, _branch: workspace,
        event_sink=events.append,
    )
    first = service.create_checkpoint(root, reason="turn_complete")
    analysis.write_text("version two", encoding="utf-8")
    second = service.create_checkpoint(
        root,
        reason="turn_complete",
        expected_head=first["checkpoint_id"],
    )

    listed = service.checkpoints(root)
    assert [item["checkpoint_id"] for item in listed["checkpoints"]][:2] == [
        second["checkpoint_id"],
        first["checkpoint_id"],
    ]
    assert first["recovery_recipe"]["steps"][0] == {
        "kind": "hydrate_workspace",
        "payload": {"tree_id": first["workspace_tree_id"]},
        "replay_policy": "never",
    }
    assert any(
        step.get("payload", {}).get("version_id") == artifact["version_id"]
        for step in first["recovery_recipe"]["steps"]
    )
    assert first["generation_refs"]["python"]["bootstrap"]["version"] == 1
    active_restore = next(
        item for item in service.recovery_actions(root)["actions"]
        if item["id"] == "restore"
    )
    assert active_restore["enabled"] is False
    assert active_restore["reason"] == "kernel is already active"
    store.finish_kernel_generation(
        generation_id,
        state="released",
        reason="idle_ttl",
    )
    restore_action = next(
        item for item in service.recovery_actions(root)["actions"]
        if item["id"] == "restore"
    )
    assert restore_action["enabled"] is True

    branch = service.fork_branch(
        root,
        from_checkpoint_id=first["checkpoint_id"],
        branch_id="branch-alternative",
        name="Alternative",
    )
    assert branch["head_checkpoint_id"] == first["checkpoint_id"]
    assert {item["branch_id"] for item in service.branches(root)["branches"]} == {
        root,
        "branch-alternative",
    }

    preview = service.revert_preview(
        root,
        target_checkpoint_id=first["checkpoint_id"],
    )
    assert preview["can_apply"] is True
    assert preview["workspace"]["writes"][0]["path"] == "analysis.txt"
    reverted = service.revert_apply(
        root,
        target_checkpoint_id=first["checkpoint_id"],
    )
    assert reverted["ok"] is True
    assert analysis.read_text(encoding="utf-8") == "version one"
    undone = service.revert_undo(
        root,
        revert_checkpoint_id=reverted["checkpoint"]["checkpoint_id"],
    )
    assert undone["ok"] is True
    assert analysis.read_text(encoding="utf-8") == "version two"
    assert service.revert_operations(root)

    timeline = service.action_timeline(root)
    assert timeline["count"] >= 1
    assert any(group["kind"] == "checkpoint" for group in timeline["groups"])
    assert any(group["kind"] == "revert" for group in timeline["groups"])
    exported = service.notebook_export(root, language="python")
    assert exported["filename"].endswith(".python.ipynb")
    assert exported["sha256"] == hashlib.sha256(exported["data"]).hexdigest()
    renderer = service.artifact_renderer(
        artifact["artifact_id"],
        version_id=artifact["version_id"],
        root_frame_id=root,
    )
    assert renderer["renderer"]["renderer_id"] == "image"
    assert renderer["version_id"] == artifact["version_id"]
    assert renderer["immutable"]["checksum"] == artifact["checksum"]
    assert events[0]["type"] == "checkpoint_created"
    reverted_events = [event for event in events if event["type"] == "branch_reverted"]
    assert len(reverted_events) == 2
    assert all(event["root_frame_id"] == root for event in reverted_events)
    assert all("operation" not in event for event in reverted_events)
    assert all("checkpoint" not in event for event in reverted_events)
    store.close()


def test_recovery_projection_is_redacted_and_actions_fail_closed_or_enable(
    tmp_path,
):
    store = Store(tmp_path / "openai4s.db")
    root = store.new_frame(project_id="default", kind="turn", status="ready")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "data.txt").write_text("data", encoding="utf-8")
    service = SessionDomainService(
        store,
        data_dir=tmp_path,
        workspace=lambda _root, _branch: workspace,
    )
    service.create_checkpoint(root)
    unavailable = service.recovery_actions(root)
    restore = next(item for item in unavailable["actions"] if item["id"] == "restore")
    assert restore["enabled"] is False
    assert "bootstrap manifest" in restore["reason"]

    service.recovery.record(
        {
            "recovery_id": "recovery-partial",
            "root_frame_id": root,
            "branch_id": root,
            "phase": "validate",
            "status": "partial",
            "detail": {"missing": ["model"], "api_key": "must-not-leak"},
        }
    )
    status = service.recovery_status(root)
    assert status["state"] == "partial"
    assert status["current"]["events"][0]["detail"]["api_key"] == "<redacted>"
    assert "must-not-leak" not in repr(status)
    assert store.list_recovery_events(
        recovery_id="recovery-partial"
    )[0]["detail"]["api_key"] == "<redacted>"
    retry = next(
        item for item in service.recovery_actions(root)["actions"]
        if item["id"] == "retry"
    )
    assert retry["enabled"] is False
    store.close()


def test_recovery_pipeline_factory_persists_every_phase(tmp_path):
    store = Store(tmp_path / "openai4s.db")
    root = store.new_frame(project_id="default", kind="turn", status="ready")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = SessionDomainService(
        store,
        data_dir=tmp_path,
        workspace=lambda _root, _branch: workspace,
    )

    class Candidate:
        generation_id = "candidate-generation"

        def shutdown(self):
            raise AssertionError("verified candidate must be published")

    published: list[str] = []
    pipeline = service.recovery.pipeline(
        build_candidate=lambda _manifest: Candidate(),
        bootstrap_candidate=lambda _candidate, _manifest: None,
        hydrate_workspace=lambda _candidate, _payload: None,
        hydrate_artifact=lambda _candidate, _payload: None,
        execute_cell=lambda _candidate, _code, _language: {"error": None},
        inspect_symbols=lambda _candidate, _language: (),
        artifact_digest=lambda _candidate, _name: None,
        inspect_environment=lambda _candidate: {
            "interpreter": "/env/bin/python"
        },
        publish=lambda candidate: published.append(candidate.generation_id),
    )
    manifest = BootstrapManifest(
        language="python",
        interpreter="/env/bin/python",
        runtime_version="3.12",
        working_directory=str(workspace),
    )
    result = pipeline.restore(
        root_frame_id=root,
        branch_id=root,
        manifest=manifest,
        recipe=RecoveryRecipe(),
        source_generation_id=None,
        recovery_id="recovery-verified",
    )

    assert result.status == "active"
    assert published == ["candidate-generation"]
    rows = store.list_recovery_events(recovery_id="recovery-verified")
    assert [(row["phase"], row["status"]) for row in rows] == [
        ("restore", "started"),
        ("build", "completed"),
        ("bootstrap", "completed"),
        ("validate", "completed"),
        ("publish", "completed"),
    ]
    assert service.recovery_status(root)["state"] == "active"
    store.close()
