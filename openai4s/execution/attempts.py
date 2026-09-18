"""Terminal states for durable scientific Cell execution attempts.

Both attempt writers — the CLI ``LocalActionExecutor`` and the Web
``CellExecutionService`` — classify through these two functions, so the
vocabulary cannot drift between them again.

A state is decided from structure, never from the text of an error. A worker's
``error`` is the whole traceback of user code: a frame that passes
``timeout=timeout``, a ``NameError`` about a variable called ``timeout`` or a
urllib connection error all contain the word, and none of them is a timeout.
Only the Host can prove one, and it does so by raising a ``TimeoutError`` (the
watchdog's reset/not-reset/reset-unavailable ladder) instead of returning a
result.
"""

from __future__ import annotations

from typing import Any, Mapping

from openai4s.execution.watchdog import KernelCancellation


def attempt_state_for_result(result: Any) -> str:
    """The terminal state of a Cell whose worker returned ``result``.

    ``interrupted`` wins over an error; any other non-empty error is an
    ordinary ``failed`` Cell, even when user code raised ``TimeoutError``
    itself; otherwise the Cell ``completed``. A missing or malformed result
    is ``failed``.
    """

    if not isinstance(result, Mapping):
        return "failed"
    if result.get("interrupted"):
        return "interrupted"
    return "failed" if str(result.get("error") or "") else "completed"


def attempt_state_for_exception(exc: BaseException, *, otherwise: str) -> str:
    """The terminal state of a Cell whose Host-side execution raised ``exc``.

    A watchdog cancellation is ``cancelled`` and a watchdog timeout is
    ``timed_out``; every other exception is the caller's ``otherwise`` (the
    Web path's ``worker_died``, the CLI's ``failed``). The type is the proof —
    the exception's message is never read.
    """

    if isinstance(exc, KernelCancellation):
        return "cancelled"
    if isinstance(exc, TimeoutError):
        return "timed_out"
    return otherwise


__all__ = ["attempt_state_for_exception", "attempt_state_for_result"]
