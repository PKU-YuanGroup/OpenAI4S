from __future__ import annotations

import json

import pytest

from openai4s.rl import (
    DatasetSnapshot,
    Episode,
    ExecutionEvent,
    Observation,
    PolicyDecision,
    RewardAnnotation,
    Segment,
    TrajectoryRecorder,
    apply_verifier,
)
from openai4s.rl.models import Trajectory


def _recorder() -> TrajectoryRecorder:
    episode = Episode(
        episode_id="ep-1",
        task="annotate a protein",
        session_id="session-1",
        created_at=1,
        policy_name="qwen",
        policy_version="v1",
    )
    return TrajectoryRecorder(episode)


def test_contract_round_trips_white_box_decision_and_is_deterministic() -> None:
    recorder = _recorder()
    recorder.add_observation(
        Observation(
            observation_id="obs-1",
            episode_id="ep-1",
            decision_id="dec-1",
            messages=[{"role": "user", "content": "annotate"}],
            created_at=2,
        )
    )
    decision = PolicyDecision(
        decision_id="dec-1",
        episode_id="ep-1",
        turn=0,
        observation_id="obs-1",
        prompt_hash="abc",
        policy_name="qwen",
        policy_version="v1",
        action={"kind": "python", "code": "print(1)"},
        created_at=3,
        token_ids=[10, 11],
        token_logprobs=[-0.2, -0.4],
        loss_mask=[0, 1],
        action_logprob=-0.6,
    )
    assert recorder.add_decision(decision) == decision
    assert recorder.add_decision(decision) == decision  # retry is idempotent
    exported = recorder.export_json()
    assert json.loads(exported)["decisions"][0]["token_ids"] == [10, 11]
    assert recorder.snapshot().sha256() == recorder.snapshot().sha256()


def test_recorder_rejects_conflicts_and_cross_episode_records() -> None:
    recorder = _recorder()
    event = ExecutionEvent(
        event_id="event-1",
        episode_id="ep-1",
        kind="cell.completed",
        created_at=4,
        payload={"status": "ok"},
    )
    recorder.add_event(event)
    with pytest.raises(ValueError, match="different content"):
        recorder.add_event(
            ExecutionEvent(
                event_id="event-1",
                episode_id="ep-1",
                kind="cell.failed",
                created_at=4,
            )
        )
    with pytest.raises(ValueError, match="different episode"):
        recorder.add_event(
            ExecutionEvent(
                event_id="event-2",
                episode_id="ep-2",
                kind="cell.completed",
                created_at=5,
            )
        )


def test_reward_versions_are_independent_and_jsonl_is_streamable() -> None:
    recorder = _recorder()
    recorder.add_reward(
        RewardAnnotation(
            annotation_id="reward-v1",
            episode_id="ep-1",
            value=0.5,
            verifier="protein-check",
            verifier_version="1",
            created_at=5,
            components={"valid": 1.0},
        )
    )
    recorder.add_reward(
        RewardAnnotation(
            annotation_id="reward-v2",
            episode_id="ep-1",
            value=0.8,
            verifier="protein-check",
            verifier_version="2",
            created_at=6,
        )
    )
    rows = [json.loads(line) for line in recorder.export_jsonl().splitlines()]
    assert rows[0]["type"] == "trajectory"
    assert [row["annotation_id"] for row in rows if row["type"] == "reward"] == [
        "reward-v1",
        "reward-v2",
    ]


def test_verifier_appends_versioned_evidence_without_rewriting_execution_facts() -> (
    None
):
    recorder = _recorder()
    recorder.add_event(
        ExecutionEvent(
            "event-verify",
            "ep-1",
            "cell.completed",
            4,
            payload={"stdout": "ok"},
        )
    )
    before = recorder.snapshot().execution_events[0].to_dict()

    class _Verifier:
        name = "science-check"
        version = "2026-11-10"

        def verify(self, trajectory: Trajectory):
            assert trajectory.execution_events[0].to_dict() == before
            # A verifier receives a detached view; this must not alter the
            # recorder's canonical execution fact.
            trajectory.execution_events[0].payload["stdout"] = "tampered"
            return [
                RewardAnnotation(
                    "reward-check-v1",
                    "ep-1",
                    0.75,
                    "science-check",
                    "2026-11-10",
                    5,
                    evidence_refs=["event-verify"],
                    confidence=0.9,
                )
            ]

    applied = apply_verifier(recorder, _Verifier())
    assert applied[0].evidence_refs == ("event-verify",)
    assert applied[0].confidence == 0.9
    assert recorder.snapshot().execution_events[0].to_dict() == before

    class _SecondVerifier:
        name = "science-check"
        version = "2026-11-11"

        def verify(self, trajectory: Trajectory):
            return [
                RewardAnnotation(
                    "reward-check-v2", "ep-1", 0.8, self.name, self.version, 6
                )
            ]

    apply_verifier(recorder, _SecondVerifier())
    assert [item.verifier_version for item in recorder.snapshot().rewards] == [
        "2026-11-10",
        "2026-11-11",
    ]


