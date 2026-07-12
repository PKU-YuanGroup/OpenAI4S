"""Atomic durable projection for activating one checkpoint branch.

Branch activation changes several pieces of *conversation-scoped* current
state together: the selected branch, session capability overrides,
conversation permission rules, the visible Artifact-version heads, and the
selected Python environment.  Keeping that transaction in one repository
prevents a daemon crash from publishing a branch id whose surrounding policy
and data projections still describe another branch.

Project/global capability and permission rows are deliberately never touched.
They remain live organization policy layered below the checkpoint's restored
conversation overrides.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, Callable

ACTIVATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS session_branch_selection (
    root_frame_id       TEXT PRIMARY KEY,
    current_branch_id   TEXT NOT NULL,
    updated_at          INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_session_branch_selection_branch
    ON session_branch_selection(current_branch_id);
"""


class SessionActivationRepository:
    """Publish the active checkpoint projection in one SQLite transaction."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        lock: Any,
        *,
        clock_ms: Callable[[], int],
        checkpoint_state: Any | None = None,
    ) -> None:
        self._connection = connection
        self._lock = lock
        self._clock_ms = clock_ms
        self._checkpoint_state = checkpoint_state
        with self._lock:
            self._connection.executescript(ACTIVATION_SCHEMA)
            self._connection.commit()

    def ensure(self, root_frame_id: str) -> str:
        """Persist the canonical root as the initial active branch."""

        root_frame_id = self._text("root_frame_id", root_frame_id)
        now = self._clock_ms()
        with self._lock:
            frame = self._connection.execute(
                "SELECT frame_id,root_frame_id FROM frames WHERE frame_id=?",
                (root_frame_id,),
            ).fetchone()
            if (
                frame is None
                or (frame["root_frame_id"] or root_frame_id) != root_frame_id
            ):
                raise ValueError("active branch selection requires a root frame")
            self._connection.execute(
                "INSERT OR IGNORE INTO session_branches("
                "branch_id,root_frame_id,created_at,updated_at) VALUES(?,?,?,?)",
                (root_frame_id, root_frame_id, now, now),
            )
            self._connection.execute(
                "INSERT OR IGNORE INTO session_branch_selection("
                "root_frame_id,current_branch_id,updated_at) VALUES(?,?,?)",
                (root_frame_id, root_frame_id, now),
            )
            self._connection.commit()
            row = self._connection.execute(
                "SELECT current_branch_id FROM session_branch_selection "
                "WHERE root_frame_id=?",
                (root_frame_id,),
            ).fetchone()
        return str(row["current_branch_id"])

    def current(self, root_frame_id: str) -> str:
        root_frame_id = self._text("root_frame_id", root_frame_id)
        with self._lock:
            row = self._connection.execute(
                "SELECT current_branch_id FROM session_branch_selection "
                "WHERE root_frame_id=?",
                (root_frame_id,),
            ).fetchone()
        return str(row["current_branch_id"]) if row else root_frame_id

    def activate_checkpoint(
        self,
        *,
        root_frame_id: str,
        branch_id: str,
        checkpoint_id: str,
        expected_current_branch_id: str | None = None,
    ) -> dict[str, Any]:
        """Restore checkpoint-backed current projections and select its branch.

        All target records are validated before the first mutation.  A failure
        therefore leaves the previously active branch and its projections
        untouched.
        """

        root_frame_id = self._text("root_frame_id", root_frame_id)
        branch_id = self._text("branch_id", branch_id)
        checkpoint_id = self._text("checkpoint_id", checkpoint_id)
        now = self._clock_ms()
        with self._lock:
            branch = self._connection.execute(
                "SELECT * FROM session_branches WHERE branch_id=?",
                (branch_id,),
            ).fetchone()
            if branch is None or branch["root_frame_id"] != root_frame_id:
                raise KeyError(f"unknown branch {branch_id!r} for this session")
            checkpoint = self._connection.execute(
                "SELECT * FROM session_checkpoints WHERE checkpoint_id=?",
                (checkpoint_id,),
            ).fetchone()
            if (
                checkpoint is None
                or checkpoint["root_frame_id"] != root_frame_id
                or branch["head_checkpoint_id"] != checkpoint_id
            ):
                raise ValueError(
                    "activation requires the selected branch head checkpoint"
                )

            current = self._connection.execute(
                "SELECT current_branch_id FROM session_branch_selection "
                "WHERE root_frame_id=?",
                (root_frame_id,),
            ).fetchone()
            previous_branch = (
                str(current["current_branch_id"]) if current else root_frame_id
            )
            if (
                expected_current_branch_id is not None
                and previous_branch != expected_current_branch_id
            ):
                raise RuntimeError(
                    "active branch changed: expected "
                    f"{expected_current_branch_id!r}, got {previous_branch!r}"
                )

            capabilities = self._json(checkpoint["capability_state"], {})
            permission_state = self._json(checkpoint["permission_state"], {})
            environment_pins = self._json(checkpoint["environment_pins"], {})
            artifact_versions = self._json(checkpoint["artifact_versions"], [])
            frame = self._connection.execute(
                "SELECT project_id FROM frames WHERE frame_id=?",
                (root_frame_id,),
            ).fetchone()
            project_id = str((frame or {})["project_id"] if frame else "default")
            session_capabilities = self._session_capabilities(
                capabilities, root_frame_id, project_id
            )
            conversation_rules = self._conversation_rules(
                permission_state, root_frame_id
            )
            version_heads = self._artifact_heads(root_frame_id, artifact_versions)
            python_env = environment_pins.get("python")
            if python_env is not None and not isinstance(python_env, str):
                raise ValueError("checkpoint Python environment pin is invalid")

            state_projection: dict[str, Any] | None = None
            try:
                if self._checkpoint_state is not None:
                    state_projection = self._checkpoint_state.restore_checkpoint(
                        checkpoint_id=checkpoint_id,
                        root_frame_id=root_frame_id,
                        project_id=project_id,
                        commit=False,
                    )
                self._connection.execute(
                    "DELETE FROM capability_states WHERE scope='session' "
                    "AND scope_id=?",
                    (root_frame_id,),
                )
                for item in session_capabilities:
                    encoded = json.dumps(
                        item["metadata"], sort_keys=True, separators=(",", ":")
                    )
                    normalized = item["name"].casefold()
                    self._connection.execute(
                        "INSERT INTO capability_states("
                        "kind,name,normalized_name,scope,scope_id,enabled,metadata,"
                        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            item["kind"],
                            item["name"],
                            normalized,
                            "session",
                            root_frame_id,
                            1 if item["enabled"] else 0,
                            encoded,
                            now,
                            now,
                        ),
                    )
                    self._connection.execute(
                        "INSERT INTO capability_events("
                        "event_id,kind,name,normalized_name,scope,scope_id,event,"
                        "enabled,metadata,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (
                            f"ce-{uuid.uuid4().hex}",
                            item["kind"],
                            item["name"],
                            normalized,
                            "session",
                            root_frame_id,
                            "checkpoint_restored",
                            1 if item["enabled"] else 0,
                            encoded,
                            now,
                        ),
                    )

                self._connection.execute(
                    "DELETE FROM permission_rules WHERE scope='conversation' "
                    "AND scope_id=?",
                    (root_frame_id,),
                )
                for item in conversation_rules:
                    self._connection.execute(
                        "INSERT INTO permission_rules("
                        "rule_id,scope,scope_id,tool,pattern,decision,created_at,"
                        "updated_at) VALUES(?,?,?,?,?,?,?,?)",
                        (
                            f"perm_{uuid.uuid4().hex[:12]}",
                            "conversation",
                            root_frame_id,
                            item["tool"],
                            item["pattern"],
                            item["decision"],
                            now,
                            now,
                        ),
                    )

                self._connection.execute(
                    "UPDATE artifacts SET latest_version_id=NULL,updated_at=? "
                    "WHERE root_frame_id=?",
                    (now, root_frame_id),
                )
                for artifact_id, version_id in version_heads.items():
                    self._connection.execute(
                        "UPDATE artifacts SET latest_version_id=?,updated_at=? "
                        "WHERE artifact_id=? AND root_frame_id=?",
                        (version_id, now, artifact_id, root_frame_id),
                    )

                self._connection.execute(
                    "UPDATE frames SET runtime_env=?,updated_at=? WHERE frame_id=?",
                    (python_env, now, root_frame_id),
                )
                self._connection.execute(
                    "INSERT INTO session_branch_selection("
                    "root_frame_id,current_branch_id,updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(root_frame_id) DO UPDATE SET "
                    "current_branch_id=excluded.current_branch_id,"
                    "updated_at=excluded.updated_at",
                    (root_frame_id, branch_id, now),
                )
            except Exception:
                self._connection.rollback()
                raise
            self._connection.commit()
        result = {
            "root_frame_id": root_frame_id,
            "previous_branch_id": previous_branch,
            "current_branch_id": branch_id,
            "checkpoint_id": checkpoint_id,
            "environment": {
                "python": python_env,
                "applied": True,
            },
            "artifacts": {
                "applied": True,
                "version_count": len(version_heads),
            },
            "capabilities": {
                "applied": True,
                "session_override_count": len(session_capabilities),
            },
            "permissions": {
                "applied": True,
                "conversation_rule_count": len(conversation_rules),
            },
        }
        if state_projection is not None:
            result["session_state"] = state_projection
            result["partial"] = bool(state_projection.get("partial"))
        return result

    def _artifact_heads(self, root_frame_id: str, raw_versions: Any) -> dict[str, str]:
        if not isinstance(raw_versions, Sequence) or isinstance(
            raw_versions, (str, bytes)
        ):
            raise ValueError("checkpoint artifact version set is invalid")
        heads: dict[str, str] = {}
        for value in raw_versions:
            version_id = self._text("version_id", str(value))
            row = self._connection.execute(
                "SELECT v.artifact_id,a.root_frame_id FROM artifact_versions AS v "
                "JOIN artifacts AS a ON a.artifact_id=v.artifact_id "
                "WHERE v.version_id=?",
                (version_id,),
            ).fetchone()
            if row is None or row["root_frame_id"] != root_frame_id:
                raise ValueError(
                    f"checkpoint Artifact version is unavailable: {version_id}"
                )
            artifact_id = str(row["artifact_id"])
            if artifact_id in heads and heads[artifact_id] != version_id:
                raise ValueError("checkpoint contains multiple heads for one Artifact")
            heads[artifact_id] = version_id
        return heads

    @staticmethod
    def _session_capabilities(
        raw: Any, root_frame_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        rows = raw.get("states") if isinstance(raw, Mapping) else []
        if not isinstance(rows, list):
            raise ValueError("checkpoint capability snapshot is invalid")
        selected: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
        ranks = {"global": 1, "project": 2, "session": 3}
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError("checkpoint capability row is invalid")
            scope = str(row.get("scope") or "")
            if scope not in ranks:
                continue
            scope_id = str(row.get("scope_id") or "")
            if scope == "session" and scope_id != root_frame_id:
                raise ValueError("checkpoint contains another session's capability")
            if scope == "project" and scope_id != project_id:
                raise ValueError("checkpoint contains another project's capability")
            if scope == "global" and scope_id:
                raise ValueError("checkpoint global capability has an invalid scope id")
            kind = str(row.get("kind") or "").strip().lower()
            name = str(row.get("name") or "").strip()
            if not kind or not name:
                raise ValueError("checkpoint capability row is incomplete")
            metadata = row.get("metadata")
            value = {
                "kind": kind,
                "name": name,
                "enabled": bool(row.get("enabled")),
                "metadata": dict(metadata) if isinstance(metadata, Mapping) else {},
            }
            key = (kind, name.casefold())
            previous = selected.get(key)
            if previous is None or ranks[scope] >= previous[0]:
                selected[key] = (ranks[scope], value)
        # Materialize the checkpoint's effective values as conversation-local
        # overrides. Project/global rows remain untouched and can continue to
        # govern capabilities absent from the frozen snapshot.
        return [selected[key][1] for key in sorted(selected)]

    @staticmethod
    def _conversation_rules(raw: Any, root_frame_id: str) -> list[dict[str, str]]:
        rows = raw.get("conversation") if isinstance(raw, Mapping) else []
        if not isinstance(rows, list):
            raise ValueError("checkpoint permission snapshot is invalid")
        output = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError("checkpoint permission row is invalid")
            if str(row.get("scope_id") or root_frame_id) != root_frame_id:
                raise ValueError(
                    "checkpoint contains another conversation's permission"
                )
            decision = str(row.get("decision") or "").lower()
            if decision not in {"allow", "ask", "deny"}:
                raise ValueError("checkpoint permission decision is invalid")
            output.append(
                {
                    "tool": str(row.get("tool") or "*") or "*",
                    "pattern": str(row.get("pattern") or "*") or "*",
                    "decision": decision,
                }
            )
        return output

    @staticmethod
    def _json(raw: Any, fallback: Any) -> Any:
        if raw in (None, ""):
            return fallback
        try:
            return json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            raise ValueError("checkpoint JSON projection is corrupt") from None

    @staticmethod
    def _text(name: str, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
        return value


__all__ = ["ACTIVATION_SCHEMA", "SessionActivationRepository"]
