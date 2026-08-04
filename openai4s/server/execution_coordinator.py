"""Web adapter for session-scoped FIFO execution ownership.

The protocol-neutral coordinator owns admission and ticket state.  This module
adds the small amount of Web-runtime policy that does not belong in Gateway:

* project ticket state onto WebSocket-friendly events;
* bind one admitted ticket to its cancellation event and current kernel lease;
* interrupt only the exact lease owned by the exact execution id; and
* expose a ``finalizing`` projection between scientific work and completion.

It deliberately does not import Gateway, kernels, or the HTTP server.  The
caller supplies the event and interrupt ports so the admission algorithm stays
directly testable.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Mapping

from openai4s.execution import (
    ExecutionCancelled,
    ExecutionOwner,
    ExecutionTicket,
    SessionExecutionCoordinator,
    TicketState,
)

EmitFor = Callable[[str, dict[str, Any]], None]
InterruptLease = Callable[[Any], bool]


@dataclass
class _Binding:
    ticket: ExecutionTicket
    cancel_event: threading.Event
    lease: Any = None
    interrupt_lease: InterruptLease | None = None
    #: Set by `mark_failed` while the turn still holds the lease. Recorded
    #: rather than acted on: failing the ticket at that moment releases the
    #: lease and promotes the next turn *before* this one has written its
    #: durable status, its prose and its terminal event.
    failure_reason: str | None = None


#: What a ticket records when its worker never started. Fixed text, so no
#: cleanup path has to render an exception it did not author.
_UNSTARTED_REASON = "worker thread was never started"


class WebExecutionCoordinator:
    """Compose FIFO admission with Web events and exact runtime cancellation."""

    def __init__(self, emit_for: EmitFor, *, clock=None, id_factory=None) -> None:
        self._emit_for = emit_for
        self._clock = clock or time.time
        kwargs: dict[str, Any] = {
            "event_sink": self._on_core_event,
            "clock": self._clock,
        }
        if id_factory is not None:
            kwargs["id_factory"] = id_factory
        self._coordinator = SessionExecutionCoordinator(**kwargs)
        self._lock = threading.RLock()
        self._tickets: dict[str, ExecutionTicket] = {}
        self._bindings: dict[str, _Binding] = {}
        self._positions: dict[str, int] = {}
        self._local = threading.local()

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
        resource_keys=(),
        metadata: Mapping[str, Any] | None = None,
    ) -> ExecutionTicket:
        ticket = self._coordinator.submit(
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
        with self._lock:
            self._tickets[ticket.execution_id] = ticket
        return ticket

    @contextmanager
    def admitted(
        self,
        ticket: ExecutionTicket,
        *,
        cancel_event: threading.Event,
        timeout: float | None = None,
    ) -> Iterator[ExecutionTicket]:
        """Wait for ``ticket``, bind it to this thread, then release exactly it."""

        self._coordinator.wait_until_running(ticket, timeout=timeout)
        binding = _Binding(ticket=ticket, cancel_event=cancel_event)
        cancel_event.clear()
        if ticket.cancellation.is_set():
            cancel_event.set()
        with self._lock:
            self._bindings[ticket.execution_id] = binding
        stack = getattr(self._local, "tickets", None)
        if stack is None:
            stack = self._local.tickets = []
        stack.append(ticket)
        try:
            yield ticket
        except BaseException as error:
            if cancel_event.is_set() and not ticket.cancellation.is_set():
                self._coordinator.request_interrupt(
                    session_id=ticket.session_id,
                    execution_id=ticket.execution_id,
                    owner=ticket.owner,
                    reason="execution cancellation was observed",
                )
            self._coordinator.fail(ticket, error)
            raise
        else:
            if cancel_event.is_set() and not ticket.cancellation.is_set():
                self._coordinator.request_interrupt(
                    session_id=ticket.session_id,
                    execution_id=ticket.execution_id,
                    owner=ticket.owner,
                    reason="execution cancellation was observed",
                )
            reason = None
            with self._lock:
                current = self._bindings.get(ticket.execution_id)
                if current is binding:
                    reason = current.failure_reason
            if reason:
                # The single exit point that still holds the lease. Reporting
                # the failure here, rather than when it was observed, is what
                # keeps the log ending in `failed` instead of
                # `failed -> completed`, and keeps the next turn from being
                # promoted while this one is still finalising.
                self._coordinator.fail(ticket, RuntimeError(reason))
            else:
                self._coordinator.complete(ticket)
        finally:
            stack.pop()
            with self._lock:
                self._bindings.pop(ticket.execution_id, None)

    def current(self, session_id: str | None = None) -> ExecutionTicket | None:
        stack = getattr(self._local, "tickets", ())
        if not stack:
            return None
        ticket = stack[-1]
        if session_id is not None and ticket.session_id != session_id:
            return None
        return ticket

    def mark_finalizing(
        self, ticket: ExecutionTicket | None = None, *, reason: str | None = None
    ) -> bool:
        ticket = ticket or self.current()
        if (
            ticket is None
            or ticket.state is not TicketState.RUNNING
            or ticket.cancellation.is_set()
        ):
            return False
        event = {
            "type": "execution_state",
            "frame_id": ticket.session_id,
            "root_frame_id": ticket.session_id,
            "execution_id": ticket.execution_id,
            "owner": ticket.owner.as_dict(),
            "status": "finalizing",
            "queue_position": 0,
            "reason": reason or "projecting final response",
            "at": self._clock(),
        }
        self._emit_for(ticket.session_id, event)
        return True

    def mark_failed(
        self, ticket: ExecutionTicket | None = None, *, reason: str | None = None
    ) -> bool:
        """Record that this turn has failed, without ending it yet.

        A turn that fails *inside* `run_message` returns normally -- its own
        handler catches the exception and reports `status="failed"` -- so the
        lease exited cleanly and the execution log read
        `queued -> running -> finalizing -> completed`. That log is what an
        operator reads to learn what a session did, and it disagreed with the
        job result, the socket and the stored frame about the one thing that
        mattered.

        Only the reason is stored. Failing the ticket at this point would
        release the lease and promote the next turn while this one still has
        its durable status, its prose and its terminal event to write -- which
        is the race this whole change exists to close. `admitted` reports it at
        the one exit that still holds the lease.
        """
        ticket = ticket or self.current()
        if ticket is None or ticket.state is not TicketState.RUNNING:
            return False
        with self._lock:
            binding = self._bindings.get(ticket.execution_id)
            if binding is None or binding.ticket is not ticket:
                return False
            binding.failure_reason = reason or "the turn failed"
        return True

    def bind_lease(
        self,
        lease: Any,
        interrupt_lease: InterruptLease,
        *,
        ticket: ExecutionTicket | None = None,
    ) -> bool:
        """Bind a frozen kernel lease to the admitted ticket on this thread."""

        ticket = ticket or self.current()
        if ticket is None:
            return False
        with self._lock:
            binding = self._bindings.get(ticket.execution_id)
            if binding is None or binding.ticket is not ticket:
                return False
            binding.lease = lease
            binding.interrupt_lease = interrupt_lease
            return True

    def unbind_lease(
        self, lease: Any, *, ticket: ExecutionTicket | None = None
    ) -> bool:
        ticket = ticket or self.current()
        if ticket is None:
            return False
        with self._lock:
            binding = self._bindings.get(ticket.execution_id)
            if binding is None or binding.lease is not lease:
                return False
            binding.lease = None
            binding.interrupt_lease = None
            return True

    def abort_unstarted(
        self, ticket: ExecutionTicket, error: BaseException | str
    ) -> str:
        """Release a ticket whose worker was never started.

        Deliberately does **not** read ``ticket.state`` first. Two reasons, and
        both are why the ordinary `cancel` is wrong here:

        * reading and then acting is a race — the FIFO can admit this ticket
          between the two, so the queued branch would no-op against a ticket
          that is now active and the ticket would be held forever;
        * `cancel`'s RUNNING branch calls `request_interrupt` and signals the
          binding, which asks the runtime to interrupt a thread that does not
          exist.

        So this asks the coordinator to do each exact thing in turn and lets it
        arbitrate: cancel the exact queued ticket, and only if that reports
        "not queued" fail the exact active one. Neither branch touches anybody
        else's ticket. The maps are cleared by the terminal event both branches
        emit, through the same path every other completion uses.

        Returns which branch acted, for the caller's log and for tests.
        """
        del error  # deliberately unused; see below
        cancelled = self._coordinator.cancel_queued(
            session_id=ticket.session_id,
            execution_id=ticket.execution_id,
            owner=ticket.owner,
            reason=_UNSTARTED_REASON,
        )
        if cancelled:
            return "cancelled_queued"
        # A FIXED reason, never one built from the original exception.
        # `fail()` renders whatever it is given through `_error_text`, which
        # calls `str(error)` -- and an exception whose `__str__` raises would
        # take the release down with it, leaving the ticket owned by a turn
        # that never began. Nothing on a cleanup path may depend on formatting
        # an exception it did not author. The original still reaches the
        # caller: it is re-raised by the spawner, unchanged.
        return (
            "failed_active"
            if self._coordinator.fail(ticket, _UNSTARTED_REASON)
            else "absent"
        )

    def cancel(
        self,
        session_id: str,
        *,
        execution_id: str,
        owner: ExecutionOwner | str,
        owner_id: str | None = None,
        reason: str = "cancelled by user",
    ) -> dict[str, Any]:
        """Cancel only an exact queued or running ticket/owner pair."""

        identity = self._owner(owner, owner_id)
        with self._lock:
            ticket = self._tickets.get(execution_id)
        if (
            ticket is None
            or ticket.session_id != session_id
            or ticket.owner != identity
        ):
            return self._cancel_result(
                False, session_id, execution_id, identity, "not_found"
            )
        if ticket.state is TicketState.QUEUED:
            changed = self._coordinator.cancel_queued(
                session_id=session_id,
                execution_id=execution_id,
                owner=identity,
                reason=reason,
            )
            return self._cancel_result(
                changed,
                session_id,
                execution_id,
                identity,
                "queued" if changed else "not_found",
            )
        if ticket.state is not TicketState.RUNNING:
            return self._cancel_result(
                False, session_id, execution_id, identity, ticket.status
            )
        changed = self._coordinator.request_interrupt(
            session_id=session_id,
            execution_id=execution_id,
            owner=identity,
            reason=reason,
        )
        interrupted = False
        if changed:
            interrupted = self._signal_binding(execution_id)
        result = self._cancel_result(
            changed,
            session_id,
            execution_id,
            identity,
            "running" if changed else "already_requested",
        )
        result["interrupted"] = interrupted
        return result

    def interrupt(
        self,
        session_id: str,
        *,
        execution_id: str,
        owner: ExecutionOwner | str,
        owner_id: str | None = None,
        reason: str = "interrupted by user",
    ) -> dict[str, Any]:
        """Interrupt only an exact *running* ticket/owner pair.

        ``cancel`` intentionally accepts both queued and running tickets.  A
        kernel interrupt is narrower: a queued ticket has no frozen kernel
        lease and must remain queued.  Keeping this distinction here avoids a
        check-then-cancel race in the HTTP adapter and prevents a queued Agent
        identity from being reported as an interrupted scientific cell.
        """

        identity = self._owner(owner, owner_id)
        with self._lock:
            ticket = self._tickets.get(execution_id)
        if (
            ticket is None
            or ticket.session_id != session_id
            or ticket.owner != identity
        ):
            return self._cancel_result(
                False, session_id, execution_id, identity, "not_found"
            )
        if ticket.state is not TicketState.RUNNING:
            return self._cancel_result(
                False, session_id, execution_id, identity, ticket.status
            )
        changed = self._coordinator.request_interrupt(
            session_id=session_id,
            execution_id=execution_id,
            owner=identity,
            reason=reason,
        )
        interrupted = self._signal_binding(execution_id) if changed else False
        result = self._cancel_result(
            changed,
            session_id,
            execution_id,
            identity,
            "running" if changed else "already_requested",
        )
        result["interrupted"] = interrupted
        return result

    def cancel_current(
        self, session_id: str, *, reason: str = "cancelled by user"
    ) -> dict[str, Any]:
        """Legacy compatibility: resolve, then cancel, the exact active owner."""

        owner = self.snapshot(session_id).get("owner")
        if not owner:
            return {
                "ok": False,
                "frame_id": session_id,
                "root_frame_id": session_id,
                "reason": "no_active_execution",
            }
        identity = owner.get("owner") or {}
        return self.cancel(
            session_id,
            execution_id=str(owner.get("execution_id") or ""),
            owner=str(identity.get("kind") or ""),
            owner_id=str(identity.get("id") or ""),
            reason=reason,
        )

    def snapshot(self, session_id: str) -> dict[str, Any]:
        return self._coordinator.snapshot(session_id)

    def drain_queued(self, session_id: str, *, reason: str) -> list[str]:
        """Cancel every still-queued ticket for one session; return their ids.

        A lifecycle Stop needs this and had no way to ask for it. `cancel` is
        deliberately exact — one ticket, matched on id AND owner — because a
        queued cancellation must never disturb a sibling. That is right for a
        user cancelling one item and wrong for "stop this session's kernel",
        where everything waiting is waiting for a kernel that is going away.

        Without it, Stop submitted its own lifecycle ticket, went to the back
        of the FIFO, and waited for work that could not finish: a queued turn
        admitted after `stop_requested` is set blocks on `stop_finished` the
        moment it tries to submit anything, and `stop_finished` is what Stop
        sets when it completes. Measured: Stop had not returned after 40s with
        three items queued behind a turn that cancelled correctly.

        The running execution is not touched here — `cancel_current` owns that,
        and it runs first.
        """
        cancelled: list[str] = []
        for item in list(self.snapshot(session_id).get("queue") or []):
            execution_id = str(item.get("execution_id") or "")
            owner = item.get("owner") or {}
            if not execution_id:
                continue
            if self._coordinator.cancel_queued(
                session_id=session_id,
                execution_id=execution_id,
                owner=str(owner.get("kind") or "agent"),
                owner_id=str(owner.get("id") or ""),
                reason=reason,
            ):
                cancelled.append(execution_id)
                self._signal_binding(execution_id)
        return cancelled

    def close_session(self, session_id: str, *, reason: str) -> bool:
        active = self.snapshot(session_id).get("owner")
        changed = self._coordinator.close_session(session_id, reason=reason)
        if active and active.get("execution_id"):
            self._signal_binding(str(active["execution_id"]))
        return changed

    def cleanup_session(self, session_id: str, *, reason: str) -> bool:
        active = self.snapshot(session_id).get("owner")
        changed = self._coordinator.cleanup_session(session_id, reason=reason)
        if active and active.get("execution_id"):
            self._signal_binding(str(active["execution_id"]))
        return changed

    def close(self, *, reason: str = "daemon shutdown") -> None:
        active_ids = [
            snapshot["owner"]["execution_id"]
            for snapshot in self._coordinator.snapshots().values()
            if snapshot.get("owner")
        ]
        self._coordinator.close(reason=reason)
        for execution_id in active_ids:
            self._signal_binding(str(execution_id))

    def _signal_binding(self, execution_id: str) -> bool:
        with self._lock:
            binding = self._bindings.get(execution_id)
            if binding is None:
                return False
            binding.cancel_event.set()
            lease = binding.lease
            interrupt = binding.interrupt_lease
        if lease is None or interrupt is None:
            return False
        try:
            return bool(interrupt(lease))
        except Exception:  # noqa: BLE001 - cancellation remains requested
            return False

    def _on_core_event(self, event: dict[str, Any]) -> None:
        session_id = str(event.get("root_frame_id") or event.get("session_id") or "")
        if not session_id:
            return
        kind = event.get("type")
        if kind == "execution_ticket_state":
            projected = dict(event)
            projected["type"] = "execution_state"
            projected["frame_id"] = session_id
            execution_id = str(projected.get("execution_id") or "")
            metadata = projected.get("metadata") or {}
            if not projected.get("reason") and metadata.get("reason"):
                projected["reason"] = metadata["reason"]
            with self._lock:
                if projected.get("queue_position") is not None:
                    self._positions[execution_id] = int(projected["queue_position"])
                projected.setdefault(
                    "queue_position",
                    self._positions.get(
                        execution_id,
                        1 if projected.get("status") == "queued" else 0,
                    ),
                )
            projected.setdefault("reason", str(projected.get("status") or "state"))
            # Internal bookkeeping first, external sink second. This ran the
            # other way round, so a subscriber that raised -- a closed socket,
            # a broken projection -- left the ticket and its queue position in
            # these maps forever, and the leak outlived the turn it belonged
            # to. Nothing about a terminal transition depends on the emit
            # succeeding, so nothing about it should wait for the emit.
            if projected.get("status") in {
                "completed",
                "failed",
                "cancelled",
            }:
                with self._lock:
                    self._tickets.pop(execution_id, None)
                    self._positions.pop(execution_id, None)
            self._emit_for(session_id, projected)
            return
        if kind == "execution_queue_changed":
            projected = dict(event)
            projected["type"] = "execution_queue"
            projected["frame_id"] = session_id
            with self._lock:
                for item in projected.get("queue") or ():
                    if (
                        item.get("execution_id")
                        and item.get("queue_position") is not None
                    ):
                        self._positions[str(item["execution_id"])] = int(
                            item["queue_position"]
                        )
            self._emit_for(session_id, projected)
            return
        if kind == "execution_owner_changed":
            projected = dict(event)
            projected["type"] = "execution_owner"
            projected["frame_id"] = session_id
            self._emit_for(session_id, projected)

    @staticmethod
    def _owner(owner: ExecutionOwner | str, owner_id: str | None) -> ExecutionOwner:
        if isinstance(owner, ExecutionOwner):
            return owner
        return ExecutionOwner(str(owner), str(owner_id or owner))

    @staticmethod
    def _cancel_result(
        ok: bool,
        session_id: str,
        execution_id: str,
        owner: ExecutionOwner,
        scope: str,
    ) -> dict[str, Any]:
        return {
            "ok": ok,
            "frame_id": session_id,
            "root_frame_id": session_id,
            "execution_id": execution_id,
            "owner": owner.as_dict(),
            "scope": scope,
        }


__all__ = ["ExecutionCancelled", "WebExecutionCoordinator"]
