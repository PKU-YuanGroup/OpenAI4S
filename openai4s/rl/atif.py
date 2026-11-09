"""Dependency-free ATIF v1.8 projection of a Science Trajectory.

Specification: https://github.com/harbor-framework/harbor/blob/main/rfcs/0001-trajectory-format.md
The exact Science Trajectory is retained under extra.openai4s; the ATIF steps
are a viewer/interchange projection, not a replacement for policy contexts.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping

from openai4s import __version__

from .models import PolicyDecision, Trajectory


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

    def __init__(self, trajectory: Trajectory, *, agent_version: str = __version__):
        self.trajectory = trajectory
        self.agent_version = agent_version

    def to_dict(self) -> dict[str, Any]:
        trajectory = self.trajectory
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
            },
        }

    def export_json(self) -> str:
        return _json_text(self.to_dict())

    def export(self) -> str:
        """Return the canonical JSON document."""
        return self.export_json()


__all__ = ["ATIFExporter"]
