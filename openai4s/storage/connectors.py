"""MCP connector configuration on a Store-owned SQLite connection."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable

from openai4s.security.secret_broker import is_ref

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


# Every env value is brokered, not just the credential-shaped ones. Deciding by
# name would need a regex over variable names, which is exactly the heuristic
# the compute provider's own README warns about: "a secret stored under an
# unrecognized name is not removed". A connector's env is small and mostly
# credentials, the UI only ever shows the names, and treating a benign
# MODE=test as secret costs nothing — whereas missing one TOKEN_FOR_X costs
# everything.
_ENV_SCOPE = "connector_env"


def env_secret_name(connector_id: str, var: str) -> str:
    return f"{connector_id}.{var}"


def broker_connector_env(store, connector_id: str, env: dict | None) -> dict:
    """Store each env value behind a reference. Returns what to persist.

    Verifies each write by reading it back before the reference replaces the
    value: a reference that resolves to nothing would leave the MCP server
    launching without the credential it needs, failing in a way that looks like
    a broken server rather than a broken migration.
    """
    if not env:
        return {}
    out: dict[str, str] = {}
    for var, value in env.items():
        text = str(value if value is not None else "")
        if not text or is_ref(text):
            out[var] = text
            continue
        ref = store.secrets.put(_ENV_SCOPE, env_secret_name(connector_id, var), text)
        if store.secrets.get(ref) != text:
            raise RuntimeError(
                f"refusing to store connector {connector_id!r} env {var!r}: "
                f"wrote to {ref} but could not read it back"
            )
        out[var] = ref
    return out


def resolve_connector_env(store, connector: dict) -> dict:
    """The env a connector's process is actually launched with.

    Values may be references or legacy plaintext; both must work, since an
    install that has not migrated has to keep launching its servers.
    """
    env = connector.get("env")
    if not isinstance(env, dict):
        return {}
    out: dict[str, str] = {}
    for var, value in env.items():
        text = str(value if value is not None else "")
        if not is_ref(text):
            out[var] = text
            continue
        resolved = store.secrets.get(text)
        # A reference that no longer resolves must not be passed through as if
        # it were the value — the server would receive the literal string
        # "secret://..." as its credential.
        out[var] = resolved if resolved is not None else ""
    return out


def forget_connector_env(store, connector: dict | None) -> None:
    """Drop the credentials behind a connector's env references."""
    env = (connector or {}).get("env")
    if not isinstance(env, dict):
        return
    for value in env.values():
        text = str(value or "")
        if is_ref(text):
            try:
                store.secrets.delete(text)
            except Exception:  # noqa: BLE001 - removing the row still matters
                pass


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


__all__ = [
    "ConnectorRepository",
    "broker_connector_env",
    "forget_connector_env",
    "public_connector",
    "resolve_connector_env",
]
