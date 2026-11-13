"""A small, provider-neutral rollout worker boundary.

The worker owns orchestration around :class:`RolloutEnvironment`; the existing
AgentEngine and RolloutSession still own action execution. Recovery is
fail-closed: a receipt left in ``running`` is reported for explicit operator
handling and is never replayed automatically.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Mapping

from openai4s.agent.models import Message

from .atif import ATIFExporter
from .environment import RolloutEnvironment, RunAlreadyTerminal
from .models import Episode, RunReceipt, Trajectory
from .recorder import trajectory_jsonl
from .rollout import RolloutSession

WorkerFormat = Literal["json", "jsonl", "atif"]


@dataclass(frozen=True)
class RecoveryResult:
    request_id: str
    episode_id: str
    receipt_state: str
    status: Literal["not_started", "recovery_required", "already_terminal"]
    can_resume: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return a detached, JSON-compatible recovery projection."""
        return {
            "request_id": self.request_id,
            "episode_id": self.episode_id,
            "receipt_state": self.receipt_state,
            "status": self.status,
            "can_resume": self.can_resume,
        }


@dataclass(frozen=True)
class RolloutExport:
    episode_id: str
    format: WorkerFormat
    schema_version: str
    sha256: str
    content: str

    @property
    def schema(self) -> str:
        """Compatibility alias for consumers that call the field ``schema``."""
        return self.schema_version

    def to_dict(self) -> dict[str, Any]:
        """Return the complete export envelope as JSON-native values."""
        return {
            "episode_id": self.episode_id,
            "format": self.format,
            "schema_version": self.schema_version,
            "sha256": self.sha256,
            "content": self.content,
        }


