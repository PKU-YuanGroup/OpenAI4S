from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from openai4s.config import Config
from openai4s.rl.models import Episode, ExecutionEvent, RewardAnnotation, Segment
from openai4s.rl.requests import (
    normalize_request,
    request_fingerprint,
)
from openai4s.rl.segments import SEGMENT_METADATA
from openai4s.storage.rl_runs import (
    EPISODE_TERMINAL_STATUSES,
    RunRepository,
    create_rl_run_schema,
    receipt_state_for_status,
)
from openai4s.store import get_store


def _open_store(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    create_rl_run_schema(store._conn)
    store._conn.commit()
    return store


def _episode(episode_id: str = "ep-1", *, task: str = "solve", created_at: int = 1):
    return Episode(
        episode_id,
        task,
        "session-1",
        created_at,
        policy_name="policy",
        policy_version="v1",
        metadata={"caller": {"batch": "a"}},
    )


def _request(episode: Episode, *, content: str = "run the task"):
    return normalize_request(
        episode,
        [{"role": "user", "content": content}],
        initial_artifacts={"artifact-1": "v1"},
        config={"temperature": 0.2},
        budget={"max_tokens": 100},
        max_turns=4,
    )


def _terminal_event(
    episode_id: str = "ep-1", status: str = "completed"
) -> ExecutionEvent:
    return ExecutionEvent(
        f"{episode_id}:episode.finished",
        episode_id,
        "episode.finished",
        99,
        {"stop_reason": status, "turns": 1},
    )


@pytest.fixture
def runtime(tmp_path):
    store = _open_store(tmp_path)
    try:
        yield store, RunRepository(store.rl)
    finally:
        store.close()


# -- request identity -------------------------------------------------------


def test_normalize_request_excludes_retry_noise_and_binds_identity() -> None:
    first = _episode(created_at=1)
    retry = _episode(created_at=999)
    # Stored details keep the original timestamp, but the logical identity does
    # not change when a caller retries later.
    assert _request(first)["episode"]["created_at"] == 1
    assert _request(first) != _request(retry)
    assert request_fingerprint(_request(first)) == request_fingerprint(_request(retry))

    generated = replace(
        first,
        metadata={
            **first.metadata,
            "reset": {
                "reset_id": "reset-" + "a" * 32,
                "workspace_id": "workspace-" + "b" * 32,
            },
        },
    )
    regenerated = replace(
        first,
        metadata={
            **first.metadata,
            "reset": {
                "reset_id": "reset-" + "c" * 32,
                "workspace_id": "workspace-" + "d" * 32,
            },
        },
    )
    assert request_fingerprint(_request(generated)) == request_fingerprint(
        _request(regenerated)
    )

    assert request_fingerprint(_request(first, content="different")) != (
        request_fingerprint(_request(first))
    )
    assert request_fingerprint(_request(replace(first, task="different task"))) != (
        request_fingerprint(_request(first))
    )


def test_caller_reset_metadata_is_bound_unless_generated() -> None:
    first = _episode()
    caller_one = replace(
        first,
        metadata={
            **first.metadata,
            "reset": {
                "reset_id": "reset-caller-a",
                "workspace_id": "workspace-caller-a",
                "note": "one",
            },
        },
    )
    caller_two = replace(
        first,
        metadata={
            **first.metadata,
            "reset": {
                "reset_id": "reset-caller-a",
                "workspace_id": "workspace-caller-a",
                "note": "two",
            },
        },
    )
    assert request_fingerprint(_request(caller_one)) != request_fingerprint(
        _request(caller_two)
    )

    # Generated-looking UUIDs with caller extras are still caller content.
    extra_one = replace(
        first,
        metadata={
            **first.metadata,
            "reset": {
                "reset_id": "reset-" + "a" * 32,
                "workspace_id": "workspace-" + "b" * 32,
                "caller_key": "one",
            },
        },
    )
    extra_two = replace(
        first,
        metadata={
            **first.metadata,
            "reset": {
                "reset_id": "reset-" + "a" * 32,
                "workspace_id": "workspace-" + "b" * 32,
                "caller_key": "two",
            },
        },
    )
    assert request_fingerprint(_request(extra_one)) != request_fingerprint(
        _request(extra_two)
    )


def test_receipt_state_mapping_is_closed() -> None:
    expected = {
        "done": "completed",
        "completed": "completed",
        "success": "completed",
        "submitted": "completed",
        "plan": "completed",
        "cancelled": "cancelled",
        "timeout": "interrupted",
        "interrupted": "interrupted",
        "max_turns": "interrupted",
        "no_progress": "interrupted",
        "budget_exhausted": "interrupted",
        "error": "failed",
        "failed": "failed",
        "scientific_failure": "failed",
        "execution_failure": "failed",
        "verifier_failure": "failed",
        # A real LocalActionExecutor stop reason (allowlist egress without a
        # proven sandbox); finalize must map it rather than raise.
        "egress_boundary_unavailable": "failed",
    }
    assert EPISODE_TERMINAL_STATUSES == frozenset(expected)
    for status, receipt_state in expected.items():
        assert receipt_state_for_status(status) == receipt_state
    with pytest.raises(ValueError, match="unsupported terminal"):
        receipt_state_for_status("unknown-status")


def test_normalize_request_rejects_non_strict_json() -> None:
    episode = _episode()
    with pytest.raises(ValueError, match="non-finite"):
        normalize_request(episode, [{"role": "user", "content": float("nan")}])
    with pytest.raises(TypeError, match="unsupported value"):
        normalize_request(episode, [{"role": "user", "content": object()}])
    with pytest.raises(ValueError, match="non-empty"):
        normalize_request(episode, [{"role": "user", "content": "x"}], {"": "v1"})


# -- admission and claim ----------------------------------------------------


def test_admit_is_idempotent_and_conflicts_in_every_state(runtime) -> None:
    store, runs = runtime
    episode = _episode()
    request = _request(episode)
    fingerprint = request_fingerprint(request)

    first = runs.admit("request-1", request)
    assert first["state"] == "accepted"
    assert first["owner_id"] is None
    assert first["protocol"] == 2
    assert first["fingerprint"] == fingerprint
    assert first["reset_metadata"]["reset_id"].startswith("reset-")
    assert first["reset_metadata"]["workspace_id"].startswith("workspace-")

    retry = runs.admit("request-1", request)
    assert retry["reset_metadata"] == first["reset_metadata"]
    assert retry["state"] == "accepted"

    # A later caller retry keeps the original stored details, including the
    # original Episode.created_at that the fingerprint deliberately ignores.
    later = runs.admit("request-1", _request(_episode(created_at=999)))
    assert later["payload"]["episode"]["created_at"] == 1
    assert later["reset_metadata"] == first["reset_metadata"]

    store.rl.append_episode(episode)
    claimed = runs.claim("request-1", fingerprint, "owner-1")
    assert claimed["state"] == "running"
    assert claimed["owner_id"] == "owner-1"
    assert claimed["reset_metadata"] == first["reset_metadata"]

    # Same content in running state returns details; it does not mint a new
    # reset identity or execute a factory.
    assert runs.admit("request-1", request)["reset_metadata"] == first["reset_metadata"]

    conflicting = _request(replace(episode, task="other"))
    with pytest.raises(ValueError, match="different content"):
        runs.admit("request-1", conflicting)

    runs.finalize(
        "request-1", "owner-1", episode.episode_id, "completed", _terminal_event()
    )
    terminal = runs.admit("request-1", request)
    assert terminal["state"] == "completed"
    assert terminal["reset_metadata"] == first["reset_metadata"]
    with pytest.raises(ValueError, match="different content"):
        runs.admit("request-1", conflicting)


def test_claim_is_exactly_once_and_never_reclaims_running(runtime) -> None:
    store, runs = runtime
    episode = _episode()
    request = _request(episode)
    fingerprint = request_fingerprint(request)

    with pytest.raises(KeyError):
        runs.claim("missing", fingerprint, "owner-1")

    runs.admit("request-1", request)
    with pytest.raises(ValueError, match="different content"):
        runs.claim("request-1", "0" * 64, "owner-1")

    store.rl.append_episode(episode)
    assert runs.claim("request-1", fingerprint, "owner-1")["state"] == "running"
    with pytest.raises(RuntimeError, match="already claimed"):
        runs.claim("request-1", fingerprint, "owner-1")
    with pytest.raises(RuntimeError, match="already claimed"):
        runs.claim("request-1", fingerprint, "owner-2")

    runs.finalize("request-1", "owner-1", "ep-1", "completed", _terminal_event())
    terminal = runs.claim("request-1", fingerprint, "owner-1")
    assert terminal["state"] == "completed"
    assert runs.claim("request-1", fingerprint, "owner-2")["state"] == "completed"


def test_legacy_receipt_without_details_fails_closed(runtime) -> None:
    _, runs = runtime
    from openai4s.rl.models import RunReceipt

    runs.admit_receipt(RunReceipt("legacy-1", "fp", "ep-1", "accepted", 1, 1))
    with pytest.raises(RuntimeError, match="legacy"):
        runs.admit("legacy-1", _request(_episode()))
    with pytest.raises(RuntimeError, match="legacy"):
        runs.get("legacy-1")


# -- terminal finalize ------------------------------------------------------


def test_finalize_closes_segments_child_first_and_freezes_immutable_result(
    runtime,
) -> None:
    store, runs = runtime
    episode = _episode()
    store.rl.append_episode(episode)
    owner = "owner-1"
    metadata = {SEGMENT_METADATA: {"persistent": True, "owner_id": owner}}
    store.rl.append_segment(
        Segment("seg-parent", "ep-1", "parent", 10, metadata=metadata)
    )
    store.rl.append_segment(
        Segment(
            "seg-child",
            "ep-1",
            "child",
            11,
            parent_segment_id="seg-parent",
            metadata=metadata,
        )
    )
    store.rl.set_active_phase("ep-1", owner, "seg-child")

    request = _request(episode)
    fingerprint = request_fingerprint(request)
    runs.admit("request-1", request)
    runs.claim("request-1", fingerprint, owner)

    trajectory = runs.finalize(
        "request-1", owner, "ep-1", "completed", _terminal_event()
    )
    assert trajectory.episode.status == "completed"
    # Phases the agent never closed are interrupted, never inferred successful.
    assert {segment.status for segment in trajectory.segments} == {"interrupted"}

    close_order = [
        json.loads(row["payload"])["segment_id"]
        for row in store._conn.execute(
            "SELECT payload FROM rl_execution_events "
            "WHERE kind='segment.closed' ORDER BY sequence"
        )
    ]
    assert close_order == ["seg-child", "seg-parent"]
    terminal_events = store._conn.execute(
        "SELECT COUNT(*) AS n FROM rl_execution_events "
        "WHERE event_id='ep-1:episode.finished'"
    ).fetchone()["n"]
    assert terminal_events == 1
    assert (
        store._conn.execute(
            "SELECT status FROM rl_episodes WHERE episode_id='ep-1'"
        ).fetchone()["status"]
        == "completed"
    )
    assert (
        store._conn.execute(
            "SELECT segment_id FROM rl_phase_bindings WHERE episode_id='ep-1'"
        ).fetchone()["segment_id"]
        is None
    )
    assert runs.get("request-1")["state"] == "completed"

    frozen = runs.result("request-1")
    assert frozen is not None
    payload = frozen.canonical_json()
    stored = store._conn.execute(
        "SELECT payload,trajectory_sha256 FROM rl_run_results "
        "WHERE request_id='request-1'"
    ).fetchone()
    assert stored["payload"] == payload
    assert (
        stored["trajectory_sha256"]
        == hashlib.sha256(payload.encode("utf-8")).hexdigest()
    )

    # Rewards are append-only live facts. They may enrich a later snapshot but
    # never rewrite the frozen terminal result returned for this request.
    store.rl.append_reward(
        RewardAnnotation("reward-1", "ep-1", 0.5, "verifier", "v1", 100)
    )
    assert runs.result("request-1").rewards == ()
    assert len(store.rl.load_trajectory("ep-1").rewards) == 1


def test_finalize_rolls_back_every_projection_on_failure(runtime, monkeypatch) -> None:
    store, runs = runtime
    episode = _episode()
    store.rl.append_episode(episode)
    owner = "owner-1"
    store.rl.append_segment(
        Segment(
            "seg-open",
            "ep-1",
            "phase",
            10,
            metadata={SEGMENT_METADATA: {"persistent": True, "owner_id": owner}},
        )
    )
    store.rl.set_active_phase("ep-1", owner, "seg-open")
    request = _request(episode)
    runs.admit("request-1", request)
    runs.claim("request-1", request_fingerprint(request), owner)

    def fail(self, episode_id):
        raise RuntimeError("projection failed")

    monkeypatch.setattr(RunRepository, "_load_trajectory_unlocked", fail)
    with pytest.raises(RuntimeError, match="projection failed"):
        runs.finalize("request-1", owner, "ep-1", "completed", _terminal_event())

    assert runs.get("request-1")["state"] == "running"
    assert runs.result("request-1") is None
    assert (
        store._conn.execute(
            "SELECT status FROM rl_episodes WHERE episode_id='ep-1'"
        ).fetchone()["status"]
        == "running"
    )
    assert (
        store._conn.execute(
            "SELECT status FROM rl_segments WHERE segment_id='seg-open'"
        ).fetchone()["status"]
        == "open"
    )
    assert (
        store._conn.execute(
            "SELECT segment_id FROM rl_phase_bindings WHERE episode_id='ep-1'"
        ).fetchone()["segment_id"]
        == "seg-open"
    )
    assert (
        store._conn.execute("SELECT COUNT(*) AS n FROM rl_execution_events").fetchone()[
            "n"
        ]
        == 0
    )
    assert (
        store._conn.execute("SELECT COUNT(*) AS n FROM rl_run_results").fetchone()["n"]
        == 0
    )


def test_finalize_rejects_unknown_or_incompatible_status(runtime) -> None:
    store, runs = runtime
    episode = _episode()
    store.rl.append_episode(episode)
    request = _request(episode)
    runs.admit("request-1", request)
    runs.claim("request-1", request_fingerprint(request), "owner-1")

    with pytest.raises(ValueError, match="unsupported terminal"):
        runs.finalize(
            "request-1",
            "owner-1",
            "ep-1",
            "unknown-status",
            _terminal_event(status="unknown-status"),
        )
    with pytest.raises(ValueError, match="different episode"):
        runs.finalize(
            "request-1",
            "owner-1",
            "ep-2",
            "completed",
            _terminal_event("ep-2"),
        )
    with pytest.raises(ValueError, match="different owner"):
        runs.finalize(
            "request-1",
            "owner-2",
            "ep-1",
            "failed",
            _terminal_event(status="failed"),
        )

    first = runs.finalize(
        "request-1", "owner-1", "ep-1", "completed", _terminal_event()
    )
    assert (
        runs.finalize(
            "request-1", "owner-1", "ep-1", "completed", _terminal_event()
        ).canonical_json()
        == first.canonical_json()
    )
    # "done" maps to the same receipt state as "completed", but the original
    # Episode.status is the retry identity and must still match exactly.
    with pytest.raises(ValueError, match="already terminal as 'completed'"):
        runs.finalize(
            "request-1", "owner-1", "ep-1", "done", _terminal_event(status="done")
        )
    with pytest.raises(ValueError, match="already terminal as 'completed'"):
        runs.finalize(
            "request-1", "owner-1", "ep-1", "failed", _terminal_event(status="failed")
        )


@pytest.mark.parametrize(
    ("episode_status", "receipt_state"),
    [
        ("done", "completed"),
        ("completed", "completed"),
        ("success", "completed"),
        ("submitted", "completed"),
        ("plan", "completed"),
        ("cancelled", "cancelled"),
        ("timeout", "interrupted"),
        ("interrupted", "interrupted"),
        ("max_turns", "interrupted"),
        ("no_progress", "interrupted"),
        ("budget_exhausted", "interrupted"),
        ("error", "failed"),
        ("failed", "failed"),
        ("scientific_failure", "failed"),
        ("execution_failure", "failed"),
        ("verifier_failure", "failed"),
    ],
)
def test_finalize_preserves_episode_status_and_maps_receipt_separately(
    tmp_path, episode_status: str, receipt_state: str
) -> None:
    store = _open_store(tmp_path)
    try:
        runs = RunRepository(store.rl)
        episode = _episode()
        store.rl.append_episode(episode)
        request = _request(episode)
        runs.admit("request-1", request)
        runs.claim("request-1", request_fingerprint(request), "owner-1")

        trajectory = runs.finalize(
            "request-1",
            "owner-1",
            "ep-1",
            episode_status,
            _terminal_event(status=episode_status),
        )
        assert trajectory.episode.status == episode_status
        assert runs.get("request-1")["state"] == receipt_state
        assert runs.result("request-1").episode.status == episode_status
        # Exact retry returns the same frozen trajectory.
        assert (
            runs.finalize(
                "request-1",
                "owner-1",
                "ep-1",
                episode_status,
                _terminal_event(status=episode_status),
            ).canonical_json()
            == trajectory.canonical_json()
        )
    finally:
        store.close()


def test_finalize_rejects_wrong_kind_or_stop_reason_mismatch(runtime) -> None:
    store, runs = runtime
    episode = _episode()
    store.rl.append_episode(episode)
    request = _request(episode)
    runs.admit("request-1", request)
    runs.claim("request-1", request_fingerprint(request), "owner-1")

    wrong_kind = ExecutionEvent(
        "ep-1:wrong-kind",
        "ep-1",
        "cell.completed",
        99,
        {"stop_reason": "completed"},
    )
    with pytest.raises(ValueError, match="episode.finished"):
        runs.finalize("request-1", "owner-1", "ep-1", "completed", wrong_kind)

    mismatch = _terminal_event(status="completed")
    with pytest.raises(ValueError, match="stop_reason"):
        runs.finalize("request-1", "owner-1", "ep-1", "failed", mismatch)
    with pytest.raises(ValueError, match="stop_reason"):
        runs.finalize("request-1", "owner-1", "ep-1", "done", mismatch)

    # Rejection happens before any terminal projection is written.
    assert runs.get("request-1")["state"] == "running"
    assert runs.result("request-1") is None
    assert (
        store._conn.execute(
            "SELECT status FROM rl_episodes WHERE episode_id='ep-1'"
        ).fetchone()["status"]
        == "running"
    )
    assert (
        store._conn.execute("SELECT COUNT(*) AS n FROM rl_run_results").fetchone()["n"]
        == 0
    )


def test_second_request_for_same_episode_is_rejected_before_work(runtime) -> None:
    store, runs = runtime
    episode = _episode()
    request = _request(episode)
    first = runs.admit("request-1", request)

    with pytest.raises(ValueError, match="already bound to another run request"):
        runs.admit("request-2", request)

    assert runs.get("request-2") is None
    assert (
        store._conn.execute("SELECT COUNT(*) AS n FROM rl_run_requests").fetchone()["n"]
        == 1
    )
    assert (
        store._conn.execute("SELECT COUNT(*) AS n FROM rl_run_receipts").fetchone()["n"]
        == 1
    )
    # The original request ID remains an exact idempotent retry.
    assert runs.admit("request-1", request)["reset_metadata"] == (
        first["reset_metadata"]
    )


def test_frozen_result_survives_store_reopen(tmp_path) -> None:
    store = _open_store(tmp_path)
    runs = RunRepository(store.rl)
    episode = _episode()
    store.rl.append_episode(episode)
    request = _request(episode)
    runs.admit("request-1", request)
    runs.claim("request-1", request_fingerprint(request), "owner-1")
    expected = runs.finalize(
        "request-1", "owner-1", "ep-1", "completed", _terminal_event()
    ).canonical_json()
    store.close()

    reopened = get_store(Config(data_dir=tmp_path).db_path)
    try:
        assert RunRepository(reopened.rl).result("request-1").canonical_json() == (
            expected
        )
    finally:
        reopened.close()


def test_result_detects_tampered_hash_or_schema(runtime) -> None:
    store, runs = runtime
    episode = _episode()
    store.rl.append_episode(episode)
    request = _request(episode)
    runs.admit("request-1", request)
    runs.claim("request-1", request_fingerprint(request), "owner-1")
    runs.finalize("request-1", "owner-1", "ep-1", "completed", _terminal_event())

    store._conn.execute(
        "UPDATE rl_run_results SET trajectory_sha256='tampered' "
        "WHERE request_id='request-1'"
    )
    store._conn.commit()
    with pytest.raises(ValueError, match="hash"):
        runs.result("request-1")


# -- step operation receipts ------------------------------------------------


def test_step_receipts_are_idempotent_and_turn_unique(runtime) -> None:
    store, runs = runtime
    episode = _episode()
    store.rl.append_episode(episode)
    request = _request(episode)
    runs.admit("request-1", request)

    with pytest.raises(ValueError, match="not running"):
        runs.claim_step("request-1", "owner-1", "step-0", 0, "fp-0")

    runs.claim("request-1", request_fingerprint(request), "owner-1")
    pending = runs.claim_step("request-1", "owner-1", "step-0", 0, "fp-0")
    assert pending["state"] == "pending"
    assert pending["response"] is None
    assert runs.claim_step("request-1", "owner-1", "step-0", 0, "fp-0")["state"] == (
        "pending"
    )

    with pytest.raises(ValueError, match="different content"):
        runs.claim_step("request-1", "owner-1", "step-0", 0, "fp-other")
    with pytest.raises(ValueError, match="already claimed"):
        runs.claim_step("request-1", "owner-1", "step-1", 0, "fp-1")
    with pytest.raises(ValueError, match="different owner"):
        runs.claim_step("request-1", "owner-2", "step-1", 1, "fp-1")

    response = {"turn": 0, "observation": [{"role": "assistant", "content": "ok"}]}
    recorded = runs.record_step("request-1", "owner-1", "step-0", response)
    assert recorded["state"] == "recorded"
    assert recorded["response"] == response
    assert (
        runs.record_step("request-1", "owner-1", "step-0", response)["response"]
        == response
    )
    with pytest.raises(ValueError, match="different content"):
        runs.record_step(
            "request-1", "owner-1", "step-0", {"turn": 0, "observation": []}
        )
    with pytest.raises(ValueError, match="different owner"):
        runs.record_step("request-1", "owner-2", "step-0", response)

    assert runs.step_result("request-1", "step-0")["response"] == response
    assert runs.step_result("request-1", "missing") is None

    runs.finalize("request-1", "owner-1", "ep-1", "completed", _terminal_event())
    # Retrieval of a completed step is allowed; a new step is not.
    assert runs.step_result("request-1", "step-0")["response"] == response
    assert (
        runs.claim_step("request-1", "owner-1", "step-0", 0, "fp-0")["state"]
        == "recorded"
    )
    with pytest.raises(ValueError, match="not running"):
        runs.claim_step("request-1", "owner-1", "step-1", 1, "fp-1")
