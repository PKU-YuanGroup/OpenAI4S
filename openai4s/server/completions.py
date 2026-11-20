"""User-visible projections for control actions and structured completion."""

from __future__ import annotations

import ast
import json
import re
from typing import Any, Iterable

from openai4s.agent.actions import (
    Action,
    CodeCell,
    NativeToolBatch,
    is_completion_only_cell,
)
from openai4s.server.urls import completion_artifact_url

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_SUMMARY_KEYS = (
    "summary",
    "answer",
    "conclusion",
    "message",
    "result",
    "摘要",
    "结论",
    "回答",
)
_FINDING_KEYS = ("findings", "key_findings", "发现", "关键发现")
_METRIC_KEYS = ("metrics", "measurements", "指标", "测量结果")
_LIMITATION_KEYS = ("limitations", "caveats", "限制", "局限性")
_ARTIFACT_KEYS = {"artifact", "artifacts", "report_file", "file", "files"}
_STRUCTURED_KEYS = set(
    (*_SUMMARY_KEYS, *_FINDING_KEYS, *_METRIC_KEYS, *_LIMITATION_KEYS)
)
_ERROR_SECTION = re.compile(
    r"(?:^|\n)ERROR(?P<annotated> \(cell line \d+\))?:\n"
    r"(?P<body>.*?)(?=\n\[system\]|\n\[usage|\Z)",
    re.DOTALL,
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|authorization)\b"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")


def response_language(text: Any) -> str:
    """Choose the deterministic fallback language from the user's text."""
    return "zh" if _CJK.search(str(text or "")) else "en"


#: Why a turn ended ``cancelled``. ``user`` covers an explicit Stop over REST
#: or the socket; ``auto_budget`` is an Auto Mode budget denial, which ends the
#: turn through the same cancel signal but was nobody's Stop.
CANCEL_REASONS = ("user", "auto_budget")


def cancellation_marker(reason: str, language: str = "en") -> str:
    """The durable, user-visible line that closes a cancelled turn.

    A turn stopped mid-Cell otherwise reopens with nothing after the
    in-progress narration ("... am running it now"), which is a claim that
    the stopped cell is still running.
    """
    zh = language == "zh"
    if reason == "auto_budget":
        return (
            "_已停止：已达到自动模式预算。_"
            if zh
            else "_Stopped: the Auto Mode budget was reached._"
        )
    return "_已由用户停止。_" if zh else "_Stopped by user._"


def action_narration(
    action: Action | None, language: str = "en", *, opening: bool = True
) -> str:
    """Describe an action without inventing reasoning or scientific results.

    ``opening`` is whether this is the first action of the run. "I am ...
    first, then ..." only reads right there; repeated mid-task it looked as
    if the analysis had started over (CHAT-4).
    """
    zh = language == "zh"
    if isinstance(action, CodeCell):
        # This is explicitly an in-progress status, never a result claim.  It
        # prevents long scientific cells from leaving the conversation blank
        # while the replace-in-place Notebook draft and kernel execution run.
        # A protocol-only submit cell stays silent because its structured
        # completion projection is the user-facing result.
        if is_completion_only_cell(action):
            return ""
        label = "R" if action.language == "r" else "Python"
        return (
            f"我已经准备好一个 {label} 单元，正在执行；真实输出会持续记录到 Notebook。"
            if zh
            else f"I have prepared a {label} cell and am running it now; its actual output will be recorded in the Notebook."
        )
    if not isinstance(action, NativeToolBatch) or not action.calls:
        return ""

    names = {call.name for call in action.calls}
    if len(action.calls) > 1:
        return (
            f"我正在执行 {len(action.calls)} 个相关步骤，并会根据返回结果继续分析。"
            if zh
            else f"I am running {len(action.calls)} related steps and will continue from their returned results."
        )
    name = action.calls[0].name
    if name == "web_search":
        if not opening:
            return "我正在检索相关来源。" if zh else "I am searching relevant sources."
        return (
            "我先检索相关来源，随后会说明检索结果如何影响下一步分析。"
            if zh
            else "I am searching relevant sources first, then I will explain how the results affect the next analysis step."
        )
    if name == "web_fetch":
        return (
            "我正在读取并核对这个来源中的关键证据。"
            if zh
            else "I am reading this source and checking its key evidence."
        )
    if names & {"write_file", "edit_file"}:
        return (
            "我正在保存阶段性结果，写出的文件会加入 Artifacts。"
            if zh
            else "I am saving the current results; written files will be added to Artifacts."
        )
    if names & {"read_text_file", "list_dir", "glob_files", "content_search"}:
        if not opening:
            return (
                "我正在查看相关文件和数据。"
                if zh
                else "I am looking at the relevant files and data."
            )
        return (
            "我先检查相关文件和数据，再根据实际内容继续分析。"
            if zh
            else "I am inspecting the relevant files and data before continuing from their actual contents."
        )
    if names & {"write_todos", "update_plan_step"}:
        return "我正在更新计划。" if zh else "I am updating the plan."
    if names & {"env_list", "env_use", "env_create"}:
        return (
            "我正在准备适合这一步分析的运行环境。"
            if zh
            else "I am preparing the appropriate runtime environment for this analysis step."
        )
    return (
        "我正在执行下一步，并会把关键结果反馈在对话中。"
        if zh
        else "I am running the next step and will report its key result in the conversation."
    )


