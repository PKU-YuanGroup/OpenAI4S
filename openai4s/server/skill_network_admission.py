"""Bind loaded Skill network manifests and admit Cell / shell execution.

Two execution sinks consult this service:

* the next Python/R Cell (``cell_run.execute``)
* shell capability issuance (``BashAuthorizationService.authorize``)

A loaded Skill's manifest is intersected with the measured sandbox posture
(``enforced`` / ``self_test_passed`` / ``network_policy``), Host egress
permission, and the caller's skill allowlist. The manifest never calls
``grant_domain`` and never widens ``OPENAI4S_KERNEL_ALLOW_RAW_NETWORK``.
"""

from __future__ import annotations

import threading
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from openai4s.skills_loader.capabilities import (
    NetworkCapability,
    canonical_network_digest,
)

_LOCK = threading.RLock()
# frame_id -> bindings in load order
_BINDINGS: dict[str, list["SkillNetworkBinding"]] = {}
_CURRENT_FRAME: ContextVar[str | None] = ContextVar(
    "skill_network_frame_id", default=None
)
_ACTIVE_HOST_ONLY_DOMAINS: ContextVar[tuple[str, ...] | None] = ContextVar(
    "skill_network_host_only_domains", default=None
)

CELL_SINK = "cell"
SHELL_SINK = "shell"
HOST_FETCH_SINK = "host_fetch"


@dataclass(frozen=True)
class SkillNetworkBinding:
    skill_id: str
    version: str
    document_digest: str
    manifest_digest: str
    action_group_id: str | None
    capability: NetworkCapability
    source: str

    def provenance(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "document_digest": self.document_digest,
            "manifest_digest": self.manifest_digest,
            "action_group_id": self.action_group_id,
            "source": self.source,
            "mode": self.capability.mode,
            "declaration": self.capability.declaration,
            "domains": list(self.capability.domains),
        }


@dataclass(frozen=True)
class AdmissionDecision:
    allowed: bool
    reason: str | None
    blocked_on: tuple[str, ...]
    sink: str
    bindings: tuple[SkillNetworkBinding, ...] = ()
    sandbox: Mapping[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "blocked_on": list(self.blocked_on),
            "sink": self.sink,
            "manifest_digests": [item.manifest_digest for item in self.bindings],
            "bindings": [item.provenance() for item in self.bindings],
            "sandbox": (
                {
                    "enforced": (self.sandbox or {}).get("enforced"),
                    "self_test_passed": (self.sandbox or {}).get("self_test_passed"),
                    "network_policy": (self.sandbox or {}).get("network_policy"),
                    "backend": (self.sandbox or {}).get("backend"),
                }
                if self.sandbox is not None
                else None
            ),
            **self.extra,
        }

    def refusal_message(self) -> str:
        return self.reason or "skill network admission denied"


def reset_bindings(frame_id: str | None = None) -> None:
    """Test helper: drop bindings for one frame or every frame."""

    with _LOCK:
        if frame_id is None:
            _BINDINGS.clear()
            return
        _BINDINGS.pop(str(frame_id), None)


def bind_skill_load(
    *,
    frame_id: str | None,
    action_group_id: str | None,
    skill_id: str,
    version: str,
    document_digest: str,
    capability: NetworkCapability,
    source: str,
) -> SkillNetworkBinding:
    binding = SkillNetworkBinding(
        skill_id=str(skill_id or ""),
        version=str(version or ""),
        document_digest=str(document_digest or ""),
        manifest_digest=str(
            capability.digest
            or canonical_network_digest(capability.mode, capability.domains)
        ),
        action_group_id=str(action_group_id) if action_group_id else None,
        capability=capability,
        source=str(source or "load_skill"),
    )
    key = str(frame_id or "").strip()
    if not key:
        return binding
    with _LOCK:
        current = list(_BINDINGS.get(key) or [])
        current.append(binding)
        _BINDINGS[key] = current
    return binding


