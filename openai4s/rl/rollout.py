"""Trainer-facing rollout lifecycle over the existing AgentEngine."""

from __future__ import annotations

from typing import Callable, Iterable, Mapping, Protocol

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
from .models import Trajectory
from .recorder import TrajectoryRecorder


class PolicySampler(Protocol):
    """Minimal external policy contract used by :meth:`RolloutSession.run`."""

    def sample(
        self, observation: tuple[Message, ...]
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
    ) -> None:
        self.engine = engine
        self.recorder = recorder
        self.sink = TrajectoryEventSink(
            recorder,
            delegate=delegate_sink if delegate_sink is not None else engine.event_sink,
        )
        self.engine.event_sink = self.sink
        self.state: RunState | None = None
        self._finished = False

    def reset(
        self,
        messages: Iterable[Mapping[str, object]],
        *,
        max_turns: int | None = None,
    ) -> RunState:
        if self.state is not None:
            raise RuntimeError("rollout session has already been reset")
        self.state = RunState(
            [dict(message) for message in messages],
            self.engine.max_turns if max_turns is None else max_turns,
        )
        if self.state.max_turns < 0:
            raise ValueError("max_turns must be non-negative")
        return self.state

    def observe(self) -> tuple[Message, ...]:
        state = self._state()
        return tuple(dict(message) for message in self.engine.prepare_decision(state))

    def step(
        self,
        reply: ModelReply | Mapping[str, object],
        *,
        observation: Iterable[Mapping[str, object]] | None = None,
    ) -> EngineStep:
        if self._finished:
            raise RuntimeError("rollout session is already finished")
        state = self._state()
        prepared = tuple(
            dict(message)
            for message in (observation if observation is not None else self.observe())
        )
        result = self.engine.step(state, reply, observation=prepared)
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
        while not self._finished and state.turn < state.max_turns:
            observation = self.observe()
            sampled = (
                sampler.sample(observation)
                if hasattr(sampler, "sample")
                else sampler(observation)
            )
            self.step(sampled, observation=observation)
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
            self._finished = True
        return self.recorder.snapshot()

    def export(self) -> str:
        return self.recorder.export_json()

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
        self._finished = True

    def _state(self) -> RunState:
        if self.state is None:
            raise RuntimeError("rollout session must be reset before use")
        return self.state


__all__ = ["PolicySampler", "RolloutSession"]
