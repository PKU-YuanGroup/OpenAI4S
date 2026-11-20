"""Model context for explicitly continuing a stopped turn; never replay work."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

_TOOL_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}")


def recovery_message(
    reason: str,
    *,
    progress_reason: str | None = None,
    tool_names: Iterable[str] = (),
) -> dict[str, str] | None:
    """The same bounded note for live history and Action Ledger reconstruction.

    Only local reason codes and identifier-shaped tool names enter the note.
    Arguments, provider errors, and incomplete streamed actions are excluded.
    The next external user message starts a fresh circuit epoch, but must not
    erase the explanation of why the previous attempt stopped.
    """
    if reason == "no_progress":
        cause = {
            "same_action": "identical tool calls returned unchanged results",
            "same_target": (
                "the same file was read or rewritten repeatedly, changing only "
                "the window or the contents"
            ),
            "similar_tool_error": "tool calls repeatedly produced the same error",
            "consecutive_malformed": "consecutive tool calls had invalid arguments",
            "long_text_repeat": "the same analysis was repeated without an action",
            "empty_reply_repeat": (
                "replies came back empty, with no text or action (the model "
                "may be spending its whole completion budget on reasoning)"
            ),
        }.get(progress_reason or "", "repeated actions made no progress")
        names = _tool_identifiers(tool_names)[:8]
        target = " Recent tool identifiers: " + ", ".join(names) + "." if names else ""
        detail = (
            f"The previous turn stopped because {cause}.{target} "
            "Do not repeat the same tool calls with identical arguments or the same analysis. "
            "On continuation, use the recorded results to choose a different approach. "
            "If the task is blocked, explain the missing prerequisite and ask for the "
            "specific user input needed instead of starting the loop again."
        )
    elif reason in {"llm_stream_timeout", "llm_stream_interrupted"}:
        detail = (
            "The previous model response was interrupted before it completed. "
            "Its partial tool arguments and code were not executed. "
            "Earlier completed tool calls and cells remain recorded in this history. "
            "On continuation, inspect those results and continue only the unfinished work. "
            "Do not replay completed actions, resubmit jobs, or recreate existing artifacts "
            "merely because the model stream was interrupted."
        )
    elif reason in {"llm_deadline_exceeded", "llm_response_too_large"}:
        # A local limit cuts a committed reply off exactly the way an upstream
        # interruption does, so the same "do not replay" rule applies. Its own
        # branch, not a wider set on the one above: the cause is local, so the
        # remedy is a narrower next step rather than simply asking again, and
        # the wording has to stay true when the limit was reached before any
        # bytes were sent.
        limit = (
            "this call's total time limit"
            if reason == "llm_deadline_exceeded"
            else "this daemon's response size limit"
        )
        detail = (
            f"The previous turn stopped because it reached {limit}. "
            "Any partial tool arguments or code from that reply were not executed. "
            "Earlier completed tool calls and cells remain recorded in this history. "
            "On continuation, inspect those results and continue only the unfinished "
            "work, in a narrower step that fits within the limit. "
            "Do not replay completed actions, resubmit jobs, or recreate existing "
            "artifacts merely because the previous reply was cut off."
        )
    else:
        return None
    return {"role": "system", "content": "[Stopped turn recovery]\n" + detail}


def _tool_identifiers(tool_names: Iterable[Any]) -> list[str]:
    """Distinct identifier-shaped tool names, in order; anything else is dropped."""
    return list(
        dict.fromkeys(
            name
            for name in tool_names
            if isinstance(name, str) and _TOOL_IDENTIFIER.fullmatch(name)
        )
    )


def progress_stop_detail(
    progress_reason: str | None,
    circuit: Any,
    tool_names: Iterable[Any] = (),
) -> dict[str, Any] | None:
    """What the no-progress circuit stopped, as the facts a user is owed.

    The stop notice used to say "Stopped repeating actions" and nothing about
    which action or how often, and the calls it meant -- three ``query``
    no-ops in the reviewed session -- are among the tools that draw no step
    card, so nothing on screen showed what had repeated. Only the reason code,
    the count, identifier-shaped tool names and, for a re-read file, its
    workspace path leave here: never arguments or results.
    """
    if not progress_reason:
        return None
    count = int(getattr(circuit, "trip_count", 0) or 0)
    tools = _tool_identifiers(tool_names)[:3]
    target = ""
    if progress_reason == "same_target":
        tools = _tool_identifiers([getattr(circuit, "same_target_tool", None)])
        target = str(getattr(circuit, "same_target_path", "") or "")
    elif progress_reason in {"long_text_repeat", "empty_reply_repeat"}:
        tools = []
    return {
        "reason": progress_reason,
        "count": count,
        "tools": tools,
        "target": target,
    }


def _target_label(target: str, language: str) -> str:
    """A re-read file as the user knows it; a spilled output is not a file to them."""
    if not target:
        return ""
    if target.startswith(".openai4s/"):
        return "一份已存档的工具输出" if language == "zh" else "a saved tool output"
    shown = target if len(target) <= 80 else "…" + target[-79:]
    return f"`{shown}`"


def _what_repeated(detail: Mapping[str, Any], language: str) -> str:
    reason = str(detail.get("reason") or "")
    count = int(detail.get("count") or 0)
    tools = [f"`{name}`" for name in detail.get("tools") or ()]
    if language == "zh":
        tool = "、".join(tools)
        times = f"连续 {count} 次" if count > 1 else "反复"
        if reason == "same_action" and tool:
            return f"模型{times}发出完全相同的 {tool} 调用，结果没有变化"
        if reason == "same_target" and tool:
            label = _target_label(str(detail.get("target") or ""), language)
            where = (
                f"对 {label} "
                if label.startswith("`")
                else f"对{label or '同一个文件'}"
            )
            # Counted since the last executed cell, not necessarily in a row.
            often = f"调用了 {count} 次" if count > 1 else "反复调用"
            return f"模型{where}{often} {tool}，每次只换了读取范围或写入内容"
        if reason == "similar_tool_error" and tool:
            return f"{tool} {times}返回同一个错误"
        if reason == "consecutive_malformed":
            return f"模型{times}发出参数格式无效的工具调用" + (
                f"（{tool}）" if tool else ""
            )
        if reason == "long_text_repeat":
            return f"模型{times}给出几乎相同的长文本，没有执行任何动作"
        if reason == "empty_reply_repeat":
            return f"模型{times}返回空回复，没有文字也没有动作"
        return ""
    tool = ", ".join(tools)
    times = f"{count} times in a row" if count > 1 else "repeatedly"
    if reason == "same_action" and tool:
        return (
            f"the model sent the identical {tool} call {times} and its result "
            "never changed"
        )
    if reason == "same_target" and tool:
        label = _target_label(str(detail.get("target") or ""), language)
        often = f"{count} times" if count > 1 else "repeatedly"
        return (
            f"the model called {tool} on {label or 'the same file'} {often}, "
            "changing only the window or the contents"
        )
    if reason == "similar_tool_error" and tool:
        return f"{tool} returned the same error {times}"
    if reason == "consecutive_malformed":
        return (
            "the model sent a tool call with invalid arguments"
            + (f" ({tool})" if tool else "")
            + f" {times}"
        )
    if reason == "long_text_repeat":
        return (
            f"the model repeated nearly the same long text {times} without "
            "taking an action"
        )
    if reason == "empty_reply_repeat":
        return f"the model returned an empty reply {times}, with no text and no action"
    return ""


def no_progress_notice(detail: Mapping[str, Any] | None, language: str = "en") -> str:
    """The user-visible line that closes a turn the circuit stopped."""
    what = _what_repeated(detail, language) if detail else ""
    if language == "zh":
        lead = (
            f"已停止重复动作：{what}。" if what else "已停止重复动作。已记录重复原因；"
        )
        return (
            lead
            + "继续时会要求模型利用已有结果并换一种方法。也可编辑提示说明缺少的条件。"
        )
    lead = (
        f"Stopped repeating actions: {what}. "
        if what
        else "Stopped repeating actions. The repetition context is recorded. "
    )
    return (
        lead + "Continue to ask the model to use existing results and choose a "
        "different approach, or edit the prompt."
    )


def no_progress_step(
    detail: Mapping[str, Any] | None, language: str = "en"
) -> dict[str, Any] | None:
    """One step card for the calls that repeated, or ``None`` with no tool.

    Thirty-three native tools draw no card of their own (``query`` among
    them), so a stop for repeating one left nothing in the conversation to
    point at. This is that card: the tool, how many times, and why it counted.
    """
    if not detail:
        return None
    tools = [str(name) for name in detail.get("tools") or ()]
    count = int(detail.get("count") or 0)
    if not tools or count < 2:
        return None
    zh = language == "zh"
    names = ", ".join(tools)
    summary = {
        "same_action": "参数相同，结果相同" if zh else "same arguments, same result",
        "same_target": (
            "同一个文件，只换了范围或内容"
            if zh
            else "same file, different window or contents"
        ),
        "similar_tool_error": "同一个错误" if zh else "the same error",
        "consecutive_malformed": "参数格式无效" if zh else "invalid arguments",
    }.get(str(detail.get("reason") or ""), "")
    step_input: dict[str, Any] = {
        "tools": tools,
        "count": count,
        "reason": str(detail.get("reason") or ""),
    }
    if detail.get("target"):
        step_input["target"] = str(detail["target"])
    return {
        "kind": "loop",
        "title": (
            f"重复调用 {names} × {count}" if zh else f"Repeated {names} × {count}"
        ),
        "input": step_input,
        "summary": summary,
    }
