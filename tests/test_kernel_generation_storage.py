"""Durable kernel-generation and attempt-binding contracts."""

from __future__ import annotations

import uuid

import pytest

from openai4s.storage.actions import AttemptStateError
from openai4s.store import Store


def _store(tmp_path) -> Store:
    return Store(tmp_path / "kernel-generations.db")


def test_generation_rows_are_uuid_addressed_and_monotonic(tmp_path):
    store = _store(tmp_path)
    first = store.create_kernel_generation(
        root_frame_id="root-1",
        language="python",
        environment={"interpreter": "/env/bin/python"},
        bootstrap={"status": "pending", "loaded_sidecars": []},
        worker_pid=123,
        owner_instance_id="daemon-a",
        state="bootstrapping",
        started_at=1000,
    )
    second = store.create_kernel_generation(
        root_frame_id="root-1",
        language="python",
        environment={"interpreter": "/env/bin/python"},
        bootstrap={"status": "active", "loaded_sidecars": []},
        worker_pid=456,
        owner_instance_id="daemon-a",
        state="active",
        started_at=2000,
    )

    assert str(uuid.UUID(first["generation_id"])) == first["generation_id"]
    assert first["ordinal"] == 0
    assert second["ordinal"] == 1
    assert second["parent_generation_id"] == first["generation_id"]
    assert first["environment_manifest_id"].startswith("env-")
    assert first["bootstrap_manifest_id"].startswith("boot-")
    assert first["worker_pid"] == 123

    touched = store.touch_kernel_generation(
        second["generation_id"],
        state="busy",
        bootstrap={"status": "active", "loaded_sidecars": []},
        at=2500,
    )
    assert touched["state"] == "busy"
    assert touched["last_activity_at"] == 2500

    ended = store.finish_kernel_generation(
        second["generation_id"],
        state="released",
        reason="idle_ttl",
        ended_at=3000,
    )
    assert ended["ended_reason"] == "idle_ttl"
    assert ended["ended_at"] == 3000
    # A late touch cannot resurrect or rewrite a terminal generation.
    unchanged = store.touch_kernel_generation(
        second["generation_id"], state="active", at=4000
    )
    assert unchanged["state"] == "released"
    assert unchanged["last_activity_at"] == 3000


def test_startup_reconciliation_only_abandons_an_older_daemon(tmp_path):
    store = _store(tmp_path)
    old = store.create_kernel_generation(
        root_frame_id="old-root",
        language="python",
        owner_instance_id="daemon-old",
        state="active",
        started_at=100,
    )
    current = store.create_kernel_generation(
        root_frame_id="current-root",
        language="r",
        owner_instance_id="daemon-current",
        state="active",
        started_at=100,
    )

    assert (
        store.abandon_live_kernel_generations(
            owner_instance_id="daemon-current", ended_at=200
        )
        == 1
    )
    old_row = store.get_kernel_generation(old["generation_id"])
    current_row = store.get_kernel_generation(current["generation_id"])
    assert old_row["state"] == "abandoned"
    assert old_row["ended_reason"] == "daemon_restart"
    assert current_row["state"] == "active"
    assert current_row["ended_at"] is None


def test_lazy_attempt_generation_binding_is_write_once(tmp_path):
    store = _store(tmp_path)
    group = store.append_action_group(
        root_frame_id="root-1", turn_id="turn-1", kind="execution"
    )
    attempt = store.allocate_execution_attempt(
        group_id=group["group_id"], producing_cell_id="cell-1"
    )
    generation_id = str(uuid.uuid4())

    bound = store.bind_execution_attempt_generation(
        attempt["attempt_id"], generation_id
    )
    assert bound["generation_id"] == generation_id
    assert (
        store.bind_execution_attempt_generation(attempt["attempt_id"], generation_id)[
            "generation_id"
        ]
        == generation_id
    )
    with pytest.raises(AttemptStateError, match="already bound"):
        store.bind_execution_attempt_generation(
            attempt["attempt_id"], str(uuid.uuid4())
        )


def test_restart_abandons_only_older_daemon_attempts(tmp_path):
    store = _store(tmp_path)
    group = store.append_action_group(
        root_frame_id="root-1", turn_id="turn-1", kind="execution"
    )
    old = store.allocate_execution_attempt(
        group_id=group["group_id"],
        producing_cell_id="cell-old",
        owner_instance_id="daemon-old",
        allocated_at=100,
    )
    current = store.allocate_execution_attempt(
        group_id=group["group_id"],
        producing_cell_id="cell-current",
        owner_instance_id="daemon-current",
        allocated_at=100,
    )

    assert (
        store.abandon_incomplete_execution_attempts(
            owner_instance_id="daemon-current", finished_at=200
        )
        == 1
    )
    old_row = store.get_execution_attempt(old["attempt_id"])
    current_row = store.get_execution_attempt(current["attempt_id"])
    assert old_row["terminal_state"] == "abandoned"
    assert old_row["error"]["type"] == "daemon_restart"
    assert current_row["terminal_state"] is None


def test_kernel_generation_audit_is_not_agent_queryable(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(PermissionError, match="kernel_generations"):
        store.query("SELECT * FROM kernel_generations")
