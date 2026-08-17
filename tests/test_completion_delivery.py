"""Exact-version manifests and atomic/recoverable completion publication."""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import sqlite3
import threading
from pathlib import Path

import pytest

from openai4s.config import Config
from openai4s.server.delivery import (
    CompletionDeliveryService,
    DeliveryValidationError,
)
from openai4s.server.urls import artifact_version_url, completion_artifact_url
from openai4s.storage.artifacts import ArtifactDeliveryReferenceError
from openai4s.storage.delivery import (
    COMPLETION_DELIVERY_SCHEMA,
    CompletionDeliveryRepository,
    DeliveryConflictError,
    canonical_json,
)
from openai4s.store import get_store

_MESSAGE_SCHEMA = """
CREATE TABLE messages (
    message_id TEXT PRIMARY KEY,
    root_frame_id TEXT NOT NULL,
    branch_id TEXT,
    frame_id TEXT,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    metadata TEXT,
    created_at INTEGER NOT NULL
);
CREATE TABLE artifacts (
    artifact_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    root_frame_id TEXT
);
CREATE TABLE artifact_versions (
    version_id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL,
    size_bytes INTEGER,
    checksum TEXT,
    snapshot_path TEXT
);
"""


class _ArtifactStore:
    def __init__(self, *, version: dict, artifact: dict) -> None:
        self.version = version
        self.artifact = artifact

    def version_meta(self, version_id: str) -> dict | None:
        return dict(self.version) if version_id == self.version["version_id"] else None

    def get_artifact(self, artifact_id: str) -> dict | None:
        return (
            dict(self.artifact) if artifact_id == self.artifact["artifact_id"] else None
        )


def _service(tmp_path, *, content=b"measured-data", **version_overrides):
    snapshots = tmp_path / "artifact-versions"
    snapshots.mkdir(parents=True)
    snapshot = snapshots / "v.bin"
    snapshot.write_bytes(content)
    version = {
        "version_id": "version/β 1?#",
        "artifact_id": "artifact-1",
        "filename": "result.csv",
        "content_type": "text/csv",
        "size_bytes": len(content),
        "checksum": hashlib.sha256(content).hexdigest(),
        "snapshot_path": str(snapshot),
        "producing_cell_id": "cell-7",
        "frame_id": "root-1",
    }
    version.update(version_overrides)
    artifact = {
        "artifact_id": "artifact-1",
        "root_frame_id": "root-1",
        "project_id": "project-1",
        "filename": "result.csv",
        "content_type": "text/csv",
    }
    service = CompletionDeliveryService(
        store=_ArtifactStore(version=version, artifact=artifact),
        data_dir=tmp_path,
    )
    return service, snapshot, version, artifact


def _repository():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(_MESSAGE_SCHEMA)
    connection.executescript(COMPLETION_DELIVERY_SCHEMA)
    connection.execute(
        "INSERT INTO artifacts(artifact_id,project_id,root_frame_id) VALUES(?,?,?)",
        ("artifact-1", "project-1", "root-1"),
    )
    connection.execute(
        "INSERT INTO artifact_versions(version_id,artifact_id,size_bytes,checksum,"
        "snapshot_path) VALUES(?,?,?,?,?)",
        ("version-1", "artifact-1", 4, "a" * 64, "/trusted/version-1"),
    )
    connection.commit()
    ticks = itertools.count(1000)
    identifiers = itertools.count(1)
    repository = CompletionDeliveryRepository(
        connection,
        threading.RLock(),
        clock_ms=lambda: next(ticks),
        id_factory=lambda prefix: f"{prefix}-{next(identifiers)}",
    )
    return connection, repository


def _manifest():
    return {
        "schema_version": 1,
        "root_frame_id": "root-1",
        "project_id": "project-1",
        "artifacts": [
            {
                "artifact_id": "artifact-1",
                "version_id": "version-1",
                "filename": "result.csv",
                "size_bytes": 4,
                "sha256": "a" * 64,
                "url": "/api/v1/artifacts/versions/version-1",
            }
        ],
    }


