"""The single provider-neutral outer agent loop."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any, Callable, Iterable, Mapping, Sequence, cast

from .actions import route_action, unexecuted_cell_note
from .events import (
    ActionRouted,
    OutcomeProduced,
    ReplyReceived,
    RunFinished,
    RunStarted,
    TextDelta,
    TurnStarted,
)
from .models import EngineResult, EngineStep, ExecutionOutcome, ModelReply, RunState
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
from .recovery import recovery_message


def _detach_tool_spec(spec: Any) -> Mapping[str, Any]:
    """Normalize a ToolSpec-like value without importing the tool catalog."""

    if isinstance(spec, Mapping):
        value = deepcopy(dict(spec))
    else:
        try:
            value = {
                "name": getattr(spec, "name"),
                "description": getattr(spec, "description"),
                "input_schema": getattr(spec, "input_schema"),
                "strict": getattr(spec, "strict", False),
            }
        except AttributeError as exc:
            raise TypeError(
                "tool schema must be a mapping or ToolSpec-like value"
            ) from exc
        value = deepcopy(value)
    if not isinstance(value.get("name"), str) or not value["name"].strip():
        raise TypeError("tool schema name must be a non-empty string")
    if not isinstance(value.get("description"), str):
        raise TypeError("tool schema description must be a string")
    if not isinstance(value.get("input_schema"), Mapping):
        raise TypeError("tool schema input_schema must be a mapping")
    value["input_schema"] = deepcopy(dict(value["input_schema"]))
    value["strict"] = bool(value.get("strict", False))
    return value


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
        tool_schema_provider: Callable[[RunState], Sequence[Any]] | None = None,
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
        self.tool_schema_provider = tool_schema_provider
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
            step = self.step(state)
            if step.terminal:
                return self._finish(
                    state,
                    step.completion,
                    step.stop_reason or "cancelled",
                    progress_reason=(
                        self._circuit(state).trip_reason
                        if step.stop_reason == NO_PROGRESS_STOP_REASON
                        else None
                    ),
                )
        return self._finish(state, None, "max_turns")

    def prepare_decision(self, state: RunState) -> tuple[Mapping[str, Any], ...]:
        """Return the exact context that the next policy call will receive.

        The returned messages are detached dictionaries.  A trainer may
        tokenize or persist them without being able to mutate engine state.
        Passing the same tuple to :meth:`step` avoids preparing context twice.
        """

        prepared = tuple(
            dict(message) for message in self.context_policy.prepare(state)
        )
        state.messages[:] = [dict(message) for message in prepared]
        return prepared

    def prepare_tools(self, state: RunState) -> tuple[Mapping[str, Any], ...]:
        """Return detached provider-neutral tools for an external policy.

        Local runtimes may inject the same dynamic projection they give their
        built-in model.  Lightweight embeddings can instead expose a
        ``policy_tools(messages)`` method on their executor.  The returned
        declarations are copied so a sampler cannot mutate a live catalog.
        """

        provider = self.tool_schema_provider
        if provider is not None:
            raw = provider(state)
        else:
            fallback = getattr(self.executor, "policy_tools", None)
            if not callable(fallback):
                return ()
            raw = fallback(tuple(dict(message) for message in state.messages))
        return tuple(_detach_tool_spec(spec) for spec in (raw or ()))

    def step(
        self,
        state: RunState,
        sampled_reply: ModelReply | Mapping[str, Any] | None = None,
        *,
        observation: Iterable[Mapping[str, Any]] | None = None,
    ) -> EngineStep:
        """Execute one decision using the built-in or an external policy.

        When ``sampled_reply`` is supplied, no model call is made.  This is the
        white-box RL boundary: an external sampler can return token telemetry
        in ``ModelReply.extra`` and the existing execution/ledger path still
        handles routing, permissions, kernels and completion.
        """

        if state.turn >= state.max_turns:
            return EngineStep(
                state.turn,
                tuple(dict(m) for m in state.messages),
                None,
                None,
                None,
                stop_reason="max_turns",
            )
        if self.cancellation.cancelled():
            return EngineStep(
                state.turn,
                tuple(dict(m) for m in state.messages),
                None,
                None,
                None,
                stop_reason="cancelled",
            )
        completion = self.completion.completion()
        if completion is not None:
            return EngineStep(
                state.turn,
                tuple(dict(m) for m in state.messages),
                None,
                None,
                None,
                completion=completion,
                stop_reason="submitted",
            )
        circuit = self._circuit(state)
        if circuit.tripped:
            return EngineStep(
                state.turn,
                tuple(dict(m) for m in state.messages),
                None,
                None,
                None,
                stop_reason=NO_PROGRESS_STOP_REASON,
            )
        turn = state.turn
        self.event_sink.emit(TurnStarted(turn))
        prepared = tuple(
            dict(message)
            for message in (
                observation if observation is not None else self.prepare_decision(state)
            )
        )
        state.messages[:] = [dict(message) for message in prepared]
        decision_hook = getattr(self.event_sink, "propose", None)
        if callable(decision_hook):
            decision_hook(turn, prepared)

        def on_delta(text: str) -> None:
            self.event_sink.emit(TextDelta(text, turn))

        raw_reply = sampled_reply
        if raw_reply is None:
            raw_reply = self.model.complete(state.messages, on_delta)
        reply = self._reply(raw_reply)
        if reply.finish_reason == "cancelled":
            return EngineStep(turn, prepared, None, None, None, stop_reason="cancelled")
        intercepted = self.reply_interceptor.intercept(reply, state)
        if intercepted is not None:
            reply = self._reply(intercepted)
        state.last_reply = reply
        state.messages.append(dict(reply.assistant_message))
        self.event_sink.emit(ReplyReceived(reply, turn))
        action = route_action(reply.content, reply.tool_calls)
        state.last_action = action
        circuit.observe_routed_action(action, reply)
        self.event_sink.emit(ActionRouted(action, turn))
        # Stable join key for nested execution telemetry. This is metadata for
        # the runtime boundary, not an additional policy action.
        decision_id = getattr(self.event_sink, "decision_id", None)
        state.metadata["rl_decision_id"] = (
            str(decision_id(turn)) if callable(decision_id) else f"decision-{turn}"
        )
        outcome = self.executor.execute(action, reply, state)
        if not isinstance(outcome, ExecutionOutcome):
            raise TypeError("executor must return ExecutionOutcome")
        outcome = self._note_unexecuted_cell(
            outcome, unexecuted_cell_note(reply.content, action)
        )
        circuit.observe_execution(action, outcome)
        state.messages.extend(dict(message) for message in outcome.history_messages)
        state.turn += 1
        self.event_sink.emit(OutcomeProduced(outcome, turn))
        if self.cancellation.cancelled():
            return EngineStep(
                turn, prepared, reply, action, outcome, stop_reason="cancelled"
            )
        if outcome.stop_reason:
            return EngineStep(
                turn,
                prepared,
                reply,
                action,
                outcome,
                completion=outcome.completion,
                stop_reason=outcome.stop_reason,
            )
        completion = outcome.completion
        if completion is None:
            completion = self.completion.completion()
        if completion is not None:
            return EngineStep(
                turn,
                prepared,
                reply,
                action,
                outcome,
                completion=completion,
                stop_reason="submitted",
            )
        return EngineStep(turn, prepared, reply, action, outcome)

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
    def _note_unexecuted_cell(outcome: ExecutionOutcome, note: str) -> ExecutionOutcome:
        """Put ``note`` on the last tool result the model is about to read.

        Routing is decided here, so this is the one place every executor's
        outcome passes through after a native action displaced a cell. The
        note rides inside a tool result because every wire carries that text,
        while a separate message between a tool batch and its results is a
        request Anthropic rejects. It is recorded like any other result, so a
        restored history reads the same words the live run did.
        """
        if not note:
            return outcome
        history = [dict(message) for message in outcome.history_messages]
        for message in reversed(history):
            content = message.get("content")
            if message.get("role") == "tool" and isinstance(content, str):
                message["content"] = content + note
                break
        else:
            return outcome
        observation = outcome.observation
        if isinstance(observation, str):
            observation += note
        return replace(
            outcome, history_messages=tuple(history), observation=observation
        )

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
            note = recovery_message(
                stop_reason,
                progress_reason=progress_reason,
                tool_names=(
                    (call.name for call in state.last_reply.tool_calls)
                    if state.last_reply is not None
                    else ()
                ),
            )
            if note is not None and (not state.messages or state.messages[-1] != note):
                state.messages.append(note)
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
