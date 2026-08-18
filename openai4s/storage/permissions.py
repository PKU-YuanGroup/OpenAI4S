"""Permission-rule persistence and resolution on a Store-owned connection."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import sqlite3
import uuid
from typing import Any, Callable

_MISSING = object()


def _canonical_json_sha256(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError(
            "permission action arguments must be canonical JSON"
        ) from error
    return hashlib.sha256(encoded).hexdigest()


def permission_action_digest(
    *,
    root_frame_id: str | None,
    project_id: str | None,
    tool: str,
    target: str,
    side_effect_class: str | None,
    resource_keys: list[str] | tuple[str, ...] | None,
    dangerous: bool,
    canonical_arguments: Any,
) -> tuple[str, str]:
    """Return ``(arguments_sha256, action_sha256)`` for one exact action.

    Full arguments are deliberately never persisted here: permission payloads
    are UI projections and may truncate large inputs or redact secrets.  The
    durable action identity instead binds a hash of the complete canonical
    arguments together with the policy-relevant scope and declarations.
    """

    if not isinstance(tool, str) or not tool:
        raise ValueError("permission action tool is invalid")
    if not isinstance(target, str):
        raise ValueError("permission action target is invalid")
    if resource_keys is not None and (
        not isinstance(resource_keys, (list, tuple))
        or any(not isinstance(value, str) or not value for value in resource_keys)
    ):
        raise ValueError("permission action resource keys are invalid")
    arguments_sha256 = _canonical_json_sha256(canonical_arguments)
    envelope = {
        "schema_version": 1,
        "root_frame_id": root_frame_id,
        "project_id": project_id,
        "tool": tool,
        "target": target,
        "side_effect_class": side_effect_class or "unknown",
        "resource_keys": sorted(set(resource_keys or ())),
        "dangerous": bool(dangerous),
        "canonical_arguments_sha256": arguments_sha256,
    }
    return arguments_sha256, _canonical_json_sha256(envelope)


def canonical_permission_action_digest(row: Any) -> str:
    """Hash the exact durable action represented by one permission request."""

    def field(name: str) -> Any:
        try:
            return row[name]
        except (KeyError, TypeError, IndexError) as error:
            raise ValueError("permission action envelope is incomplete") from error

    try:
        resources = json.loads(field("resource_keys") or "[]")
    except (TypeError, ValueError) as error:
        raise ValueError("permission action envelope is malformed") from error
    if not isinstance(resources, list) or any(
        not isinstance(value, str) or not value for value in resources
    ):
        raise ValueError("permission action envelope is malformed")
    arguments_sha256 = field("canonical_arguments_sha256")
    stored_digest = field("action_digest")
    if (
        not isinstance(arguments_sha256, str)
        or len(arguments_sha256) != 64
        or any(char not in "0123456789abcdef" for char in arguments_sha256)
        or not isinstance(stored_digest, str)
        or len(stored_digest) != 64
        or any(char not in "0123456789abcdef" for char in stored_digest)
    ):
        raise ValueError("permission action envelope is malformed")
    envelope = {
        "schema_version": 1,
        "root_frame_id": field("root_frame_id"),
        "project_id": field("project_id"),
        "tool": field("tool"),
        "target": field("target"),
        "side_effect_class": field("side_effect_class") or "unknown",
        "resource_keys": sorted(set(resources)),
        "dangerous": bool(field("dangerous")),
        "canonical_arguments_sha256": arguments_sha256,
    }
    digest = _canonical_json_sha256(envelope)
    if digest != stored_digest:
        raise ValueError("permission action envelope digest is invalid")
    return digest


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
    ("science_search", "*", "allow"),
    ("skills_edit", "*", "allow"),
    # The managed DataPro product flow uses the user's brokered Agent Plan Key;
    # supplying or activating that shared Ark credential is the explicit
    # authorization.  The bundled connector/Skill are enabled by default and
    # the UI's enable action is idempotent. Allow exactly that one narrow search
    # tool so the promised one-key, zero-friction path does not immediately ask
    # for a second approval. A standing deny still wins absolutely; every other
    # MCP call retains the ask default.
    ("mcp_call", "volcengine-datapro/dataPro_search", "allow"),
    ("mcp_call", "*", "ask"),
    # Reading a resource / rendering a prompt pulls attacker-controllable
    # content addressed by a model-chosen URI/name, so it stays "ask" like
    # mcp_call.  Seeding the rules explicitly (the resolve() fallback is already
    # "ask") makes them visible and pre-allowable from the UI rules panel.
    ("mcp_resource_read", "*", "ask"),
    ("mcp_prompt_get", "*", "ask"),
    ("exec_background", "*", "ask"),
    ("credentials_set", "*", "ask"),
    ("skills_delete", "*", "ask"),
    ("skills_publish", "*", "ask"),
)

# ``perm_seeded`` predates versioned defaults and remains a compatibility
# marker.  New releases advance this separate version and list only the rules
# introduced by that version, so upgrades add new defaults without restoring a
# default that an operator deliberately deleted or changed.
_DEFAULT_PERMISSION_RULE_VERSION = 3
_DEFAULT_PERMISSION_RULE_ADDITIONS = {
    2: (("science_search", "*", "allow"),),
    3: (("mcp_call", "volcengine-datapro/dataPro_search", "allow"),),
}


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
        admit_action_group: Callable[[str, str], None] | None = None,
    ) -> None:
        self._connection = connection
        self._lock = lock
        self._clock_ms = clock_ms
        self._get_setting = get_setting
        self._set_setting = set_setting
        self._admit_action_group = admit_action_group

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

    def get_rule(self, rule_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM permission_rules WHERE rule_id=?", (rule_id,)
            ).fetchone()
        return dict(row) if row else None

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
        """Insert fresh defaults, additive upgrades, or a forced reset."""
        seeded = bool(self._get_setting("perm_seeded", None))
        try:
            seeded_version = int(
                self._get_setting("perm_seed_version", None) or (1 if seeded else 0)
            )
        except (TypeError, ValueError):
            seeded_version = 1 if seeded else 0
        if force or not seeded:
            rules = DEFAULT_PERMISSION_RULES
        else:
            rules = tuple(
                rule
                for version in range(
                    seeded_version + 1, _DEFAULT_PERMISSION_RULE_VERSION + 1
                )
                for rule in _DEFAULT_PERMISSION_RULE_ADDITIONS.get(version, ())
            )
            if not rules:
                return
        now = self._clock_ms()
        with self._lock:
            for tool, pattern, decision in rules:
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
        self._set_setting("perm_seed_version", str(_DEFAULT_PERMISSION_RULE_VERSION))

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
        action_group_id: str | None = None,
        action_id: str | None = None,
        tool_call_id: str | None = None,
        side_effect_class: str | None = None,
        resource_keys: list[str] | tuple[str, ...] | None = None,
        payload: dict | None = None,
        dangerous: bool = False,
        canonical_arguments: Any = _MISSING,
        expires_at: int | None = None,
        created_at: int | None = None,
    ) -> dict:
        """Append one immutable pending approval identity.

        When the caller supplies an ``action_group_id``, the durable request
        and its ``permission_pending`` ledger event are published in the same
        SQLite transaction.  Approval therefore cannot become visible without
        also being attributable to the exact canonical action that requested
        it.
        """
        if not decision_id or not tool:
            raise ValueError("decision_id and tool are required")
        if resource_keys is not None and (
            not isinstance(resource_keys, (list, tuple))
            or not all(isinstance(value, str) for value in resource_keys)
        ):
            raise TypeError("resource_keys must be a list or tuple of strings")
        if not isinstance(dangerous, bool):
            raise TypeError("dangerous must be a bool")
        now = self._clock_ms() if created_at is None else int(created_at)
        encoded = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))
        encoded_resources = json.dumps(
            list(resource_keys or ()), ensure_ascii=False, separators=(",", ":")
        )
        if canonical_arguments is _MISSING:
            arguments_sha256 = None
            action_digest = None
        else:
            arguments_sha256, action_digest = permission_action_digest(
                root_frame_id=root_frame_id,
                project_id=project_id,
                tool=tool,
                target=target or "",
                side_effect_class=side_effect_class,
                resource_keys=resource_keys,
                dangerous=dangerous,
                canonical_arguments=canonical_arguments,
            )
        with self._lock:
            try:
                self._connection.execute(
                    "INSERT INTO permission_requests("
                    "decision_id,root_frame_id,frame_id,project_id,"
                    "action_group_id,action_id,tool_call_id,tool,target,"
                    "side_effect_class,resource_keys,payload,dangerous,"
                    "canonical_arguments_sha256,action_digest,state,created_at,"
                    "expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
                    "'pending',?,?)",
                    (
                        decision_id,
                        root_frame_id,
                        frame_id,
                        project_id,
                        action_group_id,
                        action_id,
                        tool_call_id,
                        tool,
                        target or "",
                        side_effect_class,
                        encoded_resources,
                        encoded,
                        int(dangerous),
                        arguments_sha256,
                        action_digest,
                        now,
                        expires_at,
                    ),
                )
                if action_group_id:
                    self._append_permission_event_locked(
                        group_id=action_group_id,
                        event_type="permission_pending",
                        decision_id=decision_id,
                        action_id=action_id,
                        tool_call_id=tool_call_id,
                        side_effect_class=side_effect_class,
                        resource_keys=list(resource_keys or ()),
                        result={
                            "decision_id": decision_id,
                            "state": "pending",
                            "tool": tool,
                            "target": target or "",
                        },
                        created_at=now,
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
        resolution_context: str | None = None,
        continuation_required: bool = False,
        expected_action_digest: str | None = None,
        resolved_at: int | None = None,
    ) -> dict:
        terminal = {"allowed", "denied", "timed_out", "cancelled"}
        if state not in terminal:
            raise ValueError(f"invalid terminal permission state: {state!r}")
        now = self._clock_ms() if resolved_at is None else int(resolved_at)
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._request_row_locked(decision_id)
                if row["state"] != "pending":
                    current = self._normalize_request(row)
                    if current["state"] == state:
                        if state == "allowed":
                            self._assert_exact_action_envelope_locked(
                                row,
                                expected_action_digest=expected_action_digest,
                            )
                        self._connection.commit()
                        return current
                    raise RuntimeError(
                        f"permission request {decision_id!r} is already {row['state']}"
                    )
                if (
                    state == "allowed"
                    and row["expires_at"] is not None
                    and int(row["expires_at"]) <= now
                ):
                    state = "timed_out"
                    scope = "once"
                    pattern = None
                    message = "approval timed out"
                    resolution_context = "expired"
                    continuation_required = False
                elif state == "allowed":
                    try:
                        self._assert_exact_action_envelope_locked(
                            row,
                            expected_action_digest=expected_action_digest,
                        )
                    except RuntimeError:
                        state = "denied"
                        scope = "once"
                        pattern = None
                        message = "permission action failed integrity validation"
                        resolution_context = "integrity_failure"
                        continuation_required = False
                cursor = self._connection.execute(
                    "UPDATE permission_requests SET state=?,scope=?,pattern=?,"
                    "message=?,resolution_context=?,continuation_required=?,"
                    "resolved_at=? WHERE decision_id=? AND state='pending'",
                    (
                        state,
                        scope,
                        pattern,
                        message,
                        resolution_context,
                        int(bool(continuation_required)),
                        now,
                        decision_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(f"permission request {decision_id!r} raced")
                if row["action_group_id"]:
                    try:
                        resources = json.loads(row["resource_keys"] or "[]")
                    except (TypeError, ValueError):
                        resources = []
                    self._append_permission_event_locked(
                        group_id=row["action_group_id"],
                        event_type="permission_resolved",
                        decision_id=decision_id,
                        action_id=row["action_id"],
                        tool_call_id=row["tool_call_id"],
                        side_effect_class=row["side_effect_class"],
                        resource_keys=(
                            resources if isinstance(resources, list) else []
                        ),
                        result={
                            "decision_id": decision_id,
                            "state": state,
                            "scope": scope,
                            "pattern": pattern,
                            "message": message,
                            "resolution_context": resolution_context,
                        },
                        created_at=now,
                    )
                self._connection.commit()
                row = self._request_row_locked(decision_id)
            except Exception:
                self._connection.rollback()
                raise
        return self._normalize_request(row)

    def consume_restart_once_grant(
        self,
        *,
        root_frame_id: str,
        tool: str,
        target: str = "",
        project_id: str | None = None,
        side_effect_class: str | None = None,
        resource_keys: list[str] | tuple[str, ...] | None = None,
        dangerous: bool = False,
        canonical_arguments: Any = _MISSING,
        consumed_at: int | None = None,
    ) -> dict | None:
        """Atomically consume one exact post-restart, ``once`` approval.

        A daemon restart destroys the blocked Python thread, so an approval can
        never resume that stack.  The safe replacement is a durable grant for
        one *fresh* action with the same conversation, tool and permission
        target.  It is intentionally narrower than a conversation rule and is
        consumed before the new handler runs.
        """

        if not root_frame_id or not tool or canonical_arguments is _MISSING:
            return None
        try:
            _, expected_action_digest = permission_action_digest(
                root_frame_id=root_frame_id,
                project_id=project_id,
                tool=tool,
                target=target or "",
                side_effect_class=side_effect_class,
                resource_keys=resource_keys,
                dangerous=dangerous,
                canonical_arguments=canonical_arguments,
            )
        except (TypeError, ValueError):
            return None
        now = self._clock_ms() if consumed_at is None else int(consumed_at)
        clauses = [
            "root_frame_id=?",
            "tool=?",
            "target=?",
            "action_digest=?",
            "state='allowed'",
            "scope='once'",
            "resolution_context='after_restart'",
            "continuation_required=1",
            "continuation_expires_at IS NOT NULL",
            "continuation_expires_at>?",
            "continuation_consumed_at IS NULL",
        ]
        params: list[Any] = [
            root_frame_id,
            tool,
            target or "",
            expected_action_digest,
            now,
        ]
        if project_id is not None:
            clauses.append("project_id=?")
            params.append(project_id)
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._connection.execute(
                    "SELECT * FROM permission_requests WHERE "
                    + " AND ".join(clauses)
                    + " ORDER BY resolved_at,created_at,decision_id LIMIT 1",
                    params,
                ).fetchone()
                if row is None:
                    self._connection.commit()
                    return None
                try:
                    stored_action_digest = canonical_permission_action_digest(row)
                except ValueError:
                    self._connection.rollback()
                    return None
                if stored_action_digest != expected_action_digest:
                    self._connection.rollback()
                    return None
                decision_id = row["decision_id"]
                if row["action_group_id"] and self._admit_action_group is not None:
                    self._admit_action_group(
                        str(row["action_group_id"]),
                        "consume_restart_once_grant",
                    )
                cursor = self._connection.execute(
                    "UPDATE permission_requests SET continuation_consumed_at=? "
                    "WHERE decision_id=? AND continuation_consumed_at IS NULL "
                    "AND continuation_expires_at>?",
                    (now, decision_id, now),
                )
                if cursor.rowcount != 1:
                    self._connection.rollback()
                    return None
                self._connection.commit()
                resolved = self._request_row_locked(decision_id)
            except Exception:
                self._connection.rollback()
                raise
        return self._normalize_request(resolved)

    def activate_restart_continuation(
        self,
        decision_id: str,
        *,
        expires_at: int | None = None,
    ) -> dict:
        """Make a post-restart approval consumable after its ledger marker exists."""

        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._request_row_locked(decision_id)
                if (
                    row["state"] != "allowed"
                    or row["resolution_context"] != "after_restart"
                ):
                    raise RuntimeError(
                        f"permission request {decision_id!r} is not a restart approval"
                    )
                self._assert_exact_action_envelope_locked(row, require=True)
                if row["action_group_id"] and self._admit_action_group is not None:
                    self._admit_action_group(
                        str(row["action_group_id"]),
                        "activate_restart_continuation",
                    )
                if not row["continuation_required"]:
                    self._connection.execute(
                        "UPDATE permission_requests SET continuation_required=1,"
                        "continuation_expires_at=? "
                        "WHERE decision_id=? AND state='allowed' "
                        "AND resolution_context='after_restart'",
                        (expires_at, decision_id),
                    )
                self._connection.commit()
                row = self._request_row_locked(decision_id)
            except Exception:
                self._connection.rollback()
                raise
        return self._normalize_request(row)

    def get_request(self, decision_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM permission_requests WHERE decision_id=?",
                (decision_id,),
            ).fetchone()
        return self._normalize_request(row) if row is not None else None

    def request_action_digest(self, decision_id: str) -> str:
        with self._lock:
            row = self._request_row_locked(decision_id)
            return canonical_permission_action_digest(row)

    def timeout_expired_requests(self, *, now: int | None = None) -> int:
        """Read-time backstop: resolve pendings whose expires_at has passed.

        The live gate thread enforces the deadline while it blocks, but after a
        daemon restart that thread is gone and the row stays ``pending`` with a
        past ``expires_at``.  Route each through ``resolve_request`` (not a bulk
        UPDATE) so action-group-bound rows still emit their resolved event, and
        keep the ``WHERE state='pending'`` guard so the sweep races safely with
        any live gate thread.
        """

        now = self._clock_ms() if now is None else int(now)
        with self._lock:
            rows = self._connection.execute(
                "SELECT decision_id FROM permission_requests "
                "WHERE state='pending' AND expires_at IS NOT NULL "
                "AND expires_at<=?",
                (now,),
            ).fetchall()
        swept = 0
        for row in rows:
            try:
                self.resolve_request(
                    row["decision_id"],
                    state="timed_out",
                    scope="once",
                    message="approval timed out",
                    resolution_context="expired",
                    resolved_at=now,
                )
                swept += 1
            except (KeyError, RuntimeError):
                # Resolved concurrently by a live gate thread; that terminal
                # state stands.
                continue
        return swept

    def list_requests(
        self,
        *,
        root_frame_id: str | None = None,
        state: str | None = None,
    ) -> list[dict]:
        if state == "pending":
            self.timeout_expired_requests()
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
    def _assert_exact_action_envelope_locked(
        row,
        *,
        expected_action_digest: str | None = None,
        require: bool = False,
    ) -> str | None:
        """Validate an immutable action before an allow-shaped transition."""

        arguments_sha256 = row["canonical_arguments_sha256"]
        stored_digest = row["action_digest"]
        if arguments_sha256 is None and stored_digest is None:
            if require or expected_action_digest is not None:
                raise RuntimeError("permission action envelope is missing")
            # Legacy, explicitly human-resolved requests predate exact action
            # hashes. They may still be denied or resolved by a user, but can
            # never become a restart continuation.
            return None
        try:
            current_digest = canonical_permission_action_digest(row)
        except ValueError as error:
            raise RuntimeError("permission action envelope is invalid") from error
        if (
            expected_action_digest is not None
            and current_digest != expected_action_digest
        ):
            raise RuntimeError("permission action digest changed before resolution")
        return current_digest

    @staticmethod
    def _normalize_request(row) -> dict:
        data = dict(row)
        try:
            payload = json.loads(data.get("payload") or "{}")
        except (TypeError, ValueError):
            payload = {}
        data["payload"] = payload if isinstance(payload, dict) else {}
        try:
            resource_keys = json.loads(data.get("resource_keys") or "[]")
        except (TypeError, ValueError):
            resource_keys = []
        data["resource_keys"] = resource_keys if isinstance(resource_keys, list) else []
        return data

    def _append_permission_event_locked(
        self,
        *,
        group_id: str,
        event_type: str,
        decision_id: str,
        action_id: str | None,
        tool_call_id: str | None,
        side_effect_class: str | None,
        resource_keys: list[str],
        result: dict,
        created_at: int,
    ) -> None:
        """Insert one ledger event inside the caller's open transaction."""

        if self._admit_action_group is not None:
            operation = f"event:{event_type}"
            if event_type == "permission_resolved":
                operation += f":{str(result.get('state') or '').lower()}"
            self._admit_action_group(group_id, operation)

        if (
            self._connection.execute(
                "SELECT 1 FROM action_groups WHERE group_id=?", (group_id,)
            ).fetchone()
            is None
        ):
            raise KeyError(f"unknown action group {group_id!r}")
        sequence = int(
            self._connection.execute(
                "SELECT COALESCE(MAX(sequence),-1)+1 AS n FROM action_events "
                "WHERE group_id=?",
                (group_id,),
            ).fetchone()["n"]
        )
        self._connection.execute(
            "INSERT INTO action_events("
            "event_id,group_id,sequence,type,action_id,tool_call_id,wire_id,"
            "canonical_arguments,raw_arguments,result,side_effect_class,"
            "resource_keys,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"ae-{uuid.uuid4().hex[:16]}",
                group_id,
                sequence,
                event_type,
                action_id,
                tool_call_id,
                None,
                json.dumps(
                    {"decision_id": decision_id},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                None,
                json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                side_effect_class,
                json.dumps(
                    list(resource_keys), ensure_ascii=False, separators=(",", ":")
                ),
                created_at,
            ),
        )


__all__ = [
    "canonical_permission_action_digest",
    "DEFAULT_PERMISSION_RULES",
    "permission_action_digest",
    "PermissionRuleRepository",
    "perm_match",
]
