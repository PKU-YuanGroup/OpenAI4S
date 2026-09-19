"""Model context for explicitly continuing a stopped turn; never replay work."""

from __future__ import annotations

import re
from typing import Iterable


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
            "similar_tool_error": "tool calls repeatedly produced the same error",
            "consecutive_malformed": "consecutive tool calls had invalid arguments",
            "long_text_repeat": "the same analysis was repeated without an action",
        }.get(progress_reason or "", "repeated actions made no progress")
        names = list(
            dict.fromkeys(
                name
                for name in tool_names
                if isinstance(name, str)
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", name)
            )
        )[:8]
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
    else:
        return None
    return {"role": "system", "content": "[Stopped turn recovery]\n" + detail}