def test_url_helper_preserves_flag_off_and_encodes_one_exact_version_segment():
    assert (
        completion_artifact_url(artifact_id="artifact/legacy", trusted_delivery=False)
        == "/api/artifacts/artifact%2Flegacy"
    )
    assert artifact_version_url("version/报告 ?#") == (
        "/api/v1/artifacts/versions/version%2F%E6%8A%A5%E5%91%8A%20%3F%23"
    )
    with pytest.raises(ValueError, match="dot path segment"):
        artifact_version_url("..")
    assert (
        completion_artifact_url(
            artifact_id="mutable", version_id=None, trusted_delivery=True
        )
        is None
    )
    with pytest.raises(ValueError, match="version_id"):
        artifact_version_url("")


def test_manifest_verifies_scope_snapshot_hash_size_and_omits_local_path(tmp_path):
    service, _snapshot, version, _artifact = _service(tmp_path)

    manifest = service.build_manifest(
        root_frame_id="root-1",
        project_id="project-1",
        versions=[{"latest_version_id": version["version_id"]}],
    )

    assert (
        manifest.sha256
        == hashlib.sha256(canonical_json(manifest.value).encode("utf-8")).hexdigest()
    )
    assert manifest.value["artifacts"] == [
        {
            "artifact_id": "artifact-1",
            "version_id": "version/β 1?#",
            "filename": "result.csv",
            "content_type": "text/csv",
            "size_bytes": 13,
            "sha256": hashlib.sha256(b"measured-data").hexdigest(),
            "url": "/api/v1/artifacts/versions/version%2F%CE%B2%201%3F%23",
        }
    ]
    # A reused byte version can have several producing Cells.  The delivery
    # manifest binds bytes and URL; producer truth lives in capture observations.
    assert "producing_cell_id" not in canonical_json(manifest.value)
    assert "snapshot_path" not in canonical_json(manifest.value)


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"root": "other-root"}, "different session"),
        ({"project": "other-project"}, "different project"),
        ({"version": {"snapshot_path": None}}, "immutable snapshot"),
        ({"version": {"checksum": "not-a-sha"}}, "valid recorded checksum"),
        ({"version": {"size_bytes": None}}, "valid recorded size"),
    ],
)
def test_manifest_scope_and_metadata_fail_closed(tmp_path, change, error):
    service, _snapshot, version, artifact = _service(
        tmp_path, **change.get("version", {})
    )
    root = change.get("root", "root-1")
    project = change.get("project", "project-1")

    with pytest.raises(DeliveryValidationError, match=error):
        service.build_manifest(
            root_frame_id=root,
            project_id=project,
            versions=[version["version_id"]],
        )

    # The fixture itself remains owned by the intended scope; the mismatch is
    # in the requested delivery boundary, not fabricated in the database row.
    assert artifact["root_frame_id"] == "root-1"


def test_manifest_rejects_missing_tampered_and_untrusted_snapshot(tmp_path):
    service, snapshot, version, _artifact = _service(tmp_path)
    snapshot.write_bytes(b"tampered")
    with pytest.raises(DeliveryValidationError, match="size verification failed"):
        service.build_manifest(
            root_frame_id="root-1",
            project_id="project-1",
            versions=[version["version_id"]],
        )

    snapshot.write_bytes(b"x" * version["size_bytes"])
    with pytest.raises(DeliveryValidationError, match="checksum verification failed"):
        service.build_manifest(
            root_frame_id="root-1",
            project_id="project-1",
            versions=[version["version_id"]],
        )

    outside = tmp_path.parent / f"{tmp_path.name}-outside.bin"
    outside.write_bytes(b"measured-data")
    untrusted, _, external_version, _ = _service(
        tmp_path / "other",
        snapshot_path=str(outside),
    )
    with pytest.raises(DeliveryValidationError, match="outside trusted storage"):
        untrusted.build_manifest(
            root_frame_id="root-1",
            project_id="project-1",
            versions=[external_version["version_id"]],
        )

    with pytest.raises(DeliveryValidationError, match="unavailable"):
        service.build_manifest(
            root_frame_id="root-1",
            project_id="project-1",
            versions=["missing-version"],
        )


