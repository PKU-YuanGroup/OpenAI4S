"""Recoverable publication of Artifact-bearing completion messages.

Artifact snapshots are committed before a turn can describe them.  The final
assistant message and its delivery record then enter SQLite in one transaction.
Only after that commit may the WebSocket projection be emitted; a crash in the
small interval between commit and emission leaves a ``committed`` row and a
durable REST-visible message for explicit recovery rather than prose with no
durable delivery fact.  The Stage 1 ledger does not itself re-emit that socket
projection; the ordinary bounded in-process WebSocket sequence buffer may
still replay it while the turn remains live.

The repository intentionally does not create its own table.  ``Store`` owns
schema installation and migrations; this module exports the DDL so that the
facade can install the same contract for new and upgraded databases.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from collections.abc import Mapping
from typing import Any, Callable

from openai4s.storage.migrations import apply_ddl_script

_SHA256 = re.compile(r"[0-9a-f]{64}")
_SQL_BATCH_SIZE = 400
_CANDIDATE_VERDICT_DIGEST_KEY = "candidate_verdict_metadata_sha256"
_TERMINAL_REVIEW_STATUSES = {
    "verified",
    "completed_with_issues",
    "review_unavailable",
}

COMPLETION_DELIVERY_SCHEMA = """
CREATE TABLE IF NOT EXISTS completion_deliveries (
    delivery_id      TEXT PRIMARY KEY,
    idempotency_key  TEXT NOT NULL,
    root_frame_id    TEXT NOT NULL,
    branch_id        TEXT NOT NULL,
    frame_id         TEXT,
    message_id       TEXT NOT NULL UNIQUE,
    manifest_json    TEXT NOT NULL,
    manifest_sha256  TEXT NOT NULL,
    content_sha256   TEXT NOT NULL,
    status           TEXT NOT NULL
                     CHECK (status IN ('committed','published')),
    created_at       INTEGER NOT NULL,
    published_at     INTEGER,
    UNIQUE (root_frame_id, branch_id, idempotency_key),
    FOREIGN KEY (message_id) REFERENCES messages(message_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_completion_deliveries_pending
    ON completion_deliveries(status, created_at, delivery_id);
CREATE INDEX IF NOT EXISTS ix_completion_deliveries_session
    ON completion_deliveries(root_frame_id, branch_id, created_at, delivery_id);
CREATE TABLE IF NOT EXISTS completion_delivery_artifacts (
    delivery_id      TEXT NOT NULL,
    ordinal          INTEGER NOT NULL,
    artifact_id      TEXT NOT NULL,
    version_id       TEXT NOT NULL,
    size_bytes       INTEGER NOT NULL,
    sha256           TEXT NOT NULL,
    PRIMARY KEY (delivery_id, ordinal),
    UNIQUE (delivery_id, version_id),
    FOREIGN KEY (delivery_id) REFERENCES completion_deliveries(delivery_id)
        ON DELETE CASCADE,
    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (version_id) REFERENCES artifact_versions(version_id)
        ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS ix_completion_delivery_artifacts_version
    ON completion_delivery_artifacts(version_id, delivery_id);
CREATE INDEX IF NOT EXISTS ix_completion_delivery_artifacts_artifact
    ON completion_delivery_artifacts(artifact_id, delivery_id);
"""


def create_completion_delivery_schema(conn: sqlite3.Connection) -> None:
    """Install the delivery ledger inside the Store's migration transaction."""
    apply_ddl_script(conn, COMPLETION_DELIVERY_SCHEMA)


class DeliveryConflictError(RuntimeError):
    """An idempotency key was reused for different completion content."""


def canonical_json(value: Any) -> str:
    """Encode a JSON value deterministically, rejecting lossy/non-finite data."""
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("completion delivery manifest must be JSON-safe") from error


def json_sha256(value: Any) -> str:
    """Return the digest of :func:`canonical_json` for ``value``."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class CompletionDeliveryRepository:
    """Atomically bind final assistant prose to a verified Artifact manifest."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        lock: Any,
        *,
        clock_ms: Callable[[], int],
        id_factory: Callable[[str], str] | None = None,
    ) -> None:
        self._connection = connection
        self._lock = lock
        self._clock_ms = clock_ms
        self._id_factory = id_factory or (
            lambda prefix: f"{prefix}-{uuid.uuid4().hex[:16]}"
        )

    def commit_final_message(
        self,
        *,
        idempotency_key: str,
        root_frame_id: str,
        branch_id: str | None,
        frame_id: str | None,
        content: str,
        manifest: Mapping[str, Any],
        message_metadata: Mapping[str, Any] | None = None,
        expected_manifest_sha256: str | None = None,
        created_at: int | None = None,
        snapshot_verifier: Callable[[Mapping[str, Any]], object] | None = None,
    ) -> dict[str, Any]:
        """Commit one final message and its delivery fact in one transaction.

        Repeating the same scoped idempotency key with byte-equivalent content
        returns the original row and never appends a second message.  Reusing
        it for another frame, manifest, or message fails closed.
        """
        key = self._required_text("idempotency_key", idempotency_key)
        root = self._required_text("root_frame_id", root_frame_id)
        branch = self._required_text("branch_id", branch_id or root)
        if frame_id is not None:
            frame_id = self._required_text("frame_id", frame_id)
        if not isinstance(content, str) or not content.strip():
            raise ValueError("completion delivery content must be non-empty")
        if not isinstance(manifest, Mapping):
            raise ValueError("completion delivery manifest must be an object")
        projected_metadata = self._canonical_message_metadata(message_metadata)
        manifest_value = dict(manifest)
        if manifest_value.get("root_frame_id") != root:
            raise ValueError("completion delivery manifest scope does not match root")
        artifacts = manifest_value.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise ValueError("completion delivery manifest must contain artifacts")

        manifest_json = canonical_json(manifest_value)
        # Validate the canonical value below rather than the caller's mutable
        # nested objects.  Another thread retaining the input mapping cannot
        # change what this transaction proves after the digest is computed.
        canonical_manifest = json.loads(manifest_json)
        manifest_sha256 = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
        if (
            expected_manifest_sha256 is not None
            and expected_manifest_sha256 != manifest_sha256
        ):
            raise ValueError("completion delivery manifest hash changed")
        content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if projected_metadata.get("review_status") == "candidate":
            bound_candidate_sha256 = projected_metadata.get("candidate_content_sha256")
            if bound_candidate_sha256 not in (None, content_sha256):
                raise ValueError("completion candidate metadata digest changed")
            projected_metadata["candidate_content_sha256"] = content_sha256
        now = self._clock_ms() if created_at is None else int(created_at)
        delivery_id = self._id_factory("delivery")
        message_id = self._id_factory("m")

        with self._lock:
            self._begin()
            try:
                existing = self._by_idempotency_key_locked(key, root, branch)
                if existing is not None:
                    if snapshot_verifier is not None:
                        self._assert_versions_visible_locked(
                            canonical_manifest,
                            root_frame_id=root,
                            snapshot_verifier=snapshot_verifier,
                        )
                    self._assert_equivalent(
                        existing,
                        frame_id=frame_id,
                        content_sha256=content_sha256,
                        manifest_sha256=manifest_sha256,
                        message_metadata=projected_metadata,
                    )
                    self._connection.commit()
                    return existing

                relations = self._assert_versions_visible_locked(
                    canonical_manifest,
                    root_frame_id=root,
                    snapshot_verifier=snapshot_verifier,
                )

                seq_row = self._connection.execute(
                    "SELECT COALESCE(MAX(seq),-1)+1 AS seq FROM messages "
                    "WHERE root_frame_id=?",
                    (root,),
                ).fetchone()
                seq = int(seq_row["seq"])
                stored_metadata = dict(projected_metadata)
                stored_metadata["completion_delivery"] = {
                    "delivery_id": delivery_id,
                    "manifest_sha256": manifest_sha256,
                    "status": "committed",
                }
                metadata = canonical_json(stored_metadata)
                self._connection.execute(
                    "INSERT INTO messages(message_id,root_frame_id,branch_id,"
                    "frame_id,seq,role,content,metadata,created_at) "
                    "VALUES(?,?,?,?,?,'assistant',?,?,?)",
                    (
                        message_id,
                        root,
                        branch,
                        frame_id,
                        seq,
                        content,
                        metadata,
                        now,
                    ),
                )
                # Deliberately after the message INSERT.  A fault here proves
                # the surrounding transaction removes the otherwise-orphaned
                # user-visible claim.
                self._connection.execute(
                    "INSERT INTO completion_deliveries("
                    "delivery_id,idempotency_key,root_frame_id,branch_id,frame_id,"
                    "message_id,manifest_json,manifest_sha256,content_sha256,status,"
                    "created_at,published_at) VALUES(?,?,?,?,?,?,?,?,?,'committed',?,NULL)",
                    (
                        delivery_id,
                        key,
                        root,
                        branch,
                        frame_id,
                        message_id,
                        manifest_json,
                        manifest_sha256,
                        content_sha256,
                        now,
                    ),
                )
                for ordinal, relation in enumerate(relations):
                    self._connection.execute(
                        "INSERT INTO completion_delivery_artifacts("
                        "delivery_id,ordinal,artifact_id,version_id,size_bytes,sha256) "
                        "VALUES(?,?,?,?,?,?)",
                        (
                            delivery_id,
                            ordinal,
                            relation["artifact_id"],
                            relation["version_id"],
                            relation["size_bytes"],
                            relation["sha256"],
                        ),
                    )
                self._connection.commit()
            except Exception:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise
            row = self._get_locked(delivery_id)
            if row is None:  # pragma: no cover - committed INSERT is authoritative
                raise RuntimeError("completion delivery disappeared after commit")
            return row

    def promote_candidate_delivery(
        self,
        *,
        delivery_id: str,
        message_id: str,
        root_frame_id: str,
        branch_id: str | None,
        frame_id: str | None,
        expected_content: str,
        content: str,
        message_metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Atomically promote one exact, unpublished Stage 1 candidate.

        The delivery id, message id, full message scope, role, provisional
        marker, and previous content bytes are all checked under one
        ``BEGIN IMMEDIATE`` transaction.  A successful write replaces the
        message bytes and verdict metadata together with the delivery's
        ``content_sha256``.  Publication is an irreversible boundary: even an
        otherwise exact retry is refused after ``mark_published``.

        The candidate digest stored in message metadata makes an exact retry
        distinguishable from a caller supplying different expected bytes after
        the candidate marker has already been consumed.
        """

        ident = self._required_text("delivery_id", delivery_id)
        message = self._required_text("message_id", message_id)
        root = self._required_text("root_frame_id", root_frame_id)
        branch = (
            root if branch_id is None else self._required_text("branch_id", branch_id)
        )
        if frame_id is not None:
            frame_id = self._required_text("frame_id", frame_id)
        if not isinstance(expected_content, str):
            raise ValueError("completion candidate expected content must be text")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("promoted completion content must be non-empty")
        verdict_metadata = self._canonical_message_metadata(message_metadata)
        if verdict_metadata.get("review_status") not in _TERMINAL_REVIEW_STATUSES:
            raise ValueError("candidate verdict metadata has no terminal review status")

        candidate_sha256 = hashlib.sha256(expected_content.encode("utf-8")).hexdigest()
        content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if verdict_metadata.get("candidate_content_sha256") not in (
            None,
            candidate_sha256,
        ):
            raise ValueError("candidate verdict metadata digest changed")
        if verdict_metadata.get("reviewed_content_sha256") not in (
            None,
            content_sha256,
        ):
            raise ValueError("reviewed candidate metadata digest changed")
        verdict_metadata_sha256 = hashlib.sha256(
            canonical_json(verdict_metadata).encode("utf-8")
        ).hexdigest()

        with self._lock:
            self._begin()
            try:
                raw = self._connection.execute(
                    self._select_sql() + " WHERE d.delivery_id=?", (ident,)
                ).fetchone()
                if raw is None:
                    raise KeyError(f"no such completion delivery {ident!r}")
                current = self._decode_and_validate_locked(raw)
                if (
                    current.get("message_id") != message
                    or current.get("root_frame_id") != root
                    or current.get("branch_id") != branch
                    or current.get("frame_id") != frame_id
                    or current.get("message_role") != "assistant"
                ):
                    raise DeliveryConflictError(
                        "completion candidate delivery scope changed"
                    )
                if (
                    current.get("status") != "committed"
                    or current.get("published_at") is not None
                ):
                    raise DeliveryConflictError(
                        "published completion delivery is immutable"
                    )
                metadata = current.get("message_metadata")
                if not isinstance(metadata, dict):  # validated above; defensive
                    raise RuntimeError(
                        "completion delivery message metadata is invalid"
                    )

                if metadata.get("review_status") == "candidate":
                    if (
                        current.get("message_content") != expected_content
                        or current.get("content_sha256") != candidate_sha256
                    ):
                        raise DeliveryConflictError(
                            "completion candidate content changed"
                        )
                    bound_candidate_sha256 = metadata.get("candidate_content_sha256")
                    if bound_candidate_sha256 not in (None, candidate_sha256):
                        raise DeliveryConflictError(
                            "completion candidate metadata digest changed"
                        )
                    if _CANDIDATE_VERDICT_DIGEST_KEY in metadata:
                        raise RuntimeError(
                            "completion candidate verdict digest is invalid"
                        )
                    promoted_metadata = dict(metadata)
                    promoted_metadata.update(verdict_metadata)
                    promoted_metadata["candidate_content_sha256"] = candidate_sha256
                    promoted_metadata[_CANDIDATE_VERDICT_DIGEST_KEY] = (
                        verdict_metadata_sha256
                    )
                    encoded_metadata = canonical_json(promoted_metadata)
                    message_cursor = self._connection.execute(
                        "UPDATE messages SET content=?,metadata=? WHERE message_id=? "
                        "AND root_frame_id=? AND branch_id=? AND frame_id IS ? "
                        "AND role='assistant' AND content=? AND metadata IS ?",
                        (
                            content,
                            encoded_metadata,
                            message,
                            root,
                            branch,
                            frame_id,
                            expected_content,
                            raw["message_metadata"],
                        ),
                    )
                    if message_cursor.rowcount != 1:
                        raise RuntimeError(
                            "completion candidate message promotion lost its CAS"
                        )
                    delivery_cursor = self._connection.execute(
                        "UPDATE completion_deliveries SET content_sha256=? "
                        "WHERE delivery_id=? AND message_id=? AND root_frame_id=? "
                        "AND branch_id=? AND frame_id IS ? AND status='committed' "
                        "AND published_at IS NULL AND content_sha256=?",
                        (
                            content_sha256,
                            ident,
                            message,
                            root,
                            branch,
                            frame_id,
                            candidate_sha256,
                        ),
                    )
                    if delivery_cursor.rowcount != 1:
                        raise RuntimeError(
                            "completion candidate delivery promotion lost its CAS"
                        )
                elif (
                    current.get("message_content") == content
                    and current.get("content_sha256") == content_sha256
                    and metadata.get("candidate_content_sha256") == candidate_sha256
                    and metadata.get(_CANDIDATE_VERDICT_DIGEST_KEY)
                    == verdict_metadata_sha256
                    and all(
                        metadata.get(key) == value
                        for key, value in verdict_metadata.items()
                    )
                ):
                    # Exact committed replay.  No write occurs.
                    pass
                else:
                    raise DeliveryConflictError(
                        "completion candidate promotion conflicts with durable content"
                    )
                self._connection.commit()
            except Exception:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise
            result = self._get_locked(ident)
            if result is None:  # pragma: no cover - row cannot vanish under lock
                raise RuntimeError(
                    "completion delivery disappeared after candidate promotion"
                )
            return result

    def bind_imported_message(
        self,
        *,
        idempotency_key: str,
        message_id: str,
        root_frame_id: str,
        branch_id: str | None,
        frame_id: str | None,
        expected_current_content: str,
        content: str,
        manifest: Mapping[str, Any],
        status: str,
        created_at: int,
        published_at: int | None = None,
        snapshot_verifier: Callable[[Mapping[str, Any]], object] | None = None,
    ) -> dict[str, Any]:
        """Bind a remapped package message to a newly verified delivery row.

        Session import creates messages before Artifact identities are known.  Once
        the exact versions have been restored, this transaction replaces the
        safe pending placeholder in that existing assistant message while
        inserting the local delivery and version relations.  A
        failure at any point restores the message exactly as it was before the
        transaction; an imported message can therefore never become visible with
        half of its delivery relation remapped.
        """

        key = self._required_text("idempotency_key", idempotency_key)
        message = self._required_text("message_id", message_id)
        root = self._required_text("root_frame_id", root_frame_id)
        branch = self._required_text("branch_id", branch_id or root)
        if frame_id is not None:
            frame_id = self._required_text("frame_id", frame_id)
        if not isinstance(expected_current_content, str):
            raise ValueError("completion delivery current content must be text")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("completion delivery content must be non-empty")
        if not isinstance(manifest, Mapping):
            raise ValueError("completion delivery manifest must be an object")
        if status not in {"committed", "published"}:
            raise ValueError("completion delivery status is invalid")
        if status == "published":
            if published_at is None:
                raise ValueError("published completion delivery needs a timestamp")
            publication = int(published_at)
        else:
            if published_at is not None:
                raise ValueError("committed completion delivery cannot be published")
            publication = None
        created = int(created_at)
        if publication is not None and publication < created:
            raise ValueError("completion delivery publication predates its commit")

        manifest_json = canonical_json(dict(manifest))
        canonical_manifest = json.loads(manifest_json)
        if canonical_manifest.get("root_frame_id") != root:
            raise ValueError("completion delivery manifest scope does not match root")
        manifest_sha256 = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
        content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        delivery_id = self._id_factory("delivery")

        with self._lock:
            self._begin()
            try:
                if self._by_idempotency_key_locked(key, root, branch) is not None:
                    raise DeliveryConflictError(
                        "completion delivery import identity already exists"
                    )
                row = self._connection.execute(
                    "SELECT root_frame_id,branch_id,frame_id,role,content,metadata,"
                    "created_at "
                    "FROM messages WHERE message_id=?",
                    (message,),
                ).fetchone()
                if (
                    row is None
                    or row["root_frame_id"] != root
                    or row["branch_id"] != branch
                    or row["frame_id"] != frame_id
                    or row["role"] != "assistant"
                    or row["content"] != expected_current_content
                    or row["created_at"] != created
                ):
                    raise RuntimeError(
                        "completion delivery import message scope is invalid"
                    )
                metadata = self._decode_projected_metadata(row["metadata"])
                if (
                    metadata.get("completion_delivery_import_pending") is not True
                    or "completion_delivery" in metadata
                ):
                    raise RuntimeError(
                        "completion delivery import pending relation is missing"
                    )
                relations = self._assert_versions_visible_locked(
                    canonical_manifest,
                    root_frame_id=root,
                    snapshot_verifier=snapshot_verifier,
                )
                envelope: dict[str, Any] = {
                    "delivery_id": delivery_id,
                    "manifest_sha256": manifest_sha256,
                    "status": status,
                }
                if publication is not None:
                    envelope["published_at"] = publication
                metadata.pop("completion_delivery_import_pending", None)
                metadata["completion_delivery"] = envelope
                self._connection.execute(
                    "UPDATE messages SET content=?,metadata=? WHERE message_id=?",
                    (content, canonical_json(metadata), message),
                )
                self._connection.execute(
                    "INSERT INTO completion_deliveries("
                    "delivery_id,idempotency_key,root_frame_id,branch_id,frame_id,"
                    "message_id,manifest_json,manifest_sha256,content_sha256,status,"
                    "created_at,published_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        delivery_id,
                        key,
                        root,
                        branch,
                        frame_id,
                        message,
                        manifest_json,
                        manifest_sha256,
                        content_sha256,
                        status,
                        created,
                        publication,
                    ),
                )
                for ordinal, relation in enumerate(relations):
                    self._connection.execute(
                        "INSERT INTO completion_delivery_artifacts("
                        "delivery_id,ordinal,artifact_id,version_id,size_bytes,sha256) "
                        "VALUES(?,?,?,?,?,?)",
                        (
                            delivery_id,
                            ordinal,
                            relation["artifact_id"],
                            relation["version_id"],
                            relation["size_bytes"],
                            relation["sha256"],
                        ),
                    )
                self._connection.commit()
            except Exception:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise
            result = self._get_locked(delivery_id)
            if result is None:  # pragma: no cover - committed INSERT is authoritative
                raise RuntimeError("completion delivery disappeared after import")
            return result

    def _assert_versions_visible_locked(
        self,
        manifest: Mapping[str, Any],
        *,
        root_frame_id: str,
        snapshot_verifier: Callable[[Mapping[str, Any]], object] | None = None,
    ) -> list[dict[str, Any]]:
        """Bind a new claim to exact, still-visible version rows.

        Manifest construction verifies immutable bytes before this repository
        is called.  There is nevertheless a scheduling gap between that read
        and the message transaction.  Re-checking identity, scope and recorded
        byte metadata under ``BEGIN IMMEDIATE`` prevents a concurrent delete or
        row change from landing a success message whose URL was already absent
        when the claim committed.
        """

        if manifest.get("schema_version") != 1:
            raise ValueError("completion delivery manifest schema is unsupported")
        project_id = self._required_text(
            "manifest project_id", manifest.get("project_id")
        )
        if manifest.get("root_frame_id") != root_frame_id:
            raise ValueError("completion delivery manifest scope does not match root")
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise ValueError("completion delivery manifest must contain artifacts")

        seen: set[str] = set()
        relations: list[dict[str, Any]] = []
        for entry in artifacts:
            if not isinstance(entry, Mapping):
                raise ValueError("completion delivery Artifact entry must be an object")
            version_id = self._required_text("version_id", entry.get("version_id"))
            artifact_id = self._required_text("artifact_id", entry.get("artifact_id"))
            if version_id in seen:
                raise ValueError("completion delivery manifest repeats a version")
            seen.add(version_id)
            expected_size = entry.get("size_bytes")
            expected_checksum = entry.get("sha256")
            if (
                isinstance(expected_size, bool)
                or not isinstance(expected_size, int)
                or expected_size < 0
                or not isinstance(expected_checksum, str)
                or _SHA256.fullmatch(expected_checksum) is None
            ):
                raise ValueError(
                    "completion delivery Artifact byte identity is invalid"
                )

            row = self._connection.execute(
                "SELECT v.artifact_id,v.size_bytes,v.checksum,v.snapshot_path,"
                "a.root_frame_id,a.project_id FROM artifact_versions v "
                "JOIN artifacts a ON a.artifact_id=v.artifact_id "
                "WHERE v.version_id=?",
                (version_id,),
            ).fetchone()
            if (
                row is None
                or row["artifact_id"] != artifact_id
                or row["root_frame_id"] != root_frame_id
                or row["project_id"] != project_id
                or row["size_bytes"] != expected_size
                or row["checksum"] != expected_checksum
                or not row["snapshot_path"]
            ):
                raise RuntimeError(
                    "completion delivery Artifact version changed before commit"
                )
            if snapshot_verifier is not None:
                snapshot_verifier(
                    {
                        "version_id": version_id,
                        "artifact_id": artifact_id,
                        "snapshot_path": row["snapshot_path"],
                        "size_bytes": row["size_bytes"],
                        "checksum": row["checksum"],
                    }
                )
            relations.append(
                {
                    "artifact_id": artifact_id,
                    "version_id": version_id,
                    "size_bytes": expected_size,
                    "sha256": expected_checksum,
                }
            )
        return relations

    def mark_published(
        self, delivery_id: str, *, published_at: int | None = None
    ) -> dict[str, Any]:
        """Mark a committed delivery published, idempotently.

        The timestamp is write-once.  Updating the message metadata in the same
        transaction keeps session reopen and the recovery ledger in agreement.
        """
        ident = self._required_text("delivery_id", delivery_id)
        now = self._clock_ms() if published_at is None else int(published_at)
        with self._lock:
            self._begin()
            try:
                row = self._get_locked(ident)
                if row is None:
                    raise KeyError(f"no such completion delivery {ident!r}")
                if row["status"] == "published":
                    self._connection.commit()
                    return row
                if now < int(row["created_at"]):
                    raise ValueError(
                        "completion delivery publication predates its commit"
                    )
                metadata = row.get("message_metadata")
                if not isinstance(metadata, dict):
                    raise RuntimeError(
                        "completion delivery message metadata is invalid"
                    )
                envelope = metadata.get("completion_delivery")
                if not isinstance(envelope, dict):
                    raise RuntimeError(
                        "completion delivery message relation is missing"
                    )
                envelope["status"] = "published"
                envelope["published_at"] = now
                self._connection.execute(
                    "UPDATE messages SET metadata=? WHERE message_id=?",
                    (canonical_json(metadata), row["message_id"]),
                )
                cursor = self._connection.execute(
                    "UPDATE completion_deliveries SET status='published',"
                    "published_at=? WHERE delivery_id=? AND status='committed'",
                    (now, ident),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("completion delivery publication lost its CAS")
                self._connection.commit()
            except Exception:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise
            result = self._get_locked(ident)
            if result is None:  # pragma: no cover - row cannot vanish under lock
                raise RuntimeError("completion delivery disappeared after publication")
            return result

    def get(self, delivery_id: str) -> dict[str, Any] | None:
        ident = self._required_text("delivery_id", delivery_id)
        with self._lock:
            return self._get_locked(ident)

    def committed(
        self,
        *,
        root_frame_id: str | None = None,
        branch_id: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """List durable rows whose socket publication was not marked complete."""
        clauses = ["d.status='committed'"]
        params: list[Any] = []
        if root_frame_id is not None:
            clauses.append("d.root_frame_id=?")
            params.append(self._required_text("root_frame_id", root_frame_id))
        if branch_id is not None:
            clauses.append("d.branch_id=?")
            params.append(self._required_text("branch_id", branch_id))
        params.append(max(1, min(int(limit), 10_000)))
        with self._lock:
            rows = self._connection.execute(
                self._select_sql()
                + " WHERE "
                + " AND ".join(clauses)
                + " ORDER BY d.created_at,d.delivery_id LIMIT ?",
                params,
            ).fetchall()
            decoded = [self._decode(row) for row in rows]
            # Validate all immutable delivery-to-version relations in bounded
            # batches under the same Store lock. A per-row query here made a
            # recovery scan hold the process-wide Store lock for N+1 queries.
            self._validate_relations_locked(decoded)
            return decoded

    def for_session(
        self,
        root_frame_id: str,
        *,
        limit: int = 10_000,
    ) -> list[dict[str, Any]]:
        """List every validated delivery owned by one Session.

        Unlike :meth:`committed`, this is a complete historical projection used
        by portable Session packages, so published rows are included as well.
        The caller supplies a bounded limit and may request one extra row to
        detect truncation rather than silently exporting an incomplete ledger.
        """

        root = self._required_text("root_frame_id", root_frame_id)
        bound = int(limit)
        if bound < 1 or bound > 100_001:
            raise ValueError("completion delivery list limit is invalid")
        with self._lock:
            rows = self._connection.execute(
                self._select_sql()
                + " WHERE d.root_frame_id=? ORDER BY d.created_at,d.delivery_id LIMIT ?",
                (root, bound),
            ).fetchall()
            decoded = [self._decode(row) for row in rows]
            self._validate_relations_locked(decoded)
            return decoded

    def validate_message_projection(
        self,
        root_frame_id: str,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Reject a corrupt delivery before conversation/session projection.

        The delivery repository's ``get`` path already validates manifests,
        hashes, metadata and exact-version relations.  Reopen readers do not
        call ``get``; they read ``messages`` directly.  Resolve any delivery
        linked to each visible ``(root, seq)`` under the same Store lock and
        apply that validation here.  A message whose metadata still claims a
        delivery but whose ledger row is gone is an orphan and fails closed.
        """

        root = self._required_text("root_frame_id", root_frame_id)
        message_seqs: list[int] = []
        for message in messages:
            seq = message.get("seq")
            if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
                raise RuntimeError(
                    "completion delivery message projection has no valid sequence"
                )
            message_seqs.append(seq)

        deliveries: list[dict[str, Any]] = []
        with self._lock:
            for chunk in self._chunks(message_seqs):
                placeholders = ",".join("?" for _ in chunk)
                rows = self._connection.execute(
                    self._select_sql()
                    + " WHERE m.root_frame_id=? AND m.seq IN ("
                    + placeholders
                    + ") ORDER BY d.delivery_id",
                    (root, *chunk),
                ).fetchall()
                deliveries.extend(self._decode(row) for row in rows)
            self._validate_relations_locked(deliveries)

        by_message_seq: dict[int, dict[str, Any]] = {}
        for delivery in deliveries:
            message_seq = int(delivery["message_seq"])
            if message_seq in by_message_seq:
                # Sequence is unique within a root. Keep an explicit guard so
                # damaged storage never becomes ambiguous visible prose.
                raise RuntimeError(
                    "completion delivery message has multiple ledger rows"
                )
            by_message_seq[message_seq] = delivery

        for message in messages:
            delivery = by_message_seq.get(int(message["seq"]))
            if delivery is not None:
                self._assert_projected_message(message, delivery)
                continue
            metadata = self._decode_projected_metadata(message.get("metadata"))
            if "completion_delivery" in metadata:
                raise RuntimeError(
                    "completion delivery message is missing its ledger row"
                )
        return messages

    @staticmethod
    def _chunks(values: list[Any]) -> list[list[Any]]:
        return [
            values[offset : offset + _SQL_BATCH_SIZE]
            for offset in range(0, len(values), _SQL_BATCH_SIZE)
        ]

    def _validate_relations_locked(self, deliveries: list[dict[str, Any]]) -> None:
        """Validate exact version relations with bounded bulk reads."""

        if not deliveries:
            return
        delivery_ids = [str(delivery["delivery_id"]) for delivery in deliveries]
        relations_by_delivery: dict[str, list[dict[str, Any]]] = {
            delivery_id: [] for delivery_id in delivery_ids
        }
        for chunk in self._chunks(delivery_ids):
            placeholders = ",".join("?" for _ in chunk)
            rows = self._connection.execute(
                "SELECT delivery_id,artifact_id,version_id,size_bytes,sha256 "
                "FROM completion_delivery_artifacts WHERE delivery_id IN ("
                + placeholders
                + ") ORDER BY delivery_id,ordinal",
                tuple(chunk),
            ).fetchall()
            for row in rows:
                delivery_id = str(row["delivery_id"])
                relation = dict(row)
                relation.pop("delivery_id", None)
                relations_by_delivery.setdefault(delivery_id, []).append(relation)

        for delivery in deliveries:
            expected = [
                {
                    "artifact_id": entry.get("artifact_id"),
                    "version_id": entry.get("version_id"),
                    "size_bytes": entry.get("size_bytes"),
                    "sha256": entry.get("sha256"),
                }
                for entry in delivery["manifest"].get("artifacts", [])
            ]
            if relations_by_delivery.get(str(delivery["delivery_id"]), []) != expected:
                raise RuntimeError("completion delivery Artifact relation mismatch")

    def _begin(self) -> None:
        if self._connection.in_transaction:
            raise RuntimeError(
                "completion delivery requires ownership of a clean SQLite transaction"
            )
        self._connection.execute("BEGIN IMMEDIATE")

    def _by_idempotency_key_locked(
        self, key: str, root: str, branch: str
    ) -> dict[str, Any] | None:
        row = self._connection.execute(
            self._select_sql()
            + " WHERE d.idempotency_key=? AND d.root_frame_id=? AND d.branch_id=?",
            (key, root, branch),
        ).fetchone()
        return self._decode_and_validate_locked(row) if row else None

    def _get_locked(self, delivery_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            self._select_sql() + " WHERE d.delivery_id=?", (delivery_id,)
        ).fetchone()
        return self._decode_and_validate_locked(row) if row else None

    def _decode_and_validate_locked(self, row: sqlite3.Row) -> dict[str, Any]:
        decoded = self._decode(row)
        self._validate_relations_locked([decoded])
        return decoded

    @staticmethod
    def _select_sql() -> str:
        return (
            "SELECT d.*,m.seq AS message_seq,m.role AS message_role,"
            "m.content AS message_content,m.metadata AS message_metadata,"
            "m.root_frame_id AS _message_root_frame_id,"
            "m.branch_id AS _message_branch_id,m.frame_id AS _message_frame_id "
            "FROM completion_deliveries d JOIN messages m "
            "ON m.message_id=d.message_id"
        )

    @classmethod
    def _assert_projected_message(
        cls,
        message: Mapping[str, Any],
        delivery: Mapping[str, Any],
    ) -> None:
        metadata = cls._decode_projected_metadata(message.get("metadata"))
        if (
            message.get("role") != delivery.get("message_role")
            or message.get("content") != delivery.get("message_content")
            or message.get("seq") != delivery.get("message_seq")
            or metadata != delivery.get("message_metadata")
        ):
            raise RuntimeError("completion delivery message projection mismatch")
        projected_id = message.get("message_id")
        if projected_id is not None and projected_id != delivery.get("message_id"):
            raise RuntimeError("completion delivery message identity mismatch")

    @staticmethod
    def _decode_projected_metadata(raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            decoded = raw
        else:
            try:
                decoded = json.loads(raw or "{}")
            except (TypeError, ValueError) as error:
                raise RuntimeError(
                    "completion delivery message metadata is invalid"
                ) from error
        if not isinstance(decoded, dict):
            raise RuntimeError("completion delivery message metadata is invalid")
        return decoded

    @staticmethod
    def _assert_equivalent(
        existing: Mapping[str, Any],
        *,
        frame_id: str | None,
        content_sha256: str,
        manifest_sha256: str,
        message_metadata: Mapping[str, Any],
    ) -> None:
        observed_metadata = existing.get("message_metadata")
        if isinstance(observed_metadata, Mapping):
            observed_metadata = dict(observed_metadata)
            observed_metadata.pop("completion_delivery", None)
        if (
            existing.get("frame_id") != frame_id
            or existing.get("content_sha256") != content_sha256
            or existing.get("manifest_sha256") != manifest_sha256
            or observed_metadata != dict(message_metadata)
        ):
            raise DeliveryConflictError(
                "completion delivery idempotency key was reused for different content"
            )

    @staticmethod
    def _canonical_message_metadata(
        value: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("completion delivery message metadata must be an object")
        raw = dict(value)
        if "completion_delivery" in raw or _CANDIDATE_VERDICT_DIGEST_KEY in raw:
            raise ValueError("completion delivery message metadata has a reserved key")
        try:
            decoded = json.loads(canonical_json(raw))
        except ValueError as error:
            raise ValueError(
                "completion delivery message metadata must be JSON-safe"
            ) from error
        if not isinstance(decoded, dict):  # pragma: no cover - Mapping round trip
            raise ValueError("completion delivery message metadata must be an object")
        return decoded

    @staticmethod
    def _required_text(name: str, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
        return value

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        try:
            manifest = json.loads(result.pop("manifest_json"))
            metadata = json.loads(result.pop("message_metadata"))
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError("completion delivery record is corrupt") from error
        if not isinstance(manifest, dict) or not isinstance(metadata, dict):
            raise RuntimeError("completion delivery record is corrupt")
        message_root_frame_id = result.pop("_message_root_frame_id", None)
        message_branch_id = result.pop("_message_branch_id", None)
        message_frame_id = result.pop("_message_frame_id", None)
        if (
            result.get("message_role") != "assistant"
            or message_root_frame_id != result.get("root_frame_id")
            or message_branch_id != result.get("branch_id")
            or message_frame_id != result.get("frame_id")
        ):
            raise RuntimeError("completion delivery message scope mismatch")
        try:
            observed_manifest_sha256 = hashlib.sha256(
                canonical_json(manifest).encode("utf-8")
            ).hexdigest()
        except ValueError as error:
            raise RuntimeError("completion delivery manifest is corrupt") from error
        if observed_manifest_sha256 != result.get("manifest_sha256"):
            raise RuntimeError("completion delivery manifest hash mismatch")
        content = result.get("message_content")
        if not isinstance(content, str) or hashlib.sha256(
            content.encode("utf-8")
        ).hexdigest() != result.get("content_sha256"):
            raise RuntimeError("completion delivery message hash mismatch")
        envelope = metadata.get("completion_delivery")
        if (
            not isinstance(envelope, dict)
            or envelope.get("delivery_id") != result.get("delivery_id")
            or envelope.get("manifest_sha256") != result.get("manifest_sha256")
            or envelope.get("status") != result.get("status")
        ):
            raise RuntimeError("completion delivery message relation mismatch")
        status = result.get("status")
        published_at = result.get("published_at")
        created_at = result.get("created_at")
        if status == "committed":
            if published_at is not None or "published_at" in envelope:
                raise RuntimeError("completion delivery publication relation mismatch")
        elif (
            isinstance(published_at, bool)
            or not isinstance(published_at, int)
            or isinstance(created_at, bool)
            or not isinstance(created_at, int)
            or published_at < created_at
            or envelope.get("published_at") != published_at
        ):
            raise RuntimeError("completion delivery publication relation mismatch")
        result["manifest"] = manifest
        result["message_metadata"] = metadata
        return result


__all__ = [
    "COMPLETION_DELIVERY_SCHEMA",
    "CompletionDeliveryRepository",
    "DeliveryConflictError",
    "canonical_json",
    "create_completion_delivery_schema",
    "json_sha256",
]
