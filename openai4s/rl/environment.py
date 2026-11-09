"""Episode-scoped environment construction for trainer rollouts."""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from openai4s.agent.engine import AgentEngine
from openai4s.agent.models import Message

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
        if self._active is not None:
            raise RuntimeError("rollout environment already has an active episode")
        if episode.status != "running":
            raise ValueError("a reset episode must have running status")
        artifacts = _text_mapping("initial_artifacts", initial_artifacts or {})
        reset_config = _json_mapping("config", config or {})
        reset_budget = _json_mapping("budget", budget or {})
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
        metadata = dict(episode.metadata)
        metadata["reset"] = reset.public_metadata()
        effective_episode = replace(episode, metadata=metadata)
        reset = replace(reset, episode=effective_episode)
        try:
            engine = self.engine_factory(reset)
            recorder = TrajectoryRecorder(effective_episode, repository=self.repository)
            session = RolloutSession(
                engine,
                recorder,
                repository=self.repository,
                request_id=request_id,
            )
            session.reset(messages, max_turns=max_turns)
            assert session.state is not None
            session.state.metadata["rl_reset"] = reset.public_metadata()
        except BaseException:
            shutil.rmtree(workspace, ignore_errors=True)
            raise
        self._active = (session, reset)
        return session

    def release(self, session: RolloutSession, *, cleanup: bool = True) -> Trajectory:
        """Release a finished session and optionally remove its workspace."""
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
        return self._active is not None

    def _new_workspace(self) -> Path:
        if self.workspace_root is None:
            return Path(tempfile.mkdtemp(prefix="openai4s-rl-"))
        path = self.workspace_root / ("episode-" + uuid.uuid4().hex)
        path.mkdir(mode=0o700)
        return path


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


__all__ = ["EpisodeReset", "RolloutEnvironment"]
