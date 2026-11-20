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
import re
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

# Appended to the native results when the same reply also carried a complete
# cell. ``route_action`` runs only the native calls, and the model used to see
# nothing but their results: a reply that wrote a script with ``write_file``
# and ran it in a cell got back the file's byte count and no trace of the run,
# sent the identical pair again, and the no-progress circuit stopped the turn
# on its third identical ``write_file``.
UNEXECUTED_CELL_NOTE = (
    "\n[system] NOTE: your reply also contained a ```{language} code cell. It "
    "was NOT executed — a reply that contains native tool calls runs only "
    "those calls, so nothing from that cell ran and it produced no output. "
    "If the cell still needs to run, send it as the ONLY action of your next "
    "reply, with no tool calls."
)


# Appended to the native results when the reply says it will run code in a cell
# or the kernel but carries no cell at all. Doubao Seed 2.1 Pro wrote "Now I'll
# download the PDF in the kernel and extract its table" in 30 of 48 consecutive
# replies and sent a web search or a fetch each time: nothing in a tool result
# showed that no code had run, so it never sent the cell.
ANNOUNCED_CELL_NOTE = (
    "\n[system] NOTE: your reply says it will run code in a cell or the "
    "kernel, but it carried no ```python cell, so no code ran: native tool "
    "calls cannot run Python. To run code, send one fenced ```python (or "
    "```r) cell as the ONLY action of your next reply, with no tool calls."
)

# What such a reply says, in the two languages the Web UI is used in. The
# phrasing varies from turn to turn ("in the kernel", "entering the Python
# kernel", "execute Python", "move into the Python runtime"), so a verb of
# going or running next to Python, the kernel or the runtime counts too. A noun
# like "single-cell" or "T cell" does not match: the cell has to be code. A
# false alarm costs one sentence in a tool result; a miss left a model
# announcing the same step for thirty turns.
#
# The Chinese half names the verb, not the noun. Doubao Seed wrote "现在正式
# 运行 Python 读取 Excel", "现在用 Python 检查…", "运行代码", "运行脚本" for
# twenty-one tool-only replies, and a list of nouns (代码单元, 内核) matched one
# of them. A verb of running, using or writing next to Python, R, code, a
# script or a cell is the promise; the same verb marked as finished (已运行,
# 运行完毕, 运行结果) is a report of work that did run, and is left alone.
# So is a later item of a stated plan ("先检索文献，再下载结构、用 R 画图，
# 最后写综述"): that reply promises a search now, and telling it no code ran
# pushes a model to run a cell before it has anything to run it on.
_ZH_NOW = (
    r"(?<!已)(?<!已经)(?<!曾)(?<!刚才)(?<!之前)(?<!上次)"
    r"(?<!再)(?<!、)(?<!然后)(?<!最后)(?<!之后)(?<!随后)(?<!稍后)"
)
_ZH_CLAUSE = r"[^。；;，,\n]"
_ANNOUNCED_CELL = re.compile(
    r"\b(?:python|code|foreground|r)\s+cell\b"
    r"|\b(?:in|into|inside|via|using|through|within|to)\s+(?:the\s+|a\s+)?"
    r"(?:python\s+|r\s+)?(?:kernel|runtime)\b"
    r"|\b(?:enter|entering|execute|executing|run|running|switch|switching|"
    r"move|moving|go|going)\b[^.\n]{0,60}\b(?:python|kernel|runtime)\b"
    r"|代码单元|python\s*单元|在内核|内核中|运行时(?!间)"
    # 运行 Python / 运行代码 / 运行检查脚本 / 执行 R 脚本
    rf"|{_ZH_NOW}(?:运行|执行|跑)"
    r"(?![完了过毕后时]|结束|成功|失败|结果|出错|报错|环境)"
    rf"{_ZH_CLAUSE}{{0,12}}?(?:python|代码|脚本|程序|单元|r(?![a-z0-9_²-]))"
    # 把脚本跑一下
    rf"|{_ZH_NOW}把{_ZH_CLAUSE}{{0,10}}?(?:代码|脚本|程序|单元)"
    rf"{_ZH_CLAUSE}{{0,6}}?(?:运行|执行|跑)"
    # 用 Python 读取… / 在 R 中…
    rf"|{_ZH_NOW}(?:用|使用|在)\s*(?:python|r(?![a-z0-9_²-]))"
    # 写一段代码检查…
    rf"|{_ZH_NOW}(?:写|编写){_ZH_CLAUSE}{{0,8}}?(?:代码|单元)",
    re.IGNORECASE,
)

# Native tools that do run code: a reply that names a cell next to one of them
# is not a promise it broke.
CODE_RUNNING_TOOLS = frozenset({"exec_background"})

# Every note the Engine appends to a tool result starts with this, so the
# no-progress circuit can tell a tool's own answer from a note riding on it.
ENGINE_NOTE_PREFIX = "\n[system] NOTE:"

# How many consecutive tool-only replies, with no cell run in between, earn
# the wording-independent reminder below.
TOOL_ONLY_STREAK_TURNS = 6

# The backstop for a reply `_ANNOUNCED_CELL` cannot read. "现在读取 Excel，
# 检查工作表与 Layout" promises a cell without naming Python, code or a
# kernel, and no pattern will keep up with how a model rephrases it. What does
# not vary is the shape: reply after reply of tool calls and no cell. It is
# sent once per streak, because a literature task that needs no code is the
# same shape and should pay one sentence for it, not one per turn.
TOOL_ONLY_STREAK_NOTE = (
    "\n[system] NOTE: your last {count} replies were tool calls only, and no "
    "code cell has run in that time. Tool calls never execute Python or R: "
    "only a complete fenced ```python (or ```r) cell does, sent as the ONLY "
    "action of a reply with no tool calls. If the next step needs code, send "
    "that cell now. If this task needs no code, carry on."
)


def unexecuted_cell_note(content: str, action: Action | None) -> str:
    """The note for a native action that displaced a cell, else ``""``.

    It names the cell ``extract_action`` would have run had the reply carried
    no native calls, so it fires exactly when routing made that choice.
    """
    if not isinstance(action, (NativeToolBatch, FinalizeAction)):
        return ""
    cell = extract_action(content if isinstance(content, str) else "")
    if cell is None:
        return ""
    return UNEXECUTED_CELL_NOTE.format(language=cell.language)


def announced_cell_note(content: str, action: Action | None) -> str:
    """The note for a native batch whose reply promised a cell it never sent.

    The counterpart of :func:`unexecuted_cell_note`: that one fires when a
    complete cell was displaced by native calls, this one when the reply names
    a cell or the kernel and carries no executable cell at all.
    """
    if not isinstance(action, NativeToolBatch):
        return ""
    if any(call.name in CODE_RUNNING_TOOLS for call in action.calls):
        return ""
    text = content if isinstance(content, str) else ""
    if extract_action(text) is not None or not _ANNOUNCED_CELL.search(text):
        return ""
    return ANNOUNCED_CELL_NOTE


def tool_only_streak_note(action: Action | None, streak: int) -> str:
    """The note for the reply that makes a tool-only streak long enough.

    ``streak`` is the circuit's count of consecutive native batches since the
    last executed cell. It passes ``TOOL_ONLY_STREAK_TURNS`` once, so the note
    is sent once per streak however long the streak runs.
    """
    if not isinstance(action, NativeToolBatch) or streak != TOOL_ONLY_STREAK_TURNS:
        return ""
    return TOOL_ONLY_STREAK_NOTE.format(count=streak)
