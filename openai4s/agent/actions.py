"""Shared action-parsing core for the dual outer loop.

Both turn loops — ``Agent.run`` (openai4s/agent/loop.py) and
``SessionRunner._loop`` (openai4s/server/gateway.py) — parse a model reply into
at most ONE executable action per step. This module is the single choke point
for that decision (CoreCoder-style: one small core, two thin loop bodies):

- the fence-info → language whitelist (``python``/``py``/bare → python kernel,
  ``r``/``R`` → R kernel) lives only here;
- structured native tool calls take precedence over code through
  ``route_action`` so the control plane cannot race scientific execution;
- the engine-owned ``finalize_response`` call is routed as a distinct terminal
  action only when it is the sole native call in a reply;
- replies without native calls keep the existing fence extractor, so a quoted
  ```` ```tool ```` inside a cell can never hijack a turn;
- the one-cell-per-step counter and the no-action nudge text live here so the
  two loops cannot drift.

The host executes exactly two kinds of instructions — python cells on the
persistent Jupyter-style kernel and R cells on the persistent R kernel. Any
other work (shell, file ops) happens *inside* those kernels or through the
declarative ```tool surface.

Pure stdlib; imports only openai4s.tools (itself pure stdlib, zero side
effects on import).
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, TypeAlias

from openai4s.tools import scan_fenced_blocks

# Fence info strings (already lowercased by parse_fence_delimiter) that mark an
# executable cell. A bare ``` fence still means python — R must be explicit.
PYTHON_INFOS = ("", "python", "py")
R_INFOS = ("r",)

# This is an engine protocol name, not a registered control tool.  Keep the
# literal here, at the routing boundary, so importing actions remains free of
# registry/finalization implementation side effects.
FINALIZE_RESPONSE_NAME = "finalize_response"


@dataclass(frozen=True)
class CodeCell:
    """One executable cell extracted from a model reply."""

    language: str  # "python" | "r"
    code: str


@dataclass(frozen=True)
class NativeToolCall:
    """One provider-normalized native tool call.

    ``raw_arguments`` is intentionally retained alongside parsed
    ``arguments`` and ``parse_error``. The action router is a lossless routing
    boundary, not another wire-format parser; provider-specific details stay
    available in ``provider_meta`` for later execution and diagnostics.
    """

    id: str
    wire_id: str | None
    name: str
    ordinal: int
    raw_arguments: str
    arguments: dict[str, Any] | None = None
    parse_error: str | None = None
    provider_meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NativeToolBatch:
    """Ordered native tool calls selected from one assistant reply."""

    calls: tuple[NativeToolCall, ...]


@dataclass(frozen=True)
class FinalizeAction:
    """One engine-owned structured completion declaration.

    Finalization deliberately retains the provider-normalized call so the
    executor can close the provider tool group with the exact call identity
    before the engine accepts completion.
    """

    call: NativeToolCall


Action: TypeAlias = CodeCell | NativeToolBatch | FinalizeAction


def is_completion_only_cell(cell: CodeCell | str, language: str = "python") -> bool:
    """Return whether a Python cell only signals ``host.submit_output``.

    Completion remains a real kernel RPC, but a direct protocol-only call is
    not scientific analysis and should not be presented as a Notebook cell.
    Calls nested inside the submitted arguments keep the cell visible because
    they may perform real computation while constructing the result.
    """
    if isinstance(cell, CodeCell):
        language = cell.language
        code = cell.code
    else:
        code = cell
    if language != "python" or not isinstance(code, str):
        return False
    try:
        tree = ast.parse(code)
    except (SyntaxError, TypeError, ValueError):
        return False
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Expr):
        return False
    call = tree.body[0].value
    if not isinstance(call, ast.Call):
        return False
    target = call.func
    if not (
        isinstance(target, ast.Attribute)
        and target.attr == "submit_output"
        and isinstance(target.value, ast.Name)
        and target.value.id == "host"
    ):
        return False
    values = [*call.args, *(keyword.value for keyword in call.keywords)]
    hidden_work = (
        ast.Call,
        ast.Await,
        ast.Yield,
        ast.YieldFrom,
        ast.NamedExpr,
        ast.ListComp,
        ast.SetComp,
        ast.DictComp,
        ast.GeneratorExp,
        ast.Lambda,
    )
    return not any(
        isinstance(node, hidden_work) for value in values for node in ast.walk(value)
    )


def extract_action(text: str) -> CodeCell | None:
    """Return the FIRST complete top-level executable cell in a model reply.

    Document order decides between languages: whichever labelled (or bare)
    executable fence closes first wins, and exactly one cell runs per step.
    The shared fence scanner preserves labelled fenced examples nested inside
    the cell (notably a literal ```tool block in a triple-quoted README). An
    incomplete outer fence is never executable.
    """
    for block in scan_fenced_blocks(text):
        if not (block.closed and block.fence_char == "`"):
            continue
        if block.info in PYTHON_INFOS:
            return CodeCell("python", block.body)
        if block.info in R_INFOS:
            return CodeCell("r", block.body)
    return None


def route_action(
    content: str,
    tool_calls: Iterable[NativeToolCall | Mapping[str, Any]] | None = None,
) -> Action | None:
    """Choose the single action channel for an assistant reply.

    Any structured native call wins over fenced code in the same reply. This
    keeps control-plane calls and scientific cells from competing for one
    turn. With no native calls, behavior is exactly ``extract_action``: the
    first complete top-level Python/R cell in document order is selected.
    """
    calls = (
        tuple(_normalize_native_tool_call(call) for call in tool_calls)
        if tool_calls is not None
        else ()
    )
    if len(calls) == 1 and calls[0].name == FINALIZE_RESPONSE_NAME:
        return FinalizeAction(calls[0])
    if calls:
        return NativeToolBatch(calls)
    return extract_action(content)


def _normalize_native_tool_call(
    call: NativeToolCall | Mapping[str, Any],
) -> NativeToolCall:
    """Convert the LLM client's canonical mapping without altering its data."""
    if isinstance(call, NativeToolCall):
        return call
    if not isinstance(call, Mapping):
        raise TypeError("native tool calls must be NativeToolCall or mapping values")
    # Passing the complete mapping through the constructor is deliberate: an
    # unexpected top-level key raises instead of being silently discarded.
    return NativeToolCall(**dict(call))


def count_code_blocks(text: str) -> int:
    """Closed top-level executable cells (both languages) in a reply — feeds
    the one-cell-per-step note when a model batches several cells."""
    n = 0
    for block in scan_fenced_blocks(text):
        if (
            block.closed
            and block.fence_char == "`"
            and (block.info in PYTHON_INFOS or block.info in R_INFOS)
        ):
            n += 1
    return n


def has_incomplete_code_block(text: str) -> bool:
    """Return whether the reply ended inside a Python/R action fence."""

    return any(
        not block.closed
        and block.fence_char == "`"
        and (block.info in PYTHON_INFOS or block.info in R_INFOS)
        for block in scan_fenced_blocks(text)
    )


# Fed back when a working turn contains neither a cell nor a tool call.
NO_CODE_NUDGE = (
    "[system] No executable action found. For a completed conversational or "
    "tool-only answer, call finalize_response as the ONLY tool call. For "
    "scientific work, reply with a ```python cell (or ```r for R) and call "
    "host.submit_output(...) from a python cell when that work is done."
)

NO_NATIVE_COMPLETION_NUDGE = (
    "[system] Prose is not a completion signal, and this model endpoint is "
    "configured without native tool calling. If the task is complete, use one "
    "small, complete ```python cell that calls host.submit_output(...) directly "
    "(the compatibility path will start Python). If scientific work remains, "
    "continue with one complete ```python or ```r cell and submit only after "
    "the real result is ready."
)

# Appended to the observation when a reply batched several cells (only the
# first one ran).
MULTI_CELL_NOTE = (
    "\n[system] NOTE: only the FIRST code cell in your reply was executed — "
    "exactly ONE cell runs per step. The later cells did NOT run, and any "
    "results you described for them are not real. Do not assume they "
    "succeeded: continue with the NEXT single cell based on the real "
    "observation above."
)

INCOMPLETE_CELL_NUDGE = (
    "[system] The previous model reply ended inside an incomplete code cell, "
    "so NOTHING from that cell was executed. Send one smaller, complete "
    "```python or ```r cell beginning before its first required dependency. "
    "Do not continue from or paste only the truncated tail."
)
