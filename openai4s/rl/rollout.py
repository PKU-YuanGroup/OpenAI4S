"""Trainer-facing rollout lifecycle over the existing AgentEngine."""

from __future__ import annotations

import inspect
from copy import deepcopy
from dataclasses import replace
from typing import Any, Callable, Iterable, Mapping, Protocol, cast

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
from .models import ExecutionEvent, RunReceipt, Trajectory
from .recorder import TrajectoryRecorder


class PolicySampler(Protocol):
    """Minimal external policy contract used by :meth:`RolloutSession.run`."""

    def sample(
        self,
        observation: tuple[Message, ...],
        *,
        tools: tuple[Mapping[str, object], ...] = (),
    ) -> ModelReply | Mapping[str, object]: ...


class AsyncPolicySampler(Protocol):
    """Optional async sampler contract for inference-server rollouts."""

    def sample(
        self,
        observation: tuple[Message, ...],
        *,
        tools: tuple[Mapping[str, object], ...] = (),
    ) -> Any: ...


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
        self._run_owner: str | None = None
        self._terminal_result: Trajectory | None = None
        self.sink = TrajectoryEventSink(
            recorder,
            delegate=delegate_sink if delegate_sink is not None else engine.event_sink,
            # RolloutSession is also a durable adapter.  Prefixing its
            # generated observation/decision/event IDs prevents two episodes
            # in the same Store from colliding on ``obs-0`` or
            # ``decision-0``.  The AgentRuntime composition supplies its own
            # sink prefix, so this does not alter the direct sink contract.
            id_prefix=recorder.episode.episode_id,
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
        receipt_context: Mapping[str, Any] | None = None,
        claimed_run: Mapping[str, Any] | None = None,
    ) -> RunState:
        if self.state is not None:
            raise RuntimeError("rollout session has already been reset")
        state = RunState(
            [dict(message) for message in messages],
            self.engine.max_turns if max_turns is None else max_turns,
        )
        if state.max_turns < 0:
            raise ValueError("max_turns must be non-negative")
        if (
            self.repository is not None
            and self.request_id is not None
            and claimed_run is None
        ):
            accepted_at = self.recorder.now_ms()
            fingerprint_payload: dict[str, Any] = {
                "episode_id": self.recorder.episode.episode_id,
                "messages": state.messages,
                "max_turns": state.max_turns,
            }
            if receipt_context is not None:
                fingerprint_payload["receipt_context"] = dict(receipt_context)
            fingerprint = self.recorder.fingerprint(fingerprint_payload)
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
        if claimed_run is not None:
            assert self.repository is not None
            if (
                claimed_run.get("state") != "running"
                or claimed_run.get("owner_id") != self.recorder.owner_id
            ):
                raise ValueError("invalid run claim")
            self._run_owner = self.recorder.owner_id
            self.receipt = self.repository.get_receipt(self.request_id)
        self.state = state
        self.state.metadata["rl_episode_id"] = self.recorder.episode.episode_id
        return state

    def observe(self) -> tuple[Message, ...]:
        state = self._state()
        # Observation preparation can be requested by both a transport layer
        # and a trainer retry.  Reuse the exact detached context for the
        # current turn so context compaction and dynamic tool projection run
        # once, and the recorded prompt hash remains the one sampled by the
        # policy.
        if (
            self._pending_observation_turn == state.turn
            and self._pending_observation is not None
        ):
            return tuple(dict(message) for message in self._pending_observation)
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
        used_cached_observation = False
        if observation is None:
            if self._pending_observation_turn == state.turn:
                pending = self._pending_observation
                if pending is None:
                    raise RuntimeError("rollout observation cache is inconsistent")
                prepared = tuple(dict(message) for message in pending)
                used_cached_observation = True
            else:
                prepared = self.observe()
                used_cached_observation = True
        else:
            prepared = tuple(dict(message) for message in observation)
            used_cached_observation = (
                self._pending_observation_turn == state.turn
                and self._pending_observation is not None
                and prepared == self._pending_observation
            )
        prepared_tools = tuple(self._pending_tools) if used_cached_observation else None
        self._pending_observation = None
        self._pending_observation_turn = None
        self._pending_tools = ()
        result = self.engine.step(
            state, reply, observation=prepared, tools=prepared_tools
        )
        # A trainer-facing step is a complete lifecycle unit. If the action
        # consumed the last allowed turn without producing a domain terminal,
        # close the Episode here instead of requiring a synthetic extra reply
        # merely to discover ``max_turns``.
        if not result.terminal and state.turn >= state.max_turns:
            result = replace(result, stop_reason="max_turns")
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
            self.finish(
                "cancelled" if self.engine.cancellation.cancelled() else "failed"
            )
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
        return self._terminal_result or self.recorder.snapshot()

    async def run_async(
        self,
        sampler: (
            AsyncPolicySampler | PolicySampler | Callable[[tuple[Message, ...]], Any]
        ),
    ) -> Trajectory:
        """Hand off this idle session to a serial lane; sampling stays on its loop.

        The caller must not concurrently use the borrowed Engine. Factory-owned
        resources should use RolloutWorker.run_async for same-thread creation and
        teardown as well as execution.
        """
        from .async_lane import Cancellation, run_on_lane

        def execute(bridge: Any, requested: Any) -> Trajectory:
            original = self.engine.cancellation
            self.engine.cancellation = Cancellation(original, requested)
            try:
                return self.run(bridge)
            finally:
                self.engine.cancellation = original

        return await run_on_lane(execute, sampler)

    def finish(self, status: str = "completed") -> Trajectory:
        from openai4s.storage.rl_runs import receipt_state_for_status

        receipt_state_for_status(status)
        if not self._finished:
            state = self.state
            if self._run_owner is not None:
                return self._finalize_durable(status, None, state.turn if state else 0)
            self.recorder.add_event(
                ExecutionEvent(
                    event_id=(f"{self.recorder.episode.episode_id}:episode.finished"),
                    episode_id=self.recorder.episode.episode_id,
                    kind="episode.finished",
                    created_at=self.recorder.now_ms(),
                    payload={
                        "stop_reason": status,
                        "completion": None,
                        "turns": state.turn if state is not None else 0,
                    },
                )
            )
            self.recorder.finish(status)
            self._settle_receipt(status)
            self._finished = True
        return self._terminal_result or self.recorder.snapshot()

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
        if self._run_owner is not None:
            self._finalize_durable(result.stop_reason, result.completion, state.turn)
            if self.sink.delegate is not None:
                self.sink.delegate.emit(RunFinished(result))
        else:
            self.sink.emit(RunFinished(result))
            self._settle_receipt(result.stop_reason)
            self._finished = True

    def _finalize_durable(self, reason: str, completion: Any, turns: int) -> Trajectory:
        assert self.repository is not None
        status = "completed" if reason in {"submitted", "plan"} else reason
        terminal = ExecutionEvent(
            event_id=f"{self.recorder.episode.episode_id}:episode.finished",
            episode_id=self.recorder.episode.episode_id,
            kind="episode.finished",
            created_at=self.recorder.now_ms(),
            payload={"stop_reason": reason, "completion": completion, "turns": turns},
        )
        trajectory = self.repository.runs.finalize(
            self.request_id,
            self._run_owner,
            self.recorder.episode.episode_id,
            status,
            terminal,
        )
        self.recorder.episode = trajectory.episode
        self.receipt = self.repository.get_receipt(self.request_id)
        self._terminal_result = trajectory
        self._finished = True
        return trajectory

    def _settle_receipt(self, stop_reason: str) -> None:
        if self.repository is None or self.request_id is None:
            return
        from openai4s.storage.rl_runs import receipt_state_for_status

        state = receipt_state_for_status(stop_reason)
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
        parameters: Mapping[str, inspect.Parameter] = inspect.signature(
            callback
        ).parameters
    except (TypeError, ValueError):
        parameters = {}
    dynamic_callback = cast(Callable[..., Any], callback)
    tool_parameter = parameters.get("tools")
    positional_tools = (
        tool_parameter is not None
        and tool_parameter.kind is inspect.Parameter.POSITIONAL_ONLY
    )
    accepts_tools = (
        tool_parameter is not None
        and tool_parameter.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    ) or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    if positional_tools:
        return dynamic_callback(observation, tools)
    if accepts_tools:
        return dynamic_callback(observation, tools=tools)
    return dynamic_callback(observation)


