"""The compute job state machine: one vocabulary, one transition table.

Before this module the vocabulary was split across three places that disagreed
with each other, and nothing enforced any of them:

  * ``LIVE_STATES`` listed ``queued`` and ``submitted``, which nothing ever
    wrote, and included ``staging``;
  * ``ComputeManager._live_count`` counted ``queued``/``submitted``/``running``
    and *omitted* ``staging`` — so a row left at ``staging`` by a crash between
    claiming it and submitting it was rehydrated on every restart, reported by
    ``reconcile()`` forever, and never occupied the slot that would have made
    anyone notice;
  * the SDK treated ``timeout`` and ``harvesting`` as live, neither of which
    the manager produces.

The repository's ``update()`` accepted any string at all, so none of this was
detectable — a typo became a state.

The vocabulary here is the one the improvement proposal specifies:

    queued -> staging -> submitted -> running -> succeeded | failed
                                              | timed_out | cancelled
                                              | unknown

Two historical states are folded in rather than dropped, because both carried
information worth keeping:

  * ``incomplete`` meant the job exited 0 but its outputs could not be
    verified. That is not a success — it becomes ``failed`` with a
    ``termination_reason`` recording why.
  * ``closed`` meant the user released the handle while the job was live. That
    is a cancellation with a particular cause, so it becomes ``cancelled``
    with its own reason.

``unknown`` is not a failure state and must never be treated as one. It means
the remote operation may or may not have taken effect, which is precisely the
case that needs reconciling rather than retrying.
"""
from __future__ import annotations

# --- the vocabulary -------------------------------------------------------

QUEUED = "queued"
STAGING = "staging"
SUBMITTED = "submitted"
RUNNING = "running"

SUCCEEDED = "succeeded"
FAILED = "failed"
TIMED_OUT = "timed_out"
CANCELLED = "cancelled"
UNKNOWN = "unknown"

#: A job in one of these may still be consuming a remote resource, so it holds
#: a concurrency slot and is rehydrated after a restart. ``unknown`` is live on
#: purpose: something may be running out there, and forgetting it is how a
#: sandbox bills unnoticed.
LIVE_STATES: tuple[str, ...] = (QUEUED, STAGING, SUBMITTED, RUNNING, UNKNOWN)

#: Mutually exclusive end states. The scorecard's requirement is exactly this:
#: no job is in two of them, and none of them is reachable by default.
TERMINAL_STATES: tuple[str, ...] = (SUCCEEDED, FAILED, TIMED_OUT, CANCELLED)

ALL_STATES: tuple[str, ...] = (*LIVE_STATES, *TERMINAL_STATES)

# --- termination reasons --------------------------------------------------

#: Why a job ended, when the status alone loses something worth keeping.
REASON_OUTPUTS_UNVERIFIED = "outputs_unverified"
REASON_HANDLE_CLOSED = "handle_closed"
REASON_SUBMIT_REJECTED = "submit_rejected"
REASON_SUBMIT_INDETERMINATE = "submit_indeterminate"
REASON_DEADLINE = "deadline_exceeded"
REASON_USER_CANCELLED = "user_cancelled"

# --- the transition table -------------------------------------------------

_TRANSITIONS: dict[str, frozenset[str]] = {
    QUEUED: frozenset({STAGING, CANCELLED, FAILED, UNKNOWN}),
    # A claimed row goes straight to `unknown` when we cannot tell whether the
    # submit landed — that edge is the whole reason the row is written first.
    # ``succeeded``/``timed_out`` are permitted directly too: the persistence
    # of the intermediate ``running`` state is deliberately best-effort, so a
    # submit that landed and finished can have its result verify a terminal
    # state while the durable row is still ``staging``. Forbidding those edges
    # made ``_commit_terminal`` read the verified result as a conflict and left
    # the row — and every later result — stuck at ``staging``.
    STAGING: frozenset(
        {SUBMITTED, RUNNING, SUCCEEDED, FAILED, TIMED_OUT, CANCELLED, UNKNOWN}
    ),
    SUBMITTED: frozenset({RUNNING, SUCCEEDED, FAILED, TIMED_OUT, CANCELLED, UNKNOWN}),
    RUNNING: frozenset({SUCCEEDED, FAILED, TIMED_OUT, CANCELLED, UNKNOWN}),
    # `unknown` is reconcilable, not terminal: a later probe may resolve it
    # either way, and closing a handle over it is a cancellation.
    UNKNOWN: frozenset({RUNNING, SUCCEEDED, FAILED, TIMED_OUT, CANCELLED}),
    # Terminal states are terminal. A job that has already ended cannot be
    # re-opened by a late probe, which is what stops a stale reply from
    # resurrecting work nobody is tracking any more.
    SUCCEEDED: frozenset(),
    FAILED: frozenset(),
    TIMED_OUT: frozenset(),
    CANCELLED: frozenset(),
}


