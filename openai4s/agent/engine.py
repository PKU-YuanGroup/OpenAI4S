"""The single provider-neutral outer agent loop."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .actions import route_action
from .events import (
    ActionRouted,
    OutcomeProduced,
    ReplyReceived,
    RunFinished,
    RunStarted,
    TextDelta,
    TurnStarted,
)
from .models import EngineResult, ExecutionOutcome, ModelReply, RunState
from .ports import (
    ActionExecutor,
    CancellationPort,
    ContextPolicy,
    EventSink,
    IdentityReplyInterceptor,
    ModelPort,
    NeverCancelled,
    NullEventSink,
    PassthroughContext,
    ReplyInterceptor,
)


class AgentEngine:
    """Coordinate context, model, routing, execution, and terminal states."""

    def __init__(
        self,
        model: ModelPort,
        executor: ActionExecutor,
        *,
        context_policy: ContextPolicy | None = None,
        event_sink: EventSink | None = None,
        cancellation: CancellationPort | None = None,
        reply_interceptor: ReplyInterceptor | None = None,
        max_turns: int = 32,
    ) -> None:
        if max_turns < 0:
            raise ValueError("max_turns must be non-negative")
        self.model = model
        self.executor = executor
        self.context_policy = context_policy or PassthroughContext()
        self.event_sink = event_sink or NullEventSink()
        self.cancellation = cancellation or NeverCancelled()
        self.reply_interceptor = reply_interceptor or IdentityReplyInterceptor()
        self.max_turns = max_turns

    def run(
        self,
        messages_or_state: RunState | Iterable[Mapping[str, Any]],
        *,
        max_turns: int | None = None,
    ) -> EngineResult:
        state = self._state(messages_or_state, max_turns)
        self.event_sink.emit(RunStarted(state.max_turns, len(state.messages)))
        while state.turn < state.max_turns:
            if self.cancellation.cancelled():
                return self._finish(state, None, "cancelled")
            turn = state.turn
            self.event_sink.emit(TurnStarted(turn))
            prepared = self.context_policy.prepare(state)
            state.messages[:] = [dict(message) for message in prepared]

            def on_delta(text: str) -> None:
                self.event_sink.emit(TextDelta(text, turn))

            raw_reply = self.model.complete(state.messages, on_delta)
            reply = self._reply(raw_reply)
            intercepted = self.reply_interceptor.intercept(reply, state)
            if intercepted is not None:
                reply = self._reply(intercepted)
            state.last_reply = reply
            state.messages.append(dict(reply.assistant_message))
            self.event_sink.emit(ReplyReceived(reply, turn))
            action = route_action(reply.content, reply.tool_calls)
            state.last_action = action
            self.event_sink.emit(ActionRouted(action, turn))
            outcome = self.executor.execute(action, reply, state)
            if not isinstance(outcome, ExecutionOutcome):
                raise TypeError("executor must return ExecutionOutcome")
            state.messages.extend(dict(message) for message in outcome.history_messages)
            state.turn += 1
            self.event_sink.emit(OutcomeProduced(outcome, turn))
            if outcome.stop_reason:
                return self._finish(state, outcome.completion, outcome.stop_reason)
            if outcome.completion is not None:
                return self._finish(state, outcome.completion, "submitted")
        return self._finish(state, None, "max_turns")

    def _state(
        self,
        value: RunState | Iterable[Mapping[str, Any]],
        max_turns: int | None,
    ) -> RunState:
        if isinstance(value, RunState):
            state = value
            if max_turns is not None:
                state.max_turns = max_turns
        else:
            limit = self.max_turns if max_turns is None else max_turns
            state = RunState([dict(message) for message in value], limit)
        if state.max_turns < 0:
            raise ValueError("max_turns must be non-negative")
        return state

    @staticmethod
    def _reply(value: ModelReply | Mapping[str, Any]) -> ModelReply:
        return value if isinstance(value, ModelReply) else ModelReply.from_mapping(value)

    def _finish(
        self, state: RunState, completion: Any, stop_reason: str
    ) -> EngineResult:
        result = EngineResult(
            tuple(dict(message) for message in state.messages),
            completion,
            stop_reason,
            state.turn,
            state.last_reply,
        )
        self.event_sink.emit(RunFinished(result))
        return result
