"""D5: the delegation completion contract — machine-readable ``task_status``.

``stop_reason`` says how the child's engine terminated; ``task_status`` says
whether the TASK is done. The value is derived exactly once, in the envelope
build inside ``DelegationRunner._run_one`` — the child's declaration is input,
machine checks can only downgrade it, and the durable lifecycle mapping
(submitted→done, max_turns→failed, error→failed, cancelled→stopped) persists
``stop_reason`` and ``task_status`` for every terminal child.
"""

from __future__ import annotations

import threading
import time

import pytest

import openai4s.agent.delegation as deleg_mod
import openai4s.agent.loop as loop_mod
from openai4s.agent.delegation import (
    DelegationError,
    DelegationRunner,
)
from openai4s.agent.models import KernelEnvSpec
from openai4s.config import get_config
from openai4s.store import get_store

_RENDEZVOUS_TIMEOUT = 30


def _submitted(output=None, *, task_status=None, final_message=None, turns=2):
    submitted = {
        "output": output if output is not None else {"ok": True},
        "completion_bullets": ["Completed child work"],
    }
    if task_status is not None:
        submitted["task_status"] = task_status
    return {
        "stop_reason": "submitted",
        "submitted_output": submitted,
        "final_message": final_message,
        "turns": turns,
    }


def _runner_with_store(**kwargs):
    cfg = get_config()
    store = get_store(cfg.db_path)
    parent = store.new_frame(kind="turn", project_id="default")
    runner = DelegationRunner(cfg, parent_frame_id=parent, store=store, **kwargs)
    return runner, store, parent


# --------------------------------------------------------------------------
# envelope shape and default derivation
# --------------------------------------------------------------------------


def test_submitted_child_defaults_to_completed_with_the_new_envelope_fields(
    monkeypatch,
):
    monkeypatch.setattr(
        loop_mod.Agent,
        "run",
        lambda self, task: _submitted(
            {"summary": "done", "limitations": ["only 3 samples"]}
        ),
    )
    runner = DelegationRunner(get_config(), child_max_turns=7)
    try:
        result = runner({"request": "finish"})
    finally:
        runner.close()

    assert result["stop_reason"] == "submitted"
    assert result["task_status"] == "completed"
    assert result["turns"] == 2
    assert result["max_turns"] == 7
    assert result["limitations"] == ["only 3 samples"]
    assert result["artifacts"] == []
    # Environment is reported even for the CLI default (no selection, no
    # durable generation): every key present, honestly None.
    assert result["environment"] == {
        "python": None,
        "env_name": None,
        "env_root": None,
        "r_env": None,
        "generation_id": None,
    }


def test_declared_partial_and_blocked_are_preserved(monkeypatch):
    for declared in ("partial", "blocked"):
        monkeypatch.setattr(
            loop_mod.Agent,
            "run",
            lambda self, task, declared=declared: _submitted(task_status=declared),
        )
        runner = DelegationRunner(get_config(), child_max_turns=3)
        try:
            result = runner({"request": "try"})
        finally:
            runner.close()
        assert result["task_status"] == declared
        # A submitted child is transport-terminal 'done' even when the task is
        # honestly not complete; the semantics live in task_status.
        assert result["stop_reason"] == "submitted"


def test_environment_reports_the_configured_spec_when_no_generation_exists(
    monkeypatch,
):
    monkeypatch.setattr(loop_mod.Agent, "run", lambda self, task: _submitted())
    env = KernelEnvSpec(
        python="/envs/sci/bin/python",
        env_root="/envs/sci",
        env_name="sci",
        r_env="r-sci",
    )
    runner = DelegationRunner(get_config(), child_max_turns=3, env=env)
    try:
        result = runner({"request": "report"})
    finally:
        runner.close()

    assert result["environment"] == {
        "python": "/envs/sci/bin/python",
        "env_name": "sci",
        "env_root": "/envs/sci",
        "r_env": "r-sci",
        "generation_id": None,
    }


def test_environment_prefers_the_childs_durable_generation(monkeypatch):
    created: dict = {}

    def run_and_register(self, task):
        store = get_store(get_config().db_path)
        row = store.create_kernel_generation(
            root_frame_id=self.frame_id,
            branch_id=self.frame_id,
            language="python",
            environment={
                "runtime": "python",
                "interpreter": "/real/bin/python",
                "environment_name": "real",
                "environment_root": "/real",
            },
            state="active",
        )
        created["generation_id"] = row["generation_id"]
        return _submitted()

    monkeypatch.setattr(loop_mod.Agent, "run", run_and_register)
    runner, store, _parent = _runner_with_store(
        child_max_turns=3,
        env=KernelEnvSpec(python="/stale/bin/python", env_name="stale"),
    )
    try:
        result = runner({"request": "report"})
    finally:
        runner.close()

    assert result["environment"]["generation_id"] == created["generation_id"]
    assert result["environment"]["python"] == "/real/bin/python"
    assert result["environment"]["env_name"] == "real"
    assert result["environment"]["env_root"] == "/real"


