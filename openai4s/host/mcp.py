"""MCP connector discovery and tool invocation for host RPC calls.

The service deliberately keeps the connector lookup and error boundary used by
the legacy dispatcher.  Policy such as permission gating and untrusted-output
screening remains outside this class.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol


class MCPStore(Protocol):
    """Minimal connector persistence used by :class:`MCPService`."""

    def get_connector(self, connector_id: str) -> dict | None: ...

    def list_connectors(self) -> list[dict]: ...

    def index_datapro_result(self, **kwargs: Any) -> dict[str, Any]: ...


def _disabled(server: Any) -> str:
    return (
        f"connector {server!r} is disabled; enable it in Customize \u2192 "
        f"Connectors to use it"
    )


def _datapro_tool_only(connector: dict) -> bool:
    """Whether this managed connector exposes only its one allowed tool."""

    return connector.get("connector_id") == "volcengine-datapro"


def _datapro_narrow_error() -> dict[str, str]:
    return {"error": "volcengine-datapro only permits dataPro_search"}


class MCPService:
    """Resolve configured MCP servers and dispatch MCP control operations."""

    def __init__(
        self,
        store: MCPStore,
        *,
        manager_factory: Callable[[], Any] | None = None,
        frame_id: Callable[[], str | None] | None = None,
        workspace: Callable[[], Any] | None = None,
    ) -> None:
        self.store = store
        self._manager_factory = manager_factory
        self._frame_id = frame_id or (lambda: None)
        self._workspace = workspace
        #: Tri-state connector allowlist: None inherits, [] denies everything,
        #: a list is exactly those. Armed by
        #: `HostDispatcher.set_child_execution_policy`, the choke point every
        #: delegated child passes through.
        self._allowed_connectors: object = None

    def set_allowed_connectors(self, allowed: object) -> None:
        """Restrict this service to these connectors. Only narrows.

        Composed through `resource_allowlist.narrow` for the reason the Skill
        half is: a delegation chain applies a policy per hop, and a hop that
        could widen is the way out of the one before it. `None` inherits the
        existing restriction rather than clearing it.
        """
        from openai4s.host import resource_allowlist

        self._allowed_connectors = resource_allowlist.narrow(
            self._allowed_connectors, allowed
        )

    def _permits(self, connector: dict) -> bool:
        """Whether this specialist may reach one resolved connector row.

        Matched against the id *and* the display name because `connector()`
        accepts either: an allowlist that understood only one spelling would
        deny access the user granted, or — worse — grant the spelling of a
        name the user denied.
        """
        from openai4s.host import resource_allowlist

        if resource_allowlist.normalise(self._allowed_connectors) is None:
            return True
        return any(
            resource_allowlist.permits(
                self._allowed_connectors, str(connector.get(key) or "")
            )
            for key in ("connector_id", "name")
        )

    def _resolve_manager_factory(self) -> Callable[[], Any]:
        if self._manager_factory is not None:
            return self._manager_factory
        # Keep this lookup dynamic.  The legacy dispatcher imported manager at
        # call time, which also lets tests and embedders replace the process-wide
        # manager without rebuilding the service.
        from openai4s.mcp_client import manager

        return manager

    def connector(self, server: str) -> dict | None:
        """Resolve by connector id first, then by exact display name.

        Allowlist-filtered here rather than in each of the six RPC entry
        points: this is the single lookup all of them share, and the launch
        config is built out of the row it returns. A connector this specialist
        may not reach therefore cannot have its process started — there is no
        command to start it with, which is stronger than refusing at each call
        site and forgetting one. Reported as absent rather than refused, so a
        distinct refusal cannot be used to enumerate what exists.
        """
        connector = self.store.get_connector(server)
        if connector:
            return connector if self._permits(connector) else None
        for candidate in self.store.list_connectors():
            if candidate.get("name") == server:
                return candidate if self._permits(candidate) else None
        return None

    def _config(self, connector: dict) -> dict:
        # One factory is shared with the Web routes so an Agent and the
        # dedicated DataPro UI cannot drift onto different transports or
        # credential paths.  Custom connectors retain the existing stdio
        # config; only the fixed managed connector receives authenticated HTTP.
        from openai4s.datapro import connector_runtime_config

        config = connector_runtime_config(self.store, connector)
        if (
            connector.get("connector_id") == "protein-design"
            and self._workspace is not None
        ):
            # The bundled scientific server confines all user-provided paths
            # to one root. Bind that root to the current session workspace,
            # not the daemon's checkout/cwd, and partition the cached MCP
            # process by the same identity so two sessions never share a path
            # authority. An operator's explicit root remains authoritative.
            workspace = str(self._workspace())
            env = dict(config.get("env") or {})
            env.setdefault("OPENAI4S_PROTEIN_DESIGN_ROOT", workspace)
            env.setdefault("OPENAI4S_PROTEIN_DESIGN_REQUIRE_ADMISSION", "1")
            config["env"] = env
            config["cache_scope"] = f"protein-design-workspace:{workspace}"
        return config

    def list(self) -> list:
        """Return the public projection of enabled, permitted connectors only.

        The catalogue is filtered as well as the call. A name the agent can see
        is a name it will ask for, and this listing is what the model's
        connector catalogue is built from: gating only the invocation would
        still advertise every connector on the host to a specialist restricted
        to one, and an advertised-but-unreachable name is both a leak and a
        dead end.
        """
        return [
            {
                "id": connector["connector_id"],
                "name": connector["name"],
                "description": connector.get("description"),
            }
            for connector in self.store.list_connectors()
            if connector.get("enabled") and self._permits(connector)
        ]

    def tools(self, server: str) -> Any:
        """List tools on an enabled connector.

        Zero-spawn when disabled. `call` already refused a disabled connector,
        but discovery did not — and discovery is what launches the process, so
        an agent could make the host run a command out of a connector row the
        user had explicitly turned off. `enabled` is a user control; it has to
        gate the spawn, not just the invocation.
        """
        manager_factory = self._resolve_manager_factory()
        connector = self.connector(server)
        if not connector:
            return {"error": f"connector {server!r} not found"}
        if not connector.get("enabled"):
            return {"error": _disabled(server)}
        if connector["connector_id"] == "volcengine-datapro":
            from openai4s import datapro

            # Answer the managed connector's discovery locally.  ``mcp_tools``
            # carries ``requires_approval = False``, decided when discovery could
            # only fork/exec a locally configured binary; over the managed HTTP
            # transport it opened an authenticated session that put the user's
            # live key on the wire with no gate, and told the model whether that
            # key was valid.  The answer is fixed anyway -- the reply was
            # filtered down to this single tool.
            return {"tools": [datapro.tool_descriptor()]}
        config = self._config(connector)
        try:
            tools = manager_factory().list_tools(
                connector["connector_id"],
                config,
            )
            return {"tools": tools}
        except Exception as exc:  # noqa: BLE001 - preserve host soft-fail contract
            return {"error": f"mcp tools failed: {exc}"}

    def call(self, spec: dict) -> Any:
        """Call one tool on an enabled connector."""
        manager_factory = self._resolve_manager_factory()
        server = spec.get("server")
        tool = spec.get("tool")
        args = spec.get("args") or {}
        connector = self.connector(server)
        if not connector:
            return {"error": f"connector {server!r} not found"}
        if not connector.get("enabled"):
            return {"error": f"connector {server!r} is disabled"}
        if connector["connector_id"] == "volcengine-datapro":
            from openai4s import datapro

            if tool != "dataPro_search":
                return {"error": "volcengine-datapro only permits dataPro_search"}
            if not isinstance(args, dict) or set(args) != {"query"}:
                return {"error": "dataPro_search requires exactly one string query"}
            try:
                args = {"query": datapro.validate_query(args.get("query"))}
            except ValueError as error:
                return {"error": str(error)}
        config = self._config(connector)
        try:
            secret_before = ""
            if connector["connector_id"] == "volcengine-datapro":
                secret_before = datapro.resolve_agent_plan_key(self.store)
            result = manager_factory().call_tool(
                connector["connector_id"],
                config,
                tool,
                args,
            )
            if connector["connector_id"] == "volcengine-datapro":
                secret_after = datapro.resolve_agent_plan_key(self.store)
                safe = datapro.redact_mcp_result(result, secret_before)
                if secret_after and secret_after != secret_before:
                    safe = datapro.redact_secret(safe, secret_after)
                receipt = datapro.index_successful_search(
                    self.store,
                    query=args["query"],
                    result=safe,
                    frame_id=self._frame_id(),
                    secrets=(secret_before, secret_after),
                )
                if receipt is not None:
                    safe["index"] = receipt
                return safe
            return result
        except Exception as exc:  # noqa: BLE001 - preserve host soft-fail contract
            return {"error": f"mcp_call({server}.{tool}) failed: {exc}"}

    def resources(self, spec: dict) -> Any:
        """List resource metadata on an enabled connector. Zero-spawn when
        disabled — see :meth:`tools`."""

        manager_factory = self._resolve_manager_factory()
        server = spec.get("server")
        connector = self.connector(server)
        if not connector:
            return {"error": f"connector {server!r} not found"}
        if not connector.get("enabled"):
            return {"error": _disabled(server)}
        if _datapro_tool_only(connector):
            return _datapro_narrow_error()
        try:
            return manager_factory().list_resources(
                connector["connector_id"],
                self._config(connector),
                spec.get("cursor"),
            )
        except Exception as exc:  # noqa: BLE001 - preserve host soft-fail contract
            return {"error": f"mcp resources failed: {exc}"}

    def read_resource(self, spec: dict) -> Any:
        """Read one resource from an enabled connector."""

        manager_factory = self._resolve_manager_factory()
        server = spec.get("server")
        uri = spec.get("uri")
        connector = self.connector(server)
        if not connector:
            return {"error": f"connector {server!r} not found"}
        if not connector.get("enabled"):
            return {"error": f"connector {server!r} is disabled"}
        if _datapro_tool_only(connector):
            return _datapro_narrow_error()
        try:
            return manager_factory().read_resource(
                connector["connector_id"],
                self._config(connector),
                uri,
            )
        except Exception as exc:  # noqa: BLE001 - preserve host soft-fail contract
            return {"error": f"mcp resource read({server}:{uri}) failed: {exc}"}

    def prompts(self, spec: dict) -> Any:
        """List prompt metadata on an enabled connector. Zero-spawn when
        disabled — see :meth:`tools`."""

        manager_factory = self._resolve_manager_factory()
        server = spec.get("server")
        connector = self.connector(server)
        if not connector:
            return {"error": f"connector {server!r} not found"}
        if not connector.get("enabled"):
            return {"error": _disabled(server)}
        if _datapro_tool_only(connector):
            return _datapro_narrow_error()
        try:
            return manager_factory().list_prompts(
                connector["connector_id"],
                self._config(connector),
                spec.get("cursor"),
            )
        except Exception as exc:  # noqa: BLE001 - preserve host soft-fail contract
            return {"error": f"mcp prompts failed: {exc}"}

    def get_prompt(self, spec: dict) -> Any:
        """Render one named prompt from an enabled connector."""

        manager_factory = self._resolve_manager_factory()
        server = spec.get("server")
        name = spec.get("name")
        connector = self.connector(server)
        if not connector:
            return {"error": f"connector {server!r} not found"}
        if not connector.get("enabled"):
            return {"error": f"connector {server!r} is disabled"}
        if _datapro_tool_only(connector):
            return _datapro_narrow_error()
        try:
            return manager_factory().get_prompt(
                connector["connector_id"],
                self._config(connector),
                name,
                spec.get("arguments"),
            )
        except Exception as exc:  # noqa: BLE001 - preserve host soft-fail contract
            return {"error": f"mcp prompt get({server}.{name}) failed: {exc}"}


__all__ = ["MCPService"]
