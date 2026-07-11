"""Provider-neutral descriptions of native control-plane tools.

This module is metadata-only.  It derives native JSON tool declarations from
the existing declarative registry, but it does not import or alter the legacy
fenced-tool parser/executor.  Provider adapters can translate ``ToolSpec`` into
their own wire shape later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from openai4s.tools.base import Tool
from openai4s.tools.registry import REGISTRY

# Portable intersection of the native function-name rules used by OpenAI Chat
# and Responses, Anthropic Messages, Gemini function declarations, and the Ark
# OpenAI-compatible wire: ASCII letter/underscore first, then ASCII letters,
# digits, underscores, or hyphens, with a maximum length of 64 characters.
_PORTABLE_TOOL_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}\Z", re.ASCII)

# Completion remains an in-kernel host signal, and shell remains kernel-local.
# Check both public name and routed host method so an alias cannot expose either
# capability through the native control plane.
_NEVER_NATIVE = frozenset({"bash", "submit_output"})


@dataclass(frozen=True)
class ToolSpec:
    """One provider-neutral native tool declaration."""

    name: str
    description: str
    input_schema: dict
    strict: bool = False


def _validate_portable_name(name: str) -> None:
    if not isinstance(name, str) or _PORTABLE_TOOL_NAME.fullmatch(name) is None:
        raise ValueError(
            f"native tool name {name!r} is not portable across OpenAI, "
            "Anthropic, Gemini, and Ark; expected "
            "[A-Za-z_][A-Za-z0-9_-]{0,63}"
        )


def control_tool_specs(tools: Iterable[Tool] | None = None) -> tuple[ToolSpec, ...]:
    """Return fresh native declarations for the safe control-tool registry.

    Every call deep-copies the legacy metadata.  Callers may therefore adapt a
    returned schema for a provider without mutating ``REGISTRY`` or another
    caller's declarations.
    """

    specs: list[ToolSpec] = []
    for tool in REGISTRY if tools is None else tuple(tools):
        if tool.name in _NEVER_NATIVE or tool.host_method in _NEVER_NATIVE:
            continue
        _validate_portable_name(tool.name)
        schema = tool.input_schema()
        specs.append(
            ToolSpec(
                name=tool.name,
                description=tool.description,
                input_schema=schema,
                strict=tool.supports_provider_strict(),
            )
        )
    return tuple(specs)


__all__ = ["ToolSpec", "control_tool_specs"]
