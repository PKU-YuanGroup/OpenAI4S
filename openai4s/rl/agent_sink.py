"""Bridge provider-neutral AgentEngine events into a Science Trajectory."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any, Iterable, Mapping

from openai4s.agent.actions import (
    Action,
    CodeCell,
    FinalizeAction,
    NativeToolBatch,
    NativeToolCall,
)
from openai4s.agent.events import (
    ActionRouted,
    OutcomeProduced,
    ReplyReceived,
    RunFinished,
)
from openai4s.agent.models import ModelReply

from .models import CostRecord, ExecutionEvent, Observation, PolicyDecision
from .recorder import TrajectoryRecorder


def _cost_record(
    episode_id: str,
    turn: int,
    usage: Any,
    created_at: int,
    *,
    decision_id: str,
) -> CostRecord | None:
    """Project authenticated provider counters into one RL cost fact.

    Pricing depends on the provider/model and belongs to the ledger's pricing
    boundary. The trajectory records token evidence here and leaves
    ``cost_usd`` unknown rather than trusting a model supplied dollar value.
    """

    from openai4s.llm.usage import measured_usage

    measured = measured_usage(usage)
    if "input_tokens" not in measured or "output_tokens" not in measured:
        return None
    metadata = {
        key: int(measured[key])
        for key in (
            "total_tokens",
            "cache_read",
            "cache_write",
            "reasoning_tokens",
        )
        if key in measured
    }
    return CostRecord(
        cost_id=f"{episode_id}:cost-{turn}",
        episode_id=episode_id,
        created_at=created_at,
        prompt_tokens=int(measured["input_tokens"]),
        completion_tokens=int(measured["output_tokens"]),
        source="policy_reply",
        decision_id=decision_id,
        metadata=metadata,
    )


def _action(value: Action | None) -> dict[str, Any]:
    if value is None:
        return {"type": "none"}
    if isinstance(value, CodeCell):
        return {"type": "code_cell", "language": value.language, "code": value.code}
    if isinstance(value, FinalizeAction):
        return {"type": "finalize", "call": asdict(value.call)}
    if isinstance(value, NativeToolBatch):
        return {
            "type": "native_tool_batch",
            "calls": [asdict(call) for call in value.calls],
        }
    raise TypeError(f"unsupported action {type(value)!r}")


def _prompt_hash(messages: Iterable[Mapping[str, Any]]) -> str:
    payload = json.dumps(
        list(messages), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class TrajectoryEventSink:
    """Forward AgentEngine events and capture one episode's RL records.

    The sink is intentionally an adapter rather than a replacement for the
    Action Ledger.  Its execution events carry source IDs and payloads; the
    canonical ledger remains the source of execution truth and can be joined
    later by frame/turn or an explicit action-group binding.
    """

    def __init__(
        self,
        recorder: TrajectoryRecorder,
        delegate: Any | None = None,
        *,
        policy_name: str | None = None,
        policy_version: str | None = None,
        parent_decision_id: str | None = None,
        frame_id: str | None = None,
        id_prefix: str | None = None,
    ) -> None:
        self.recorder = recorder
        self.delegate = delegate
        self.policy_name = policy_name or recorder.episode.policy_name or "unknown"
        self.policy_version = (
            policy_version or recorder.episode.policy_version or "unknown"
        )
        self.parent_decision_id = parent_decision_id
        self.frame_id = frame_id
        self.id_prefix = str(id_prefix or "").strip()
        self._observations: dict[int, Observation] = {}
        self._replies: dict[int, ModelReply] = {}

    def decision_id(self, turn: int) -> str:
        base = f"decision-{turn}"
        return f"{self.id_prefix}:{base}" if self.id_prefix else base

    def _record_id(self, kind: str, turn: int) -> str:
        base = f"{kind}-{turn}"
        return f"{self.id_prefix}:{base}" if self.id_prefix else base

    def propose(self, turn: int, messages: Iterable[Mapping[str, Any]]) -> Observation:
        observation = Observation(
            observation_id=self._record_id("obs", turn),
            episode_id=self.recorder.episode.episode_id,
            decision_id=self.decision_id(turn),
            messages=[dict(message) for message in messages],
            created_at=self.recorder.now_ms(),
        )
        self._observations[turn] = self.recorder.add_observation(observation)
        return observation

    def emit(self, event: Any) -> None:
        if self.delegate is not None:
            self.delegate.emit(event)
        if isinstance(event, ReplyReceived):
            self._replies[event.turn] = event.reply
            cost = _cost_record(
                self.recorder.episode.episode_id,
                event.turn,
                event.reply.usage,
                self.recorder.now_ms(),
                decision_id=self.decision_id(event.turn),
            )
            if cost is not None:
                self.recorder.add_cost(cost)
        elif isinstance(event, ActionRouted):
            self._record_decision(event.turn, event.action)
            self.recorder.add_event(
                ExecutionEvent(
                    event_id=self._record_id("action", event.turn),
                    episode_id=self.recorder.episode.episode_id,
                    kind="action.dispatched",
                    created_at=self.recorder.now_ms(),
                    decision_id=self.decision_id(event.turn),
                    payload={"action": _action(event.action)},
                )
            )
        elif isinstance(event, OutcomeProduced):
            outcome = event.outcome
            self.recorder.add_event(
                ExecutionEvent(
                    event_id=self._record_id("outcome", event.turn),
                    episode_id=self.recorder.episode.episode_id,
                    kind="execution.completed",
                    created_at=self.recorder.now_ms(),
                    decision_id=self.decision_id(event.turn),
                    payload={
                        "observation": outcome.observation,
                        "completion": outcome.completion,
                        "stop_reason": outcome.stop_reason,
                    },
                )
            )
        elif isinstance(event, RunFinished):
            result = event.result
            self.recorder.add_event(
                ExecutionEvent(
                    event_id=(
                        f"{self.id_prefix}:episode.finished"
                        if self.id_prefix
                        else "episode.finished"
                    ),
                    episode_id=self.recorder.episode.episode_id,
                    kind="episode.finished",
                    created_at=self.recorder.now_ms(),
                    payload={
                        "stop_reason": result.stop_reason,
                        "completion": result.completion,
                        "turns": result.turns,
                    },
                )
            )
            self.recorder.finish(
                "completed"
                if result.stop_reason in {"submitted", "plan"}
                else result.stop_reason
            )

    def _record_decision(self, turn: int, action: Action | None) -> None:
        reply = self._replies.get(turn)
        observation = self._observations.get(turn)
        if reply is None or observation is None:
            raise RuntimeError(
                "ActionRouted arrived before trajectory decision proposal"
            )
        extra = reply.extra
        # Carry the durable RL join keys through the existing execution
        # context. The executor/Kernel manager forwards these keys to the Host
        # Dispatcher; Host RPCs remain execution events, not new decisions.
        extra.setdefault("rl_episode_id", self.recorder.episode.episode_id)
        extra.setdefault("rl_decision_id", self.decision_id(turn))
        token_ids = extra.get("token_ids")
        token_logprobs = extra.get("token_logprobs")
        loss_mask = extra.get("loss_mask")
        raw_response = reply.raw if isinstance(reply.raw, Mapping) else None
        self.recorder.add_decision(
            PolicyDecision(
                decision_id=self.decision_id(turn),
                episode_id=self.recorder.episode.episode_id,
                turn=turn,
                observation_id=observation.observation_id,
                prompt_hash=_prompt_hash(observation.messages),
                policy_name=str(extra.get("policy_name", self.policy_name)),
                policy_version=str(extra.get("policy_version", self.policy_version)),
                action=_action(action),
                created_at=self.recorder.now_ms(),
                parent_decision_id=self.parent_decision_id,
                frame_id=self.frame_id,
                action_logprob=extra.get("action_logprob"),
                token_ids=token_ids,
                token_logprobs=token_logprobs,
                loss_mask=loss_mask,
                raw_response=raw_response,
                status="dispatched",
                assistant_message=reply.assistant_message,
            )
        )