def test_manifest_rejects_mid_read_rewrite_with_restored_mtime(tmp_path, monkeypatch):
    original = b"A" * (1024 * 1024 + 4096)
    replacement = b"B" * len(original)
    service, snapshot, version, _artifact = _service(tmp_path, content=original)
    snapshot_stat = snapshot.stat()
    snapshot_identity = (snapshot_stat.st_dev, snapshot_stat.st_ino)
    native_read = os.read
    mutated = False

    def rewrite_after_first_snapshot_read(descriptor, size):
        nonlocal mutated
        chunk = native_read(descriptor, size)
        descriptor_stat = os.fstat(descriptor)
        if (
            chunk
            and not mutated
            and (descriptor_stat.st_dev, descriptor_stat.st_ino) == snapshot_identity
        ):
            mutated = True
            with snapshot.open("r+b", buffering=0) as stream:
                stream.write(replacement)
                os.fsync(stream.fileno())
            os.utime(
                snapshot,
                ns=(snapshot_stat.st_atime_ns, snapshot_stat.st_mtime_ns),
            )
        return chunk

    monkeypatch.setattr(
        "openai4s.server.delivery.os.read", rewrite_after_first_snapshot_read
    )

    with pytest.raises(DeliveryValidationError, match="changed during verification"):
        service.build_manifest(
            root_frame_id="root-1",
            project_id="project-1",
            versions=[version["version_id"]],
        )

    assert mutated is True
    assert snapshot.stat().st_size == len(original)
    assert snapshot.stat().st_mtime_ns == snapshot_stat.st_mtime_ns


def test_final_message_and_delivery_commit_are_atomic_and_idempotent():
    connection, repository = _repository()

    first = repository.commit_final_message(
        idempotency_key="turn-1:completion",
        root_frame_id="root-1",
        branch_id="branch-1",
        frame_id="root-1",
        content="Delivered [result](/api/v1/artifacts/versions/version-1).",
        manifest=_manifest(),
    )
    again = repository.commit_final_message(
        idempotency_key="turn-1:completion",
        root_frame_id="root-1",
        branch_id="branch-1",
        frame_id="root-1",
        content="Delivered [result](/api/v1/artifacts/versions/version-1).",
        manifest=_manifest(),
    )

    assert again["delivery_id"] == first["delivery_id"]
    assert first["status"] == "committed"
    assert first["message_role"] == "assistant"
    assert first["message_metadata"]["completion_delivery"]["status"] == ("committed")
    assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    assert (
        connection.execute("SELECT COUNT(*) FROM completion_deliveries").fetchone()[0]
        == 1
    )
    relation = connection.execute(
        "SELECT artifact_id,version_id,size_bytes,sha256 "
        "FROM completion_delivery_artifacts"
    ).fetchone()
    assert tuple(relation) == ("artifact-1", "version-1", 4, "a" * 64)
    with pytest.raises(DeliveryConflictError, match="different content"):
        repository.commit_final_message(
            idempotency_key="turn-1:completion",
            root_frame_id="root-1",
            branch_id="branch-1",
            frame_id="root-1",
            content="A different claim.",
            manifest=_manifest(),
        )
    assert connection.in_transaction is False
    assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1


def test_delivery_binds_exact_version_until_owning_message_is_deleted():
    connection, repository = _repository()
    committed = repository.commit_final_message(
        idempotency_key="turn-pinned:completion",
        root_frame_id="root-1",
        branch_id="branch-1",
        frame_id="root-1",
        content="Exact bytes stay addressable.",
        manifest=_manifest(),
    )

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        connection.execute("DELETE FROM artifact_versions WHERE version_id='version-1'")
    connection.rollback()

    connection.execute(
        "DELETE FROM messages WHERE message_id=?",
        (committed["message_id"],),
    )
    connection.commit()
    assert repository.get(committed["delivery_id"]) is None
    connection.execute("DELETE FROM artifact_versions WHERE version_id='version-1'")
    connection.commit()


