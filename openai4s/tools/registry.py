"""The public tool registry + the ```tool call convention.

`REGISTRY` is the ordered list of every declared `Tool`. `parse_tool_calls`
extracts ```tool JSON blocks from a model reply; `execute_tool_call` runs one
call by routing it through a HostDispatcher passed in by the caller; and
`render_tools_prompt` describes the surface for the system prompt.

The dispatcher is always passed in — this module never imports the
HostDispatcher (or the agent loop / gateway), so it stays importable with zero
side effects. Pure stdlib.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from openai4s.tools.base import Tool
from openai4s.tools.bash import bash, precheck_command
from openai4s.tools.edit import edit_file, static_edit_precheck
from openai4s.tools.env import env_create, env_list, env_use
from openai4s.tools.fs import list_dir, read_text_file, write_file
from openai4s.tools.search import content_search, glob_files
from openai4s.tools.web import web_fetch, web_search

# Ordered, canonical tool surface. Order here is the order shown in the prompt.
REGISTRY: list[Tool] = [
    list_dir,
    read_text_file,
    write_file,
    glob_files,
    content_search,
    edit_file,
    bash,
    env_list,
    env_use,
    env_create,
    web_search,
    web_fetch,
]

_BY_NAME: dict[str, Tool] = {t.name: t for t in REGISTRY}


def get_tool(name: str) -> Tool | None:
    """Look up a Tool by its ReAct name, or None if unknown."""
    return _BY_NAME.get(name)


def all_tools() -> list[Tool]:
    """A copy of the registry list (callers may reorder/filter freely)."""
    return list(REGISTRY)


# --- parsing model replies -------------------------------------------------

# A CommonMark-style fenced-code delimiter line (backticks or tildes), optionally
# indented, with an optional info string. The action protocol itself only honors
# backtick `python` / `tool` blocks; recognizing tildes and longer outer fences
# prevents a triple-backtick example nested inside them from becoming an action.
_FENCE_RE = re.compile(r"^[ \t]*(?P<fence>`{3,}|~{3,})(?P<info>[^\n]*?)[ \t]*$")


@dataclass(frozen=True)
class FencedBlock:
    """One top-level fenced block found in a model reply.

    `body` preserves nested fenced examples verbatim. `closed=False` marks a
    streaming/incomplete block; callers may hide it from prose, but must never
    execute it as code or a tool call.
    """

    info: str
    fence_char: str
    fence_length: int
    body: str
    start: int
    end: int
    closed: bool


def parse_fence_delimiter(line: str) -> tuple[str, int, str] | None:
    """Return `(character, run length, normalized info)` for a fence line."""
    m = _FENCE_RE.match(line.rstrip("\r\n"))
    if not m:
        return None
    fence = m.group("fence")
    return fence[0], len(fence), (m.group("info") or "").strip().lower()


def scan_fenced_blocks(text: str) -> list[FencedBlock]:
    """Return top-level fenced blocks using a small, nesting-aware scanner.

    An info-bearing delimiter inside another block opens a nested example and
    a bare delimiter closes it. This is deliberately a little more permissive
    than CommonMark: models often place a literal ```tool example inside a
    ```python triple-quoted string, and the outer Python cell must remain whole.
    Only blocks whose outer delimiter reaches a matching close are executable.
    """
    if not isinstance(text, str) or not text:
        return []

    blocks: list[FencedBlock] = []
    stack: list[tuple[str, int]] = []
    top_info = ""
    top_char = "`"
    top_length = 3
    top_start = 0
    body_start = 0
    offset = 0
    for line in text.splitlines(keepends=True):
        delimiter = parse_fence_delimiter(line)
        if delimiter is not None:
            fence_char, fence_length, info = delimiter
            if not stack:
                stack.append((fence_char, fence_length))
                top_info = info
                top_char = fence_char
                top_length = fence_length
                top_start = offset
                body_start = offset + len(line)
            elif fence_char != stack[-1][0] or fence_length < stack[-1][1]:
                # A shorter or alternate-character delimiter is literal body
                # (e.g. ```tool inside a ````python documentation cell).
                pass
            elif info:
                # A labelled fence inside an outer fence is a quoted/nested
                # example, not the outer closer.
                stack.append((fence_char, fence_length))
            else:
                stack.pop()
                if not stack:
                    blocks.append(
                        FencedBlock(
                            info=top_info,
                            fence_char=top_char,
                            fence_length=top_length,
                            body=text[body_start:offset],
                            start=top_start,
                            end=offset + len(line),
                            closed=True,
                        )
                    )
        offset += len(line)

    if stack:
        blocks.append(
            FencedBlock(
                info=top_info,
                fence_char=top_char,
                fence_length=top_length,
                body=text[body_start:],
                start=top_start,
                end=len(text),
                closed=False,
            )
        )
    return blocks


def strip_fenced_blocks(text: str) -> str:
    """Remove complete and incomplete top-level fences from visible prose."""
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else ""
    out: list[str] = []
    pos = 0
    for block in scan_fenced_blocks(text):
        out.append(text[pos : block.start])
        pos = block.end
    out.append(text[pos:])
    return "".join(out)


def _coerce_call(obj: Any) -> tuple[dict | None, str | None]:
    """Normalize one decoded object into {"name", "arguments"} or an error str.

    Accepts both {"name":..., "arguments":{...}} and {"tool":..., "args":{...}}.
    """
    if not isinstance(obj, dict):
        return None, f"tool call must be a JSON object, got {type(obj).__name__}"
    name = obj.get("name")
    if not isinstance(name, str) or not name:
        name = obj.get("tool")
    if not isinstance(name, str) or not name:
        return None, "tool call missing a 'name' (or 'tool') string"
    args = obj.get("arguments")
    if args is None:
        args = obj.get("args")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return None, f"tool call {name!r}: 'arguments' must be a JSON object"
    return {"name": name, "arguments": args}, None


def _parse_tool_body(body: str, calls: list[dict], errors: list[str]) -> None:
    """Decode one ```tool block body (a JSON object or list) into calls/errors."""
    body = (body or "").strip()
    if not body:
        errors.append("empty ```tool block")
        return
    try:
        decoded = json.loads(body)
    except Exception as e:  # noqa: BLE001 — decoder recursion is an observation
        errors.append(f"invalid JSON in ```tool block: {e}")
        return
    items = decoded if isinstance(decoded, list) else [decoded]
    for item in items:
        call, err = _coerce_call(item)
        if err is not None:
            errors.append(err)
            continue
        if get_tool(call["name"]) is None:
            errors.append(f"unknown tool: {call['name']!r}")
            continue
        calls.append(call)


