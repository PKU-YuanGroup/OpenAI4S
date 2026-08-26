"""Sub-agent delegation and steering services for host RPC."""

from __future__ import annotations

from typing import Any, Callable, Protocol

from openai4s.host import resource_allowlist
from openai4s.specialists import BUILTIN_SPECIALISTS, builtin_specialist


class AgentProfileStore(Protocol):
    def get_agent(self, name: str, **kwargs) -> dict | None: ...


Delegate = Callable[[dict], Any]
DelegateProvider = Callable[[], Delegate | None]
SteeringProvider = Callable[[], dict[str, Callable[..., Any]]]
StoreProvider = Callable[[], AgentProfileStore]
CapabilityScopeProvider = Callable[[], dict[str, str | None]]
SpecialistEnabled = Callable[[str], bool]


#: Compatibility view of the single source of truth in
#: :mod:`openai4s.specialists`. This used to hold exactly one hand-written
#: entry (REMOTE_GPU_PROVISIONER) while the gateway catalog advertised six
#: specialists — the divergence D7 removes. Derived, never edited here.
BUILTIN_SPECIALIST_PROMPTS = {
    name: profile.system_prompt for name, profile in BUILTIN_SPECIALISTS.items()
}


def _with_persona(request: Any, persona: str) -> Any:
    """Prepend the specialist's persona to every request shape `delegate` takes.

    `host.delegate` accepts a string, a dict, or a LIST of either -- the list
    is fan-out, several children from one call. Only the first two shapes were
    handled, so a fan-out to a named specialist silently produced generic
    agents: the profile's system prompt was looked up, found, and then dropped
    on the floor. The caller saw a successful delegation to `bioinfo` whose
    children had never been told they were `bioinfo`.

    Recursive rather than a third branch, because a list of dicts is a shape
    the SDK allows and two flat branches would have missed it the same way.
    """
    if isinstance(request, str):
        return persona + request
    if isinstance(request, dict) and "request" in request:
        return {
            **request,
            "request": _with_persona(request.get("request", ""), persona),
        }
    if isinstance(request, list):
        return [_with_persona(item, persona) for item in request]
    return request


class DelegationService:
    """Inject specialist context and expose the session steering surface."""

    def __init__(
        self,
        *,
        delegate: Delegate | None = None,
        delegate_provider: DelegateProvider | None = None,
        steering: dict[str, Callable[..., Any]] | SteeringProvider,
        store: AgentProfileStore | StoreProvider,
        capability_scope: CapabilityScopeProvider | None = None,
        specialist_enabled: SpecialistEnabled | None = None,
    ) -> None:
        if delegate is not None and delegate_provider is not None:
            raise ValueError("provide delegate or delegate_provider, not both")
        self._delegate_source = delegate
        self._delegate_provider = delegate_provider
        self._steering_source = steering
        self._store_source = store
        self._capability_scope = capability_scope or (lambda: {})
        self._specialist_enabled = specialist_enabled or (lambda _name: True)

    def _delegate(self) -> Delegate | None:
        if self._delegate_provider is not None:
            return self._delegate_provider()
        return self._delegate_source

    def _steering(self) -> dict[str, Callable[..., Any]]:
        source = self._steering_source
        return source() if callable(source) else source

    def _store(self) -> AgentProfileStore:
        source = self._store_source
        return source() if callable(source) else source

    def available(self) -> bool:
        return self._delegate() is not None

    def delegate(self, spec: dict) -> Any:
        delegate = self._delegate()
        if delegate is None:
            raise RuntimeError("host.delegate not available: no sub-agent runner wired")
        name = spec.get("specialist") or spec.get("name")
        if name:
            if not self._specialist_enabled(str(name)):
                raise RuntimeError(
                    f"specialist {name!r} is disabled by capability policy"
                )
            try:
                scope = self._capability_scope()
                try:
                    agent = self._store().get_agent(
                        name,
                        project_id=scope.get("project_id"),
                        session_id=scope.get("session_id"),
                    )
                except TypeError:
                    # Lightweight embedders/test doubles can retain the
                    # historical one-argument Store protocol.
                    agent = self._store().get_agent(name)
            except Exception:  # noqa: BLE001 - optional profile lookup
                agent = None
            builtin = builtin_specialist(str(name))
            builtin_prompt = builtin.system_prompt if builtin is not None else None
            system_prompt = (
                agent.get("system_prompt") if agent else None
            ) or builtin_prompt
            if agent:
                # Profiles may grow richer than the current SQLite form. Keep
                # every supported per-agent execution override on the delegate
                # envelope. A call-site *setting* wins; a call-site
                # *restriction* may only narrow the row's -- see
                # `_with_profile_overrides`, where "always wins" was the defect.
                # A stored row with a builtin's name OVERRIDES the builtin:
                # only the row's overrides apply (its empty prompt still falls
                # back to the builtin persona above).
                spec = _with_profile_overrides(spec, agent)
            elif builtin is not None:
                # The builtin supplies the runtime policy the catalog always
                # advertised: default capabilities and the unrestricted floor
                # ride the spec, so `child_execution_policy(spec)` derives a
                # real ChildExecutionPolicy that `_run_one` arms through the
                # existing `set_child_execution_policy` choke point.
                overrides = builtin.profile_overrides()
                if overrides:
                    spec = _with_profile_overrides(spec, overrides)
            if system_prompt:
                request = spec.get("request")
                persona = (
                    f"You are acting as the specialist **{name}**.\n"
                    f"{system_prompt}\n\n"
                )
                spec = {**spec, "request": _with_persona(request, persona)}
        return delegate(spec)

    def children(self) -> Any:
        function = self._steering().get("children")
        return function() if function else []

    def collect(self, spec: dict) -> Any:
        function = self._steering().get("collect")
        if not function:
            raise RuntimeError("host.collect not available in this session")
        return function(spec)

    def stop_child(self, child_id: str) -> Any:
        function = self._steering().get("stop_child")
        if not function:
            raise RuntimeError("host.stop_child not available")
        return function(child_id)

    def send_message(self, spec: dict) -> Any:
        function = self._steering().get("send_message")
        if not function:
            raise RuntimeError("host.send_message not available")
        return function(spec)

    def stats(self) -> Any:
        function = self._steering().get("delegation_stats")
        return (
            function()
            if function
            else {"total": 0, "running": 0, "done": 0, "failed": 0}
        )


