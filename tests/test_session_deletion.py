from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from openai4s.config import Config
from openai4s.host.data import kernel_artifact_input_dir
from openai4s.server.delivery import CompletionDeliveryService
from openai4s.server.session_deletion import SessionDeletionService
from openai4s.storage.snapshots import WorkspaceCAS
from openai4s.store import get_store
from openai4s.tools.dynamic_scopes import DynamicScopeStore


def test_session_delete_cleans_new_aggregate_and_preserves_shared_cas(tmp_path):
    cfg = Config(data_dir=tmp_path)
    store = get_store(cfg.db_path)
    store.create_project(project_id="science", name="Science")
    deleted = store.new_frame(project_id="science", status="ready")
    child = store.new_frame(parent_id=deleted, kind="delegate")
    kept = store.new_frame(project_id="science", status="ready")

    workspace_root = tmp_path / "agent-workspaces"
    deleted_workspace = workspace_root / deleted
    kept_workspace = workspace_root / kept
    deleted_workspace.mkdir(parents=True)
    kept_workspace.mkdir(parents=True)
    (deleted_workspace / "scratch.txt").write_text("delete", "utf-8")
    (kept_workspace / "scratch.txt").write_text("keep", "utf-8")
    branch_root = (
        workspace_root / ".branches" / hashlib.sha256(deleted.encode()).hexdigest()[:24]
    )
    branch_root.mkdir(parents=True)
    (branch_root / "branch.txt").write_text("delete", "utf-8")
    dynamic_root = tmp_path / "dynamic-tools" / deleted
    dynamic_root.mkdir(parents=True)
    (dynamic_root / "manifest.json").write_text("{}", "utf-8")
    shared_dynamic_root = tmp_path / "dynamic-tools" / "_scoped"
    shared_dynamic_root.mkdir(parents=True)
    (shared_dynamic_root / "events.jsonl").write_text("shared", "utf-8")
    deleted_inputs = kernel_artifact_input_dir(tmp_path, deleted)
    kept_inputs = kernel_artifact_input_dir(tmp_path, kept)
    deleted_inputs.mkdir(parents=True)
    kept_inputs.mkdir(parents=True)
    (deleted_inputs / "version.bin").write_bytes(b"delete")
    (kept_inputs / "version.bin").write_bytes(b"keep")

    cas = WorkspaceCAS(tmp_path / "workspace-cas")
    shared_blob = cas.put_blob(b"shared")
    unique_blob = cas.put_blob(b"unique")
    shared_tree = cas.put_tree(
        [{"path": "shared.txt", "blob": shared_blob, "size": 6, "mode": 0o600}]
    )
    unique_tree = cas.put_tree(
        [{"path": "unique.txt", "blob": unique_blob, "size": 6, "mode": 0o600}]
    )

    unique_snapshot = tmp_path / "artifact-versions" / "unique.bin"
    unique_snapshot.parent.mkdir(parents=True)
    unique_snapshot.write_bytes(b"unique")
    shared_snapshot = tmp_path / "artifact-versions" / "shared.bin"
    shared_snapshot.write_bytes(b"shared")
    shared_alias = str(
        tmp_path / "artifact-versions" / ".." / "artifact-versions" / "shared.bin"
    )
    outside = tmp_path.parent / f"outside-{deleted}.bin"
    outside.write_bytes(b"outside")

    with store._lock:
        db = store._conn
        db.executemany(
            "INSERT INTO action_groups(group_id,root_frame_id,branch_id,turn_id,"
            "ordinal,kind,created_at) VALUES(?,?,?,?,?,?,1)",
            [
                ("group-delete", deleted, deleted, "turn-delete", 0, "cell"),
                ("group-keep", kept, kept, "turn-keep", 0, "cell"),
            ],
        )
        db.executemany(
            "INSERT INTO action_events(event_id,group_id,sequence,type,created_at) "
            "VALUES(?,?,?,?,1)",
            [
                ("event-delete", "group-delete", 0, "proposed"),
                ("event-keep", "group-keep", 0, "proposed"),
            ],
        )
        db.executemany(
            "INSERT INTO execution_attempts(attempt_id,group_id,producing_cell_id,"
            "attempt_ordinal,allocated_at) VALUES(?,?,?,?,1)",
            [
                ("attempt-delete", "group-delete", "cell-delete", 0),
                ("attempt-keep", "group-keep", "cell-keep", 0),
            ],
        )
        db.executemany(
            "INSERT INTO kernel_generations(generation_id,root_frame_id,branch_id,"
            "language,ordinal,state,started_at,last_activity_at) VALUES(?,?,?,?,0,'failed',1,1)",
            [
                ("generation-delete", deleted, deleted, "python"),
                ("generation-keep", kept, kept, "python"),
            ],
        )
        db.executemany(
            "INSERT INTO recovery_journal(entry_id,recovery_id,root_frame_id,"
            "branch_id,sequence,phase,status,detail,created_at) VALUES(?,?,?,?,0,'plan','done','{}',1)",
            [
                ("journal-delete", "recovery-delete", deleted, deleted),
                ("journal-keep", "recovery-keep", kept, kept),
            ],
        )
        db.executemany(
            "INSERT INTO session_branches(branch_id,root_frame_id,created_at,updated_at) "
            "VALUES(?,?,1,1)",
            [(deleted, deleted), (kept, kept)],
        )
        db.executemany(
            "INSERT INTO session_checkpoints(checkpoint_id,root_frame_id,branch_id,"
            "reason,workspace_tree_id,artifact_versions,environment_pins,generation_refs,"
            "capability_state,permission_state,recovery_recipe,metadata,created_at) "
            "VALUES(?,?,?,'test',?,'[]','{}','{}','{}','{}','{}','{}',1)",
            [
                ("checkpoint-delete-unique", deleted, deleted, unique_tree["tree_id"]),
                ("checkpoint-delete-shared", deleted, deleted, shared_tree["tree_id"]),
                ("checkpoint-keep-shared", kept, kept, shared_tree["tree_id"]),
            ],
        )
        db.executemany(
            "INSERT INTO snapshot_operations(operation_id,root_frame_id,branch_id,"
            "kind,status,preview,created_at) VALUES(?,?,?,'checkpoint','done','{}',1)",
            [("operation-delete", deleted, deleted), ("operation-keep", kept, kept)],
        )
        db.executemany(
            "INSERT INTO permission_requests(decision_id,root_frame_id,frame_id,"
            "project_id,tool,target,state,created_at) VALUES(?,?,?,?,?,'','pending',1)",
            [
                ("request-delete", deleted, child, "science", "bash"),
                ("request-keep", kept, kept, "science", "bash"),
            ],
        )
        db.executemany(
            "INSERT INTO host_call_log(call_id,frame_id,method,created_at) VALUES(?,?,?,1)",
            [("call-delete", child, "query"), ("call-keep", kept, "query")],
        )
        db.executemany(
            "INSERT INTO artifacts(artifact_id,project_id,root_frame_id,filename,"
            "created_at,updated_at) VALUES(?,?,?,?,1,1)",
            [
                ("artifact-delete", "science", deleted, "delete.bin"),
                ("artifact-outside", "science", deleted, "outside.bin"),
                ("artifact-delete-shared", "science", deleted, "shared.bin"),
                ("artifact-keep", "science", kept, "shared.bin"),
            ],
        )
        db.executemany(
            "INSERT INTO artifact_versions(version_id,artifact_id,path,snapshot_path,"
            "frame_id,created_at) VALUES(?,?,?,?,?,1)",
            [
                (
                    "version-delete",
                    "artifact-delete",
                    str(deleted_workspace / "delete.bin"),
                    str(unique_snapshot),
                    child,
                ),
                (
                    "version-outside",
                    "artifact-outside",
                    str(outside),
                    str(outside),
                    child,
                ),
                (
                    "version-delete-shared",
                    "artifact-delete-shared",
                    shared_alias,
                    shared_alias,
                    child,
                ),
                (
                    "version-keep",
                    "artifact-keep",
                    str(shared_snapshot),
                    str(shared_snapshot),
                    kept,
                ),
            ],
        )
        db.execute(
            "INSERT INTO lineage_edges(edge_id,input_version_id,output_version_id,"
            "frame_id,created_at) VALUES('edge-delete','version-delete','version-delete',?,1)",
            (child,),
        )
        db.executemany(
            "INSERT INTO capability_states(kind,name,normalized_name,scope,scope_id,"
            "enabled,created_at,updated_at) VALUES('skill','X','x','session',?,1,1,1)",
            [(deleted,), (kept,)],
        )
        db.executemany(
            "INSERT INTO capability_manifests(manifest_id,session_id,project_id,kind,entries,created_at) "
            "VALUES(?,?,'science','skill','[]',1)",
            [("manifest-delete", deleted), ("manifest-keep", kept)],
        )
        db.commit()

    dropped: list[tuple[str, str]] = []
    resumed: list[str] = []
    service = SessionDeletionService(
        store,
        data_dir=tmp_path,
        cas=cas,
        drop_runtime=lambda root, reason: dropped.append((root, reason)),
        drop_resume_window=resumed.append,
    )
    result = service.delete_session(deleted)

    assert result["ok"] is True and result["freed_sessions"] == 1
    assert dropped == [(deleted, "frame_deleted")] and resumed == [deleted]
    assert not deleted_workspace.exists() and not branch_root.exists()
    assert not dynamic_root.exists() and shared_dynamic_root.exists()
    assert not deleted_inputs.exists() and kept_inputs.exists()
    assert kept_workspace.exists()
    assert (
        not unique_snapshot.exists() and shared_snapshot.exists() and outside.exists()
    )
    assert not cas._tree_path(unique_tree["tree_id"]).exists()
    assert not cas._blob_path(unique_blob).exists()
    assert cas._tree_path(shared_tree["tree_id"]).exists()
    assert cas._blob_path(shared_blob).exists()

    with store._lock:
        db = store._conn
        for table, column in (
            ("action_groups", "root_frame_id"),
            ("kernel_generations", "root_frame_id"),
            ("recovery_journal", "root_frame_id"),
            ("session_branches", "root_frame_id"),
            ("session_checkpoints", "root_frame_id"),
            ("snapshot_operations", "root_frame_id"),
            ("permission_requests", "root_frame_id"),
            ("artifacts", "root_frame_id"),
        ):
            assert (
                db.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {column}=?", (deleted,)
                ).fetchone()[0]
                == 0
            )
            assert (
                db.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {column}=?", (kept,)
                ).fetchone()[0]
                >= 1
            )
        assert (
            db.execute(
                "SELECT COUNT(*) FROM action_events WHERE group_id='group-delete'"
            ).fetchone()[0]
            == 0
        )
        assert (
            db.execute(
                "SELECT COUNT(*) FROM execution_attempts WHERE group_id='group-delete'"
            ).fetchone()[0]
            == 0
        )
        assert (
            db.execute(
                "SELECT COUNT(*) FROM host_call_log WHERE frame_id=?", (child,)
            ).fetchone()[0]
            == 0
        )
        assert (
            db.execute(
                "SELECT COUNT(*) FROM lineage_edges WHERE edge_id='edge-delete'"
            ).fetchone()[0]
            == 0
        )
        assert (
            db.execute(
                "SELECT COUNT(*) FROM capability_states WHERE scope_id=?", (deleted,)
            ).fetchone()[0]
            == 0
        )
        assert (
            db.execute(
                "SELECT COUNT(*) FROM capability_manifests WHERE session_id=?",
                (deleted,),
            ).fetchone()[0]
            == 0
        )


