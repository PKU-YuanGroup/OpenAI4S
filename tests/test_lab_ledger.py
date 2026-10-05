"""Offline proofs for durable Lab transactions and aggregate boundaries."""

import json
import sqlite3

import pytest

from openai4s.storage.lab import _COMMAND_SOURCES, LabLedger, LabLedgerError
from openai4s.store import Store


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "ledger.sqlite")
    yield value
    value.close()


@pytest.fixture
def ledger(store):
    return LabLedger(store._conn, store._lock, clock_ms=lambda: 123456)


def run_input(run_id="r1", root="root1", **changes):
    return {
        "run_id": run_id,
        "root_frame_id": root,
        "mode": "simulation",
        "backend": "fake",
        "device_id": "fake.extractor",
        "profile": "toy",
        "adapter_version": "1",
        "capability_revision": "rev",
        "descriptor": {},
        "config": {"seed": 7},
        "config_hash": "config1",
        "budgets": {},
        "create_idempotency_key": run_id,
        **changes,
    }


def command_input(command_id="c1", run_id="r1", **changes):
    return {
        "command_id": command_id,
        "run_id": run_id,
        "idempotency_key": command_id,
        "request_hash": "request1",
        "operation": "mix_model",
        "request": {},
        "origin": "system",
        "state": "admitted",
        **changes,
    }


def observation():
    return {
        "channels": [{"name": "layers", "source": "simulated_sensor", "value": [0.1]}],
        "sim_time": 0.1,
        "sim_time_unit": "model_time",
    }


def evaluation():
    return {
        "reward": 0.75,
        "ground_truth": {"private_test_value": 0.42},
        "metrics": {"test": True},
    }


def ready(ledger, run_id="r1", root="root1"):
    ledger.create_run(run_input(run_id, root))
    return ledger.append_initial_observation(
        run_id, observation=observation(), evaluation=evaluation()
    )


def dispatched(ledger, command_id="c1", run_id="r1", keys=("resource",)):
    ledger.insert_command(command_input(command_id, run_id))
    return ledger.begin_dispatch(
        command_id,
        resource_keys=keys,
        expected_revision=ledger.get_run(run_id)["revision"],
    )


def receipt(**changes):
    return {
        "applied": True,
        "status": "succeeded",
        "error": None,
        "raw": {"terminated": False, "truncated": False},
        "end_reason": None,
        "evaluation": evaluation(),
        "provider_action": {"index": 3},
        **changes,
    }


def snapshot(store):
    # Includes all tables, schema, and sqlite_sequence (event cursors).
    return list(store._conn.iterdump())


def assert_code(code, invoke):
    with pytest.raises(LabLedgerError) as caught:
        invoke()
    assert caught.value.code == code
    return caught.value


def test_create_run_idempotency_and_conflict(ledger, store):
    first, created = ledger.create_run(run_input(status="ended", revision=8))
    assert created and first["status"] == "creating" and first["revision"] == 0
    assert first["created_at"] == first["updated_at"] == 123456
    before = snapshot(store)
    assert ledger.create_run(
        run_input(run_id="ignored", create_idempotency_key="r1")
    ) == (first, False)
    assert snapshot(store) == before
    assert_code(
        "idempotency_conflict",
        lambda: ledger.create_run(run_input(config_hash="different")),
    )
    assert snapshot(store) == before
    assert ledger.get_run("missing") is None
    assert ledger.list_runs("root1") == [first]
    assert ledger.list_runs("other") == []
    assert ledger.nonterminal_runs() == [first]
    assert ledger.events_since("root1")[0]["state"] == "creating"


def test_insert_command_idempotency_sequence_and_rejected_accounting(ledger, store):
    ledger.create_run(run_input())
    ledger.end_run("r1", end_reason="create_failed", status="failed")
    first, inserted = ledger.insert_command(
        command_input(
            state="rejected", error_code="invalid_parameters", error="bad request"
        )
    )
    assert inserted and first["seq"] == 1 and first["completed_at"] == 123456
    before = snapshot(store)
    assert ledger.insert_command(command_input(state="rejected")) == (first, False)
    assert snapshot(store) == before
    assert_code(
        "idempotency_conflict",
        lambda: ledger.insert_command(command_input(request_hash="different")),
    )
    assert snapshot(store) == before
    second, inserted = ledger.insert_command(command_input("c2"))
    assert inserted and second["seq"] == 2
    assert ledger.get_run("r1")["command_count"] == 2
    assert ledger.list_commands("r1", after_seq=1, limit=1) == [second]
    assert ledger.get_command("missing") is None
    assert ledger.inflight_commands("r1") == [second]
    assert ledger.inflight_commands("other") == []


def test_command_sources_match_contract():
    assert _COMMAND_SOURCES == {
        "awaiting_approval": frozenset({"created"}),
        "admitted": frozenset({"created", "awaiting_approval"}),
        "dispatching": frozenset({"admitted"}),
        "running": frozenset({"dispatching"}),
        "succeeded": frozenset(
            {"dispatching", "running", "stop_requested", "outcome_unknown"}
        ),
        "failed": frozenset(
            {"dispatching", "running", "stop_requested", "outcome_unknown"}
        ),
        "rejected": frozenset({"created", "awaiting_approval", "admitted"}),
        "not_dispatched": frozenset({"created", "awaiting_approval", "admitted"}),
        "outcome_unknown": frozenset({"dispatching", "running", "stop_requested"}),
        "stop_requested": frozenset({"dispatching", "running"}),
        "stopped": frozenset({"stop_requested"}),
    }


def test_transition_cas_and_fields(ledger, store):
    ledger.create_run(run_input())
    ledger.insert_command(command_input(state="created"))
    before = snapshot(store)
    assert not ledger.transition_command(
        "c1", to_state="admitted", from_states={"awaiting_approval"}
    )
    assert snapshot(store) == before
    with pytest.raises(ValueError):
        ledger.transition_command("c1", to_state="created")
    with pytest.raises(ValueError):
        ledger.transition_command("c1", to_state="succeeded", from_states={"created"})
    assert_code(
        "invalid_parameters",
        lambda: ledger.transition_command("c1", to_state="admitted", request={}),
    )
    assert ledger.transition_command("c1", to_state="admitted", approval_ref="approved")
    assert ledger.transition_command(
        "c1", to_state="rejected", error="x" * 2100, error_code="invalid_parameters"
    )
    row = ledger.get_command("c1")
    assert row["approval_ref"] == "approved" and row["completed_at"] == 123456
    assert len(row["error"]) == 2000 and row["error"].endswith("…[truncated]")
