from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest import mock

import pytest

import openai4s.agent.loop as loop_mod
from openai4s.agent.delegation import DelegationRunner
from openai4s.agent.engine import AgentEngine
from openai4s.agent.events import ReplyReceived
from openai4s.agent.models import ExecutionOutcome, ModelReply, RunState
from openai4s.config import Config
from openai4s.llm import normalize_usage
from openai4s.rl import Episode, TrajectoryEventSink, TrajectoryRecorder
from openai4s.store import get_store


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
        "segment.started",
        "action.dispatched",
        "execution.completed",
        "segment.closed",
        "episode.finished",
    ]
    segment = trajectory.segments[0]
    assert segment.status == "completed"
    assert segment.metadata["openai4s.segment"]["source"] == "inferred"
    assert segment.decision_id == "decision-0"
    assert all(
        event.segment_id == segment.segment_id
        for event in trajectory.execution_events[:-1]
    )
    assert trajectory.costs == ()


def test_agent_sink_persists_tool_projection_and_sampling_evidence() -> None:
    recorder = TrajectoryRecorder(Episode("ep-evidence", "task", "session", 1))

    class _ToolExecutor(_Executor):
        def policy_tools(self, messages):
            return (
                {
                    "name": "inspect",
                    "description": "Inspect",
                    "input_schema": {"type": "object"},
                },
            )

    class _Policy:
        def complete(self, messages, on_delta):
            return ModelReply(
                content="done",
                extra={
                    "prompt_token_ids": [3, 4],
                    "token_ids": [5, 6],
                    "sampling_config": {"temperature": 0.4},
                },
            )

    engine = AgentEngine(
        _Policy(),
        _ToolExecutor(),
        event_sink=TrajectoryEventSink(recorder),
        max_turns=1,
    )
    engine.run(RunState([{"role": "user", "content": "go"}], max_turns=1))
    trajectory = recorder.snapshot()
    assert trajectory.observations[0].tools[0]["name"] == "inspect"
    assert trajectory.decisions[0].prompt_token_ids == (3, 4)
    assert trajectory.decisions[0].token_ids == (5, 6)
    assert trajectory.decisions[0].sampling_config == {"temperature": 0.4}


