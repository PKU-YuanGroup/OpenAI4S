"""Request/attempt identity: idempotent reserve, 409 conflict, no auto-resume."""

from __future__ import annotations

import threading
import time

import pytest

import openai4s.agent.loop as loop_mod
from openai4s.agent.delegation import (
    FANOUT_CAP,
    MAX_DEPTH,
    SESSION_CAP,
    DelegationConflictError,
    DelegationRunner,
)
from openai4s.config import get_config
from openai4s.store import get_store


def _submitted(output=None):
    return {
        "stop_reason": "submitted",
        "submitted_output": {
            "output": output if output is not None else {"ok": True},
            "completion_bullets": ["child complete"],
        },
        "final_message": None,
    }


def _root_store():
    cfg = get_config()
    store = get_store(cfg.db_path)
    root = store.new_frame(kind="turn", project_id="science")
    return cfg, store, root


def _identity(call="call-1", group="group-1"):
    return {
        "parent_action_group_id": group,
        "native_call_id": call,
    }


def test_caps_are_unchanged():
    assert FANOUT_CAP == 48
    assert SESSION_CAP == 1000
    assert MAX_DEPTH == 4


def test_ten_concurrent_identical_requests_create_one_child(monkeypatch):
    monkeypatch.setattr(loop_mod.Agent, "run", lambda self, task: _submitted(task))
    cfg, store, root = _root_store()
    runner = DelegationRunner(
        cfg,
        parent_frame_id=root,
        store=store,
        owner_instance_id="owner-conc",
        runner_instance_id="runner-conc",
    )
    errors: list[BaseException] = []
    results: list[dict] = []

    def worker():
        try:
            results.append(
                runner(
                    {
                        "request": "same concurrent task",
                        **_identity(),
                    }
                )
            )
        except BaseException as error:  # noqa: BLE001 - collect and fail later
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
        assert not thread.is_alive()
    runner.close()

    assert errors == []
    assert len(results) == 10
    assert {item["child_id"] for item in results} == {results[0]["child_id"]}
    budget = store.delegation_budget(root)
    assert budget["spawned"] == 1
    requests = store._conn.execute(
        "SELECT request_id, child_id FROM delegation_requests WHERE root_frame_id=?",
        (root,),
    ).fetchall()
    attempts = store._conn.execute(
        "SELECT attempt_id FROM delegation_attempts"
    ).fetchall()
    assert len(requests) == 1
    assert len(attempts) == 1


def test_store_reserve_ten_identical_identities_is_one_request():
    cfg, store, root = _root_store()
    store.restore_delegation_tree(
        root_frame_id=root,
        owner_instance_id="owner-store",
        runner_instance_id="runner-store",
        budget_limit=SESSION_CAP,
    )
    errors: list[BaseException] = []
    reservations: list[dict] = []

    def worker():
        try:
            reservations.append(
                store.reserve_delegation_children(
                    root_frame_id=root,
                    owner_instance_id="owner-store",
                    runner_instance_id="runner-store",
                    count=1,
                    depth=0,
                    parent_child_id=None,
                    parent_action_group_id="group-store",
                    native_call_id="call-store",
                    request_sha256="a" * 64,
                    payload={"request": "store-level"},
                )
            )
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert errors == []
    assert len({item["child_ids"][0] for item in reservations}) == 1
    assert sum(1 for item in reservations if item.get("reused")) == 9
    assert store.delegation_budget(root)["spawned"] == 1
    del cfg


def test_two_native_call_ids_in_the_same_action_group_create_two_requests(
    monkeypatch,
):
    monkeypatch.setattr(loop_mod.Agent, "run", lambda self, task: _submitted(task))
    cfg, store, root = _root_store()
    runner = DelegationRunner(
        cfg,
        parent_frame_id=root,
        store=store,
        owner_instance_id="o",
        runner_instance_id="r",
    )
    first = runner({"request": "task a", **_identity("call-a")})
    second = runner({"request": "task a", **_identity("call-b")})
    runner.close()
    assert first["child_id"] != second["child_id"]
    rows = store._conn.execute(
        "SELECT native_call_id FROM delegation_requests WHERE root_frame_id=? "
        "ORDER BY native_call_id",
        (root,),
    ).fetchall()
    assert [row[0] for row in rows] == ["call-a", "call-b"]
    assert store.delegation_budget(root)["spawned"] == 2


