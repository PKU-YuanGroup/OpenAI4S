"""Shared runtime policy for skills, specialists, and future capabilities."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Protocol


class CapabilityRepository(Protocol):
    def set_enabled(self, kind: str, name: str, enabled: bool, **kwargs) -> dict:
        ...

    def resolve(self, kind: str, name: str, **kwargs) -> dict:
        ...

    def snapshot(self, kind: str, names: Iterable[str], **kwargs) -> dict:
        ...

    def explicit_states(self, kind: str | None = None, **kwargs) -> list[dict]:
        ...

    def append_event(self, kind: str, name: str, event: str, **kwargs) -> dict:
        ...

    def list_events(self, **kwargs) -> list[dict]:
        ...

    def record_manifest(self, **kwargs) -> dict:
        ...

    def latest_manifest(self, session_id: str, *, kind: str) -> dict | None:
        ...


@dataclass(frozen=True)
class CapabilityStateService:
    """Resolve one shared enablement predicate at a concrete runtime scope."""

    repository: CapabilityRepository
    project_id: str | None = None
    session_id: str | None = None

    def scoped(
        self,
        *,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> "CapabilityStateService":
        return replace(
            self,
            project_id=self.project_id if project_id is None else project_id,
            session_id=self.session_id if session_id is None else session_id,
        )

    def state(self, kind: str, name: str) -> dict:
        return self.repository.resolve(
            kind,
            name,
            project_id=self.project_id,
            session_id=self.session_id,
        )

    def is_enabled(self, kind: str, name: str) -> bool:
        return bool(self.state(kind, name)["enabled"])

    def predicate(self, kind: str):
        return lambda name: self.is_enabled(kind, name)

    def set_enabled(
        self,
        kind: str,
        name: str,
        enabled: bool,
        *,
        scope: str = "global",
        scope_id: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        if scope_id is None:
            if scope == "project":
                scope_id = self.project_id
            elif scope == "session":
                scope_id = self.session_id
        return self.repository.set_enabled(
            kind,
            name,
            bool(enabled),
            scope=scope,
            scope_id=scope_id or "",
            metadata=metadata,
        )

    def snapshot(self, kind: str, names: Iterable[str]) -> dict[str, dict]:
        return self.repository.snapshot(
            kind,
            names,
            project_id=self.project_id,
            session_id=self.session_id,
        )

    def disabled_names(self, kind: str) -> set[str]:
        return {
            name
            for name, state in self.snapshot(
                kind,
                [row["name"] for row in self.repository.explicit_states(kind)],
            ).items()
            if not state["enabled"]
        }

    def record_event(
        self,
        kind: str,
        name: str,
        event: str,
        *,
        metadata: dict | None = None,
    ) -> dict:
        scope = (
            "session" if self.session_id else "project" if self.project_id else "global"
        )
        scope_id = self.session_id or self.project_id or ""
        return self.repository.append_event(
            kind,
            name,
            event,
            scope=scope,
            scope_id=scope_id,
            enabled=self.is_enabled(kind, name),
            metadata=metadata,
        )

    def record_manifest(self, kind: str, entries: list[dict]) -> dict | None:
        # A manifest belongs to a concrete persistent session.  Stateless CLI
        # loaders still return the same in-memory manifest but do not fabricate
        # a session identity just to write a row.
        if not self.session_id:
            return None
        return self.repository.record_manifest(
            session_id=self.session_id,
            project_id=self.project_id,
            kind=kind,
            entries=entries,
        )

    def latest_manifest(self, kind: str) -> dict | None:
        if not self.session_id:
            return None
        return self.repository.latest_manifest(self.session_id, kind=kind)


class SpecialistProfileService:
    """Apply capability policy to both stored and built-in specialists.

    This service is intentionally independent of the Web gateway.  Any prompt,
    resolver, or catalog can call ``filter_profiles`` and get the same result.
    """

    def __init__(self, profiles, capabilities: CapabilityStateService) -> None:
        self._profiles = profiles
        self.capabilities = capabilities

    def scoped(
        self,
        *,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> "SpecialistProfileService":
        return SpecialistProfileService(
            self._profiles,
            self.capabilities.scoped(
                project_id=project_id,
                session_id=session_id,
            ),
        )

    def enabled(self, name: str) -> bool:
        return self.capabilities.is_enabled("specialist", name)

    def filter_profiles(
        self,
        profiles: Iterable[dict],
        *,
        include_disabled: bool = False,
    ) -> list[dict]:
        output = []
        for profile in profiles:
            item = dict(profile)
            item["enabled"] = self.enabled(str(item.get("name") or ""))
            if include_disabled or item["enabled"]:
                output.append(item)
        return output

    def list(self, *, include_disabled: bool = False) -> list[dict]:
        return self.filter_profiles(
            self._profiles.list(),
            include_disabled=include_disabled,
        )

    def resolve(self, name: str, *, include_disabled: bool = False) -> dict | None:
        profile = self._profiles.get(name)
        if profile is None:
            return None
        enabled = self.enabled(name)
        if not enabled and not include_disabled:
            return None
        return {**profile, "enabled": enabled}


__all__ = ["CapabilityStateService", "SpecialistProfileService"]