def test_delivery_rejects_a_manifest_changed_after_service_verification():
    _connection, repository = _repository()

    with pytest.raises(ValueError, match="manifest hash changed"):
        repository.commit_final_message(
            idempotency_key="turn-hash:completion",
            root_frame_id="root-1",
            branch_id="branch-1",
            frame_id="root-1",
            content="Never committed.",
            manifest=_manifest(),
            expected_manifest_sha256="0" * 64,
        )


def test_delivery_insert_fault_rolls_back_the_final_message():
    connection, repository = _repository()
    connection.execute(
        "CREATE TRIGGER fail_delivery BEFORE INSERT ON completion_deliveries "
        "BEGIN SELECT RAISE(ABORT, 'injected delivery fault'); END"
    )

    with pytest.raises(sqlite3.IntegrityError, match="injected delivery fault"):
        repository.commit_final_message(
            idempotency_key="turn-2:completion",
            root_frame_id="root-1",
            branch_id="branch-1",
            frame_id="root-1",
            content="This must not survive.",
            manifest=_manifest(),
        )

    assert connection.in_transaction is False
    assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    assert (
        connection.execute("SELECT COUNT(*) FROM completion_deliveries").fetchone()[0]
        == 0
    )


def test_import_binding_atomically_replaces_source_message_relation_and_urls():
    connection, repository = _repository()
    source_metadata = {
        "keep": {"imported": True},
        "completion_delivery_import_pending": True,
    }
    connection.execute(
        "INSERT INTO messages(message_id,root_frame_id,branch_id,frame_id,seq,role,"
        "content,metadata,created_at) VALUES(?,?,?,?,?,'assistant',?,?,?)",
        (
            "imported-message",
            "root-1",
            "branch-1",
            "root-1",
            0,
            "Source /api/v1/artifacts/versions/source-version",
            json.dumps(source_metadata),
            800,
        ),
    )
    connection.commit()

    imported = repository.bind_imported_message(
        idempotency_key="package:source-delivery",
        message_id="imported-message",
        root_frame_id="root-1",
        branch_id="branch-1",
        frame_id="root-1",
        expected_current_content=("Source /api/v1/artifacts/versions/source-version"),
        content="Local /api/v1/artifacts/versions/version-1",
        manifest=_manifest(),
        status="published",
        created_at=800,
        published_at=900,
    )

    assert imported["delivery_id"] != "source-delivery"
    assert imported["message_content"].endswith("/version-1")
    assert imported["message_metadata"]["keep"] == {"imported": True}
    assert imported["message_metadata"]["completion_delivery"] == {
        "delivery_id": imported["delivery_id"],
        "manifest_sha256": imported["manifest_sha256"],
        "published_at": 900,
        "status": "published",
    }
    assert [row["delivery_id"] for row in repository.for_session("root-1")] == [
        imported["delivery_id"]
    ]


