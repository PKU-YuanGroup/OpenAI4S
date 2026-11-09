from __future__ import annotations

import threading

import pytest

from openai4s.agent.engine import AgentEngine
from openai4s.agent.models import ExecutionOutcome, ModelReply
from openai4s.config import Config
from openai4s.rl import (
    Episode,
    PolicyAdapter,
    RolloutEnvironment,
    RolloutSession,
    TrajectoryRecorder,
)
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


def test_policy_adapter_preserves_behavior_identity_and_missing_logprobs() -> None:
    class _Provider:
        def generate(self, observation, *, config):
            assert observation[0]["content"] == "run"
            assert config["temperature"] == 0.7
            return {
                "content": "done",
                "policy_name": "spoofed",
                "policy_version": "spoofed",
            }

    adapter = PolicyAdapter(
        _Provider(),
        policy_name="vllm",
        policy_version="weights-42",
        config={"temperature": 0.7},
    )
    reply = adapter.sample(({"role": "user", "content": "run"},))
    assert reply.extra["policy_name"] == "vllm"
    assert reply.extra["policy_version"] == "weights-42"
    assert reply.extra["sampling_config"] == {"temperature": 0.7}
    assert reply.extra["behavior_logprobs_available"] is False
    assert "action_logprob" not in reply.extra


def test_rollout_environment_resets_with_independent_workspace_and_metadata(tmp_path):
    created = []

    def build_engine(reset):
        created.append(reset)
        return AgentEngine(_NeverCalledModel(), _Executor(), max_turns=1)

    environment = RolloutEnvironment(build_engine, workspace_root=tmp_path)
    first = environment.reset(
        Episode("ep-isolated-1", "task", "session", 1),
        [{"role": "user", "content": "one"}],
        initial_artifacts={"input": "version-1"},
        config={"seed": 11},
        budget={"wall_time_ms": 1000},
    )
    first_workspace = created[0].workspace
    assert first_workspace.is_dir()
    assert first.state is not None
    assert first.state.metadata["rl_reset"]["initial_artifacts"] == {
        "input": "version-1"
    }
    with pytest.raises(RuntimeError, match="already has an active"):
        environment.reset(
            Episode("ep-isolated-2", "task", "session", 1),
            [{"role": "user", "content": "two"}],
        )

    first.run(lambda _observation: ModelReply(content="done"))
    first_trajectory = environment.release(first)
    assert first_trajectory.episode.metadata["reset"]["workspace_id"].startswith(
        "workspace-"
    )
    assert not first_workspace.exists()

    second = environment.reset(
        Episode("ep-isolated-2", "task", "session", 1),
        [{"role": "user", "content": "two"}],
    )
    assert created[1].workspace != first_workspace
    second.run(lambda _observation: ModelReply(content="done"))
    environment.release(second)


def test_rollout_environment_reserves_reset_while_engine_is_constructed(tmp_path):
    started = threading.Event()
    proceed = threading.Event()
    created = []

    def build_engine(reset):
        created.append(reset)
        started.set()
        assert proceed.wait(timeout=5)
        return AgentEngine(_NeverCalledModel(), _Executor(), max_turns=1)

    environment = RolloutEnvironment(build_engine, workspace_root=tmp_path)
    holder = []

    def reset_first():
        holder.append(
            environment.reset(
                Episode("ep-concurrent-1", "task", "session", 1),
                [{"role": "user", "content": "one"}],
            )
        )

    thread = threading.Thread(target=reset_first)
    thread.start()
    assert started.wait(timeout=5)
    try:
        with pytest.raises(RuntimeError, match="already has an active"):
            environment.reset(
                Episode("ep-concurrent-2", "task", "session", 1),
                [{"role": "user", "content": "two"}],
            )
    finally:
        proceed.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(holder) == 1
    holder[0].finish()
    environment.release(holder[0])


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
