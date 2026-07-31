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

Two tiers, not one flat column
------------------------------
``project_id`` also carries the reserved value ``GLOBAL_SCOPE``: a memory every
project inherits. Without it the column was flat, and the Memory pane -- which
offered no scope control -- wrote everything to the literal ``"default"``, a
project nothing on this installation creates, while injection reads the
session's real ``proj_*``. Saving a memory therefore succeeded, listed, and was
never once injected. A tier a project can inherit *and* override is what makes
"remember this everywhere" and "here, not there" expressible at all.

Override is per block, because a block is the unit a person names when they say
what a memory is *about*. A project that has said anything about ``style``
speaks for ``style`` there, and the global ``style`` entries stand down instead
of being merged into contradictory background. The counts come back from
``resolve`` so an inherited item that stopped arriving is visible, rather than
being discovered by noticing the agent forgot something.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any, Callable

from openai4s.memory_budget import MAX_MEMORIES_PER_SCOPE, MAX_MEMORY_CHARS

#: The explicit cross-project scope. Spelled out at the call site so that
#: reading every project's memories is always a visible decision.
ALL_PROJECTS = "all"

#: The tier every project inherits from. A reserved ``project_id`` rather than
#: a second column: the value has to travel unchanged through every existing
#: read, write and index, and a nullable scope column would have made "which
#: tier is this in" a two-field question that half the call sites get wrong.
GLOBAL_SCOPE = "global"


class MemoryLimitError(ValueError):
    """A write refused *before* it reached the table.

    Carries a stable ``code`` so the gateway can answer 400 with something a
    client may branch on, and so the kernel-side soft-fail message is the same
    sentence an HTTP client is given.
    """

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def _scope(project_id: str | None) -> str:
    if not project_id:
        raise ValueError(
            "memory operations require an explicit project_id; pass "
            f"{ALL_PROJECTS!r} for the deliberate cross-project view, or "
            f"{GLOBAL_SCOPE!r} for the tier every project inherits. "
            "A missing scope used to mean 'every project', which is how "
            "one session's prompt came to carry another project's memories."
        )
    return project_id