def outcome_narration(
    action: Action | None,
    outcome: Any,
    language: str = "en",
    *,
    had_public_prose: bool = False,
) -> str:
    """Project a real execution outcome without exposing code or reasoning.

    Error text is reduced to one bounded, redacted exception headline.  A
    successful code turn always gets a short deterministic post-execution
    notice unless that Cell itself completed the task.  Pre-action model prose
    can only describe intent; it is never evidence that the Cell succeeded.
    """
    if not isinstance(action, CodeCell):
        return ""
    if getattr(outcome, "stop_reason", None) == "cancelled":
        return ""

    observation = str(getattr(outcome, "observation", "") or "")
    error = _error_headline(observation)
    zh = language == "zh"
    refusal = _host_refusal(error) if error else None
    if refusal is not None:
        return _refusal_narration(refusal, zh)
    if error:
        return (
            f"这个单元运行出错：`{error}`。智能体会根据这条报错修正后重试。"
            if zh
            else f"This cell failed: `{error}`. The agent will correct it from this error and try again."
        )
    if getattr(outcome, "completion", None) is not None:
        return ""

    # A cell that only keeps the session's books (plan, task list, runtime
    # environment) used to be narrated as "output:" plus its raw return dict,
    # interpreter path included. One plain line says what it did (CHAT-4).
    bookkeeping = bookkeeping_calls(action.code) if action.language == "python" else ()
    if bookkeeping:
        return _bookkeeping_narration(
            bookkeeping, _section_body(observation, "stdout:"), zh
        )

    # "No exception" is not "success": a cell whose subprocess exited 1 used
    # to be narrated as "completed successfully", and the narration carried
    # no output, so every result had to be dug out of the Notebook. Say the
    # cell finished, show the first lines it printed, and flag stderr.
    stdout = _section_body(observation, "stdout:")
    # A long stdout reaches the model as a head, an omission marker and a
    # tail; counting that preview's lines reported "160 lines" for 3001. Show
    # only the head, and give the real size the marker states.
    omitted = _OMITTED_MARKER.search(stdout)
    total_chars = omitted.group(1) if omitted else None
    if omitted:
        stdout = stdout[: omitted.start()].rstrip()
    stderr_lines = _line_count(_section_body(observation, "stderr:"))
    stdout_lines = _line_count(stdout)
    parts: list[str] = []
    if stdout_lines:
        shown = min(stdout_lines, _PREVIEW_LINES)
        if total_chars:
            shown = -1  # never "the whole output" when the model saw a cut
        if zh:
            parts.append(
                "单元已运行结束，输出："
                if shown == stdout_lines
                else "单元已运行结束，输出的前几行："
            )
        else:
            parts.append(
                "The cell finished. Output:"
                if shown == stdout_lines
                else "The cell finished. First lines of its output:"
            )
        parts.append("```text\n" + _output_preview(stdout) + "\n```")
        if total_chars:
            parts.append(
                f"（输出很长，共 {total_chars} 个字符，完整输出在 Notebook。）"
                if zh
                else f"(The output is long: {total_chars} characters in total; the "
                "full output is in the Notebook.)"
            )
        elif shown < stdout_lines:
            parts.append(
                f"（共 {stdout_lines} 行，完整输出在 Notebook。）"
                if zh
                else f"({stdout_lines} lines in total; the full output is in the Notebook.)"
            )
    elif stderr_lines:
        parts.append("单元已运行结束。" if zh else "The cell finished.")
    else:
        parts.append(
            "单元已运行结束，没有输出。" if zh else "The cell finished with no output."
        )
    if stderr_lines:
        parts.append(
            f"另有 stderr {stderr_lines} 行，可能包含警告或错误，请在 Notebook 查看。"
            if zh
            else f"It also wrote {stderr_lines} stderr line(s), which may contain "
            "warnings or errors; see the Notebook."
        )
    return "\n".join(parts)


