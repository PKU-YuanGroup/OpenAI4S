from __future__ import annotations

import json
from dataclasses import replace

import pytest

from openai4s.rl import (
    CostRecord,
    DatasetSnapshot,
    Episode,
    ExecutionEvent,
    Observation,
    PolicyDecision,
    RewardAnnotation,
    RolloutDataset,
    Segment,
    TrajectoryRecorder,
)


def _finished_recorder() -> TrajectoryRecorder:
    recorder = TrajectoryRecorder(Episode("ep-dataset", "task", "session", 1))
    for turn in (0, 1):
        decision_id = f"decision-{turn}"
        recorder.add_observation(
            Observation(
                f"observation-{turn}",
                "ep-dataset",
                decision_id,
                [{"role": "user", "content": f"turn {turn}"}],
                2 + turn,
            )
        )
        recorder.add_decision(
            PolicyDecision(
                decision_id,
                "ep-dataset",
                turn,
                f"observation-{turn}",
                f"prompt-{turn}",
                "policy",
                "v1",
                {"type": "python", "code": f"print({turn})"},
                4 + turn,
                token_ids=[turn + 1],
                loss_mask=[1],
            )
        )
    recorder.add_segment(
        Segment(
            "segment-1",
            "ep-dataset",
            "analysis",
            6,
            decision_id="decision-0",
            status="closed",
        )
    )
    recorder.add_segment(
        Segment("segment-2", "ep-dataset", "verification", 6, status="closed")
    )
    recorder.add_event(
        ExecutionEvent(
            "event-1",
            "ep-dataset",
            "cell.completed",
            7,
            decision_id="decision-0",
            segment_id="segment-1",
        )
    )
    recorder.add_event(
        ExecutionEvent(
            "event-2", "ep-dataset", "cell.completed", 8, decision_id="decision-1"
        )
    )
    recorder.add_reward(
        RewardAnnotation(
            "reward-segment-v1",
            "ep-dataset",
            0.7,
            "science",
            "v1",
            9,
            segment_id="segment-1",
            evidence_refs=["event-1"],
        )
    )
    recorder.add_reward(
        RewardAnnotation(
            "reward-episode-v1",
            "ep-dataset",
            0.9,
            "science",
            "v1",
            10,
            evidence_refs=["event-2"],
        )
    )
    recorder.add_reward(
        RewardAnnotation(
            "reward-unbound-segment-v1",
            "ep-dataset",
            0.8,
            "science",
            "v1",
            10,
            segment_id="segment-2",
            evidence_refs=["event-2"],
        )
    )
    recorder.add_reward(
        RewardAnnotation(
            "reward-other-verifier",
            "ep-dataset",
            0.1,
            "science",
            "v2",
            11,
        )
    )
    recorder.add_cost(
        CostRecord(
            "cost-1", "ep-dataset", 12, completion_tokens=3, decision_id="decision-0"
        )
    )
    recorder.add_cost(CostRecord("cost-episode", "ep-dataset", 13, wall_time_ms=4))
    recorder.finish("completed")
    return recorder


def test_dataset_projects_explicit_reward_joins_without_attributing_episode_reward():
    recorder = _finished_recorder()
    trajectory = recorder.snapshot()
    snapshot = recorder.create_snapshot("snapshot-1", created_at=20)

    dataset = RolloutDataset.from_trajectory(
        trajectory, snapshot, verifier="science", verifier_version="v1"
    )

    assert [sample.decision_id for sample in dataset.samples] == [
        "decision-0",
        "decision-1",
    ]
    assert [item["annotation_id"] for item in dataset.samples[0].rewards] == [
        "reward-segment-v1"
    ]
    assert dataset.samples[0].costs[0]["cost_id"] == "cost-1"
    assert dataset.samples[0].execution_event_ids == ("event-1",)
    assert dataset.samples[0].segment_ids == ("segment-1",)
    assert dataset.samples[0].terminal is False
    assert dataset.samples[1].terminal is True
    assert [item["annotation_id"] for item in dataset.episode_rewards] == [
        "reward-episode-v1"
    ]
    assert [item["annotation_id"] for item in dataset.segment_rewards] == [
        "reward-unbound-segment-v1",
    ]
    assert [item["cost_id"] for item in dataset.episode_costs] == ["cost-episode"]
    assert dataset.reward_verifier == "science"
    assert dataset.reward_verifier_version == "v1"


def test_dataset_jsonl_is_deterministic_and_streamable():
    recorder = _finished_recorder()
    dataset = RolloutDataset.from_trajectory(
        recorder.snapshot(), recorder.create_snapshot("snapshot-1", created_at=20)
    )

    lines = [json.loads(line) for line in dataset.jsonl().splitlines()]
    assert lines[0]["type"] == "dataset"
    assert [line["type"] for line in lines[1:]] == ["sample", "sample"]
    assert [line["decision_id"] for line in lines[1:]] == [
        "decision-0",
        "decision-1",
    ]
    assert list(dataset.iter_jsonl()) == dataset.jsonl().splitlines()
    assert dataset.jsonl() == dataset.jsonl()
    assert len(dataset.sha256()) == 64


def test_dataset_rejects_snapshot_drift_and_running_episodes():
    recorder = _finished_recorder()
    snapshot = recorder.create_snapshot("snapshot-1", created_at=20)
    recorder.add_cost(CostRecord("late-cost", "ep-dataset", 21, wall_time_ms=1))
    with pytest.raises(ValueError, match="does not match"):
        RolloutDataset.from_trajectory(recorder.snapshot(), snapshot)

    running = TrajectoryRecorder(Episode("ep-running", "task", "session", 1))
    with pytest.raises(ValueError, match="terminal"):
        running.create_snapshot("snapshot-running", created_at=2)


def test_dataset_rejects_unknown_fact_joins():
    recorder = _finished_recorder()
    trajectory = recorder.snapshot()
    bad_reward = RewardAnnotation(
        "reward-bad",
        "ep-dataset",
        0.1,
        "science",
        "v1",
        22,
        decision_id="missing-decision",
    )
    bad = replace(trajectory, rewards=trajectory.rewards + (bad_reward,))
    snapshot = DatasetSnapshot.from_trajectory(bad, "snapshot-bad", created_at=23)
    with pytest.raises(ValueError, match="unknown decision"):
        RolloutDataset.from_trajectory(bad, snapshot)