def _block_of(row: dict) -> str:
    return str(row.get("block") or "general")


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
        """Insert one memory, or refuse before inserting anything.

        Both limits are checked ahead of the INSERT, and the count is checked
        inside the same lock that performs it. Pruning afterwards would mean
        the row briefly exists while its author is told it does not; counting
        outside the lock would let two concurrent writers both read
        ``limit - 1`` and both proceed.
        """
        scope = _scope(project_id)
        if scope == ALL_PROJECTS:
            raise MemoryLimitError(
                f"{ALL_PROJECTS!r} is a read-only view; write to "
                f"{GLOBAL_SCOPE!r} or to one project",
                code="memory_scope_invalid",
            )
        text = str(content or "").strip()
        if not text:
            raise MemoryLimitError("memory content is empty", code="memory_empty")
        if len(text) > MAX_MEMORY_CHARS:
            # Refused, not clipped -- the same reason injection skips an
            # over-long item rather than truncating it: half a protocol is a
            # different protocol, and nothing downstream can tell it was cut.
            raise MemoryLimitError(
                f"a memory may be at most {MAX_MEMORY_CHARS} characters "
                f"(this one is {len(text)}); keep the document as a file and "
                "remember where it is",
                code="memory_too_long",
            )
        now = self._clock_ms()
        memory_id = f"mem_{uuid.uuid4().hex[:12]}"
        with self._lock:
            existing = self._connection.execute(
                "SELECT COUNT(*) n FROM memories WHERE project_id=?",
                (scope,),
            ).fetchone()["n"]
            if existing >= MAX_MEMORIES_PER_SCOPE:
                raise MemoryLimitError(
                    f"scope {scope!r} already holds {existing} memories "
                    f"(limit {MAX_MEMORIES_PER_SCOPE}); delete one first",
                    code="memory_scope_full",
                )
            self._connection.execute(
                "INSERT INTO memories(memory_id,project_id,block,content,created_at) "
                "VALUES(?,?,?,?,?)",
                (memory_id, scope, block, text, now),
            )
            self._connection.commit()
        return {
            "memory_id": memory_id,
            "project_id": scope,
            "block": block,
            "content": text,
            "created_at": now,
        }

    def list(
        self,
        project_id: str | None = None,
        block: str | None = None,
    ) -> list[dict]:
        """The memories in effect for a scope -- what a prompt would carry."""
        return self.resolve(project_id, block)["memories"]

    def resolve(
        self,
        project_id: str | None = None,
        block: str | None = None,
    ) -> dict:
        """Effective memories for a scope, and what inheritance did to get there.

        ``inherited``/``overridden`` exist so the Memory pane can say *why* a
        global memory is absent from a project's context. Returning only the
        merged list would make an override indistinguishable from a memory that
        was never saved, which is the class of silence this feature already had
        one of.
        """
        scope = _scope(project_id)
        if scope == ALL_PROJECTS:
            return {
                "memories": self._rows(None, block),
                "inherited": 0,
                "overridden": 0,
            }
        if scope == GLOBAL_SCOPE:
            return {
                "memories": self._rows([GLOBAL_SCOPE], block),
                "inherited": 0,
                "overridden": 0,
            }
        rows = self._rows([scope, GLOBAL_SCOPE], block)
        own = [row for row in rows if row["project_id"] != GLOBAL_SCOPE]
        shared = [row for row in rows if row["project_id"] == GLOBAL_SCOPE]
        owned_blocks = {_block_of(row) for row in own}
        inherited = [row for row in shared if _block_of(row) not in owned_blocks]
        # Project first, then what it inherits. Order is priority here:
        # `memory_budget.select` truncates from the end, so a project's own
        # memories survive a full context and the global background is what
        # gets dropped -- the choice the user would make.
        return {
            "memories": own + inherited,
            "inherited": len(inherited),
            "overridden": len(shared) - len(inherited),
        }

    def delete(self, memory_id: str, project_id: str | None = None) -> bool:
        """Delete one memory *from a named scope*; True when a row went.

        The scope is required and does not default. An id-only delete crosses
        every boundary this module exists to draw: anything holding an id -- a
        stale tab, a copied link, a future agent capability -- could remove a
        memory belonging to a project it is not in, and the answer came back as
        an indistinguishable ``{"ok": true}``.
        """
        scope = _scope(project_id)
        with self._lock:
            if scope == ALL_PROJECTS:
                cursor = self._connection.execute(
                    "DELETE FROM memories WHERE memory_id=?",
                    (memory_id,),
                )
            else:
                cursor = self._connection.execute(
                    "DELETE FROM memories WHERE memory_id=? AND project_id=?",
                    (memory_id, scope),
                )
            self._connection.commit()
            return bool(cursor.rowcount)

    def blocks(self, project_id: str | None = None) -> list[dict]:
        """Category counts over the *effective* memories for a scope.

        Counted from `resolve` rather than by a GROUP BY on the column, so the
        chips a project shows describe the memories it actually has -- its own
        plus what it inherits -- instead of a set that still counts globals its
        own blocks have overridden.
        """
        counts: dict[str, int] = {}
        for row in self.resolve(project_id)["memories"]:
            key = _block_of(row)
            counts[key] = counts.get(key, 0) + 1
        return [
            {"block": key, "count": count}
            for key, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

    def _rows(self, scopes: list[str] | None, block: str | None) -> list[dict]:
        sql = (
            "SELECT memory_id,project_id,block,content,created_at FROM memories "
            "WHERE 1=1"
        )
        params: list[Any] = []
        if scopes is not None:
            placeholders = ",".join("?" for _ in scopes)
            sql += f" AND project_id IN ({placeholders})"
            params.extend(scopes)
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


__all__ = ["ALL_PROJECTS", "GLOBAL_SCOPE", "MemoryLimitError", "MemoryRepository"]