#: The omission marker ``agent.runtime._budgeted`` puts into a long section.
_OMITTED_MARKER = re.compile(
    r"\n*\[\.\.\. [\d,]+ characters omitted \u2014 full ([\d,]+) chars[^\n]*"
)


#: A Cell that raised because a Host call in it was refused: the user denied
#: it, a rule or policy refused it, or the turn was stopped while it waited.
#: (`sdk/bash.py` words a bash refusal; the worker words every other Host one.)
_HOST_REFUSAL = re.compile(
    r"^(?:RuntimeError:\s*)?(?:bash:\s*|host\.[\w.]+ error:\s*)Permission denied:\s*"
    r"(?P<reason>.*?)(?:;\s*command was not executed)?\s*$"
)
_REFUSAL_PREFIXES = (
    "这个单元里需要批准的操作",
    "An action in this cell that needed approval",
)


def _host_refusal(headline: str) -> str | None:
    """The refusal's reason when ``headline`` is a Host refusal, else None."""

    match = _HOST_REFUSAL.match(headline or "")
    return match.group("reason").strip() if match else None


def _refusal_narration(reason: str, zh: bool) -> str:
    """A refusal is not a failure: no "this cell failed", no raw RuntimeError."""

    if reason == "turn cancelled":
        # The turn's own stop marker says what happened.
        return ""
    if reason == "approval timed out":
        return (
            "这个单元里需要批准的操作等待批准超时，没有执行。"
            if zh
            else "An action in this cell that needed approval timed out waiting for it and did not run."
        )
    if not reason or reason == "denied by user":
        return (
            "这个单元里需要批准的操作被你拒绝了，没有执行。智能体会据此调整下一步。"
            if zh
            else "An action in this cell that needed approval was declined by you and did not run. "
            "The agent will adjust its next step."
        )
    return (
        f"这个单元里需要批准的操作未获批准，没有执行（原因：{reason}）。智能体会据此调整下一步。"
        if zh
        else f"An action in this cell that needed approval was not approved and did not run "
        f"(reason: {reason}). The agent will adjust its next step."
    )


#: ``host`` calls that keep the session's books -- the task list, the plan,
#: the runtime environment -- rather than analyse anything.
_BOOKKEEPING_CALLS = frozenset(
    {
        "todo_write",
        "todo_read",
        "plan_update",
        "plan_read",
        "env.list",
        "env.list_dependencies",
        "env.use",
        "env.create",
    }
)


def _host_call(node: ast.AST) -> str | None:
    """``env.list`` for ``host.env.list(...)``; None for anything else."""

    if not isinstance(node, ast.Call):
        return None
    parts: list[str] = []
    target = node.func
    while isinstance(target, ast.Attribute):
        parts.append(target.attr)
        target = target.value
    if isinstance(target, ast.Name) and target.id == "host" and parts:
        return ".".join(reversed(parts))
    return None


