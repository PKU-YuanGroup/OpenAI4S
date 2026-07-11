"""Class-based control-tool surface for openai4s.

Workspace tools are named ``Tool`` subclasses whose modules contain both schema
and domain behaviour. Model calls still enter through ``HostDispatcher`` before
that behaviour runs, preserving permissions, human approval, egress controls,
injection screening, UI activity events, audit logs, and replay. There is no
shell or completion tool: shell/scientific work remains Code-as-Action and only
``host.submit_output`` completes a task.

This package is pure stdlib and imports nothing from the engine (no
host_dispatch / loop / gateway) at module load, so it stays importable with
zero side effects. Wiring into the agent loops happens elsewhere.
"""
from openai4s.tools.base import Tool
from openai4s.tools.content_search import ContentSearchTool
from openai4s.tools.edit import EditFileTool
from openai4s.tools.glob_files import GlobFilesTool
from openai4s.tools.list_directory import ListDirectoryTool
from openai4s.tools.native import ToolSpec, control_tool_specs
from openai4s.tools.read_text_file import ReadTextFileTool
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
    get_tool_by_host_method,
    parse_fence_delimiter,
    parse_tool_calls,
    render_tools_prompt,
    run_tool_calls,
    scan_fenced_blocks,
    strip_fenced_blocks,
)
from openai4s.tools.write_file import WriteFileTool

__all__ = [
    "Tool",
    "ToolSpec",
    "ListDirectoryTool",
    "ReadTextFileTool",
    "WriteFileTool",
    "GlobFilesTool",
    "ContentSearchTool",
    "EditFileTool",
    "FencedBlock",
    "REGISTRY",
    "get_tool",
    "get_tool_by_host_method",
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
