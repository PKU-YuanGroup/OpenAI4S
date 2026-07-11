"""Tree-wide budget, cancellation, lineage, and live-steering contracts."""

from __future__ import annotations

import threading
import time

import pytest

import openai4s.agent.delegation as deleg_mod
import openai4s.agent.loop as loop_mod
from openai4s.agent.delegation import (
    DelegationBudget,
    DelegationError,
    DelegationRunner,
)
from openai4s.agent.models import RunState
from openai4s.config import get_config


def _submitted(output=None):
    return {
        "stop_reason": "submitted",
        "submitted_output": {
            "output": output or {"ok": True},
            "completion_bullets": ["Completed child work"],
        },
        "final_message": None,
    }


def _wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.001)
    raise AssertionError("condition not reached before timeout")


def test_nested_runners_share_one_tree_budget_and_stats(monkeypatch):
    monkeypatch.setattr(deleg_mod, "SESSION_CAP", 3)

    def fake_run(self, task):
        if self.delegate_depth == 1:
            nested = self.dispatcher._delegate_fn(
                {"request": ["grandchild-a", "grandchild-b"]}
            )
            assert len(nested) == 2
        return _submitted({"task": task, "depth": self.delegate_depth})

    monkeypatch.setattr(loop_mod.Agent, "run", fake_run)
    runner = DelegationRunner(get_config())

    result = runner({"request": "root child"})

    assert result["output"]["depth"] == 1
    assert runner.delegation_stats() == {
        "total": 3,
        "direct_total": 1,
        "running": 0,
        "done": 3,
        "failed": 0,
        "stopped": 0,
        "pending": 0,
        "spawned_session": 3,
        "active_session": 0,
        "remaining_session_budget": 0,
        "budget_root_frame_id": None,
        "depth": 0,
    }
    with pytest.raises(DelegationError, match="already spawned 3"):
        runner({"request": "one child too many"})