# --------------------------------------------------------------------------
# require_artifacts: machine checks can only downgrade
# --------------------------------------------------------------------------


def _save_child_artifact(agent, filename):
    store = get_store(get_config().db_path)
    store.save_artifact(
        path=f"/tmp/{filename}",
        filename=filename,
        content_type="text/csv",
        size_bytes=1,
        checksum="x",
        frame_id=agent.frame_id,
    )


def test_missing_required_artifacts_downgrade_a_completed_claim(monkeypatch):
    monkeypatch.setattr(
        loop_mod.Agent, "run", lambda self, task: _submitted(task_status="completed")
    )
    runner, store, _parent = _runner_with_store(child_max_turns=3)
    try:
        result = runner({"request": "produce", "require_artifacts": ["results.csv"]})
    finally:
        runner.close()

    assert result["task_status"] == "partial"
    assert result["missing_artifacts"] == ["results.csv"]
    # The option is a public override, visible on the child projection.
    assert runner.children()[0]["overrides"]["require_artifacts"] == ["results.csv"]


def test_present_required_artifacts_keep_the_declared_status(monkeypatch):
    def run_and_save(self, task):
        _save_child_artifact(self, "results.csv")
        return _submitted()

    monkeypatch.setattr(loop_mod.Agent, "run", run_and_save)
    runner, store, _parent = _runner_with_store(child_max_turns=3)
    try:
        result = runner({"request": "produce", "require_artifacts": ["results.csv"]})
    finally:
        runner.close()

    assert result["task_status"] == "completed"
    assert result["missing_artifacts"] == []
    assert result["artifacts"] == ["results.csv"]


def test_trailing_star_globs_match_required_artifacts(monkeypatch):
    def run_and_save(self, task):
        _save_child_artifact(self, "results_batch1.csv")
        return _submitted()

    monkeypatch.setattr(loop_mod.Agent, "run", run_and_save)
    runner, store, _parent = _runner_with_store(child_max_turns=3)
    try:
        result = runner({"request": "produce", "require_artifacts": ["results_*"]})
    finally:
        runner.close()

    assert result["task_status"] == "completed"
    assert result["missing_artifacts"] == []


def test_a_declared_failure_is_never_upgraded_by_present_artifacts(monkeypatch):
    def run_and_save(self, task):
        _save_child_artifact(self, "results.csv")
        return _submitted(task_status="failed")

    monkeypatch.setattr(loop_mod.Agent, "run", run_and_save)
    runner, store, _parent = _runner_with_store(child_max_turns=3)
    try:
        result = runner({"request": "produce", "require_artifacts": ["results.csv"]})
    finally:
        runner.close()

    assert result["task_status"] == "failed"


def test_malformed_require_artifacts_is_refused_before_reservation():
    runner, store, parent = _runner_with_store(child_max_turns=3)
    try:
        with pytest.raises(DelegationError, match="require_artifacts"):
            runner({"request": "x", "require_artifacts": "results.csv"})
        with pytest.raises(DelegationError, match="require_artifacts"):
            runner({"request": "x", "require_artifacts": ["ok", ""]})
        with pytest.raises(DelegationError, match="require_artifacts"):
            runner({"request": "x", "require_artifacts": ["a*b"]})
        assert runner.delegation_stats()["spawned_session"] == 0
    finally:
        runner.close()


# --------------------------------------------------------------------------
# lifecycle mapping: max_turns / error / cancelled / schema violation
# --------------------------------------------------------------------------


def test_max_turns_with_output_is_partial_and_lifecycle_failed(monkeypatch):
    monkeypatch.setattr(
        loop_mod.Agent,
        "run",
        lambda self, task: {
            "stop_reason": "max_turns",
            "submitted_output": None,
            "final_message": "got halfway through",
            "turns": 3,
        },
    )
    runner, store, parent = _runner_with_store(child_max_turns=3)
    try:
        result = runner({"request": "long task"})
        assert result["stop_reason"] == "max_turns"
        assert result["task_status"] == "partial"
        assert runner.children()[0]["status"] == "failed"
    finally:
        runner.close()

    child = store.delegation_tree(parent)["children"][0]
    assert child["status"] == "failed"
    assert child["stop_reason"] == "max_turns"
    assert child["task_status"] == "partial"


