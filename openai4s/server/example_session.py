"""How the built-in example session ends.

The example is seeded by running cells through the notebook REPL, not by a
turn, so nothing closed it the way a turn is closed: its summary went to the
store only (a page that was watching never received it) and the session was
recorded ``done`` whatever had happened in it.
"""

from __future__ import annotations

from typing import Any, Callable


def failed_steps(store: Any, frame_id: str) -> list[str]:
    """One line per step that failed, including one its cell recovered from.

    A cell that catches a failed fetch and carries on finishes without an
    error, so the cell results alone cannot say that a data source was
    unreachable. The step record can.
    """

    lines: list[str] = []
    for step in store.list_steps(frame_id):
        if step.get("status") != "error":
            continue
        title = " ".join(str(step.get("title") or "step").split())
        reason = " ".join(str(step.get("summary") or "").split())
        reason = reason.removeprefix("failed:").removeprefix("failed").strip()
        lines.append(f"{title}: {reason[:200]}" if reason else title)
    return lines


def failed_steps_note(steps: list[str]) -> str:
    """The paragraph that names those steps in the summary, or ``""``."""

    if not steps:
        return ""
    count = f"{len(steps)} step" + ("" if len(steps) == 1 else "s")
    return (
        f"{count} failed along the way, so what depended on "
        + ("it" if len(steps) == 1 else "them")
        + " is missing below:\n"
        + "".join(f"- {line}\n" for line in steps)
    )


def finish(
    *,
    store: Any,
    emit: Callable[[dict], None],
    frame_id: str,
    summary: str,
    failed_cells: int,
) -> str:
    """Publish the summary and close the session the way a turn does.

    The same two events a turn ends with -- the final text as a
    ``text_chunk``, then the terminal ``frame_update`` -- so an open page
    shows the summary without a reload. Returns the terminal status.
    """

    status = "failed" if failed_cells else "completed"
    emit(
        {
            "type": "text_chunk",
            "frame_id": frame_id,
            "block_type": "text",
            "chunk": summary + "\n",
        }
    )
    store.add_message(
        root_frame_id=frame_id, role="assistant", frame_id=frame_id, content=summary
    )
    store.update_frame(frame_id, status="failed" if failed_cells else "done")
    emit({"type": "frame_update", "frame_id": frame_id, "status": status})
    return status


__all__ = ["failed_steps", "failed_steps_note", "finish"]
