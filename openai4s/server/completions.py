"""User-visible projections for control actions and structured completion."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable
from urllib.parse import quote

from openai4s.agent.actions import (
    Action,
    CodeCell,
    NativeToolBatch,
    is_completion_only_cell,
)

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


def action_narration(action: Action | None, language: str = "en") -> str:
    """Describe an action without inventing reasoning or scientific results."""
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
            f"我已经准备好一个 {label} Cell，正在执行；真实输出会持续记录到 Notebook。"
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
        return (
            "我先检查相关文件和数据，再根据实际内容继续分析。"
            if zh
            else "I am inspecting the relevant files and data before continuing from their actual contents."
        )
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
    if error:
        return (
            f"这个 Cell 执行失败：`{error}`。下一轮会依据真实 traceback 修复。"
            if zh
            else f"This cell failed: `{error}`. The next step will repair it from the actual traceback."
        )
    if getattr(outcome, "completion", None) is not None:
        return ""

    stdout_lines = _section_line_count(observation, "stdout:")
    stderr_lines = _section_line_count(observation, "stderr:")
    details: list[str] = []
    if stdout_lines:
        details.append(
            f"stdout {stdout_lines} 行" if zh else f"{stdout_lines} stdout line(s)"
        )
    if stderr_lines:
        details.append(
            f"stderr {stderr_lines} 行" if zh else f"{stderr_lines} stderr line(s)"
        )
    if details:
        joined = "、".join(details) if zh else " and ".join(details)
        return (
            f"这个 Cell 已成功完成，产生了 {joined}；实际输出已记录在 Notebook。"
            if zh
            else f"This cell completed successfully with {joined}; the actual output is recorded in the Notebook."
        )
    return (
        "这个 Cell 已成功完成（没有 stdout 或 stderr）；运行状态已保留在 Notebook。"
        if zh
        else "This cell completed successfully with no stdout or stderr; its runtime state is recorded in the Notebook."
    )


def completion_message(
    completion: Any,
    artifacts: Iterable[dict] = (),
    *,
    previous_text: str = "",
    language: str = "en",
    require_fallback: bool = True,
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

    for heading_en, heading_zh, keys in (
        ("Key findings:", "关键发现：", _FINDING_KEYS),
        ("Metrics:", "指标：", _METRIC_KEYS),
        ("Limitations:", "限制与局限：", _LIMITATION_KEYS),
    ):
        value = _first_value(output, keys)
        body = _structured_body(value)
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
    if fresh_bullets:
        heading = "完成内容：" if zh else "Completed work:"
        parts.append(heading + "\n" + "\n".join(f"- {item}" for item in fresh_bullets))

    artifact_lines = _artifact_lines(artifacts)
    if artifact_lines:
        heading = "产物：" if zh else "Artifacts:"
        parts.append(heading + "\n" + "\n".join(artifact_lines))

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
            f"- {str(key)[:120]}: {_compact_value(item)}"
            for key, item in list(value.items())[:20]
        ]
        return "\n".join(items)[:4000]
    if isinstance(value, (list, tuple)):
        items = [f"- {_compact_value(item)}" for item in list(value)[:20]]
        return "\n".join(items)[:4000]
    return str(value).strip()[:4000]


def _compact_value(value: Any) -> str:
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
    lines = [line.strip() for line in match.group("body").splitlines() if line.strip()]
    if not lines:
        return "execution error"
    headline = lines[-1]
    headline = _BEARER.sub("Bearer <redacted>", headline)
    headline = _SECRET_ASSIGNMENT.sub(r"\1\2<redacted>", headline)
    headline = headline.replace("`", "'")
    return headline[:240]


def _section_line_count(observation: str, marker: str) -> int:
    needle = "\n" + marker + "\n"
    start = observation.find(needle)
    if start < 0:
        return 0
    body = observation[start + len(needle) :]
    stops = [
        index
        for boundary in ("\nstderr:", "\nERROR", "\n[usage")
        if (index := body.find(boundary)) >= 0
    ]
    if stops:
        body = body[: min(stops)]
    return len(body.rstrip().splitlines()) if body.rstrip() else 0


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


def _artifact_lines(artifacts: Iterable[dict]) -> list[str]:
    lines: list[str] = []
    seen: set[tuple[str, str]] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        name = str(artifact.get("filename") or "artifact")
        ident = str(artifact.get("artifact_id") or artifact.get("id") or "")
        key = (ident, name)
        if key in seen:
            continue
        seen.add(key)
        target = quote(ident or name, safe="")
        label = name.replace("[", "\\[").replace("]", "\\]")
        lines.append(f"- [{label}](/api/artifacts/{target})")
    return lines


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()


__all__ = [
    "action_narration",
    "completion_message",
    "outcome_narration",
    "response_language",
]