def parse_tool_calls(reply: str) -> tuple[list[dict], list[str]]:
    """Scan `reply` for TOP-LEVEL ```tool blocks and return (calls, errors).

    Fences are paired by character and run length, so a ```tool token nested
    inside another fence (e.g. quoted inside a ```python cell or a longer
    ```` block) is content, never a tool call. Only complete, top-level backtick
    tool blocks are honored. Each body is JSON: a single call object or list.
    Both
    {"name","arguments"} and {"tool","args"} shapes are accepted. Malformed JSON
    and unknown tool names become `errors` entries (fed back to the model) and
    are dropped from `calls`. Order preserved. Never raises on bad input.
    """
    calls: list[dict] = []
    errors: list[str] = []
    if not isinstance(reply, str):
        return calls, errors
    try:
        blocks = scan_fenced_blocks(reply)
    except Exception as e:  # noqa: BLE001 — malformed output never breaks the loop
        return calls, [f"could not scan tool fences: {e}"]
    for block in blocks:
        if block.fence_char != "`" or block.info != "tool":
            continue
        if not block.closed:
            errors.append("unclosed ```tool block")
            continue
        _parse_tool_body(block.body, calls, errors)
    return calls, errors


# --- prompt rendering ------------------------------------------------------


def render_tools_prompt() -> str:
    """A concise system-prompt section describing the ```tool convention and
    listing every tool as "- signature(): description"."""
    lines = [
        "## Tools",
        "",
        "Besides ```python Code-as-Action cells you can call a small set of "
        "deterministic tools. Emit a tool call as a fenced ```tool block whose "
        "body is a single JSON object:",
        "",
        "```tool",
        '{"name": "read_text_file", "arguments": {"path": "data/notes.md"}}',
        "```",
        "",
        "One JSON object per ```tool block. You may emit several blocks in one "
        "reply; they run top-to-bottom and every result is returned to you "
        "before your next turn.",
        "",
        "Use a tool for small, deterministic operations — listing a directory, "
        "reading/writing a file, glob, grep, a web search or fetch, an "
        "environment switch, or a single-string edit. Use a ```python cell for "
        "analysis, plotting, modeling, simulations, and any multi-step "
        "computation needing persistent kernel state. NEVER write a python cell "
        "merely to list files, grep, or fetch a URL — use the tool.",
        "",
        "Available tools:",
    ]
    for tool in REGISTRY:
        lines.append(f"- {tool.signature_line()}: {tool.description}")
    return "\n".join(lines)


