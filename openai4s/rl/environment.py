"""Episode-scoped environment construction for trainer rollouts."""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from openai4s.agent.actions import CodeCell, FinalizeAction, NativeToolBatch
from openai4s.agent.engine import AgentEngine
from openai4s.agent.models import EngineStep, Message, ModelReply

from .models import Episode, Trajectory
from .recorder import TrajectoryRecorder
from .rollout import RolloutSession


@dataclass(frozen=True)
class EpisodeReset:
    """Immutable inputs handed to an Engine factory for one Episode."""

    episode: Episode
    reset_id: str
    workspace: Path
    workspace_id: str
    initial_artifacts: Mapping[str, str]
    config: Mapping[str, Any]
    budget: Mapping[str, Any]

    def public_metadata(self) -> dict[str, Any]:
        """Return reset facts safe to include in a trajectory envelope."""
        return {
            "reset_id": self.reset_id,
            "workspace_id": self.workspace_id,
            "initial_artifacts": dict(self.initial_artifacts),
            "config": dict(self.config),
            "budget": dict(self.budget),
        }


EngineFactory = Callable[[EpisodeReset], AgentEngine]


class RolloutEnvironment:
    """Build isolated Engine/Recorder pairs one Episode at a time.

    The factory owns runtime configuration (including workspace binding) while
    this adapter owns Episode identity, reset metadata, and workspace lifetime.
    It does not copy or replace the AgentEngine loop.
    """

    def __init__(
        self,
        engine_factory: EngineFactory,
        *,
        repository: Any | None = None,
        workspace_root: str | Path | None = None,
    ) -> None:
        self.engine_factory = engine_factory
        self.repository = repository
        self.workspace_root = (
            Path(workspace_root).expanduser().resolve()
            if workspace_root is not None
            else None
        )
        if self.workspace_root is not None:
            self.workspace_root.mkdir(parents=True, exist_ok=True)
        # Reset is a reservation as well as a constructor.  Keep the
        # reservation separate from ``_active`` so a slow Engine factory
        # cannot let a second caller pass the active check while the first
        # episode is still being assembled.
        self._lock = threading.RLock()
        self._resetting = False
        self._active: tuple[RolloutSession, EpisodeReset] | None = None

    def reset(
        self,
        episode: Episode,
        messages: list[Message] | tuple[Message, ...],
        *,
        initial_artifacts: Mapping[str, str] | None = None,
        config: Mapping[str, Any] | None = None,
        budget: Mapping[str, Any] | None = None,
        max_turns: int | None = None,
        request_id: str | None = None,
    ) -> RolloutSession:
        """Create and reset one isolated rollout session."""
        with self._lock:
            if self._active is not None or self._resetting:
                raise RuntimeError("rollout environment already has an active episode")
            self._resetting = True

        workspace: Path | None = None
        try:
            if episode.status != "running":
                raise ValueError("a reset episode must have running status")
            artifacts = _text_mapping(
                "initial_artifacts",
                {} if initial_artifacts is None else initial_artifacts,
            )
            reset_config = _json_mapping("config", {} if config is None else config)
            reset_budget = _json_mapping("budget", {} if budget is None else budget)
            reset_id = "reset-" + uuid.uuid4().hex
            workspace_id = "workspace-" + uuid.uuid4().hex
            workspace = self._new_workspace()
            reset = EpisodeReset(
                episode=episode,
                reset_id=reset_id,
                workspace=workspace,
                workspace_id=workspace_id,
                initial_artifacts=artifacts,
                config=reset_config,
                budget=reset_budget,
            )
            # Receipt identity must bind caller inputs, but generated reset and
            # workspace UUIDs are intentionally excluded: a retry of the same
            # request must hash identically even though it receives a fresh
            # isolated workspace.
            receipt_context = {
                "episode": {
                    "episode_id": episode.episode_id,
                    "task": episode.task,
                    "session_id": episode.session_id,
                    "policy_name": episode.policy_name,
                    "policy_version": episode.policy_version,
                    "parent_episode_id": episode.parent_episode_id,
                    "metadata": dict(episode.metadata),
                },
                "initial_artifacts": dict(reset.initial_artifacts),
                "config": dict(reset.config),
                "budget": dict(reset.budget),
            }
            metadata = dict(episode.metadata)
            metadata["reset"] = reset.public_metadata()
            effective_episode = replace(episode, metadata=metadata)
            reset = replace(reset, episode=effective_episode)
            engine = self.engine_factory(reset)
            recorder = TrajectoryRecorder(effective_episode, repository=self.repository)
            session = RolloutSession(
                engine,
                recorder,
                repository=self.repository,
                request_id=request_id,
            )
            session.reset(
                messages,
                max_turns=max_turns,
                receipt_context=receipt_context,
            )
            assert session.state is not None
            session.state.metadata["rl_reset"] = reset.public_metadata()
            with self._lock:
                self._active = (session, reset)
        except BaseException:
            if workspace is not None:
                shutil.rmtree(workspace, ignore_errors=True)
            raise
        finally:
            with self._lock:
                self._resetting = False
        return session

    def release(self, session: RolloutSession, *, cleanup: bool = True) -> Trajectory:
        """Release a finished session and optionally remove its workspace."""
        with self._lock:
            active = self._active
            if active is None or active[0] is not session:
                raise ValueError("session does not belong to this rollout environment")
            if not session.finished:
                raise RuntimeError("cannot release an unfinished rollout session")
            trajectory = session.recorder.snapshot()
            self._active = None
        if cleanup:
            shutil.rmtree(active[1].workspace, ignore_errors=True)
        return trajectory

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active is not None

    def _new_workspace(self) -> Path:
        if self.workspace_root is None:
            return Path(tempfile.mkdtemp(prefix="openai4s-rl-"))
        path = self.workspace_root / ("episode-" + uuid.uuid4().hex)
        path.mkdir(mode=0o700)
        return path