def bookkeeping_calls(code: str) -> tuple[str, ...]:
    """The bookkeeping ``host`` calls of a Python cell that does nothing else.

    Empty as soon as the cell computes or prints anything of its own: a cell
    is bookkeeping only when every statement is one of these calls, bare,
    printed, or assigned to a name that is then echoed.
    """

    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return ()
    calls: list[str] = []
    held: set[str] = set()
    for statement in tree.body:
        if (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and _host_call(statement.value) in _BOOKKEEPING_CALLS
        ):
            held.add(statement.targets[0].id)
            calls.append(str(_host_call(statement.value)))
            continue
        if not isinstance(statement, ast.Expr):
            return ()
        value = statement.value
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "print"
            and len(value.args) == 1
            and not value.keywords
        ):
            value = value.args[0]
        if isinstance(value, ast.Name) and value.id in held:
            continue
        name = _host_call(value)
        if name not in _BOOKKEEPING_CALLS:
            return ()
        calls.append(str(name))
    return tuple(calls)


_PACKAGE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_MAX_NAMED_PACKAGES = 6


def _echoed_result(stdout: str) -> dict:
    """The dict a bookkeeping call returned, when the cell echoed exactly it."""

    try:
        value = ast.literal_eval(stdout.strip())
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return {}
    return value if isinstance(value, dict) else {}


def _package_labels(names: Any, log: str) -> str:
    """``statsmodels 0.15.0, patsy`` -- names, with the version the log shows."""

    labels: list[str] = []
    for item in names if isinstance(names, list) else []:
        name = str(item.get("name") if isinstance(item, dict) else item)
        if not _PACKAGE_NAME.fullmatch(name):
            continue
        version = re.search(
            rf"(?im)^\s*\+ {re.escape(name)}==([0-9][\w.+!-]*)$"
            rf"|\b{re.escape(name)}-([0-9][\w.+!]*)",
            log,
        )
        found = (version.group(1) or version.group(2)) if version else ""
        labels.append(f"{name} {found}".strip())
    shown = labels[:_MAX_NAMED_PACKAGES]
    if len(labels) > len(shown):
        shown.append(f"+{len(labels) - len(shown)}")
    return ", ".join(shown)


def _bookkeeping_line(name: str, result: dict, zh: bool) -> str:
    if name == "todo_write":
        todos = result.get("todos")
        if isinstance(todos, list):
            done = sum(
                1
                for todo in todos
                if isinstance(todo, dict) and todo.get("status") == "completed"
            )
            return (
                f"更新了计划（{len(todos)} 步，完成 {done} 步）。"
                if zh
                else f"Updated the plan ({len(todos)} steps, {done} done)."
            )
        return "更新了计划。" if zh else "Updated the plan."
    if name == "plan_update":
        return "更新了计划进度。" if zh else "Updated the plan's progress."
    if name in {"todo_read", "plan_read"}:
        return "查看了当前计划。" if zh else "Checked the current plan."
    if name in {"env.list", "env.list_dependencies"}:
        environments = result.get("environments")
        if isinstance(environments, list):
            return (
                f"列出了 {len(environments)} 个环境。"
                if zh
                else f"Listed {len(environments)} environments."
            )
        return "列出了可用环境。" if zh else "Listed the available environments."
    if name == "env.use":
        env = result.get("env")
        label = str(env.get("name") or "") if isinstance(env, dict) else ""
        if _PACKAGE_NAME.fullmatch(label):
            return (
                f"已切换到 {label} 环境，下一个单元起生效。"
                if zh
                else f"Switched to the {label} environment, from the next cell on."
            )
        return "已切换运行环境。" if zh else "Switched the runtime environment."
    # env.create: an install into the kernel's environment. Only names and
    # versions are repeated; the result's log and interpreter path are not.
    log = str(result.get("log") or "")
    installed = _package_labels(result.get("installed"), log)
    failed = _package_labels(result.get("failed"), "")
    parts: list[str] = []
    if installed:
        parts.append(f"已安装 {installed}。" if zh else f"Installed {installed}.")
    if failed:
        parts.append(f"未能安装 {failed}。" if zh else f"Could not install {failed}.")
    if parts:
        return ("" if zh else " ").join(parts)
    if result.get("ok") is False:
        return (
            "安装没有完成，详情见 Notebook。"
            if zh
            else "The install did not complete; see the Notebook for details."
        )
    return "安装步骤已结束。" if zh else "The install step finished."