def test_max_turns_with_no_output_at_all_is_failed(monkeypatch):
    monkeypatch.setattr(
        loop_mod.Agent,
        "run",
        lambda self, task: {
            "stop_reason": "max_turns",
            "submitted_output": None,
            "final_message": None,
            "turns": 3,
        },
    )
    runner, store, parent = _runner_with_store(child_max_turns=3)
    try:
        result = runner({"request": "long task"})
    finally:
        runner.close()

    assert result["task_status"] == "failed"
    child = store.delegation_tree(parent)["children"][0]
    assert (child["status"], child["stop_reason"]) == ("failed", "max_turns")


def test_error_child_is_failed_with_stop_reason_persisted(monkeypatch):
    def broken_run(self, task):
        raise RuntimeError("kernel exploded")

    monkeypatch.setattr(loop_mod.Agent, "run", broken_run)
    runner, store, parent = _runner_with_store(child_max_turns=3)
    try:
        result = runner({"request": "boom"})
    finally:
        runner.close()

    assert result["stop_reason"] == "error"
    assert result["task_status"] == "failed"
    # The failed shape mirrors the new envelope fields.
    assert result["max_turns"] == 3
    assert result["limitations"] == []
    assert "environment" in result and "artifacts" in result
    child = store.delegation_tree(parent)["children"][0]
    assert child["status"] == "failed"
    assert child["stop_reason"] == "error"
    assert child["task_status"] == "failed"


def test_output_schema_violation_is_failed(monkeypatch):
    monkeypatch.setattr(
        loop_mod.Agent, "run", lambda self, task: _submitted({"wrong": 1})
    )
    runner, store, parent = _runner_with_store(child_max_turns=3)
    try:
        result = runner(
            {
                "request": "typed",
                "output_schema": {"type": "object", "required": ["x"]},
            }
        )
    finally:
        runner.close()

    assert "output_schema violation" in result["error"]
    assert result["task_status"] == "failed"
    child = store.delegation_tree(parent)["children"][0]
    assert child["status"] == "failed"
    assert child["task_status"] == "failed"


def test_cancelled_child_keeps_the_stopped_shape_without_task_status(monkeypatch):
    started = threading.Event()

    def cancellable_run(self, task):
        started.set()
        deadline = time.monotonic() + _RENDEZVOUS_TIMEOUT
        while time.monotonic() < deadline:
            if self.cancellation.cancelled():
                return {
                    "stop_reason": "cancelled",
                    "submitted_output": None,
                    "final_message": None,
                }
            time.sleep(0.001)
        raise AssertionError("never cancelled")

    monkeypatch.setattr(loop_mod.Agent, "run", cancellable_run)
    runner, store, parent = _runner_with_store(child_max_turns=3)
    try:
        handle = runner({"request": "stop me", "wait": False})
        assert started.wait(_RENDEZVOUS_TIMEOUT)
        runner.stop_child(handle["child_id"])
        result = runner.collect({"child_ids": [handle["child_id"]]})[0]
    finally:
        runner.close()

    assert result["stop_reason"] == "stopped"
    assert "task_status" not in result
    child = store.delegation_tree(parent)["children"][0]
    assert child["status"] == "stopped"
    assert child["task_status"] is None


# --------------------------------------------------------------------------
# bounded retry
# --------------------------------------------------------------------------


def test_retries_rerun_a_failed_child_with_limitations_appended(monkeypatch):
    tasks: list[str] = []

    def scripted_run(self, task):
        tasks.append(task)
        if len(tasks) == 1:
            return _submitted(
                {"summary": "stuck", "limitations": ["missing dependency X"]},
                task_status="blocked",
            )
        return _submitted({"summary": "recovered"})

    monkeypatch.setattr(loop_mod.Agent, "run", scripted_run)
    runner = DelegationRunner(get_config(), child_max_turns=3)
    try:
        result = runner({"request": "fragile work", "retries": 1})
        assert result["task_status"] == "completed"
        assert len(tasks) == 2
        assert "missing dependency X" in tasks[1]
        assert "task_status=blocked" in tasks[1]
        # Each retry consumes budget normally: two spawned children. The
        # original child advertises the option in its public overrides; the
        # retry child's spec has it popped (the loop owns the budget).
        assert runner.delegation_stats()["spawned_session"] == 2
        children = runner.children()
        assert len(children) == 2
        assert [child["overrides"].get("retries") for child in children] == [1, None]
    finally:
        runner.close()


