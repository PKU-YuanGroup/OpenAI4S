"""Bounded sub-agent delegation, cancellation, progress, and live steering.

``host.delegate`` creates a tree, not a collection of unrelated runners.  The
tree owns the session-wide spawn budget and child identities; each runner only
owns its direct children and executor.  A context variable carries that tree
through child construction, so a grandchild created by ``Agent.__post_init__``
cannot reset the budget accidentally.

Cancellation is exact and deterministic.  ``stop_child`` marks the target and
all descendants, cancels pending futures, and interrupts only the foreground
Kernel(s) owned by those child Agents.  A stopped child can never publish a
late output.  Steering messages are queued in memory and consumed by the child
context policy at model turn boundaries instead of being appended only once at
startup.
"""

from __future__ import annotations

import contextvars
import dataclasses
import threading
import time
import uuid
from collections import deque
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor, TimeoutError
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from openai4s.agent.cell_record import DelegatedCellRecorder, compose_cell_hooks
from openai4s.agent.delegation_workspace import (
    materialize_outputs_into_parent,
    prepare_child_scratch,
    private_scratch_enabled,
    publish_child_outputs,
)
from openai4s.agent.models import KernelEnvSpec
from openai4s.agent.runtime import CompactionPolicy
from openai4s.config import Config
from openai4s.host.delegation_policy import child_execution_policy
from openai4s.observability import carry_context
from openai4s.security.sandbox import KernelReadIsolation
from openai4s.storage.delegation_attempts import (
    DelegationRequestConflict,
    delegation_identity,
    request_sha256,
)

FANOUT_CAP = 48
SESSION_CAP = 1000
MAX_DEPTH = 4

DELEGATION_PROCESS_INSTANCE_ID = f"delegation-{uuid.uuid4()}"

_TERMINAL = frozenset({"done", "failed", "stopped"})

#: Severity order for the machine-readable completion contract. A child's
#: declaration is input to the envelope build, never the record: machine
#: checks may move the status DOWN this ranking, never up.
_TASK_STATUS_RANK = {"completed": 0, "partial": 1, "blocked": 2, "failed": 3}

#: task_status values that mean "the task is not done" — the bounded retry
#: option re-runs a child exactly when its envelope lands on one of these.
_RETRYABLE_STATUS = frozenset({"partial", "blocked", "failed"})

#: Alias keys under which a structured completion carries its limitations
#: (mirrors the projection aliases in openai4s/server/completions.py without
#: importing the server layer into the delegation core).
_LIMITATION_ALIASES = ("limitations", "caveats", "限制", "局限性")


class DelegationError(RuntimeError):
    pass


class DelegationConflictError(DelegationError):
    """Same durable request key, different digest — HTTP 409."""

    http_status = 409


