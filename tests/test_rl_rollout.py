from __future__ import annotations

import pytest

from openai4s.agent.engine import AgentEngine
from openai4s.agent.models import ExecutionOutcome, ModelReply
from openai4s.config import Config
from openai4s.rl import Episode, RolloutSession, TrajectoryRecorder
from openai4s.store import get_store


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


def test_rollout_receipt_prevents_exact_retry_after_completion(tmp_path) -> None:
    store = get_store(Config(data_dir=tmp_path).db_path)
    try:

        def make_session():
            engine = AgentEngine(_NeverCalledModel(), _Executor(), max_turns=1)
            recorder = TrajectoryRecorder(
                Episode("ep-receipt", "task", "session", 1), repository=store.rl
            )
            return RolloutSession(
                engine,
                recorder,
                repository=store.rl,
                request_id="request-receipt",
            )

        session = make_session()
        session.reset([{"role": "user", "content": "run"}])
        session.run(lambda _observation: ModelReply(content="done"))
        assert store.rl.get_receipt("request-receipt").state == "completed"

        retry = make_session()
        with pytest.raises(RuntimeError, match="already settled"):
            retry.reset([{"role": "user", "content": "run"}])
        assert retry.state is None
    finally:
        store.close()


def test_rollout_receipt_is_settled_when_sampler_fails(tmp_path) -> None:
    store = get_store(Config(data_dir=tmp_path).db_path)
    try:
        engine = AgentEngine(_NeverCalledModel(), _Executor(), max_turns=1)
        recorder = TrajectoryRecorder(
            Episode("ep-failed-receipt", "task", "session", 1),
            repository=store.rl,
        )
        session = RolloutSession(
            engine,
            recorder,
            repository=store.rl,
            request_id="request-failed-receipt",
        )
        session.reset([{"role": "user", "content": "run"}])

        def fail(_observation):
            raise RuntimeError("sampler failed")

        with pytest.raises(RuntimeError, match="sampler failed"):
            session.run(fail)
        assert store.rl.get_receipt("request-failed-receipt").state == "failed"
    finally:
        store.close()
