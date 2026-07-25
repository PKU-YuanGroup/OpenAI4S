"""Agent-profile persistence on a Store-owned SQLite connection."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable


class AgentProfileRepository:
    """Persist named specialist profiles and decode their JSON capabilities.

    The repository shares its owning ``Store`` connection and re-entrant lock.
    Upserts deliberately retain the legacy read-then-write boundary: the
    existence check releases the lock before the insert or update acquires it.
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

    def list(self) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT name,description,skill_names,connectors,unrestricted,"
                "system_prompt,created_at,updated_at FROM agents ORDER BY name"
            ).fetchall()

        agents = []
        for row in rows:
            agent = dict(row)
            for key in ("skill_names", "connectors"):
                if agent.get(key):
                    try:
                        agent[key] = json.loads(agent[key])
                    except (ValueError, TypeError):
                        agent[key] = None
            agents.append(agent)
        return agents

    def get(self, name: str) -> dict | None:
        for agent in self.list():
            if agent["name"] == name:
                return agent
        return None

    def update(self, name: str, **fields: Any) -> dict | None:
        """Change only the columns supplied, leaving the rest as stored.

        `upsert` writes every column unconditionally, and the specialist
        editor sends three of them. Routing an edit through `upsert` therefore
        wrote NULL over `skill_names` and `connectors` and reset
        `unrestricted` to 1 -- a resource restriction silently became no
        restriction at all, which is the failure direction that matters.

        `None` cannot be the "not supplied" marker here: a NULL allowlist is a
        real, distinct state meaning "inherit from the parent", so absent and
        null have to stay tellable apart. Only keys actually present are
        written.
        """
        if self.get(name) is None:
            return None
        columns: list[str] = []
        params: list[Any] = []
        for key in ("description", "system_prompt"):
            if key in fields:
                columns.append(f"{key}=?")
                params.append(fields[key] or "")
        for key, column in (
            ("skill_names", "skill_names"),
            ("connectors", "connectors"),
        ):
            if key in fields:
                value = fields[key]
                columns.append(f"{column}=?")
                params.append(json.dumps(value) if value is not None else None)
        if "unrestricted" in fields:
            columns.append("unrestricted=?")
            params.append(1 if fields["unrestricted"] else 0)
        if columns:
            columns.append("updated_at=?")
            params.append(self._clock_ms())
            params.append(name)
            self._execute(
                f"UPDATE agents SET {','.join(columns)} WHERE name=?", tuple(params)
            )
        return self.get(name)

    def upsert(
        self,
        *,
        name: str,
        description: str = "",
        system_prompt: str = "",
        skill_names: list | None = None,
        connectors: list | None = None,
        unrestricted: bool = True,
    ) -> dict:
        now = self._clock_ms()
        exists = self.get(name) is not None
        encoded_skills = json.dumps(skill_names) if skill_names is not None else None
        encoded_connectors = json.dumps(connectors) if connectors is not None else None
        if exists:
            self._execute(
                "UPDATE agents SET description=?,skill_names=?,connectors=?,"
                "unrestricted=?,system_prompt=?,updated_at=? WHERE name=?",
                (
                    description,
                    encoded_skills,
                    encoded_connectors,
                    1 if unrestricted else 0,
                    system_prompt,
                    now,
                    name,
                ),
            )
        else:
            self._execute(
                "INSERT INTO agents(name,description,skill_names,connectors,"
                "unrestricted,system_prompt,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    name,
                    description,
                    encoded_skills,
                    encoded_connectors,
                    1 if unrestricted else 0,
                    system_prompt,
                    now,
                    now,
                ),
            )
        return self.get(name) or {"name": name}

    def delete(self, name: str) -> None:
        self._execute("DELETE FROM agents WHERE name=?", (name,))

    def _execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._connection.execute(sql, params)
            self._connection.commit()


__all__ = ["AgentProfileRepository"]