def test_shared_budget_reservation_is_atomic_across_concurrent_runners(monkeypatch):
    monkeypatch.setattr(loop_mod.Agent, "run", lambda self, task: _submitted())
    budget = DelegationBudget("root-session", limit=8)
    root = DelegationRunner(get_config(), budget=budget)
    sibling = DelegationRunner(get_config(), budget=budget)
    barrier = threading.Barrier(17)
    successes: list[str] = []
    failures: list[str] = []
    lock = threading.Lock()

    def spawn(runner, index):
        barrier.wait()
        try:
            result = runner({"request": f"child-{index}"})
        except DelegationError as error:
            with lock:
                failures.append(str(error))
        else:
            with lock:
                successes.append(result["child_id"])

    threads = [
        threading.Thread(target=spawn, args=(root if index % 2 else sibling, index))
        for index in range(16)
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(3)
        assert not thread.is_alive()

    assert len(successes) == 8
    assert len(set(successes)) == 8
    assert len(failures) == 8
    assert root._spawned == sibling._spawned == 8
    assert budget.usage() == {
        "root_frame_id": "root-session",
        "limit": 8,
        "spawned": 8,
        "active": 0,
        "remaining": 0,
    }


def test_depth_four_is_an_unconditional_leaf(monkeypatch):
    observed = []

    def fake_run(self, task):
        observed.append(
            {
                "depth": self.delegate_depth,
                "allow_delegate": self.allow_delegate,
                "delegate_fn": self.dispatcher._delegate_fn,
            }
        )
        return _submitted()

    monkeypatch.setattr(loop_mod.Agent, "run", fake_run)
    parent = DelegationRunner(get_config(), depth=3)
    parent({"request": "make a leaf"})

    assert observed == [
        {"depth": 4, "allow_delegate": False, "delegate_fn": None}
    ]
    leaf = DelegationRunner(get_config(), depth=4)
    with pytest.raises(DelegationError, match="leaves and cannot delegate"):
        leaf({"request": "must not run"})


class _FakeKernel:
    instances = []
    action_started = threading.Event()
    release_action = threading.Event()
    block_actions = False

    def __init__(self, *args, **kwargs):
        del args, kwargs
        self.interrupt_calls = 0
        self.action_codes = []
        type(self).instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        del args

    def execute(self, code, **kwargs):
        del kwargs
        if "_sd =" in code:
            return {"stdout": "", "stderr": "", "error": None}
        self.action_codes.append(code)
        type(self).action_started.set()
        if type(self).block_actions:
            assert type(self).release_action.wait(2)
        return {
            "stdout": "",
            "stderr": "",
            "error": "Interrupted" if self.interrupt_calls else None,
            "interrupted": bool(self.interrupt_calls),
        }

    def interrupt(self):
        self.interrupt_calls += 1
        type(self).release_action.set()


def _reset_fake_kernel(*, block_actions: bool) -> None:
    _FakeKernel.instances = []
    _FakeKernel.action_started = threading.Event()
    _FakeKernel.release_action = threading.Event()
    _FakeKernel.block_actions = block_actions


def test_stop_child_interrupts_exact_foreground_kernel_and_engine_cancels(monkeypatch):
    _reset_fake_kernel(block_actions=True)
    engine_results = []
    original_run = loop_mod.Agent.run

    def record_run(self, task):
        result = original_run(self, task)
        engine_results.append(result)
        return result

    def fake_chat(messages, cfg, **kwargs):
        del messages, cfg, kwargs
        return {
            "content": "```python\nprint('long scientific cell')\n```",
            "tool_calls": [],
        }

    monkeypatch.setattr(loop_mod, "Kernel", _FakeKernel)
    monkeypatch.setattr(loop_mod, "chat", fake_chat)
    monkeypatch.setattr(loop_mod.Agent, "run", record_run)
    runner = DelegationRunner(get_config(), child_max_turns=2)
    handle = runner({"request": "run a long cell", "wait": False})

    assert _FakeKernel.action_started.wait(2)
    stopped = runner.stop_child(handle["child_id"])
    result = runner.collect({"child_ids": [handle["child_id"]]})[0]

    assert stopped["status"] == "stopped"
    assert result["stop_reason"] == "stopped"
    assert result["output"] is None
    assert engine_results[0]["stop_reason"] == "cancelled"
    assert len(_FakeKernel.instances) == 1
    assert _FakeKernel.instances[0].interrupt_calls == 1
    assert len(_FakeKernel.instances[0].action_codes) == 1


def test_late_model_reply_after_stop_cannot_execute_or_submit(monkeypatch):
    _reset_fake_kernel(block_actions=False)
    model_started = threading.Event()
    release_model = threading.Event()
    engine_results = []
    model_calls = []
    original_run = loop_mod.Agent.run

    def record_run(self, task):
        result = original_run(self, task)
        engine_results.append(result)
        return result

    def late_chat(messages, cfg, **kwargs):
        del messages, cfg, kwargs
        model_calls.append("started")
        model_started.set()
        assert release_model.wait(2)
        return {
            "content": (
                "```python\n"
                "host.submit_output({'summary':'late'}, ['Submitted late'])\n"
                "```"
            ),
            "tool_calls": [],
        }

    monkeypatch.setattr(loop_mod, "Kernel", _FakeKernel)
    monkeypatch.setattr(loop_mod, "chat", late_chat)
    monkeypatch.setattr(loop_mod.Agent, "run", record_run)
    runner = DelegationRunner(get_config(), child_max_turns=2)
    handle = runner({"request": "wait for the model", "wait": False})

    assert model_started.wait(2)
    runner.stop_child(handle["child_id"])
    release_model.set()
    result = runner.collect({"child_ids": [handle["child_id"]]})[0]

    assert engine_results[0]["stop_reason"] == "cancelled"
    assert result["stop_reason"] == "stopped"
    assert result["output"] is None
    assert model_calls == ["started"]
    # A capability-scoped skill bootstrap may already have run as a system
    # cell.  Cancellation must still prevent the late model-authored action.
    assert not any(
        "host.submit_output" in code
        for code in _FakeKernel.instances[0].action_codes
    )


def test_parent_stop_propagates_to_running_descendants(monkeypatch):
    child_ready = threading.Event()
    grandchild_ready = threading.Event()
    sibling_ready = threading.Event()
    release_sibling = threading.Event()
    nested_handle = {}

    def cancellable_run(self, task):
        if self.delegate_depth == 1 and task == "parent child":
            nested_handle.update(
                self.dispatcher._delegate_fn(
                    {"request": "grandchild", "wait": False}
                )
            )
            child_ready.set()
            assert grandchild_ready.wait(2)
        elif self.delegate_depth == 1:
            sibling_ready.set()
            assert release_sibling.wait(2)
            assert not self.cancellation.cancelled()
            return _submitted({"sibling": "unharmed"})
        else:
            grandchild_ready.set()
        _wait_for(lambda: self.cancellation.cancelled())
        return {
            "stop_reason": "cancelled",
            "submitted_output": None,
            "final_message": None,
        }

    monkeypatch.setattr(loop_mod.Agent, "run", cancellable_run)
    runner = DelegationRunner(get_config())
    parent = runner({"request": "parent child", "wait": False})
    sibling = runner({"request": "sibling child", "wait": False})
    assert child_ready.wait(2)
    assert grandchild_ready.wait(2)
    assert sibling_ready.wait(2)

    runner.stop_child(parent["child_id"])
    runner.collect({"child_ids": [parent["child_id"]]})
    _wait_for(lambda: runner.delegation_stats()["stopped"] == 2)

    stats = runner.delegation_stats()
    assert stats["total"] == 3
    assert stats["stopped"] == 2
    assert stats["running"] == 1
    assert runner._children[sibling["child_id"]].stop_event.is_set() is False
    descendants = runner._tree.descendants(parent["child_id"], include_self=False)
    assert [child.child_id for child in descendants] == [
        nested_handle["child_id"]
    ]
    assert descendants[0].stop_event.is_set()
    release_sibling.set()
    sibling_result = runner.collect({"child_ids": [sibling["child_id"]]})[0]
    assert sibling_result["output"] == {"sibling": "unharmed"}


def test_live_steering_is_delivered_at_next_turn_boundary(monkeypatch):
    events = []
    first_boundary = threading.Event()
    continue_turn = threading.Event()
    observed_messages = []

    def boundary_run(self, task):
        state = RunState(
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": task},
            ],
            max_turns=self.max_turns,
        )
        self.context_policy.prepare(state)
        first_boundary.set()
        assert continue_turn.wait(2)
        state.turn = 1
        self.context_policy.prepare(state)
        observed_messages.extend(state.messages)
        return _submitted()

    monkeypatch.setattr(loop_mod.Agent, "run", boundary_run)
    runner = DelegationRunner(get_config(), event_sink=events.append)
    handle = runner({"request": "initial task", "wait": False})
    assert first_boundary.wait(2)

    queued = runner.send_message(
        {"child_id": handle["child_id"], "message": "Use the newer dataset"}
    )
    assert queued["status"] == "queued"
    snapshot = runner.children()[0]
    assert snapshot["steering"]["queued"] == 1
    assert snapshot["steering"]["delivered"] == 0

    continue_turn.set()
    runner.collect({"child_ids": [handle["child_id"]]})
    snapshot = runner.children()[0]
    assert snapshot["steering"]["queued"] == 0
    assert snapshot["steering"]["delivered"] == 1
    assert snapshot["steering"]["messages"][0]["boundary"] == 2
    assert any(
        message["role"] == "user"
        and "Use the newer dataset" in message["content"]
        for message in observed_messages
    )
    assert "steering_queued" in {event["event"] for event in events}
    assert "steering_delivered" in {event["event"] for event in events}
    assert runner.send_message(
        {"child_id": handle["child_id"], "message": "too late"}
    )["status"] == "rejected"


def test_child_model_steps_and_policy_overrides_remain_visible(monkeypatch):
    observed = []

    def fake_run(self, task):
        observed.append(
            {
                "task": task,
                "provider": self.cfg.llm.provider,
                "model": self.cfg.llm.model,
                "max_turns": self.max_turns,
            }
        )
        return _submitted()

    monkeypatch.setattr(loop_mod.Agent, "run", fake_run)
    runner = DelegationRunner(get_config())
    runner(
        {
            "request": {
                "request": "special work",
                "model": {"provider": "chatgpt", "model": "special-model"},
                "steps": 3,
                "permissions": {"bash": "deny"},
                "capabilities": ["web", "read_file"],
            }
        }
    )

    assert observed == [
        {
            "task": "special work",
            "provider": "chatgpt",
            "model": "special-model",
            "max_turns": 3,
        }
    ]
    child = runner.children()[0]
    assert child["overrides"] == {
        "model": {"provider": "chatgpt", "model": "special-model"},
        "steps": 3,
        "permissions": {"bash": "deny"},
        "capabilities": ["web", "read_file"],
    }
    assert child["depth"] == 1
    assert child["parent_child_id"] is None
    assert child["progress"]["max_turns"] == 3
