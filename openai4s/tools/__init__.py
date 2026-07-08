"""ReAct tool surface for openai4s.

A thin, declarative layer on top of the Code-as-Action model: each `Tool` is
metadata that names a small deterministic operation (list/read/write/glob/grep/
edit/bash/env/web) and the host method it routes to. Tools do NOT re-implement
any fs/shell/web logic — `execute_tool_call` dispatches them through the
existing `HostDispatcher` (passed in by the caller), so every call inherits the
permission gate, egress fence, injection screening, UI activity steps, and call
logging. Analysis, plotting, modeling and multi-step computation stay in
```python cells with persistent kernel state.

This package is pure stdlib and imports nothing from the engine (no
host_dispatch / loop / gateway) at module load, so it stays importable with
zero side effects. Wiring into the agent loops happens elsewhere.
"""
from openai4s.tools.base import Tool
from openai4s.tools.registry import (
    MAX_TOOL_CALLS_PER_TURN,
    REGISTRY,
    all_tools,
    execute_tool_call,
    finalize_tool_batch,
    format_tool_result,
    get_tool,
    parse_tool_calls,
    render_tools_prompt,
    run_tool_calls,
)

__all__ = [
    "Tool",
    "REGISTRY",
    "get_tool",
    "all_tools",
    "parse_tool_calls",
    "render_tools_prompt",
    "execute_tool_call",
    "format_tool_result",
    "run_tool_calls",
    "finalize_tool_batch",
    "MAX_TOOL_CALLS_PER_TURN",
]
