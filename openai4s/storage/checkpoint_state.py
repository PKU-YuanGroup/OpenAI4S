"""Immutable plan, review, and memory state for session checkpoints.

Workspace bytes, Artifact heads, environment pins, and policy state live in
their owning checkpoint fields.  This repository owns the remaining mutable
session-domain projections that must travel with a branch: structured plans,
Reviewer activity/settings/annotations, and the project's memory blocks.

The snapshot body is canonical JSON with a SHA-256 integrity digest.  Capture
and restore accept ``commit=False`` so the checkpoint and branch-activation
repositories can include them in their own SQLite transaction.  Older
checkpoints have no row here; restore treats that as explicitly unavailable
and never guesses or clears live state.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any, Callable

CHECKPOINT_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoint_state_snapshots (
    checkpoint_id          TEXT PRIMARY KEY,
    root_frame_id          TEXT NOT NULL,
    branch_id              TEXT NOT NULL,
    project_id             TEXT NOT NULL,
    schema_version         INTEGER NOT NULL,
    state_json             TEXT NOT NULL,
    state_sha256           TEXT NOT NULL,
    source_checkpoint_id   TEXT,
    trust_state            TEXT NOT NULL DEFAULT 'local',
    import_source_sha256   TEXT,
    created_at             INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_checkpoint_state_root
    ON checkpoint_state_snapshots(root_frame_id, branch_id, created_at);
"""

SCHEMA_VERSION = 1
MAX_PLANS = 5_000
MAX_REVIEW_STEPS = 25_000
MAX_ANNOTATIONS = 25_000
MAX_MEMORIES = 50_000
MAX_STATE_BYTES = 64 << 20

_REVIEW_KINDS = ("review", "review_settings")
_REVIEW_SETTING_NAMES = {
    "auto_review": "review:auto:{root_frame_id}",
    "reviewer_model": "review:model:{root_frame_id}",
}


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_value(raw: Any, fallback: Any) -> Any:
    if raw in (None, ""):
        return fallback
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


