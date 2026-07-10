"""ReAct tool surface for openai4s.

A thin, declarative layer on top of the Code-as-Action model: each `Tool` is
metadata that names a small deterministic operation (list/read/write/glob/grep/
edit/env/web) and the host method it routes to. Tools do NOT re-implement any
fs/web logic — `execute_tool_call` dispatches them through the existing
`HostDispatcher` (passed in by the caller), so every call inherits the
permission gate, egress fence, injection screening, UI activity steps, and call
logging. There is deliberately no shell tool: the host executes only python/R
cells, and shell commands run inside the kernel. Analysis, plotting, modeling
and multi-step computation stay in ```python / ```r cells with persistent
kernel state.

This package is pure stdlib and imports nothing from the engine (no
host_dispatch / loop / gateway) at module load, so it stays importable with
zero side effects. Wiring into the agent loops happens elsewhere.
"""
from openai4s.tools.base import Tool
from openai4s.tools.native import ToolSpec, control_tool_specs
from openai4s.tools.registry import (
    MAX_TOOL_CALLS_PER_TURN,
    MAX_TOOL_OBS_CHARS,
    REGISTRY,
    FencedBlock,
    all_tools,
    execute_tool_call,
    finalize_tool_batch,
    format_tool_result,
    get_tool,
    parse_fence_delimiter,
    parse_tool_calls,
    render_tools_prompt,
    run_tool_calls,
    scan_fenced_blocks,
    strip_fenced_blocks,
)

__all__ = [
    "Tool",
    "ToolSpec",
    "FencedBlock",
    "REGISTRY",
    "get_tool",
    "all_tools",
    "parse_fence_delimiter",
    "parse_tool_calls",
    "render_tools_prompt",
    "execute_tool_call",
    "format_tool_result",
    "run_tool_calls",
    "scan_fenced_blocks",
    "strip_fenced_blocks",
    "finalize_tool_batch",
    "MAX_TOOL_CALLS_PER_TURN",
    "MAX_TOOL_OBS_CHARS",
    "control_tool_specs",
]