class RolloutEnvironmentAdapter:
    """JSON-safe trainer boundary over :class:`RolloutEnvironment`.

    The trainer should exchange observations, sampled replies, and detached
    execution results; it should not need to import ``AgentEngine`` or inspect
    ``Action``/``ExecutionOutcome`` objects.  This adapter owns that narrow
    translation while the existing environment and engine retain ownership of
    workspaces, permissions, kernels, and ledger writes.

    Requests use one stable shape::

        {"episode": {...}, "messages": [{"role": "user", "content": ...}]}

    ``episode`` may also be an :class:`Episode` for in-process callers.  All
    returned mappings are detached JSON values, and the workspace path is
    deliberately never included in the response.
    """

    def __init__(self, environment: RolloutEnvironment):
        if not isinstance(environment, RolloutEnvironment):
            raise TypeError("environment must be a RolloutEnvironment")
        self.environment = environment
        self._session: RolloutSession | None = None
        self._released = False
        self._released_result: dict[str, Any] | None = None

    def reset(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Reset one Episode and return its first trainer observation."""

        if self._session is not None and not self._released:
            raise RuntimeError("environment adapter already has an active episode")
        self._session = None
        self._released = False
        self._released_result = None
        if not isinstance(request, Mapping):
            raise TypeError("reset request must be a mapping")
        episode = _episode(request.get("episode"))
        messages = _messages(request.get("messages"))
        session = self.environment.reset(
            episode,
            messages,
            initial_artifacts=request.get("initial_artifacts"),
            config=request.get("config"),
            budget=request.get("budget"),
            max_turns=request.get("max_turns"),
            request_id=request.get("request_id"),
        )
        self._session = session
        observation = session.observe()
        return self._observation_response(observation)

    def observe(self) -> dict[str, Any]:
        """Prepare and return the exact context for the next policy call."""

        session = self._active()
        if self._released:
            raise RuntimeError("rollout episode has already been released")
        if session.finished:
            return self._observation_response(()) | {
                "terminal": True,
                "status": "terminal",
            }
        return self._observation_response(session.observe())

    def step(
        self,
        reply: ModelReply | Mapping[str, Any],
        *,
        observation: Any = None,
    ) -> dict[str, Any]:
        """Execute one externally sampled reply and return detached results.

        ``observation`` is optional.  When omitted, the observation prepared by
        the preceding :meth:`reset`/:meth:`observe` call is reused by the
        underlying session, preserving the exact prompt hash captured in the
        trajectory.
        """

        session = self._active()
        if self._released:
            raise RuntimeError("rollout episode has already been released")
        prepared = None if observation is None else _messages(observation)
        result = session.step(reply, observation=prepared)
        response = _step_response(session, result)
        if session.finished:
            response["status"] = "terminal"
        else:
            response["status"] = "running"
            response["next_observation"] = _messages(session.observe())
            response["next_tools"] = _json(session.current_tools)
        return response

    def finish(self, status: str = "completed") -> dict[str, Any]:
        """Explicitly terminalize the active Episode without scoring it."""

        session = self._active()
        session.finish(status)
        return self._release_finished(session, include_stop_reason=True)

    def _release_finished(
        self, session: RolloutSession, *, include_stop_reason: bool = False
    ) -> dict[str, Any]:
        """Release once and return the detached result on repeated calls."""

        if self._released:
            if self._released_result is None:
                raise RuntimeError("rollout release state is inconsistent")
            return _json(self._released_result)
        released = self.environment.release(session)
        self._released = True
        result: dict[str, Any] = {
            "episode": _json(released.episode.to_dict()),
            "status": "terminal",
            "counts": _trajectory_counts(released),
        }
        if include_stop_reason:
            result["stop_reason"] = released.episode.status
        self._released_result = result
        return _json(result)

    def export(self, format: str = "json") -> dict[str, Any]:
        """Return a schema/hash-bound JSON, JSONL, or ATIF export."""

        session = self._active()
        if not session.finished:
            raise RuntimeError("cannot export a running rollout")
        # Import lazily to keep the environment module's construction path
        # independent from the export implementation.
        from .worker import RolloutWorker

        exported = RolloutWorker.export(session.recorder.snapshot(), format)
        return exported.to_dict()

    def release(self) -> dict[str, Any]:
        """Release a terminal Episode after inspecting its final step."""

        session = self._active()
        if self._released:
            return self._release_finished(session)
        if not session.finished:
            raise RuntimeError("cannot release an unfinished rollout session")
        return self._release_finished(session)

    def rewards(
        self,
        *,
        verifier: str | None = None,
        verifier_version: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read annotations without computing or mutating reward."""

        values = self._active().recorder.snapshot().rewards
        return [
            _json(value.to_dict())
            for value in values
            if (verifier is None or value.verifier == verifier)
            and (verifier_version is None or value.verifier_version == verifier_version)
        ]

    def costs(self) -> list[dict[str, Any]]:
        """Read measured cost evidence without estimating missing fields."""

        return [
            _json(value.to_dict()) for value in self._active().recorder.snapshot().costs
        ]

    @property
    def finished(self) -> bool:
        return self._session is not None and self._session.finished

    def _active(self) -> RolloutSession:
        if self._session is None:
            raise RuntimeError("environment adapter must be reset first")
        return self._session

    def _observation_response(self, observation: Any) -> dict[str, Any]:
        session = self._active()
        state = session.state
        assert state is not None
        return {
            "episode": _json(session.recorder.episode.to_dict()),
            "turn": state.turn,
            "max_turns": state.max_turns,
            "observation": _messages(observation),
            "tools": _json(session.current_tools),
            "terminal": session.finished,
            "status": "terminal" if session.finished else "running",
        }


# The short name mirrors the plan and keeps embedding code independent from
# the concrete class name used by the environment implementation.
EnvironmentAdapter = RolloutEnvironmentAdapter


def _episode(value: Any) -> Episode:
    if isinstance(value, Episode):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("reset request episode must be an Episode or mapping")
    try:
        return Episode(**dict(value))
    except TypeError as exc:
        raise ValueError(f"invalid episode: {exc}") from exc


def _messages(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        raise TypeError("messages/observation must be a list or tuple")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise TypeError(f"message {index} must be a mapping")
        result.append(_json(dict(item)))
    return result


def _json(value: Any) -> Any:
    """Round-trip a value through strict JSON and return a detached object."""

    try:
        result = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise TypeError("trainer boundary values must be JSON-compatible") from exc
    return result


def _action(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, CodeCell):
        return {"type": "code_cell", "language": value.language, "code": value.code}
    if isinstance(value, FinalizeAction):
        return {"type": "finalize", "call": _json(value.call.__dict__)}
    if isinstance(value, NativeToolBatch):
        return {
            "type": "native_tool_batch",
            "calls": [_json(call.__dict__) for call in value.calls],
        }
    raise TypeError(f"unsupported engine action {type(value)!r}")


def _step_response(session: RolloutSession, result: EngineStep) -> dict[str, Any]:
    state = session.state
    assert state is not None
    outcome = result.outcome
    return {
        "episode": _json(session.recorder.episode.to_dict()),
        "turn": result.turn,
        "observation": _messages(result.observation),
        "action": _action(result.action),
        "outcome": (
            None
            if outcome is None
            else _json(
                {
                    "history_messages": list(outcome.history_messages),
                    "observation": outcome.observation,
                    "completion": outcome.completion,
                    "stop_reason": outcome.stop_reason,
                }
            )
        ),
        "completion": _json(result.completion),
        "stop_reason": result.stop_reason,
        "terminal": session.finished,
        "state": {"turn": state.turn, "max_turns": state.max_turns},
    }


def _trajectory_counts(trajectory: Trajectory) -> dict[str, int]:
    return {
        "observations": len(trajectory.observations),
        "decisions": len(trajectory.decisions),
        "segments": len(trajectory.segments),
        "execution_events": len(trajectory.execution_events),
        "rewards": len(trajectory.rewards),
        "costs": len(trajectory.costs),
    }


def _text_mapping(name: str, value: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"{name} keys must be non-empty strings")
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{name} values must be non-empty strings")
        result[key] = item
    return result


def _json_mapping(name: str, value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    result = json.loads(json.dumps(dict(value), ensure_ascii=False, allow_nan=False))
    if not isinstance(result, dict):
        raise TypeError(f"{name} must encode as an object")
    return result


__all__ = [
    "EnvironmentAdapter",
    "EpisodeReset",
    "RolloutEnvironment",
    "RolloutEnvironmentAdapter",
]