def test_import_binding_fault_rolls_back_message_update_and_ledger():
    connection, repository = _repository()
    source_metadata = {"completion_delivery_import_pending": True}
    source_content = "Source /api/v1/artifacts/versions/source-version"
    connection.execute(
        "INSERT INTO messages(message_id,root_frame_id,branch_id,frame_id,seq,role,"
        "content,metadata,created_at) VALUES(?,?,?,?,?,'assistant',?,?,?)",
        (
            "imported-message",
            "root-1",
            "branch-1",
            "root-1",
            0,
            source_content,
            json.dumps(source_metadata, sort_keys=True),
            800,
        ),
    )
    connection.execute(
        "CREATE TRIGGER fail_import_delivery BEFORE INSERT ON completion_deliveries "
        "BEGIN SELECT RAISE(ABORT, 'injected import delivery fault'); END"
    )
    connection.commit()

    with pytest.raises(sqlite3.IntegrityError, match="injected import delivery fault"):
        repository.bind_imported_message(
            idempotency_key="package:source-delivery",
            message_id="imported-message",
            root_frame_id="root-1",
            branch_id="branch-1",
            frame_id="root-1",
            expected_current_content=source_content,
            content="Local /api/v1/artifacts/versions/version-1",
            manifest=_manifest(),
            status="committed",
            created_at=800,
        )

    row = connection.execute(
        "SELECT content,metadata FROM messages WHERE message_id='imported-message'"
    ).fetchone()
    assert row["content"] == source_content
    assert json.loads(row["metadata"]) == source_metadata
    assert connection.in_transaction is False
    assert (
        connection.execute("SELECT COUNT(*) FROM completion_deliveries").fetchone()[0]
        == 0
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "DELETE FROM artifact_versions WHERE version_id='version-1'",
        "UPDATE artifact_versions SET checksum='bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' WHERE version_id='version-1'",
        "UPDATE artifacts SET project_id='other-project' WHERE artifact_id='artifact-1'",
    ],
)
def test_delivery_commit_rechecks_exact_version_visibility_inside_transaction(mutation):
    connection, repository = _repository()
    connection.execute(mutation)
    connection.commit()

    with pytest.raises(RuntimeError, match="changed before commit"):
        repository.commit_final_message(
            idempotency_key="turn-race:completion",
            root_frame_id="root-1",
            branch_id="branch-1",
            frame_id="root-1",
            content="This claim must not become visible.",
            manifest=_manifest(),
        )

    assert connection.in_transaction is False
    assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    assert (
        connection.execute("SELECT COUNT(*) FROM completion_deliveries").fetchone()[0]
        == 0
    )


def test_publication_is_write_once_and_committed_rows_are_recoverable():
    _connection, repository = _repository()
    committed = repository.commit_final_message(
        idempotency_key="turn-3:completion",
        root_frame_id="root-1",
        branch_id="branch-1",
        frame_id="root-1",
        content="Durable before publication.",
        manifest=_manifest(),
    )
    assert [row["delivery_id"] for row in repository.committed()] == [
        committed["delivery_id"]
    ]

    published = repository.mark_published(committed["delivery_id"], published_at=2000)
    repeated = repository.mark_published(committed["delivery_id"], published_at=9999)

    assert published["status"] == "published"
    assert published["published_at"] == 2000
    assert published["message_metadata"]["completion_delivery"] == {
        "delivery_id": committed["delivery_id"],
        "manifest_sha256": committed["manifest_sha256"],
        "published_at": 2000,
        "status": "published",
    }
    assert repeated["published_at"] == 2000
    assert repository.committed() == []


def test_recovery_fails_closed_if_durable_manifest_hash_no_longer_matches():
    connection, repository = _repository()
    committed = repository.commit_final_message(
        idempotency_key="turn-4:completion",
        root_frame_id="root-1",
        branch_id="branch-1",
        frame_id="root-1",
        content="Verified before corruption.",
        manifest=_manifest(),
    )
    connection.execute(
        "UPDATE completion_deliveries SET manifest_json='{}' WHERE delivery_id=?",
        (committed["delivery_id"],),
    )
    connection.commit()

    with pytest.raises(RuntimeError, match="manifest hash mismatch"):
        repository.get(committed["delivery_id"])


def test_idempotent_retry_fails_closed_if_version_relation_was_lost():
    connection, repository = _repository()
    committed = repository.commit_final_message(
        idempotency_key="turn-relation:completion",
        root_frame_id="root-1",
        branch_id="branch-1",
        frame_id="root-1",
        content="Durable exact relation.",
        manifest=_manifest(),
    )
    connection.execute(
        "DELETE FROM completion_delivery_artifacts WHERE delivery_id=?",
        (committed["delivery_id"],),
    )
    connection.commit()

    with pytest.raises(RuntimeError, match="Artifact relation mismatch"):
        repository.commit_final_message(
            idempotency_key="turn-relation:completion",
            root_frame_id="root-1",
            branch_id="branch-1",
            frame_id="root-1",
            content="Durable exact relation.",
            manifest=_manifest(),
        )
    assert connection.in_transaction is False
    assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1


