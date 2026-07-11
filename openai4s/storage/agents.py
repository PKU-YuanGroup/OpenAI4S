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
        encoded_skills = (
            json.dumps(skill_names) if skill_names is not None else None
        )
        encoded_connectors = (
            json.dumps(connectors) if connectors is not None else None
        )
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
