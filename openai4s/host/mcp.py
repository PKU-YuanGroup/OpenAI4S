"""MCP connector discovery and tool invocation for host RPC calls.

The service deliberately keeps the connector lookup and error boundary used by
the legacy dispatcher.  Policy such as permission gating and untrusted-output
screening remains outside this class.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol


class MCPStore(Protocol):
    """Minimal connector persistence used by :class:`MCPService`."""

    def get_connector(self, connector_id: str) -> dict | None:
        ...

    def list_connectors(self) -> list[dict]:
        ...


class MCPService:
    """Resolve configured MCP servers and dispatch MCP control operations."""

    def __init__(
        self,
        store: MCPStore,
        *,
        manager_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.store = store
        self._manager_factory = manager_factory

    def _resolve_manager_factory(self) -> Callable[[], Any]:
        if self._manager_factory is not None:
            return self._manager_factory
        # Keep this lookup dynamic.  The legacy dispatcher imported manager at
        # call time, which also lets tests and embedders replace the process-wide
        # manager without rebuilding the service.
        from openai4s.mcp_client import manager

        return manager

    def connector(self, server: str) -> dict | None:
        """Resolve by connector id first, then by exact display name."""
        connector = self.store.get_connector(server)
        if connector:
            return connector
        for candidate in self.store.list_connectors():
            if candidate.get("name") == server:
                return candidate
        return None

    def _config(self, connector: dict) -> dict:
        config = {
            "command": connector["command"],
            "args": connector.get("args"),
            # Resolved: the row holds references once migrated, and launching
            # the server with the literal "secret://..." string as its
            # credential fails as a broken server, not a broken lookup.
            "env": self.store.connector_env(connector),
        }
        if connector.get("cwd"):
            config["cwd"] = connector["cwd"]
        return config

    def list(self) -> list:
        """Return the public projection of enabled connectors only."""
        return [
            {
                "id": connector["connector_id"],
                "name": connector["name"],
                "description": connector.get("description"),
            }
            for connector in self.store.list_connectors()
            if connector.get("enabled")
        ]

    def tools(self, server: str) -> Any:
        """List tools, including for a configured but disabled connector."""
        manager_factory = self._resolve_manager_factory()
        connector = self.connector(server)
        if not connector:
            return {"error": f"connector {server!r} not found"}
        config = self._config(connector)
        try:
            return {
                "tools": manager_factory().list_tools(
                    connector["connector_id"],
                    config,
                )
            }
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
        config = self._config(connector)
        try:
            return manager_factory().call_tool(
                connector["connector_id"],
                config,
                tool,
                args,
            )
        except Exception as exc:  # noqa: BLE001 - preserve host soft-fail contract
            return {"error": f"mcp_call({server}.{tool}) failed: {exc}"}

    def resources(self, spec: dict) -> Any:
        """List resource metadata; configured disabled servers remain inspectable."""

        manager_factory = self._resolve_manager_factory()
        server = spec.get("server")
        connector = self.connector(server)
        if not connector:
            return {"error": f"connector {server!r} not found"}
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
        try:
            return manager_factory().read_resource(
                connector["connector_id"],
                self._config(connector),
                uri,
            )
        except Exception as exc:  # noqa: BLE001 - preserve host soft-fail contract
            return {"error": f"mcp resource read({server}:{uri}) failed: {exc}"}

    def prompts(self, spec: dict) -> Any:
        """List prompt metadata; configured disabled servers remain inspectable."""

        manager_factory = self._resolve_manager_factory()
        server = spec.get("server")
        connector = self.connector(server)
        if not connector:
            return {"error": f"connector {server!r} not found"}
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