@pytest.mark.parametrize("stop_method", ["stop_child", "cancel_all"])
def test_terminal_attempt_stop_atomically_prevents_retry(monkeypatch, stop_method):
    """A retry cannot appear after cancellation snapshots a terminal attempt."""

    retry_ready = threading.Event()
    release_retry = threading.Event()
    attempts: list[str] = []
    results: list[dict] = []
    errors: list[BaseException] = []
    real_retry_spec = deleg_mod._retry_spec

    def paused_retry_spec(spec, result, attempt):
        retry = real_retry_spec(spec, result, attempt)
        retry_ready.set()
        assert release_retry.wait(_RENDEZVOUS_TIMEOUT)
        return retry

    def always_failed(self, task):
        attempts.append(task)
        return _submitted(task_status="failed")

    monkeypatch.setattr(loop_mod.Agent, "run", always_failed)
    monkeypatch.setattr(deleg_mod, "_retry_spec", paused_retry_spec)
    runner, store, parent = _runner_with_store(child_max_turns=3)

    def invoke():
        try:
            results.append(runner({"request": "fragile", "retries": 1}))
        except BaseException as error:  # noqa: BLE001 - surface thread failures
            errors.append(error)

    worker = threading.Thread(target=invoke)
    worker.start()
    try:
        assert retry_ready.wait(_RENDEZVOUS_TIMEOUT)
        original = runner.children()[0]
        assert original["status"] == "done"
        assert original["task_status"] == "failed"
        if stop_method == "stop_child":
            runner.stop_child(original["child_id"])
        else:
            runner.cancel_all("test cancellation")
    finally:
        release_retry.set()
        worker.join(_RENDEZVOUS_TIMEOUT)
        runner.close(cancel=True)

    assert not worker.is_alive()
    assert errors == []
    assert attempts == ["fragile"]
    assert results[0]["task_status"] == "failed"
    assert len(runner.children()) == 1
    assert runner.delegation_stats()["spawned_session"] == 1
    durable = store.delegation_tree(parent)
    assert durable["budget"]["spawned"] == 1
    assert durable["budget"]["active"] == 0
    assert len(durable["children"]) == 1
    assert durable["children"][0]["status"] == "done"
    assert durable["children"][0]["task_status"] == "failed"


def test_retries_are_clamped_to_two(monkeypatch):
    calls: list[str] = []

    def always_blocked(self, task):
        calls.append(task)
        return _submitted(task_status="blocked")

    monkeypatch.setattr(loop_mod.Agent, "run", always_blocked)
    runner = DelegationRunner(get_config(), child_max_turns=3)
    try:
        result = runner({"request": "hopeless", "retries": 9})
    finally:
        runner.close()

    assert len(calls) == 3  # 1 original + 2 clamped retries
    assert result["task_status"] == "blocked"


def test_completed_child_never_retries_and_default_is_zero(monkeypatch):
    calls: list[str] = []

    def run_once(self, task):
        calls.append(task)
        return _submitted()

    monkeypatch.setattr(loop_mod.Agent, "run", run_once)
    runner = DelegationRunner(get_config(), child_max_turns=3)
    try:
        assert runner({"request": "fine", "retries": 2})["task_status"] == "completed"
        assert len(calls) == 1

        calls.clear()
        monkeypatch.setattr(
            loop_mod.Agent,
            "run",
            lambda self, task: (calls.append(task) or _submitted(task_status="failed")),
        )
        assert runner({"request": "no retry option"})["task_status"] == "failed"
        assert len(calls) == 1
    finally:
        runner.close()


def test_async_children_refuse_retries_before_reservation():
    runner = DelegationRunner(get_config(), child_max_turns=3)
    try:
        with pytest.raises(DelegationError, match="retries"):
            runner({"request": "async", "wait": False, "retries": 1})
        assert runner.delegation_stats()["spawned_session"] == 0
    finally:
        runner.close()


def test_malformed_retries_is_refused():
    runner = DelegationRunner(get_config(), child_max_turns=3)
    try:
        with pytest.raises(DelegationError, match="retries"):
            runner({"request": "x", "retries": "twice"})
        with pytest.raises(DelegationError, match="retries"):
            runner({"request": "x", "retries": True})
    finally:
        runner.close()


# --------------------------------------------------------------------------
# collect carries the same contract
# --------------------------------------------------------------------------


def test_collect_carries_task_status(monkeypatch):
    monkeypatch.setattr(
        loop_mod.Agent, "run", lambda self, task: _submitted(task_status="partial")
    )
    runner = DelegationRunner(get_config(), child_max_turns=3)
    try:
        handle = runner({"request": "async work", "wait": False})
        result = runner.collect({"child_ids": [handle["child_id"]]})[0]
    finally:
        runner.close()

    assert result["task_status"] == "partial"
    assert result["turns"] == 2
    assert "environment" in result
