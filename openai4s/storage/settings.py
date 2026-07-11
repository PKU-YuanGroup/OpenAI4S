"""Settings, model profiles, and message feedback on Store's connection."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable


class SettingsRepository:
    """Own the ``settings`` key/value table and its structured projections."""

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

    def get(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM settings WHERE key=?",
                (key,),
            ).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value: str) -> None:
        self._execute(
            "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=excluded.updated_at",
            (key, value, self._clock_ms()),
        )

    def list_model_profiles(self) -> list[dict]:
        raw = self.get("model_profiles")
        if not raw:
            return []
        try:
            value = json.loads(raw)
        except (ValueError, TypeError):
            return []
        return value if isinstance(value, list) else []

    def set_model_profiles(self, profiles: list[dict]) -> None:
        self.set("model_profiles", json.dumps(profiles))

    def mutate_model_profiles(self, mutate: Callable[[list[dict]], Any]) -> Any:
        """Atomically read, mutate, and write profiles under Store's RLock."""
        with self._lock:
            profiles = self.list_model_profiles()
            result = mutate(profiles)
            self.set_model_profiles(profiles)
            return result

    def set_feedback(self, frame_id: str, key: str, rating: str | None) -> None:
        setting_key = f"fb:{frame_id}:{key}"
        if rating:
            self.set(setting_key, rating)
        else:
            self._execute(
                "DELETE FROM settings WHERE key=?",
                (setting_key,),
            )

    def list_feedback(self, frame_id: str) -> dict:
        prefix = f"fb:{frame_id}:"
        with self._lock:
            rows = self._connection.execute(
                "SELECT key,value FROM settings WHERE key LIKE ?",
                (f"{prefix}%",),
            ).fetchall()
        return {row["key"][len(prefix) :]: row["value"] for row in rows}

    def _execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._connection.execute(sql, params)
            self._connection.commit()


__all__ = ["SettingsRepository"]
