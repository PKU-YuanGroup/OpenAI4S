"""Bind loaded Skill network manifests and admit Cell / shell execution.

Two execution sinks consult this service:

* the next Python/R Cell (``cell_run.execute``)
* shell capability issuance (``BashAuthorizationService.authorize``)

A loaded Skill's manifest is intersected with the measured sandbox posture
(``enforced`` / ``self_test_passed`` / ``network_policy``), Host egress
permission, and the caller's skill allowlist. The manifest never calls
``grant_domain`` and never widens ``OPENAI4S_KERNEL_ALLOW_RAW_NETWORK``.

A declared ``host_only`` manifest limits the network of that Skill's OWN
helper code (its ``kernel.py`` sidecar) to the domains it declares (owner
decision 34). The SDK names the Skill whose sidecar is on the calling stack
(``sdk.host.attribute_caller``) and the dispatcher enters
``frame_scope(frame, caller_skill=...)``; only such an attributed Host request
is held to the manifest. Native tools, the agent's own Cell code and its
``host.bash`` keep the session's ordinary network policy. Loading a
``host_only`` Skill still requires a kernel that cannot reach the network
except through the Host (``admit_load`` / ``admit_cell``), since otherwise its
helpers could bypass the limit.
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
#: The Skill whose helper code made the Host request being served, if any.
_ACTIVE_CALLER_SKILL: ContextVar[str | None] = ContextVar(
    "skill_network_caller_skill", default=None
)
_UNSET: Any = object()

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
    """Serve one Host request for ``frame_id``, made by ``caller_skill``'s helpers.

    Only a request a bound ``host_only`` Skill's own helper code made is held
    to that Skill's declared domains. With no caller Skill -- a native tool,
    the agent's own Cell code -- the scope narrows nothing, including inside
    an outer scope that did.
    """

    def __init__(self, frame_id: str | None, caller_skill: str | None = None) -> None:
        self._frame_id = frame_id
        self._caller_skill = str(caller_skill or "").strip() or None
        self._token: Any = None
        self._caller_token: Any = None
        self._domains_token: Any = None

    def __enter__(self) -> "frame_scope":
        self._token = _CURRENT_FRAME.set(
            str(self._frame_id) if self._frame_id else None
        )
        self._caller_token = _ACTIVE_CALLER_SKILL.set(self._caller_skill)
        self._domains_token = _ACTIVE_HOST_ONLY_DOMAINS.set(
            helper_host_only_domains(self._frame_id, self._caller_skill)
        )
        return self

    def __exit__(self, *exc: object) -> None:
        if self._domains_token is not None:
            _ACTIVE_HOST_ONLY_DOMAINS.reset(self._domains_token)
        if self._caller_token is not None:
            _ACTIVE_CALLER_SKILL.reset(self._caller_token)
        if self._token is not None:
            _CURRENT_FRAME.reset(self._token)


def host_only_declared_domains(frame_id: str | None = None) -> tuple[str, ...] | None:
    """Union of declared host_only destinations, or None if none are bound.

    ``None`` means no ``host_only`` Skill is bound. An empty tuple means one is
    bound with no destinations, so its helpers may reach no host at all. A
    description of the frame's bindings; what a request is held to is
    ``active_host_only_domains``.
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


def helper_bindings(
    frame_id: str | None, skill: str | None
) -> list[SkillNetworkBinding]:
    """The declared ``host_only`` bindings of ``skill`` in the frame."""

    name = str(skill or "").strip()
    if not name:
        return []
    return [
        item
        for item in _declared_host_only(bindings_for(frame_id))
        if item.skill_id == name
    ]


def helper_host_only_domains(
    frame_id: str | None, skill: str | None
) -> tuple[str, ...] | None:
    """Domains ``skill``'s helper code may reach, or None when not limited."""

    bound = helper_bindings(frame_id, skill)
    if not bound:
        return None
    return host_only_declared_domains_from(bound)


def active_host_only_domains() -> tuple[str, ...] | None:
    """Domains the Host request being served is held to, or None.

    Set only by a ``frame_scope`` whose caller is a bound ``host_only``
    Skill's helper code; every other request keeps the session's policy.
    """

    return _ACTIVE_HOST_ONLY_DOMAINS.get()


