"""The `Tool` value type for the ReAct tool surface.

A `Tool` is pure metadata: a ReAct name the model writes, the `host_method`
it routes to on the `HostDispatcher` (`_m_<host_method>`), a human description,
and a small JSON-Schema-ish `parameters` block. Tools do NOT re-implement any
fs/shell/web logic — `openai4s.tools.registry.execute_tool_call` dispatches
them through the existing dispatcher so they inherit its permission gate,
egress fence, injection screening, UI activity steps, and call logging.

Pure stdlib, zero side effects on import.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tool:
    """One ReAct tool, declared once and routed through the HostDispatcher.

    Fields:
      name         — the tool name the model writes in a ```tool block.
      host_method  — the dispatcher method to call (resolves to `_m_<method>`).
      description  — one-line human/prompt description.
      parameters   — {"properties": {name: schema, ...}, "required": [name, ...]}.
      read_only    — True when the tool cannot mutate workspace state.
      writes_files — True when the tool creates/overwrites files.
      needs_network — True when the tool may reach the network.
      mutates_cwd  — True when the tool can change on-disk state via a shell.
      dangerous    — True when the tool warrants an extra static precheck.
      output_limit — max characters of formatted observation returned.
    """

    name: str
    host_method: str
    description: str
    parameters: dict
    read_only: bool = True
    writes_files: bool = False
    needs_network: bool = False
    mutates_cwd: bool = False
    dangerous: bool = False
    output_limit: int = 20000

    def signature_line(self) -> str:
        """Compact "name(arg1, arg2?, ...)" — optional args suffixed with '?'.

        Argument order follows the declared `properties` order; a parameter is
        "optional" when it is absent from the `required` list.
        """
        props = self.parameters.get("properties") or {}
        required = set(self.parameters.get("required") or [])
        parts = [(arg if arg in required else f"{arg}?") for arg in props]
        return f"{self.name}({', '.join(parts)})"
