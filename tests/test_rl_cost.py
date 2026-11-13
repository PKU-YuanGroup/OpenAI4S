from __future__ import annotations

import json

import pytest

from openai4s.config import Config
from openai4s.rl import (
    Episode,
    ProviderCostEvidence,
    TrajectoryRecorder,
    provider_cost_record,
)
from openai4s.store import get_store


def _job(**overrides):
    value = {
        "job_id": "job-cost",
        "provider": "byoc:test",
        "receipt": "sandbox-7",
        "status": "succeeded",
        "created_at": 100,
        "submitted_at": 110,
        "terminal_at": 410,
    }
    value.update(overrides)
    return value


def _evidence(**overrides):
    value = {
        "job_id": "job-cost",
        "receipt": "sandbox-7",
        "provider": "byoc:test",
        "source": "provider:billing",
        "observed_at": 500,
        "authenticated": True,
        "cost_usd": 0.125,
        "gpu_time_ms": 275,
    }
    value.update(overrides)
    return ProviderCostEvidence(**value)


def test_provider_cost_evidence_is_bound_to_receipt_and_keeps_job_timing_metadata():
    value = provider_cost_record(
        "episode-cost", _evidence(), _job(), decision_id="decision-2"
    )

    assert value.cost_usd == pytest.approx(0.125)
    assert value.gpu_time_ms == 275
    assert value.wall_time_ms is None
    assert value.metadata["compute_job"]["timing"] == {
        "created_at": 100,
        "submitted_at": 110,
        "terminal_at": 410,
        "elapsed_ms": 300,
    }
    assert value.metadata["provider_cost_evidence"]["receipt"] == "sandbox-7"


def test_missing_provider_amount_stays_unknown_without_inferred_gpu_time():
    value = provider_cost_record(
        "episode-unknown",
        _evidence(cost_usd=None, gpu_time_ms=None),
        _job(),
    )

    assert value.cost_usd is None
    assert value.gpu_time_ms is None
    assert value.wall_time_ms is None
    assert value.metadata["compute_job"]["timing"]["elapsed_ms"] == 300


@pytest.mark.parametrize(
    ("evidence_overrides", "job_overrides", "message"),
    [
        ({"job_id": "other"}, {}, "job_id"),
        ({"receipt": "other"}, {}, "receipt"),
        ({"provider": "ssh:lab"}, {}, "provider"),
        ({}, {"receipt": None}, "compute_job.receipt"),
        ({"observed_at": 50}, {}, "predates"),
        ({}, {"submitted_at": 500, "terminal_at": 400}, "precedes"),
    ],
)
def test_provider_cost_evidence_rejects_unmatched_or_invalid_job_facts(
    evidence_overrides, job_overrides, message
):
    with pytest.raises((ValueError, TypeError), match=message):
        provider_cost_record(
            "episode-cost", _evidence(**evidence_overrides), _job(**job_overrides)
        )


def test_provider_cost_evidence_requires_authentication_and_nonnegative_amounts():
    with pytest.raises(ValueError, match="authenticated"):
        ProviderCostEvidence(
            "job-cost", "sandbox-7", "provider:billing", 500, authenticated=False
        )
    with pytest.raises(ValueError, match="cost_usd"):
        _evidence(cost_usd=-0.01)
    with pytest.raises(ValueError, match="gpu_time_ms"):
        _evidence(gpu_time_ms=-1)


def test_provider_cost_append_is_idempotent_and_export_preserves_fact():
    recorder = TrajectoryRecorder(Episode("episode-export", "task", "session", 1))
    first = recorder.add_provider_cost(_evidence(), _job())
    second = recorder.add_provider_cost(_evidence(), _job())

    assert second == first
    exported = json.loads(recorder.export_json())
    assert len(exported["costs"]) == 1
    assert exported["costs"][0]["cost_usd"] == pytest.approx(0.125)
    assert exported["costs"][0]["metadata"]["compute_job"]["job_id"] == "job-cost"


def test_provider_cost_is_durable_and_survives_trajectory_reload(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    try:
        recorder = TrajectoryRecorder(
            Episode("episode-durable-cost", "task", "session", 1),
            repository=store.rl,
        )
        recorder.add_provider_cost(_evidence(), _job())

        reloaded = store.rl.load_trajectory("episode-durable-cost")
        assert len(reloaded.costs) == 1
        assert reloaded.costs[0].cost_usd == pytest.approx(0.125)
        assert reloaded.costs[0].metadata["compute_job"]["receipt"] == "sandbox-7"
    finally:
        store.close()