def test_same_key_different_digest_is_http_409(monkeypatch):
    monkeypatch.setattr(loop_mod.Agent, "run", lambda self, task: _submitted(task))
    cfg, store, root = _root_store()
    runner = DelegationRunner(
        cfg,
        parent_frame_id=root,
        store=store,
        owner_instance_id="o",
        runner_instance_id="r",
    )
    runner({"request": "first digest", **_identity()})
    with pytest.raises(DelegationConflictError) as caught:
        runner({"request": "second digest", **_identity()})
    runner.close()
    assert caught.value.http_status == 409
    assert "digest conflict" in str(caught.value)
    assert store.delegation_budget(root)["spawned"] == 1
    assert (
        store._conn.execute("SELECT COUNT(*) FROM delegation_requests").fetchone()[0]
        == 1
    )


def test_terminal_reuse_makes_zero_provider_calls(monkeypatch):
    calls: list[str] = []

    def counting_run(self, task):
        calls.append(task)
        return _submitted({"task": task})

    monkeypatch.setattr(loop_mod.Agent, "run", counting_run)
    cfg, store, root = _root_store()
    runner = DelegationRunner(
        cfg,
        parent_frame_id=root,
        store=store,
        owner_instance_id="o",
        runner_instance_id="r",
    )
    first = runner({"request": "stable task", **_identity()})
    second = runner({"request": "stable task", **_identity()})
    collected = runner.collect({"child_ids": [first["child_id"]]})
    runner.close()
    assert len(calls) == 1
    assert first["child_id"] == second["child_id"]
    assert collected[0]["request_id"]
    assert collected[0]["attempt_id"]
    assert collected[0]["artifact_refs"] == []
    assert second["request_id"] == first["request_id"]


def test_restart_does_not_auto_continue_until_explicit_continue(monkeypatch):
    calls: list[str] = []
    started = threading.Event()
    release = threading.Event()

    def gated_run(self, task):
        calls.append(task)
        started.set()
        assert release.wait(5)
        return _submitted({"task": task})

    monkeypatch.setattr(loop_mod.Agent, "run", gated_run)
    cfg, store, root = _root_store()
    first = DelegationRunner(
        cfg,
        parent_frame_id=root,
        store=store,
        owner_instance_id="daemon-old",
        runner_instance_id="runner-old",
    )
    handle = first({"request": "must not auto resume", "wait": False, **_identity()})
    assert started.wait(5)
    reopened = DelegationRunner(
        cfg,
        parent_frame_id=root,
        store=store,
        owner_instance_id="daemon-new",
        runner_instance_id="runner-new",
    )
    restored = reopened.children()[0]
    assert restored["child_id"] == handle["child_id"]
    assert restored["status"] == "stopped"
    before = len(calls)
    time.sleep(0.05)
    assert len(calls) == before
    groups_before = store.list_action_groups(root)
    time.sleep(0.05)
    assert store.list_action_groups(root) == groups_before
    release.set()
    first.collect({"child_ids": [handle["child_id"]]})
    first.close()

    continued = reopened.continue_child(handle["child_id"])
    reopened.close()
    assert len(calls) == before + 1
    assert continued["child_id"] != handle["child_id"]
    assert continued["request_id"] == restored["request_id"]
    assert continued["attempt_id"] != restored["attempt_id"]
    nos = [
        row[0]
        for row in store._conn.execute(
            "SELECT attempt_no FROM delegation_attempts ORDER BY attempt_no"
        )
    ]
    assert nos == [1, 2]