def test_verifier_identity_and_annotation_scope_are_checked() -> None:
    recorder = _recorder()

    class _WrongVerifier:
        name = "trusted"
        version = "v1"

        def verify(self, trajectory: Trajectory):
            return [RewardAnnotation("wrong", "ep-1", 1.0, "other", "v1", 2)]

    with pytest.raises(ValueError, match="name does not match"):
        apply_verifier(recorder, _WrongVerifier())


def test_verifier_rejects_unjoinable_evidence_and_targets() -> None:
    recorder = _recorder()
    recorder.add_observation(
        Observation("obs-1", "ep-1", "dec-1", [{"role": "user"}], 2)
    )
    recorder.add_decision(
        PolicyDecision(
            "dec-1",
            "ep-1",
            0,
            "obs-1",
            "hash",
            "policy",
            "v1",
            {"type": "none"},
            3,
        )
    )

    class _Verifier:
        name = "trusted"
        version = "v1"

        def verify(self, trajectory: Trajectory):
            return [
                RewardAnnotation(
                    "reward-invalid",
                    "ep-1",
                    1.0,
                    self.name,
                    self.version,
                    4,
                    decision_id="dec-missing",
                    evidence_refs=["event-missing"],
                )
            ]

    with pytest.raises(ValueError, match="unknown decision"):
        apply_verifier(recorder, _Verifier())

    class _EvidenceVerifier(_Verifier):
        def verify(self, trajectory: Trajectory):
            return [
                RewardAnnotation(
                    "reward-invalid-evidence",
                    "ep-1",
                    1.0,
                    self.name,
                    self.version,
                    4,
                    decision_id="dec-1",
                    evidence_refs=["event-missing"],
                )
            ]

    with pytest.raises(ValueError, match="unknown evidence"):
        apply_verifier(recorder, _EvidenceVerifier())


def test_dataset_snapshot_requires_terminal_complete_facts_and_detects_drift() -> None:
    recorder = _recorder()
    recorder.add_segment(
        Segment(
            "segment-1",
            "ep-1",
            "analysis",
            2,
            status="completed",
        )
    )
    recorder.add_event(ExecutionEvent("event-snapshot", "ep-1", "cell.completed", 3))
    recorder.finish("success")

    snapshot = DatasetSnapshot.from_trajectory(
        recorder.snapshot(), "dataset-1", created_at=4
    )
    assert snapshot.event_sequence_max == 1
    assert snapshot.record_counts["execution_events"] == 1
    assert snapshot.matches(recorder.snapshot())

    recorder.add_reward(RewardAnnotation("reward-after", "ep-1", 0.1, "v", "1", 5))
    assert not snapshot.matches(recorder.snapshot())


def test_dataset_snapshot_rejects_running_episode_and_open_segment() -> None:
    running = _recorder()
    with pytest.raises(ValueError, match="terminal episode"):
        DatasetSnapshot.from_trajectory(
            running.snapshot(), "dataset-running", created_at=1
        )

    running.add_segment(Segment("segment-open", "ep-1", "analysis", 2))
    running.finish("success")
    with pytest.raises(ValueError, match="open segments"):
        DatasetSnapshot.from_trajectory(
            running.snapshot(), "dataset-open", created_at=3
        )


def test_recorder_cannot_finish_an_episode_as_running() -> None:
    recorder = _recorder()
    with pytest.raises(ValueError, match="terminal"):
        recorder.finish("running")