class CheckpointStateRepository:
    """Capture and restore immutable checkpoint-owned domain state."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        lock: Any,
        *,
        clock_ms: Callable[[], int],
    ) -> None:
        self._connection = connection
        self._lock = lock
        self._clock_ms = clock_ms
        with self._lock:
            self._connection.executescript(CHECKPOINT_STATE_SCHEMA)
            self._migrate_schema()
            self._connection.commit()

    def _migrate_schema(self) -> None:
        columns = {
            str(row["name"])
            for row in self._connection.execute(
                "PRAGMA table_info(checkpoint_state_snapshots)"
            ).fetchall()
        }
        for name, declaration in (
            ("trust_state", "TEXT NOT NULL DEFAULT 'local'"),
            ("import_source_sha256", "TEXT"),
        ):
            if name not in columns:
                self._connection.execute(
                    f"ALTER TABLE checkpoint_state_snapshots ADD COLUMN "
                    f"{name} {declaration}"
                )

    def capture_checkpoint(
        self,
        *,
        checkpoint_id: str,
        root_frame_id: str,
        branch_id: str,
        source_checkpoint_id: str | None = None,
        commit: bool = True,
    ) -> dict[str, Any] | None:
        """Capture live state or clone an exact historical snapshot.

        Revert checkpoints pass ``source_checkpoint_id``.  If that older
        checkpoint predates structured state snapshots, no row is created:
        capturing the current live projection would falsely claim it belonged
        to the historical target.
        """

        checkpoint_id = self._text("checkpoint_id", checkpoint_id)
        root_frame_id = self._text("root_frame_id", root_frame_id)
        branch_id = self._text("branch_id", branch_id)
        with self._lock:
            existing = self._row(checkpoint_id)
            if existing is not None:
                return self._decode(existing, include_state=False)

            source = None
            if source_checkpoint_id is not None:
                source_checkpoint_id = self._text(
                    "source_checkpoint_id", source_checkpoint_id
                )
                source = self._row(source_checkpoint_id)
                if source is None:
                    return None
                if source["root_frame_id"] != root_frame_id:
                    raise ValueError(
                        "checkpoint state source belongs to another session"
                    )
                project_id = str(source["project_id"])
                state_json = str(source["state_json"])
                state_sha256 = str(source["state_sha256"])
                self._validated_state(source)
            else:
                frame = self._connection.execute(
                    "SELECT project_id,root_frame_id FROM frames WHERE frame_id=?",
                    (root_frame_id,),
                ).fetchone()
                if frame is None:
                    # Compatibility callers can create low-level checkpoint rows
                    # before a frame exists.  Such rows are not falsely upgraded
                    # to a restorable plan/review/memory snapshot.
                    return None
                canonical_root = str(frame["root_frame_id"] or root_frame_id)
                if canonical_root != root_frame_id:
                    raise ValueError("checkpoint state requires a root frame")
                project_id = str(frame["project_id"] or "default")
                state = self._capture_state(root_frame_id, project_id)
                encoded = _canonical_json(state)
                if len(encoded) > MAX_STATE_BYTES:
                    raise ValueError("checkpoint domain state exceeds the size limit")
                state_json = encoded.decode("utf-8")
                state_sha256 = _sha256(encoded)

            now = self._clock_ms()
            self._connection.execute(
                "INSERT INTO checkpoint_state_snapshots("
                "checkpoint_id,root_frame_id,branch_id,project_id,schema_version,"
                "state_json,state_sha256,source_checkpoint_id,trust_state,"
                "import_source_sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    checkpoint_id,
                    root_frame_id,
                    branch_id,
                    project_id,
                    SCHEMA_VERSION,
                    state_json,
                    state_sha256,
                    source_checkpoint_id,
                    "local",
                    None,
                    now,
                ),
            )
            if commit:
                self._connection.commit()
            row = self._row(checkpoint_id)
        if row is None:
            raise RuntimeError("checkpoint domain state did not persist")
        return self._decode(row, include_state=False)

    def get(
        self,
        checkpoint_id: str,
        *,
        include_state: bool = False,
    ) -> dict[str, Any] | None:
        checkpoint_id = self._text("checkpoint_id", checkpoint_id)
        with self._lock:
            row = self._row(checkpoint_id)
            if row is None:
                return None
            # Integrity is checked even for a summary read.  A corrupt snapshot
            # must never be advertised as available for activation.
            self._validated_state(row)
            return self._decode(row, include_state=include_state)

    def list(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return integrity-checked summaries without plan/memory bodies."""

        root_frame_id = self._text("root_frame_id", root_frame_id)
        clauses = ["root_frame_id=?"]
        params: list[Any] = [root_frame_id]
        if branch_id is not None:
            clauses.append("branch_id=?")
            params.append(self._text("branch_id", branch_id))
        params.append(max(1, min(int(limit), 1_000)))
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM checkpoint_state_snapshots WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at DESC,checkpoint_id DESC LIMIT ?",
                tuple(params),
            ).fetchall()
            return [self._decode(row, include_state=False) for row in rows]

    def validate_checkpoint_state_import(
        self,
        source: Mapping[str, Any],
        *,
        include_state: bool = False,
    ) -> dict[str, Any]:
        """Pure preflight for an untrusted checkpoint-state envelope.

        This method performs no SQL read or write.  Package preflight can call
        it before creating a project, frame, Artifact, branch, or checkpoint.
        Artifact *existence* is deliberately left to the package-level manifest
        validator; this boundary validates only reference shape and scope.
        """

        if not isinstance(source, Mapping):
            raise ValueError("checkpoint state import envelope must be an object")
        identities = {
            name: self._text(name, str(source.get(name) or ""))
            for name in (
                "checkpoint_id",
                "root_frame_id",
                "branch_id",
                "project_id",
            )
        }
        state = self._source_state(source)
        self._validate_scope(
            state,
            identities["root_frame_id"],
            identities["project_id"],
        )
        object_ids = [
            *(str(item["plan_id"]) for item in state["plans"]),
            *(str(item["step_id"]) for item in state["review"]["steps"]),
            *(str(item["annotation_id"]) for item in state["review"]["annotations"]),
            *(str(item["memory_id"]) for item in state["memory"]["entries"]),
        ]
        if len(object_ids) != len(set(object_ids)):
            raise ValueError("checkpoint state identities are not globally unique")
        reserved = {
            identities["checkpoint_id"],
            identities["root_frame_id"],
            identities["project_id"],
        }
        if reserved & set(object_ids):
            raise ValueError("checkpoint state object identity collides with its scope")
        artifact_ids = sorted(
            {
                str(item.get("artifact_id"))
                for item in state["plans"]
                if item.get("artifact_id")
            }
            | {
                str(item.get("artifact_id"))
                for item in state["review"]["annotations"]
                if item.get("artifact_id")
            }
        )
        result = {
            **identities,
            "schema_version": SCHEMA_VERSION,
            "state_sha256": str(source["state_sha256"]),
            "counts": {
                "plans": len(state["plans"]),
                "review_steps": len(state["review"]["steps"]),
                "annotations": len(state["review"]["annotations"]),
                "memories": len(state["memory"]["entries"]),
            },
            "artifact_ids": artifact_ids,
            "valid": True,
            "contains_bodies": bool(include_state),
        }
        if source.get("source_checkpoint_id"):
            result["source_checkpoint_id"] = self._text(
                "source_checkpoint_id", str(source["source_checkpoint_id"])
            )
        if include_state:
            result["state"] = state
        return result

    def import_quarantined_snapshot(
        self,
        source: Mapping[str, Any],
        *,
        checkpoint_id: str,
        root_frame_id: str,
        branch_id: str,
        project_id: str,
        artifact_id_map: Mapping[str, str] | None = None,
        source_checkpoint_id: str | None = None,
        commit: bool = True,
    ) -> dict[str, Any]:
        """Validate, remap, and persist an untrusted package snapshot.

        Database identities are derived from the new root plus each source ID,
        so the same logical object maps consistently across every imported
        checkpoint without retaining the source identity.  Artifact references
        use the package importer's already-validated map.  Review automation is
        always materialized as an explicit local ``off`` override.
        """

        validated = self.validate_checkpoint_state_import(
            source,
            include_state=True,
        )
        checkpoint_id = self._text("checkpoint_id", checkpoint_id)
        root_frame_id = self._text("root_frame_id", root_frame_id)
        branch_id = self._text("branch_id", branch_id)
        project_id = self._text("project_id", project_id)
        if source_checkpoint_id is not None:
            source_checkpoint_id = self._text(
                "source_checkpoint_id", source_checkpoint_id
            )
        source_state = validated["state"]
        source_digest = str(validated["state_sha256"])
        source_identity = {
            name: str(validated[name])
            for name in (
                "checkpoint_id",
                "root_frame_id",
                "branch_id",
                "project_id",
            )
        }
        artifact_map = {
            self._text("source artifact_id", str(old)): self._text(
                "artifact_id", str(new)
            )
            for old, new in dict(artifact_id_map or {}).items()
        }
        identity_replacements = {
            source_identity["checkpoint_id"]: checkpoint_id,
            source_identity["branch_id"]: branch_id,
            source_identity["project_id"]: project_id,
        }
        # A canonical root branch commonly uses the same source string for
        # root_frame_id and branch_id.  Session identity wins in generic nested
        # references; explicit branch fields are assigned separately below.
        identity_replacements[source_identity["root_frame_id"]] = root_frame_id
        old_parent = str(source.get("source_checkpoint_id") or "")
        if old_parent and source_checkpoint_id:
            identity_replacements[old_parent] = source_checkpoint_id
        remapped, old_ids = self._remap_import_state(
            source_state,
            root_frame_id=root_frame_id,
            project_id=project_id,
            identity_seed=root_frame_id,
            artifact_id_map=artifact_map,
            identity_replacements=identity_replacements,
        )
        encoded = _canonical_json(remapped)
        if len(encoded) > MAX_STATE_BYTES:
            raise ValueError("imported checkpoint domain state exceeds the size limit")
        digest = _sha256(encoded)
        self._validated_state(
            {
                "schema_version": SCHEMA_VERSION,
                "state_json": encoded.decode("utf-8"),
                "state_sha256": digest,
            }
        )
        self._validate_scope(remapped, root_frame_id, project_id)
        residual = self._identity_residuals(remapped) & old_ids
        if residual:
            raise ValueError("imported checkpoint state retains a source identity")

        with self._lock:
            if self._row(checkpoint_id) is not None:
                raise ValueError("checkpoint domain state already exists")
            checkpoint = self._connection.execute(
                "SELECT root_frame_id,branch_id FROM session_checkpoints "
                "WHERE checkpoint_id=?",
                (checkpoint_id,),
            ).fetchone()
            if (
                checkpoint is None
                or checkpoint["root_frame_id"] != root_frame_id
                or checkpoint["branch_id"] != branch_id
            ):
                raise ValueError("imported state target checkpoint scope is invalid")
            frame = self._connection.execute(
                "SELECT project_id,root_frame_id FROM frames WHERE frame_id=?",
                (root_frame_id,),
            ).fetchone()
            if (
                frame is None
                or str(frame["root_frame_id"] or root_frame_id) != root_frame_id
                or frame["project_id"] != project_id
            ):
                raise ValueError("imported state target session scope is invalid")
            if source_checkpoint_id is not None:
                parent = self._connection.execute(
                    "SELECT root_frame_id FROM checkpoint_state_snapshots "
                    "WHERE checkpoint_id=?",
                    (source_checkpoint_id,),
                ).fetchone()
                if parent is None or parent["root_frame_id"] != root_frame_id:
                    raise ValueError("imported state parent snapshot is unavailable")
            self._validate_import_artifacts(remapped, root_frame_id)
            try:
                self._connection.execute(
                    "INSERT INTO checkpoint_state_snapshots("
                    "checkpoint_id,root_frame_id,branch_id,project_id,"
                    "schema_version,state_json,state_sha256,source_checkpoint_id,"
                    "trust_state,import_source_sha256,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        checkpoint_id,
                        root_frame_id,
                        branch_id,
                        project_id,
                        SCHEMA_VERSION,
                        encoded.decode("utf-8"),
                        digest,
                        source_checkpoint_id,
                        "quarantined_import",
                        source_digest,
                        self._clock_ms(),
                    ),
                )
            except Exception:
                if commit:
                    self._connection.rollback()
                raise
            if commit:
                self._connection.commit()
            row = self._row(checkpoint_id)
        if row is None:
            raise RuntimeError("imported checkpoint domain state did not persist")
        return self._decode(row, include_state=False)

    def restore_checkpoint(
        self,
        *,
        checkpoint_id: str,
        root_frame_id: str,
        project_id: str,
        commit: bool = True,
    ) -> dict[str, Any]:
        """Replace live projections with one verified immutable snapshot."""

        checkpoint_id = self._text("checkpoint_id", checkpoint_id)
        root_frame_id = self._text("root_frame_id", root_frame_id)
        project_id = self._text("project_id", project_id)
        with self._lock:
            row = self._row(checkpoint_id)
            if row is None:
                return self.unavailable_projection(checkpoint_id)
            if row["root_frame_id"] != root_frame_id:
                raise ValueError("checkpoint domain state belongs to another session")
            if row["project_id"] != project_id:
                raise ValueError("checkpoint domain state belongs to another project")
            state = self._validated_state(row)
            self._validate_scope(state, root_frame_id, project_id)
            try:
                self._restore_plans(state["plans"], root_frame_id)
                review = state["review"]
                self._restore_review_steps(review["steps"], root_frame_id)
                self._restore_annotations(review["annotations"], root_frame_id)
                self._restore_review_settings(review["settings"], root_frame_id)
                self._restore_memories(state["memory"]["entries"], project_id)
            except Exception:
                if commit:
                    self._connection.rollback()
                raise
            if commit:
                self._connection.commit()
        return {
            "checkpoint_id": checkpoint_id,
            "available": True,
            "applied": True,
            "partial": False,
            "state_sha256": str(row["state_sha256"]),
            "trust_state": str(row["trust_state"] or "local"),
            "plans": {"applied": True, "count": len(state["plans"])},
            "review": {
                "applied": True,
                "step_count": len(state["review"]["steps"]),
                "annotation_count": len(state["review"]["annotations"]),
                "settings_count": sum(
                    1
                    for item in state["review"]["settings"].values()
                    if item.get("present")
                ),
            },
            "memory": {
                "applied": True,
                "count": len(state["memory"]["entries"]),
            },
        }

    @staticmethod
    def unavailable_projection(checkpoint_id: str) -> dict[str, Any]:
        return {
            "checkpoint_id": checkpoint_id,
            "available": False,
            "applied": False,
            "partial": True,
            "reason": "legacy_checkpoint_without_domain_state_snapshot",
            "plans": {"applied": False, "preserved_live_state": True},
            "review": {"applied": False, "preserved_live_state": True},
            "memory": {"applied": False, "preserved_live_state": True},
        }

    def _source_state(self, source: Mapping[str, Any]) -> dict[str, Any]:
        if int(source.get("schema_version") or 0) != SCHEMA_VERSION:
            raise ValueError("unsupported imported checkpoint state version")
        raw_state = source.get("state")
        if isinstance(raw_state, Mapping):
            encoded = _canonical_json(dict(raw_state))
        elif isinstance(source.get("state_json"), str):
            try:
                decoded = json.loads(str(source["state_json"]))
            except (TypeError, ValueError):
                raise ValueError("imported checkpoint state JSON is corrupt") from None
            encoded = _canonical_json(decoded)
        else:
            raise ValueError("imported checkpoint state body is missing")
        expected = str(source.get("state_sha256") or "")
        if len(expected) != 64 or _sha256(encoded) != expected:
            raise ValueError("imported checkpoint state checksum mismatch")
        return self._validated_state(
            {
                "schema_version": SCHEMA_VERSION,
                "state_json": encoded.decode("utf-8"),
                "state_sha256": expected,
            }
        )

    def _remap_import_state(
        self,
        source: Mapping[str, Any],
        *,
        root_frame_id: str,
        project_id: str,
        identity_seed: str,
        artifact_id_map: Mapping[str, str],
        identity_replacements: Mapping[str, str],
    ) -> tuple[dict[str, Any], set[str]]:
        plan_map = self._derived_identity_map(
            source["plans"],
            key="plan_id",
            prefix="plan-i-",
            kind="plan",
            seed=identity_seed,
        )
        review = source["review"]
        step_map = self._derived_identity_map(
            review["steps"],
            key="step_id",
            prefix="review-i-",
            kind="review-step",
            seed=identity_seed,
        )
        annotation_map = self._derived_identity_map(
            review["annotations"],
            key="annotation_id",
            prefix="ann_i_",
            kind="annotation",
            seed=identity_seed,
        )
        memory_map = self._derived_identity_map(
            source["memory"]["entries"],
            key="memory_id",
            prefix="mem_i_",
            kind="memory",
            seed=identity_seed,
        )
        if any(old == new for old, new in artifact_id_map.items()):
            raise ValueError("imported Artifact identity was not remapped")
        replacements = {
            **dict(identity_replacements),
            **dict(artifact_id_map),
            **plan_map,
            **step_map,
            **annotation_map,
            **memory_map,
        }
        old_ids = set(replacements)

        plans = []
        for raw in source["plans"]:
            old_plan_id = str(raw["plan_id"])
            item = self._remap_refs(dict(raw), replacements)
            item["plan_id"] = plan_map[old_plan_id]
            item["frame_id"] = root_frame_id
            item["project_id"] = project_id
            old_artifact = str(raw.get("artifact_id") or "")
            item["artifact_id"] = artifact_id_map.get(old_artifact)
            plans.append(item)

        review_steps = []
        for raw in review["steps"]:
            old_step_id = str(raw["step_id"])
            item = self._remap_refs(dict(raw), replacements)
            item["step_id"] = step_map[old_step_id]
            item["frame_id"] = root_frame_id
            if str(item.get("kind") or "").casefold() == "review_settings":
                for key in ("input", "output"):
                    value = item.get(key)
                    payload = dict(value) if isinstance(value, Mapping) else {}
                    payload.update(
                        {
                            "active": False,
                            "reason": "quarantined_session_import",
                        }
                    )
                    if key == "input" and "requested_auto_review" in payload:
                        payload["requested_auto_review"] = False
                    item[key] = payload
            review_steps.append(item)

        annotations = []
        for raw in review["annotations"]:
            old_artifact = str(raw.get("artifact_id") or "")
            mapped_artifact = artifact_id_map.get(old_artifact)
            if not mapped_artifact:
                continue
            old_annotation_id = str(raw["annotation_id"])
            item = self._remap_refs(dict(raw), replacements)
            item["annotation_id"] = annotation_map[old_annotation_id]
            item["root_frame_id"] = root_frame_id
            item["artifact_id"] = mapped_artifact
            annotations.append(item)

        memories = []
        for raw in source["memory"]["entries"]:
            old_memory_id = str(raw["memory_id"])
            item = self._remap_refs(dict(raw), replacements)
            item["memory_id"] = memory_map[old_memory_id]
            item["project_id"] = project_id
            memories.append(item)

        now = self._clock_ms()
        return (
            {
                "version": SCHEMA_VERSION,
                "plans": plans,
                "review": {
                    "steps": review_steps,
                    "annotations": annotations,
                    "settings": {
                        "auto_review": {
                            "present": True,
                            "value": "0",
                            "updated_at": now,
                        },
                        "reviewer_model": {
                            "present": False,
                            "value": None,
                            "updated_at": None,
                        },
                    },
                },
                "memory": {"project_id": project_id, "entries": memories},
            },
            old_ids,
        )

    @staticmethod
    def _derived_identity_map(
        rows: Sequence[Mapping[str, Any]],
        *,
        key: str,
        prefix: str,
        kind: str,
        seed: str,
    ) -> dict[str, str]:
        output: dict[str, str] = {}
        for row in rows:
            old = str(row.get(key) or "")
            if not old or old in output:
                raise ValueError(f"imported checkpoint has invalid {kind} identities")
            digest = hashlib.sha256(
                f"{seed}\0{kind}\0{old}".encode("utf-8")
            ).hexdigest()[:20]
            output[old] = f"{prefix}{digest}"
        return output

    @classmethod
    def _remap_refs(cls, value: Any, replacements: Mapping[str, str]) -> Any:
        if isinstance(value, str):
            return replacements.get(value, value)
        if isinstance(value, list):
            return [cls._remap_refs(item, replacements) for item in value]
        if isinstance(value, Mapping):
            return {
                replacements.get(str(key), str(key)): cls._remap_refs(
                    item, replacements
                )
                for key, item in value.items()
            }
        return value

    @classmethod
    def _identity_residuals(
        cls,
        value: Any,
        *,
        key: str | None = None,
    ) -> set[str]:
        found: set[str] = set()
        if isinstance(value, Mapping):
            for child_key, child in value.items():
                name = str(child_key)
                found.update(cls._identity_residuals(child, key=name))
        elif isinstance(value, list):
            for child in value:
                found.update(cls._identity_residuals(child, key=key))
        elif isinstance(value, str) and key is not None:
            normalized = key.casefold()
            if normalized.endswith("_id") or normalized.endswith("_ids"):
                found.add(value)
        return found

    def _validate_import_artifacts(
        self, state: Mapping[str, Any], root_frame_id: str
    ) -> None:
        artifact_ids = {
            str(plan.get("artifact_id"))
            for plan in state["plans"]
            if plan.get("artifact_id")
        }
        artifact_ids.update(
            str(annotation.get("artifact_id"))
            for annotation in state["review"]["annotations"]
            if annotation.get("artifact_id")
        )
        for artifact_id in sorted(artifact_ids):
            row = self._connection.execute(
                "SELECT root_frame_id FROM artifacts WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()
            if row is None or row["root_frame_id"] != root_frame_id:
                raise ValueError("imported checkpoint Artifact mapping is unavailable")

    def _capture_state(self, root_frame_id: str, project_id: str) -> dict[str, Any]:
        plans = [
            self._plan_row(row)
            for row in self._bounded_rows(
                "SELECT * FROM plans WHERE frame_id=? "
                "ORDER BY created_at,plan_id LIMIT ?",
                (root_frame_id, MAX_PLANS + 1),
                limit=MAX_PLANS,
                label="plans",
            )
        ]
        review_steps = [
            self._review_step_row(row)
            for row in self._bounded_rows(
                "SELECT * FROM frame_steps WHERE frame_id=? "
                "AND lower(kind) IN (?,?) ORDER BY seq,step_id LIMIT ?",
                (root_frame_id, *_REVIEW_KINDS, MAX_REVIEW_STEPS + 1),
                limit=MAX_REVIEW_STEPS,
                label="review steps",
            )
        ]
        annotations = [
            dict(row)
            for row in self._bounded_rows(
                "SELECT * FROM annotations WHERE root_frame_id=? "
                "ORDER BY created_at,annotation_id LIMIT ?",
                (root_frame_id, MAX_ANNOTATIONS + 1),
                limit=MAX_ANNOTATIONS,
                label="review annotations",
            )
        ]
        memories = [
            dict(row)
            for row in self._bounded_rows(
                "SELECT memory_id,project_id,block,content,created_at FROM memories "
                "WHERE project_id=? ORDER BY created_at,memory_id LIMIT ?",
                (project_id, MAX_MEMORIES + 1),
                limit=MAX_MEMORIES,
                label="memories",
            )
        ]
        setting_keys = {
            name: template.format(root_frame_id=root_frame_id)
            for name, template in _REVIEW_SETTING_NAMES.items()
        }
        rows = self._connection.execute(
            "SELECT key,value,updated_at FROM settings WHERE key IN (?,?)",
            tuple(setting_keys.values()),
        ).fetchall()
        settings_by_key = {str(row["key"]): dict(row) for row in rows}
        settings = {}
        for name, key in setting_keys.items():
            row = settings_by_key.get(key)
            settings[name] = {
                "present": row is not None,
                "value": row.get("value") if row else None,
                "updated_at": row.get("updated_at") if row else None,
            }
        return {
            "version": SCHEMA_VERSION,
            "plans": plans,
            "review": {
                "steps": review_steps,
                "annotations": annotations,
                "settings": settings,
            },
            "memory": {"project_id": project_id, "entries": memories},
        }

    def _bounded_rows(
        self,
        sql: str,
        params: Sequence[Any],
        *,
        limit: int,
        label: str,
    ) -> list[sqlite3.Row]:
        rows = self._connection.execute(sql, tuple(params)).fetchall()
        if len(rows) > limit:
            raise ValueError(f"checkpoint has too many {label} to snapshot safely")
        return list(rows)

    @staticmethod
    def _plan_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["steps"] = _json_value(item.get("steps"), [])
        item["step_status"] = _json_value(item.get("step_status"), {})
        if not isinstance(item["steps"], list):
            item["steps"] = []
        if not isinstance(item["step_status"], Mapping):
            item["step_status"] = {}
        return item

    @staticmethod
    def _review_step_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for key in ("input", "output"):
            item[key] = _json_value(item.get(key), None)
        return item

    def _validated_state(self, row: Mapping[str, Any]) -> dict[str, Any]:
        if int(row["schema_version"] or 0) != SCHEMA_VERSION:
            raise ValueError("unsupported checkpoint domain state version")
        try:
            state = json.loads(str(row["state_json"]))
        except (TypeError, ValueError):
            raise ValueError("checkpoint domain state JSON is corrupt") from None
        if not isinstance(state, dict) or state.get("version") != SCHEMA_VERSION:
            raise ValueError("checkpoint domain state body is invalid")
        encoded = _canonical_json(state)
        if len(encoded) > MAX_STATE_BYTES:
            raise ValueError("checkpoint domain state exceeds the size limit")
        if _sha256(encoded) != row["state_sha256"]:
            raise ValueError("checkpoint domain state checksum mismatch")
        plans = state.get("plans")
        review = state.get("review")
        memory = state.get("memory")
        if not isinstance(plans, list) or len(plans) > MAX_PLANS:
            raise ValueError("checkpoint plan snapshot is invalid")
        if not isinstance(review, Mapping):
            raise ValueError("checkpoint review snapshot is invalid")
        if (
            not isinstance(review.get("steps"), list)
            or len(review["steps"]) > MAX_REVIEW_STEPS
        ):
            raise ValueError("checkpoint review steps snapshot is invalid")
        if (
            not isinstance(review.get("annotations"), list)
            or len(review["annotations"]) > MAX_ANNOTATIONS
        ):
            raise ValueError("checkpoint review annotations snapshot is invalid")
        if not isinstance(review.get("settings"), Mapping):
            raise ValueError("checkpoint review settings snapshot is invalid")
        if not isinstance(memory, Mapping):
            raise ValueError("checkpoint memory snapshot is invalid")
        if (
            not isinstance(memory.get("entries"), list)
            or len(memory["entries"]) > MAX_MEMORIES
        ):
            raise ValueError("checkpoint memory entries snapshot is invalid")
        return state

    @staticmethod
    def _validate_scope(
        state: Mapping[str, Any], root_frame_id: str, project_id: str
    ) -> None:
        seen: dict[str, set[str]] = {
            "plans": set(),
            "review steps": set(),
            "annotations": set(),
            "memories": set(),
        }

        def unique(bucket: str, value: Any) -> str:
            text = str(value or "")
            if not text or text in seen[bucket]:
                raise ValueError(f"checkpoint {bucket} contain invalid identities")
            seen[bucket].add(text)
            return text

        for plan in state["plans"]:
            if not isinstance(plan, Mapping):
                raise ValueError("checkpoint plan row is invalid")
            unique("plans", plan.get("plan_id"))
            if (
                plan.get("frame_id") != root_frame_id
                or plan.get("project_id") != project_id
            ):
                raise ValueError("checkpoint plan row escaped its session scope")
        review = state["review"]
        for step in review["steps"]:
            if not isinstance(step, Mapping):
                raise ValueError("checkpoint review step row is invalid")
            unique("review steps", step.get("step_id"))
            if (
                step.get("frame_id") != root_frame_id
                or str(step.get("kind") or "").casefold() not in _REVIEW_KINDS
            ):
                raise ValueError("checkpoint review step escaped its session scope")
        for annotation in review["annotations"]:
            if not isinstance(annotation, Mapping):
                raise ValueError("checkpoint annotation row is invalid")
            unique("annotations", annotation.get("annotation_id"))
            if annotation.get("root_frame_id") != root_frame_id:
                raise ValueError("checkpoint annotation escaped its session scope")
        memory = state["memory"]
        if memory.get("project_id") != project_id:
            raise ValueError("checkpoint memory snapshot escaped its project scope")
        for item in memory["entries"]:
            if not isinstance(item, Mapping):
                raise ValueError("checkpoint memory row is invalid")
            unique("memories", item.get("memory_id"))
            if item.get("project_id") != project_id:
                raise ValueError("checkpoint memory row escaped its project scope")
        for name in _REVIEW_SETTING_NAMES:
            item = review["settings"].get(name)
            if not isinstance(item, Mapping) or not isinstance(
                item.get("present"), bool
            ):
                raise ValueError("checkpoint review setting is invalid")

    def _restore_plans(self, plans: list[dict[str, Any]], root_frame_id: str) -> None:
        self._reject_foreign_ids("plans", "plan_id", "frame_id", root_frame_id, plans)
        self._connection.execute("DELETE FROM plans WHERE frame_id=?", (root_frame_id,))
        for item in plans:
            self._connection.execute(
                "INSERT INTO plans(plan_id,frame_id,project_id,title,rationale,"
                "confidence,steps,status,step_status,artifact_id,created_at,"
                "updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    item["plan_id"],
                    root_frame_id,
                    item["project_id"],
                    item.get("title"),
                    item.get("rationale"),
                    item.get("confidence"),
                    _canonical_json(item.get("steps") or []).decode("utf-8"),
                    item.get("status") or "draft",
                    _canonical_json(item.get("step_status") or {}).decode("utf-8"),
                    item.get("artifact_id"),
                    int(item.get("created_at") or 0),
                    int(item.get("updated_at") or 0),
                ),
            )

    def _restore_review_steps(
        self, steps: list[dict[str, Any]], root_frame_id: str
    ) -> None:
        self._reject_foreign_ids(
            "frame_steps", "step_id", "frame_id", root_frame_id, steps
        )
        self._connection.execute(
            "DELETE FROM frame_steps WHERE frame_id=? AND lower(kind) IN (?,?)",
            (root_frame_id, *_REVIEW_KINDS),
        )
        for item in steps:
            self._connection.execute(
                "INSERT INTO frame_steps(step_id,frame_id,seq,kind,title,summary,"
                "input,output,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    item["step_id"],
                    root_frame_id,
                    int(item.get("seq") or 0),
                    str(item.get("kind") or "review"),
                    item.get("title"),
                    item.get("summary"),
                    self._optional_json(item.get("input")),
                    self._optional_json(item.get("output")),
                    item.get("status"),
                    int(item.get("created_at") or 0),
                    int(item.get("updated_at") or item.get("created_at") or 0),
                ),
            )

    def _restore_annotations(
        self, annotations: list[dict[str, Any]], root_frame_id: str
    ) -> None:
        self._reject_foreign_ids(
            "annotations",
            "annotation_id",
            "root_frame_id",
            root_frame_id,
            annotations,
        )
        self._connection.execute(
            "DELETE FROM annotations WHERE root_frame_id=?", (root_frame_id,)
        )
        for item in annotations:
            artifact = self._connection.execute(
                "SELECT root_frame_id FROM artifacts WHERE artifact_id=?",
                (item.get("artifact_id"),),
            ).fetchone()
            if artifact is None or artifact["root_frame_id"] != root_frame_id:
                raise ValueError("checkpoint annotation Artifact is unavailable")
            self._connection.execute(
                "INSERT INTO annotations(annotation_id,root_frame_id,artifact_id,"
                "artifact_name,rel_x,rel_y,number,body,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    item["annotation_id"],
                    root_frame_id,
                    item["artifact_id"],
                    item.get("artifact_name"),
                    float(item.get("rel_x") or 0),
                    float(item.get("rel_y") or 0),
                    int(item.get("number") or 0),
                    str(item.get("body") or ""),
                    str(item.get("status") or "open"),
                    int(item.get("created_at") or 0),
                    int(item.get("updated_at") or item.get("created_at") or 0),
                ),
            )

    def _restore_review_settings(
        self, settings: Mapping[str, Any], root_frame_id: str
    ) -> None:
        for name, template in _REVIEW_SETTING_NAMES.items():
            key = template.format(root_frame_id=root_frame_id)
            self._connection.execute("DELETE FROM settings WHERE key=?", (key,))
            item = settings[name]
            if item.get("present"):
                self._connection.execute(
                    "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?)",
                    (
                        key,
                        item.get("value"),
                        int(item.get("updated_at") or self._clock_ms()),
                    ),
                )

    def _restore_memories(
        self, memories: list[dict[str, Any]], project_id: str
    ) -> None:
        self._reject_foreign_ids(
            "memories", "memory_id", "project_id", project_id, memories
        )
        self._connection.execute(
            "DELETE FROM memories WHERE project_id=?", (project_id,)
        )
        for item in memories:
            self._connection.execute(
                "INSERT INTO memories(memory_id,project_id,block,content,created_at) "
                "VALUES(?,?,?,?,?)",
                (
                    item["memory_id"],
                    project_id,
                    item.get("block"),
                    item.get("content"),
                    int(item.get("created_at") or 0),
                ),
            )

    def _reject_foreign_ids(
        self,
        table: str,
        id_column: str,
        scope_column: str,
        scope_id: str,
        rows: Sequence[Mapping[str, Any]],
    ) -> None:
        ids = [str(item[id_column]) for item in rows]
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        foreign = self._connection.execute(
            f"SELECT {id_column} FROM {table} WHERE {id_column} IN ({placeholders}) "
            f"AND {scope_column}!=? LIMIT 1",
            (*ids, scope_id),
        ).fetchone()
        if foreign is not None:
            raise ValueError(f"checkpoint {table} identity belongs to another scope")

    @staticmethod
    def _optional_json(value: Any) -> str | None:
        if value is None:
            return None
        return _canonical_json(value).decode("utf-8")

    def _row(self, checkpoint_id: str) -> sqlite3.Row | None:
        return self._connection.execute(
            "SELECT * FROM checkpoint_state_snapshots WHERE checkpoint_id=?",
            (checkpoint_id,),
        ).fetchone()

    def _decode(self, row: sqlite3.Row, *, include_state: bool) -> dict[str, Any]:
        state = self._validated_state(row)
        result = {
            key: row[key]
            for key in (
                "checkpoint_id",
                "root_frame_id",
                "branch_id",
                "project_id",
                "schema_version",
                "state_sha256",
                "source_checkpoint_id",
                "trust_state",
                "import_source_sha256",
                "created_at",
            )
        }
        result["counts"] = {
            "plans": len(state["plans"]),
            "review_steps": len(state["review"]["steps"]),
            "annotations": len(state["review"]["annotations"]),
            "memories": len(state["memory"]["entries"]),
        }
        result["available"] = True
        result["restorable"] = True
        result["quarantined"] = result["trust_state"] != "local"
        result["contains_bodies"] = bool(include_state)
        if include_state:
            result["state"] = state
        return result

    @staticmethod
    def _text(name: str, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
        return value


__all__ = [
    "CHECKPOINT_STATE_SCHEMA",
    "CheckpointStateRepository",
    "SCHEMA_VERSION",
]
