"""Permission-rule persistence and resolution on a Store-owned connection."""

from __future__ import annotations

import fnmatch
import json
import sqlite3
import uuid
from typing import Any, Callable


def perm_match(text: str, pattern: str) -> bool:
    """Match a permission target while preserving exact metacharacter text."""
    text = text or ""
    pattern = pattern or "*"
    if pattern in ("*", ""):
        return True
    if text == pattern:
        return True
    try:
        return fnmatch.fnmatchcase(text, pattern)
    except Exception:  # noqa: BLE001
        return False


# Gentle defaults for the local research daemon.  The kernel can already run
# arbitrary Python, so routine confined work stays frictionless while genuinely
# external or irreversible host operations ask an actively watching human.
DEFAULT_PERMISSION_RULES = (
    ("read_file", "*.env", "deny"),
    ("read_file", "*", "allow"),
    ("write_file", "*", "allow"),
    ("edit_file", "*", "allow"),
    ("glob", "*", "allow"),
    ("grep", "*", "allow"),
    ("list_dir", "*", "allow"),
    ("save_artifact", "*", "allow"),
    ("delegate", "*", "allow"),
    ("env_setup", "*", "allow"),
    ("web_fetch", "*", "allow"),
    ("web_search", "*", "allow"),
    ("skills_edit", "*", "allow"),
    ("mcp_call", "*", "ask"),
    ("exec_background", "*", "ask"),
    ("credentials_set", "*", "ask"),
    ("skills_delete", "*", "ask"),
    ("skills_publish", "*", "ask"),
)