# --- execution -------------------------------------------------------------


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        return str(value)


def _render_list(key: str, value: Any) -> str:
    """Compactly render a list-valued result field (results/matches/entries)."""
    if not isinstance(value, list):
        return f"{key}: {value}"
    head = value[:50]
    body = "\n".join(_safe_json(item) for item in head)
    if len(value) > len(head):
        body += f"\n… ({len(value) - len(head)} more)"
    return f"{key} ({len(value)}):\n{body}"


def _render_result_body(result: Any) -> str:
    """Turn a handler result into a compact readable string, preferring common
    text fields before falling back to JSON."""
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        return _safe_json(result)
    if set(result.keys()) == {"error"}:
        return f"error: {result['error']}"
    parts: list[str] = []
    for key in ("content", "stdout", "output"):
        val = result.get(key)
        if isinstance(val, str) and val:
            parts.append(val if key == "content" else f"[{key}]\n{val}")
    stderr = result.get("stderr")
    if isinstance(stderr, str) and stderr:
        parts.append(f"[stderr]\n{stderr}")
    for key in ("results", "matches", "entries"):
        if result.get(key):
            parts.append(_render_list(key, result[key]))
    if "exit_code" in result:
        parts.append(f"exit_code={result['exit_code']}")
    if not parts and "ok" in result:
        parts.append(f"ok={result['ok']}")
    if result.get("error"):
        parts.append(f"error: {result['error']}")
    if not parts:
        return _safe_json(result)
    return "\n".join(parts)


def _truncate_with_marker(text: str, limit: int, marker: str) -> str:
    """Truncate to a strict character limit while keeping a visible marker."""
    if len(text) <= limit:
        return text
    if limit <= 0:
        return ""
    if len(marker) >= limit:
        return marker[:limit]
    return text[: limit - len(marker)] + marker


def format_tool_result(tool: Tool, result: Any) -> str:
    """Produce a readable "[Tool: <name>]\\n<compact result>" string, bounded
    to `tool.output_limit` characters."""
    text = f"[Tool: {tool.name}]\n{_render_result_body(result)}"
    return _truncate_with_marker(text, tool.output_limit, "\n… [truncated]")


