"""The single provider-neutral outer agent loop."""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, cast

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
    CompletionPort,
    ContextPolicy,
    EventSink,
    IdentityReplyInterceptor,
    ModelPort,
    NeverCancelled,
    NoCompletion,
    NullEventSink,
    PassthroughContext,
    ReplyInterceptor,
)
from .progress_circuit import (
    NO_PROGRESS_STOP_REASON,
    ProgressCircuit,
    attach_progress_circuit,
    circuit_from_state,
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
        completion: CompletionPort | None = None,
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
        self.completion = completion or NoCompletion()
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
            completion = self.completion.completion()
            if completion is not None:
                return self._finish(state, completion, "submitted")
            circuit = self._circuit(state)
            if circuit.tripped:
                return self._finish(
                    state,
                    None,
                    NO_PROGRESS_STOP_REASON,
                    progress_reason=circuit.trip_reason,
                )
            turn = state.turn
            self.event_sink.emit(TurnStarted(turn))
            prepared = self.context_policy.prepare(state)
            state.messages[:] = [dict(message) for message in prepared]

            def on_delta(text: str) -> None:
                self.event_sink.emit(TextDelta(text, turn))

            raw_reply = self.model.complete(state.messages, on_delta)
            reply = self._reply(raw_reply)
            if reply.finish_reason == "cancelled":
                # A model that observed cancellation mid-call answers with the
                # canonical no-op reply. It is not history: appending its empty
                # assistant message would leave a non-final empty turn in a
                # session that outlives this run (the Web session list and the
                # Action Ledger both alias ``state.messages``), and the
                # Anthropic and Gemini wires reject exactly that on every
                # later call. Finish here, before the reply is recorded or
                # routed, the same way a cancellation seen between turns does.
                return self._finish(state, None, "cancelled")
            intercepted = self.reply_interceptor.intercept(reply, state)
            if intercepted is not None:
                reply = self._reply(intercepted)
            state.last_reply = reply
            state.messages.append(dict(reply.assistant_message))
            self.event_sink.emit(ReplyReceived(reply, turn))
            action = route_action(reply.content, reply.tool_calls)
            state.last_action = action
            # A trip is enforced at the top of the next iteration, before the
            # model is called again: every threshold trips inside the
            # observe_* call that reaches it, so a refusal here would never
            # fire -- and if it did, it would drop the routed reply from the
            # Action Ledger (ActionRouted is what records it) and from the
            # team usage ledger.
            circuit.observe_routed_action(action, reply)
            self.event_sink.emit(ActionRouted(action, turn))
            outcome = self.executor.execute(action, reply, state)
            if not isinstance(outcome, ExecutionOutcome):
                raise TypeError("executor must return ExecutionOutcome")
            circuit.observe_execution(action, outcome)
            state.messages.extend(dict(message) for message in outcome.history_messages)
            state.turn += 1
            self.event_sink.emit(OutcomeProduced(outcome, turn))
            if self.cancellation.cancelled():
                return self._finish(state, None, "cancelled")
            if outcome.stop_reason:
                return self._finish(state, outcome.completion, outcome.stop_reason)
            completion = outcome.completion
            if completion is None:
                completion = self.completion.completion()
            if completion is not None:
                return self._finish(state, completion, "submitted")
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
        return (
            value if isinstance(value, ModelReply) else ModelReply.from_mapping(value)
        )

    def _circuit(self, state: RunState) -> ProgressCircuit:
        existing = circuit_from_state(state)
        if existing is not None:
            return existing
        binder = getattr(self.executor, "bind_progress_circuit", None)
        if callable(binder):
            cast(Callable[[RunState], None], binder)(state)
            existing = circuit_from_state(state)
            if existing is not None:
                return existing
        circuit = ProgressCircuit()
        attach_progress_circuit(state, circuit)
        return circuit

    def _finish(
        self,
        state: RunState,
        completion: Any,
        stop_reason: str,
        *,
        progress_reason: str | None = None,
    ) -> EngineResult:
        if stop_reason != NO_PROGRESS_STOP_REASON:
            progress_reason = None
            completion_value = completion
        else:
            completion_value = None
        result = EngineResult(
            tuple(dict(message) for message in state.messages),
            completion_value,
            stop_reason,
            state.turn,
            state.last_reply,
            progress_reason=progress_reason,
        )
        self.event_sink.emit(RunFinished(result))
        return result
