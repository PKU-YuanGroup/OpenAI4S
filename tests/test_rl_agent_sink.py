from __future__ import annotations

from openai4s.agent.engine import AgentEngine
from openai4s.agent.models import ExecutionOutcome, ModelReply, RunState
from openai4s.rl import Episode, TrajectoryEventSink, TrajectoryRecorder


class _Executor:
    def execute(self, action, reply, state):
        return ExecutionOutcome(observation={"ok": True})


def test_agent_events_export_policy_decision_and_execution_facts() -> None:
    recorder = TrajectoryRecorder(
        Episode("ep-1", "test task", "session-1", 1, "test-policy", "v1")
    )
    sink = TrajectoryEventSink(recorder)
    engine = AgentEngine(_Model(), _Executor(), event_sink=sink, max_turns=1)
    result = engine.run(RunState([{"role": "user", "content": "go"}], max_turns=1))

    trajectory = recorder.snapshot()
    assert result.stop_reason == "max_turns"
    assert trajectory.episode.status == "max_turns"
    assert [item.observation_id for item in trajectory.observations] == ["obs-0"]
    assert [item.decision_id for item in trajectory.decisions] == ["decision-0"]
    assert [item.kind for item in trajectory.execution_events] == [
        "action.dispatched",
        "execution.completed",
        "episode.finished",
    ]


class _Model:
    def complete(self, messages, on_delta):
        return ModelReply(
            content="```python\nprint(1)\n```",
            extra={
                "policy_name": "external",
                "policy_version": "vllm-7",
                "token_ids": [1, 2],
                "token_logprobs": [-0.1, -0.2],
                "loss_mask": [0, 1],
                "action_logprob": -0.3,
            },
        )