def _bookkeeping_narration(calls: tuple[str, ...], stdout: str, zh: bool) -> str:
    # Only the last value is echoed, so a result is matched to a call by its
    # own keys and every other call gets its plain line.
    result = _echoed_result(stdout)
    lines: list[str] = []
    for name in calls:
        line = _bookkeeping_line(name, result, zh)
        if line not in lines:
            lines.append(line)
    return ("" if zh else " ").join(lines)


_BOOKKEEPING_SENTENCE = (
    r"(?:更新了计划(?:（\d+ 步，完成 \d+ 步）)?。|更新了计划进度。|查看了当前计划。"
    r"|列出了 \d+ 个环境。|列出了可用环境。|已切换到 \S+ 环境，下一个单元起生效。"
    r"|已切换运行环境。|已安装 [A-Za-z0-9_.+, -]+。|未能安装 [A-Za-z0-9_.+, -]+。|安装步骤已结束。"
    r"|安装没有完成，详情见 Notebook。"
    r"|Updated the plan(?: \(\d+ steps, \d+ done\))?\.|Updated the plan's progress\."
    r"|Checked the current plan\.|Listed \d+ environments\."
    r"|Listed the available environments\."
    r"|Switched to the \S+ environment, from the next cell on\."
    r"|Switched the runtime environment\.|Installed [A-Za-z0-9_.+, -]+\."
    r"|Could not install [A-Za-z0-9_.+, -]+\.|The install step finished\."
    r"|The install did not complete; see the Notebook for details\.)"
)
_BOOKKEEPING_NARRATION = re.compile(
    rf"{_BOOKKEEPING_SENTENCE}(?: ?{_BOOKKEEPING_SENTENCE})*"
)


#: How much of a cell's stdout the chat repeats after it runs.
_PREVIEW_LINES = 6
_PREVIEW_LINE_CHARS = 200
_FENCE = "`" * 3


def _output_preview(stdout: str) -> str:
    lines = []
    for line in stdout.splitlines()[:_PREVIEW_LINES]:
        line = _BEARER.sub("Bearer <redacted>", line)
        line = _SECRET_ASSIGNMENT.sub(r"\1\2<redacted>", line)
        if len(line) > _PREVIEW_LINE_CHARS:
            line = line[: _PREVIEW_LINE_CHARS - 1] + "…"
        # A fence inside the output would close the block early.
        lines.append(line.replace(_FENCE, "'" * 3))
    return "\n".join(lines)


