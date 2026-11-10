"""Trainer-facing rollout lifecycle over the existing AgentEngine."""

from __future__ import annotations

import inspect
from copy import deepcopy
from typing import Any, Callable, Iterable, Mapping, Protocol

from openai4s.agent.engine import AgentEngine
from openai4s.agent.events import RunFinished
from openai4s.agent.models import (
    EngineResult,
    EngineStep,
    Message,
    ModelReply,
    RunState,
)

from .agent_sink import TrajectoryEventSink
from .models import RunReceipt, Trajectory
from .recorder import TrajectoryRecorder


class PolicySampler(Protocol):
    """Minimal external policy contract used by :meth:`RolloutSession.run`."""

    def sample(
        self,
        observation: tuple[Message, ...],
        *,
        tools: tuple[Mapping[str, object], ...] = (),
    ) -> ModelReply | Mapping[str, object]: ...


class RolloutSession:
    """Expose reset/observe/step/finish without copying the AgentEngine loop.

    ``engine`` remains the owner of routing, permissions, kernels, completion,
    cancellation and action ledger callbacks. The session only supplies an
    external sampled reply and records the resulting trajectory.
    """

    def __init__(
        self,
        engine: AgentEngine,
        recorder: TrajectoryRecorder,
        *,
        delegate_sink: object | None = None,
        repository: Any | None = None,
        request_id: str | None = None,
    ) -> None:
        if request_id is not None and repository is None:
            raise ValueError("request_id requires a receipt repository")
        self.engine = engine
        self.recorder = recorder
        self.repository = repository
        self.request_id = request_id
        self.receipt: RunReceipt | None = None
        self.sink = TrajectoryEventSink(
            recorder,
            delegate=delegate_sink if delegate_sink is not None else engine.event_sink,
            capture_wall_time=True,
        )
        self.engine.event_sink = self.sink
        self.state: RunState | None = None
        self._finished = False
        self._pending_observation: tuple[Message, ...] | None = None
        self._pending_observation_turn: int | None = None
        self._pending_tools: tuple[Mapping[str, object], ...] = ()

    def reset(
        self,
        messages: Iterable[Mapping[str, object]],
        *,
        max_turns: int | None = None,
    ) -> RunState:
        if self.state is not None:
            raise RuntimeError("rollout session has already been reset")
        state = RunState(
            [dict(message) for message in messages],
            self.engine.max_turns if max_turns is None else max_turns,
        )
        if state.max_turns < 0:
            raise ValueError("max_turns must be non-negative")
        if self.repository is not None and self.request_id is not None:
            accepted_at = self.recorder.now_ms()
            fingerprint = self.recorder.fingerprint(
                {
                    "episode_id": self.recorder.episode.episode_id,
                    "messages": state.messages,
                    "max_turns": state.max_turns,
                }
            )
            candidate = RunReceipt(
                request_id=self.request_id,
                fingerprint=fingerprint,
                episode_id=self.recorder.episode.episode_id,
                state="accepted",
                accepted_at=accepted_at,
                updated_at=accepted_at,
            )
            self.receipt = self.repository.admit_receipt(candidate)
            if self.receipt.state != "accepted":
                raise RuntimeError(
                    f"rollout request {self.request_id!r} was already settled"
                )
            try:
                self.receipt = self.repository.update_receipt_state(
                    self.request_id, "running", expected_state="accepted"
                )
            except ValueError as exc:
                raise RuntimeError(
                    f"rollout request {self.request_id!r} was already claimed"
                ) from exc
        self.state = state
        self.state.metadata["rl_episode_id"] = self.recorder.episode.episode_id
        return state

    def observe(self) -> tuple[Message, ...]:
        state = self._state()
        prepared = tuple(
            dict(message) for message in self.engine.prepare_decision(state)
        )
        self._pending_observation = prepared
        self._pending_observation_turn = state.turn
        self._pending_tools = tuple(self.engine.prepare_tools(state))
        return prepared

    @property
    def current_tools(self) -> tuple[Mapping[str, object], ...]:
        """The detached tool declarations for the last :meth:`observe` call."""

        return tuple(deepcopy(dict(tool)) for tool in self._pending_tools)

    def step(
        self,
        reply: ModelReply | Mapping[str, object],
        *,
        observation: Iterable[Mapping[str, object]] | None = None,
    ) -> EngineStep:
        if self._finished:
            raise RuntimeError("rollout session is already finished")
        state = self._state()
        if observation is None:
            if self._pending_observation_turn == state.turn:
                pending = self._pending_observation
                if pending is None:
                    raise RuntimeError("rollout observation cache is inconsistent")
                prepared = tuple(dict(message) for message in pending)
            else:
                prepared = self.observe()
        else:
            prepared = tuple(dict(message) for message in observation)
        prepared_tools = (
            tuple(self._pending_tools)
            if self._pending_observation_turn == state.turn
            else None
        )
        self._pending_observation = None
        self._pending_observation_turn = None
        self._pending_tools = ()
        result = self.engine.step(
            state, reply, observation=prepared, tools=prepared_tools
        )
        if result.terminal:
            self._emit_finished(result)
        return result

    def run(
        self,
        sampler: (
            PolicySampler
            | Callable[[tuple[Message, ...]], ModelReply | Mapping[str, object]]
        ),
    ) -> Trajectory:
        state = self._state()
        try:
            while not self._finished and state.turn < state.max_turns:
                observation = self.observe()
                sampled = _sample(sampler, observation, self.current_tools)
                self.step(sampled, observation=observation)
        except Exception:
            self.finish("failed")
            raise
        if not self._finished:
            self._emit_finished(
                EngineStep(
                    state.turn,
                    tuple(dict(message) for message in state.messages),
                    None,
                    None,
                    None,
                    stop_reason="max_turns",
                )
            )
        return self.recorder.snapshot()

    def finish(self, status: str = "completed") -> Trajectory:
        if not self._finished:
            self.recorder.finish(status)
            self._settle_receipt(status)
            self._finished = True
        return self.recorder.snapshot()

    def export(self) -> str:
        return self.recorder.export_json()

    @property
    def finished(self) -> bool:
        """Whether this rollout has emitted its terminal lifecycle event."""
        return self._finished

    def _emit_finished(self, step: EngineStep) -> None:
        if self._finished:
            return
        state = self._state()
        result = EngineResult(
            tuple(dict(message) for message in state.messages),
            step.completion,
            step.stop_reason or "completed",
            state.turn,
            step.reply,
        )
        self.sink.emit(RunFinished(result))
        self._settle_receipt(result.stop_reason)
        self._finished = True

    def _settle_receipt(self, stop_reason: str) -> None:
        if self.repository is None or self.request_id is None:
            return
        state = {
            "completed": "completed",
            "done": "completed",
            "cancelled": "cancelled",
            "timeout": "interrupted",
            "interrupted": "interrupted",
            "max_turns": "interrupted",
            "no_progress": "interrupted",
            "error": "failed",
            "failed": "failed",
        }.get(stop_reason, "completed")
        self.receipt = self.repository.update_receipt_state(self.request_id, state)

    def _state(self) -> RunState:
        if self.state is None:
            raise RuntimeError("rollout session must be reset before use")
        return self.state


def _sample(
    sampler: (
        PolicySampler
        | Callable[[tuple[Message, ...]], ModelReply | Mapping[str, object]]
    ),
    observation: tuple[Message, ...],
    tools: tuple[Mapping[str, object], ...],
) -> ModelReply | Mapping[str, object]:
    """Call a modern sampler without breaking legacy one-argument callables."""

    callback = sampler.sample if hasattr(sampler, "sample") else sampler
    try:
        parameters = inspect.signature(callback).parameters
    except (TypeError, ValueError):
        parameters = {}
    tool_parameter = parameters.get("tools")
    accepts_tools = (
        tool_parameter is not None
        and tool_parameter.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    ) or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    if accepts_tools:
        return callback(observation, tools=tools)
    return callback(observation)


__all__ = ["PolicySampler", "RolloutSession"]