def test_session_delete_explicitly_cleans_delivery_observations_and_env_refs(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    deleted = store.new_frame(project_id="science", status="ready")
    kept = store.new_frame(project_id="science", status="ready")
    versions_dir = tmp_path / "artifact-versions"
    versions_dir.mkdir()
    deleted_bytes = b"same"
    kept_bytes = b"kept"
    deleted_path = tmp_path / "deleted.txt"
    kept_path = tmp_path / "kept.txt"
    deleted_snapshot = versions_dir / "deleted-snapshot.txt"
    kept_snapshot = versions_dir / "kept-snapshot.txt"
    deleted_path.write_bytes(deleted_bytes)
    kept_path.write_bytes(kept_bytes)
    deleted_snapshot.write_bytes(deleted_bytes)
    kept_snapshot.write_bytes(kept_bytes)
    deleted_checksum = hashlib.sha256(deleted_bytes).hexdigest()
    kept_checksum = hashlib.sha256(kept_bytes).hexdigest()
    shared_env = store.upsert_env_snapshot(
        {
            "kind": "python",
            "generation_id": "generation-shared",
            "packages": [],
            "package_count": 0,
        }
    )
    observed_env = store.upsert_env_snapshot(
        {
            "kind": "python",
            "generation_id": "generation-deleted-observation",
            "packages": [],
            "package_count": 0,
        }
    )
    deleted_head = store.record_cell_artifact(
        path=str(deleted_path),
        filename="deleted.txt",
        content_type="text/plain",
        size_bytes=len(deleted_bytes),
        checksum=deleted_checksum,
        producing_cell_id="cell-deleted-1",
        frame_id=deleted,
        env_snapshot_id=shared_env,
        snapshot_path=str(deleted_snapshot),
        reuse_matching_head=True,
    )
    store.record_cell_artifact(
        path=str(deleted_path),
        filename="deleted.txt",
        content_type="text/plain",
        size_bytes=len(deleted_bytes),
        checksum=deleted_checksum,
        producing_cell_id="cell-deleted-2",
        frame_id=deleted,
        env_snapshot_id=observed_env,
        snapshot_path=str(deleted_snapshot),
        reuse_matching_head=True,
    )
    kept_head = store.record_cell_artifact(
        path=str(kept_path),
        filename="kept.txt",
        content_type="text/plain",
        size_bytes=len(kept_bytes),
        checksum=kept_checksum,
        producing_cell_id="cell-kept",
        frame_id=kept,
        env_snapshot_id=shared_env,
        snapshot_path=str(kept_snapshot),
        reuse_matching_head=True,
    )
    delivery = CompletionDeliveryService(store=store, data_dir=tmp_path)
    deleted_delivery = store.commit_completion_delivery(
        idempotency_key="deleted-completion",
        root_frame_id=deleted,
        branch_id=deleted,
        frame_id=deleted,
        content="Delivered deleted.txt",
        manifest=delivery.build_manifest(
            root_frame_id=deleted,
            project_id="science",
            versions=[deleted_head],
        ).value,
    )
    kept_delivery = store.commit_completion_delivery(
        idempotency_key="kept-completion",
        root_frame_id=kept,
        branch_id=kept,
        frame_id=kept,
        content="Delivered kept.txt",
        manifest=delivery.build_manifest(
            root_frame_id=kept,
            project_id="science",
            versions=[kept_head],
        ).value,
    )

    result = store.delete_frame(deleted)

    assert result["deleted"] is True
    assert result["deleted_rows"]["artifact_capture_observations"] == 2
    assert result["deleted_rows"]["completion_delivery_artifacts"] == 1
    assert result["deleted_rows"]["completion_deliveries"] == 1
    assert (
        store.list_artifact_capture_observations(
            artifact_id=deleted_head["artifact_id"]
        )
        == []
    )
    assert (
        len(
            store.list_artifact_capture_observations(
                artifact_id=kept_head["artifact_id"]
            )
        )
        == 1
    )
    assert store.get_completion_delivery(deleted_delivery["delivery_id"]) is None
    assert store.get_completion_delivery(kept_delivery["delivery_id"]) is not None
    assert store.get_env_snapshot(observed_env) is None
    assert store.get_env_snapshot(shared_env) is not None

    store.delete_frame(kept)
    assert store.get_env_snapshot(shared_env) is None
    store.close()


def test_workspace_cas_public_writes_share_gc_lock_and_reject_released_blob(tmp_path):
    cas = WorkspaceCAS(tmp_path / "cas")
    blob = cas.put_blob(b"candidate")
    tree = cas.put_tree([{"path": "value.bin", "blob": blob, "size": 9, "mode": 0o600}])
    started = threading.Event()

    def write_while_locked():
        started.set()
        return cas.put_blob(b"concurrent")

    with ThreadPoolExecutor(max_workers=1) as pool:
        with cas._lock:
            future = pool.submit(write_while_locked)
            assert started.wait(1)
            assert not future.done()
        assert cas.get_blob(future.result()) == b"concurrent"

    assert cas.release_trees([tree["tree_id"]])["blobs"] == 1
    with pytest.raises(ValueError, match="blob does not exist"):
        cas.put_tree([{"path": "stale.bin", "blob": blob, "size": 9, "mode": 0o600}])


def test_cas_gc_waits_for_checkpoint_reference_publication(tmp_path):
    cfg = Config(data_dir=tmp_path)
    store = get_store(cfg.db_path)
    deleted = store.new_frame(project_id="default", status="ready")
    kept = store.new_frame(project_id="default", status="ready")
    cas = WorkspaceCAS(tmp_path / "workspace-cas")
    blob = cas.put_blob(b"shared-in-flight")
    tree = cas.put_tree(
        [{"path": "shared.txt", "blob": blob, "size": 16, "mode": 0o600}]
    )
    store.create_session_checkpoint(
        root_frame_id=deleted,
        reason="candidate",
        workspace_tree_id=tree["tree_id"],
    )

    db_deleted = threading.Event()
    original_delete = store.delete_frame

    def observed_delete(root_frame_id):
        result = original_delete(root_frame_id)
        db_deleted.set()
        return result

    store.delete_frame = observed_delete
    service = SessionDeletionService(
        store,
        data_dir=tmp_path,
        cas=cas,
        drop_runtime=lambda _root, _reason: None,
        drop_resume_window=lambda _root: None,
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        with cas.locked():
            deletion = pool.submit(service.delete_session, deleted)
            assert db_deleted.wait(2)
            assert not deletion.done()
            # This is the production capture -> checkpoint publication lock
            # boundary. GC must refresh retained IDs after acquiring it.
            store.create_session_checkpoint(
                root_frame_id=kept,
                reason="published while deletion waits",
                workspace_tree_id=tree["tree_id"],
            )
        assert deletion.result(timeout=2)["ok"] is True

    assert cas.get_tree(tree["tree_id"])["tree_id"] == tree["tree_id"]
    assert cas.get_blob(blob) == b"shared-in-flight"


def test_snapshot_cleanup_never_follows_symlink_target(tmp_path):
    cas = WorkspaceCAS(tmp_path / "workspace-cas")
    service = SessionDeletionService(
        get_store(Config(data_dir=tmp_path).db_path),
        data_dir=tmp_path,
        cas=cas,
        drop_runtime=lambda _root, _reason: None,
        drop_resume_window=lambda _root: None,
    )
    versions = tmp_path / "artifact-versions"
    versions.mkdir()
    target = versions / "kept.bin"
    target.write_bytes(b"keep")
    candidate = versions / "candidate.bin"
    candidate.symlink_to(target)

    assert (
        service._unlink_owned_file(
            str(candidate),
            (versions,),
            retained_paths=set(),
            retained_files=set(),
        )
        is False
    )
    assert candidate.is_symlink()
    assert target.read_bytes() == b"keep"


def test_project_delete_removes_only_its_dynamic_scope(tmp_path):
    cfg = Config(data_dir=tmp_path)
    store = get_store(cfg.db_path)
    store.create_project(project_id="science", name="Science")
    root = store.new_frame(project_id="science", status="ready")
    scopes = DynamicScopeStore(tmp_path / "dynamic-tools" / "_scoped")
    project_manifest = "dyn-" + ("a" * 64)
    global_manifest = "dyn-" + ("b" * 64)
    scopes.write_manifest(
        {
            "manifest_id": project_manifest,
            "scope": "project",
            "scope_id": "science",
        }
    )
    scopes.write_manifest(
        {"manifest_id": global_manifest, "scope": "global", "scope_id": ""}
    )
    scopes.append_activation(
        operation="promote",
        scope="project",
        scope_id="science",
        name="project_tool",
        manifest_id=project_manifest,
        actor_root_frame_id=root,
        actor_project_id="science",
    )
    scopes.append_activation(
        operation="promote",
        scope="global",
        scope_id="",
        name="global_tool",
        manifest_id=global_manifest,
        actor_root_frame_id=root,
        actor_project_id="science",
    )
    service = SessionDeletionService(
        store,
        data_dir=tmp_path,
        cas=WorkspaceCAS(tmp_path / "workspace-cas"),
        drop_runtime=lambda _root, _reason: None,
        drop_resume_window=lambda _root: None,
    )

    result = service.delete_project("science")

    assert result["freed_dynamic_events"] == 1
    assert result["freed_dynamic_manifests"] == 1
    assert scopes.events(scope="project", scope_id="science") == ([], [])
    assert len(scopes.events(scope="global", scope_id="")[0]) == 1
    records, errors = scopes.manifest_records()
    assert errors == []
    assert [record["manifest_id"] for record in records] == [global_manifest]


def test_feedback_delete_escapes_like_metacharacters(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    with store._lock:
        store._conn.executemany(
            "INSERT INTO frames(frame_id,project_id,root_frame_id,kind,status,"
            "created_at,updated_at) VALUES(?,'default',?,'turn','ready',1,1)",
            [("f-percent%", "f-percent%"), ("f-percentX", "f-percentX")],
        )
        store._conn.executemany(
            "INSERT INTO settings(key,value,updated_at) VALUES(?,?,1)",
            [("fb:f-percent%:vote", "up"), ("fb:f-percentX:vote", "down")],
        )
        store._conn.commit()

    store.delete_frame("f-percent%")

    assert store.get_setting("fb:f-percent%:vote") is None
    assert store.get_setting("fb:f-percentX:vote") == "down"


def _admitted_pin(store, root, reservation):
    """One reserved pin plus the ledger row that correlates it."""
    annotation = store.add_annotation(
        root_frame_id=root,
        artifact_id="a-del",
        artifact_name="p.png",
        rel_x=0.5,
        rel_y=0.5,
        body="pin",
    )
    store.reserve_with_admission(
        reservation_id=reservation,
        root_frame_id=root,
        annotation_ids=[annotation["annotation_id"]],
    )
    return annotation["annotation_id"]


def test_deleting_a_session_takes_its_admission_ledger_with_it(tmp_path):
    """The correlation record outliving everything it correlates.

    `annotation_admissions` was not in the deletion aggregate. Deleting a
    session removed its frames and its annotations and kept every ledger row
    naming their root, annotation, request and job ids -- a record of what a
    user commented on, in a session they deleted. Root-scoped, and only the
    deleted root's.
    """
    cfg = Config(data_dir=tmp_path)
    store = get_store(cfg.db_path)
    store.create_project(project_id="science", name="Science")
    deleted = store.new_frame(project_id="science", status="ready")
    kept = store.new_frame(project_id="science", status="ready")
    _admitted_pin(store, deleted, "resv-deleted-session-0001")
    _admitted_pin(store, kept, "resv-kept-session-000001")

    service = SessionDeletionService(
        store,
        data_dir=tmp_path,
        cas=WorkspaceCAS(tmp_path / "workspace-cas"),
        drop_runtime=lambda root, reason: None,
        drop_resume_window=lambda root: None,
    )
    assert service.delete_session(deleted)["ok"] is True

    assert store.get_admission("resv-deleted-session-0001") is None
    assert store.get_admission("resv-kept-session-000001") is not None
    store.close()


def _orphan_admission(store, frame, reservation):
    """A ledger row naming a MEMBER frame as its root.

    The routes now refuse to admit against a child, so this shape can only
    arrive from a database written before that guard existed -- which is
    exactly the row a root-scoped cascade walks straight past.
    """
    store.record_admission(
        reservation_id=reservation,
        root_frame_id=frame,
        annotation_ids=[],
        request_id="req-orphan",
        job_id="job-orphan",
    )


def test_deletion_also_sweeps_an_admission_that_names_a_member_frame(tmp_path):
    """Root-scoped alone was not enough. A row naming a child survived the
    cascade, and a client holding that child id plus the reservation could
    still be answered about a Session that no longer exists."""
    cfg = Config(data_dir=tmp_path)
    store = get_store(cfg.db_path)
    store.create_project(project_id="science", name="Science")
    deleted = store.new_frame(project_id="science", status="ready")
    child = store.new_frame(parent_id=deleted, kind="delegate")
    kept = store.new_frame(project_id="science", status="ready")
    _orphan_admission(store, child, "resv-orphan-on-child-001")
    _orphan_admission(store, kept, "resv-orphan-elsewhere-01")

    service = SessionDeletionService(
        store,
        data_dir=tmp_path,
        cas=WorkspaceCAS(tmp_path / "workspace-cas"),
        drop_runtime=lambda root, reason: None,
        drop_resume_window=lambda root: None,
    )
    assert service.delete_session(deleted)["ok"] is True

    assert store.get_admission("resv-orphan-on-child-001") is None
    assert store.get_admission("resv-orphan-elsewhere-01") is not None
    store.close()


def test_the_project_cascade_sweeps_member_frame_admissions_too(tmp_path):
    """The project path collects its own frame ids, so it is a separate
    walk and a separate assertion."""
    cfg = Config(data_dir=tmp_path)
    store = get_store(cfg.db_path)
    store.create_project(project_id="doomed", name="Doomed")
    store.create_project(project_id="survivor", name="Survivor")
    root = store.new_frame(project_id="doomed", status="ready")
    child = store.new_frame(parent_id=root, kind="delegate")
    elsewhere = store.new_frame(project_id="survivor", status="ready")
    _orphan_admission(store, child, "resv-proj-orphan-child01")
    _orphan_admission(store, elsewhere, "resv-proj-orphan-safe01")

    service = SessionDeletionService(
        store,
        data_dir=tmp_path,
        cas=WorkspaceCAS(tmp_path / "workspace-cas"),
        drop_runtime=lambda root_, reason: None,
        drop_resume_window=lambda root_: None,
    )
    assert service.delete_project("doomed")["ok"] is True

    assert store.get_admission("resv-proj-orphan-child01") is None
    assert store.get_admission("resv-proj-orphan-safe01") is not None
    store.close()


def test_deleting_a_project_takes_every_admission_ledger_in_it(tmp_path):
    """The project cascade reaches the same table by the same root scope, but
    over a set of roots -- a separate code path in `_delete_aggregate_locked`
    and so a separate assertion."""
    cfg = Config(data_dir=tmp_path)
    store = get_store(cfg.db_path)
    store.create_project(project_id="doomed", name="Doomed")
    store.create_project(project_id="survivor", name="Survivor")
    first = store.new_frame(project_id="doomed", status="ready")
    second = store.new_frame(project_id="doomed", status="ready")
    elsewhere = store.new_frame(project_id="survivor", status="ready")
    _admitted_pin(store, first, "resv-doomed-first-00001")
    _admitted_pin(store, second, "resv-doomed-second-0001")
    _admitted_pin(store, elsewhere, "resv-survivor-000000001")

    service = SessionDeletionService(
        store,
        data_dir=tmp_path,
        cas=WorkspaceCAS(tmp_path / "workspace-cas"),
        drop_runtime=lambda root, reason: None,
        drop_resume_window=lambda root: None,
    )
    assert service.delete_project("doomed")["ok"] is True

    assert store.get_admission("resv-doomed-first-00001") is None
    assert store.get_admission("resv-doomed-second-0001") is None
    assert store.get_admission("resv-survivor-000000001") is not None
    store.close()
