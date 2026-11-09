from __future__ import annotations

from openai4s.agent.engine import AgentEngine
from openai4s.agent.models import ExecutionOutcome, ModelReply
from openai4s.rl import Episode, RolloutSession, TrajectoryRecorder


class _Executor:
    def execute(self, action, reply, state):
        return ExecutionOutcome(observation="executed", stop_reason="done")


def test_rollout_session_runs_external_sampler_and_exports_terminal_trajectory() -> (
    None
):
    engine = AgentEngine(_NeverCalledModel(), _Executor(), max_turns=2)
    recorder = TrajectoryRecorder(Episode("ep-rollout", "task", "session", 1))
    session = RolloutSession(engine, recorder)
    session.reset([{"role": "user", "content": "run"}])

    trajectory = session.run(
        lambda observation: ModelReply(
            content="done",
            extra={"policy_name": "vllm", "policy_version": "step-1"},
        )
    )

    assert trajectory.episode.status == "done"
    assert trajectory.decisions[0].policy_name == "vllm"
    assert trajectory.execution_events[-1].kind == "episode.finished"
    assert session.export() == trajectory.canonical_json()


class _NeverCalledModel:
    def complete(self, messages, on_delta):
        raise AssertionError("external sampler should own model generation")
