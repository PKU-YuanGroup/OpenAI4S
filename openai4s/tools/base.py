"""Executable base class for the agent's control-plane tools.

Concrete tools declare their metadata as class attributes and keep their
domain behaviour in ``execute``.  Model-originated calls must enter through
``invoke`` so the :class:`HostDispatcher` can apply permissions, approvals,
auditing, injection screening, and UI activity events before ``execute`` is
reached by the dispatcher's thin host-method adapter.

The positional constructor remains available for older declarative entries
and callers that build dynamic metadata-only tools. New built-ins should use
named subclasses, mirroring CoreCoder's extensible tool catalogue.
"""

from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError
from typing import Any


class Tool:
    """Base interface and compatibility adapter for one control tool.

    Subclasses normally set the metadata below as class attributes and
    override :meth:`execute`.  ``Tool(name, host_method, ...)`` is retained for
    schema-only extensions and compatibility; built-in tools never use it.
    """

    name: str = ""
    host_method: str = ""
    description: str = ""
    parameters: dict = {"properties": {}, "required": []}
    read_only: bool = True
    writes_files: bool = False
    needs_network: bool = False
    mutates_cwd: bool = False
    dangerous: bool = False
    output_limit: int = 20_000

    _METADATA_FIELDS = (
        "name",
        "host_method",
        "description",
        "parameters",
        "read_only",
        "writes_files",
        "needs_network",
        "mutates_cwd",
        "dangerous",
        "output_limit",
    )

    def __init__(
        self,
        name: str | None = None,
        host_method: str | None = None,
        description: str | None = None,
        parameters: dict | None = None,
        read_only: bool | None = None,
        writes_files: bool | None = None,
        needs_network: bool | None = None,
        mutates_cwd: bool | None = None,
        dangerous: bool | None = None,
        output_limit: int | None = None,
    ) -> None:
        # No arguments snapshots the concrete subclass's class declarations.
        overrides = {
            "name": name,
            "host_method": host_method,
            "description": description,
            "parameters": parameters,
            "read_only": read_only,
            "writes_files": writes_files,
            "needs_network": needs_network,
            "mutates_cwd": mutates_cwd,
            "dangerous": dangerous,
            "output_limit": output_limit,
        }
        for field, value in overrides.items():
            if value is None:
                value = getattr(type(self), field)
            object.__setattr__(self, field, value)
        object.__setattr__(self, "_frozen", True)

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_frozen", False):
            raise FrozenInstanceError(f"cannot assign to field {name!r}")
        object.__setattr__(self, name, value)

    def __repr__(self) -> str:
        values = ", ".join(
            f"{field}={getattr(self, field)!r}" for field in self._METADATA_FIELDS
        )
        return f"{type(self).__name__}({values})"

    def __eq__(self, other: object) -> bool:
        if type(other) is not type(self):
            return False
        return all(
            getattr(self, field) == getattr(other, field)
            for field in self._METADATA_FIELDS
        )

    def invoke(self, dispatcher: Any, arguments: dict) -> Any:
        """Enter the host's policy envelope for a model-originated call.

        This is deliberately separate from :meth:`execute`: direct execution
        is reserved for the HostDispatcher's protected method adapters.
        """
        return dispatcher(self.host_method, [dict(arguments)])

    def execute(self, context: Any, arguments: dict) -> Any:
        """Run the tool's domain behaviour inside the dispatcher envelope."""
        raise NotImplementedError(f"{type(self).__name__} does not implement execute()")

    def native_precheck(self, arguments: dict) -> str | None:
        """Return a cheap pre-dispatch error for native/fenced calls, if any."""
        return None

    def signature_line(self) -> str:
        """Return ``name(arg1, arg2?, ...)`` using declared parameter order."""
        props = self.parameters.get("properties") or {}
        required = set(self.parameters.get("required") or [])
        parts = [(arg if arg in required else f"{arg}?") for arg in props]
        return f"{self.name}({', '.join(parts)})"

    def schema(self) -> dict:
        """Return an OpenAI-style function schema for simple integrations."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": copy.deepcopy(
                        self.parameters.get("properties") or {}
                    ),
                    "required": list(self.parameters.get("required") or []),
                },
            },
        }


__all__ = ["Tool"]
