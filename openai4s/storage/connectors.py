"""MCP connector configuration on a Store-owned SQLite connection."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable

# Columns safe to hand to an HTTP client. `env` is deliberately absent: it
# carries a connector's credentials (API tokens for the MCP server it
# launches), and `list()` decodes it into a live dict for the process-spawning
# callers that genuinely need it. Anything crossing the wire must go through
# public_connector() instead of spreading a row.
_PUBLIC_FIELDS = (
    "connector_id",
    "name",
    "description",
    "command",
    "args",
    "enabled",
    "created_at",
    "updated_at",
)


def public_connector(connector: dict) -> dict:
    """Project a connector row for an API response, never including env values.

    Env *names* are returned so the UI can show which variables are configured;
    their values are reduced to a boolean. Callers that must launch the server
    (host/mcp.py, the probe/call routes) read the raw row instead.
    """
    env = connector.get("env")
    env_keys = sorted(env.keys()) if isinstance(env, dict) else []
    out = {k: connector.get(k) for k in _PUBLIC_FIELDS if k in connector}
    out["env_keys"] = env_keys
    out["has_env"] = bool(env_keys)
    return out


class ConnectorRepository:
    """Persist and decode configured MCP server connections."""

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

    def list(self) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT connector_id,name,description,command,args,env,enabled,"
                "created_at,updated_at FROM connectors ORDER BY name"
            ).fetchall()

        connectors = []
        for row in rows:
            connector = dict(row)
            connector["enabled"] = bool(connector["enabled"])
            for key in ("command", "args", "env"):
                if connector.get(key):
                    try:
                        connector[key] = json.loads(connector[key])
                    except (ValueError, TypeError):
                        pass
            connectors.append(connector)
        return connectors

    def get(self, connector_id: str) -> dict | None:
        for connector in self.list():
            if connector["connector_id"] == connector_id:
                return connector
        return None

    def upsert(
        self,
        *,
        connector_id: str,
        name: str,
        command,
        description: str = "",
        args=None,
        env=None,
        enabled: bool = True,
    ) -> dict:
        # Preserve the legacy read-then-write boundary.  It intentionally does
        # not widen the shared lock into a new transaction in this extraction.
        now = self._clock_ms()
        exists = self.get(connector_id) is not None
        encoded_command = json.dumps(command)
        encoded_args = json.dumps(args or [])
        encoded_env = json.dumps(env or {})
        if exists:
            self._execute(
                "UPDATE connectors SET name=?,description=?,command=?,args=?,"
                "env=?,enabled=?,updated_at=? WHERE connector_id=?",
                (
                    name,
                    description,
                    encoded_command,
                    encoded_args,
                    encoded_env,
                    1 if enabled else 0,
                    now,
                    connector_id,
                ),
            )
        else:
            self._execute(
                "INSERT INTO connectors(connector_id,name,description,command,"
                "args,env,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    connector_id,
                    name,
                    description,
                    encoded_command,
                    encoded_args,
                    encoded_env,
                    1 if enabled else 0,
                    now,
                    now,
                ),
            )
        return self.get(connector_id) or {"connector_id": connector_id}

    def set_enabled(self, connector_id: str, enabled: bool) -> None:
        self._execute(
            "UPDATE connectors SET enabled=?,updated_at=? WHERE connector_id=?",
            (1 if enabled else 0, self._clock_ms(), connector_id),
        )

    def delete(self, connector_id: str) -> None:
        self._execute(
            "DELETE FROM connectors WHERE connector_id=?",
            (connector_id,),
        )

    def _execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._connection.execute(sql, params)
            self._connection.commit()


__all__ = ["ConnectorRepository", "public_connector"]