class PermissionRuleRepository:
    """Own persisted permission rules and their precedence semantics.

    ``Store`` supplies its SQLite connection and re-entrant lock.  Settings
    callbacks preserve the existing two-step seed behavior: commit default
    rules first, then write the ``perm_seeded`` marker through Store.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        lock: Any,
        *,
        clock_ms: Callable[[], int],
        get_setting: Callable[[str, str | None], str | None],
        set_setting: Callable[[str, str], None],
    ) -> None:
        self._connection = connection
        self._lock = lock
        self._clock_ms = clock_ms
        self._get_setting = get_setting
        self._set_setting = set_setting

    def set_rule(
        self,
        *,
        scope: str,
        scope_id: str = "",
        tool: str,
        pattern: str = "*",
        decision: str,
    ) -> str:
        """Upsert a rule while retaining its identity for the same key."""
        scope_id = scope_id or ""
        pattern = pattern or "*"
        now = self._clock_ms()
        with self._lock:
            row = self._connection.execute(
                "SELECT rule_id FROM permission_rules WHERE scope=? AND "
                "scope_id=? AND tool=? AND pattern=?",
                (scope, scope_id, tool, pattern),
            ).fetchone()
            if row:
                rule_id = row["rule_id"]
                self._connection.execute(
                    "UPDATE permission_rules SET decision=?, updated_at=? "
                    "WHERE rule_id=?",
                    (decision, now, rule_id),
                )
            else:
                rule_id = f"perm_{uuid.uuid4().hex[:12]}"
                self._connection.execute(
                    "INSERT INTO permission_rules(rule_id,scope,scope_id,tool,"
                    "pattern,decision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        rule_id,
                        scope,
                        scope_id,
                        tool,
                        pattern,
                        decision,
                        now,
                        now,
                    ),
                )
            self._connection.commit()
        return rule_id

    def delete_rule(self, rule_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "DELETE FROM permission_rules WHERE rule_id=?",
                (rule_id,),
            )
            self._connection.commit()

    def get_rules(self, *, scope: str, scope_id: str = "") -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM permission_rules WHERE scope=? AND scope_id=? "
                "ORDER BY updated_at",
                (scope, scope_id or ""),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_for_frame(
        self,
        *,
        root_frame_id: str | None = None,
        project_id: str | None = None,
    ) -> dict:
        """Return every rule relevant to a conversation, grouped by scope."""
        return {
            "global": self.get_rules(scope="global", scope_id=""),
            "project": (
                self.get_rules(scope="project", scope_id=project_id)
                if project_id
                else []
            ),
            "conversation": (
                self.get_rules(scope="conversation", scope_id=root_frame_id)
                if root_frame_id
                else []
            ),
        }

    def resolve(
        self,
        *,
        root_frame_id: str | None = None,
        project_id: str | None = None,
        tool: str,
        pattern_input: str = "",
    ) -> str:
        """Resolve a call to ``allow``, ``ask``, or ``deny``.

        Any matching deny is an absolute veto.  Otherwise the most specific
        tool and target pattern wins, followed by narrower scope and recency.
        """
        candidates = list(self.get_rules(scope="global", scope_id=""))
        if project_id:
            candidates += self.get_rules(scope="project", scope_id=project_id)
        if root_frame_id:
            candidates += self.get_rules(
                scope="conversation",
                scope_id=root_frame_id,
            )

        scope_rank = {"global": 0, "project": 1, "conversation": 2}
        best = None
        best_key = None
        for rule in candidates:
            rule_tool = rule["tool"] or "*"
            rule_pattern = rule["pattern"] or "*"
            if not perm_match(tool, rule_tool):
                continue
            if not perm_match(pattern_input or "", rule_pattern):
                continue
            if rule["decision"] == "deny":
                return "deny"
            key = (
                0 if rule_tool in ("*", "") else 1,
                0 if rule_pattern in ("*", "") else 1,
                len(rule_pattern),
                scope_rank.get(rule["scope"], 0),
                rule.get("updated_at") or 0,
            )
            if best_key is None or key > best_key:
                best_key = key
                best = rule
        return best["decision"] if best else "ask"

    def seed_defaults(self, *, force: bool = False) -> None:
        """Idempotently insert defaults or restore them during a reset."""
        if not force and self._get_setting("perm_seeded", None):
            return
        now = self._clock_ms()
        with self._lock:
            for tool, pattern, decision in DEFAULT_PERMISSION_RULES:
                row = self._connection.execute(
                    "SELECT rule_id, decision FROM permission_rules "
                    "WHERE scope='global' AND scope_id='' AND tool=? AND pattern=?",
                    (tool, pattern),
                ).fetchone()
                if row is not None:
                    if force and row["decision"] != decision:
                        self._connection.execute(
                            "UPDATE permission_rules SET decision=?, updated_at=? "
                            "WHERE rule_id=?",
                            (decision, now, row["rule_id"]),
                        )
                    continue
                self._connection.execute(
                    "INSERT INTO permission_rules(rule_id,scope,scope_id,tool,"
                    "pattern,decision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        f"perm_{uuid.uuid4().hex[:12]}",
                        "global",
                        "",
                        tool,
                        pattern,
                        decision,
                        now,
                        now,
                    ),
                )
            self._connection.commit()
        self._set_setting("perm_seeded", "1")

    # --- durable per-action approval requests --------------------------
    def create_request(
        self,
        *,
        decision_id: str,
        tool: str,
        target: str = "",
        root_frame_id: str | None = None,
        frame_id: str | None = None,
        project_id: str | None = None,
        payload: dict | None = None,
        expires_at: int | None = None,
        created_at: int | None = None,
    ) -> dict:
        """Append one immutable pending approval identity."""
        if not decision_id or not tool:
            raise ValueError("decision_id and tool are required")
        now = self._clock_ms() if created_at is None else int(created_at)
        encoded = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            try:
                self._connection.execute(
                    "INSERT INTO permission_requests("
                    "decision_id,root_frame_id,frame_id,project_id,tool,target,"
                    "payload,state,created_at,expires_at) "
                    "VALUES(?,?,?,?,?,?,?,'pending',?,?)",
                    (
                        decision_id,
                        root_frame_id,
                        frame_id,
                        project_id,
                        tool,
                        target or "",
                        encoded,
                        now,
                        expires_at,
                    ),
                )
            except Exception:
                self._connection.rollback()
                raise
            self._connection.commit()
            row = self._request_row_locked(decision_id)
        return self._normalize_request(row)

    def resolve_request(
        self,
        decision_id: str,
        *,
        state: str,
        scope: str | None = None,
        pattern: str | None = None,
        message: str | None = None,
        resolved_at: int | None = None,
    ) -> dict:
        terminal = {"allowed", "denied", "timed_out", "cancelled"}
        if state not in terminal:
            raise ValueError(f"invalid terminal permission state: {state!r}")
        now = self._clock_ms() if resolved_at is None else int(resolved_at)
        with self._lock:
            row = self._request_row_locked(decision_id)
            if row["state"] != "pending":
                current = self._normalize_request(row)
                if current["state"] == state:
                    return current
                raise RuntimeError(
                    f"permission request {decision_id!r} is already {row['state']}"
                )
            cursor = self._connection.execute(
                "UPDATE permission_requests SET state=?,scope=?,pattern=?,"
                "message=?,resolved_at=? WHERE decision_id=? AND state='pending'",
                (state, scope, pattern, message, now, decision_id),
            )
            if cursor.rowcount != 1:
                self._connection.rollback()
                raise RuntimeError(f"permission request {decision_id!r} raced")
            self._connection.commit()
            row = self._request_row_locked(decision_id)
        return self._normalize_request(row)

    def get_request(self, decision_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM permission_requests WHERE decision_id=?",
                (decision_id,),
            ).fetchone()
        return self._normalize_request(row) if row is not None else None

    def list_requests(
        self,
        *,
        root_frame_id: str | None = None,
        state: str | None = None,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list[Any] = []
        if root_frame_id is not None:
            clauses.append("root_frame_id=?")
            params.append(root_frame_id)
        if state is not None:
            clauses.append("state=?")
            params.append(state)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM permission_requests"
                + where
                + " ORDER BY created_at,decision_id",
                params,
            ).fetchall()
        return [self._normalize_request(row) for row in rows]

    def _request_row_locked(self, decision_id: str):
        row = self._connection.execute(
            "SELECT * FROM permission_requests WHERE decision_id=?",
            (decision_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown permission request {decision_id!r}")
        return row

    @staticmethod
    def _normalize_request(row) -> dict:
        data = dict(row)
        try:
            payload = json.loads(data.get("payload") or "{}")
        except (TypeError, ValueError):
            payload = {}
        data["payload"] = payload if isinstance(payload, dict) else {}
        return data


__all__ = [
    "DEFAULT_PERMISSION_RULES",
    "PermissionRuleRepository",
    "perm_match",
]