class DelegationBudget:
    """Session-scoped, injectable budget shared by every runner in a tree.

    ``spawned`` is cumulative and enforces the historical whole-session cap;
    releasing a finished child only decrements ``active`` and never refunds a
    spawn.  Gateway can keep one instance on SessionState and inject it into a
    fresh root runner each turn without relying on module-global lifecycle.
    """

    def __init__(
        self,
        root_frame_id: str | None = None,
        *,
        limit: int | None = None,
        initial_usage: Mapping[str, Any] | None = None,
        store: Any | None = None,
        owner_instance_id: str | None = None,
        runner_instance_id: str | None = None,
    ) -> None:
        initial = dict(initial_usage or {})
        if limit is None:
            limit = int(initial.get("limit") or SESSION_CAP)
        if limit < 1:
            raise ValueError("delegation budget limit must be positive")
        self.root_frame_id = root_frame_id
        self.limit = int(limit)
        self._lock = threading.Lock()
        self._spawned = max(0, int(initial.get("spawned") or 0))
        self._active = max(0, int(initial.get("active") or 0))
        self._sequence = max(0, int(initial.get("sequence") or 0))
        self._store = store
        self._owner_instance_id = owner_instance_id
        self._runner_instance_id = runner_instance_id
        self._request_index: dict[tuple[str, str], dict[str, Any]] = {}

    def reserve(
        self,
        count: int,
        *,
        depth: int,
        parent_child_id: str | None = None,
    ) -> list[str]:
        if count < 0:
            raise ValueError("delegation reservation must not be negative")
        with self._lock:
            if self._durable:
                try:
                    reservation = self._store.reserve_delegation_children(
                        root_frame_id=self.root_frame_id,
                        owner_instance_id=self._owner_instance_id,
                        runner_instance_id=self._runner_instance_id,
                        count=count,
                        depth=depth,
                        parent_child_id=parent_child_id,
                    )
                except (RuntimeError, KeyError) as error:
                    raise DelegationError(str(error)) from error
                self._sync_usage_locked(reservation.get("budget") or {})
                return [str(item) for item in reservation.get("child_ids") or ()]
            if self._spawned + count > self.limit:
                raise DelegationError(
                    f"session spawn cap reached ({self.limit}); "
                    f"already spawned {self._spawned}, requested {count}"
                )
            self._spawned += count
            self._active += count
            child_ids = []
            for _ in range(count):
                self._sequence += 1
                child_ids.append(f"child-{depth}-{self._sequence}")
            return child_ids

    def reserve_request(
        self,
        *,
        depth: int,
        parent_child_id: str | None,
        parent_action_group_id: str,
        native_call_id: str,
        request_sha256: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Reserve one child under a durable request identity."""

        group = str(parent_action_group_id or "").strip()
        call = str(native_call_id or "").strip()
        digest = str(request_sha256 or "").strip()
        if not group or not call or not digest:
            raise ValueError("delegation request identity is incomplete")
        with self._lock:
            if self._durable:
                try:
                    reservation = self._store.reserve_delegation_children(
                        root_frame_id=self.root_frame_id,
                        owner_instance_id=self._owner_instance_id,
                        runner_instance_id=self._runner_instance_id,
                        count=1,
                        depth=depth,
                        parent_child_id=parent_child_id,
                        parent_action_group_id=group,
                        native_call_id=call,
                        request_sha256=digest,
                        payload=payload,
                    )
                except DelegationRequestConflict as error:
                    raise DelegationConflictError(str(error)) from error
                except (RuntimeError, KeyError) as error:
                    raise DelegationError(str(error)) from error
                self._sync_usage_locked(reservation.get("budget") or {})
                return reservation
            key = (group, call)
            existing = self._request_index.get(key)
            if existing is not None:
                if existing["request_sha256"] != digest:
                    raise DelegationConflictError(
                        f"delegation request digest conflict for native_call_id {call!r}"
                    )
                return {**existing, "reused": True}
            if self._spawned + 1 > self.limit:
                raise DelegationError(
                    f"session spawn cap reached ({self.limit}); "
                    f"already spawned {self._spawned}, requested 1"
                )
            self._spawned += 1
            self._active += 1
            self._sequence += 1
            child_id = f"child-{depth}-{self._sequence}"
            record = {
                "child_ids": [child_id],
                "child_id": child_id,
                "request_id": f"dreq-mem-{self._sequence}",
                "attempt_id": f"datm-mem-{self._sequence}",
                "attempt_no": 1,
                "request_sha256": digest,
                "reused": False,
                "payload": dict(payload or {}),
                "budget": {
                    "root_frame_id": self.root_frame_id,
                    "limit": self.limit,
                    "spawned": self._spawned,
                    "active": self._active,
                    "remaining": max(0, self.limit - self._spawned),
                },
            }
            self._request_index[key] = record
            return record

    def continue_request(
        self,
        *,
        child_id: str,
        depth: int,
        parent_child_id: str | None,
    ) -> dict[str, Any]:
        """Spawn the next attempt. Restore never calls this."""

        if self._durable:
            try:
                reservation = self._store.continue_delegation_request(
                    root_frame_id=self.root_frame_id,
                    owner_instance_id=self._owner_instance_id,
                    runner_instance_id=self._runner_instance_id,
                    child_id=child_id,
                    depth=depth,
                    parent_child_id=parent_child_id,
                )
            except DelegationRequestConflict as error:
                raise DelegationConflictError(str(error)) from error
            except (RuntimeError, KeyError) as error:
                raise DelegationError(str(error)) from error
            with self._lock:
                self._sync_usage_locked(reservation.get("budget") or {})
            return reservation
        raise DelegationError("continue requires durable delegation storage")

    def release(self, count: int = 1) -> None:
        """Release active slots without refunding cumulative spawn usage."""

        if count < 0:
            raise ValueError("delegation release must not be negative")
        with self._lock:
            if self._durable:
                usage = self._store.release_delegation_budget(
                    root_frame_id=self.root_frame_id,
                    owner_instance_id=self._owner_instance_id,
                    runner_instance_id=self._runner_instance_id,
                    count=count,
                )
                self._sync_usage_locked(usage)
                return
            self._active = max(0, self._active - count)

    def usage(self) -> dict[str, Any]:
        with self._lock:
            if self._durable:
                try:
                    usage = self._store.delegation_budget(self.root_frame_id)
                except Exception:  # noqa: BLE001
                    usage = None
                if usage:
                    self._sync_usage_locked(usage)
            return {
                "root_frame_id": self.root_frame_id,
                "limit": self.limit,
                "spawned": self._spawned,
                "active": self._active,
                "remaining": max(0, self.limit - self._spawned),
            }

    def _set_spawned_for_compatibility(self, value: int) -> None:
        with self._lock:
            if self._durable:
                raise RuntimeError("cannot rewrite a durable delegation budget")
            self._spawned = max(0, int(value))
            self._active = min(self._active, self._spawned)

    def bind_persistence(
        self,
        *,
        store: Any,
        owner_instance_id: str,
        runner_instance_id: str,
        usage: Mapping[str, Any],
    ) -> None:
        with self._lock:
            self._store = store
            self._owner_instance_id = owner_instance_id
            self._runner_instance_id = runner_instance_id
            self._sync_usage_locked(usage)

    @property
    def _durable(self) -> bool:
        return bool(
            self.root_frame_id
            and self._store is not None
            and self._owner_instance_id
            and self._runner_instance_id
        )

    def _sync_usage_locked(self, usage: Mapping[str, Any]) -> None:
        if usage.get("root_frame_id"):
            self.root_frame_id = str(usage["root_frame_id"])
        self.limit = max(1, int(usage.get("limit") or self.limit))
        self._spawned = max(0, int(usage.get("spawned") or 0))
        self._active = max(0, int(usage.get("active") or 0))
        self._sequence = max(0, int(usage.get("sequence") or self._sequence))


class _SteeringMessage:
    __slots__ = (
        "message_id",
        "text",
        "status",
        "queued_at",
        "delivered_at",
        "boundary",
    )

    def __init__(self, message_id: str, text: str, queued_at: float) -> None:
        self.message_id = message_id
        self.text = text
        self.status = "queued"
        self.queued_at = queued_at
        self.delivered_at: float | None = None
        self.boundary: int | None = None

    def deliver(self, boundary: int, delivered_at: float) -> None:
        self.status = "delivered"
        self.boundary = boundary
        self.delivered_at = delivered_at

    def discard(self) -> None:
        if self.status == "queued":
            self.status = "discarded"

    @classmethod
    def restore(cls, value: Mapping[str, Any]) -> _SteeringMessage:
        message = cls(
            str(value.get("message_id") or "restored-message"),
            str(value.get("text_preview") or ""),
            float(value.get("queued_at") or 0.0),
        )
        state = str(value.get("status") or "discarded")
        message.status = (
            state if state in {"queued", "delivered", "discarded"} else "discarded"
        )
        message.delivered_at = (
            float(value["delivered_at"])
            if value.get("delivered_at") is not None
            else None
        )
        message.boundary = (
            int(value["boundary"]) if value.get("boundary") is not None else None
        )
        return message

    def snapshot(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "status": self.status,
            "queued_at": self.queued_at,
            "delivered_at": self.delivered_at,
            "boundary": self.boundary,
        }

    def persistence_snapshot(self) -> dict[str, Any]:
        return {**self.snapshot(), "text_preview": self.text}


class _RetryChain:
    """Cancellation shared by every immutable attempt of one logical child.

    Mutations happen while ``_DelegationTree.lock`` is held. The Event makes
    cancellation visible to the child runtime without taking that tree lock.
    """

    def __init__(self, chain_id: str) -> None:
        self.chain_id = chain_id
        self.cancel_event = threading.Event()
        self.reason = "child stopped"

    def cancel(self, reason: str) -> None:
        if not self.cancel_event.is_set():
            self.reason = reason
            self.cancel_event.set()

    def cancelled(self) -> bool:
        return self.cancel_event.is_set()


class _Child:
    """Thread-safe state for one direct or nested sub-agent."""

    def __init__(
        self,
        child_id: str,
        name: str | None,
        spec: dict[str, Any],
        *,
        depth: int,
        parent_child_id: str | None,
        parent_frame_id: str | None,
        store: Any | None,
        budget: DelegationBudget,
        clock: Callable[[], float],
        retry_chain: _RetryChain | None = None,
    ) -> None:
        self.child_id = child_id
        self.name = name
        self.spec = spec
        self.depth = depth
        self.parent_child_id = parent_child_id
        self.parent_frame_id = parent_frame_id
        self.store = store
        self.budget = budget
        self._retry_chain = retry_chain or _RetryChain(child_id)
        self.status = "pending"
        self.result: dict[str, Any] | None = None
        self.future: Future | None = None
        self.stop_event = threading.Event()
        self._stop_reason = "child stopped"
        self.error: str | None = None
        self.frame_id: str | None = None
        self.agent: Any | None = None
        self.created_at = clock()
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.turn_boundary = 0
        self.max_turns: int | None = None
        self.last_progress_at: float | None = None
        self._clock = clock
        self._budget_released = False
        self._lock = threading.RLock()
        self._inbox: deque[_SteeringMessage] = deque()
        self._messages: list[_SteeringMessage] = []
        self.request_id: str | None = None
        self.attempt_id: str | None = None
        self.artifact_refs: list[dict[str, Any]] = []
        self.completion = threading.Event()

    def begin(self, max_turns: int) -> bool:
        with self._lock:
            if self.stop_event.is_set():
                self._mark_stopped_locked(self.stop_event_reason())
                return False
            self.status = "running"
            self.started_at = self._clock()
            self.max_turns = max_turns
            return True

    def attach_agent(self, agent: Any) -> bool:
        """Attach the exact runtime; return whether it was already stopped."""

        with self._lock:
            self.agent = agent
            return self.stop_event.is_set()

    def detach_agent(self, agent: Any) -> None:
        with self._lock:
            if self.agent is agent:
                self.agent = None

    def set_frame(self, frame_id: str) -> None:
        with self._lock:
            self.frame_id = frame_id

    def set_future(self, future: Future) -> None:
        with self._lock:
            self.future = future
            should_cancel = self.stop_event.is_set()
        if should_cancel:
            future.cancel()

    def request_stop(self, reason: str) -> tuple[bool, Any | None, Future | None]:
        """Atomically stop state and return runtime handles to signal outside."""

        with self._lock:
            if self.status in _TERMINAL:
                return False, None, self.future
            first = not self.stop_event.is_set()
            if first:
                self._stop_reason = reason
                self.stop_event.set()
            self._mark_stopped_locked(reason)
            return first, self.agent, self.future

    def stop_event_reason(self) -> str:
        with self._lock:
            return self._stop_reason

    def finish_done(self, result: dict[str, Any]) -> bool:
        with self._lock:
            if self.stop_event.is_set():
                self._mark_stopped_locked(self.stop_event_reason())
                return False
            self.status = "done"
            self.result = result
            self.error = None
            self.finished_at = self._clock()
            self._discard_queued_locked()
            self._release_budget_locked()
            self.completion.set()
            return True

    def finish_failed(self, error: str, result: dict[str, Any]) -> bool:
        with self._lock:
            if self.stop_event.is_set():
                self._mark_stopped_locked(self.stop_event_reason())
                return False
            self.status = "failed"
            self.error = error
            self.result = result
            self.finished_at = self._clock()
            self._discard_queued_locked()
            self._release_budget_locked()
            self.completion.set()
            return True

    def stopped_result(self) -> dict[str, Any]:
        with self._lock:
            self._mark_stopped_locked(self.stop_event_reason())
            return dict(self.result or {})

    def enqueue(self, message: _SteeringMessage) -> tuple[bool, int]:
        with self._lock:
            if self.status in _TERMINAL or self.stop_event.is_set():
                return False, len(self._inbox)
            self._inbox.append(message)
            self._messages.append(message)
            return True, len(self._inbox)

    def consume_steering(self, boundary: int) -> list[_SteeringMessage]:
        with self._lock:
            messages = list(self._inbox)
            self._inbox.clear()
            now = self._clock()
            for message in messages:
                message.deliver(boundary, now)
            return messages

    def mark_boundary(self, boundary: int) -> None:
        with self._lock:
            self.turn_boundary = max(self.turn_boundary, boundary)
            self.last_progress_at = self._clock()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            queued = sum(message.status == "queued" for message in self._messages)
            delivered = sum(message.status == "delivered" for message in self._messages)
            discarded = sum(message.status == "discarded" for message in self._messages)
            output = (self.result or {}).get("output")
            if self.status == "stopped":
                output = None
            return {
                "child_id": self.child_id,
                "name": self.name,
                "status": self.status,
                "task_status": (self.result or {}).get("task_status"),
                "output": output,
                "error": self.error,
                "depth": self.depth,
                "parent_child_id": self.parent_child_id,
                "parent_frame_id": self.parent_frame_id,
                "frame_id": self.frame_id,
                "request_id": self.request_id,
                "attempt_id": self.attempt_id,
                "artifact_refs": list(self.artifact_refs),
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "progress": {
                    "turn_boundary": self.turn_boundary,
                    "max_turns": self.max_turns,
                    "last_progress_at": self.last_progress_at,
                },
                "steering": {
                    "queued": queued,
                    "delivered": delivered,
                    "discarded": discarded,
                    "messages": [message.snapshot() for message in self._messages],
                },
                "overrides": _public_overrides(self.spec),
            }

    def persistence_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "child_id": self.child_id,
                "name": self.name,
                "status": self.status,
                "depth": self.depth,
                "parent_child_id": self.parent_child_id,
                "parent_frame_id": self.parent_frame_id,
                "frame_id": self.frame_id,
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "turn_boundary": self.turn_boundary,
                "max_turns": self.max_turns,
                "last_progress_at": self.last_progress_at,
                "overrides": _public_overrides(self.spec),
                "result": self.result,
                "error": self.error,
                "request_id": self.request_id,
                "attempt_id": self.attempt_id,
                "artifact_refs": list(self.artifact_refs),
                # Every terminal persists its stop_reason: the stopped reason
                # text for stopped children (unchanged), the engine's
                # stop_reason (submitted/max_turns/error) for the rest.
                "stop_reason": (
                    self._stop_reason
                    if self.status == "stopped"
                    else (self.result or {}).get("stop_reason")
                ),
                "task_status": (self.result or {}).get("task_status"),
            }

    @classmethod
    def from_persisted(
        cls,
        value: Mapping[str, Any],
        *,
        store: Any | None,
        budget: DelegationBudget,
        clock: Callable[[], float],
    ) -> _Child:
        overrides = value.get("overrides")
        spec = dict(overrides) if isinstance(overrides, Mapping) else {}
        if value.get("name"):
            spec["name"] = value["name"]
        child = cls(
            str(value["child_id"]),
            value.get("name"),
            spec,
            depth=max(0, int(value.get("depth") or 0)),
            parent_child_id=value.get("parent_child_id"),
            parent_frame_id=value.get("parent_frame_id"),
            store=store,
            budget=budget,
            clock=clock,
        )
        state = str(value.get("status") or "stopped")
        child.status = state if state in _TERMINAL else "stopped"
        child.frame_id = value.get("frame_id")
        child.result = (
            dict(value["result"]) if isinstance(value.get("result"), Mapping) else None
        )
        child.error = value.get("error")
        child.created_at = float(value.get("created_at") or 0.0)
        child.started_at = (
            float(value["started_at"]) if value.get("started_at") is not None else None
        )
        child.finished_at = (
            float(value["finished_at"])
            if value.get("finished_at") is not None
            else None
        )
        progress = value.get("progress")
        if isinstance(progress, Mapping):
            child.turn_boundary = max(0, int(progress.get("turn_boundary") or 0))
            child.max_turns = (
                int(progress["max_turns"])
                if progress.get("max_turns") is not None
                else None
            )
            child.last_progress_at = (
                float(progress["last_progress_at"])
                if progress.get("last_progress_at") is not None
                else None
            )
        steering = value.get("steering")
        rows = steering.get("messages") if isinstance(steering, Mapping) else ()
        for row in rows or ():
            if not isinstance(row, Mapping):
                continue
            message = _SteeringMessage.restore(row)
            child._messages.append(message)
            if message.status == "queued":
                child._inbox.append(message)
        child._stop_reason = str(value.get("stop_reason") or "restored terminal child")
        if child.status == "stopped":
            child.stop_event.set()
        child._budget_released = True
        child.request_id = value.get("request_id")
        child.attempt_id = value.get("attempt_id")
        refs = value.get("artifact_refs")
        child.artifact_refs = (
            [dict(item) for item in refs] if isinstance(refs, list) else []
        )
        if child.status in _TERMINAL:
            child.completion.set()
        return child

    def _mark_stopped_locked(self, reason: str) -> None:
        was_terminal = self.status in _TERMINAL
        self.status = "stopped"
        self.error = None
        self.finished_at = self.finished_at or self._clock()
        # Deliberately no task_status: a stopped child's task was neither
        # completed nor judged — the daemon-restart repair path leaves the
        # column NULL for the same reason.
        self.result = {
            "child_id": self.child_id,
            "name": self.name,
            "stop_reason": "stopped",
            "output": None,
            "completion_bullets": [],
            "error": None,
            "reason": reason,
            "frame_id": self.frame_id,
            "turns": self.turn_boundary or None,
            "max_turns": self.max_turns,
            "environment": None,
            "limitations": [],
            "artifacts": [],
        }
        self._discard_queued_locked()
        if not was_terminal:
            self._release_budget_locked()
        self.completion.set()

    def _discard_queued_locked(self) -> None:
        self._inbox.clear()
        for message in self._messages:
            message.discard()

    def _release_budget_locked(self) -> None:
        if self._budget_released:
            return
        self._budget_released = True
        self.budget.release()


#: Child step kinds that are worth relaying into the parent Timeline: skill
#: loads, environment switches/installs, artifact saves, nested delegation.
#: Everything else (searches, fetches, reads…) is dropped unless it ends in an
#: error — never per-chunk output.
_CHILD_STEP_KINDS = frozenset({"skill", "env", "artifact", "delegate"})

#: Per-child relay budget. A 48-way fan-out must not evict the turn's own
#: prose and cards from the bounded WS replay buffer, so after this many
#: forwarded steps the rest collapse into one "N more steps elided" marker.
_CHILD_STEP_CAP = 200

#: Bound on begin-events stashed for possible error escalation.
_CHILD_STEP_STASH_CAP = 32


class _ChildStepForwarder:
    """Bounded relay of one child's meaningful semantic steps.

    Installed as the child dispatcher's ``on_step`` by ``_run_one`` whenever
    the tree carries a session step sink (Web only — the CLI has no sink and
    is unchanged). Each begin event is decorated with the child identity under
    ``input["delegation"]`` so the UI and the durable root-keyed
    ``frame_steps`` rows can attribute it; end events ride the same step_id.
    Steps of non-meaningful kinds are stashed and relayed only when they end
    in an error, so failures stay visible without per-chunk noise.
    """

    def __init__(
        self,
        sink: Callable[[dict[str, Any]], None],
        *,
        child_id: str,
        frame_id: str | None,
        name: str | None,
        depth: int,
    ) -> None:
        self._sink = sink
        self._decoration = {
            "delegation_child_id": child_id,
            "child_frame_id": frame_id,
            "child_name": name,
            "depth": depth,
        }
        self._lock = threading.Lock()
        self._forwarded_ids: set[str] = set()
        self._pending_begin: dict[str, dict[str, Any]] = {}
        self._forwarded = 0
        self._elided = 0

    def __call__(self, event: dict[str, Any]) -> None:
        try:
            self._relay(event)
        except Exception:  # noqa: BLE001 - observability must not break a child
            pass

    def _decorated(self, event: dict[str, Any]) -> dict[str, Any]:
        payload = dict(event)
        base = payload.get("input")
        merged = dict(base) if isinstance(base, dict) else {}
        merged["delegation"] = dict(self._decoration)
        payload["input"] = merged
        return payload

    def _emit_begin_locked(self, event: dict[str, Any]) -> bool:
        if self._forwarded >= _CHILD_STEP_CAP:
            self._elided += 1
            return False
        self._forwarded += 1
        self._sink(self._decorated(event))
        return True

    def _relay(self, event: dict[str, Any]) -> None:
        step_id = str(event.get("step_id") or "")
        phase = event.get("phase")
        if not step_id:
            return
        with self._lock:
            if phase == "begin":
                if event.get("kind") in _CHILD_STEP_KINDS:
                    if self._emit_begin_locked(event):
                        self._forwarded_ids.add(step_id)
                elif len(self._pending_begin) < _CHILD_STEP_STASH_CAP:
                    self._pending_begin[step_id] = dict(event)
                return
            if step_id in self._forwarded_ids:
                self._forwarded_ids.discard(step_id)
                self._sink(dict(event))
                return
            stashed = self._pending_begin.pop(step_id, None)
            if stashed is not None and event.get("status") == "error":
                # Errors are meaningful whatever their kind: relay the stashed
                # begin so the end has a card to land on.
                if self._emit_begin_locked(stashed):
                    self._sink(dict(event))

    def flush(self) -> None:
        """Emit the single elision marker once the child run is over."""
        with self._lock:
            elided = self._elided
            self._elided = 0
            name = self._decoration.get("child_name") or self._decoration.get(
                "delegation_child_id"
            )
        if not elided:
            return
        step_id = f"s-elide-{uuid.uuid4().hex[:12]}"
        try:
            self._sink(
                self._decorated(
                    {
                        "phase": "begin",
                        "step_id": step_id,
                        "kind": "delegate",
                        "title": f"{name}: further steps elided",
                        "input": {},
                    }
                )
            )
            self._sink(
                {
                    "phase": "end",
                    "step_id": step_id,
                    "status": "done",
                    "output": {"elided": elided},
                    "summary": f"{elided} more steps elided",
                }
            )
        except Exception:  # noqa: BLE001 - the marker is best effort
            pass


class _DelegationTree:
    """Shared identities, budget, lineage, and event projection for one tree."""

    def __init__(
        self,
        *,
        budget: DelegationBudget | None = None,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
        child_step_sink: Callable[[dict[str, Any]], None] | None = None,
        persistence_sink: Callable[[_Child], None] | None = None,
        trusted_capture_admission: Callable[[], str | None] | None = None,
        trusted_capture_lease: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.lock = threading.RLock()
        # Stage 1 Web Artifact capture brackets a delegated Cell with a shared
        # workspace snapshot. The gate is separate from ``lock`` so steering,
        # cancellation and status reads remain live while one synchronous
        # lineage owns capture. RLock is intentional: a child may synchronously
        # delegate a grandchild on the same Host-RPC thread.
        self.trusted_capture_gate = threading.RLock()
        self.budget = budget or DelegationBudget()
        self.message_sequence = 0
        self.children: dict[str, _Child] = {}
        self.event_sink = event_sink
        # The session step sink child dispatchers forward their meaningful
        # steps into (root-keyed on the Web). Lives on the tree so nested
        # runners — which adopt the tree through the contextvar — inherit it
        # without per-level threading.
        self.child_step_sink = child_step_sink
        self.persistence_sink = persistence_sink
        self.trusted_capture_admission = trusted_capture_admission
        self.trusted_capture_lease = trusted_capture_lease
        self.clock = clock

    def allocate(
        self, *, parent_child_id: str | None, depth: int, count: int
    ) -> list[str]:
        with self.lock:
            if parent_child_id is not None:
                parent = self.children.get(parent_child_id)
                if (
                    parent is None
                    or parent.stop_event.is_set()
                    or parent.snapshot()["status"] != "running"
                ):
                    raise DelegationError(
                        "cannot delegate from a stopped or finished child"
                    )
            return self.budget.reserve(
                count,
                depth=depth,
                parent_child_id=parent_child_id,
            )

    def register(self, child: _Child) -> None:
        with self.lock:
            self.children[child.child_id] = child
        self.emit("registered", child)

    def create_retry(
        self,
        previous: _Child,
        *,
        spec: dict[str, Any],
        depth: int,
        parent_child_id: str | None,
        parent_frame_id: str | None,
        store: Any | None,
        direct_children: dict[str, _Child],
    ) -> _Child | None:
        """Atomically refuse cancellation or reserve and register one retry."""

        with self.lock:
            chain = previous._retry_chain
            if chain.cancelled():
                return None
            child_ids = self.allocate(
                parent_child_id=parent_child_id,
                depth=depth,
                count=1,
            )
            child = _Child(
                child_ids[0],
                spec.get("name"),
                spec,
                depth=depth + 1,
                parent_child_id=parent_child_id,
                parent_frame_id=parent_frame_id,
                store=store,
                budget=self.budget,
                clock=self.clock,
                retry_chain=chain,
            )
            self.children[child.child_id] = child
            direct_children[child.child_id] = child
            # Publish registration before cancellation can observe the child;
            # otherwise a stopped event could race ahead of "registered".
            self.emit("registered", child)
            return child

    def cancel_retry_subtrees(
        self, child_ids: Sequence[str], reason: str
    ) -> list[tuple[_Child, bool, Any | None, Future | None]]:
        """Cancel attempts, their retry siblings, and all nested descendants.

        Chain cancellation and child stop publication share the same tree lock
        as ``create_retry``. Therefore a retry is either registered and stopped
        here, or observes the cancelled chain before consuming another budget
        slot; it cannot appear in the gap after a cancellation snapshot.
        """

        with self.lock:
            affected: set[str] = set()
            frontier = list(child_ids)
            while frontier:
                current = frontier.pop(0)
                if current in affected:
                    continue
                child = self.children.get(current)
                if child is None:
                    continue
                chain = child._retry_chain
                chain.cancel(reason)
                related = [
                    candidate.child_id
                    for candidate in self.children.values()
                    if candidate._retry_chain is chain
                    or candidate.parent_child_id == current
                ]
                affected.add(current)
                frontier.extend(item for item in related if item not in affected)

            stopped = []
            for child in self.children.values():
                if child.child_id not in affected:
                    continue
                first, agent, future = child.request_stop(reason)
                stopped.append((child, first, agent, future))
            return stopped

    def restore(self, children: Sequence[_Child]) -> None:
        with self.lock:
            for child in children:
                self.children[child.child_id] = child
                for message in child._messages:
                    try:
                        sequence = int(message.message_id.rsplit("-", 1)[-1])
                    except (TypeError, ValueError):
                        continue
                    self.message_sequence = max(self.message_sequence, sequence)

    def next_message_id(self) -> str:
        with self.lock:
            self.message_sequence += 1
            return f"steer-{self.message_sequence}"

    def descendants(self, child_id: str, *, include_self: bool = True) -> list[_Child]:
        with self.lock:
            found: list[_Child] = []
            frontier = [child_id]
            while frontier:
                current = frontier.pop(0)
                child = self.children.get(current)
                if child is not None and (include_self or current != child_id):
                    found.append(child)
                frontier.extend(
                    candidate.child_id
                    for candidate in self.children.values()
                    if candidate.parent_child_id == current
                )
            return found

    def subtree(self, parent_child_id: str | None) -> list[_Child]:
        with self.lock:
            if parent_child_id is None:
                return list(self.children.values())
        return self.descendants(parent_child_id, include_self=False)

    def emit(self, event: str, child: _Child, **extra: Any) -> None:
        persist = self.persistence_sink
        if persist is not None:
            try:
                persist(child)
            except Exception:  # noqa: BLE001
                pass
        sink = self.event_sink
        if sink is None:
            return
        payload = {
            "type": "delegation_child_event",
            "event": event,
            "at": self.clock(),
            "child": child.snapshot(),
            **extra,
        }
        try:
            sink(payload)
        except Exception:  # noqa: BLE001 - observability cannot strand a child
            pass


_ACTIVE_DELEGATION: contextvars.ContextVar[tuple[_DelegationTree, str] | None] = (
    contextvars.ContextVar("openai4s_active_delegation", default=None)
)


class _ChildCancellation:
    def __init__(self, child: _Child) -> None:
        self._child = child

    def cancelled(self) -> bool:
        return self._child.stop_event.is_set() or self._child._retry_chain.cancelled()


def _child_context_budget(cfg: Config):
    """A budget provider for one child's model, or None if it cannot be known.

    Returning None restores the previous behaviour deliberately: an unknown
    model falls back to the configured default rather than to a guess, and a
    capability lookup that raises must not take the child down with it.
    """

    def _budget(_state: Any) -> int | None:
        try:
            from openai4s.llm import get_model_capabilities

            capabilities = get_model_capabilities(
                str(getattr(cfg.llm, "provider", "") or ""),
                str(getattr(cfg.llm, "model", "") or ""),
                base_url=str(getattr(cfg.llm, "base_url", "") or ""),
            )
            return (
                capabilities.usable_context_tokens
                or capabilities.context_window_tokens
                or None
            )
        except Exception:  # noqa: BLE001 - an unknown model is not a failure
            return None

    return _budget


class _SteeringContextPolicy:
    """Inject newly delivered parent messages before each child model turn."""

    def __init__(self, cfg: Config, child: _Child, tree: _DelegationTree) -> None:
        # The child's OWN model decides when the child compacts. This was a
        # bare `CompactionPolicy(cfg)`, which falls back to
        # `cfg.context_window_tokens` -- the daemon default, 262,144 -- while
        # the Web session path has always derived the budget from the model's
        # declared capability. A child may run a different model than its
        # parent (`overrides["model"]`), which is exactly when the two numbers
        # diverge: a child on a model whose usable window is 136,000 tokens
        # would compact against 262,144 and sail past its real limit, learning
        # about it as a provider rejection rather than as a compaction.
        self._base = CompactionPolicy(
            cfg, context_budget_provider=_child_context_budget(cfg)
        )
        self._child = child
        self._tree = tree

    def bind_runtime(self, **providers: Any) -> None:
        """Fill in providers only the child's ``Agent.run`` can build.

        The tool catalogue and the kernel cwd exist only inside the run, so
        the loop hands them in after construction; a provider the base policy
        already carries (the child's own context budget) is kept.  Without
        this a child priced its trigger without tool schemas and never got
        the kernel-readable copy of an oversized output.
        """
        for name, value in providers.items():
            if getattr(self._base, name, None) is None:
                setattr(self._base, name, value)

    def prepare(self, state: Any) -> Sequence[Mapping[str, Any]]:
        boundary = int(getattr(state, "turn", 0)) + 1
        self._child.mark_boundary(boundary)
        self._tree.emit("progress", self._child)
        messages = self._child.consume_steering(boundary)
        if messages:
            state.messages.append(
                {
                    "role": "user",
                    "content": (
                        "[Steering from the parent at this turn boundary]\n"
                        + "\n".join(f"- {message.text}" for message in messages)
                    ),
                }
            )
            self._tree.emit(
                "steering_delivered",
                self._child,
                message_ids=[message.message_id for message in messages],
                boundary=boundary,
            )
        return self._base.prepare(state)


class DelegationRunner:
    """Direct-child facade backed by one session-wide delegation tree."""

    def __init__(
        self,
        cfg: Config,
        child_max_turns: int | None = None,
        depth: int = 0,
        parent_frame_id: str | None = None,
        store: Any | None = None,
        *,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
        child_step_sink: Callable[[dict[str, Any]], None] | None = None,
        budget: DelegationBudget | None = None,
        delegation_tree: _DelegationTree | None = None,
        parent_child_id: str | None = None,
        owner_instance_id: str | None = None,
        runner_instance_id: str | None = None,
        workspace: str | Path | None = None,
        read_isolation: KernelReadIsolation | None = None,
        cell_hooks_factory: Callable[[str], object] | None = None,
        trusted_capture_admission: Callable[[], str | None] | None = None,
        trusted_capture_lease: Callable[[], Any] | None = None,
        env: KernelEnvSpec | None = None,
        private_scratch: bool | None = None,
    ) -> None:
        if depth < 0 or depth > MAX_DEPTH:
            raise ValueError(f"delegation depth must be between 0 and {MAX_DEPTH}")
        active = _ACTIVE_DELEGATION.get()
        if delegation_tree is None and active is not None:
            delegation_tree = active[0]
        if parent_child_id is None and active is not None:
            parent_child_id = active[1]
        self.cfg = cfg
        self.child_max_turns = child_max_turns
        self.depth = depth
        self.parent_frame_id = parent_frame_id
        self.parent_child_id = parent_child_id
        self.store = store
        # Children run here instead of in os.getcwd(). The Web gateway passes
        # the parent session's workspace so a delegated child's kernels and
        # relative file writes land where the parent's artifact capture looks,
        # never in the daemon's launch directory. None preserves the CLI
        # contract: each child resolves its own process cwd at run() start.
        self.workspace = workspace
        # Carried unchanged through every nesting level. This is a process
        # boundary selected by the Web owner, not a child-model option.
        self.read_isolation = read_isolation
        # Interpreter/environment inheritance: the parent session's selection,
        # threaded into each child Agent (which threads it into ITS nested
        # runner), so every descendant kernel runs the same environment. None
        # preserves the CLI contract (sys.executable, no env). Re-stamped per
        # Web turn alongside workspace/read_isolation.
        self.env = env
        # Feature-flagged child-private scratch. Default follows the strict
        # env flag (off). Tests pass an explicit bool so they do not mutate
        # process environment.
        self.private_scratch = (
            bool(private_scratch)
            if private_scratch is not None
            else private_scratch_enabled()
        )
        # Web embedding supplies the Artifact boundary.  The delegation core
        # only forwards this duck-typed hook and remains independent of server
        # storage or UI projections.
        self.cell_hooks_factory = cell_hooks_factory
        self.owner_instance_id = owner_instance_id or DELEGATION_PROCESS_INSTANCE_ID
        self.runner_instance_id = runner_instance_id or f"runner-{uuid.uuid4()}"
        if (
            delegation_tree is not None
            and budget is not None
            and delegation_tree.budget is not budget
        ):
            raise ValueError("budget conflicts with delegation_tree budget")
        restored: dict[str, Any] | None = None
        if (
            delegation_tree is None
            and parent_frame_id
            and store is not None
            and callable(getattr(store, "restore_delegation_tree", None))
        ):
            restored = store.restore_delegation_tree(
                root_frame_id=parent_frame_id,
                owner_instance_id=self.owner_instance_id,
                runner_instance_id=self.runner_instance_id,
                budget_limit=(budget.limit if budget is not None else SESSION_CAP),
            )
            usage = restored.get("budget") or {}
            if budget is None:
                budget = DelegationBudget(
                    parent_frame_id,
                    limit=int(usage.get("limit") or SESSION_CAP),
                    initial_usage=usage,
                    store=store,
                    owner_instance_id=self.owner_instance_id,
                    runner_instance_id=self.runner_instance_id,
                )
            else:
                budget.bind_persistence(
                    store=store,
                    owner_instance_id=self.owner_instance_id,
                    runner_instance_id=self.runner_instance_id,
                    usage=usage,
                )

        persistence_sink = None
        if restored is not None:

            def persist(child: _Child) -> None:
                store.persist_delegation_child(
                    root_frame_id=parent_frame_id,
                    owner_instance_id=self.owner_instance_id,
                    runner_instance_id=self.runner_instance_id,
                    child=child.persistence_snapshot(),
                    messages=[
                        message.persistence_snapshot() for message in child._messages
                    ],
                )

            persistence_sink = persist

        self._tree = delegation_tree or _DelegationTree(
            budget=(budget or DelegationBudget(parent_frame_id)),
            event_sink=event_sink,
            child_step_sink=child_step_sink,
            persistence_sink=persistence_sink,
            trusted_capture_admission=trusted_capture_admission,
            trusted_capture_lease=trusted_capture_lease,
        )
        if trusted_capture_admission is not None:
            self._tree.trusted_capture_admission = trusted_capture_admission
        if trusted_capture_lease is not None:
            self._tree.trusted_capture_lease = trusted_capture_lease
        self.budget = self._tree.budget
        if event_sink is not None and self._tree.event_sink is None:
            self._tree.event_sink = event_sink
        if child_step_sink is not None and self._tree.child_step_sink is None:
            self._tree.child_step_sink = child_step_sink
        self._lock = self._tree.lock
        self._children: dict[str, _Child] = {}
        if restored is not None:
            restored_children = [
                _Child.from_persisted(
                    item,
                    store=store,
                    budget=self.budget,
                    clock=self._tree.clock,
                )
                for item in restored.get("children") or ()
            ]
            self._tree.restore(restored_children)
            self._children = {
                child.child_id: child
                for child in restored_children
                if child.parent_child_id == self.parent_child_id
            }
        self._pool = ThreadPoolExecutor(max_workers=FANOUT_CAP)

    @property
    def _spawned(self) -> int:
        """Compatibility view of the former runner-local counter."""

        return int(self.budget.usage()["spawned"])

    @_spawned.setter
    def _spawned(self, value: int) -> None:
        self.budget._set_spawned_for_compatibility(value)

    def _reserve(self, n: int) -> list[str]:
        return self._tree.allocate(
            parent_child_id=self.parent_child_id,
            depth=self.depth,
            count=n,
        )

    def _child_from_reservation(
        self, reservation: Mapping[str, Any], spec: dict[str, Any]
    ) -> tuple[_Child, bool]:
        child_id = str((reservation.get("child_ids") or [""])[0])
        reused = bool(reservation.get("reused"))
        existing = self._tree.children.get(child_id) or self._children.get(child_id)
        if reused:
            child = existing or self._restore_one_child(child_id)
            if child is None:
                raise DelegationError(
                    f"reused delegation child {child_id!r} is not available"
                )
            if reservation.get("request_id"):
                child.request_id = str(reservation["request_id"])
            if reservation.get("attempt_id"):
                child.attempt_id = str(reservation["attempt_id"])
            return child, True
        child = _Child(
            child_id,
            spec.get("name"),
            spec,
            depth=self.depth + 1,
            parent_child_id=self.parent_child_id,
            parent_frame_id=self.parent_frame_id,
            store=self.store,
            budget=self.budget,
            clock=self._tree.clock,
        )
        child.request_id = (
            str(reservation["request_id"]) if reservation.get("request_id") else None
        )
        child.attempt_id = (
            str(reservation["attempt_id"]) if reservation.get("attempt_id") else None
        )
        self._children[child.child_id] = child
        self._tree.register(child)
        return child, False

    def _restore_one_child(self, child_id: str) -> _Child | None:
        if self.store is None or not self.parent_frame_id:
            return None
        tree = self.store.delegation_tree(self.parent_frame_id)
        for item in tree.get("children") or ():
            if str(item.get("child_id") or "") != child_id:
                continue
            child = _Child.from_persisted(
                item,
                store=self.store,
                budget=self.budget,
                clock=self._tree.clock,
            )
            self._tree.children[child.child_id] = child
            if child.parent_child_id == self.parent_child_id:
                self._children[child.child_id] = child
            return child
        return None

    def _reuse_child(self, child: _Child, *, wait: bool, is_list: bool) -> Any:
        """Return an existing child without spawning and without charging budget."""

        if child.status in _TERMINAL:
            result = self._enrich_result(child.result or child.snapshot(), child)
            return [result] if is_list else result
        if wait:
            child.completion.wait()
            result = self._enrich_result(child.result or child.snapshot(), child)
            return [result] if is_list else result
        handle = self._enrich_result(child.snapshot(), child)
        return [handle] if is_list else handle

    @staticmethod
    def _enrich_result(
        result: Mapping[str, Any] | None, child: _Child
    ) -> dict[str, Any]:
        payload = dict(result or {})
        payload.setdefault("child_id", child.child_id)
        payload["request_id"] = child.request_id
        payload["attempt_id"] = child.attempt_id
        payload["artifact_refs"] = list(
            payload.get("artifact_refs") or child.artifact_refs or []
        )
        return payload

    def _run_one(self, child: _Child) -> dict[str, Any]:
        spec = child.spec
        child_cfg = _child_config(self.cfg, spec)
        execution_policy = child_execution_policy(spec)
        max_turns = _child_turn_budget(spec, self.child_max_turns, child_cfg.max_turns)
        if not child.begin(max_turns):
            self._persist_status(child, "stopped")
            self._tree.emit("stopped", child)
            return child.stopped_result()
        self._tree.emit("running", child)

        child_frame_id: str | None = None
        if self.store is not None:
            child_frame_id = self.store.new_frame(
                parent_id=self.parent_frame_id,
                kind="delegate",
                name=spec.get("name") or child.child_id,
                model=child_cfg.llm.model,
                depth=child.depth,
            )
            child.set_frame(child_frame_id)
            self._tree.emit("frame_attached", child)
            print(
                f"[delegate] frame_id={child_frame_id} "
                f"child={child.child_id} depth={child.depth} "
                f"leaf={child.depth >= MAX_DEPTH}"
            )

        # Recording is unconditional whenever the child has a durable frame;
        # the stage-1 Artifact capture hooks stay optional (their flag
        # defaults off) and compose after the recorder so a capture failure
        # can never lose the execution record.
        recorder = (
            DelegatedCellRecorder(self.store, child_frame_id)
            if self.store is not None and child_frame_id
            else None
        )
        capture_hooks = (
            self.cell_hooks_factory(child_frame_id)
            if self.cell_hooks_factory is not None and child_frame_id
            else None
        )

        token = _ACTIVE_DELEGATION.set((self._tree, child.child_id))
        agent: Any | None = None
        step_forwarder: _ChildStepForwarder | None = None
        scratch = None
        child_workspace = self.workspace
        if self.private_scratch and self.workspace:
            scratch = prepare_child_scratch(
                data_dir=self.cfg.data_dir,
                root_frame_id=str(self.parent_frame_id or "cli"),
                child_id=child.child_id,
                parent_workspace=self.workspace,
            )
            child_workspace = str(scratch.directory)
        try:
            from openai4s.agent.loop import Agent

            agent = Agent(
                cfg=child_cfg,
                max_turns=max_turns,
                verbose=False,
                use_skills=(
                    not execution_policy.restricted
                    or "skills" in execution_policy.allowed
                ),
                allow_delegate=(
                    child.depth < MAX_DEPTH
                    and execution_policy.allows_alias("delegation")
                ),
                frame_id=child_frame_id,
                delegate_depth=child.depth,
                cancellation=_ChildCancellation(child),
                context_policy=_SteeringContextPolicy(child_cfg, child, self._tree),
                workspace=child_workspace,
                read_isolation=self.read_isolation,
                cell_execution_hooks=compose_cell_hooks(recorder, capture_hooks),
                delegated_cell_hooks_factory=self.cell_hooks_factory,
                env=self.env,
                # Child kernel lifetimes become durable generation rows under
                # the child frame, so artifact environment provenance resolves
                # the child's real interpreter instead of assuming the daemon.
                generations=self.store,
            )
            agent.dispatcher.set_child_execution_policy(execution_policy)
            step_sink = self._tree.child_step_sink
            if step_sink is not None:
                # D8: relay the child's meaningful semantic steps (bounded,
                # decorated with the child identity) into the parent session's
                # step sink. Lives on the tree, so nested descendants forward
                # too; the CLI has no sink and stays silent.
                step_forwarder = _ChildStepForwarder(
                    step_sink,
                    child_id=child.child_id,
                    frame_id=child_frame_id,
                    name=child.name or spec.get("name"),
                    depth=child.depth,
                )
                agent.dispatcher.on_step = step_forwarder
            if recorder is not None:
                # The Agent creates its generation registrar inside run(), so
                # the reader is bound late and resolved per cell.
                recorder.bind_generation_source(agent.current_kernel_generation_id)
            if child.attach_agent(agent):
                return child.stopped_result()
            result = agent.run(_spec_to_task(spec))
        except BaseException as error:  # noqa: BLE001 - child failure is a result
            if child.stop_event.is_set():
                self._persist_status(child, "stopped")
                self._tree.emit("stopped", child)
                return child.stopped_result()
            detail = str(error) or type(error).__name__
            failed = {
                "child_id": child.child_id,
                "name": child.name,
                "stop_reason": "error",
                "task_status": "failed",
                "output": None,
                "completion_bullets": [],
                "error": detail,
                "frame_id": child_frame_id,
                "turns": None,
                "max_turns": max_turns,
                "environment": self._child_environment(child_frame_id),
                "limitations": [],
                "artifacts": self._child_artifacts(child_frame_id),
                "artifact_refs": self._publish_scratch(child, scratch, child_frame_id),
            }
            child.finish_failed(detail, failed)
            self._persist_status(child, "failed")
            self._tree.emit("failed", child)
            return failed
        finally:
            if agent is not None:
                child.detach_agent(agent)
            if step_forwarder is not None:
                step_forwarder.flush()
            _ACTIVE_DELEGATION.reset(token)

        # Cancellation wins every race, including a late host.submit_output.
        if child.stop_event.is_set() or result.get("stop_reason") == "cancelled":
            child.request_stop(child.stop_event_reason())
            self._persist_status(child, "stopped")
            self._tree.emit("stopped", child)
            return child.stopped_result()

        submitted = result.get("submitted_output") or {}
        artifact_refs = self._publish_scratch(child, scratch, child_frame_id)
        out = {
            "child_id": child.child_id,
            "name": spec.get("name"),
            "stop_reason": result.get("stop_reason"),
            "output": submitted.get("output"),
            "completion_bullets": submitted.get("completion_bullets", []),
            "final_message": result.get("final_message"),
            "frame_id": child_frame_id,
            "turns": result.get("turns"),
            "max_turns": max_turns,
            "environment": self._child_environment(child_frame_id),
            "limitations": _completion_limitations(submitted.get("output")),
            "artifacts": self._child_artifacts(child_frame_id),
            "artifact_refs": artifact_refs,
            "request_id": child.request_id,
            "attempt_id": child.attempt_id,
        }
        schema = spec.get("output_schema")
        if schema is not None:
            from openai4s.host.completion import validate_output_schema

            violation = validate_output_schema(out["output"], schema)
            if violation:
                error = f"output_schema violation: {violation}"
                out["error"] = error
                out["task_status"] = "failed"
                child.finish_failed(error, out)
                self._persist_status(child, "failed")
                self._tree.emit("failed", child)
                return out

        # Single-writer task_status derivation: the child's declaration is
        # input; require_artifacts is verified against the store and can only
        # downgrade the claim.
        missing = _missing_required_artifacts(
            _validated_require_artifacts(spec), out["artifacts"]
        )
        if missing is not None:
            out["missing_artifacts"] = missing
        out["task_status"] = _derive_task_status(
            result.get("stop_reason"), submitted, result.get("final_message"), missing
        )

        if result.get("stop_reason") == "max_turns":
            # A child that exhausted its turn budget did not finish its task;
            # 'done' would launder exhaustion into success. The envelope keeps
            # the raw stop_reason; the durable lifecycle records failure.
            error = "max_turns exhausted before completion"
            out["error"] = error
            child.finish_failed(error, out)
            self._persist_status(child, "failed")
            self._tree.emit("failed", child)
            return out

        # A stop arriving between schema validation and publication still wins.
        if not child.finish_done(out):
            self._persist_status(child, "stopped")
            self._tree.emit("stopped", child)
            return child.stopped_result()
        self._persist_status(child, "done")
        self._tree.emit("done", child)
        return out

    def _child_environment(self, child_frame_id: str | None) -> dict[str, Any]:
        """The environment actually in effect for one child, honestly sourced.

        The configured :class:`KernelEnvSpec` is the baseline; when the child
        worker really spawned it registered a durable kernel generation under
        the child frame, and that row (which also reflects a mid-run
        ``env_use`` switch) overrides the configuration.
        """
        env = self.env
        info: dict[str, Any] = {
            "python": env.python if env is not None else None,
            "env_name": env.env_name if env is not None else None,
            "env_root": env.env_root if env is not None else None,
            "r_env": env.r_env if env is not None else None,
            "generation_id": None,
        }
        if self.store is None or not child_frame_id:
            return info
        reader = getattr(self.store, "latest_kernel_generation", None)
        if not callable(reader):
            return info
        try:
            row = reader(child_frame_id, "python")
        except Exception:  # noqa: BLE001 - provenance must not fail the child
            row = None
        if isinstance(row, Mapping):
            info["generation_id"] = row.get("generation_id")
            environment = row.get("environment")
            if isinstance(environment, Mapping):
                if environment.get("interpreter"):
                    info["python"] = environment["interpreter"]
                if environment.get("environment_name") is not None:
                    info["env_name"] = environment["environment_name"]
                if environment.get("environment_root") is not None:
                    info["env_root"] = environment["environment_root"]
        return info

    def _child_artifacts(self, child_frame_id: str | None) -> list[str]:
        """Artifact names the store attributes to the child frame — never
        the child's own claims."""
        if self.store is None or not child_frame_id:
            return []
        reader = getattr(self.store, "artifact_names_for_frame", None)
        if not callable(reader):
            return []
        try:
            names = reader(child_frame_id)
        except Exception:  # noqa: BLE001 - evidence lookup must not fail the child
            return []
        return [str(name) for name in names or ()]

    def _publish_scratch(
        self,
        child: _Child,
        scratch: Any,
        child_frame_id: str | None,
    ) -> list[dict[str, Any]]:
        if scratch is None:
            return list(child.artifact_refs)
        refs = publish_child_outputs(
            scratch,
            store=self.store,
            child_frame_id=child_frame_id,
            root_frame_id=self.parent_frame_id,
        )
        child.artifact_refs = refs
        return refs

    def _run_with_retries(self, child: _Child) -> dict[str, Any]:
        """Run one child, then apply its bounded ``retries`` option.

        Each retry is a NEW child (a terminal delegation row is immutable and
        every attempt consumes session budget normally), re-run with the
        previous attempt's limitations appended to the request. The final
        attempt's envelope is what the caller sees. There is no other
        automatic retry anywhere in the delegation runtime.
        """
        result = self._run_one(child)
        budget = _retry_budget(child.spec)
        attempt = 0
        while (
            attempt < budget
            and result.get("task_status") in _RETRYABLE_STATUS
            and result.get("stop_reason") != "stopped"
            and not child.stop_event.is_set()
            and not child._retry_chain.cancelled()
        ):
            attempt += 1
            retry_spec = _retry_spec(child.spec, result, attempt)
            try:
                retry_child = self._tree.create_retry(
                    child,
                    spec=retry_spec,
                    parent_child_id=self.parent_child_id,
                    depth=self.depth,
                    parent_frame_id=self.parent_frame_id,
                    store=self.store,
                    direct_children=self._children,
                )
            except DelegationError:
                # Budget exhausted: the honest last result stands.
                break
            if retry_child is None:
                break
            child = retry_child
            result = self._run_one(retry_child)
        return result

    def __call__(self, spec: dict[str, Any]) -> Any:
        if self.depth >= MAX_DEPTH:
            raise DelegationError(
                f"agents at depth {MAX_DEPTH} are leaves and cannot delegate"
            )
        request = spec.get("request")
        wait = spec.get("wait", True)
        if isinstance(request, list):
            items, is_list = request, True
        else:
            items, is_list = [request], False
        if len(items) > FANOUT_CAP:
            raise DelegationError(
                f"delegate fanout {len(items)} exceeds cap {FANOUT_CAP}; "
                "split into multiple waves"
            )
        if self.cell_hooks_factory is not None and (not wait or len(items) != 1):
            # Stage 1's Web hook proves authorship by bracketing one child
            # Code Cell with a shared-workspace snapshot and durable capture.
            # Two children executing Cells concurrently can each observe the
            # other's writes, so no directory-diff algorithm can truthfully
            # attribute those bytes. Reject before reserving budget or creating
            # child rows. Synchronous single-child delegation, including a
            # nested chain, remains safe because the blocked ancestor cannot
            # mutate the workspace while its child executes.
            raise DelegationError(
                "parallel delegation is unavailable while trusted Artifact "
                "capture is enabled; delegate one child with wait=true"
            )
        if self.cell_hooks_factory is not None:
            admission = self._tree.trusted_capture_admission
            if admission is not None:
                try:
                    refusal = admission()
                except BaseException:
                    refusal = "trusted Artifact capture admission could not be verified"
                if refusal:
                    raise DelegationError(str(refusal))
        lease = nullcontext()
        if self.cell_hooks_factory is not None:
            lease_factory = self._tree.trusted_capture_lease
            if lease_factory is not None:
                try:
                    lease = lease_factory()
                except BaseException as error:
                    raise DelegationError(
                        "trusted Artifact capture admission could not be verified"
                    ) from error
                if not callable(getattr(lease, "__enter__", None)) or not callable(
                    getattr(lease, "__exit__", None)
                ):
                    raise DelegationError(
                        "trusted Artifact capture admission could not be verified"
                    )
        with lease:
            capture_gate = None
            if self.cell_hooks_factory is not None:
                capture_gate = self._tree.trusted_capture_gate
                if not capture_gate.acquire(blocking=False):
                    # Waiting would strand a parent Cell behind work it is
                    # itself awaiting, so contention is an admission refusal
                    # rather than a queue.
                    raise DelegationError(
                        "another delegated child owns trusted Artifact capture; "
                        "wait for it to finish before delegating again"
                    )
            try:
                return self._call_admitted(
                    spec, items, is_list=is_list, wait=bool(wait)
                )
            finally:
                if capture_gate is not None:
                    capture_gate.release()

    def _call_admitted(
        self,
        spec: dict[str, Any],
        items: list[Any],
        *,
        is_list: bool,
        wait: bool,
    ) -> Any:
        """Spawn after trusted-capture admission has become exclusive."""

        child_specs = [_normalize_item(item, spec) for item in items]
        if self.parent_child_id is not None:
            with self._tree.lock:
                parent = self._tree.children.get(self.parent_child_id)
            if parent is None:
                raise DelegationError("delegation parent is no longer available")
            child_specs = [
                _apply_parent_execution_ceiling(child_spec, parent.spec)
                for child_spec in child_specs
            ]
        if self.cell_hooks_factory is not None:
            child_specs = [
                _apply_trusted_capture_ceiling(child_spec) for child_spec in child_specs
            ]
        for child_spec in child_specs:
            try:
                child_execution_policy(child_spec)
                _child_turn_budget(
                    child_spec,
                    self.child_max_turns,
                    self.cfg.max_turns,
                )
            except (TypeError, ValueError) as error:
                raise DelegationError(
                    f"invalid child execution policy: {error}"
                ) from error
            _validated_require_artifacts(child_spec)
            if _retry_budget(child_spec) > 0 and not wait:
                # An asynchronous child is collected once through its own
                # handle; a retry's replacement result would be unobservable.
                raise DelegationError(
                    "retries requires wait: true — collect an asynchronous "
                    "child and re-delegate explicitly instead"
                )
        identity = delegation_identity(spec)
        if identity is not None and len(child_specs) == 1:
            with self._tree.lock:
                if self.parent_child_id is not None:
                    parent = self._tree.children.get(self.parent_child_id)
                    if (
                        parent is None
                        or parent.stop_event.is_set()
                        or parent.snapshot()["status"] != "running"
                    ):
                        raise DelegationError(
                            "cannot delegate from a stopped or finished child"
                        )
                reservation = self.budget.reserve_request(
                    depth=self.depth,
                    parent_child_id=self.parent_child_id,
                    parent_action_group_id=identity[0],
                    native_call_id=identity[1],
                    request_sha256=request_sha256(child_specs[0]),
                    payload=child_specs[0],
                )
                child, reused = self._child_from_reservation(
                    reservation, child_specs[0]
                )
            if reused:
                return self._reuse_child(child, wait=wait, is_list=is_list)
            children = [child]
        else:
            child_ids = self._reserve(len(items))
            children = []
            for child_id, child_spec in zip(child_ids, child_specs):
                child = _Child(
                    child_id,
                    child_spec.get("name"),
                    child_spec,
                    depth=self.depth + 1,
                    parent_child_id=self.parent_child_id,
                    parent_frame_id=self.parent_frame_id,
                    store=self.store,
                    budget=self.budget,
                    clock=self._tree.clock,
                )
                with self._tree.lock:
                    self._children[child.child_id] = child
                self._tree.register(child)
                children.append(child)

        # A pooled thread starts with whatever context it was created in --
        # `ThreadPoolExecutor.submit` copies nothing -- so a child would run
        # with no execution principal and no correlation id. The principal
        # matters most: a sub-agent reads user data through the same `host.*`
        # surface its parent does, and `resolve()` refuses an execution that
        # carries none, so without this every delegated read in team mode
        # fails closed. A child runs as its parent, never wider.
        #
        # One wrapper *per child*, not one for the fan-out: `carry_context`
        # captures a single `Context`, and a `Context` cannot be entered
        # twice concurrently -- sharing one across a fan-out raises "cannot
        # enter context: already entered" in every sibling but the first.
        if not wait:
            for child in children:
                child.set_future(self._pool.submit(carry_context(self._run_one), child))
            handles = [child.snapshot() for child in children]
            return handles if is_list else handles[0]

        if len(children) == 1:
            # On the caller's own thread, which already has the context.
            results = [self._run_with_retries(children[0])]
        else:
            futures = [
                self._pool.submit(carry_context(self._run_with_retries), child)
                for child in children
            ]
            for child, future in zip(children, futures):
                child.set_future(future)
            results = [future.result() for future in futures]
        return results if is_list else results[0]

    def children(self) -> list[dict[str, Any]]:
        with self._tree.lock:
            direct = list(self._children.values())
        return [child.snapshot() for child in direct]

    def set_event_sink(self, sink: Callable[[dict[str, Any]], None] | None) -> None:
        """(Re)point the shared tree's live delegation event sink."""

        self._tree.event_sink = sink

    def set_child_step_sink(
        self, sink: Callable[[dict[str, Any]], None] | None
    ) -> None:
        """(Re)point the shared tree's child step relay target."""

        self._tree.child_step_sink = sink

    def set_trusted_capture_admission(
        self, admission: Callable[[], str | None] | None
    ) -> None:
        """Update the shared tree's Web-owned capture precondition."""

        self._tree.trusted_capture_admission = admission

    def set_trusted_capture_lease(self, lease: Callable[[], Any] | None) -> None:
        """Update the shared tree's atomic capture lifetime."""

        self._tree.trusted_capture_lease = lease

    def collect(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        child_ids = spec.get("child_ids")
        timeout = spec.get("timeout")
        with self._tree.lock:
            targets = (
                list(self._children.values())
                if not child_ids
                else [
                    self._children[item] for item in child_ids if item in self._children
                ]
            )
        output: list[dict[str, Any]] = []
        for child in targets:
            future = child.future
            if future is not None:
                try:
                    future.result(timeout=timeout)
                except TimeoutError:
                    # A collect timeout is an observation, not child failure.
                    pass
                except CancelledError:
                    child.request_stop(child.stop_event_reason())
                except BaseException as error:  # noqa: BLE001
                    detail = str(error) or type(error).__name__
                    failed = {
                        "child_id": child.child_id,
                        "stop_reason": "error",
                        "task_status": "failed",
                        "output": None,
                        "error": detail,
                    }
                    child.finish_failed(detail, failed)
            output.append(self._enrich_result(child.result or child.snapshot(), child))
        return output

    def continue_child(self, child_id: str, *, wait: bool = True) -> dict[str, Any]:
        """Create the next attempt for a durable request.

        Restore never calls this. After a daemon restart the original child
        stays stopped until the caller continues it explicitly.
        """

        if self.cell_hooks_factory is not None and not wait:
            raise DelegationError(
                "parallel delegation is unavailable while trusted Artifact "
                "capture is enabled; delegate one child with wait=true"
            )
        reservation = self.budget.continue_request(
            child_id=child_id,
            depth=self.depth,
            parent_child_id=self.parent_child_id,
        )
        payload = dict(reservation.get("payload") or {})
        if not payload.get("request"):
            payload["request"] = payload.get("task") or payload.get("name") or child_id
        with self._tree.lock:
            child, _reused = self._child_from_reservation(reservation, payload)
        if wait:
            return self._enrich_result(self._run_with_retries(child), child)
        child.set_future(self._pool.submit(carry_context(self._run_one), child))
        return self._enrich_result(child.snapshot(), child)

    def materialize_child(
        self,
        child_id: str,
        *,
        paths: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Copy published child Artifact versions into the parent workspace.

        Never deletes Artifact versions.
        """

        if not self.workspace:
            raise DelegationError("materialize requires a parent workspace")
        with self._tree.lock:
            child = self._children.get(child_id) or self._tree.children.get(child_id)
        if child is None:
            child = self._restore_one_child(child_id)
        if child is None:
            raise KeyError(f"no such child {child_id!r}")
        refs = list(child.artifact_refs)
        if (
            not refs
            and child.result
            and isinstance(child.result.get("artifact_refs"), list)
        ):
            refs = list(child.result["artifact_refs"])
        return materialize_outputs_into_parent(
            parent_workspace=self.workspace,
            artifact_refs=refs,
            store=self.store,
            paths=paths,
        )

    def stop_child(self, child_id: str) -> dict[str, Any]:
        return self._stop_subtree(
            child_id,
            f"stopped by parent {self.parent_child_id or 'root'}",
        )

    def _stop_subtree(self, child_id: str, reason: str) -> dict[str, Any]:
        with self._tree.lock:
            if child_id not in self._children:
                raise KeyError(f"no such child {child_id!r}")
            stopped = self._tree.cancel_retry_subtrees([child_id], reason)
        self._signal_stopped(stopped, direct_ids={child_id})
        return self._children[child_id].snapshot()

    def _signal_stopped(
        self,
        stopped: Sequence[tuple[_Child, bool, Any | None, Future | None]],
        *,
        direct_ids: set[str],
    ) -> None:
        """Signal runtime handles after atomic tree cancellation is published."""

        for child, first, agent, future in stopped:
            if future is not None:
                future.cancel()
            if first and agent is not None:
                try:
                    agent.interrupt_foreground()
                except Exception:  # noqa: BLE001 - exact interrupt is best effort
                    pass
            if child.snapshot()["status"] == "stopped":
                self._persist_status(child, "stopped")
                self._tree.emit(
                    "stopped",
                    child,
                    propagated=child.child_id not in direct_ids,
                )

    def send_message(self, spec: dict[str, Any]) -> dict[str, Any]:
        child_id = spec["child_id"]
        with self._tree.lock:
            child = self._children.get(child_id)
        if child is None:
            raise KeyError(f"no such child {child_id!r}")
        message = _SteeringMessage(
            self._tree.next_message_id(),
            str(spec.get("message", "")),
            self._tree.clock(),
        )
        accepted, queued = child.enqueue(message)
        if not accepted:
            return {
                "ok": False,
                "child_id": child_id,
                "message_id": message.message_id,
                "status": "rejected",
                "queued": queued,
                "reason": f"child is {child.snapshot()['status']}",
            }
        self._tree.emit("steering_queued", child, message_id=message.message_id)
        return {
            "ok": True,
            "child_id": child_id,
            "message_id": message.message_id,
            "status": "queued",
            "queued": queued,
            "delivered": False,
        }

    def delegation_stats(self) -> dict[str, Any]:
        children = self._tree.subtree(self.parent_child_id)
        usage = self.budget.usage()
        stats: dict[str, Any] = {
            "total": len(children),
            "direct_total": len(self._children),
            "running": 0,
            "done": 0,
            "failed": 0,
            "stopped": 0,
            "pending": 0,
            "spawned_session": usage["spawned"],
            "active_session": usage["active"],
            "remaining_session_budget": usage["remaining"],
            "budget_root_frame_id": usage["root_frame_id"],
            "depth": self.depth,
        }
        for child in children:
            status = child.snapshot()["status"]
            stats[status] = stats.get(status, 0) + 1
        return stats

    def cancel_all(self, reason: str = "parent cancelled") -> list[str]:
        """Cancel every descendant owned by this runner's subtree."""

        with self._tree.lock:
            direct_ids = list(self._children)
            stopped = self._tree.cancel_retry_subtrees(direct_ids, reason)
        self._signal_stopped(stopped, direct_ids=set(direct_ids))
        return direct_ids

    def close(self, *, cancel: bool = False) -> None:
        if cancel:
            self.cancel_all("delegation runner closed")
        self._pool.shutdown(wait=False, cancel_futures=cancel)

    def _persist_status(self, child: _Child, status: str) -> None:
        snapshot = child.snapshot()
        frame_id = snapshot.get("frame_id")
        if child.store is None or not frame_id:
            return
        try:
            child.store.update_frame(frame_id, status=status)
        except Exception:  # noqa: BLE001 - state remains observable in memory
            pass


def _derive_task_status(
    stop_reason: Any,
    submitted: Mapping[str, Any],
    final_message: Any,
    missing_artifacts: list[str] | None,
) -> str:
    """The single authoritative task_status derivation for one child envelope.

    The child's declaration (``submit_output``'s top-level ``task_status`` or
    ``finalize_response``'s property, which lands inside ``output``) is input;
    machine checks can only DOWNGRADE it. ``max_turns`` is at best partial —
    failed when the child produced literally nothing. A terminated child that
    never submitted is failed regardless of what its transport said.
    """
    if stop_reason == "max_turns":
        return "partial" if (submitted or final_message) else "failed"
    if not submitted and stop_reason != "submitted":
        return "failed"
    declared = submitted.get("task_status")
    if declared is None:
        output = submitted.get("output")
        if isinstance(output, Mapping):
            declared = output.get("task_status")
    if not isinstance(declared, str) or declared not in _TASK_STATUS_RANK:
        declared = "completed"
    status = declared
    if missing_artifacts:
        # Required artifacts the store cannot attribute to this child cap the
        # status at partial; a declared blocked/failed already ranks lower.
        if _TASK_STATUS_RANK[status] < _TASK_STATUS_RANK["partial"]:
            status = "partial"
    return status


def _completion_limitations(output: Any) -> list[str]:
    """The child's own structured limitations, normalized to a string list."""
    if not isinstance(output, Mapping):
        return []
    for key in _LIMITATION_ALIASES:
        value = output.get(key)
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        if isinstance(value, (list, tuple)):
            items = [str(item).strip() for item in value if str(item).strip()]
            if items:
                return items
    return []


def _validated_require_artifacts(spec: Mapping[str, Any]) -> list[str] | None:
    """Parse ``require_artifacts``: exact names or trailing-star globs only."""
    raw = spec.get("require_artifacts")
    if raw is None:
        return None
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        raise DelegationError(
            "require_artifacts must be a list of artifact filenames "
            "(exact names or trailing-star globs)"
        )
    patterns: list[str] = []
    for item in raw:
        name = str(item or "").strip()
        if not name:
            raise DelegationError(
                "require_artifacts must contain only non-empty filenames"
            )
        if "*" in name[:-1]:
            raise DelegationError(
                f"require_artifacts pattern {name!r} is invalid: '*' is only "
                "supported as a trailing glob"
            )
        patterns.append(name)
    return patterns


def _missing_required_artifacts(
    patterns: list[str] | None, produced: list[str]
) -> list[str] | None:
    """Which required names/globs no store-attributed artifact satisfies."""
    if patterns is None:
        return None
    missing: list[str] = []
    for pattern in patterns:
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            if not any(name.startswith(prefix) for name in produced):
                missing.append(pattern)
        elif pattern not in produced:
            missing.append(pattern)
    return missing


def _retry_budget(spec: Mapping[str, Any]) -> int:
    """The clamped 0..2 bounded-retry option; malformed values are refused."""
    raw = spec.get("retries")
    if raw is None:
        return 0
    if isinstance(raw, bool):
        raise DelegationError("retries must be an integer (clamped to 0-2)")
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        raise DelegationError("retries must be an integer (clamped to 0-2)") from None
    return max(0, min(2, parsed))


def _retry_spec(
    spec: Mapping[str, Any], previous: Mapping[str, Any], attempt: int
) -> dict[str, Any]:
    """The re-run spec: same task, previous limitations appended as context."""
    retry = dict(spec)
    # The retry loop owns the budget; a nested reading of the option must not
    # multiply it.
    retry.pop("retries", None)
    lines = [
        f"[Retry {attempt}] The previous attempt ended with "
        f"task_status={previous.get('task_status')}."
    ]
    if previous.get("error"):
        lines.append(f"Previous error: {previous['error']}")
    limitations = previous.get("limitations") or []
    if limitations:
        lines.append("Previous limitations:")
        lines.extend(f"- {item}" for item in limitations)
    if previous.get("missing_artifacts"):
        lines.append(
            "Missing required artifacts: "
            + ", ".join(str(item) for item in previous["missing_artifacts"])
        )
    note = "\n".join(lines)
    request = retry.get("request")
    if isinstance(request, str):
        retry["request"] = request + "\n\n" + note
    elif isinstance(request, Mapping):
        inner = dict(request)
        for key in ("task", "prompt"):
            if inner.get(key):
                inner[key] = f"{inner[key]}\n\n{note}"
                break
        else:
            inner["task"] = note
        retry["request"] = inner
    else:
        summary = str(retry.get("context_summary") or "")
        retry["context_summary"] = (summary + "\n\n" + note).strip()
    return retry


def _normalize_item(item: Any, parent_spec: dict[str, Any]) -> dict[str, Any]:
    inherited = {
        key: value
        for key in (
            "task",
            "name",
            "context_summary",
            "output_schema",
            "model",
            "provider",
            "steps",
            "max_steps",
            "max_turns",
            "permissions",
            "capabilities",
            "skill_names",
            "connectors",
            "unrestricted",
            "require_artifacts",
            "retries",
        )
        if (value := parent_spec.get(key)) is not None
    }
    if isinstance(item, str):
        return {"request": item, **inherited}
    if isinstance(item, dict):
        # Explicit null == absent == default, on the nested door too: the
        # top-level wire codec already drops None values, so a None inside a
        # fan-out item must inherit rather than clobber the inherited value
        # (or crash the turn-budget parser on a present-but-None steps).
        item = {key: value for key, value in item.items() if value is not None}
        normalized = dict(inherited)
        normalized.update(item)
        # `update` lets a child REPLACE what it inherited, which for a resource
        # allowlist means delegating is the way out of it: a child restricted
        # to one Skill could name three and get three. Narrow instead, so the
        # child's own list can only ever be a subset of its parent's. `None`
        # on either side inherits the other, which is what the tri-state means.
        from openai4s.host import resource_allowlist

        for key in ("skill_names", "connectors"):
            if key in inherited or key in item:
                narrowed = resource_allowlist.narrow(inherited.get(key), item.get(key))
                if narrowed is None:
                    normalized.pop(key, None)
                else:
                    normalized[key] = sorted(narrowed)
        return normalized
    raise DelegationError(
        f"delegate: each request item must be str or dict, got {type(item).__name__}"
    )


def _spec_to_task(spec: dict[str, Any]) -> str:
    parts: list[str] = []
    if spec.get("task"):
        parts.append(str(spec["task"]))
    request = spec.get("request")
    if isinstance(request, str):
        parts.append(request)
    elif isinstance(request, dict):
        if request.get("task"):
            parts.append(str(request["task"]))
        elif request.get("prompt"):
            parts.append(str(request["prompt"]))
        else:
            parts.append(str(request))
    if spec.get("context_summary"):
        parts.append(f"\nContext from the parent agent:\n{spec['context_summary']}")
    return "\n".join(part for part in parts if part).strip() or "(no task provided)"


def _apply_parent_execution_ceiling(
    spec: Mapping[str, Any], parent_spec: Mapping[str, Any]
) -> dict[str, Any]:
    """Prevent a nested child from widening its parent's authority."""

    merged = dict(spec)
    parent_policy = child_execution_policy(parent_spec)
    child_policy = child_execution_policy(merged)
    if parent_policy.restricted:
        if merged.get("unrestricted") is True:
            raise DelegationError(
                "nested child cannot widen a restricted parent capability policy"
            )
        if "capabilities" not in merged:
            merged["capabilities"] = sorted(parent_policy.allowed)
        else:
            denied = sorted(
                capability
                for capability in child_policy.allowed
                if not parent_policy.permits_capability(capability)
            )
            if denied:
                raise DelegationError(
                    "nested child capabilities exceed parent policy: "
                    + ", ".join(denied)
                )
        merged["unrestricted"] = False

    severity = {"allow": 0, "ask": 1, "deny": 2}
    combined = dict(parent_policy.permissions)
    for key, decision in child_policy.permissions.items():
        parent_decision = parent_policy.decision(key)
        if (
            parent_decision is not None
            and severity[parent_decision] > severity[decision]
        ):
            decision = parent_decision
        combined[key] = decision
    if combined:
        merged["permissions"] = combined

    # Resource allowlists are narrowed here too, not only in `_normalize_item`.
    # The two narrow against different things and only this one bounds a
    # grandchild: `_normalize_item` narrows an item against the *delegate()
    # call's own kwargs*, while this runs on the nested path and narrows
    # against the parent CHILD's spec. Without it, a child restricted to one
    # Skill or connector could delegate a grandchild that named three and get
    # three — the same widening the `_normalize_item` fix closed, one level
    # further down.
    from openai4s.host import resource_allowlist

    for key in ("skill_names", "connectors"):
        if key in merged or key in parent_spec:
            narrowed = resource_allowlist.narrow(parent_spec.get(key), merged.get(key))
            if narrowed is None:
                merged.pop(key, None)
            else:
                merged[key] = sorted(narrowed)
    return merged


def _apply_trusted_capture_ceiling(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Forbid asynchronous child kernels that outlive capture ownership."""

    merged = dict(spec)
    permissions = dict(child_execution_policy(merged).permissions)
    # This is a mandatory Stage 1 provenance ceiling, not a caller preference.
    # It is applied after nested-policy merging so neither an unrestricted
    # child nor an explicit allow can widen it back open.
    permissions["background"] = "deny"
    merged["permissions"] = permissions
    return merged


def _child_config(cfg: Config, spec: Mapping[str, Any]) -> Config:
    """Copy model/provider overrides without mutating the parent configuration."""

    child = dataclasses.replace(cfg)
    llm = dataclasses.replace(cfg.llm)
    model = spec.get("model")
    if isinstance(model, Mapping):
        for key in (
            "provider",
            "model",
            "base_url",
            "max_tokens",
            "temperature",
            "timeout_s",
        ):
            if key in model and model[key] is not None:
                setattr(llm, key, model[key])
    elif model:
        llm.model = str(model)
    if spec.get("provider"):
        llm.provider = str(spec["provider"])
    child.llm = llm
    return child


def _child_turn_budget(
    spec: Mapping[str, Any], configured: int | None, default: int
) -> int:
    for key in ("max_turns", "max_steps", "steps"):
        if key not in spec:
            continue
        value = spec.get(key)
        if isinstance(value, bool) or value is None:
            raise DelegationError(f"{key} must be a positive integer")
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            raise DelegationError(f"{key} must be a positive integer") from None
        if parsed <= 0:
            raise DelegationError(f"{key} must be a positive integer")
        return parsed
    for value in (configured, default):
        if value is not None and not isinstance(value, bool) and int(value) > 0:
            return int(value)
    return max(1, int(default))


def _public_overrides(spec: Mapping[str, Any]) -> dict[str, Any]:
    overrides = {
        key: spec[key]
        for key in (
            "model",
            "provider",
            "steps",
            "max_steps",
            "max_turns",
            "permissions",
            "capabilities",
            "skill_names",
            "connectors",
            "unrestricted",
            "require_artifacts",
            "retries",
        )
        if key in spec
    }
    model = overrides.get("model")
    if isinstance(model, Mapping):
        overrides["model"] = {
            key: model[key]
            for key in (
                "provider",
                "model",
                "base_url",
                "max_tokens",
                "temperature",
                "timeout_s",
            )
            if key in model
        }
    return overrides


__all__ = [
    "DelegationBudget",
    "DelegationConflictError",
    "DelegationError",
    "DelegationRunner",
    "FANOUT_CAP",
    "MAX_DEPTH",
    "SESSION_CAP",
]
