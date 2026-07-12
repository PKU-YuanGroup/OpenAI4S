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
from typing import Any, Callable, Mapping, Sequence

from openai4s.agent.runtime import CompactionPolicy
from openai4s.config import Config
from openai4s.host.delegation_policy import child_execution_policy

FANOUT_CAP = 48
SESSION_CAP = 1000
MAX_DEPTH = 4

DELEGATION_PROCESS_INSTANCE_ID = f"delegation-{uuid.uuid4()}"

_TERMINAL = frozenset({"done", "failed", "stopped"})


class DelegationError(RuntimeError):
    pass


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
    ) -> None:
        self.child_id = child_id
        self.name = name
        self.spec = spec
        self.depth = depth
        self.parent_child_id = parent_child_id
        self.parent_frame_id = parent_frame_id
        self.store = store
        self.budget = budget
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
                "output": output,
                "error": self.error,
                "depth": self.depth,
                "parent_child_id": self.parent_child_id,
                "parent_frame_id": self.parent_frame_id,
                "frame_id": self.frame_id,
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
                "stop_reason": (
                    self._stop_reason if self.status == "stopped" else None
                ),
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
        return child

    def _mark_stopped_locked(self, reason: str) -> None:
        was_terminal = self.status in _TERMINAL
        self.status = "stopped"
        self.error = None
        self.finished_at = self.finished_at or self._clock()
        self.result = {
            "child_id": self.child_id,
            "name": self.name,
            "stop_reason": "stopped",
            "output": None,
            "completion_bullets": [],
            "error": None,
            "reason": reason,
            "frame_id": self.frame_id,
        }
        self._discard_queued_locked()
        if not was_terminal:
            self._release_budget_locked()

    def _discard_queued_locked(self) -> None:
        self._inbox.clear()
        for message in self._messages:
            message.discard()

    def _release_budget_locked(self) -> None:
        if self._budget_released:
            return
        self._budget_released = True
        self.budget.release()


class _DelegationTree:
    """Shared identities, budget, lineage, and event projection for one tree."""

    def __init__(
        self,
        *,
        budget: DelegationBudget | None = None,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
        persistence_sink: Callable[[_Child], None] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.lock = threading.RLock()
        self.budget = budget or DelegationBudget()
        self.message_sequence = 0
        self.children: dict[str, _Child] = {}
        self.event_sink = event_sink
        self.persistence_sink = persistence_sink
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


_ACTIVE_DELEGATION: contextvars.ContextVar[
    tuple[_DelegationTree, str] | None
] = contextvars.ContextVar("openai4s_active_delegation", default=None)


class _ChildCancellation:
    def __init__(self, child: _Child) -> None:
        self._child = child

    def cancelled(self) -> bool:
        return self._child.stop_event.is_set()


class _SteeringContextPolicy:
    """Inject newly delivered parent messages before each child model turn."""

    def __init__(self, cfg: Config, child: _Child, tree: _DelegationTree) -> None:
        self._base = CompactionPolicy(cfg)
        self._child = child
        self._tree = tree

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
        budget: DelegationBudget | None = None,
        delegation_tree: _DelegationTree | None = None,
        parent_child_id: str | None = None,
        owner_instance_id: str | None = None,
        runner_instance_id: str | None = None,
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
            persistence_sink=persistence_sink,
        )
        self.budget = self._tree.budget
        if event_sink is not None and self._tree.event_sink is None:
            self._tree.event_sink = event_sink
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

        token = _ACTIVE_DELEGATION.set((self._tree, child.child_id))
        agent: Any | None = None
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
            )
            agent.dispatcher.set_child_execution_policy(execution_policy)
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
                "output": None,
                "completion_bullets": [],
                "error": detail,
                "frame_id": child_frame_id,
            }
            child.finish_failed(detail, failed)
            self._persist_status(child, "failed")
            self._tree.emit("failed", child)
            return failed
        finally:
            if agent is not None:
                child.detach_agent(agent)
            _ACTIVE_DELEGATION.reset(token)

        # Cancellation wins every race, including a late host.submit_output.
        if child.stop_event.is_set() or result.get("stop_reason") == "cancelled":
            child.request_stop(child.stop_event_reason())
            self._persist_status(child, "stopped")
            self._tree.emit("stopped", child)
            return child.stopped_result()

        submitted = result.get("submitted_output") or {}
        out = {
            "child_id": child.child_id,
            "name": spec.get("name"),
            "stop_reason": result.get("stop_reason"),
            "output": submitted.get("output"),
            "completion_bullets": submitted.get("completion_bullets", []),
            "final_message": result.get("final_message"),
            "frame_id": child_frame_id,
        }
        schema = spec.get("output_schema")
        if schema is not None:
            from openai4s.host.completion import validate_output_schema

            violation = validate_output_schema(out["output"], schema)
            if violation:
                error = f"output_schema violation: {violation}"
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
        child_ids = self._reserve(len(items))

        children: list[_Child] = []
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

        if not wait:
            for child in children:
                child.set_future(self._pool.submit(self._run_one, child))
            handles = [child.snapshot() for child in children]
            return handles if is_list else handles[0]

        if len(children) == 1:
            results = [self._run_one(children[0])]
        else:
            futures = [self._pool.submit(self._run_one, child) for child in children]
            for child, future in zip(children, futures):
                child.set_future(future)
            results = [future.result() for future in futures]
        return results if is_list else results[0]

    def children(self) -> list[dict[str, Any]]:
        with self._tree.lock:
            direct = list(self._children.values())
        return [child.snapshot() for child in direct]

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
                        "output": None,
                        "error": detail,
                    }
                    child.finish_failed(detail, failed)
            output.append(child.result or child.snapshot())
        return output

    def stop_child(self, child_id: str) -> dict[str, Any]:
        return self._stop_subtree(
            child_id,
            f"stopped by parent {self.parent_child_id or 'root'}",
        )

    def _stop_subtree(self, child_id: str, reason: str) -> dict[str, Any]:
        with self._tree.lock:
            if child_id not in self._children:
                raise KeyError(f"no such child {child_id!r}")
        affected = self._tree.descendants(child_id)
        for child in affected:
            first, agent, future = child.request_stop(reason)
            if future is not None:
                future.cancel()
            if first and agent is not None:
                try:
                    agent.interrupt_foreground()
                except Exception:  # noqa: BLE001 - exact interrupt is best effort
                    pass
            if child.snapshot()["status"] == "stopped":
                self._persist_status(child, "stopped")
                self._tree.emit("stopped", child, propagated=child.child_id != child_id)
        return self._children[child_id].snapshot()

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

        direct_ids = [child["child_id"] for child in self.children()]
        for child_id in direct_ids:
            self._stop_subtree(child_id, reason)
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
        )
        if (value := parent_spec.get(key)) is not None
    }
    if isinstance(item, str):
        return {"request": item, **inherited}
    if isinstance(item, dict):
        normalized = dict(inherited)
        normalized.update(item)
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
    "DelegationError",
    "DelegationRunner",
    "FANOUT_CAP",
    "MAX_DEPTH",
    "SESSION_CAP",
]
