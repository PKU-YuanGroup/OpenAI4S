"""Atomic creation of quarantined Session-package roots.

An imported root must never exist without its mutation quarantine.  Ordinary
project/frame/settings facades each commit independently, leaving a process-exit
window between them.  This focused repository creates the safe placeholder
aggregate and its quarantine row in one immediate SQLite transaction; untrusted
package metadata is applied only after that boundary exists.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any, Callable


class SessionImportRepository:
    """Own the first durable transaction of a Session-package import."""

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

    def create_quarantined_root(
        self,
        *,
        project_id: str,
        quarantine_value: str,
    ) -> dict[str, Any]:
        """Create safe project/root placeholders and quarantine atomically."""

        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("import project_id must be a non-empty string")
        if not isinstance(quarantine_value, str) or not quarantine_value:
            raise ValueError("import quarantine value must be non-empty")
        root_frame_id = f"f-{uuid.uuid4().hex[:12]}"
        now = self._clock_ms()
        setting_key = f"session:import-quarantine:{root_frame_id}"
        with self._lock:
            if self._connection.in_transaction:
                raise RuntimeError(
                    "Session import creation requires a clean SQLite transaction"
                )
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    "INSERT INTO projects(project_id,name,description,context,"
                    "is_example,created_at,updated_at) VALUES(?,?,?,?,0,?,?)",
                    (
                        project_id,
                        "Imported Session (quarantined)",
                        "Session package import in progress",
                        "",
                        now,
                        now,
                    ),
                )
                self._connection.execute(
                    "INSERT INTO frames(frame_id,parent_id,project_id,root_frame_id,"
                    "kind,name,model,status,depth,created_at,updated_at) "
                    "VALUES(?,NULL,?,?,?,'Imported session',NULL,'done',0,?,?)",
                    (
                        root_frame_id,
                        project_id,
                        root_frame_id,
                        "turn",
                        now,
                        now,
                    ),
                )
                self._connection.execute(
                    "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?)",
                    (setting_key, quarantine_value, now),
                )
                self._connection.commit()
            except BaseException:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise
        return {
            "project_id": project_id,
            "root_frame_id": root_frame_id,
            "quarantine_key": setting_key,
        }


__all__ = ["SessionImportRepository"]
