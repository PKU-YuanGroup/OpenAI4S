"""Startup reconciliation uses only durable intent, never provider replay."""

import pytest

from openai4s.lab.reconcile import reconcile_on_startup
from openai4s.store import Store


@pytest.mark.parametrize(
    "state",
    [
        "creating",
        "ready",
        "admitted",
        "dispatching",
        "running",
        "stop_requested",
        "outcome_unknown",
    ],
)
def test_restart_resolves_intent_without_inventing_receipts(tmp_path, state):
    store = Store(tmp_path / "reconcile.sqlite")
    ledger = store.lab
    try:
        ledger.create_run(
            {
                "run_id": "old-run",
                "root_frame_id": "root",
                "mode": "simulation",
                "backend": "fake",
                "device_id": "fake.extractor.01",
                "profile": "toy-extract-v0",
                "adapter_version": "1",
                "capability_revision": "revision",
                "descriptor": {},
                "config": {},
                "config_hash": "config",
                "budgets": {},
                "create_idempotency_key": "create",
                "daemon_instance": "old",
            }
        )
        if state != "creating":
            ledger.append_initial_observation(
                "old-run",
                observation={
                    "channels": [],
                    "sim_time": 0,
                    "sim_time_unit": "model_time",
                },
                evaluation={},
            )
        if state not in {"creating", "ready"}:
            ledger.insert_command(
                {
                    "command_id": "command",
                    "run_id": "old-run",
                    "idempotency_key": "key",
                    "request_hash": "hash",
                    "operation": "mix_model",
                    "request": {},
                    "origin": "system",
                    "state": "admitted",
                }
            )
            if state != "admitted":
                ledger.begin_dispatch(
                    "command",
                    resource_keys=["lab:fake#old-run:vessel"],
                    expected_revision=0,
                )
                if state in {"running", "stop_requested"}:
                    ledger.transition_command("command", to_state=state)
                elif state == "outcome_unknown":
                    ledger.mark_outcome_unknown("command", error="unknown")
        snapshot = list(store._conn.iterdump())
        same = reconcile_on_startup(ledger, instance_id="old", clock_ms=lambda: 100)
        assert same == {
            "runs_ended": 0,
            "runs_failed": 0,
            "outcome_unknown": 0,
            "not_dispatched": 0,
            "reconciled_at_ms": 100,
        }
        assert snapshot == list(store._conn.iterdump())
        summary = reconcile_on_startup(ledger, instance_id="new", clock_ms=lambda: 200)
        run = ledger.get_run("old-run")
        assert run["status"] == ("failed" if state == "creating" else "ended")
        assert run["end_reason"] == (
            "create_failed" if state == "creating" else "provider_lost"
        )
        if state not in {"creating", "ready"}:
            row = ledger.get_command("command")
            assert row["state"] == (
                "not_dispatched" if state == "admitted" else "outcome_unknown"
            )
            assert row["receipt"] is None and row["observation_id"] is None
        assert summary["not_dispatched"] == int(state == "admitted")
        assert summary["outcome_unknown"] == int(
            state in {"dispatching", "running", "stop_requested"}
        )
        assert not ledger.inflight_commands()
        before = list(store._conn.iterdump())
        reconcile_on_startup(ledger, instance_id="newer", clock_ms=lambda: 300)
        assert list(store._conn.iterdump()) == before
    finally:
        store.close()
