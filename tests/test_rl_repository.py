from __future__ import annotations

import pytest

from openai4s.config import Config
from openai4s.rl import (
    Episode,
    ExecutionEvent,
    Observation,
    PolicyDecision,
    RewardAnnotation,
    Segment,
    TrajectoryRecorder,
    apply_verifier,
)
from openai4s.rl.models import RunReceipt
from openai4s.store import get_store


def test_rl_records_use_store_connection_and_survive_reopen(tmp_path) -> None:
    db = Config(data_dir=tmp_path).db_path
    store = get_store(db)
    recorder = TrajectoryRecorder(
        Episode("ep-db", "task", "session-db", 1), repository=store.rl
    )
    recorder.add_event(
        ExecutionEvent("event-db", "ep-db", "cell.completed", 2, {"ok": True})
    )
    recorder.add_reward(RewardAnnotation("reward-1", "ep-db", 0.5, "verifier", "v1", 3))
    receipt = RunReceipt("request-1", "fingerprint-1", "ep-db", "accepted", 4, 4)
    assert store.rl.admit_receipt(receipt) == receipt
    store.close()

    reopened = get_store(db)
    try:
        assert (
            reopened._conn.execute(
                "SELECT COUNT(*) FROM rl_execution_events"
            ).fetchone()[0]
            == 1
        )
        assert (
            reopened._conn.execute(
                "SELECT COUNT(*) FROM rl_reward_annotations"
            ).fetchone()[0]
            == 1
        )
        assert reopened.rl.get_receipt("request-1") == receipt
    finally:
        reopened.close()


def test_rl_policy_inputs_survive_durable_trajectory_reload(tmp_path) -> None:
    store = get_store(Config(data_dir=tmp_path).db_path)
    try:
        recorder = TrajectoryRecorder(
            Episode("ep-policy-inputs", "task", "session", 1),
            repository=store.rl,
        )
        recorder.add_observation(
            Observation(
                "obs-policy-inputs",
                "ep-policy-inputs",
                "decision-policy-inputs",
                [{"role": "user", "content": "run"}],
                2,
                tools=[{"name": "inspect", "input_schema": {"type": "object"}}],
            )
        )
        recorder.add_decision(
            PolicyDecision(
                "decision-policy-inputs",
                "ep-policy-inputs",
                0,
                "obs-policy-inputs",
                "hash",
                "policy",
                "v1",
                {"type": "code_cell"},
                3,
                prompt_token_ids=[1, 2],
                sampling_config={"temperature": 0.4},
            )
        )
        loaded = store.rl.load_trajectory("ep-policy-inputs")
        assert loaded.observations[0].tools[0]["name"] == "inspect"
        assert loaded.decisions[0].prompt_token_ids == (1, 2)
        assert loaded.decisions[0].sampling_config == {"temperature": 0.4}
    finally:
        store.close()


def test_rl_receipt_conflict_is_rejected_atomically(tmp_path) -> None:
    store = get_store(Config(data_dir=tmp_path).db_path)
    try:
        store.rl.admit_receipt(
            RunReceipt("request-1", "fp-1", "ep-1", "accepted", 1, 1)
        )
        with pytest.raises(ValueError, match="different content"):
            store.rl.admit_receipt(
                RunReceipt("request-1", "fp-2", "ep-2", "accepted", 1, 1)
            )
        assert store.rl.get_receipt("request-1").fingerprint == "fp-1"
    finally:
        store.close()


def test_rl_segments_reuse_episode_identity_and_event_sequence(tmp_path) -> None:
    store = get_store(Config(data_dir=tmp_path).db_path)
    try:
        first = Episode("ep-segments", "task", "session", 100)
        second = Episode("ep-segments", "task", "session", 200)
        assert store.rl.ensure_episode(first) == first
        assert store.rl.ensure_episode(second) == first

        event_one = store.rl.append_execution_event(
            ExecutionEvent("event-one", "ep-segments", "segment.started", 101)
        )
        event_two = store.rl.append_execution_event(
            ExecutionEvent("event-two", "ep-segments", "segment.closed", 102)
        )
        assert (event_one.sequence, event_two.sequence) == (1, 2)
        # A retry without a sequence receives the original durable sequence.
        assert store.rl.append_execution_event(event_one) == event_one
    finally:
        store.close()


