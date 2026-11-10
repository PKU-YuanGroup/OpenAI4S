"""Dependency-free ATIF v1.8 projection of a Science Trajectory.

Specification: https://github.com/harbor-framework/harbor/blob/main/rfcs/0001-trajectory-format.md
The exact Science Trajectory is retained under extra.openai4s; the ATIF steps
are a viewer/interchange projection, not a replacement for policy contexts.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from openai4s import __version__

from .models import PolicyDecision, Trajectory

_STOP_REASON_FOR_STATUS = {
    "completed": {"completed", "submitted", "plan"},
    "success": {"completed", "submitted", "plan", "success"},
    "submitted": {"completed", "submitted"},
    "done": {"done"},
    "failed": {"failed", "error"},
    "error": {"failed", "error"},
    "cancelled": {"cancelled"},
    "interrupted": {"interrupted", "timeout"},
    "timeout": {"interrupted", "timeout"},
    "max_turns": {"max_turns"},
    "no_progress": {"no_progress"},
    "scientific_failure": {"scientific_failure"},
    "budget_exhausted": {"budget_exhausted"},
    "execution_failure": {"execution_failure"},
    "verifier_failure": {"verifier_failure"},
}
_POST_TERMINAL_PROJECTIONS = frozenset(
    {"artifact.version", "compute.job", "compute.job_event"}
)


@dataclass(frozen=True)
class CompletionContract:
    """Evidence about rollout termination, separate from scientific reward."""

    status: str
    terminal: bool
    trace_complete: bool
    completion_evidence: bool
    terminal_event_id: str | None = None
    failure: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "terminal": self.terminal,
            "trace_complete": self.trace_complete,
            "completion_evidence": self.completion_evidence,
            "terminal_event_id": self.terminal_event_id,
            "failure": self.failure,
        }


def completion_contract(trajectory: Trajectory) -> CompletionContract:
    """Validate positive terminal evidence without scoring the Episode.

    A terminal status is a valid rollout outcome, including ``failed``,
    ``cancelled`` and ``max_turns``.  Only a ``completed``/legacy ``success``
    status carrying non-null completion evidence is marked as having positive
    completion evidence.  This is not a scientific reward or verifier result.
    A normal ``done`` status remains terminal without that evidence.
    """

    if not isinstance(trajectory, Trajectory):
        raise TypeError("trajectory must be a Trajectory")
    status = trajectory.episode.status
    terminal_events = [
        event
        for event in trajectory.execution_events
        if event.kind == "episode.finished"
    ]
    if status == "running":
        if terminal_events:
            return CompletionContract(
                status=status,
                terminal=False,
                trace_complete=False,
                completion_evidence=False,
                terminal_event_id=terminal_events[-1].event_id,
                failure="running episode has a terminal event",
            )
        return CompletionContract(
            status=status,
            terminal=False,
            trace_complete=False,
            completion_evidence=False,
            failure="trajectory is unfinished",
        )
    if status not in _STOP_REASON_FOR_STATUS:
        return CompletionContract(
            status=status,
            terminal=True,
            trace_complete=False,
            completion_evidence=False,
            failure=f"unknown terminal status {status!r}",
        )
    if len(terminal_events) != 1:
        return CompletionContract(
            status=status,
            terminal=True,
            trace_complete=False,
            completion_evidence=False,
            failure="expected exactly one episode.finished event",
        )
    ordered_events = sorted(
        trajectory.execution_events,
        key=lambda item: (
            item.sequence if item.sequence is not None else 0,
            item.event_id,
        ),
    )
    terminal = terminal_events[0]
    lifecycle_events = [
        event
        for event in ordered_events
        if event.kind not in _POST_TERMINAL_PROJECTIONS
    ]
    if not lifecycle_events or lifecycle_events[-1].event_id != terminal.event_id:
        return CompletionContract(
            status=status,
            terminal=True,
            trace_complete=False,
            completion_evidence=False,
            terminal_event_id=terminal.event_id,
            failure="episode.finished must be the final execution event",
        )
    stop_reason = terminal.payload.get("stop_reason")
    if not isinstance(stop_reason, str) or not stop_reason:
        return CompletionContract(
            status=status,
            terminal=True,
            trace_complete=False,
            completion_evidence=False,
            terminal_event_id=terminal.event_id,
            failure="episode.finished is missing stop_reason",
        )
    if stop_reason not in _STOP_REASON_FOR_STATUS[status]:
        return CompletionContract(
            status=status,
            terminal=True,
            trace_complete=False,
            completion_evidence=False,
            terminal_event_id=terminal.event_id,
            failure=(
                f"stop_reason {stop_reason!r} does not match terminal status {status!r}"
            ),
        )
    completion = terminal.payload.get("completion")
    evidence = status in {"completed", "success"} and completion is not None
    if status in {"completed", "success"} and completion is None:
        return CompletionContract(
            status=status,
            terminal=True,
            trace_complete=False,
            completion_evidence=False,
            terminal_event_id=terminal.event_id,
            failure="completed status has no positive completion evidence",
        )
    return CompletionContract(
        status=status,
        terminal=True,
        trace_complete=True,
        completion_evidence=evidence,
        terminal_event_id=terminal.event_id,
    )


def completion_failure(trajectory: Trajectory) -> str | None:
    """Return the contract failure, if any, for Harbor-style validation."""

    return completion_contract(trajectory).failure


def _json_text(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _message_text(message: Mapping[str, Any] | None) -> str:
    content = message.get("content") if message is not None else None
    # Provider multimodal parts are not ATIF file references. Preserve them in
    # the source extension rather than relabeling them or synthesizing text.
    return content if isinstance(content, str) else ""


def _tool_calls(decision: PolicyDecision) -> list[dict[str, Any]]:
    action = decision.action
    if action.get("type") == "native_tool_batch":
        calls = action["calls"]
    elif action.get("type") == "finalize":
        calls = [action["call"]]
    else:
        # Code cells are non-tool actions in ATIF. Their exact code and
        # language stay in the decision extension; no native call is invented.
        return []
    result = []
    for call in calls:
        arguments = call.get("arguments")
        result.append(
            {
                "tool_call_id": call["id"],
                "function_name": call["name"],
                "arguments": dict(arguments) if isinstance(arguments, Mapping) else {},
                "extra": {
                    "raw_arguments": call.get("raw_arguments"),
                    "parse_error": call.get("parse_error"),
                    "arguments_available": isinstance(arguments, Mapping),
                },
            }
        )
    return result


class ATIFExporter:
    """Export one episode without inferring rewards, costs, or policy text."""

    def __init__(
        self,
        trajectory: Trajectory,
        *,
        agent_version: str = __version__,
        children: Iterable[Trajectory] = (),
    ):
        self.trajectory = trajectory
        self.agent_version = agent_version
        child_values = tuple(children)
        if any(not isinstance(child, Trajectory) for child in child_values):
            raise TypeError("children must contain Trajectory values")
        if len({child.episode.episode_id for child in child_values}) != len(
            child_values
        ):
            raise ValueError("children must have unique episode IDs")
        for child in child_values:
            if child.episode.parent_episode_id != trajectory.episode.episode_id:
                raise ValueError("child trajectory is not linked to the root episode")
        self.children = tuple(
            sorted(child_values, key=lambda child: child.episode.episode_id)
        )

    def to_dict(self) -> dict[str, Any]:
        trajectory = self.trajectory
        contract = completion_contract(trajectory)
        # Detach every nested extension from the source records.
        source = json.loads(trajectory.canonical_json())
        decisions = sorted(
            trajectory.decisions, key=lambda item: (item.turn, item.decision_id)
        )
        observations = {item.observation_id: item for item in trajectory.observations}
        initial = (
            observations.get(decisions[0].observation_id)
            if decisions
            else next(iter(trajectory.observations), None)
        )
        steps: list[dict[str, Any]] = []
        if initial is not None:
            for message in initial.messages:
                role = message.get("role")
                role_text = role if isinstance(role, str) else "system"
                steps.append(
                    {
                        "step_id": len(steps) + 1,
                        "source": {"user": "user", "assistant": "agent"}.get(
                            role_text, "system"
                        ),
                        "message": _message_text(message),
                        "extra": {
                            "original_role": role,
                            "observation_id": initial.observation_id,
                        },
                    }
                )
        outcomes: dict[str, list[dict[str, Any]]] = {}
        for event in source["execution_events"]:
            if event["kind"] != "execution.completed" or event["decision_id"] is None:
                continue
            observation = event["payload"].get("observation", event["payload"])
            outcomes.setdefault(event["decision_id"], []).append(
                {
                    "content": (
                        observation
                        if isinstance(observation, str)
                        else _json_text(observation)
                    ),
                    # The Engine outcome may cover a batch. Its per-call
                    # attribution is unknown, so source_call_id stays absent.
                    "extra": {
                        "event_id": event["event_id"],
                        "source_id": event["source_id"],
                    },
                }
            )
        for decision in decisions:
            step: dict[str, Any] = {
                "step_id": len(steps) + 1,
                "timestamp": datetime.fromtimestamp(
                    decision.created_at / 1000, timezone.utc
                )
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
                "source": "agent",
                "message": _message_text(decision.assistant_message),
                "llm_call_count": 1,
                "extra": {
                    "decision_id": decision.decision_id,
                    "observation_id": decision.observation_id,
                    "policy_name": decision.policy_name,
                    "policy_version": decision.policy_version,
                    "text_available": isinstance(
                        (decision.assistant_message or {}).get("content"), str
                    ),
                },
            }
            calls = _tool_calls(decision)
            if calls:
                step["tool_calls"] = calls
            if decision.decision_id in outcomes:
                step["observation"] = {"results": outcomes[decision.decision_id]}
            metrics: dict[str, Any] = {}
            if decision.token_ids is not None:
                metrics["completion_token_ids"] = list(decision.token_ids)
            if decision.token_logprobs is not None:
                metrics["logprobs"] = list(decision.token_logprobs)
            for name in ("action_logprob", "loss_mask"):
                value = getattr(decision, name)
                if value is not None:
                    metrics.setdefault("extra", {})[name] = (
                        list(value) if name == "loss_mask" else value
                    )
            costs = [
                item
                for item in trajectory.costs
                if item.decision_id == decision.decision_id
            ]
            for field in ("prompt_tokens", "completion_tokens", "cost_usd"):
                values = [
                    getattr(item, field)
                    for item in costs
                    if getattr(item, field) is not None
                ]
                if values:
                    metrics[field] = sum(values)
            if costs:
                metrics.setdefault("extra", {})["cost_ids"] = [
                    item.cost_id for item in costs
                ]
            if metrics:
                step["metrics"] = metrics
            steps.append(step)
        child_documents: list[dict[str, Any]] = []
        for child in self.children:
            child_document = ATIFExporter(
                child, agent_version=self.agent_version
            ).to_dict()
            parent_decisions = {
                decision.parent_decision_id
                for decision in child.decisions
                if decision.parent_decision_id is not None
            }
            for step in steps:
                extra = step.setdefault("extra", {})
                decision_id = extra.get("decision_id")
                if decision_id in parent_decisions:
                    refs = extra.setdefault("subagent_trajectory_refs", [])
                    refs.append(
                        {
                            "trajectory_id": child.episode.episode_id,
                            "session_id": child.episode.session_id,
                        }
                    )
            child_documents.append(
                {
                    "trajectory_id": child.episode.episode_id,
                    "parent_episode_id": child.episode.parent_episode_id,
                    "parent_decision_ids": sorted(parent_decisions),
                    "document": child_document,
                }
            )
        if not steps:
            steps.append(
                {
                    "step_id": 1,
                    "source": "system",
                    "message": "",
                    "extra": {"kind": "episode_without_decisions"},
                }
            )
        return {
            "schema_version": "ATIF-v1.8",
            "session_id": trajectory.episode.session_id,
            "trajectory_id": trajectory.episode.episode_id,
            "agent": {"name": "openai4s", "version": self.agent_version},
            "steps": steps,
            "notes": (
                "One new agent step per PolicyDecision. Initial context is represented "
                "from the first observed context. "
                "Exact per-decision contexts, non-text content, code actions, raw responses, "
                "execution events, versioned rewards and costs are in extra.openai4s. "
                "Aggregate outcomes are not attributed to individual tool calls."
            ),
            "final_metrics": {"total_steps": len(steps)},
            "extra": {
                "openai4s": source,
                "science_trajectory_sha256": trajectory.sha256(),
                "completion_contract": contract.to_dict(),
                "child_trajectories": child_documents,
            },
        }

    def export_json(self) -> str:
        return _json_text(self.to_dict())

    def export(self) -> str:
        """Return the canonical JSON document."""
        return self.export_json()


__all__ = [
    "ATIFExporter",
    "CompletionContract",
    "completion_contract",
    "completion_failure",
]
