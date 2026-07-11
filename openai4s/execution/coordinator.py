"""Observable FIFO admission for session-scoped scientific execution.

The coordinator deliberately sits above kernels and below HTTP/WebSocket
adapters.  It does not execute code or deliver a process signal.  Instead it
owns admission, exposes an exact-ticket cancellation signal, and guarantees
that one session has at most one scientific writer at a time.

Callers that already use a lock-style turn barrier can migrate incrementally::

    with coordinator.execution(
        root_frame_id,
        owner="agent",
        owner_id=job_id,
        language="python",
    ) as ticket:
        run_turn(cancelled=ticket.cancellation.is_set)

An interrupt endpoint must supply all of ``session_id``, ``execution_id`` and
the expected owner.  There is intentionally no unscoped "interrupt current"
operation here: signal delivery remains the caller's job and should use the
exact kernel lease associated with the admitted ticket.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterator, Mapping, Sequence


class TicketState(str, Enum):
    """Lifecycle states exposed to persistence and UI projections."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = frozenset(
    {TicketState.COMPLETED, TicketState.FAILED, TicketState.CANCELLED}
)


class CoordinatorError(RuntimeError):
    """Base error for admission and ticket-lifecycle failures."""


class CoordinatorClosed(CoordinatorError):
    """Raised when admission is attempted on a closed coordinator/session."""


class ExecutionCancelled(CoordinatorError):
    """Raised when a queued ticket is cancelled before it can run."""


class TicketStateError(CoordinatorError):
    """Raised when an operation does not target the exact active ticket."""


@dataclass(frozen=True)
class ExecutionOwner:
    """Stable identity of the component that owns an execution ticket.

    ``kind`` is a UI/policy category such as ``agent``, ``user_repl``,
    ``recovery``, ``lifecycle`` or ``system``.  ``owner_id`` distinguishes
    concurrent jobs of the same kind.
    """

    kind: str
    owner_id: str

    def __post_init__(self) -> None:
        if not str(self.kind).strip():
            raise ValueError("execution owner kind must not be empty")
        if not str(self.owner_id).strip():
            raise ValueError("execution owner id must not be empty")

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "id": self.owner_id}