def test_rl_receipt_state_is_durable_and_terminal(tmp_path) -> None:
    store = get_store(Config(data_dir=tmp_path).db_path)
    try:
        accepted = store.rl.admit_receipt(
            RunReceipt("request-state", "fp", "ep", "accepted", 1, 1)
        )
        running = store.rl.update_receipt_state("request-state", "running")
        assert accepted.state == "accepted"
        assert running.state == "running"
        with pytest.raises(ValueError, match="cannot return"):
            store.rl.update_receipt_state("request-state", "accepted")
        with pytest.raises(ValueError, match="expected state"):
            store.rl.update_receipt_state(
                "request-state", "completed", expected_state="accepted"
            )
        completed = store.rl.update_receipt_state("request-state", "completed")
        assert completed.state == "completed"
        assert store.rl.update_receipt_state("request-state", "completed") == completed
        with pytest.raises(ValueError, match="already terminal"):
            store.rl.update_receipt_state("request-state", "failed")
    finally:
        store.close()


def test_dataset_snapshot_metadata_is_durable_and_idempotent(tmp_path) -> None:
    store = get_store(Config(data_dir=tmp_path).db_path)
    try:
        recorder = TrajectoryRecorder(
            Episode("ep-snapshot", "task", "session", 1), repository=store.rl
        )
        recorder.add_segment(
            Segment("seg-snapshot", "ep-snapshot", "analysis", 2, status="completed")
        )
        recorder.add_event(
            ExecutionEvent("event-snapshot", "ep-snapshot", "cell.completed", 3)
        )
        recorder.finish("completed")
        first = recorder.create_snapshot("snapshot-1", created_at=4)
        assert recorder.create_snapshot("snapshot-1", created_at=4) == first
        assert store.rl.get_snapshot("snapshot-1") == first
        assert (
            store.rl.load_trajectory("ep-snapshot").sha256() == first.trajectory_sha256
        )
        with pytest.raises(KeyError):
            store.rl.get_snapshot("missing")
    finally:
        store.close()


def test_versioned_verifiers_and_snapshots_survive_reopen(tmp_path) -> None:
    db = Config(data_dir=tmp_path).db_path
    store = get_store(db)
    recorder = TrajectoryRecorder(
        Episode("ep-verifiers", "task", "session", 1), repository=store.rl
    )
    recorder.add_segment(
        Segment("seg-verifiers", "ep-verifiers", "analysis", 2, status="completed")
    )
    recorder.add_event(
        ExecutionEvent("event-verifiers", "ep-verifiers", "cell.completed", 3)
    )
    recorder.finish("success")

    class _VerifierV1:
        name = "science-check"
        version = "v1"

        def verify(self, trajectory):
            return [
                RewardAnnotation(
                    "reward-v1",
                    trajectory.episode.episode_id,
                    0.4,
                    self.name,
                    self.version,
                    4,
                )
            ]

    class _VerifierV2:
        name = "science-check"
        version = "v2"

        def verify(self, trajectory):
            return [
                RewardAnnotation(
                    "reward-v2",
                    trajectory.episode.episode_id,
                    0.9,
                    self.name,
                    self.version,
                    5,
                )
            ]

    try:
        apply_verifier(recorder, _VerifierV1())
        snapshot_v1 = recorder.create_snapshot("snapshot-v1", created_at=6)
        apply_verifier(recorder, _VerifierV2())
        snapshot_v2 = recorder.create_snapshot("snapshot-v2", created_at=7)
        assert snapshot_v1.matches(store.rl.load_trajectory("ep-verifiers")) is False
        assert snapshot_v2.matches(store.rl.load_trajectory("ep-verifiers"))
    finally:
        store.close()

    reopened = get_store(db)
    try:
        trajectory = reopened.rl.load_trajectory("ep-verifiers")
        assert [item.verifier_version for item in trajectory.rewards] == ["v1", "v2"]
        assert reopened.rl.get_snapshot("snapshot-v1") == snapshot_v1
        assert reopened.rl.get_snapshot("snapshot-v2") == snapshot_v2
        assert reopened.rl.get_snapshot("snapshot-v2").matches(trajectory)
    finally:
        reopened.close()
