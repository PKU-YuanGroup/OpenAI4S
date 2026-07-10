"""Versioned JSON contracts for deterministic harness scenarios and events."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = 1
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")
_EXTERNAL_TAGS = {
    "external",
    "network",
    "live_llm",
    "gpu",
    "ssh",
    "lab",
    "docker",
    "browser",
}


class ScenarioValidationError(ValueError):
    """Raised when a scenario cannot be interpreted unambiguously."""


def _require_mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ScenarioValidationError(f"{where} must be a JSON object")
    return value


def _only_keys(value: Mapping[str, Any], allowed: set[str], where: str) -> None:
    extras = sorted(set(value) - allowed)
    if extras:
        raise ScenarioValidationError(
            f"{where} contains unsupported field(s): {', '.join(extras)}"
        )


@dataclass(frozen=True)
class FaultSpec:
    """A deterministic fault injected at the Nth visit to a named point."""

    point: str
    occurrence: int
    kind: str
    message: str
    retryable: bool = False

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], index: int) -> "FaultSpec":
        where = f"faults[{index}]"
        _only_keys(
            raw,
            {"point", "occurrence", "kind", "message", "retryable"},
            where,
        )
        point = raw.get("point")
        kind = raw.get("kind")
        message = raw.get("message")
        occurrence = raw.get("occurrence", 1)
        if not isinstance(point, str) or not point.strip():
            raise ScenarioValidationError(f"{where}.point must be a non-empty string")
        if not isinstance(kind, str) or not kind.strip():
            raise ScenarioValidationError(f"{where}.kind must be a non-empty string")
        if not isinstance(message, str) or not message:
            raise ScenarioValidationError(f"{where}.message must be a non-empty string")
        if (
            not isinstance(occurrence, int)
            or isinstance(occurrence, bool)
            or occurrence < 1
        ):
            raise ScenarioValidationError(
                f"{where}.occurrence must be a positive integer"
            )
        retryable = raw.get("retryable", False)
        if not isinstance(retryable, bool):
            raise ScenarioValidationError(f"{where}.retryable must be a boolean")
        return cls(point.strip(), occurrence, kind.strip(), message, retryable)


@dataclass(frozen=True)
class ProviderStep:
    """One scripted model result: exactly one response or one error."""

    response: Mapping[str, Any] | None = None
    error: Mapping[str, Any] | None = None
    terminal_reason: str | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], index: int) -> "ProviderStep":
        where = f"provider_script[{index}]"
        _only_keys(raw, {"response", "error", "terminal_reason"}, where)
        has_response = "response" in raw
        has_error = "error" in raw
        if has_response == has_error:
            raise ScenarioValidationError(
                f"{where} must contain exactly one of response or error"
            )
        response = None
        error = None
        if has_response:
            response = dict(_require_mapping(raw["response"], f"{where}.response"))
            content = response.get("content")
            if not isinstance(content, str):
                raise ScenarioValidationError(
                    f"{where}.response.content must be a string"
                )
        else:
            error = dict(_require_mapping(raw["error"], f"{where}.error"))
            _only_keys(
                error,
                {"kind", "message", "status", "headers", "retryable"},
                f"{where}.error",
            )
            if not isinstance(error.get("kind"), str) or not error["kind"]:
                raise ScenarioValidationError(
                    f"{where}.error.kind must be a non-empty string"
                )
            if not isinstance(error.get("message"), str) or not error["message"]:
                raise ScenarioValidationError(
                    f"{where}.error.message must be a non-empty string"
                )
            status = error.get("status")
            if status is not None and (
                not isinstance(status, int) or isinstance(status, bool)
            ):
                raise ScenarioValidationError(
                    f"{where}.error.status must be an integer"
                )
            headers = error.get("headers")
            if headers is not None and (
                not isinstance(headers, Mapping)
                or any(
                    not isinstance(key, str) or not isinstance(value, str)
                    for key, value in headers.items()
                )
            ):
                raise ScenarioValidationError(
                    f"{where}.error.headers must map strings to strings"
                )
            retryable = error.get("retryable", False)
            if not isinstance(retryable, bool):
                raise ScenarioValidationError(
                    f"{where}.error.retryable must be a boolean"
                )
        terminal_reason = raw.get("terminal_reason")
        if terminal_reason is not None and (
            not isinstance(terminal_reason, str) or not terminal_reason.strip()
        ):
            raise ScenarioValidationError(
                f"{where}.terminal_reason must be a non-empty string"
            )
        if error is not None and terminal_reason is not None:
            raise ScenarioValidationError(
                f"{where}.terminal_reason is only valid for a response"
            )
        return cls(response, error, terminal_reason)


@dataclass(frozen=True)
class Expectations:
    terminal_reason: str
    model_attempts: int
    event_kinds: tuple[str, ...] = ()
    invariants: tuple[str, ...] = ("ordered_events", "one_run_terminal")

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Expectations":
        _only_keys(
            raw,
            {"terminal_reason", "model_attempts", "event_kinds", "invariants"},
            "expect",
        )
        terminal_reason = raw.get("terminal_reason")
        model_attempts = raw.get("model_attempts")
        if not isinstance(terminal_reason, str) or not terminal_reason:
            raise ScenarioValidationError(
                "expect.terminal_reason must be a non-empty string"
            )
        if (
            not isinstance(model_attempts, int)
            or isinstance(model_attempts, bool)
            or model_attempts < 0
        ):
            raise ScenarioValidationError(
                "expect.model_attempts must be a non-negative integer"
            )

        def strings(name: str) -> tuple[str, ...]:
            value = raw.get(name, [])
            if not isinstance(value, list) or any(
                not isinstance(item, str) or not item for item in value
            ):
                raise ScenarioValidationError(
                    f"expect.{name} must be an array of non-empty strings"
                )
            return tuple(value)

        # An explicit "invariants": [] is an opt-out and must not be replaced
        # by the defaults (unlike event_kinds, [] and absent differ here).
        if "invariants" in raw:
            invariants = strings("invariants")
        else:
            invariants = ("ordered_events", "one_run_terminal")
        return cls(
            terminal_reason=terminal_reason,
            model_attempts=model_attempts,
            event_kinds=strings("event_kinds"),
            invariants=invariants,
        )


@dataclass(frozen=True)
class PermissionSettings:
    """Permission mode captured by a scenario, independent of viewer state."""

    noninteractive: str = "rules_only"

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PermissionSettings":
        _only_keys(raw, {"noninteractive"}, "permissions")
        mode = raw.get("noninteractive", "rules_only")
        allowed = {"deny", "rules_only", "allow"}
        if not isinstance(mode, str) or mode not in allowed:
            raise ScenarioValidationError(
                "permissions.noninteractive must be one of "
                "deny, rules_only, or allow"
            )
        return cls(noninteractive=mode)


@dataclass(frozen=True)
class Scenario:
    schema_version: int
    id: str
    tags: tuple[str, ...]
    surface: str
    task: str
    fixtures: Mapping[str, Any]
    provider_script: tuple[ProviderStep, ...]
    faults: tuple[FaultSpec, ...]
    permissions: PermissionSettings
    expect: Expectations
    source_path: Path | None = field(default=None, compare=False)

    @property
    def is_offline(self) -> bool:
        return "offline" in self.tags and not (_EXTERNAL_TAGS & set(self.tags))

    def in_tier(self, tier: str) -> bool:
        return f"tier:{tier}" in self.tags

    @classmethod
    def from_dict(
        cls, raw: Mapping[str, Any], *, source_path: Path | None = None
    ) -> "Scenario":
        _only_keys(
            raw,
            {
                "schema_version",
                "id",
                "tags",
                "surface",
                "task",
                "fixtures",
                "provider_script",
                "faults",
                "permissions",
                "expect",
            },
            "scenario",
        )
        version = raw.get("schema_version")
        if version != SCHEMA_VERSION:
            raise ScenarioValidationError(
                f"unsupported scenario schema_version {version!r}; "
                f"expected {SCHEMA_VERSION}"
            )
        scenario_id = raw.get("id")
        if not isinstance(scenario_id, str) or not _ID_RE.fullmatch(scenario_id):
            raise ScenarioValidationError(
                "scenario.id must match [a-z0-9][a-z0-9._-]{0,80}"
            )
        tags_raw = raw.get("tags")
        if (
            not isinstance(tags_raw, list)
            or not tags_raw
            or any(not isinstance(tag, str) or not tag for tag in tags_raw)
        ):
            raise ScenarioValidationError(
                "scenario.tags must be a non-empty array of strings"
            )
        if len(set(tags_raw)) != len(tags_raw):
            raise ScenarioValidationError("scenario.tags must not contain duplicates")
        surface = raw.get("surface")
        task = raw.get("task")
        if not isinstance(surface, str) or not surface:
            raise ScenarioValidationError("scenario.surface must be a non-empty string")
        if not isinstance(task, str) or not task:
            raise ScenarioValidationError("scenario.task must be a non-empty string")
        fixtures = dict(_require_mapping(raw.get("fixtures", {}), "scenario.fixtures"))
        script_raw = raw.get("provider_script")
        if not isinstance(script_raw, list) or not script_raw:
            raise ScenarioValidationError(
                "scenario.provider_script must be a non-empty array"
            )
        faults_raw = raw.get("faults", [])
        if not isinstance(faults_raw, list):
            raise ScenarioValidationError("scenario.faults must be an array")
        expect_raw = _require_mapping(raw.get("expect"), "expect")
        permissions_raw = _require_mapping(raw.get("permissions", {}), "permissions")
        provider_script = tuple(
            ProviderStep.from_dict(_require_mapping(item, f"provider_script[{i}]"), i)
            for i, item in enumerate(script_raw)
        )
        faults = tuple(
            FaultSpec.from_dict(_require_mapping(item, f"faults[{i}]"), i)
            for i, item in enumerate(faults_raw)
        )
        slots = [(fault.point, fault.occurrence) for fault in faults]
        if len(slots) != len(set(slots)):
            raise ScenarioValidationError(
                "scenario.faults contains duplicate point/occurrence entries"
            )
        return cls(
            schema_version=version,
            id=scenario_id,
            tags=tuple(tags_raw),
            surface=surface,
            task=task,
            fixtures=fixtures,
            provider_script=provider_script,
            faults=faults,
            permissions=PermissionSettings.from_dict(permissions_raw),
            expect=Expectations.from_dict(expect_raw),
            source_path=source_path,
        )


@dataclass(frozen=True)
class EventEnvelope:
    """Versioned append-only event used by deterministic contract traces."""

    schema_version: int
    event_id: str
    seq: int
    run_id: str
    root_frame_id: str
    turn_id: str | None
    parent_event_id: str | None
    kind: str
    phase: str
    status: str
    monotonic_ms: int
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "seq": self.seq,
            "run_id": self.run_id,
            "root_frame_id": self.root_frame_id,
            "turn_id": self.turn_id,
            "parent_event_id": self.parent_event_id,
            "kind": self.kind,
            "phase": self.phase,
            "status": self.status,
            "monotonic_ms": self.monotonic_ms,
            "payload": dict(self.payload),
        }


def load_scenario(path: str | Path) -> Scenario:
    source = Path(path)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScenarioValidationError(f"cannot load scenario {source}: {exc}") from exc
    return Scenario.from_dict(
        _require_mapping(raw, f"scenario {source}"), source_path=source
    )
