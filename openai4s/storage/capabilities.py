"""Persistent capability enablement and bootstrap manifests.

The repository owns the durable half of the capability registry.  It keeps a
small materialized state table for fast policy checks and an append-only event
table for audit/recovery.  Scope precedence is deliberately identical for all
capability kinds: ``session`` overrides ``project``, which overrides
``global``; an absent row means enabled.

This module is pure stdlib and shares the connection + ``RLock`` owned by
``Store``.  It does not discover skills or import sidecars.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any, Callable, Iterable

_SCOPES = frozenset({"global", "project", "session"})


def _normalized(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise ValueError("capability name is required")
    return value.casefold()


def _validated_scope(scope: str, scope_id: str | None) -> tuple[str, str]:
    scope = str(scope or "global").strip().lower()
    if scope not in _SCOPES:
        raise ValueError("scope must be global, project, or session")
    resolved_id = "" if scope == "global" else str(scope_id or "").strip()
    if scope != "global" and not resolved_id:
        raise ValueError(f"scope_id is required for {scope} capability state")
    return scope, resolved_id


def _decode_json(raw: Any, fallback: Any) -> Any:
    if raw in (None, ""):
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


class CapabilityStateRepository:
    """Persist scoped enablement, append-only events, and manifests."""

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

    def set_enabled(
        self,
        kind: str,
        name: str,
        enabled: bool,
        *,
        scope: str = "global",
        scope_id: str = "",
        metadata: dict | None = None,
    ) -> dict:
        kind = str(kind or "").strip().lower()
        if not kind:
            raise ValueError("capability kind is required")
        display_name = str(name or "").strip()
        normalized_name = _normalized(display_name)
        scope, scope_id = _validated_scope(scope, scope_id)
        now = self._clock_ms()
        encoded = json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"))
        event_id = f"ce-{uuid.uuid4().hex}"
        with self._lock:
            self._connection.execute(
                "INSERT INTO capability_states(kind,name,normalized_name,scope,"
                "scope_id,enabled,metadata,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(kind,normalized_name,scope,"
                "scope_id) DO UPDATE SET name=excluded.name,enabled=excluded.enabled,"
                "metadata=excluded.metadata,updated_at=excluded.updated_at",
                (
                    kind,
                    display_name,
                    normalized_name,
                    scope,
                    scope_id,
                    1 if enabled else 0,
                    encoded,
                    now,
                    now,
                ),
            )
            self._connection.execute(
                "INSERT INTO capability_events(event_id,kind,name,normalized_name,"
                "scope,scope_id,event,enabled,metadata,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    kind,
                    display_name,
                    normalized_name,
                    scope,
                    scope_id,
                    "enabled" if enabled else "disabled",
                    1 if enabled else 0,
                    encoded,
                    now,
                ),
            )
            self._connection.commit()
        return self.resolve(
            kind,
            display_name,
            project_id=scope_id if scope == "project" else None,
            session_id=scope_id if scope == "session" else None,
        )

    def resolve(
        self,
        kind: str,
        name: str,
        *,
        project_id: str | None = None,
        session_id: str | None = None,
        default: bool = True,
    ) -> dict:
        kind = str(kind or "").strip().lower()
        normalized_name = _normalized(name)
        clauses = ["(scope='global' AND scope_id='')"]
        params: list[Any] = [kind, normalized_name]
        if project_id:
            clauses.append("(scope='project' AND scope_id=?)")
            params.append(str(project_id))
        if session_id:
            clauses.append("(scope='session' AND scope_id=?)")
            params.append(str(session_id))
        sql = (
            "SELECT kind,name,scope,scope_id,enabled,metadata,created_at,updated_at "
            "FROM capability_states WHERE kind=? AND normalized_name=? AND ("
            + " OR ".join(clauses)
            + ") ORDER BY CASE scope WHEN 'session' THEN 3 WHEN 'project' THEN 2 "
            "ELSE 1 END DESC LIMIT 1"
        )
        with self._lock:
            row = self._connection.execute(sql, tuple(params)).fetchone()
        if row is None:
            return {
                "kind": kind,
                "name": str(name),
                "enabled": bool(default),
                "scope": "default",
                "scope_id": "",
                "metadata": {},
            }
        result = dict(row)
        result["enabled"] = bool(result["enabled"])
        result["metadata"] = _decode_json(result.get("metadata"), {})
        return result

    def snapshot(
        self,
        kind: str,
        names: Iterable[str],
        *,
        project_id: str | None = None,
        session_id: str | None = None,
        default: bool = True,
    ) -> dict[str, dict]:
        """Return one effective state per requested name.

        Callers provide discovery order/names so capabilities that have never
        been toggled are represented truthfully with ``scope='default'``.
        """

        return {
            str(name): self.resolve(
                kind,
                str(name),
                project_id=project_id,
                session_id=session_id,
                default=default,
            )
            for name in names
        }

    def explicit_states(
        self,
        kind: str | None = None,
        *,
        scope: str | None = None,
        scope_id: str | None = None,
    ) -> list[dict]:
        where: list[str] = []
        params: list[Any] = []
        if kind:
            where.append("kind=?")
            params.append(str(kind).strip().lower())
        if scope:
            resolved_scope, resolved_id = _validated_scope(scope, scope_id)
            where.extend(("scope=?", "scope_id=?"))
            params.extend((resolved_scope, resolved_id))
        sql = (
            "SELECT kind,name,scope,scope_id,enabled,metadata,created_at,updated_at "
            "FROM capability_states"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY kind,normalized_name,scope,scope_id"
        with self._lock:
            rows = self._connection.execute(sql, tuple(params)).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["enabled"] = bool(item["enabled"])
            item["metadata"] = _decode_json(item.get("metadata"), {})
            output.append(item)
        return output

    def append_event(
        self,
        kind: str,
        name: str,
        event: str,
        *,
        scope: str = "global",
        scope_id: str = "",
        enabled: bool | None = None,
        metadata: dict | None = None,
    ) -> dict:
        kind = str(kind or "").strip().lower()
        display_name = str(name or "").strip()
        normalized_name = _normalized(display_name)
        scope, scope_id = _validated_scope(scope, scope_id)
        event = str(event or "").strip().lower()
        if not kind or not event:
            raise ValueError("capability kind and event are required")
        record = {
            "event_id": f"ce-{uuid.uuid4().hex}",
            "kind": kind,
            "name": display_name,
            "scope": scope,
            "scope_id": scope_id,
            "event": event,
            "enabled": enabled,
            "metadata": dict(metadata or {}),
            "created_at": self._clock_ms(),
        }
        with self._lock:
            self._connection.execute(
                "INSERT INTO capability_events(event_id,kind,name,normalized_name,"
                "scope,scope_id,event,enabled,metadata,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    record["event_id"],
                    kind,
                    display_name,
                    normalized_name,
                    scope,
                    scope_id,
                    event,
                    None if enabled is None else (1 if enabled else 0),
                    json.dumps(
                        record["metadata"], sort_keys=True, separators=(",", ":")
                    ),
                    record["created_at"],
                ),
            )
            self._connection.commit()
        return record

    def list_events(
        self,
        *,
        kind: str | None = None,
        name: str | None = None,
        session_id: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        where: list[str] = []
        params: list[Any] = []
        if kind:
            where.append("kind=?")
            params.append(str(kind).strip().lower())
        if name:
            where.append("normalized_name=?")
            params.append(_normalized(name))
        if session_id:
            where.append("scope='session' AND scope_id=?")
            params.append(str(session_id))
        sql = (
            "SELECT event_id,kind,name,scope,scope_id,event,enabled,metadata,"
            "created_at FROM capability_events"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        # ``clock_ms`` can produce identical timestamps for a burst of events;
        # rowid preserves their append order while event_id is intentionally
        # random and therefore cannot be used as a chronology tie-breaker.
        sql += " ORDER BY created_at DESC,rowid DESC LIMIT ?"
        params.append(max(1, min(int(limit), 2000)))
        with self._lock:
            rows = self._connection.execute(sql, tuple(params)).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            if item["enabled"] is not None:
                item["enabled"] = bool(item["enabled"])
            item["metadata"] = _decode_json(item.get("metadata"), {})
            output.append(item)
        return output

    def record_manifest(
        self,
        *,
        session_id: str,
        project_id: str | None,
        kind: str,
        entries: list[dict],
        manifest_id: str | None = None,
    ) -> dict:
        session_id = str(session_id or "").strip()
        if not session_id:
            raise ValueError("session_id is required for a capability manifest")
        kind = str(kind or "").strip().lower()
        if not kind:
            raise ValueError("capability kind is required")
        record = {
            "manifest_id": manifest_id or f"cm-{uuid.uuid4().hex}",
            "session_id": session_id,
            "project_id": str(project_id or ""),
            "kind": kind,
            "entries": list(entries),
            "created_at": self._clock_ms(),
        }
        with self._lock:
            self._connection.execute(
                "INSERT INTO capability_manifests(manifest_id,session_id,project_id,"
                "kind,entries,created_at) VALUES(?,?,?,?,?,?)",
                (
                    record["manifest_id"],
                    session_id,
                    record["project_id"],
                    kind,
                    json.dumps(entries, sort_keys=True, separators=(",", ":")),
                    record["created_at"],
                ),
            )
            self._connection.commit()
        return record

    def latest_manifest(self, session_id: str, *, kind: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT manifest_id,session_id,project_id,kind,entries,created_at "
                "FROM capability_manifests WHERE session_id=? AND kind=? "
                "ORDER BY created_at DESC,manifest_id DESC LIMIT 1",
                (str(session_id), str(kind).strip().lower()),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["entries"] = _decode_json(result.get("entries"), [])
        return result


__all__ = ["CapabilityStateRepository"]
