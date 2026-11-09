from __future__ import annotations

import threading

import pytest

from openai4s.agent.engine import AgentEngine
from openai4s.agent.models import ExecutionOutcome, ModelReply
from openai4s.config import Config
from openai4s.rl import (
    Episode,
    ExecutionEvent,
    Observation,
    PolicyAdapter,
    PolicyDecision,
    RolloutEnvironment,
    RolloutSession,
    RolloutWorker,
    RunReceipt,
    TrajectoryRecorder,
    TrajectoryReplayer,
)
from openai4s.rl.replay import ReplayCursorError, ReplayUnavailable
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


def test_rollout_worker_releases_environment_and_exports_both_formats(tmp_path):
    def build_engine(_reset):
        return AgentEngine(_NeverCalledModel(), _Executor(), max_turns=1)

    worker = RolloutWorker(RolloutEnvironment(build_engine, workspace_root=tmp_path))
    trajectory = worker.run(
        Episode("ep-worker", "task", "session", 1),
        [{"role": "user", "content": "run"}],
        lambda _observation: ModelReply(content="done"),
    )
    assert trajectory.episode.status == "done"
    json_export = worker.export_json(trajectory)
    assert json_export.format == "json"
    assert json_export.schema_version == trajectory.schema_version
    assert json_export.content == trajectory.canonical_json()
    exported = worker.export(trajectory, "jsonl")
    assert exported.format == "jsonl"
    assert (
        exported.sha256
        == __import__("hashlib").sha256(exported.content.encode("utf-8")).hexdigest()
    )
    assert exported.content.splitlines()[0].startswith('{"episode":')
    assert worker.environment.active is False


def test_rollout_worker_recovery_never_replays_running_receipt(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    try:
        store.rl.admit_receipt(
            RunReceipt("request-recover", "fp", "ep-recover", "accepted", 1, 1)
        )
        store.rl.update_receipt_state("request-recover", "running")
        worker = RolloutWorker(
            RolloutEnvironment(
                lambda _reset: AgentEngine(
                    _NeverCalledModel(), _Executor(), max_turns=1
                )
            ),
            repository=store.rl,
        )
        recovered = worker.recover("request-recover")
        assert recovered.status == "recovery_required"
        assert recovered.can_resume is False
    finally:
        store.close()


def test_rollout_worker_refuses_running_request_before_engine_factory(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    created = []
    try:
        store.rl.admit_receipt(
            RunReceipt("request-running", "fp", "ep-running", "accepted", 1, 1)
        )
        store.rl.update_receipt_state("request-running", "running")
        environment = RolloutEnvironment(
            lambda reset: created.append(reset)
            or AgentEngine(_NeverCalledModel(), _Executor(), max_turns=1),
            repository=store.rl,
            workspace_root=tmp_path / "workspaces",
        )
        worker = RolloutWorker(environment)
        with pytest.raises(RuntimeError, match="recovery_required"):
            worker.run(
                Episode("ep-running", "task", "session", 1),
                [{"role": "user", "content": "run"}],
                lambda _observation: ModelReply(content="done"),
                request_id="request-running",
            )
        assert created == []
        assert environment.active is False
    finally:
        store.close()


def test_rollout_worker_repository_binds_environment_for_run_receipts(tmp_path):
    store = get_store(Config(data_dir=tmp_path / "store").db_path)
    try:
        environment = RolloutEnvironment(
            lambda _reset: AgentEngine(_NeverCalledModel(), _Executor(), max_turns=1),
            workspace_root=tmp_path / "workspaces",
        )
        worker = RolloutWorker(environment, repository=store.rl)
        trajectory = worker.run(
            Episode("ep-worker-receipt", "task", "session", 1),
            [{"role": "user", "content": "run"}],
            lambda _observation: ModelReply(content="done"),
            request_id="request-worker-receipt",
        )
        assert trajectory.episode.status == "done"
        assert store.rl.get_receipt("request-worker-receipt").state == "completed"
        assert environment.repository is store.rl
    finally:
        store.close()


def test_rollout_worker_does_not_settle_receipt_on_process_interrupt(tmp_path):
    store = get_store(Config(data_dir=tmp_path / "store").db_path)
    try:
        environment = RolloutEnvironment(
            lambda _reset: AgentEngine(_NeverCalledModel(), _Executor(), max_turns=1),
            workspace_root=tmp_path / "workspaces",
        )
        worker = RolloutWorker(environment, repository=store.rl)

        def interrupted(_observation):
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            worker.run(
                Episode("ep-worker-interrupt", "task", "session", 1),
                [{"role": "user", "content": "run"}],
                interrupted,
                request_id="request-worker-interrupt",
            )
        assert store.rl.get_receipt("request-worker-interrupt").state == "running"
        assert environment.active is True
    finally:
        store.close()


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


def test_trajectory_replayer_provides_cursored_event_and_decision_views() -> None:
    recorder = TrajectoryRecorder(Episode("ep-replay", "task", "session", 1))
    recorder.add_observation(
        Observation(
            "obs-0",
            "ep-replay",
            "decision-0",
            [{"role": "user", "content": "run"}],
            2,
        )
    )
    recorder.add_decision(
        PolicyDecision(
            "decision-0",
            "ep-replay",
            0,
            "obs-0",
            "prompt-hash",
            "policy",
            "v1",
            {"type": "code_cell"},
            3,
        )
    )
    recorder.add_event(
        ExecutionEvent(
            "event-1",
            "ep-replay",
            "artifact.created",
            4,
            {"output_version_id": "v-out", "checksum": "abc"},
        )
    )
    recorder.add_event(ExecutionEvent("event-2", "ep-replay", "cell.completed", 5))
    recorder.finish("success")
    snapshot = recorder.create_snapshot("snapshot-replay", created_at=6)

    class _Repository:
        def load_trajectory(self, episode_id):
            assert episode_id == "ep-replay"
            return recorder.snapshot()

        def get_snapshot(self, snapshot_id):
            assert snapshot_id == "snapshot-replay"
            return snapshot

    replayer = TrajectoryReplayer(_Repository())
    page = replayer.replay(
        "ep-replay", "event", after_sequence=0, limit=1, snapshot_id="snapshot-replay"
    )
    assert [item["event_id"] for item in page.records] == ["event-1"]
    assert page.complete is False
    assert page.next_sequence == 2
    rest = replayer.replay("ep-replay", "event", after_sequence=1)
    assert [item["event_id"] for item in rest.records] == ["event-2"]

    decisions = replayer.replay("ep-replay", "decision")
    assert [item["type"] for item in decisions.records] == [
        "observation",
        "decision",
    ]
    artifacts = replayer.replay(
        "ep-replay",
        "artifact",
        artifact_checker=lambda version_id: {
            "version_id": version_id,
            "checksum": "abc",
        },
    )
    assert artifacts.verified is True
    assert artifacts.records[0]["version_id"] == "v-out"
    with pytest.raises(ReplayCursorError, match="ahead"):
        replayer.replay("ep-replay", "event", after_sequence=3)
    with pytest.raises(ReplayUnavailable, match="controlled runner"):
        replayer.replay("ep-replay", "execution")


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