def _with_profile_overrides(spec: dict, profile: dict) -> dict:
    """Merge a stored Specialist row into one `delegate` call's spec.

    Two different merges, because two of these fields are restrictions and the
    rest are settings.

    A setting the caller named wins; the row is only a default. That is what
    `if target not in merged` implements and it is right for model, provider,
    steps and permissions.

    A *restriction* must not work that way, and it did. `skill_names`,
    `connectors` and `unrestricted` all took the call-site value verbatim
    whenever the call named one, so the stored row was advisory: measured
    against a row of `{skill_names: ["only-this"], connectors: [],
    unrestricted: False}`, a call naming three skills got three, a call naming a
    connector got it despite `[]` meaning *denied*, and a call passing
    `unrestricted=True` got it. Both are reachable through the documented
    `host.delegate(...)` signature and the `delegate_task` tool schema, so the
    agent chose its own allowlist and the exit criterion -- "a child may only
    narrow" -- was inverted.

    `resource_allowlist.narrow` is the intersection this needed and it already
    existed; its own docstring warns that treating the child's value as the
    answer "would hand a restricted parent's delegate an unrestricted set, and
    delegation would become the way out of every restriction". Nothing called
    it here. `SkillService.set_allowed_skills` does narrow correctly -- it was
    being handed a value that had already been widened.
    """
    merged = dict(spec)
    for source, target in (
        ("model", "model"),
        ("provider", "provider"),
        ("steps", "steps"),
        ("max_steps", "max_steps"),
        ("max_turns", "max_turns"),
        ("permissions", "permissions"),
        ("capabilities", "capabilities"),
    ):
        if target not in merged and profile.get(source) is not None:
            merged[target] = profile[source]

    # The row is the parent, the call site is the child, and the result may
    # only be tighter than the row. `skills` is the row's legacy spelling of
    # `skill_names`; a row that sets both is intersected with itself, which is
    # the row.
    row_skills: object = profile.get("skill_names")
    if profile.get("skills") is not None:
        row_skills = resource_allowlist.narrow(row_skills, profile.get("skills"))
    for target, row_value in (
        ("skill_names", row_skills),
        ("connectors", profile.get("connectors")),
    ):
        effective = resource_allowlist.narrow(row_value, merged.get(target))
        if effective is not None:
            merged[target] = sorted(effective)
        else:
            merged.pop(target, None)

    # `unrestricted` is a floor, not a default: a row that says False cannot be
    # raised to True by the call it restricts.
    if profile.get("unrestricted") is not None:
        if profile["unrestricted"]:
            merged.setdefault("unrestricted", True)
        else:
            merged["unrestricted"] = False
    return merged


__all__ = ["BUILTIN_SPECIALIST_PROMPTS", "DelegationService"]
