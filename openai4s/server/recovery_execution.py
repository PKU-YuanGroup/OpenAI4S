"""Execute one recovery mutation through the verified kernel pipeline.

Policy/input freezing lives in :mod:`recovery_control`; this module owns the
small orchestration adapter that runs every language candidate under one
recovery id, stops after the first incomplete candidate, and emits one durable
session terminal event.  Concrete worker/CAS/artifact ports are supplied by the
Gateway runtime so this service remains directly testable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

from openai4s.kernel.recovery import (
    BootstrapManifest,
    Candidate,
    RecoveryRecipe,
    RecoveryResult,
)
from openai4s.server.recovery_control import RecoveryActionPlan, RecoveryControlService


def _not_cancelled() -> bool:
    return False


def _ignore_candidate(_candidate: Candidate) -> None:
    return None


def _ignore_event(_event: dict[str, Any]) -> None:
    return None


@dataclass(frozen=True)
class RecoveryExecutionPorts:
    build_candidate: Callable[[BootstrapManifest], Candidate]
    bootstrap_candidate: Callable[[Candidate, BootstrapManifest], Any]
    hydrate_workspace: Callable[[Candidate, Mapping[str, Any]], Any]
    hydrate_artifact: Callable[[Candidate, Mapping[str, Any]], Any]
    execute_cell: Callable[[Candidate, str, str], Mapping[str, Any]]
    inspect_symbols: Callable[[Candidate, str], Any]
    artifact_digest: Callable[[Candidate, str], str | None]
    inspect_environment: Callable[[Candidate], Mapping[str, Any]]
    publish_candidate: Callable[[Candidate, BootstrapManifest, str | None], Any]
    cancelled: Callable[[], bool] = _not_cancelled
    candidate_started: Callable[[Candidate], Any] = _ignore_candidate
    candidate_finished: Callable[[Candidate], Any] = _ignore_candidate
    event_sink: Callable[[dict[str, Any]], Any] = _ignore_event


class RecoveryMutationExecutor:
    """Run all checkpoint runtimes without ever replaying an unsafe step."""

    def __init__(
        self,
        control: RecoveryControlService,
        ports: RecoveryExecutionPorts,
    ) -> None:
        self.control = control
        self.ports = ports

    def run(self, plan: RecoveryActionPlan) -> dict[str, Any]:
        results: list[RecoveryResult] = []
        for index, manifest in enumerate(plan.manifests):
            current: list[Candidate] = []

            def build(value: BootstrapManifest) -> Candidate:
                candidate = self.ports.build_candidate(value)
                current.append(candidate)
                self.ports.candidate_started(candidate)
                return candidate

            pipeline = self.control.pipeline(
                build_candidate=build,
                bootstrap_candidate=self.ports.bootstrap_candidate,
                hydrate_workspace=self.ports.hydrate_workspace,
                hydrate_artifact=self.ports.hydrate_artifact,
                execute_cell=self.ports.execute_cell,
                inspect_symbols=self.ports.inspect_symbols,
                artifact_digest=self.ports.artifact_digest,
                inspect_environment=self.ports.inspect_environment,
                publish=lambda candidate, selected=manifest: (
                    self.ports.publish_candidate(
                        candidate,
                        selected,
                        plan.source_generation_ids.get(selected.language),
                    )
                ),
                cancelled=self.ports.cancelled,
            )
            try:
                result = pipeline.restore(
                    root_frame_id=plan.root_frame_id,
                    branch_id=plan.branch_id,
                    manifest=manifest,
                    recipe=_recipe_for_language(
                        plan.recipe,
                        manifest.language,
                        include_hydration=index == 0,
                    ),
                    source_generation_id=plan.source_generation_ids.get(
                        manifest.language
                    ),
                    recovery_id=plan.recovery_id,
                )
            finally:
                for candidate in current:
                    try:
                        self.ports.candidate_finished(candidate)
                    except Exception:  # noqa: BLE001 - pipeline state is canonical
                        pass
            results.append(result)
            if result.status != "active":
                break

        status = _aggregate_status(results, len(plan.manifests))
        detail = {
            "action": plan.action_id,
            "checkpoint_id": plan.checkpoint_id,
            "languages": [manifest.language for manifest in plan.manifests],
            "completed_languages": [
                manifest.language
                for manifest, result in zip(plan.manifests, results)
                if result.status == "active"
            ],
            "issues": [issue for result in results for issue in result.issues],
        }
        self.control.record(
            {
                "recovery_id": plan.recovery_id,
                "root_frame_id": plan.root_frame_id,
                "branch_id": plan.branch_id,
                "phase": "session",
                "status": "completed" if status == "active" else status,
                "detail": detail,
            }
        )
        event = {
            "type": "recovery_state",
            "root_frame_id": plan.root_frame_id,
            "branch_id": plan.branch_id,
            "recovery_id": plan.recovery_id,
            "state": status,
            "status": status,
            "message": f"{plan.action_id}: {status}",
        }
        try:
            self.ports.event_sink(event)
        except Exception:  # noqa: BLE001 - durable terminal event already exists
            pass
        return {
            "ok": status == "active",
            "action": plan.action_id,
            "recovery_id": plan.recovery_id,
            "root_frame_id": plan.root_frame_id,
            "branch_id": plan.branch_id,
            "checkpoint_id": plan.checkpoint_id,
            "status": status,
            "results": [
                {
                    "status": result.status,
                    "source_generation_id": result.source_generation_id,
                    "candidate_generation_id": result.candidate_generation_id,
                    "manifest_id": result.manifest_id,
                    "issues": list(result.issues),
                    "replayed_steps": list(result.replayed_steps),
                    "skipped_steps": list(result.skipped_steps),
                }
                for result in results
            ],
        }


def _recipe_for_language(
    recipe: RecoveryRecipe,
    language: str,
    *,
    include_hydration: bool,
) -> RecoveryRecipe:
    steps = []
    for step in recipe.steps:
        if step.kind in {"hydrate_workspace", "hydrate_artifact"}:
            if include_hydration:
                steps.append(step)
            continue
        if step.kind == "replay_cell":
            if str(step.payload.get("language") or language) == language:
                steps.append(step)
            continue
        # Unknown steps are retained once so the orchestrator reports a
        # partial recovery instead of silently dropping them.
        if include_hydration:
            steps.append(step)

    requirements = recipe.environment_requirements
    selected_requirements: Mapping[str, Any] = requirements
    nested = requirements.get(language) if isinstance(requirements, Mapping) else None
    if isinstance(nested, Mapping):
        selected_requirements = nested
    return RecoveryRecipe(
        steps=tuple(steps),
        required_symbols={language: tuple(recipe.required_symbols.get(language) or ())},
        artifact_hashes=(dict(recipe.artifact_hashes) if include_hydration else {}),
        environment_requirements=dict(selected_requirements),
        namespace_coverage=(
            recipe.namespace_coverage if include_hydration else "verified"
        ),
    )


def _aggregate_status(results: list[RecoveryResult], expected: int) -> str:
    if not results:
        return "cancelled"
    if (
        all(result.status == "active" for result in results)
        and len(results) == expected
    ):
        return "active"
    if any(result.status == "cancelled" for result in results):
        return "cancelled"
    if any(result.status == "partial" for result in results) or any(
        result.status == "active" for result in results
    ):
        return "partial"
    return "failed"


__all__ = ["RecoveryExecutionPorts", "RecoveryMutationExecutor"]