class RolloutWorker:
    """Run one isolated rollout and expose explicit recovery/export results."""

    def __init__(self, environment: RolloutEnvironment, *, repository: Any = None):
        self.environment = environment
        environment_repository = getattr(environment, "repository", None)
        if repository is None:
            repository = environment_repository
        elif environment_repository is None:
            # The worker owns the durable boundary when the caller supplies a
            # repository here.  RolloutEnvironment must receive the same
            # object so reset creates a recorder that persists the episode and
            # all subsequent events.
            environment.repository = repository
        elif environment_repository is not repository:
            raise ValueError("worker and environment repositories must match")
        self.repository = repository

    def reset(
        self,
        episode: Episode,
        messages: Iterable[Message],
        *,
        initial_artifacts: Mapping[str, str] | None = None,
        config: Mapping[str, Any] | None = None,
        budget: Mapping[str, Any] | None = None,
        max_turns: int | None = None,
        request_id: str | None = None,
    ) -> RolloutSession:
        """Reserve one Episode through the environment boundary."""
        return self.environment.reset(
            episode,
            tuple(messages),
            initial_artifacts=initial_artifacts,
            config=config,
            budget=budget,
            max_turns=max_turns,
            request_id=request_id,
        )

    def release(self, session: RolloutSession, *, cleanup: bool = True) -> Trajectory:
        """Release a finished Episode and clean its workspace by default."""
        return self.environment.release(session, cleanup=cleanup)

    def run(
        self,
        episode: Episode,
        messages: Iterable[Message],
        sampler: Any,
        *,
        initial_artifacts: Mapping[str, str] | None = None,
        config: Mapping[str, Any] | None = None,
        budget: Mapping[str, Any] | None = None,
        max_turns: int | None = None,
        request_id: str | None = None,
    ) -> Trajectory:
        try:
            session = self.reset(
                episode,
                messages,
                initial_artifacts=initial_artifacts,
                config=config,
                budget=budget,
                max_turns=max_turns,
                request_id=request_id,
            )
        except RunAlreadyTerminal as settled:
            return settled.trajectory
        try:
            return session.run(sampler)
        finally:
            # RolloutSession settles ordinary exceptions before this boundary.
            # A BaseException (for example, process interruption) leaves a
            # running durable receipt untouched so a later worker reports
            # recovery_required instead of guessing that side effects settled.
            if session.finished:
                self.release(session)

    async def run_async(
        self,
        episode: Episode,
        messages: Iterable[Message],
        sampler: Any,
        *,
        initial_artifacts: Mapping[str, str] | None = None,
        config: Mapping[str, Any] | None = None,
        budget: Mapping[str, Any] | None = None,
        max_turns: int | None = None,
        request_id: str | None = None,
    ) -> Trajectory:
        """Run the complete synchronous lifetime on one owned execution lane."""
        from .async_lane import Cancellation, run_on_lane

        def execute(bridge: Any, requested: Any) -> Trajectory:
            try:
                session = self.reset(
                    episode,
                    messages,
                    initial_artifacts=initial_artifacts,
                    config=config,
                    budget=budget,
                    max_turns=max_turns,
                    request_id=request_id,
                )
            except RunAlreadyTerminal as settled:
                return settled.trajectory
            session.engine.cancellation = Cancellation(
                session.engine.cancellation, requested
            )
            try:
                if requested.is_set():
                    return session.finish("cancelled")
                return session.run(bridge)
            finally:
                if session.finished:
                    self.release(session)

        return await run_on_lane(execute, sampler, interrupt=self.environment.interrupt)

    @property
    def cancel_confirmed(self) -> bool:
        return not self.environment.active

    async def aclose(self) -> None:
        if self.environment.active:
            raise RuntimeError("rollout execution remains active or recovery_required")

    def recover(self, request_id: str) -> RecoveryResult:
        """Inspect a durable receipt without retrying the scientific work."""
        if self.repository is None or not callable(
            getattr(self.repository, "get_receipt", None)
        ):
            raise RuntimeError("recovery requires a durable receipt repository")
        receipt = self.repository.get_receipt(request_id)
        if receipt is None:
            raise KeyError(request_id)
        if isinstance(receipt, Mapping):
            receipt = RunReceipt(**dict(receipt))
        if not isinstance(receipt, RunReceipt):
            raise TypeError("receipt repository returned an unsupported value")
        if receipt.state == "accepted":
            status: Literal["not_started", "recovery_required", "already_terminal"] = (
                "not_started"
            )
        elif receipt.state == "running":
            status = "recovery_required"
        elif receipt.state in {"completed", "failed", "cancelled", "interrupted"}:
            status = "already_terminal"
        else:
            # Receipt state is durable input.  An unknown value cannot prove
            # that side effects settled, so recovery remains explicit and
            # fail-closed rather than silently treating it as terminal.
            status = "recovery_required"
        safe_to_start = False
        if receipt.state == "accepted":
            try:
                details = self.repository.runs.get(request_id)
                safe_to_start = bool(details and details.get("protocol") == 2)
            except (AttributeError, RuntimeError):
                status = "recovery_required"
        return RecoveryResult(
            request_id=receipt.request_id,
            episode_id=receipt.episode_id,
            receipt_state=receipt.state,
            status=status,
            can_resume=safe_to_start,
        )

    def result(self, request_id: str) -> Trajectory | None:
        if self.repository is None:
            raise RuntimeError("result retrieval requires a durable receipt repository")
        return self.repository.runs.result(request_id)

    @staticmethod
    def export(
        trajectory: Trajectory,
        format: WorkerFormat = "json",
        *,
        children: Iterable[Trajectory] = (),
    ) -> RolloutExport:
        if not isinstance(trajectory, Trajectory):
            raise TypeError("trajectory must be a Trajectory")
        if trajectory.episode.status == "running":
            raise ValueError("cannot export a running rollout")
        if format == "json":
            content = trajectory.canonical_json()
            schema_version = trajectory.schema_version
        elif format == "jsonl":
            content = trajectory_jsonl(trajectory)
            schema_version = trajectory.schema_version
        elif format == "atif":
            exporter = ATIFExporter(trajectory, children=children)
            content = exporter.export_json()
            schema_version = exporter.to_dict()["schema_version"]
        else:
            raise ValueError("format must be 'json', 'jsonl', or 'atif'")
        return RolloutExport(
            episode_id=trajectory.episode.episode_id,
            format=format,
            schema_version=schema_version,
            sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            content=content,
        )

    @staticmethod
    def export_json(trajectory: Trajectory) -> RolloutExport:
        """Export canonical JSON with its schema and byte hash."""
        return RolloutWorker.export(trajectory, "json")

    @staticmethod
    def export_jsonl(trajectory: Trajectory) -> RolloutExport:
        """Export deterministic JSONL with its schema and byte hash."""
        return RolloutWorker.export(trajectory, "jsonl")

    @staticmethod
    def export_atif(
        trajectory: Trajectory, *, children: Iterable[Trajectory] = ()
    ) -> RolloutExport:
        """Export the Harbor/ATIF interchange projection with its byte hash."""
        return RolloutWorker.export(trajectory, "atif", children=children)


__all__ = ["RecoveryResult", "RolloutExport", "RolloutWorker", "WorkerFormat"]