def bindings_for(frame_id: str | None) -> tuple[SkillNetworkBinding, ...]:
    key = str(frame_id or "").strip()
    if not key:
        return ()
    with _LOCK:
        return tuple(_BINDINGS.get(key) or ())


def use_frame(frame_id: str | None) -> Any:
    """Bind the current Host/Cell thread to a session frame for check_url."""

    token = _CURRENT_FRAME.set(str(frame_id) if frame_id else None)
    return token


def reset_frame(token: Any) -> None:
    _CURRENT_FRAME.reset(token)


class frame_scope:
    def __init__(self, frame_id: str | None) -> None:
        self._frame_id = frame_id
        self._token: Any = None
        self._domains_token: Any = None

    def __enter__(self) -> "frame_scope":
        self._token = _CURRENT_FRAME.set(
            str(self._frame_id) if self._frame_id else None
        )
        domains = host_only_declared_domains(self._frame_id)
        self._domains_token = _ACTIVE_HOST_ONLY_DOMAINS.set(
            domains if domains is not None else None
        )
        return self

    def __exit__(self, *exc: object) -> None:
        if self._domains_token is not None:
            _ACTIVE_HOST_ONLY_DOMAINS.reset(self._domains_token)
        if self._token is not None:
            _CURRENT_FRAME.reset(self._token)


def host_only_declared_domains(frame_id: str | None = None) -> tuple[str, ...] | None:
    """Union of declared host_only destinations, or None if none are bound.

    ``None`` means no extra constraint. An empty tuple means a host_only Skill
    is bound with no destinations, so every host is undeclared.
    """

    key = str(frame_id or _CURRENT_FRAME.get() or "").strip()
    bindings = bindings_for(key) if key else bindings_for(_CURRENT_FRAME.get())
    declared: list[str] = []
    found = False
    for binding in bindings:
        if binding.capability.mode != "host_only":
            continue
        if binding.capability.declaration != "declared":
            continue
        found = True
        declared.extend(binding.capability.domains)
    if not found:
        return None
    return tuple(sorted(set(declared)))


def active_host_only_domains() -> tuple[str, ...] | None:
    current = _ACTIVE_HOST_ONLY_DOMAINS.get()
    if current is not None:
        return current
    return host_only_declared_domains(_CURRENT_FRAME.get())


def host_only_boundary_holds(status: Mapping[str, Any] | None) -> bool:
    """True only when the OS sandbox is the measured Host-only boundary."""

    if not isinstance(status, Mapping):
        return False
    if status.get("backend") == "remote":
        return False
    return (
        status.get("enforced") is True
        and status.get("self_test_passed") is True
        and status.get("network_policy") == "blocked"
    )