def active_caller_skill() -> str | None:
    """The Skill whose helper code made the Host request being served."""

    return _ACTIVE_CALLER_SKILL.get()


def _declared_host_only(
    bindings: Sequence[SkillNetworkBinding],
) -> list[SkillNetworkBinding]:
    return [
        item
        for item in bindings
        if item.capability.mode == "host_only"
        and item.capability.declaration == "declared"
    ]


def narrowing_summary(
    frame_id: str | None = None, skill: str | None = None
) -> dict[str, Any] | None:
    """Which loaded Skills' helpers are limited in this frame, and to what.

    With ``skill``, that Skill alone. ``None`` when no matching declared
    ``host_only`` Skill is bound. The domains are what those Skills' own
    helper code may reach; the rest of the session is not limited by them.
    A description for people and the model that grants nothing.
    """

    key = str(frame_id or _CURRENT_FRAME.get() or "").strip()
    host_only = (
        helper_bindings(key, skill) if skill else _declared_host_only(bindings_for(key))
    )
    if not host_only:
        return None
    return {
        "skills": list(dict.fromkeys(item.skill_id for item in host_only)),
        "domains": list(host_only_declared_domains_from(host_only)),
    }


def narrowing_notice(
    frame_id: str | None, candidate: SkillNetworkBinding
) -> dict[str, Any] | None:
    """The notice a load owes the user, or None when nothing changes.

    Only a declared ``host_only`` manifest limits a Skill's helpers, and only
    the first load of a given manifest in a frame says something new. Call
    before binding ``candidate``.
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
    domains = host_only_declared_domains_from(
        [*helper_bindings(frame_id, candidate.skill_id), candidate]
    )
    return {
        "notice": "skill_network",
        "skill": candidate.skill_id,
        "skill_domains": list(candidate.capability.domains),
        "skills": [candidate.skill_id],
        "domains": list(domains),
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
    caller_skill: Any = _UNSET,
) -> AdmissionDecision:
    """Intersect bound manifests with measured posture and Host egress.

    Destinations (``command_domains`` / ``urls``) are held to a manifest only
    for a request ``caller_skill``'s own helpers made; it defaults to the
    caller of the Host request being served (``frame_scope``).
    """

    caller = active_caller_skill() if caller_skill is _UNSET else caller_skill
    return _admit_bindings(
        bindings_for(frame_id),
        sink=sink,
        sandbox_status=sandbox_status,
        command_domains=command_domains,
        urls=urls,
        caller_skill=caller,
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
    caller_skill: str | None = None,
) -> AdmissionDecision:
    if sink == HOST_FETCH_SINK:
        return _admit_host_fetch(_caller_host_only(bindings, caller_skill), urls)
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
        helper = _caller_host_only(host_only, caller_skill)
        if helper:
            # A command a Skill's own helpers run is held to its manifest; the
            # agent's own commands keep the session's network policy.
            return _admit_shell_domains(
                helper, command_domains, sandbox_status, bindings
            )
    return _allow(sink=sink, bindings=bindings, sandbox=sandbox_status)


def _caller_host_only(
    bindings: Sequence[SkillNetworkBinding], caller_skill: str | None
) -> list[SkillNetworkBinding]:
    """The declared ``host_only`` bindings of the calling Skill, if any."""

    name = str(caller_skill or "").strip()
    if not name:
        return []
    return [item for item in _declared_host_only(bindings) if item.skill_id == name]


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


def _limit_subject(skill: str) -> str:
    """How a refusal names the Skill; ``refusal_view`` recognizes it."""

    return f"the {skill} Skill's own helper code"


def _limit_clause(bindings: Sequence[SkillNetworkBinding]) -> str:
    host_only = _declared_host_only(bindings)
    skills = list(dict.fromkeys(item.skill_id for item in host_only))
    declared = host_only_declared_domains_from(host_only)
    if not skills:
        subject = "a loaded Skill's own helper code"
    else:
        subject = " and ".join(_limit_subject(skill) for skill in skills)
    shown = ", ".join(declared[:_LISTED_DOMAINS])
    if len(declared) > _LISTED_DOMAINS:
        shown += ", …"
    count = f"{len(declared)} domain" + ("" if len(declared) == 1 else "s")
    return f"{subject} may only reach {count}" + (f" ({shown})" if shown else "")


#: The other half of every Skill-limit refusal: the limit is not the session's.
_ONLY_HELPERS = (
    "This limit covers only that Skill's own helpers; the rest of this "
    "session's network is unchanged"
)


def undeclared_destination_reason(
    host: str, bindings: Sequence[SkillNetworkBinding]
) -> str:
    """Why a Skill helper's request to ``host`` is refused, in actionable words.

    It names the Skill and the domains its helpers may reach, says approval
    cannot change it, and that nothing else in the session is limited: a
    model told only "refused" spent twenty minutes working around a limit
    that never applied to its own requests.
    """

    return (
        f"{UNDECLARED_MARK} destination {host!r}: {_limit_clause(bindings)}. "
        "The request was not sent, and approving it cannot widen a Skill's "
        f"network limit. {_ONLY_HELPERS}: make this request from your own "
        "code or with a built-in tool (web_fetch, science_search) instead."
    )


def search_engines_reason(engines: Sequence[str]) -> str:
    """Why a Skill helper's web search was refused: no engine is declared.

    A refused search used to come back as "0 results" with the reason in a
    note, so it read as "nothing on the web" and the model retried it.
    """

    named = ", ".join(str(item) for item in engines if item) or "none"
    return (
        f"{UNDECLARED_MARK} any enabled search engine ({named}): "
        f"{_limit_clause(_active_caller_bindings())}. The search was not sent "
        f"and retrying it from the Skill's helpers will not help. "
        f"{_ONLY_HELPERS}: search from your own code or with the web_search "
        "tool instead."
    )


def _active_caller_bindings(frame_id: str | None = None) -> list[SkillNetworkBinding]:
    key = str(frame_id or _CURRENT_FRAME.get() or "").strip()
    return helper_bindings(key, active_caller_skill())


def undeclared_destination_reason_for_frame(
    host: str, frame_id: str | None = None
) -> str:
    """``undeclared_destination_reason`` for the request being served."""

    return undeclared_destination_reason(host, _active_caller_bindings(frame_id))


def refusal_view(reason: str, frame_id: str | None) -> dict[str, Any] | None:
    """The structured "blocked by a Skill helper's network limit" view.

    ``None`` unless ``reason`` is one this module worded. The Skill and domain
    lists come from the frame's bindings, not from parsing the text, which
    elides a long domain list; the Skill is the one the refusal names
    (``_limit_subject``), since the request's own scope has ended by the time
    its card is drawn.
    """

    text = str(reason or "")
    if UNDECLARED_MARK not in text:
        return None
    key = str(frame_id or _CURRENT_FRAME.get() or "").strip()
    named = [
        item
        for item in _declared_host_only(bindings_for(key))
        if _limit_subject(item.skill_id) in text
    ]
    summary = (
        {
            "skills": list(dict.fromkeys(item.skill_id for item in named)),
            "domains": list(host_only_declared_domains_from(named)),
        }
        if named
        else narrowing_summary(key) or {"skills": [], "domains": []}
    )
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
    limit. Uses the frame scope the dispatcher has already entered, so only a
    request a ``host_only`` Skill's own helpers made is refused here.
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
    caller_skill: Any = _UNSET,
) -> AdmissionDecision:
    return admit(
        sink=SHELL_SINK,
        frame_id=frame_id,
        sandbox_status=sandbox_status,
        command_domains=command_domains,
        caller_skill=caller_skill,
    )


def constrain_check_url(url: str) -> str | None:
    """Return a blocked-domain host if the calling Skill's manifest forbids ``url``.

    Only a request a bound ``host_only`` Skill's own helper code made is held
    to its manifest (``frame_scope``). Does not grant access. ``None`` means
    this layer has no extra denial; ``check_url`` still applies the Host
    allowlist.
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
