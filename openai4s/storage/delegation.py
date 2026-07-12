"""Durable, bounded projection of a session's sub-agent delegation tree."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any, Callable

DELEGATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS delegation_sessions (
    root_frame_id TEXT PRIMARY KEY,
    budget_limit INTEGER NOT NULL CHECK (budget_limit > 0),
    spawned INTEGER NOT NULL DEFAULT 0 CHECK (spawned >= 0),
    active INTEGER NOT NULL DEFAULT 0 CHECK (active >= 0),
    child_sequence INTEGER NOT NULL DEFAULT 0 CHECK (child_sequence >= 0),
    owner_instance_id TEXT NOT NULL,
    runner_instance_id TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS delegation_children (
    root_frame_id TEXT NOT NULL,
    child_id TEXT NOT NULL,
    parent_child_id TEXT,
    parent_frame_id TEXT,
    frame_id TEXT,
    name TEXT,
    depth INTEGER NOT NULL CHECK (depth >= 0),
    status TEXT NOT NULL CHECK (
        status IN ('pending','running','done','failed','stopped')
    ),
    owner_instance_id TEXT NOT NULL,
    runner_instance_id TEXT NOT NULL,
    overrides_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    error TEXT,
    stop_reason TEXT,
    turn_boundary INTEGER NOT NULL DEFAULT 0 CHECK (turn_boundary >= 0),
    max_turns INTEGER,
    last_progress_at REAL,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    PRIMARY KEY(root_frame_id, child_id)
);
CREATE INDEX IF NOT EXISTS ix_delegation_children_root
    ON delegation_children(root_frame_id, created_at, child_id);
CREATE INDEX IF NOT EXISTS ix_delegation_children_live
    ON delegation_children(root_frame_id, status, runner_instance_id);
CREATE TABLE IF NOT EXISTS delegation_steering (
    root_frame_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    child_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('queued','delivered','discarded')
    ),
    text_preview TEXT NOT NULL DEFAULT '',
    queued_at REAL NOT NULL,
    delivered_at REAL,
    boundary INTEGER,
    owner_instance_id TEXT NOT NULL,
    runner_instance_id TEXT NOT NULL,
    PRIMARY KEY(root_frame_id, message_id)
);
CREATE INDEX IF NOT EXISTS ix_delegation_steering_child
    ON delegation_steering(root_frame_id, child_id, queued_at, message_id);
"""

_LIVE = frozenset({"pending", "running"})
_TERMINAL = frozenset({"done", "failed", "stopped"})
_STATES = _LIVE | _TERMINAL
_MESSAGE_STATES = frozenset({"queued", "delivered", "discarded"})
_SECRET_KEY = re.compile(
    r"(?:^|_)(?:api_?key|token|secret|password|passwd|credential|authorization|cookie)(?:$|_)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"\bBearer\s+[^\s,;]+", re.IGNORECASE)
_TOKEN = re.compile(r"\b(?:sk|ark)[-_][A-Za-z0-9._-]{8,}\b", re.IGNORECASE)
_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)"
    r"[A-Z0-9_]*)\s*[:=]\s*([^\s,;]+)"
)