def _raw_env_enabled() -> bool:
    import os

    value = (os.environ.get("OPENAI4S_KERNEL_ALLOW_RAW_NETWORK") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _deny(
    *,
    sink: str,
    reason: str,
    blocked_on: Sequence[str],
    bindings: Sequence[SkillNetworkBinding],
    sandbox: Mapping[str, Any] | None,
    extra: dict[str, Any] | None = None,
) -> AdmissionDecision:
    return AdmissionDecision(
        allowed=False,
        reason=reason,
        blocked_on=tuple(blocked_on),
        sink=sink,
        bindings=tuple(bindings),
        sandbox=status_view(sandbox),
        extra=extra or {},
    )


def _allow(
    *,
    sink: str,
    bindings: Sequence[SkillNetworkBinding],
    sandbox: Mapping[str, Any] | None,
    extra: dict[str, Any] | None = None,
) -> AdmissionDecision:
    return AdmissionDecision(
        allowed=True,
        reason=None,
        blocked_on=(),
        sink=sink,
        bindings=tuple(bindings),
        sandbox=status_view(sandbox),
        extra=extra or {},
    )


def status_view(status: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(status, Mapping):
        return None
    return {
        "enforced": status.get("enforced"),
        "self_test_passed": status.get("self_test_passed"),
        "network_policy": status.get("network_policy"),
        "backend": status.get("backend"),
        "state": status.get("state"),
        "mode": status.get("mode"),
    }


def admit(
    *,
    sink: str,
    frame_id: str | None,
    sandbox_status: Mapping[str, Any] | None,
    command_domains: Sequence[str] = (),
    urls: Sequence[str] = (),
) -> AdmissionDecision:
    """Intersect bound manifests with measured posture and Host egress."""

    bindings = bindings_for(frame_id)
    if sink == HOST_FETCH_SINK:
        return _admit_host_fetch(bindings, urls)
    if not bindings:
        return _allow(sink=sink, bindings=(), sandbox=sandbox_status)

    raw_required = [
        item
        for item in bindings
        if item.capability.mode == "raw_required"
        and item.capability.declaration == "declared"
    ]
    if raw_required:
        return _deny(
            sink=sink,
            reason=(
                "skill requires raw kernel network and is blocked in this "
                "version (OPENAI4S_KERNEL_ALLOW_RAW_NETWORK does not grant it)"
            ),
            blocked_on=("raw_network",),
            bindings=raw_required,
            sandbox=sandbox_status,
            extra={"compat_raw_env": _raw_env_enabled()},
        )

    host_only = [
        item
        for item in bindings
        if item.capability.mode == "host_only"
        and item.capability.declaration == "declared"
    ]
    if not host_only:
        return _allow(sink=sink, bindings=bindings, sandbox=sandbox_status)

    if not host_only_boundary_holds(sandbox_status):
        blocked = []
        status = sandbox_status or {}
        if (
            status.get("backend") == "remote"
            or status.get("self_test_passed") is not True
        ):
            blocked.append("sandbox_unproven")
        if status.get("enforced") is not True:
            blocked.append("sandbox_not_enforced")
        if status.get("network_policy") == "raw_allowed" or _raw_env_enabled():
            blocked.append("raw_network_allowed")
        if status.get("network_policy") in {"not_enforced", "unproven", None}:
            blocked.append("network_policy")
        if not blocked:
            blocked.append("sandbox_unproven")
        return _deny(
            sink=sink,
            reason=(
                "skill requires Host-only network but the measured sandbox "
                "does not confine the kernel to the Host path"
            ),
            blocked_on=tuple(dict.fromkeys(blocked)),
            bindings=host_only,
            sandbox=sandbox_status,
        )

    if sink == SHELL_SINK:
        return _admit_shell_domains(
            host_only, command_domains, sandbox_status, bindings
        )
    return _allow(sink=sink, bindings=bindings, sandbox=sandbox_status)


def _admit_shell_domains(
    host_only: Sequence[SkillNetworkBinding],
    command_domains: Sequence[str],
    sandbox_status: Mapping[str, Any] | None,
    bindings: Sequence[SkillNetworkBinding],
) -> AdmissionDecision:
    from openai4s import egress

    declared = host_only_declared_domains_from(host_only)
    for host in command_domains:
        if not domain_in_declared(host, declared):
            return _deny(
                sink=SHELL_SINK,
                reason=(
                    f"skill host_only manifest does not declare destination {host!r}"
                ),
                blocked_on=("undeclared_domain",),
                bindings=host_only,
                sandbox=sandbox_status,
                extra={"domain": host},
            )
        if not egress.domain_allowed(host):
            return _deny(
                sink=SHELL_SINK,
                reason=egress.blocked_message(host),
                blocked_on=("unapproved_domain",),
                bindings=host_only,
                sandbox=sandbox_status,
                extra={"domain": host},
            )
    return _allow(sink=SHELL_SINK, bindings=bindings, sandbox=sandbox_status)


def _admit_host_fetch(
    bindings: Sequence[SkillNetworkBinding],
    urls: Sequence[str],
) -> AdmissionDecision:
    host_only = [
        item
        for item in bindings
        if item.capability.mode == "host_only"
        and item.capability.declaration == "declared"
    ]
    if not host_only:
        return _allow(sink=HOST_FETCH_SINK, bindings=bindings, sandbox=None)
    declared = host_only_declared_domains_from(host_only)
    from openai4s.egress import domain_of

    for url in urls:
        host = domain_of(url) if "://" in str(url) else str(url)
        if not host:
            continue
        if not domain_in_declared(host, declared):
            return _deny(
                sink=HOST_FETCH_SINK,
                reason=(
                    f"skill host_only manifest does not declare destination {host!r}"
                ),
                blocked_on=("undeclared_domain",),
                bindings=host_only,
                sandbox=None,
                extra={"domain": host, "url": url},
            )
    return _allow(sink=HOST_FETCH_SINK, bindings=bindings, sandbox=None)


def host_only_declared_domains_from(
    bindings: Sequence[SkillNetworkBinding],
) -> tuple[str, ...]:
    declared: list[str] = []
    for binding in bindings:
        declared.extend(binding.capability.domains)
    return tuple(sorted(set(declared)))


def domain_in_declared(host: str, declared: Sequence[str]) -> bool:
    from openai4s.egress import domain_of

    candidate = domain_of(host) or str(host or "").strip().lower().rstrip(".")
    if not candidate:
        return False
    if not declared:
        return False
    for item in declared:
        allowed = domain_of(item) or str(item or "").strip().lower().rstrip(".")
        if not allowed:
            continue
        if candidate == allowed or candidate.endswith("." + allowed):
            return True
    return False


def raw_required_binding(frame_id: str | None) -> SkillNetworkBinding | None:
    """A bound Skill this version refuses outright, or None.

    Separate from `admit` because this half needs no measured posture: a
    declared `raw_required` manifest is denied whatever the sandbox reports,
    including with the compatibility env var set. That lets a caller with no
    live kernel -- the CLI and delegated children, which reach a Cell through
    `LocalActionExecutor` rather than `CellExecutionService` -- apply the
    unconditional refusal without spawning a worker to ask about confinement.

    The `host_only` half genuinely depends on posture and is not answered
    here; those callers still admit less than the Web path does.
    """

    for item in bindings_for(frame_id):
        if (
            item.capability.mode == "raw_required"
            and item.capability.declaration == "declared"
        ):
            return item
    return None


def admit_cell(
    *,
    frame_id: str | None,
    sandbox_status: Mapping[str, Any] | None,
) -> AdmissionDecision:
    return admit(
        sink=CELL_SINK,
        frame_id=frame_id,
        sandbox_status=sandbox_status,
    )


def admit_shell(
    *,
    frame_id: str | None,
    sandbox_status: Mapping[str, Any] | None,
    command_domains: Sequence[str] = (),
) -> AdmissionDecision:
    return admit(
        sink=SHELL_SINK,
        frame_id=frame_id,
        sandbox_status=sandbox_status,
        command_domains=command_domains,
    )


def constrain_check_url(url: str) -> str | None:
    """Return a blocked-domain host if a bound host_only Skill forbids ``url``.

    Does not grant access. ``None`` means this layer has no extra denial;
    ``check_url`` still applies the Host allowlist.
    """

    from openai4s.egress import domain_of

    declared = active_host_only_domains()
    if declared is None:
        return None
    host = domain_of(url)
    if not host:
        return None
    if domain_in_declared(host, declared):
        return None
    return host


def load_event_metadata(
    *,
    skill_id: str,
    version: str,
    document_digest: str,
    capability: NetworkCapability,
    action_group_id: str | None,
    source: str,
) -> dict[str, Any]:
    return {
        "skill_id": skill_id,
        "version": version,
        "document_digest": document_digest,
        "manifest_digest": capability.digest,
        "network_mode": capability.mode,
        "network_declaration": capability.declaration,
        "action_group_id": action_group_id,
        "source": source,
        "domains": list(capability.domains),
    }


# Imported by tests that assert the two sinks cannot skip admission.
ADMISSION_SINKS = (CELL_SINK, SHELL_SINK)