def execute_tool_call(dispatcher: Any, call: Any) -> tuple[str, bool]:
    """Run one parsed tool call through `dispatcher` and return
    (observation_text, ok).

    `dispatcher` is a callable with the HostDispatcher signature
    `dispatcher(method, args) -> result`. This routes `tool.host_method` with a
    single snake_case spec dict, so the call inherits the dispatcher's
    permission gate, egress fence, injection screening, UI activity step, and
    logging. Cheap static prechecks (dangerous bash, degenerate edit) run
    first and short-circuit without dispatching. Any exception is turned into
    an error observation — this never raises.
    """
    name: Any = "unknown"
    tool: Tool | None = None
    try:
        if not isinstance(call, dict):
            return "[Tool error] tool call must be a JSON object", False
        raw_name = call.get("name")
        name = raw_name if type(raw_name) is str else "unknown"
        tool = get_tool(name)
        if tool is None:
            return f"[Tool error] unknown tool: {name!r}", False

        raw_spec = call.get("arguments")
        if raw_spec is None:
            raw_spec = {}
        if not isinstance(raw_spec, dict):
            return (
                f"[Tool error] {tool.name}: 'arguments' must be a JSON object",
                False,
            )
        spec = dict(raw_spec)

        if tool.dangerous and tool.host_method == "bash":
            reason = precheck_command(spec.get("command", ""))
            if reason:
                return (
                    f"[Tool: {tool.name}] blocked by static safety precheck: {reason}",
                    False,
                )
        if tool.host_method == "edit_file":
            err = static_edit_precheck(spec)
            if err:
                return f"[Tool: {tool.name}] {err}", False

        result = dispatcher(tool.host_method, [spec])
        ok = not (isinstance(result, dict) and set(result.keys()) == {"error"})
        return format_tool_result(tool, result), ok
    except Exception as e:  # noqa: BLE001 — a tool error must not crash the loop
        try:
            detail = str(e)
        except Exception:  # noqa: BLE001 — even a broken exception is observable
            detail = type(e).__name__
        text = f"[Tool error] {name}: {detail}"
        limit = tool.output_limit if tool is not None else 20_000
        return _truncate_with_marker(text, limit, "\n… [truncated]"), False


# --- batching --------------------------------------------------------------

# One reply may emit several ```tool blocks. Bound both the number executed and
# the total observation size so a single turn cannot blow the context window
# (each result is already bounded to a tool's output_limit, but the JOIN was
# not). Extras are reported as skipped rather than silently dropped.
MAX_TOOL_CALLS_PER_TURN = 16
MAX_TOOL_OBS_CHARS = 60000


def finalize_tool_batch(parts: list[str], n_total: int, errors: list[str]) -> str:
    """Assemble a bounded "[Tool Results]" observation from result strings +
    parse errors, appending a skipped-calls note when the batch was capped."""
    notices: list[str] = []
    if n_total > MAX_TOOL_CALLS_PER_TURN:
        notices.append(
            f"[Tool note] {n_total - MAX_TOOL_CALLS_PER_TURN} further tool "
            f"call(s) in this reply were NOT run — emit at most "
            f"{MAX_TOOL_CALLS_PER_TURN} tool calls per turn."
        )
    for err in errors:
        notices.append(f"[Tool error] {err}")
    # Control-plane notices come first so a large result cannot hide the fact
    # that later calls were skipped or malformed.
    sections = notices + list(parts)
    obs = "[Tool Results]\n" + (
        "\n\n".join(sections) if sections else "(no tool output)"
    )
    return _truncate_with_marker(
        obs, MAX_TOOL_OBS_CHARS, "\n… [tool results truncated]"
    )


def run_tool_calls(dispatcher: Any, calls: list[dict], errors: list[str]) -> str:
    """Execute a batch of parsed tool calls through `dispatcher` (up to
    MAX_TOOL_CALLS_PER_TURN) and return a single bounded observation string.

    For a fixed dispatcher (the CLI loop). The web loop runs calls inline so it
    can apply a pending env switch between them, but uses finalize_tool_batch
    for the same bounding.
    """
    parts: list[str] = []
    for call in calls[:MAX_TOOL_CALLS_PER_TURN]:
        text, _ok = execute_tool_call(dispatcher, call)
        parts.append(text)
    return finalize_tool_batch(parts, len(calls), errors)
