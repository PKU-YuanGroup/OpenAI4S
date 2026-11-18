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
    DECLARATIONS,
    NETWORK_MODES,
    UNKNOWN_MODE,
    NetworkCapability,
    canonical_network_digest,
    declared_capability,
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


def skill_binding(
    *,
    action_group_id: str | None,
    skill_id: str,
    version: str,
    document_digest: str,
    capability: NetworkCapability,
    source: str,
) -> SkillNetworkBinding:
    """The requirement one load would bind, built without binding it."""

    return SkillNetworkBinding(
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
    binding = skill_binding(
        action_group_id=action_group_id,
        skill_id=skill_id,
        version=version,
        document_digest=document_digest,
        capability=capability,
        source=source,
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


def restore_bindings(
    frame_id: str | None, events: Sequence[Mapping[str, Any]]
) -> tuple[SkillNetworkBinding, ...]:
    """Restore durable ``skill_loaded`` requirements after daemon restart.

    The audit row is treated as authorization-relevant input: a malformed or
    digest-mismatched row fails closed instead of silently dropping the Skill's
    network requirement.
    """

    key = str(frame_id or "").strip()
    if not key:
        return ()
    restored: list[SkillNetworkBinding] = []
    for event in reversed(list(events)):
        if str(event.get("event") or "") != "skill_loaded":
            continue
        metadata = event.get("metadata")
        if not isinstance(metadata, Mapping):
            raise ValueError("persisted Skill network binding lacks metadata")
        binding_frame_id = str(metadata.get("binding_frame_id") or "").strip()
        # New rows are exact-frame requirements. Rows written before that key
        # existed cannot prove which frame loaded executable guidance, so they
        # retain the old session-wide fail-closed behavior rather than being
        # silently discarded during upgrade.
        if binding_frame_id and binding_frame_id != key:
            continue
        mode = str(metadata.get("network_mode") or "").strip().lower()
        declaration = str(metadata.get("network_declaration") or "").strip().lower()
        source = str(metadata.get("source") or "persisted")
        raw_domains = metadata.get("domains")
        if not isinstance(raw_domains, list) or declaration not in DECLARATIONS:
            raise ValueError("persisted Skill network binding is malformed")
        if declaration == "declared":
            if mode not in NETWORK_MODES:
                raise ValueError("persisted Skill network mode is invalid")
            capability = declared_capability(mode, raw_domains, source=source)
        else:
            if mode != UNKNOWN_MODE or raw_domains:
                raise ValueError("persisted legacy Skill network binding widened")
            capability = NetworkCapability(
                mode=UNKNOWN_MODE,
                domains=(),
                declaration=declaration,
                source=source,
                digest=canonical_network_digest(UNKNOWN_MODE, ()),
                explicit=True,
            )
        manifest_digest = str(metadata.get("manifest_digest") or "")
        if manifest_digest != capability.digest:
            raise ValueError("persisted Skill network manifest digest mismatch")
        restored.append(
            SkillNetworkBinding(
                skill_id=str(metadata.get("skill_id") or event.get("name") or ""),
                version=str(metadata.get("version") or ""),
                document_digest=str(metadata.get("document_digest") or ""),
                manifest_digest=manifest_digest,
                action_group_id=(
                    str(metadata["action_group_id"])
                    if metadata.get("action_group_id")
                    else None
                ),
                capability=capability,
                source=source,
            )
        )
    with _LOCK:
        current = list(_BINDINGS.get(key) or ())
        identities = {
            (
                item.skill_id,
                item.version,
                item.document_digest,
                item.manifest_digest,
                item.action_group_id,
                item.source,
            )
            for item in current
        }
        for item in restored:
            identity = (
                item.skill_id,
                item.version,
                item.document_digest,
                item.manifest_digest,
                item.action_group_id,
                item.source,
            )
            if identity not in identities:
                current.append(item)
                identities.add(identity)
        if current:
            _BINDINGS[key] = current
        return tuple(current)


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


def _declared_host_only(
    bindings: Sequence[SkillNetworkBinding],
) -> list[SkillNetworkBinding]:
    return [
        item
        for item in bindings
        if item.capability.mode == "host_only"
        and item.capability.declaration == "declared"
    ]


def narrowing_summary(frame_id: str | None = None) -> dict[str, Any] | None:
    """Which loaded Skills narrow this frame's network, and to what.

    ``None`` when no declared ``host_only`` Skill is bound. The domain list is
    the union every Host request is held to, the same one ``check_url`` and
    shell admission read; it is a description for people and the model and
    grants nothing.
    """

    key = str(frame_id or _CURRENT_FRAME.get() or "").strip()
    host_only = _declared_host_only(bindings_for(key))
    if not host_only:
        return None
    return {
        "skills": list(dict.fromkeys(item.skill_id for item in host_only)),
        "domains": list(host_only_declared_domains_from(host_only)),
    }


def narrowing_notice(
    frame_id: str | None, candidate: SkillNetworkBinding
) -> dict[str, Any] | None:
    """The session notice a load owes the user, or None when nothing changes.

    Only a declared ``host_only`` manifest narrows the network, and only the
    first load of a given manifest in a frame changes what the session can
    reach; a repeat load says nothing new. Call before binding ``candidate``.
    """

    if candidate.capability.mode != "host_only":
        return None
    if candidate.capability.declaration != "declared":
        return None
    for item in bindings_for(frame_id):
        if (
            item.skill_id == candidate.skill_id
            and item.manifest_digest == candidate.manifest_digest
        ):
            return None
    bound = _declared_host_only(bindings_for(frame_id))
    union = host_only_declared_domains_from([*bound, candidate])
    skills = list(
        dict.fromkeys([*(item.skill_id for item in bound), candidate.skill_id])
    )
    return {
        "notice": "skill_network",
        "skill": candidate.skill_id,
        "skill_domains": list(candidate.capability.domains),
        "skills": skills,
        "domains": list(union),
    }


def host_only_boundary_holds(status: Mapping[str, Any] | None) -> bool:
    """True only when the OS sandbox is the measured Host-only boundary."""

    if not isinstance(status, Mapping):
        return False
    from openai4s.security.sandbox import boundary_holds

    return boundary_holds(status)


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


def _raw_required_denial(
    *,
    sink: str,
    bindings: Sequence[SkillNetworkBinding],
    sandbox: Mapping[str, Any] | None,
) -> AdmissionDecision | None:
    """The half of admission that needs no measured posture, or None.

    A declared `raw_required` manifest is refused whatever the sandbox
    reports, so this answer is available before a worker exists. Both the
    full `admit` and the pre-bootstrap preflight route through here: the
    refusal message and `blocked_on` are one string in one place, not a
    second copy at each sink that happens to need the early answer.
    """

    raw_required = [
        item
        for item in bindings
        if item.capability.mode == "raw_required"
        and item.capability.declaration == "declared"
    ]
    if not raw_required:
        return None
    return _deny(
        sink=sink,
        reason=(
            "skill requires raw kernel network and is blocked in this "
            "version (OPENAI4S_KERNEL_ALLOW_RAW_NETWORK does not grant it)"
        ),
        blocked_on=("raw_network",),
        bindings=raw_required,
        sandbox=sandbox,
        extra={"compat_raw_env": _raw_env_enabled()},
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

    return _admit_bindings(
        bindings_for(frame_id),
        sink=sink,
        sandbox_status=sandbox_status,
        command_domains=command_domains,
        urls=urls,
    )


def admit_load(
    *,
    frame_id: str | None,
    candidate: SkillNetworkBinding,
    sandbox_status: Mapping[str, Any] | None,
) -> AdmissionDecision:
    """Decide a load against the frame's requirements plus ``candidate``.

    The candidate is judged before it is bound. A refused load hands over no
    recipe, so it must leave no requirement behind either: bound first, a
    refused ``host_only`` manifest kept narrowing every later Host request in
    the frame, and a refused ``raw_required`` one kept refusing every Cell.
    """

    return _admit_bindings(
        (*bindings_for(frame_id), candidate),
        sink=LOAD_SINK,
        sandbox_status=sandbox_status,
    )


def load_needs_posture(frame_id: str | None, candidate: SkillNetworkBinding) -> bool:
    """Whether admitting ``candidate`` into the frame depends on a posture.

    Only a declared ``host_only`` requirement does, the frame's or the
    candidate's. ``raw_required`` is refused on any posture, and it is
    refused first.
    """

    modes = {
        item.capability.mode
        for item in (*bindings_for(frame_id), candidate)
        if item.capability.declaration == "declared"
    }
    return "host_only" in modes and "raw_required" not in modes


def unstarted_kernel_posture() -> dict[str, Any] | None:
    """The posture this daemon gives a new Kernel, measured without one.

    A native ``load_skill`` is served outside any worker, so there is no
    worker posture to read. This builds the sandbox a Kernel would get, in a
    temp workspace it removes, which runs the same self-test. ``None`` when
    it cannot be built.

    It answers one question: whether to hand the recipe over. It admits no
    code. Every execution sink still measures the worker it actually runs on,
    and that worker can differ from this one (a cluster node, a workspace the
    self-test fails in).
    """

    import tempfile

    from openai4s.security.sandbox import create_kernel_sandbox

    try:
        with tempfile.TemporaryDirectory(prefix="openai4s-load-posture-") as probe:
            sandbox = create_kernel_sandbox(probe)
            try:
                return dict(sandbox.status.to_dict())
            finally:
                sandbox.close()
    except Exception:  # noqa: BLE001 - an unbuildable sandbox proves nothing
        return None


def _admit_bindings(
    bindings: Sequence[SkillNetworkBinding],
    *,
    sink: str,
    sandbox_status: Mapping[str, Any] | None,
    command_domains: Sequence[str] = (),
    urls: Sequence[str] = (),
) -> AdmissionDecision:
    if sink == HOST_FETCH_SINK:
        return _admit_host_fetch(bindings, urls)
    if not bindings:
        return _allow(sink=sink, bindings=(), sandbox=sandbox_status)

    denial = _raw_required_denial(sink=sink, bindings=bindings, sandbox=sandbox_status)
    if denial is not None:
        return denial

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
                reason=undeclared_destination_reason(host, host_only),
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
                reason=undeclared_destination_reason(host, host_only),
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


#: Every Skill-limit refusal names its cause with this phrase. The phrase is
#: also how a Step card recognizes the refusal (``refusal_view``); both come
#: from this module, so the wording and the recognizer cannot drift apart.
UNDECLARED_MARK = "skill host_only manifest does not declare"
_LISTED_DOMAINS = 24


def _limit_clause(bindings: Sequence[SkillNetworkBinding]) -> str:
    host_only = _declared_host_only(bindings)
    skills = list(dict.fromkeys(item.skill_id for item in host_only))
    declared = host_only_declared_domains_from(host_only)
    if not skills:
        subject = "a loaded Skill limits"
    elif len(skills) == 1:
        subject = f"the loaded {skills[0]} Skill limits"
    else:
        subject = f"the loaded {', '.join(skills)} Skills limit"
    shown = ", ".join(declared[:_LISTED_DOMAINS])
    if len(declared) > _LISTED_DOMAINS:
        shown += ", …"
    count = f"{len(declared)} domain" + ("" if len(declared) == 1 else "s")
    return f"{subject} this session's network to {count}" + (
        f" ({shown})" if shown else ""
    )


def undeclared_destination_reason(
    host: str, bindings: Sequence[SkillNetworkBinding]
) -> str:
    """Why a request to ``host`` is refused, in words a person can act on.

    It used to be only the first clause, which named neither the Skill nor
    the domains it allows, and the Cell then rewrote it to "host authorization
    unavailable". Approval is named because the request is refused whatever
    anyone approves.
    """

    return (
        f"{UNDECLARED_MARK} destination {host!r}: {_limit_clause(bindings)}. "
        "The request was not sent, and approving it cannot widen a Skill's "
        "network limit; use one of those domains or the Skill's own helpers."
    )


def search_engines_reason(engines: Sequence[str]) -> str:
    """Why a web search was refused: no enabled engine is a declared host.

    A refused search used to come back as "0 results" with the reason in a
    note, so it read as "nothing on the web" and the model retried it.
    """

    key = str(_CURRENT_FRAME.get() or "").strip()
    named = ", ".join(str(item) for item in engines if item) or "none"
    return (
        f"{UNDECLARED_MARK} any enabled search engine ({named}): "
        f"{_limit_clause(bindings_for(key))}. The search was not sent and "
        "retrying will not help; use the Skill's own lookup helpers, or "
        "host.web_fetch on one of those domains."
    )


def undeclared_destination_reason_for_frame(
    host: str, frame_id: str | None = None
) -> str:
    """``undeclared_destination_reason`` for the current (or given) frame."""

    key = str(frame_id or _CURRENT_FRAME.get() or "").strip()
    return undeclared_destination_reason(host, bindings_for(key))


def refusal_view(reason: str, frame_id: str | None) -> dict[str, Any] | None:
    """The structured "blocked by a Skill's network limit" view of a refusal.

    ``None`` unless ``reason`` is one this module worded. The Skill and domain
    lists come from the frame's bindings, not from parsing the text, which
    elides a long domain list.
    """

    text = str(reason or "")
    if UNDECLARED_MARK not in text:
        return None
    summary = narrowing_summary(frame_id) or {"skills": [], "domains": []}
    destination = ""
    marker = f"{UNDECLARED_MARK} destination "
    start = text.find(marker)
    if start >= 0:
        rest = text[start + len(marker) :]
        if rest[:1] in {"'", '"'}:
            end = rest.find(rest[0], 1)
            if end > 0:
                destination = rest[1:end]
    return {
        "by": "skill_network",
        "skills": list(summary["skills"]),
        "domains": list(summary["domains"]),
        "destination": destination,
        "search": f"{UNDECLARED_MARK} any enabled search engine" in text,
    }


#: Host methods whose single target a Skill limit can refuse up front.
_TARGET_KEYS = {
    "web_fetch": "url",
    "web_download": "url",
    "request_network_access": "domain",
}


def request_refusal(method: str, spec: Any) -> str | None:
    """A Skill-limit refusal for a request that would otherwise ask for approval.

    The approval gate ran before the handler, so a request the bound manifest
    was always going to refuse first asked the user, and the "allowed" answer
    then failed. Answering here, before the gate, keeps the user from being
    asked to approve something that cannot happen. Granting network access to
    an undeclared domain is the same case: the grant cannot widen a Skill's
    limit. Uses the frame scope the dispatcher has already entered.
    """

    key = _TARGET_KEYS.get(method)
    if key is None or not isinstance(spec, Mapping):
        return None
    target = str(spec.get(key) or "").strip()
    if not target:
        return None
    from openai4s.egress import domain_of

    declared = active_host_only_domains()
    if declared is None:
        return None
    host = domain_of(target)
    if not host or domain_in_declared(host, declared):
        return None
    return undeclared_destination_reason_for_frame(host)


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


def admit_cell_preflight(*, frame_id: str | None) -> AdmissionDecision:
    """Cell admission's posture-independent half, for use before bootstrap.

    `CellExecutionService` cannot run full admission first: the measured
    posture it intersects comes from the worker that `prepare_language`
    prepares. But `prepare_language` also runs Skill bootstrap -- sidecar
    imports, `origin="system"` -- so waiting for it means a refused Skill's
    code has already executed by the time the user Cell is denied. This
    answers the unconditional half early; the full `admit_cell` still runs
    afterwards for `host_only`, which genuinely needs the posture.
    """

    bindings = bindings_for(frame_id)
    denial = _raw_required_denial(sink=CELL_SINK, bindings=bindings, sandbox=None)
    if denial is not None:
        return denial
    return _allow(sink=CELL_SINK, bindings=bindings, sandbox=None)


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
    binding_frame_id: str | None = None,
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
        "binding_frame_id": str(binding_frame_id or ""),
        "domains": list(capability.domains),
    }


# Imported by tests that assert the two sinks cannot skip admission.
#: Every path that can reach executable code, named so an omission is a
#: failing test rather than a grep that happened not to look. Two of these
#: were found by review after shipping: the CLI/delegation Cell hook and the
#: background executor both ran while this tuple said the surface was two
#: names wide.
CLI_CELL_SINK = "cli_cell"
BACKGROUND_SINK = "exec_background"
LOAD_SINK = "load_skill"

ADMISSION_SINKS = (
    CELL_SINK,
    SHELL_SINK,
    CLI_CELL_SINK,
    BACKGROUND_SINK,
    LOAD_SINK,
)
