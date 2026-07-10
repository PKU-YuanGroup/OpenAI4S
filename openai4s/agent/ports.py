"""Dependency ports for the pure agent engine."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Protocol, Sequence

from .actions import Action
from .events import AgentEvent
from .models import ExecutionOutcome, ModelReply, RunState


class ModelPort(Protocol):
    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        on_delta: Callable[[str], None],
    ) -> ModelReply | Mapping[str, Any]: ...


class ContextPolicy(Protocol):
    def prepare(self, state: RunState) -> Sequence[Mapping[str, Any]]: ...


class ActionExecutor(Protocol):
    def execute(
        self, action: Action | None, reply: ModelReply, state: RunState
    ) -> ExecutionOutcome: ...


class EventSink(Protocol):
    def emit(self, event: AgentEvent) -> None: ...


class CancellationPort(Protocol):
    def cancelled(self) -> bool: ...


class ReplyInterceptor(Protocol):
    def intercept(
        self, reply: ModelReply, state: RunState
    ) -> ModelReply | Mapping[str, Any] | None: ...


class PassthroughContext:
    def prepare(self, state: RunState) -> Sequence[Mapping[str, Any]]:
        return state.messages


class NullEventSink:
    def emit(self, event: AgentEvent) -> None:
        del event


class NeverCancelled:
    def cancelled(self) -> bool:
        return False


class IdentityReplyInterceptor:
    def intercept(self, reply: ModelReply, state: RunState) -> ModelReply:
        del state
        return reply
