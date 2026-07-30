"""Long-term memory persistence on a Store-owned SQLite connection.

Reads are scoped by project, and the cross-project view has to be asked for by
name. It used to be the *fallback*: ``project_id`` of ``None`` meant "no WHERE
clause", so a caller that did not know its scope was handed every project's
memories. The gateway seeded system prompts with
``list_memories(project_id=st.project_id or "all")``, so any session whose
project was falsy silently injected the whole installation's remembered context
-- other people's projects included -- into the model.

Falling open is the wrong direction for a scope boundary: a missing memory is
visible and reported, a leaked one is neither. ``ALL_PROJECTS`` is therefore an
explicit token and ``None`` is refused.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any, Callable

#: The explicit cross-project scope. Spelled out at the call site so that
#: reading every project's memories is always a visible decision.
ALL_PROJECTS = "all"


def _scope(project_id: str | None) -> str:
    if not project_id:
        raise ValueError(
            "memory reads require an explicit project_id; pass "
            f"{ALL_PROJECTS!r} for the deliberate cross-project view. "
            "A missing scope used to mean 'every project', which is how "
            "one session's prompt came to carry another project's memories."
        )
    return project_id


class MemoryRepository:
    """CRUD and category projections for the ``memories`` table."""

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
        content: str,
        block: str = "general",
        project_id: str = "default",
    ) -> dict:
        now = self._clock_ms()
        memory_id = f"mem_{uuid.uuid4().hex[:12]}"
        self._execute(
            "INSERT INTO memories(memory_id,project_id,block,content,created_at) "
            "VALUES(?,?,?,?,?)",
            (memory_id, project_id, block, content, now),
        )
        return {
            "memory_id": memory_id,
            "project_id": project_id,
            "block": block,
            "content": content,
            "created_at": now,
        }

    def list(
        self,
        project_id: str | None = None,
        block: str | None = None,
    ) -> list[dict]:
        project_id = _scope(project_id)
        sql = (
            "SELECT memory_id,project_id,block,content,created_at FROM memories "
            "WHERE 1=1"
        )
        params: list[Any] = []
        if project_id != ALL_PROJECTS:
            sql += " AND project_id=?"
            params.append(project_id)
        if block:
            sql += " AND block=?"
            params.append(block)
        sql += " ORDER BY created_at DESC"
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
        return [
            {
                "memory_id": row["memory_id"],
                "project_id": row["project_id"],
                "block": row["block"],
                "content": row["content"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def delete(self, memory_id: str) -> None:
        self._execute(
            "DELETE FROM memories WHERE memory_id=?",
            (memory_id,),
        )

    def blocks(self, project_id: str | None = None) -> list[dict]:
        project_id = _scope(project_id)
        sql = "SELECT block, COUNT(*) n FROM memories"
        params: list[Any] = []
        if project_id != ALL_PROJECTS:
            sql += " WHERE project_id=?"
            params.append(project_id)
        sql += " GROUP BY block ORDER BY n DESC"
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
        return [{"block": row["block"] or "general", "count": row["n"]} for row in rows]

    def _execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._connection.execute(sql, params)
            self._connection.commit()


__all__ = ["MemoryRepository"]
