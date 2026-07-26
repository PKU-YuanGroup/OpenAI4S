"""Structured plan persistence on a Store-owned SQLite connection."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any, Callable

#: Every status a plan may hold. Enforced in Python rather than as a SQL CHECK:
#: the table is created with `CREATE TABLE IF NOT EXISTS`, so a constraint added
#: now would apply to new databases only and quietly not to any existing one --
#: a guard that protects whoever needs it least.
#:
#: `paused` is the one that was missing. A cancelled plan turn left the row on
#: `executing` forever, and `get_by_frame` prefers the newest non-discarded
#: plan, so that stuck row permanently shadowed every new draft for the session.
PLAN_STATUSES = frozenset(
    {"draft", "executing", "paused", "completed", "failed", "discarded"}
)


class PlanRepository:
    """CRUD for the ``plans`` table.

    The repository never opens its own database and never owns a second lock.
    ``Store`` injects its connection and re-entrant lock so existing transaction
    and thread-safety boundaries remain unchanged.
    """

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

    @staticmethod
    def normalize_row(row: Any) -> dict:
        data = dict(row)
        for key in ("steps", "step_status"):
            if data.get(key):
                try:
                    data[key] = json.loads(data[key])
                except (ValueError, TypeError):
                    pass
        if not isinstance(data.get("steps"), list):
            data["steps"] = []
        if not isinstance(data.get("step_status"), dict):
            data["step_status"] = {}
        return data

    def create(
        self,
        *,
        frame_id: str,
        project_id: str = "default",
        title: str | None,
        rationale: str | None,
        confidence: str | None,
        steps: list[dict],
        artifact_id: str | None = None,
        status: str = "draft",
    ) -> dict:
        now = self._clock_ms()
        plan_id = f"plan-{uuid.uuid4().hex[:12]}"
        self._execute(
            "INSERT INTO plans(plan_id,frame_id,project_id,title,rationale,"
            "confidence,steps,status,step_status,artifact_id,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                plan_id,
                frame_id,
                project_id,
                title,
                rationale,
                confidence,
                json.dumps(steps, ensure_ascii=False, default=str),
                status,
                json.dumps({}, ensure_ascii=False),
                artifact_id,
                now,
                now,
            ),
        )
        return self.get(plan_id)

    def get(self, plan_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM plans WHERE plan_id=?", (plan_id,)
            ).fetchone()
        return self.normalize_row(row) if row else None

    def get_by_frame(self, frame_id: str) -> dict | None:
        """Return the newest non-discarded plan, else the newest plan."""
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM plans WHERE frame_id=? AND status!='discarded' "
                "ORDER BY created_at DESC LIMIT 1",
                (frame_id,),
            ).fetchone()
            if row is None:
                row = self._connection.execute(
                    "SELECT * FROM plans WHERE frame_id=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (frame_id,),
                ).fetchone()
        return self.normalize_row(row) if row else None

    def list_for_frame(self, frame_id: str, *, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM plans WHERE frame_id=? ORDER BY created_at DESC "
                "LIMIT ?",
                (frame_id, limit),
            ).fetchall()
        return [self.normalize_row(row) for row in rows]

    def update(
        self,
        plan_id: str,
        *,
        title: str | None = None,
        rationale: str | None = None,
        confidence: str | None = None,
        steps: list[dict] | None = None,
        status: str | None = None,
        step_status: dict | None = None,
        artifact_id: str | None = None,
    ) -> None:
        sets: list[str] = []
        params: list[Any] = []

        def add(column: str, value: Any, *, as_json: bool = False) -> None:
            sets.append(f"{column}=?")
            params.append(
                json.dumps(value, ensure_ascii=False, default=str) if as_json else value
            )

        if title is not None:
            add("title", title)
        if rationale is not None:
            add("rationale", rationale)
        if confidence is not None:
            add("confidence", confidence)
        if steps is not None:
            add("steps", steps, as_json=True)
        if status is not None:
            if status not in PLAN_STATUSES:
                raise ValueError(
                    f"unknown plan status {status!r}; expected one of "
                    + ", ".join(sorted(PLAN_STATUSES))
                )
            add("status", status)
        if step_status is not None:
            add("step_status", step_status, as_json=True)
        if artifact_id is not None:
            add("artifact_id", artifact_id)
        if not sets:
            return
        sets.append("updated_at=?")
        params.append(self._clock_ms())
        params.append(plan_id)
        with self._lock:
            self._connection.execute(
                f"UPDATE plans SET {','.join(sets)} WHERE plan_id=?", params
            )
            self._connection.commit()

    def pause_orphaned_executing(self) -> int:
        """Move plans left mid-execution by a dead daemon to ``paused``.

        A plan only reaches ``executing`` while a turn is running, and a turn
        cannot outlive the process that started it. So on a fresh boot every
        ``executing`` row is by definition orphaned: nothing is going to finish
        it, and `get_by_frame` prefers the newest non-discarded plan, which
        means that row shadows every new draft for its session until someone
        edits the database.

        Paused rather than failed. The steps that completed did complete, and
        the honest description of a plan whose turn was interrupted is that it
        stopped, not that it went wrong.
        """
        with self._lock:
            cursor = self._connection.execute(
                "UPDATE plans SET status='paused', updated_at=? "
                "WHERE status='executing'",
                (self._clock_ms(),),
            )
            self._connection.commit()
            return int(cursor.rowcount or 0)

    def set_step_status(
        self,
        plan_id: str,
        step_id: str,
        status: str,
        note: str | None = None,
    ) -> dict | None:
        """Preserve the existing read/merge/write status update semantics."""
        plan = self.get(plan_id)
        if not plan:
            return None
        step_status = dict(plan.get("step_status") or {})
        step_status[step_id] = {
            "status": status,
            "note": note,
            "updated_at": self._clock_ms(),
        }
        self.update(plan_id, step_status=step_status)
        return self.get(plan_id)

    def delete_for_frame(self, frame_id: str) -> None:
        self._execute("DELETE FROM plans WHERE frame_id=?", (frame_id,))

    def _execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._connection.execute(sql, params)
            self._connection.commit()


__all__ = ["PlanRepository"]
