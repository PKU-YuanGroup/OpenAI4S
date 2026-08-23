"""Small, cohesive metadata repositories on a Store-owned SQLite connection."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from typing import Any, Callable, Mapping

# Credential reads are derivable and must never be duplicated in the audit log.
DERIVABLE_HOST_CALLS = frozenset(
    {
        "credentials_get",
        "credentials_issue",
        "credentials_redeem",
        "credentials_list",
    }
)

# Secret-bearing RPCs remain auditable by method name, but their raw arguments
# do not cross the persistence boundary.
SECRET_ARG_HOST_CALLS = frozenset(
    {
        "credentials_set",
        # Shell authorization carries the raw command and worker-reported
        # output.  A separate synthetic ``bash`` audit entry contains only the
        # bounded/redacted projection produced by BashAuthorizationService.
        "authorize_bash",
        "consume_bash_authorization",
        "record_bash_result",
    }
)


class NotesRepository:
    """Persist project notes and expose their legacy API projection."""

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

    def add(
        self,
        *,
        project_id: str,
        content: str,
        title: str | None = None,
    ) -> dict:
        now = self._clock_ms()
        note_id = f"note_{uuid.uuid4().hex[:12]}"
        self._execute(
            "INSERT INTO notes(note_id,project_id,title,body,created_at) "
            "VALUES(?,?,?,?,?)",
            (note_id, project_id, title, content, now),
        )
        return {
            "note_id": note_id,
            "project_id": project_id,
            "content": content,
            "created_at": now,
            "updated_at": now,
        }

    def list(self, project_id: str) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT note_id,project_id,title,body,created_at FROM notes "
                "WHERE project_id=? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        return [
            {
                "note_id": row["note_id"],
                "project_id": row["project_id"],
                "content": row["body"],
                "title": row["title"],
                "created_at": row["created_at"],
                "updated_at": row["created_at"],
            }
            for row in rows
        ]

    def project_of(self, note_id: str) -> str | None:
        """Which project owns a note. A note is addressed by its own id, so a
        route that only has that id cannot ask the project guard anything
        without this."""
        with self._lock:
            row = self._connection.execute(
                "SELECT project_id FROM notes WHERE note_id=?", (note_id,)
            ).fetchone()
        return str(row["project_id"]) if row and row["project_id"] else None

    def delete(self, note_id: str) -> None:
        self._execute("DELETE FROM notes WHERE note_id=?", (note_id,))

    def _execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._connection.execute(sql, params)
            self._connection.commit()


class FolderRepository:
    """Persist project folders and frame-to-folder assignments."""

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

    def create(self, *, project_id: str, name: str) -> dict:
        now = self._clock_ms()
        folder_id = f"fold_{uuid.uuid4().hex[:10]}"
        self._execute(
            "INSERT INTO folders(folder_id,project_id,name,created_at) "
            "VALUES(?,?,?,?)",
            (folder_id, project_id, name, now),
        )
        return {
            "folder_id": folder_id,
            "project_id": project_id,
            "name": name,
            "created_at": now,
        }

    def list(self, project_id: str) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT folder_id,project_id,name,created_at FROM folders "
                "WHERE project_id=? ORDER BY name",
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def project_of(self, folder_id: str) -> str | None:
        """Which project owns a folder — see `NoteRepository.project_of`."""
        with self._lock:
            row = self._connection.execute(
                "SELECT project_id FROM folders WHERE folder_id=?", (folder_id,)
            ).fetchone()
        return str(row["project_id"]) if row and row["project_id"] else None

    def rename(self, folder_id: str, name: str) -> None:
        self._execute(
            "UPDATE folders SET name=? WHERE folder_id=?",
            (name, folder_id),
        )

    def delete(self, folder_id: str) -> None:
        # Keep the historical two-transaction boundary: frames are un-filed and
        # committed before the folder row is removed in a second transaction.
        self._execute(
            "UPDATE frames SET folder_id=NULL WHERE folder_id=?",
            (folder_id,),
        )
        self._execute(
            "DELETE FROM folders WHERE folder_id=?",
            (folder_id,),
        )

    def set_frame_folder(self, frame_id: str, folder_id: str | None) -> None:
        self._execute(
            "UPDATE frames SET folder_id=? WHERE frame_id=?",
            (folder_id, frame_id),
        )

    def _execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._connection.execute(sql, params)
            self._connection.commit()


class EndpointRepository:
    """Persist managed endpoint metadata with legacy dynamic-field upserts."""

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

    def upsert(self, name: str, **fields: Any) -> None:
        now = self._clock_ms()
        with self._lock:
            exists = self._connection.execute(
                "SELECT 1 FROM managed_endpoints WHERE name=?",
                (name,),
            ).fetchone()
            if exists:
                fields["updated_at"] = now
                columns = ", ".join(f"{key}=?" for key in fields)
                self._connection.execute(
                    f"UPDATE managed_endpoints SET {columns} WHERE name=?",
                    (*fields.values(), name),
                )
            else:
                fields.setdefault("created_at", now)
                fields["updated_at"] = now
                fields["name"] = name
                columns = ", ".join(fields)
                placeholders = ", ".join("?" for _ in fields)
                self._connection.execute(
                    f"INSERT INTO managed_endpoints({columns}) "
                    f"VALUES({placeholders})",
                    tuple(fields.values()),
                )
            self._connection.commit()

    def list(self) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM managed_endpoints ORDER BY created_at"
            ).fetchall()
        return [dict(row) for row in rows]


class CompactionRepository:
    """Archive compacted conversation slices for later inspection."""

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

    def archive(
        self,
        *,
        frame_id: str | None,
        summary: str,
        compacted: list[dict],
        project_id: str = "default",
        branch_id: str | None = None,
        ledger_cursor: Any = None,
        recovery_pointer: Any = None,
        generation_id: Any = None,
        metadata: Mapping[str, Any] | None = None,
        handoff: str | None = None,
        context_before: Mapping[str, Any] | None = None,
        context_after: Mapping[str, Any] | None = None,
        artifact_refs: list[dict] | None = None,
    ) -> str:
        archive_id = f"ca-{uuid.uuid4().hex[:12]}"
        self._execute(
            "INSERT INTO compaction_archives("
            "archive_id,frame_id,project_id,branch_id,ledger_cursor,"
            "recovery_pointer,generation_id,metadata,summary,handoff,compacted,"
            "n_messages,context_before,context_after,artifact_refs,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                archive_id,
                frame_id,
                project_id,
                branch_id,
                self._json(ledger_cursor),
                self._json(recovery_pointer),
                None if generation_id is None else str(generation_id),
                self._json(dict(metadata or {})),
                summary,
                handoff,
                json.dumps(compacted, ensure_ascii=False),
                len(compacted),
                self._json(dict(context_before or {})),
                self._json(dict(context_after or {})),
                self._json(list(artifact_refs or [])),
                self._clock_ms(),
            ),
        )
        return archive_id

    def list(self, frame_id: str, *, limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit), 500))
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM compaction_archives WHERE frame_id=? "
                "ORDER BY created_at DESC,archive_id DESC LIMIT ?",
                (frame_id, limit),
            ).fetchall()
        result: list[dict] = []
        for row in rows:
            item = dict(row)
            for key in (
                "ledger_cursor",
                "recovery_pointer",
                "metadata",
                "context_before",
                "context_after",
                "artifact_refs",
            ):
                try:
                    item[key] = json.loads(item.get(key) or "null")
                except (TypeError, ValueError):
                    item[key] = None
            try:
                item["compacted"] = json.loads(item.get("compacted") or "[]")
            except (TypeError, ValueError):
                item["compacted"] = []
            result.append(item)
        return result

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def _execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._connection.execute(sql, params)
            self._connection.commit()


class HostCallRepository:
    """Persist scrubbed host-RPC audit records."""

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

    def log(
        self,
        *,
        method: str,
        args: list,
        ok: bool,
        frame_id: str | None = None,
        result: Any = None,
        action_group_id: str | None = None,
        action_id: str | None = None,
        permission_decision_id: str | None = None,
        side_effect_class: str | None = None,
        resource_keys: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        if method in DERIVABLE_HOST_CALLS:
            return
        if method in SECRET_ARG_HOST_CALLS:
            preview = "<redacted secret args>"
        else:
            try:
                preview = json.dumps(args, ensure_ascii=False)[:500]
            except (TypeError, ValueError):
                preview = "<unserializable>"
        result_preview, result_digest = self._result_audit(method, result)
        self._execute(
            "INSERT INTO host_call_log("
            "call_id,frame_id,action_group_id,action_id,permission_decision_id,"
            "method,args_preview,result_preview,result_digest,"
            "side_effect_class,resource_keys,ok,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"hc-{uuid.uuid4().hex[:12]}",
                frame_id,
                action_group_id,
                action_id,
                permission_decision_id,
                method,
                preview,
                result_preview,
                result_digest,
                side_effect_class,
                json.dumps(list(resource_keys or ()), separators=(",", ":")),
                1 if ok else 0,
                self._clock_ms(),
            ),
        )

    @staticmethod
    def _result_audit(method: str, result: Any) -> tuple[str, str | None]:
        """Return a bounded shape preview plus a content-integrity digest.

        Raw Host results may contain research data or credential material.  The
        audit row records that an output existed and whether it later changed,
        without becoming a second plaintext data store.
        """

        if method in SECRET_ARG_HOST_CALLS:
            return "<redacted secret-bearing result>", None
        try:
            encoded = json.dumps(
                result,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=repr,
            )
            digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        except Exception:  # noqa: BLE001 - audit must never break execution
            encoded = repr(result)
            digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        if isinstance(result, dict):
            shape = {
                "type": "object",
                "keys": sorted(str(key)[:80] for key in result)[:64],
                "size": len(result),
                "error": bool(result.get("error")),
            }
        elif isinstance(result, (list, tuple)):
            shape = {"type": "array", "size": len(result)}
        elif isinstance(result, str):
            shape = {"type": "string", "length": len(result)}
        elif result is None:
            shape = {"type": "null"}
        else:
            shape = {"type": type(result).__name__}
        return json.dumps(shape, separators=(",", ":")), digest

    def _execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._connection.execute(sql, params)
            self._connection.commit()


__all__ = [
    "CompactionRepository",
    "DERIVABLE_HOST_CALLS",
    "EndpointRepository",
    "FolderRepository",
    "HostCallRepository",
    "NotesRepository",
    "SECRET_ARG_HOST_CALLS",
]