def completion_message(
    completion: Any,
    artifacts: Iterable[dict] = (),
    *,
    previous_text: str = "",
    language: str = "en",
    require_fallback: bool = True,
    trusted_delivery: bool = False,
) -> str:
    """Render a submitted result into a durable, human-facing final message."""
    spec = completion if isinstance(completion, dict) else {}
    output = spec.get("output", completion)
    bullets = spec.get("completion_bullets") or []
    zh = language == "zh"
    previous = _normalized(previous_text)
    parts: list[str] = []

    summary = _summary_text(output)
    if summary and not _summary_already_visible(output, summary, previous):
        parts.append(summary)

    structured = False
    for heading_en, heading_zh, keys in (
        ("Key findings:", "关键发现：", _FINDING_KEYS),
        ("Metrics:", "指标：", _METRIC_KEYS),
        ("Limitations:", "限制与局限：", _LIMITATION_KEYS),
    ):
        value = _first_value(output, keys)
        body = _structured_body(value)
        structured = structured or bool(body)
        if body and _normalized(body) not in previous:
            parts.append((heading_zh if zh else heading_en) + "\n" + body)

    if not parts:
        fallback = _fallback_output_text(output)
        if fallback and _normalized(fallback) not in previous:
            parts.append(fallback)

    fresh_bullets = [
        str(item).strip()
        for item in bullets
        if str(item).strip() and _normalized(str(item)) not in previous
    ]
    bullets_at = len(parts)  # the list goes here, once the artifacts are known

    artifact_lines = _artifact_lines(artifacts, trusted_delivery=trusted_delivery)
    # ART-8: scripts, logs and copies of inputs are how the result was made,
    # not the result; listing them with the figures and tables buried the
    # deliverables. They stay linked, on one line after the deliverables.
    deliverables = [line for kind, line in artifact_lines if kind == "deliverable"]
    process = [line for kind, line in artifact_lines if kind == "process"]
    if deliverables:
        heading = "产物：" if zh else "Artifacts:"
        parts.append(heading + "\n" + "\n".join(f"- {link}" for link in deliverables))
    if process:
        heading = (
            f"过程文件（脚本、日志、输入副本，{len(process)} 个）："
            if zh
            else f"Working files (scripts, logs, input copies; {len(process)}):"
        )
        parts.append(heading + " " + ("、" if zh else ", ").join(process))

    # CHAT-5: a direct answer -- one paragraph, nothing structured, no files --
    # is not followed by a "Completed work" list that only restates it. The
    # list stays whenever it says more than the paragraph does.
    direct = (
        bool(summary)
        and not structured
        and not artifact_lines
        and sum(len(item) for item in fresh_bullets) <= len(summary)
    )
    if fresh_bullets and not direct:
        heading = "完成内容：" if zh else "Completed work:"
        parts.insert(
            bullets_at,
            heading + "\n" + "\n".join(f"- {item}" for item in fresh_bullets),
        )

    if not parts and previous:
        return ""
    if not parts and require_fallback:
        return "任务已完成。" if zh else "The task is complete."
    return "\n\n".join(parts)


def _summary_text(output: Any) -> str:
    if isinstance(output, str):
        return output.strip()
    if not isinstance(output, dict):
        if output is None:
            return ""
        return str(output).strip()
    for key in _SUMMARY_KEYS:
        value = output.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:4000]
        if isinstance(value, (int, float, bool)):
            return f"{key}: {value}"
    return ""


def _fallback_output_text(output: Any) -> str:
    """Keep legacy arbitrary outputs visible when no public fields exist."""
    if not isinstance(output, dict):
        return ""
    visible = {
        str(key): value
        for key, value in output.items()
        if key not in _ARTIFACT_KEYS and key not in _STRUCTURED_KEYS
    }
    if not visible or set(visible) <= {"ok", "status"}:
        return ""
    rendered = json.dumps(visible, ensure_ascii=False, indent=2, default=str)
    return f"```json\n{rendered[:4000]}\n```"


def _first_value(output: Any, keys: tuple[str, ...]) -> Any:
    if not isinstance(output, dict):
        return None
    for key in keys:
        value = output.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _structured_body(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, dict):
        items = [
            f"- {_readable_key(str(key))[:120]}: {_compact_value(item)}"
            for key, item in list(value.items())[:20]
        ]
        return "\n".join(items)[:4000]
    if isinstance(value, (list, tuple)):
        items = [f"- {_compact_value(item)}" for item in list(value)[:20]]
        return "\n".join(items)[:4000]
    return str(value).strip()[:4000]


_SNAKE_KEY = re.compile(r"[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+")


def _readable_key(key: str) -> str:
    """``cox_tmb_HR_low`` reads as "cox tmb HR low" (ART-8); prose keys stay."""

    return key.replace("_", " ") if _SNAKE_KEY.fullmatch(key) else key


def _compact_value(value: Any) -> str:
    if isinstance(value, float) and not isinstance(value, bool):
        # 0.42712345 -> 0.4271; exponent forms stay as repr, which is clearer.
        short = format(value, ".4g")
        return short if "e" not in short else repr(value)
    if isinstance(value, str):
        return value.strip()[:500]
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, default=str)[:500]
    return str(value)[:500]