class CancellationSignal:
    """Read-only cancellation token shared with an admitted execution.

    Only :class:`SessionExecutionCoordinator` can request cancellation.  Code
    running a cell receives the signal's ``is_set``/``wait`` methods but cannot
    accidentally cancel an unrelated ticket by mutating a session-global
    event.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._reason: str | None = None

    def is_set(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    @property
    def reason(self) -> str | None:
        with self._lock:
            return self._reason

    def _request(self, reason: str) -> bool:
        with self._lock:
            if self._event.is_set():
                return False
            self._reason = reason
            self._event.set()
            return True


class ExecutionTicket:
    """One immutable execution identity with coordinator-owned state."""

    def __init__(
        self,
        *,
        coordinator_token: object,
        execution_id: str,
        session_id: str,
        owner: ExecutionOwner,
        queued_at: float,
        branch_id: str | None,
        language: str | None,
        generation_id: str | int | None,
        resource_keys: Sequence[str],
        metadata: Mapping[str, Any] | None,
    ) -> None:
        self._coordinator_token = coordinator_token
        self.execution_id = execution_id
        self.session_id = session_id
        # ``root_frame_id`` is the proposal's name for the session key.  Keep
        # both spellings so future Gateway integration is explicit.
        self.root_frame_id = session_id
        self.branch_id = branch_id
        self.owner = owner
        self.language = language
        self.generation_id = generation_id
        self.resource_keys = tuple(resource_keys)
        self.metadata = dict(metadata or {})
        self.queued_at = queued_at
        self.cancellation = CancellationSignal()

        self._lock = threading.Lock()
        self._state = TicketState.QUEUED
        self._started_at: float | None = None
        self._finished_at: float | None = None
        self._error: str | None = None
        self._admission_resolved = threading.Event()
        self._finished = threading.Event()

    @property
    def state(self) -> TicketState:
        with self._lock:
            return self._state

    @property
    def status(self) -> str:
        return self.state.value

    @property
    def started_at(self) -> float | None:
        with self._lock:
            return self._started_at

    @property
    def finished_at(self) -> float | None:
        with self._lock:
            return self._finished_at

    @property
    def error(self) -> str | None:
        with self._lock:
            return self._error

    @property
    def cancelled(self) -> bool:
        return self.cancellation.is_set() or self.state is TicketState.CANCELLED

    def wait_finished(self, timeout: float | None = None) -> bool:
        """Block until terminal state; return ``False`` on timeout."""

        return self._finished.wait(timeout)

    def snapshot(self, *, queue_position: int | None = None) -> dict[str, Any]:
        """Return a JSON-serializable, internally consistent ticket view."""

        with self._lock:
            payload: dict[str, Any] = {
                "execution_id": self.execution_id,
                "session_id": self.session_id,
                "root_frame_id": self.root_frame_id,
                "branch_id": self.branch_id,
                "owner": self.owner.as_dict(),
                "language": self.language,
                "generation_id": self.generation_id,
                "resource_keys": list(self.resource_keys),
                "status": self._state.value,
                "queued_at": self.queued_at,
                "started_at": self._started_at,
                "finished_at": self._finished_at,
                "cancel_requested": self.cancellation.is_set(),
                "cancel_reason": self.cancellation.reason,
                "error": self._error,
            }
        if queue_position is not None:
            payload["queue_position"] = queue_position
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload

    def _transition(
        self,
        state: TicketState,
        *,
        at: float,
        error: str | None = None,
    ) -> None:
        with self._lock:
            self._state = state
            if state is TicketState.RUNNING:
                self._started_at = at
                self._admission_resolved.set()
            elif state in TERMINAL_STATES:
                self._finished_at = at
                self._error = error
                # A terminal queued ticket must wake both admission waiters and
                # clients waiting on the final result.
                self._admission_resolved.set()
                self._finished.set()


@dataclass
class _SessionQueue:
    active: ExecutionTicket | None = None
    queued: deque[ExecutionTicket] = field(default_factory=deque)
    execution_ids: set[str] = field(default_factory=set)
    closed: bool = False
    close_reason: str | None = None
    remove_when_idle: bool = False


EventSink = Callable[[dict[str, Any]], None]
Clock = Callable[[], float]
IdFactory = Callable[[], str]


class SessionExecutionCoordinator:
    """Serialize scientific writers independently for every session.

    All state changes are protected by one short-held condition lock.  User
    work and event delivery run outside it, so an active execution in one
    session never blocks admission or completion in another session.
    """

    def __init__(
        self,
        *,
        event_sink: EventSink | None = None,
        clock: Clock = time.time,
        id_factory: IdFactory | None = None,
    ) -> None:
        self._event_sink = event_sink
        self._clock = clock
        self._id_factory = id_factory or (lambda: f"exec-{uuid.uuid4().hex}")
        self._condition = threading.Condition(threading.RLock())
        self._sessions: dict[str, _SessionQueue] = {}
        self._coordinator_token = object()
        self._closed = False
        self._close_reason: str | None = None

    def submit(
        self,
        session_id: str,
        *,
        owner: ExecutionOwner | str,
        owner_id: str | None = None,
        execution_id: str | None = None,
        branch_id: str | None = None,
        language: str | None = None,
        generation_id: str | int | None = None,
        resource_keys: Sequence[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> ExecutionTicket:
        """Queue a ticket and immediately admit it when the session is idle."""

        session_id = _required("session_id", session_id)
        identity = _coerce_owner(owner, owner_id)
        ticket_id = _required("execution_id", execution_id or self._id_factory())
        events: list[dict[str, Any]] = []
        with self._condition:
            if self._closed:
                raise CoordinatorClosed(self._close_reason or "coordinator is closed")
            session = self._sessions.setdefault(session_id, _SessionQueue())
            if session.closed:
                raise CoordinatorClosed(
                    session.close_reason or f"session {session_id!r} is closed"
                )
            if ticket_id in session.execution_ids:
                raise ValueError(
                    f"execution id {ticket_id!r} already exists in session "
                    f"{session_id!r}"
                )
            ticket = ExecutionTicket(
                coordinator_token=self._coordinator_token,
                execution_id=ticket_id,
                session_id=session_id,
                owner=identity,
                queued_at=self._clock(),
                branch_id=branch_id,
                language=language,
                generation_id=generation_id,
                resource_keys=resource_keys,
                metadata=metadata,
            )
            session.execution_ids.add(ticket_id)
            session.queued.append(ticket)
            events.append(self._state_event(ticket, queue_position=len(session.queued)))
            self._promote_locked(session_id, session, events)
            events.append(self._queue_event_locked(session_id, session))
            self._condition.notify_all()
        self._emit(events)
        return ticket

    def wait_until_running(
        self, ticket: ExecutionTicket, timeout: float | None = None
    ) -> ExecutionTicket:
        """Wait without polling until a ticket runs or reaches terminal state.

        A timeout atomically cancels a still-queued ticket.  Doing this while
        holding the admission condition prevents a timeout/promotion race from
        reserving the session for a caller that has already stopped waiting.
        """

        self._validate_ticket(ticket)
        events: list[dict[str, Any]] = []
        with self._condition:
            ready = self._condition.wait_for(
                lambda: ticket.state is not TicketState.QUEUED,
                timeout=timeout,
            )
            if not ready:
                session = self._sessions.get(ticket.session_id)
                assert session is not None
                # The condition is held, so a still-queued ticket cannot be
                # promoted between this check and its removal.
                if ticket in session.queued:
                    session.queued.remove(ticket)
                    reason = "admission timed out"
                    ticket.cancellation._request(reason)
                    ticket._transition(TicketState.CANCELLED, at=self._clock())
                    events.append(self._state_event(ticket, reason=reason))
                    self._promote_locked(ticket.session_id, session, events)
                    events.append(
                        self._queue_event_locked(ticket.session_id, session)
                    )
                    self._condition.notify_all()
                timeout_error = TimeoutError(
                    f"timed out waiting for execution {ticket.execution_id!r}"
                )
            else:
                timeout_error = None
            state = ticket.state
        self._emit(events)
        if timeout_error is not None:
            raise timeout_error
        if state is TicketState.RUNNING:
            return ticket
        if state is TicketState.CANCELLED:
            raise ExecutionCancelled(
                ticket.cancellation.reason
                or f"execution {ticket.execution_id!r} was cancelled"
            )
        raise TicketStateError(
            f"execution {ticket.execution_id!r} reached {state.value} before "
            "admission"
        )

    @contextmanager
    def execution(
        self,
        session_id: str,
        *,
        owner: ExecutionOwner | str,
        owner_id: str | None = None,
        execution_id: str | None = None,
        branch_id: str | None = None,
        language: str | None = None,
        generation_id: str | int | None = None,
        resource_keys: Sequence[str] = (),
        metadata: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Iterator[ExecutionTicket]:
        """Queue, await, and always release one execution ticket.

        Normal exit marks the ticket completed.  Any ``BaseException`` marks it
        failed and is re-raised.  If an exact interrupt/close requested
        cancellation, cancellation wins over either outcome.  This provides the
        exception-safe release guarantee expected from the old lock barrier.
        """

        ticket = self.submit(
            session_id,
            owner=owner,
            owner_id=owner_id,
            execution_id=execution_id,
            branch_id=branch_id,
            language=language,
            generation_id=generation_id,
            resource_keys=resource_keys,
            metadata=metadata,
        )
        self.wait_until_running(ticket, timeout=timeout)

        try:
            yield ticket
        except BaseException as error:
            self.fail(ticket, error)
            raise
        else:
            self.complete(ticket)

    def complete(self, ticket: ExecutionTicket) -> bool:
        """Finish the exact active ticket and admit the next queued writer."""

        return self._finish(ticket, error=None)

    def fail(self, ticket: ExecutionTicket, error: BaseException | str) -> bool:
        """Fail the exact active ticket, releasing ownership even on errors."""

        detail = str(error) if isinstance(error, str) else _error_text(error)
        return self._finish(ticket, error=detail)

    def cancel_queued(
        self,
        *,
        session_id: str,
        execution_id: str,
        owner: ExecutionOwner | str,
        owner_id: str | None = None,
        reason: str = "cancelled by owner",
    ) -> bool:
        """Cancel only the matching owner's still-queued ticket.

        Returning ``False`` means the ticket was absent, already admitted, or
        owned by somebody else.  No active execution is touched.
        """

        identity = _coerce_owner(owner, owner_id)
        events: list[dict[str, Any]] = []
        with self._condition:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            ticket = next(
                (
                    item
                    for item in session.queued
                    if item.execution_id == execution_id and item.owner == identity
                ),
                None,
            )
            if ticket is None:
                return False
            session.queued.remove(ticket)
            ticket.cancellation._request(reason)
            ticket._transition(TicketState.CANCELLED, at=self._clock())
            events.append(self._state_event(ticket, reason=reason))
            self._promote_locked(session_id, session, events)
            events.append(self._queue_event_locked(session_id, session))
            self._condition.notify_all()
        self._emit(events)
        return True

    def request_interrupt(
        self,
        *,
        session_id: str,
        execution_id: str,
        owner: ExecutionOwner | str,
        owner_id: str | None = None,
        reason: str = "interrupt requested by owner",
    ) -> bool:
        """Signal cancellation only for an exact active owner/ticket pair."""

        identity = _coerce_owner(owner, owner_id)
        events: list[dict[str, Any]] = []
        with self._condition:
            session = self._sessions.get(session_id)
            ticket = session.active if session is not None else None
            if (
                ticket is None
                or ticket.execution_id != execution_id
                or ticket.owner != identity
            ):
                return False
            changed = ticket.cancellation._request(reason)
            if changed:
                events.append(
                    {
                        "type": "execution_interrupt_requested",
                        "session_id": session_id,
                        "root_frame_id": session_id,
                        "execution_id": ticket.execution_id,
                        "owner": ticket.owner.as_dict(),
                        "status": ticket.status,
                        "reason": reason,
                        "at": self._clock(),
                    }
                )
                events.append(self._queue_event_locked(session_id, session))
                self._condition.notify_all()
        self._emit(events)
        return changed

    def snapshot(self, session_id: str) -> dict[str, Any]:
        """Return owner and current FIFO positions for one session."""

        with self._condition:
            session = self._sessions.get(session_id)
            if session is None:
                return self._empty_snapshot(session_id)
            return self._snapshot_locked(session_id, session)

    def snapshots(self) -> dict[str, dict[str, Any]]:
        """Return consistent snapshots of all currently retained sessions."""

        with self._condition:
            return {
                session_id: self._snapshot_locked(session_id, session)
                for session_id, session in self._sessions.items()
            }

    def close_session(
        self, session_id: str, *, reason: str = "session closed"
    ) -> bool:
        """Reject new work, cancel waiters, and signal the exact active owner."""

        return self._close_session(session_id, reason=reason, remove_when_idle=False)

    def cleanup_session(
        self, session_id: str, *, reason: str = "session cleaned up"
    ) -> bool:
        """Close a session and drop its queue after the active owner releases.

        Once removed, a future request may recreate the same session id.  This
        is suitable for idle-TTL cleanup where later access starts a fresh
        runtime.  Existing ticket objects keep their terminal events/state.
        """

        return self._close_session(session_id, reason=reason, remove_when_idle=True)

    def close(self, *, reason: str = "coordinator closed") -> None:
        """Wake every waiter and signal every active ticket exactly once."""

        events: list[dict[str, Any]] = []
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._close_reason = reason
            for session_id, session in self._sessions.items():
                self._close_session_locked(session_id, session, reason, events)
            self._condition.notify_all()
        self._emit(events)

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    def _finish(self, ticket: ExecutionTicket, *, error: str | None) -> bool:
        self._validate_ticket(ticket)
        events: list[dict[str, Any]] = []
        with self._condition:
            session = self._sessions.get(ticket.session_id)
            if session is None or session.active is not ticket:
                if ticket.state in TERMINAL_STATES:
                    return False
                raise TicketStateError(
                    f"execution {ticket.execution_id!r} is not the exact active owner"
                )
            previous = ticket
            if ticket.cancellation.is_set():
                state = TicketState.CANCELLED
                reason = ticket.cancellation.reason or "execution cancelled"
                terminal_error = None
            elif error is None:
                state = TicketState.COMPLETED
                reason = None
                terminal_error = None
            else:
                state = TicketState.FAILED
                reason = None
                terminal_error = error
            ticket._transition(state, at=self._clock(), error=terminal_error)
            session.active = None
            events.append(
                self._state_event(ticket, reason=reason, error=terminal_error)
            )
            events.append(
                self._owner_event(
                    ticket.session_id,
                    previous=previous,
                    current=None,
                    reason=state.value,
                )
            )
            if not session.closed:
                self._promote_locked(ticket.session_id, session, events)
            events.append(self._queue_event_locked(ticket.session_id, session))
            if session.remove_when_idle and session.active is None:
                if not session.queued:
                    self._sessions.pop(ticket.session_id, None)
            self._condition.notify_all()
        self._emit(events)
        return True

    def _close_session(
        self, session_id: str, *, reason: str, remove_when_idle: bool
    ) -> bool:
        events: list[dict[str, Any]] = []
        with self._condition:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            if session.closed:
                if remove_when_idle:
                    session.remove_when_idle = True
                    self._remove_idle_session_locked(session_id, session)
                return False
            session.remove_when_idle = remove_when_idle
            self._close_session_locked(session_id, session, reason, events)
            self._remove_idle_session_locked(session_id, session)
            self._condition.notify_all()
        self._emit(events)
        return True

    def _close_session_locked(
        self,
        session_id: str,
        session: _SessionQueue,
        reason: str,
        events: list[dict[str, Any]],
    ) -> None:
        session.closed = True
        session.close_reason = reason
        while session.queued:
            ticket = session.queued.popleft()
            ticket.cancellation._request(reason)
            ticket._transition(TicketState.CANCELLED, at=self._clock())
            events.append(self._state_event(ticket, reason=reason))
        active = session.active
        if active is not None and active.cancellation._request(reason):
            events.append(
                {
                    "type": "execution_interrupt_requested",
                    "session_id": session_id,
                    "root_frame_id": session_id,
                    "execution_id": active.execution_id,
                    "owner": active.owner.as_dict(),
                    "status": active.status,
                    "reason": reason,
                    "at": self._clock(),
                }
            )
        events.append(
            {
                "type": "execution_session_closed",
                "session_id": session_id,
                "root_frame_id": session_id,
                "reason": reason,
                "at": self._clock(),
            }
        )
        events.append(self._queue_event_locked(session_id, session))

    def _promote_locked(
        self,
        session_id: str,
        session: _SessionQueue,
        events: list[dict[str, Any]],
    ) -> None:
        if session.closed or session.active is not None:
            return
        if not session.queued:
            return
        ticket = session.queued.popleft()
        ticket._transition(TicketState.RUNNING, at=self._clock())
        session.active = ticket
        events.append(self._state_event(ticket, queue_position=0))
        events.append(
            self._owner_event(
                session_id,
                previous=None,
                current=ticket,
                reason="admitted",
            )
        )

    def _remove_idle_session_locked(
        self, session_id: str, session: _SessionQueue
    ) -> None:
        if not session.remove_when_idle or session.active is not None:
            return
        if not session.queued:
            self._sessions.pop(session_id, None)

    def _snapshot_locked(
        self, session_id: str, session: _SessionQueue
    ) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "root_frame_id": session_id,
            "closed": session.closed,
            "close_reason": session.close_reason,
            "owner": (
                session.active.snapshot(queue_position=0)
                if session.active is not None
                else None
            ),
            "queue": [
                ticket.snapshot(queue_position=position)
                for position, ticket in enumerate(session.queued, start=1)
            ],
            "queued_count": len(session.queued),
            "active_count": 1 if session.active is not None else 0,
        }

    def _empty_snapshot(self, session_id: str) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "root_frame_id": session_id,
            "closed": self._closed,
            "close_reason": self._close_reason if self._closed else None,
            "owner": None,
            "queue": [],
            "queued_count": 0,
            "active_count": 0,
        }

    def _queue_event_locked(
        self, session_id: str, session: _SessionQueue
    ) -> dict[str, Any]:
        return {
            "type": "execution_queue_changed",
            "at": self._clock(),
            **self._snapshot_locked(session_id, session),
        }

    def _state_event(
        self,
        ticket: ExecutionTicket,
        *,
        queue_position: int | None = None,
        reason: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        event = {
            "type": "execution_ticket_state",
            "at": self._clock(),
            **ticket.snapshot(queue_position=queue_position),
        }
        if reason is not None:
            event["reason"] = reason
        if error is not None:
            event["error"] = error
        return event

    def _owner_event(
        self,
        session_id: str,
        *,
        previous: ExecutionTicket | None,
        current: ExecutionTicket | None,
        reason: str,
    ) -> dict[str, Any]:
        return {
            "type": "execution_owner_changed",
            "session_id": session_id,
            "root_frame_id": session_id,
            "previous_execution_id": (
                previous.execution_id if previous is not None else None
            ),
            "previous_owner": previous.owner.as_dict() if previous else None,
            "execution_id": current.execution_id if current is not None else None,
            "owner": current.owner.as_dict() if current is not None else None,
            "reason": reason,
            "at": self._clock(),
        }

    def _validate_ticket(self, ticket: ExecutionTicket) -> None:
        if not isinstance(ticket, ExecutionTicket):
            raise TypeError("expected an ExecutionTicket")
        if ticket._coordinator_token is not self._coordinator_token:
            raise TicketStateError("ticket belongs to a different coordinator")

    def _emit(self, events: Sequence[dict[str, Any]]) -> None:
        sink = self._event_sink
        if sink is None:
            return
        for event in events:
            try:
                sink(event)
            except Exception:
                # Telemetry/UI projection must never strand the current owner or
                # prevent the next FIFO ticket from being admitted.
                continue


def _coerce_owner(
    owner: ExecutionOwner | str, owner_id: str | None
) -> ExecutionOwner:
    if isinstance(owner, ExecutionOwner):
        if owner_id is not None and owner_id != owner.owner_id:
            raise ValueError("owner_id conflicts with ExecutionOwner.owner_id")
        return owner
    kind = _required("owner", owner)
    # Treat a bare string as both kind and identity for simple lifecycle/system
    # callers.  Concurrent jobs should pass their job/frame id explicitly.
    return ExecutionOwner(kind=kind, owner_id=_required("owner_id", owner_id or kind))


def _required(name: str, value: object) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise ValueError(f"{name} must not be empty")
    return text


def _error_text(error: BaseException) -> str:
    message = str(error).strip()
    return type(error).__name__ + (f": {message}" if message else "")


__all__ = [
    "CancellationSignal",
    "CoordinatorClosed",
    "CoordinatorError",
    "ExecutionCancelled",
    "ExecutionOwner",
    "ExecutionTicket",
    "SessionExecutionCoordinator",
    "TicketState",
    "TicketStateError",
]
