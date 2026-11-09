from __future__ import annotations

import pytest

from openai4s.config import Config
from openai4s.rl import Episode, ExecutionEvent, RewardAnnotation, TrajectoryRecorder
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