def test_prompt_hash_includes_tools_without_changing_legacy_empty_tool_hash() -> None:
    from openai4s.rl.agent_sink import _prompt_hash

    messages = [{"role": "user", "content": "go"}]
    legacy = hashlib.sha256(
        json.dumps(
            messages, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    assert _prompt_hash(messages) == legacy
    assert _prompt_hash(messages, [{"name": "one"}]) != _prompt_hash(
        messages, [{"name": "two"}]
    )


def test_agent_sink_records_authenticated_reply_token_cost_without_pricing() -> None:
    recorder = TrajectoryRecorder(Episode("ep-cost", "task", "session", 1))
    sink = TrajectoryEventSink(recorder)
    sink.emit(
        ReplyReceived(
            ModelReply(
                content="done",
                usage=normalize_usage(
                    {"input_tokens": 12, "output_tokens": 5, "cache_read": 2},
                    "chatgpt",
                ),
            ),
            turn=0,
        )
    )

    cost = recorder.snapshot().costs[0]
    assert cost.cost_id == "ep-cost:cost-0"
    assert cost.decision_id == "decision-0"
    assert cost.prompt_tokens == 12
    assert cost.completion_tokens == 5
    assert cost.cost_usd is None
    assert cost.metadata["cache_read"] == 2


def test_agent_sink_can_record_measured_turn_wall_time() -> None:
    recorder = TrajectoryRecorder(Episode("ep-wall", "task", "session", 1))
    ticks = iter((10, 3_010_000))
    sink = TrajectoryEventSink(
        recorder, capture_wall_time=True, clock_ns=lambda: next(ticks)
    )
    engine = AgentEngine(_Model(), _Executor(), event_sink=sink, max_turns=1)

    engine.run(RunState([{"role": "user", "content": "go"}], max_turns=1))

    wall = [item for item in recorder.snapshot().costs if item.source == "turn_wall"]
    assert len(wall) == 1
    assert wall[0].wall_time_ms == 3
    assert wall[0].decision_id == "decision-0"


def test_delegated_worker_trajectory_keeps_parent_episode_and_decision(
    tmp_path: Path,
) -> None:
    cfg = Config(data_dir=tmp_path)
    store = get_store(cfg.db_path)
    parent_frame = store.new_frame(kind="turn")
    store.rl.append_episode(Episode("episode-parent", "parent task", parent_frame, 1))
    arguments = {
        "summary": "completed the delegated task",
        "completion_bullets": ["Completed delegated work"],
    }
    call = {
        "id": "delegate-finalize",
        "wire_id": "delegate-finalize",
        "name": "finalize_response",
        "ordinal": 0,
        "raw_arguments": json.dumps(arguments),
        "arguments": arguments,
        "parse_error": None,
        "provider_meta": {},
    }

    def scripted_chat(*_args, **_kwargs):
        return {
            "content": "",
            "tool_calls": [call],
            "assistant_message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [call],
            },
        }

    runner = DelegationRunner(
        cfg,
        parent_frame_id=parent_frame,
        store=store,
        rl_context={"episode_id": "episode-parent", "decision_id": "decision-parent"},
    )
    try:
        with mock.patch.object(loop_mod, "chat", scripted_chat):
            result = runner(
                {
                    "request": "delegate the analysis",
                    "_rl_parent_context": {
                        "episode_id": "episode-parent",
                        "decision_id": "decision-parent",
                    },
                }
            )
        assert result["stop_reason"] == "submitted"
        child_episode = store._conn.execute(
            "SELECT episode_id, parent_episode_id, status, metadata "
            "FROM rl_episodes WHERE episode_id != ?",
            ("episode-parent",),
        ).fetchone()
        assert child_episode is not None
        assert child_episode["parent_episode_id"] == "episode-parent"
        assert child_episode["status"] == "completed"
        assert json.loads(child_episode["metadata"])["training_policy"] is False
        child_episode_id = child_episode["episode_id"]
        decision = store._conn.execute(
            "SELECT decision_id, episode_id, action_group_id, payload "
            "FROM rl_decisions"
        ).fetchone()
        assert decision is not None
        payload = json.loads(decision["payload"])
        assert decision["decision_id"] == f"{child_episode_id}:decision-0"
        assert decision["episode_id"] == child_episode_id
        assert decision["action_group_id"]
        assert (
            store._conn.execute(
                "SELECT group_id FROM action_groups WHERE group_id=?",
                (decision["action_group_id"],),
            ).fetchone()
            is not None
        )
        assert payload["parent_decision_id"] == "decision-parent"
        observation = store._conn.execute(
            "SELECT observation_id, payload FROM rl_observations"
        ).fetchone()
        assert observation is not None
        assert observation["observation_id"] == f"{child_episode_id}:obs-0"
        assert json.loads(observation["payload"])["decision_id"] == (
            f"{child_episode_id}:decision-0"
        )
    finally:
        runner.close()
        store.close()


@pytest.mark.parametrize("durable", [False, True])
def test_inferred_segments_close_on_execution_exception(tmp_path, durable):
    from openai4s.rl import RolloutSession

    class FailingExecutor:
        def execute(self, *args):
            raise RuntimeError("execution broke")

    store = get_store(Config(data_dir=tmp_path).db_path) if durable else None
    try:
        recorder = TrajectoryRecorder(
            Episode("ep-error", "task", "session", 1),
            repository=store.rl if store else None,
        )
        session = RolloutSession(AgentEngine(_Model(), FailingExecutor()), recorder)
        session.reset([{"role": "user", "content": "run"}])
        with pytest.raises(RuntimeError, match="execution broke"):
            session.run(lambda _: _Model().complete([], None))
        trajectory = recorder.snapshot()
        assert trajectory.segments[0].status == "failed"
        assert trajectory.execution_events[-2].kind == "segment.closed"
        assert trajectory.execution_events[-1].kind == "episode.finished"
        assert recorder.open_segments() == ()
    finally:
        if store:
            store.close()


@pytest.mark.parametrize("durable", [False, True])
def test_segment_lifecycle_retry_preserves_original_event(tmp_path, durable):
    from dataclasses import replace

    from openai4s.rl import Segment
    from openai4s.rl.segments import lifecycle_event

    store = get_store(Config(data_dir=tmp_path).db_path) if durable else None
    try:
        recorder = TrajectoryRecorder(
            Episode("ep-retry-seg", "task", "session", 1),
            repository=store.rl if store else None,
        )
        segment = Segment("phase", "ep-retry-seg", "analysis", 2)
        first = recorder.transition_segment(
            segment, lifecycle_event(segment, started=True, at=2)
        )
        retry = replace(segment, created_at=3)
        assert (
            recorder.transition_segment(
                retry, lifecycle_event(retry, started=True, at=3)
            )
            == first
        )
        recorder.close_segment("phase", "completed")
        frozen = recorder.snapshot().canonical_json()
        recorder.close_segment("phase", "completed")
        assert recorder.snapshot().canonical_json() == frozen
        with pytest.raises(ValueError, match="conflict"):
            recorder.close_segment("phase", "failed")
        assert recorder.snapshot().canonical_json() == frozen
    finally:
        if store:
            store.close()


@pytest.mark.stubbed_backend
def test_real_agent_kernel_records_inferred_segments(monkeypatch, tmp_path):
    from openai4s.agent import Agent
    from tests.test_agent import ScriptedLLM

    cfg = Config(data_dir=tmp_path / "data")
    store = get_store(cfg.db_path)
    frame = store.new_frame(kind="turn")
    recorder = TrajectoryRecorder(
        Episode("ep-real", "compute", frame, 1), repository=store.rl
    )
    monkeypatch.setattr(
        loop_mod,
        "chat",
        ScriptedLLM(
            [
                "```python\nprint(6*7)\n```",
                "```python\nhost.submit_output({'answer': 42}, ['Computed answer'])\n```",
            ]
        ),
    )
    try:
        agent = Agent(
            cfg=cfg,
            frame_id=frame,
            use_skills=False,
            allow_delegate=False,
            trajectory_recorder=recorder,
            max_turns=3,
        )
        result = agent.run("compute 6*7")
        assert result["stop_reason"] == "submitted"
        trajectory = store.rl.load_trajectory("ep-real")
        assert len(trajectory.segments) == 2
        assert all(s.status == "completed" for s in trajectory.segments)
        assert all(
            s.metadata["openai4s.segment"]["source"] == "inferred"
            for s in trajectory.segments
        )
        assert all(
            s.metadata["openai4s.segment"]["action_group_id"]
            for s in trajectory.segments
        )
        assert trajectory.execution_events[-1].kind == "episode.finished"
    finally:
        store.close()


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