class DelegationProjectionRepository:
    """Own durable delegation leases, budget counters, and safe read models."""

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
            self._connection.executescript(DELEGATION_SCHEMA)
            self._connection.commit()

    def restore(
        self,
        *,
        root_frame_id: str,
        owner_instance_id: str,
        runner_instance_id: str,
        budget_limit: int,
    ) -> dict[str, Any]:
        """Acquire a runner lease and stop non-terminal work from the old lease."""

        root = _required("root_frame_id", root_frame_id)
        owner = _required("owner_instance_id", owner_instance_id)
        runner = _required("runner_instance_id", runner_instance_id)
        limit = int(budget_limit)
        if limit < 1:
            raise ValueError("delegation budget limit must be positive")
        now_ms = self._clock_ms()
        now = now_ms / 1000.0
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                session = self._connection.execute(
                    "SELECT * FROM delegation_sessions WHERE root_frame_id=?",
                    (root,),
                ).fetchone()
                if session is None:
                    self._connection.execute(
                        "INSERT INTO delegation_sessions("
                        "root_frame_id,budget_limit,spawned,active,child_sequence,"
                        "owner_instance_id,runner_instance_id,created_at,updated_at)"
                        " VALUES(?,?,?,?,?,?,?,?,?)",
                        (root, limit, 0, 0, 0, owner, runner, now_ms, now_ms),
                    )
                elif session["runner_instance_id"] != runner:
                    rows = self._connection.execute(
                        "SELECT child_id,name,frame_id FROM delegation_children "
                        "WHERE root_frame_id=? AND status IN ('pending','running')",
                        (root,),
                    ).fetchall()
                    for child in rows:
                        result = _encode_result(
                            {
                                "child_id": child["child_id"],
                                "name": child["name"],
                                "stop_reason": "stopped",
                                "output": None,
                                "completion_bullets": [],
                                "error": None,
                                "reason": "daemon_restart",
                                "frame_id": child["frame_id"],
                            }
                        )
                        self._connection.execute(
                            "UPDATE delegation_children SET status='stopped',"
                            "result_json=?,error=NULL,stop_reason='daemon_restart',"
                            "finished_at=? WHERE root_frame_id=? AND child_id=? "
                            "AND status IN ('pending','running')",
                            (result, now, root, child["child_id"]),
                        )
                    self._connection.execute(
                        "UPDATE delegation_steering SET status='discarded' "
                        "WHERE root_frame_id=? AND status='queued'",
                        (root,),
                    )
                active = self._connection.execute(
                    "SELECT COUNT(*) AS n FROM delegation_children "
                    "WHERE root_frame_id=? AND runner_instance_id=? "
                    "AND status IN ('pending','running')",
                    (root, runner),
                ).fetchone()["n"]
                self._connection.execute(
                    "UPDATE delegation_sessions SET active=?,owner_instance_id=?,"
                    "runner_instance_id=?,updated_at=? WHERE root_frame_id=?",
                    (int(active), owner, runner, now_ms, root),
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
            return self._project_locked(root, include_text=True)

    def reserve(
        self,
        *,
        root_frame_id: str,
        owner_instance_id: str,
        runner_instance_id: str,
        count: int,
        depth: int,
        parent_child_id: str | None,
    ) -> dict[str, Any]:
        root = _required("root_frame_id", root_frame_id)
        owner = _required("owner_instance_id", owner_instance_id)
        runner = _required("runner_instance_id", runner_instance_id)
        count = int(count)
        depth = int(depth)
        if count < 0 or depth < 0:
            raise ValueError("delegation reservation values must be non-negative")
        now_ms = self._clock_ms()
        now = now_ms / 1000.0
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                session = self._lease_locked(root, owner, runner)
                spawned = int(session["spawned"])
                limit = int(session["budget_limit"])
                if spawned + count > limit:
                    raise RuntimeError(
                        f"session spawn cap reached ({limit}); already spawned "
                        f"{spawned}, requested {count}"
                    )
                sequence = int(session["child_sequence"])
                child_ids: list[str] = []
                for _ in range(count):
                    sequence += 1
                    child_id = f"child-{depth}-{sequence}"
                    child_ids.append(child_id)
                    self._connection.execute(
                        "INSERT INTO delegation_children("
                        "root_frame_id,child_id,parent_child_id,depth,status,"
                        "owner_instance_id,runner_instance_id,created_at) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (
                            root,
                            child_id,
                            parent_child_id,
                            depth,
                            "pending",
                            owner,
                            runner,
                            now,
                        ),
                    )
                self._connection.execute(
                    "UPDATE delegation_sessions SET spawned=?,active=active+?,"
                    "child_sequence=?,updated_at=? WHERE root_frame_id=?",
                    (spawned + count, count, sequence, now_ms, root),
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
            return {
                "child_ids": child_ids,
                "budget": self._budget_locked(root),
            }

    def release(
        self,
        *,
        root_frame_id: str,
        owner_instance_id: str,
        runner_instance_id: str,
        count: int = 1,
    ) -> dict[str, Any]:
        root = _required("root_frame_id", root_frame_id)
        with self._lock:
            session = self._connection.execute(
                "SELECT * FROM delegation_sessions WHERE root_frame_id=?",
                (root,),
            ).fetchone()
            if session is None:
                raise KeyError(f"unknown delegation session {root!r}")
            if (
                session["owner_instance_id"] != owner_instance_id
                or session["runner_instance_id"] != runner_instance_id
            ):
                return self._budget_locked(root)
            self._connection.execute(
                "UPDATE delegation_sessions SET active=MAX(0,active-?),"
                "updated_at=? WHERE root_frame_id=?",
                (max(0, int(count)), self._clock_ms(), root),
            )
            self._connection.commit()
            return self._budget_locked(root)

    def persist_child(
        self,
        *,
        root_frame_id: str,
        owner_instance_id: str,
        runner_instance_id: str,
        child: Mapping[str, Any],
        messages: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any] | None:
        root = _required("root_frame_id", root_frame_id)
        child_id = _required("child_id", str(child.get("child_id") or ""))
        state = str(child.get("status") or "pending")
        if state not in _STATES:
            raise ValueError(f"unknown delegation child state: {state!r}")
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM delegation_children WHERE root_frame_id=? "
                "AND child_id=?",
                (root, child_id),
            ).fetchone()
            if row is None:
                return None
            if (
                row["owner_instance_id"] != owner_instance_id
                or row["runner_instance_id"] != runner_instance_id
            ):
                return self._child_locked(root, child_id, include_text=True)
            if row["status"] not in _TERMINAL:
                result = child.get("result") if state in _TERMINAL else None
                self._connection.execute(
                    "UPDATE delegation_children SET parent_child_id=?,"
                    "parent_frame_id=?,frame_id=?,name=?,depth=?,status=?,"
                    "overrides_json=?,result_json=?,error=?,stop_reason=?,"
                    "turn_boundary=?,max_turns=?,last_progress_at=?,created_at=?,"
                    "started_at=?,finished_at=? WHERE root_frame_id=? AND child_id=?",
                    (
                        child.get("parent_child_id"),
                        child.get("parent_frame_id"),
                        child.get("frame_id"),
                        _text(child.get("name"), 160),
                        max(0, int(child.get("depth") or 0)),
                        state,
                        _encode(child.get("overrides") or {}, 4000),
                        _encode_result(result) if result is not None else None,
                        _text(child.get("error"), 1200),
                        _text(child.get("stop_reason"), 240),
                        max(0, int(child.get("turn_boundary") or 0)),
                        _positive(child.get("max_turns")),
                        _float(child.get("last_progress_at")),
                        float(child.get("created_at") or 0.0),
                        _float(child.get("started_at")),
                        _float(child.get("finished_at")),
                        root,
                        child_id,
                    ),
                )
            for message in messages:
                self._persist_message_locked(
                    root,
                    child_id,
                    owner_instance_id,
                    runner_instance_id,
                    message,
                )
            self._connection.commit()
            return self._child_locked(root, child_id, include_text=True)

    def project(self, root_frame_id: str) -> dict[str, Any]:
        root = _required("root_frame_id", root_frame_id)
        with self._lock:
            return self._project_locked(root, include_text=False)

    def budget(self, root_frame_id: str) -> dict[str, Any] | None:
        root = _required("root_frame_id", root_frame_id)
        with self._lock:
            exists = self._connection.execute(
                "SELECT 1 FROM delegation_sessions WHERE root_frame_id=?",
                (root,),
            ).fetchone()
            return self._budget_locked(root) if exists is not None else None

    def _project_locked(
        self, root_frame_id: str, *, include_text: bool
    ) -> dict[str, Any]:
        session = self._connection.execute(
            "SELECT * FROM delegation_sessions WHERE root_frame_id=?",
            (root_frame_id,),
        ).fetchone()
        stats = {key: 0 for key in ("pending", "running", "done", "failed", "stopped")}
        if session is None:
            return {
                "root_frame_id": root_frame_id,
                "initialized": False,
                "budget": None,
                "stats": {"total": 0, **stats},
                "children": [],
            }
        rows = self._connection.execute(
            "SELECT * FROM delegation_children WHERE root_frame_id=? "
            "ORDER BY created_at,child_id",
            (root_frame_id,),
        ).fetchall()
        children = [
            self._normalize_child(row, include_text=include_text) for row in rows
        ]
        for child in children:
            stats[child["status"]] += 1
        return {
            "root_frame_id": root_frame_id,
            "initialized": True,
            "budget": self._budget_from(session),
            "stats": {"total": len(children), **stats},
            "children": children,
        }

    def _normalize_child(
        self, row: sqlite3.Row, *, include_text: bool
    ) -> dict[str, Any]:
        result = _decode(row["result_json"])
        overrides = _decode(row["overrides_json"])
        message_rows = self._connection.execute(
            "SELECT message_id,status,text_preview,queued_at,delivered_at,boundary "
            "FROM delegation_steering WHERE root_frame_id=? AND child_id=? "
            "ORDER BY queued_at,message_id",
            (row["root_frame_id"], row["child_id"]),
        ).fetchall()
        messages = []
        for item in message_rows:
            message = {
                "message_id": item["message_id"],
                "status": item["status"],
                "queued_at": item["queued_at"],
                "delivered_at": item["delivered_at"],
                "boundary": item["boundary"],
            }
            if include_text:
                message["text_preview"] = item["text_preview"]
            messages.append(message)
        return {
            "child_id": row["child_id"],
            "name": row["name"],
            "status": row["status"],
            "depth": int(row["depth"]),
            "parent_child_id": row["parent_child_id"],
            "parent_frame_id": row["parent_frame_id"],
            "frame_id": row["frame_id"],
            "overrides": overrides if isinstance(overrides, dict) else {},
            "result": result if isinstance(result, dict) else None,
            "output": result.get("output") if isinstance(result, dict) else None,
            "error": row["error"],
            "stop_reason": row["stop_reason"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "progress": {
                "turn_boundary": int(row["turn_boundary"] or 0),
                "max_turns": row["max_turns"],
                "last_progress_at": row["last_progress_at"],
            },
            "steering": {
                "queued": sum(item["status"] == "queued" for item in message_rows),
                "delivered": sum(
                    item["status"] == "delivered" for item in message_rows
                ),
                "discarded": sum(
                    item["status"] == "discarded" for item in message_rows
                ),
                "messages": messages,
            },
        }

    def _persist_message_locked(
        self,
        root: str,
        child_id: str,
        owner: str,
        runner: str,
        message: Mapping[str, Any],
    ) -> None:
        message_id = _required("message_id", str(message.get("message_id") or ""))
        state = str(message.get("status") or "queued")
        if state not in _MESSAGE_STATES:
            state = "discarded"
        self._connection.execute(
            "INSERT INTO delegation_steering("
            "root_frame_id,message_id,child_id,status,text_preview,queued_at,"
            "delivered_at,boundary,owner_instance_id,runner_instance_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(root_frame_id,message_id) "
            "DO UPDATE SET status=excluded.status,delivered_at=excluded.delivered_at,"
            "boundary=excluded.boundary",
            (
                root,
                message_id,
                child_id,
                state,
                _text(message.get("text_preview"), 600) or "",
                float(message.get("queued_at") or 0.0),
                _float(message.get("delivered_at")),
                int(message["boundary"])
                if message.get("boundary") is not None
                else None,
                owner,
                runner,
            ),
        )

    def _lease_locked(self, root: str, owner: str, runner: str) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM delegation_sessions WHERE root_frame_id=?", (root,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown delegation session {root!r}")
        if row["owner_instance_id"] != owner or row["runner_instance_id"] != runner:
            raise RuntimeError("delegation runner lease is no longer active")
        return row

    def _child_locked(
        self, root: str, child_id: str, *, include_text: bool
    ) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM delegation_children WHERE root_frame_id=? AND child_id=?",
            (root, child_id),
        ).fetchone()
        return self._normalize_child(row, include_text=include_text) if row else None

    def _budget_locked(self, root: str) -> dict[str, Any]:
        row = self._connection.execute(
            "SELECT * FROM delegation_sessions WHERE root_frame_id=?", (root,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown delegation session {root!r}")
        return self._budget_from(row)

    @staticmethod
    def _budget_from(row: sqlite3.Row) -> dict[str, Any]:
        limit = int(row["budget_limit"])
        spawned = int(row["spawned"])
        return {
            "root_frame_id": row["root_frame_id"],
            "limit": limit,
            "spawned": spawned,
            "active": int(row["active"]),
            "remaining": max(0, limit - spawned),
            "sequence": int(row["child_sequence"]),
        }


def _required(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = _BEARER.sub("Bearer <redacted>", str(value))
    text = _TOKEN.sub("<redacted-token>", text)
    text = _ASSIGNMENT.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    return (
        text
        if len(text) <= limit
        else text[: max(0, limit - 18)] + "...[host truncated]"
    )


def _public(value: Any, depth: int = 0) -> Any:
    if depth >= 6:
        return "<max-depth>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _text(value, 2000)
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for index, (raw_key, item) in enumerate(value.items()):
            if index >= 60:
                output["<truncated>"] = True
                break
            key = str(raw_key)[:160]
            output[key] = (
                "<redacted>" if _SECRET_KEY.search(key) else _public(item, depth + 1)
            )
        return output
    if isinstance(value, Sequence) and not isinstance(
        value, (bytes, bytearray, memoryview)
    ):
        output = [_public(item, depth + 1) for item in value[:60]]
        if len(value) > 60:
            output.append("<truncated>")
        return output
    return _text(value, 1000)


def _encode(value: Any, limit: int) -> str:
    encoded = json.dumps(
        _public(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    if len(encoded) <= limit:
        return encoded
    return json.dumps(
        {"truncated": True, "preview": _text(encoded, max(256, limit - 80))},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _encode_result(value: Any) -> str:
    return _encode(value, 16_000)


def _decode(value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


def _positive(value: Any) -> int | None:
    if value is None:
        return None
    number = int(value)
    return number if number > 0 else None


__all__ = ["DELEGATION_SCHEMA", "DelegationProjectionRepository"]