def _error_headline(observation: str) -> str:
    match = _ERROR_SECTION.search(observation)
    if not match:
        return ""
    # ``format_observation`` only emits a real ERROR section when the cell
    # actually raised, and it is always either annotated with a cell line or
    # immediately followed by the "[system] The cell stopped" block.  A
    # *successful* cell that merely prints an "ERROR:" line to stdout has
    # neither, and must not be narrated as a failure.
    if not match.group("annotated") and not observation[match.end() :].startswith(
        "\n[system]"
    ):
        return ""
    headline = _exception_line(match.group("body"))
    if not headline:
        return "execution error"
    headline = _BEARER.sub("Bearer <redacted>", headline)
    headline = _SECRET_ASSIGNMENT.sub(r"\1\2<redacted>", headline)
    headline = headline.replace("`", "'")
    return headline[:240]


#: ``Name`` or ``Name: message`` at column 0, dotted names included
#: (``patsy.PatsyError: ...``, ``KeyboardInterrupt``).
_EXCEPTION_HEADER = re.compile(r"(?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*(?::(?:\s.*)?)?")
#: A traceback's caret/underline line, which points at code and says nothing.
_MARKER_LINE = re.compile(r"[\s^~]+")
_TRACEBACK_START = "Traceback (most recent call last):"


def _exception_line(body: str) -> str:
    """The line that names the exception, not whatever line came last.

    A multi-line exception message ends with its own detail -- patsy prints
    the formula and a caret line under ``PatsyError: ...`` -- so the last line
    of a traceback can be ``^^^^^^^`` (CHAT-3). Python prints the exception
    header as the first unindented line after the last traceback's frames;
    without a traceback (a SyntaxError, an R error) it is the last unindented
    ``Name: message`` line, and failing that the last line that is not a marker.
    """
    lines = [line.rstrip() for line in body.splitlines() if line.strip()]
    start = max(
        (index for index, line in enumerate(lines) if line == _TRACEBACK_START),
        default=-1,
    )
    if start >= 0:
        for line in lines[start + 1 :]:
            if line[0].isspace():
                continue
            if _EXCEPTION_HEADER.fullmatch(line):
                return line
            break
    for line in reversed(lines):
        if not line[0].isspace() and ":" in line and _EXCEPTION_HEADER.fullmatch(line):
            return line
    for line in reversed(lines):
        if not _MARKER_LINE.fullmatch(line):
            return line.strip()
    return ""


def _section_body(observation: str, marker: str) -> str:
    needle = "\n" + marker + "\n"
    start = observation.find(needle)
    if start < 0:
        return ""
    body = observation[start + len(needle) :]
    stops = [
        index
        for boundary in ("\nstderr:", "\nERROR", "\n[system]", "\n[usage")
        if (index := body.find(boundary)) >= 0
    ]
    if stops:
        body = body[: min(stops)]
    return body.rstrip()


def _line_count(body: str) -> int:
    return len(body.splitlines()) if body else 0


def _summary_already_visible(output: Any, summary: str, previous: str) -> bool:
    if _normalized(summary) in previous:
        return True
    if not isinstance(output, dict):
        return False
    for key in _SUMMARY_KEYS:
        value = output.get(key)
        normalized = _normalized(str(value)) if value is not None else ""
        if normalized and normalized in previous:
            return True
    return False


#: Suffixes and folders of files that record how a result was made.
_PROCESS_SUFFIXES = frozenset(
    {".py", ".r", ".sh", ".bash", ".ipynb", ".log", ".pyc", ".tmp", ".bak", ".rds"}
)
_PROCESS_DIRS = ("project-inputs/", "inputs/", "__pycache__/", ".openai4s")


def _artifact_kind(name: str) -> str:
    lower = name.lower().replace("\\", "/")
    dot = lower.rfind(".")
    suffix = lower[dot:] if dot > lower.rfind("/") else ""
    if suffix in _PROCESS_SUFFIXES:
        return "process"
    if lower.startswith(_PROCESS_DIRS) or any(f"/{d}" in lower for d in _PROCESS_DIRS):
        return "process"
    return "deliverable"