class IllegalTransition(ValueError):
    """A status write that the state machine does not allow."""

    def __init__(self, job_id: str, current: str, requested: str) -> None:
        self.job_id = job_id
        self.current = current
        self.requested = requested
        super().__init__(
            f"compute job {job_id!r} cannot move {current!r} -> {requested!r}; "
            f"legal next states are "
            f"{sorted(_TRANSITIONS.get(current, frozenset())) or 'none (terminal)'}"
        )


def is_live(status: str | None) -> bool:
    return status in LIVE_STATES


def is_terminal(status: str | None) -> bool:
    return status in TERMINAL_STATES


def can_transition(current: str | None, requested: str) -> bool:
    """True when ``requested`` is reachable from ``current``.

    A row with no current status is being created, so any state is reachable.
    Re-writing a *live* state a job is already in is allowed: probes are
    naturally repeated, and treating a no-op as a violation would make callers
    guess.

    Re-writing a *terminal* state is not, and the difference is not pedantry.
    A status write carries the evidence that established it — the artifact
    manifest, its digest, the reason and the terminal timestamp — so
    ``succeeded -> succeeded`` is not a no-op at all. Two pollers that both
    read ``running`` could each reach the write: the first commits its
    manifest, and the second, arriving after the remote directory had been
    reused, committed *its* bytes over the row that the first one's caller had
    already been told about. The compare-and-swap could not see it, because it
    re-reads the current status and same-state was legal.

    So a terminal state is written exactly once. Every later terminal write
    loses the compare-and-swap and gets the stored row back instead, which is
    the answer the caller has to report.
    """
    if current is None:
        return requested in ALL_STATES
    if is_terminal(current):
        # Terminal evidence is immutable — including against a write of the
        # same status. ``_TRANSITIONS`` already refuses every *other* target.
        return False
    if current == requested:
        return True
    return requested in _TRANSITIONS.get(current, frozenset())


def check_transition(job_id: str, current: str | None, requested: str) -> None:
    """Raise ``IllegalTransition`` unless the move is legal."""
    if requested not in ALL_STATES:
        raise IllegalTransition(job_id, current or "<new>", requested)
    if not can_transition(current, requested):
        raise IllegalTransition(job_id, current or "<new>", requested)


#: How historical rows map onto the vocabulary above. Applied by the numbered
#: migration, and kept here so the migration and the runtime cannot drift.
LEGACY_STATUS_MAP: dict[str, tuple[str, str | None]] = {
    "done": (SUCCEEDED, None),
    "incomplete": (FAILED, REASON_OUTPUTS_UNVERIFIED),
    "closed": (CANCELLED, REASON_HANDLE_CLOSED),
}


__all__ = [
    "ALL_STATES",
    "CANCELLED",
    "FAILED",
    "IllegalTransition",
    "LEGACY_STATUS_MAP",
    "LIVE_STATES",
    "QUEUED",
    "REASON_DEADLINE",
    "REASON_HANDLE_CLOSED",
    "REASON_OUTPUTS_UNVERIFIED",
    "REASON_SUBMIT_INDETERMINATE",
    "REASON_SUBMIT_REJECTED",
    "REASON_USER_CANCELLED",
    "RUNNING",
    "STAGING",
    "SUBMITTED",
    "SUCCEEDED",
    "TERMINAL_STATES",
    "TIMED_OUT",
    "UNKNOWN",
    "can_transition",
    "check_transition",
    "is_live",
    "is_terminal",
]