async def _sample_async(
    sampler: AsyncPolicySampler | PolicySampler | Callable[[tuple[Message, ...]], Any],
    observation: tuple[Message, ...],
    tools: tuple[Mapping[str, object], ...],
) -> ModelReply | Mapping[str, object]:
    """Call a sampler with the same compatibility rules as :func:`_sample`."""

    async_callback = getattr(sampler, "sample_async", None)
    callback = (
        async_callback
        if callable(async_callback)
        else sampler.sample if hasattr(sampler, "sample") else sampler
    )
    try:
        parameters: Mapping[str, inspect.Parameter] = inspect.signature(
            callback
        ).parameters
    except (TypeError, ValueError):
        parameters = {}
    dynamic_callback = cast(Callable[..., Any], callback)
    tool_parameter = parameters.get("tools")
    positional_tools = (
        tool_parameter is not None
        and tool_parameter.kind is inspect.Parameter.POSITIONAL_ONLY
    )
    accepts_tools = (
        tool_parameter is not None
        and tool_parameter.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    ) or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    if positional_tools:
        result = dynamic_callback(observation, tools)
    elif accepts_tools:
        result = dynamic_callback(observation, tools=tools)
    else:
        result = dynamic_callback(observation)
    if inspect.isawaitable(result):
        result = await result
    return result


__all__ = ["AsyncPolicySampler", "PolicySampler", "RolloutSession"]