def _artifact_lines(
    artifacts: Iterable[dict], *, trusted_delivery: bool = False
) -> list[tuple[str, str]]:
    """``(kind, markdown link)`` per artifact, deliverables and process files."""
    lines: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        name = str(artifact.get("filename") or "artifact")
        version_id = artifact.get("version_id") or artifact.get("latest_version_id")
        ident = str(artifact.get("artifact_id") or artifact.get("id") or "")
        target = completion_artifact_url(
            artifact_id=ident,
            filename=name,
            version_id=version_id,
            trusted_delivery=trusted_delivery,
        )
        # Under trusted delivery a missing version id cannot degrade to the
        # mutable Artifact head.  Flag-off keeps the pre-Stage-1 fallback.
        if target is None:
            continue
        key = ((str(version_id) if trusted_delivery else ident), name)
        if key in seen:
            continue
        seen.add(key)
        label = name.replace("[", "\\[").replace("]", "\\]")
        lines.append((_artifact_kind(name), f"[{label}]({target})"))
    return lines


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()


#: The status lines this module writes around actions. A notebook export and
#: anything else that wants only the model's own narration filters these out;
#: ``tests/test_server_completions.py`` checks every template is recognised so
#: the two cannot drift apart.
_PROGRESS_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"我已经准备好一个 (?:R|Python) (?:单元|Cell)，正在执行；真实输出会持续记录到 Notebook。",
        r"I have prepared a (?:R|Python) cell and am running it now; its actual "
        r"output will be recorded in the Notebook\.",
        r"我正在执行 \d+ 个相关步骤，并会根据返回结果继续分析。",
        r"I am running \d+ related steps and will continue from their returned "
        r"results\.",
        r"我先检索相关来源，随后会说明检索结果如何影响下一步分析。",
        r"我正在检索相关来源。",
        r"I am searching relevant sources\.",
        r"I am searching relevant sources first, then I will explain how the "
        r"results affect the next analysis step\.",
        r"我正在读取并核对这个来源中的关键证据。",
        r"I am reading this source and checking its key evidence\.",
        r"我正在保存阶段性结果，写出的文件会加入 Artifacts。",
        r"I am saving the current results; written files will be added to "
        r"Artifacts\.",
        r"我先检查相关文件和数据，再根据实际内容继续分析。",
        r"我正在查看相关文件和数据。",
        r"I am looking at the relevant files and data\.",
        r"我正在更新计划。",
        r"I am updating the plan\.",
        r"I am inspecting the relevant files and data before continuing from "
        r"their actual contents\.",
        r"我正在准备适合这一步分析的运行环境。",
        r"I am preparing the appropriate runtime environment for this analysis "
        r"step\.",
        r"我正在执行下一步，并会把关键结果反馈在对话中。",
        r"I am running the next step and will report its key result in the "
        r"conversation\.",
        r"_(?:已停止：已达到自动模式预算。|已由用户停止。|Stopped: the Auto Mode "
        r"budget was reached\.|Stopped by user\.)_",
    )
)
_PROGRESS_PREFIXES = (
    "The cell finished",
    "单元已运行结束",
    "This cell failed: `",
    "这个单元运行出错：`",
    *_REFUSAL_PREFIXES,
    # Written by earlier versions; still in stored sessions.
    "This cell completed successfully",
    "这个 Cell 已成功完成",
    "Cell 已运行结束",
    "这个 Cell 执行失败：`",
    "这个 Cell 里需要批准的操作",
)


def is_progress_narration(text: Any) -> bool:
    """Whether ``text`` is a status line written here, not the model's prose."""

    value = str(text or "").strip()
    if not value:
        return False
    if value.startswith(_PROGRESS_PREFIXES):
        return True
    if _BOOKKEEPING_NARRATION.fullmatch(value):
        return True
    return any(pattern.fullmatch(value) for pattern in _PROGRESS_PATTERNS)


__all__ = [
    "CANCEL_REASONS",
    "action_narration",
    "bookkeeping_calls",
    "cancellation_marker",
    "completion_message",
    "is_progress_narration",
    "outcome_narration",
    "response_language",
]
