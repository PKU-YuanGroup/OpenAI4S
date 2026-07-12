"""Sub-agent delegation and steering services for host RPC."""

from __future__ import annotations

from typing import Any, Callable, Protocol


class AgentProfileStore(Protocol):
    def get_agent(self, name: str, **kwargs) -> dict | None:
        ...


Delegate = Callable[[dict], Any]
DelegateProvider = Callable[[], Delegate | None]
SteeringProvider = Callable[[], dict[str, Callable[..., Any]]]
StoreProvider = Callable[[], AgentProfileStore]
CapabilityScopeProvider = Callable[[], dict[str, str | None]]
SpecialistEnabled = Callable[[str], bool]


BUILTIN_SPECIALIST_PROMPTS = {
    "REMOTE_GPU_PROVISIONER": """\
You are the remote-GPU provisioning specialist. Your job is to turn a user-added
SSH GPU host into real, verified services that the main scientist can call.

Protocol:
1. Inspect the current state with `host.remote_gpu_status()` and choose the
   default/reachable SSH alias unless the user named a specific one.
2. Use visible shell steps (`host.bash("ssh <alias> ...")`) to inspect the
   remote host, create a scratch/service directory, and install or locate real
   model runners. Prefer existing scripts/environments already present on the
   host before downloading anything large.
3. Provision only real services. For this app the important capabilities are:
   `fold` (a wrapper consumed by `host.fold`) and `score_mutations` (an ESM
   masked-marginal wrapper consumed by `host.score_mutations`). If you also
   provision ProteinMPNN or another method, register it under a clear capability
   name such as `proteinmpnn`.
4. Verify before registering. A capability must have either a verified script
   path or a structured `path_exists` / `executable_exists` probe that exits 0
   on the remote host. Then call
   `host.register_remote_capability(alias, capability, script=..., engine=...,
   invoke=..., markers=..., probe={"kind":"path_exists","path":...})`.
5. If provisioning cannot be completed, return a concise blocking reason and the
   exact remote checks you ran. Never claim a model is configured until verified.
""",
}


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
            builtin_prompt = BUILTIN_SPECIALIST_PROMPTS.get(str(name).upper())
            system_prompt = (
                agent.get("system_prompt") if agent else None
            ) or builtin_prompt
            if agent:
                # Profiles may grow richer than the current SQLite form. Keep
                # every supported per-agent execution override on the delegate
                # envelope, while an explicit call-site value always wins.
                spec = _with_profile_overrides(spec, agent)
            if system_prompt:
                request = spec.get("request")
                persona = (
                    f"You are acting as the specialist **{name}**.\n"
                    f"{system_prompt}\n\n"
                )
                if isinstance(request, str):
                    spec = {**spec, "request": persona + request}
                elif isinstance(request, dict) and "request" in request:
                    spec = {
                        **spec,
                        "request": {
                            **request,
                            "request": persona + str(request.get("request", "")),
                        },
                    }
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
    merged = dict(spec)
    for source, target in (
        ("model", "model"),
        ("provider", "provider"),
        ("steps", "steps"),
        ("max_steps", "max_steps"),
        ("max_turns", "max_turns"),
        ("permissions", "permissions"),
        ("capabilities", "capabilities"),
        ("skill_names", "skill_names"),
        ("skills", "skill_names"),
        ("connectors", "connectors"),
        ("unrestricted", "unrestricted"),
    ):
        if target not in merged and profile.get(source) is not None:
            merged[target] = profile[source]
    return merged


__all__ = ["BUILTIN_SPECIALIST_PROMPTS", "DelegationService"]