def test_store_delivery_facade_commits_recovers_and_marks_published(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    root = store.new_frame(project_id="project-1", status="ready")
    snapshot = tmp_path / "artifact-versions" / "version-1.bin"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_bytes(b"aaaa")
    checksum = hashlib.sha256(b"aaaa").hexdigest()
    artifact = store.save_artifact(
        path=str(snapshot),
        filename="result.csv",
        content_type="text/csv",
        size_bytes=4,
        checksum=checksum,
        frame_id=root,
        project_id="project-1",
        snapshot_path=str(snapshot),
    )
    manifest = (
        CompletionDeliveryService(
            store=store,
            data_dir=tmp_path,
        )
        .build_manifest(
            root_frame_id=root,
            project_id="project-1",
            versions=[artifact["version_id"]],
        )
        .value
    )

    committed = store.commit_completion_delivery(
        idempotency_key="store-turn:completion",
        root_frame_id=root,
        branch_id=root,
        frame_id=root,
        content="Durable Store delivery.",
        manifest=manifest,
    )

    assert [
        row["delivery_id"]
        for row in store.committed_completion_deliveries(
            root_frame_id=root,
            branch_id=root,
        )
    ] == [committed["delivery_id"]]
    assert store.get_completion_delivery(committed["delivery_id"])["status"] == (
        "committed"
    )
    published = store.mark_completion_delivery_published(
        committed["delivery_id"],
        published_at=int(committed["created_at"]) + 1,
    )
    assert published["status"] == "published"
    assert published["published_at"] == int(committed["created_at"]) + 1
    assert store.committed_completion_deliveries(root_frame_id=root) == []
    with pytest.raises(ArtifactDeliveryReferenceError, match="completion message"):
        store.delete_artifact(artifact["artifact_id"])
    store.close()


@pytest.mark.parametrize(
    "corrupt",
    [
        "manifest",
        "message_role",
        "delivery_scope",
        "committed_publication",
        "orphan_message",
    ],
)
def test_store_reopen_refuses_a_corrupt_completion_delivery(tmp_path, corrupt):
    """A durable link is revalidated before any conversation projects it."""

    store = get_store(Config(data_dir=tmp_path).db_path)
    root = store.new_frame(project_id="project-1", status="ready")
    snapshot = tmp_path / "artifact-versions" / "version-1.bin"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_bytes(b"aaaa")
    checksum = hashlib.sha256(b"aaaa").hexdigest()
    artifact = store.save_artifact(
        path=str(snapshot),
        filename="result.csv",
        content_type="text/csv",
        size_bytes=4,
        checksum=checksum,
        frame_id=root,
        project_id="project-1",
        snapshot_path=str(snapshot),
    )
    verified = CompletionDeliveryService(store=store, data_dir=tmp_path).build_manifest(
        root_frame_id=root,
        project_id="project-1",
        versions=[artifact["version_id"]],
    )
    committed = store.commit_completion_delivery(
        idempotency_key="store-reopen:completion",
        root_frame_id=root,
        branch_id=root,
        frame_id=root,
        content="Durable [result](/api/v1/artifacts/versions/version-1).",
        manifest=verified.value,
        expected_manifest_sha256=verified.sha256,
    )

    assert store.list_branch_message_boundaries(root)[0]["content"].startswith(
        "Durable"
    )
    with store._lock:  # noqa: SLF001 - inject durable corruption at the DB boundary
        if corrupt == "manifest":
            store._conn.execute(  # noqa: SLF001
                "UPDATE completion_deliveries SET manifest_json='{}' "
                "WHERE delivery_id=?",
                (committed["delivery_id"],),
            )
        elif corrupt == "message_role":
            store._conn.execute(  # noqa: SLF001
                "UPDATE messages SET role='user' WHERE message_id=?",
                (committed["message_id"],),
            )
        elif corrupt == "delivery_scope":
            store._conn.execute(  # noqa: SLF001
                "UPDATE completion_deliveries SET branch_id='other-branch' "
                "WHERE delivery_id=?",
                (committed["delivery_id"],),
            )
        elif corrupt == "committed_publication":
            store._conn.execute(  # noqa: SLF001
                "UPDATE completion_deliveries SET published_at=created_at+1 "
                "WHERE delivery_id=?",
                (committed["delivery_id"],),
            )
        else:
            store._conn.execute(  # noqa: SLF001
                "DELETE FROM completion_deliveries WHERE delivery_id=?",
                (committed["delivery_id"],),
            )
        store._conn.commit()  # noqa: SLF001

    with pytest.raises(RuntimeError, match="completion delivery"):
        store.list_branch_message_boundaries(root)
    store.close()


def test_store_reopen_validates_delivery_projection_with_bounded_queries(tmp_path):
    """A long mixed history must not validate its delivery ledger row by row."""

    store = get_store(Config(data_dir=tmp_path).db_path)
    root = store.new_frame(project_id="project-1", status="ready")
    snapshot = tmp_path / "artifact-versions" / "version-1.bin"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_bytes(b"aaaa")
    checksum = hashlib.sha256(b"aaaa").hexdigest()
    artifact = store.save_artifact(
        path=str(snapshot),
        filename="result.csv",
        content_type="text/csv",
        size_bytes=4,
        checksum=checksum,
        frame_id=root,
        project_id="project-1",
        snapshot_path=str(snapshot),
    )
    manifest = CompletionDeliveryService(
        store=store,
        data_dir=tmp_path,
    ).build_manifest(
        root_frame_id=root,
        project_id="project-1",
        versions=[artifact["version_id"]],
    )
    for index in range(64):
        store.add_message(
            root_frame_id=root,
            branch_id=root,
            frame_id=root,
            role="user",
            content=f"ordinary-{index}",
        )
        store.commit_completion_delivery(
            idempotency_key=f"mixed-{index}:completion",
            root_frame_id=root,
            branch_id=root,
            frame_id=root,
            content=f"delivery-{index}",
            manifest=manifest.value,
            expected_manifest_sha256=manifest.sha256,
        )

    statements: list[str] = []
    with store._lock:  # noqa: SLF001 - observe the exact SQLite read contract
        store._conn.set_trace_callback(statements.append)  # noqa: SLF001
    try:
        messages = store.list_branch_messages(root, limit=None)
    finally:
        with store._lock:  # noqa: SLF001
            store._conn.set_trace_callback(None)  # noqa: SLF001

    assert len(messages) == 128
    assert all("message_id" not in message for message in messages)
    delivery_reads = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith("SELECT")
        and (
            "completion_deliveries" in statement
            or "completion_delivery_artifacts" in statement
        )
    ]
    assert len(delivery_reads) == 2, delivery_reads
    store.close()


def test_delivery_projection_batches_past_sql_parameter_boundary():
    """The 401st delivery opens a second bounded query, not an N+1 loop."""

    connection, repository = _repository()
    for index in range(401):
        repository.commit_final_message(
            idempotency_key=f"batch-{index}:completion",
            root_frame_id="root-1",
            branch_id="branch-1",
            frame_id="root-1",
            content=f"delivery-{index}",
            manifest=_manifest(),
        )
    rows = connection.execute(
        "SELECT seq,role,content,metadata,created_at FROM messages "
        "WHERE root_frame_id='root-1' ORDER BY seq"
    ).fetchall()
    messages = []
    for row in rows:
        message = dict(row)
        message["metadata"] = json.loads(message["metadata"])
        messages.append(message)

    statements: list[str] = []
    connection.set_trace_callback(statements.append)
    try:
        validated = repository.validate_message_projection("root-1", messages)
    finally:
        connection.set_trace_callback(None)

    assert validated == messages
    delivery_reads = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith("SELECT")
        and (
            "completion_deliveries" in statement
            or "completion_delivery_artifacts" in statement
        )
    ]
    assert len(delivery_reads) == 4, delivery_reads
