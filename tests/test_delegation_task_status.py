"""D5: the delegation completion contract — machine-readable ``task_status``.

``stop_reason`` says how the child's engine terminated; ``task_status`` says
whether the TASK is done. The value is derived exactly once, in the envelope
build inside ``DelegationRunner._run_one`` — the child's declaration is input,
machine checks can only downgrade it, and the durable lifecycle mapping
(submitted→done, max_turns→failed, error→failed, cancelled→stopped) persists
``stop_reason`` and ``task_status`` for every terminal child.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time

import pytest

import openai4s.agent.delegation as deleg_mod
import openai4s.agent.loop as loop_mod
from openai4s.agent.delegation import (
    DelegationError,
    DelegationRunner,
    _project_artifact_evidence,
)
from openai4s.agent.models import KernelEnvSpec
from openai4s.config import get_config
from openai4s.store import get_store
from openai4s.tools.delegation import DelegateTaskTool
from openai4s.tools.registry import execute_tool_call

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
    # No durable frame: the evidence key is omitted rather than invented.
    assert "artifact_evidence" not in result
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
    assert "artifact_evidence" not in result
    child = store.delegation_tree(parent)["children"][0]
    assert child["status"] == "stopped"
    assert child["task_status"] is None
    assert "artifact_evidence" not in child


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


# --------------------------------------------------------------------------
# bounded artifact_evidence: store records, never the child's pointers
# --------------------------------------------------------------------------

_SHA = "a" * 64


def _plant(
    frame_id,
    tmp_path,
    filename,
    *,
    status="ok",
    snapshot=True,
    size_bytes=None,
    checksum=_SHA,
    delete=False,
    cell=True,
):
    """One version attributed to ``frame_id``. The checksum is not the file hash."""
    store = get_store(get_config().db_path)
    payload = b"evidence-bytes"
    path = tmp_path / filename
    path.write_bytes(payload)
    cell_id = None
    if cell:
        if status == "ok":
            logged = {}
        elif status == "interrupted":
            logged = {"interrupted": True}
        else:
            logged = {"error": "failed"}
        cell_id = store.log_cell(
            frame_id=frame_id,
            code="value = 1\n",
            result=logged,
            origin="delegate",
        )
    recorded = len(payload) if size_bytes is None else size_bytes
    saved = store.save_artifact(
        path=str(path),
        filename=filename,
        content_type="text/plain",
        size_bytes=recorded,
        checksum=checksum,
        producing_cell_id=cell_id,
        frame_id=frame_id,
        snapshot_path=str(path) if snapshot else None,
    )
    if delete:
        path.unlink()
    return saved, path


def _stamp_versions(store, stamps):
    repo = store._artifacts
    with repo._lock:
        for version_id, created_at in stamps.items():
            repo._connection.execute(
                "UPDATE artifact_versions SET created_at=? WHERE version_id=?",
                (created_at, version_id),
            )
        repo._connection.commit()


def _item(result, version_id):
    items = result["artifact_evidence"]["items"]
    return next(item for item in items if item["version_id"] == version_id)


@pytest.mark.parametrize(
    ("filename", "public_filename"),
    [
        ("/Users/private/research/report.csv", None),
        (r"C:\Private\research\report.csv", None),
        ("C:/Private/research/report.csv", None),
        (r"\\research-host\private\report.csv", None),
        ("reports/report.csv", "reports/report.csv"),
        ("report.csv", "report.csv"),
    ],
)
def test_saved_artifact_evidence_omits_absolute_filenames(
    tmp_path, filename, public_filename
):
    """A real Host save may carry a path-shaped display filename."""
    from openai4s.host_dispatch import build_dispatcher

    cfg = get_config()
    store = get_store(cfg.db_path)
    frame_id = store.new_frame(kind="delegate")
    dispatcher = build_dispatcher(cfg, workspace=tmp_path, frame_id=frame_id)
    store.set_permission_rule(
        scope="conversation",
        scope_id=frame_id,
        tool="save_artifact",
        pattern="*",
        decision="allow",
    )
    (tmp_path / "report.csv").write_text("value\n1\n", encoding="utf-8")
    cell_id = store.log_cell(
        frame_id=frame_id, code="value = 1\n", result={}, origin="delegate"
    )
    saved = dispatcher(
        "save_artifact",
        [{"path": "report.csv", "filename": filename, "producing_cell_id": cell_id}],
    )
    assert "version_id" in saved
    rows = store.artifact_evidence_rows_for_frame(frame_id, limit=12)
    before = json.dumps(rows, sort_keys=True)

    evidence = _project_artifact_evidence(rows, frame_id)

    assert evidence["items"][0]["filename"] == public_filename
    assert evidence["items"][0]["version_id"] == saved["version_id"]
    assert evidence["items"][0]["verdict"] == "verified_version_and_producer"
    assert rows["versions"][0]["filename"] == filename
    assert json.dumps(rows, sort_keys=True) == before


def test_other_frame_artifact_is_excluded_from_child_evidence(monkeypatch, tmp_path):
    """A same-name version from another frame is not this child's evidence.

    A row the reader leaked anyway stays insufficient: attribution is part of
    the verdict, so a native write on another frame cannot be verified.
    """
    planted = {}

    def run(self, task):
        other = planted["other"]
        foreign, _path = _plant(other, tmp_path, "shared.csv", cell=False)
        own, _path = _plant(self.frame_id, tmp_path, "shared.csv")
        planted["foreign"] = foreign["version_id"]
        planted["own"] = own["version_id"]
        return _submitted()

    monkeypatch.setattr(loop_mod.Agent, "run", run)
    runner, store, parent = _runner_with_store(child_max_turns=3)
    other = store.new_frame(parent_id=parent, kind="delegate")
    planted["parent"] = parent
    planted["other"] = other
    try:
        result = runner({"request": "produce"})
    finally:
        runner.close()

    assert result["artifacts"] == ["shared.csv"]
    assert result["task_status"] == "completed"
    ids = [item["version_id"] for item in result["artifact_evidence"]["items"]]
    assert planted["foreign"] not in ids
    own = _item(result, planted["own"])
    assert own["verdict"] == "verified_version_and_producer"

    leaked = tmp_path / "leaked.txt"
    leaked.write_bytes(b"foreign-native")
    projected = _project_artifact_evidence(
        {
            "versions": [
                {
                    "version_id": "v-foreign",
                    "artifact_id": "a-foreign",
                    "filename": "shared.csv",
                    "checksum": _SHA,
                    "size_bytes": leaked.stat().st_size,
                    "snapshot_path": str(leaked),
                    "producing_cell_id": None,
                    "frame_id": "frame-other",
                }
            ],
            "observations": [],
            "cells": {},
            "total": 1,
        },
        "frame-child",
    )
    leaked_item = projected["items"][0]
    assert leaked_item["verdict"] == "insufficient_evidence"
    assert "other_frame" in leaked_item["reasons"]


def test_metadata_without_a_snapshot_is_insufficient(monkeypatch, tmp_path):
    planted = {}

    def run(self, task):
        saved, _path = _plant(self.frame_id, tmp_path, "bare.txt", snapshot=False)
        planted["version_id"] = saved["version_id"]
        return _submitted()

    monkeypatch.setattr(loop_mod.Agent, "run", run)
    runner, _store, _parent = _runner_with_store(child_max_turns=3)
    try:
        result = runner({"request": "meta"})
    finally:
        runner.close()

    item = _item(result, planted["version_id"])
    assert item["verdict"] == "insufficient_evidence"
    assert item["reasons"] == ["no_snapshot"]
    assert item["checksum"] == _SHA
    assert item["capture_kind"] is None


def test_uppercase_checksum_is_not_a_sha256_record(monkeypatch, tmp_path):
    planted = {}

    def run(self, task):
        saved, _path = _plant(self.frame_id, tmp_path, "upper.txt", checksum="A" * 64)
        planted["version_id"] = saved["version_id"]
        return _submitted()

    monkeypatch.setattr(loop_mod.Agent, "run", run)
    runner, _store, _parent = _runner_with_store(child_max_turns=3)
    try:
        result = runner({"request": "upper"})
    finally:
        runner.close()

    item = _item(result, planted["version_id"])
    assert item["checksum"] is None
    assert item["verdict"] == "insufficient_evidence"
    assert "no_checksum" in item["reasons"]


def test_missing_or_resized_snapshot_is_insufficient(monkeypatch, tmp_path):
    planted = {}

    def run(self, task):
        missing, _path = _plant(self.frame_id, tmp_path, "gone.txt", delete=True)
        resized, path = _plant(self.frame_id, tmp_path, "resized.txt", size_bytes=1)
        planted["missing"] = missing["version_id"]
        planted["resized"] = resized["version_id"]
        planted["resized_bytes"] = path.read_bytes()
        return _submitted()

    monkeypatch.setattr(loop_mod.Agent, "run", run)
    runner, _store, _parent = _runner_with_store(child_max_turns=3)
    try:
        result = runner({"request": "files"})
    finally:
        runner.close()

    missing = _item(result, planted["missing"])
    resized = _item(result, planted["resized"])
    assert missing["verdict"] == "insufficient_evidence"
    assert "snapshot_missing" in missing["reasons"]
    assert resized["verdict"] == "insufficient_evidence"
    assert "size_mismatch" in resized["reasons"]
    assert hashlib.sha256(planted["resized_bytes"]).hexdigest() != resized["checksum"]


def test_reused_head_is_verified_for_the_observing_child(monkeypatch, tmp_path):
    planted = {}

    def run(self, task):
        store = get_store(get_config().db_path)
        other = planted["other"]
        payload = b"reuse-bytes"
        path = tmp_path / "result.csv"
        path.write_bytes(payload)
        other_cell = store.log_cell(
            frame_id=other,
            code="value = 1\n",
            result={"error": "failed"},
            origin="delegate",
        )
        child_cell = store.log_cell(
            frame_id=self.frame_id,
            code="value = 2\n",
            result={},
            origin="delegate",
        )
        original = store.record_cell_artifact(
            path=str(path),
            filename="result.csv",
            content_type="text/csv",
            size_bytes=len(payload),
            checksum=_SHA,
            producing_cell_id=other_cell,
            frame_id=other,
            snapshot_path=str(path),
            reuse_matching_head=True,
        )
        reused = store.record_cell_artifact(
            path=str(path),
            filename="result.csv",
            content_type="text/csv",
            size_bytes=len(payload),
            checksum=_SHA,
            producing_cell_id=child_cell,
            frame_id=self.frame_id,
            snapshot_path=str(tmp_path / "not-retained.csv"),
            reuse_matching_head=True,
        )
        planted["version_id"] = reused["version_id"]
        planted["original"] = original["version_id"]
        planted["capture_kind"] = reused["capture_kind"]
        planted["digest"] = hashlib.sha256(payload).hexdigest()
        return _submitted()

    monkeypatch.setattr(loop_mod.Agent, "run", run)
    runner, store, parent = _runner_with_store(child_max_turns=3)
    planted["other"] = store.new_frame(parent_id=parent, kind="delegate")
    try:
        result = runner({"request": "reuse"})
    finally:
        runner.close()

    assert planted["version_id"] == planted["original"]
    assert planted["capture_kind"] == "head_checksum_reused"
    assert planted["digest"] != _SHA
    rows = store.artifact_evidence_rows_for_frame(result["frame_id"], limit=12)
    version = next(
        row for row in rows["versions"] if row["version_id"] == planted["version_id"]
    )
    assert version["frame_id"] == planted["other"]
    item = _item(result, planted["version_id"])
    assert item["verdict"] == "verified_version_and_producer"
    assert item["capture_kind"] == "head_checksum_reused"
    assert item["cell_status"] == "ok"
    assert item["checksum"] == _SHA
    assert item["reasons"] == []


def test_failed_producing_cell_is_insufficient(monkeypatch, tmp_path):
    planted = {}

    def run(self, task):
        saved, _path = _plant(self.frame_id, tmp_path, "bad.txt", status="error")
        planted["version_id"] = saved["version_id"]
        return _submitted()

    monkeypatch.setattr(loop_mod.Agent, "run", run)
    runner, _store, _parent = _runner_with_store(child_max_turns=3)
    try:
        result = runner({"request": "fail cell"})
    finally:
        runner.close()

    item = _item(result, planted["version_id"])
    assert item["verdict"] == "insufficient_evidence"
    assert item["cell_status"] == "error"
    assert "cell_failed" in item["reasons"]


def test_evidence_keeps_twelve_newest_versions(monkeypatch, tmp_path):
    planted = {}

    def run(self, task):
        store = get_store(get_config().db_path)
        ids = []
        for index in range(13):
            saved, _path = _plant(self.frame_id, tmp_path, f"f{index:02d}.txt")
            ids.append(saved["version_id"])
        _stamp_versions(
            store, {version_id: index + 1 for index, version_id in enumerate(ids)}
        )
        planted["ids"] = ids
        return _submitted()

    monkeypatch.setattr(loop_mod.Agent, "run", run)
    runner, _store, _parent = _runner_with_store(child_max_turns=3)
    try:
        result = runner({"request": "many"})
    finally:
        runner.close()

    evidence = result["artifact_evidence"]
    assert evidence["total"] == 13
    assert evidence["truncated"] is True
    assert len(evidence["items"]) == 12
    kept = [item["version_id"] for item in evidence["items"]]
    assert planted["ids"][0] not in kept
    assert kept[0] == planted["ids"][-1]
    assert set(kept) == set(planted["ids"][1:])
    assert result["task_status"] == "completed"
    assert len(result["artifacts"]) == 13


def test_evidence_does_not_change_task_status_or_artifact_names(monkeypatch, tmp_path):
    monkeypatch.setattr(
        loop_mod.Agent,
        "run",
        lambda self, task: _submitted(task_status="completed"),
    )
    runner, _store, _parent = _runner_with_store(child_max_turns=3)
    try:
        missing = runner({"request": "produce", "require_artifacts": ["results.csv"]})
    finally:
        runner.close()
    assert missing["task_status"] == "partial"
    assert missing["missing_artifacts"] == ["results.csv"]
    assert missing["artifacts"] == []
    assert missing["artifact_evidence"]["items"] == []

    def run(self, task):
        _save_child_artifact(self, "results.csv")
        return _submitted(task_status="completed")

    monkeypatch.setattr(loop_mod.Agent, "run", run)
    runner, _store, _parent = _runner_with_store(child_max_turns=3)
    try:
        present = runner({"request": "produce", "require_artifacts": ["results.csv"]})
    finally:
        runner.close()
    assert present["task_status"] == "completed"
    assert present["missing_artifacts"] == []
    assert present["artifacts"] == ["results.csv"]
    item = present["artifact_evidence"]["items"][0]
    assert item["filename"] == "results.csv"
    assert item["verdict"] == "insufficient_evidence"
    assert "no_checksum" in item["reasons"]
    assert "no_snapshot" in item["reasons"]


def test_child_reported_evidence_is_ignored(monkeypatch, tmp_path):
    planted = {}

    def run(self, task):
        saved, _path = _plant(self.frame_id, tmp_path, "real.txt", cell=False)
        planted["version_id"] = saved["version_id"]
        return _submitted(
            {
                "ok": True,
                "artifact_evidence": {
                    "scope": "version_and_producer",
                    "items": [
                        {
                            "version_id": "forged",
                            "verdict": "verified_version_and_producer",
                        }
                    ],
                },
                "artifact_refs": [{"version_id": "forged"}],
            }
        )

    monkeypatch.setattr(loop_mod.Agent, "run", run)
    runner, _store, _parent = _runner_with_store(child_max_turns=3)
    try:
        result = runner({"request": "forge"})
    finally:
        runner.close()

    assert result["artifact_refs"] == []
    assert result["task_status"] == "completed"
    ids = [item["version_id"] for item in result["artifact_evidence"]["items"]]
    assert ids == [planted["version_id"]]
    assert "forged" not in ids
    assert result["output"]["artifact_refs"][0]["version_id"] == "forged"


def test_truncated_result_keeps_artifact_evidence(monkeypatch, tmp_path):
    planted = {}

    def run(self, task):
        saved, _path = _plant(self.frame_id, tmp_path, "kept.txt", cell=False)
        planted["version_id"] = saved["version_id"]
        output = {f"k{index:02d}": "y" * 500 for index in range(60)}
        return _submitted(output)

    monkeypatch.setattr(loop_mod.Agent, "run", run)
    runner, store, parent = _runner_with_store(child_max_turns=3)
    try:
        result = runner({"request": "huge"})
    finally:
        runner.close()

    assert result["output"]["k00"] == "y" * 500
    assert _item(result, planted["version_id"])["verdict"] == (
        "verified_version_and_producer"
    )
    row = store._conn.execute(
        "SELECT result_json FROM delegation_children WHERE root_frame_id=?",
        (parent,),
    ).fetchone()
    assert len(row["result_json"]) <= 16_000
    decoded = json.loads(row["result_json"])
    assert decoded["truncated"] is True
    assert decoded["task_status"] == "completed"
    assert (
        decoded["artifact_evidence"]["items"][0]["version_id"] == planted["version_id"]
    )
    child = store.delegation_tree(parent)["children"][0]
    assert child["artifact_evidence"]["items"][0]["version_id"] == planted["version_id"]
    assert child["task_status"] == "completed"


def test_native_write_without_a_cell_can_be_verified(monkeypatch, tmp_path):
    planted = {}

    def run(self, task):
        saved, path = _plant(self.frame_id, tmp_path, "native.txt", cell=False)
        planted["version_id"] = saved["version_id"]
        planted["digest"] = hashlib.sha256(path.read_bytes()).hexdigest()
        return _submitted()

    monkeypatch.setattr(loop_mod.Agent, "run", run)
    runner, _store, _parent = _runner_with_store(child_max_turns=3)
    try:
        result = runner({"request": "native"})
    finally:
        runner.close()

    item = _item(result, planted["version_id"])
    assert planted["digest"] != _SHA
    assert item["verdict"] == "verified_version_and_producer"
    assert item["reasons"] == ["no_cell_receipt"]
    assert item["cell_status"] is None
    assert item["producing_cell_id"] is None


def test_evidence_read_failure_does_not_fail_the_child(monkeypatch):
    def explode(_frame_id, *, limit):
        raise RuntimeError("evidence read failed")

    monkeypatch.setattr(loop_mod.Agent, "run", lambda self, task: _submitted())
    runner, store, _parent = _runner_with_store(child_max_turns=3)
    monkeypatch.setattr(store, "artifact_evidence_rows_for_frame", explode)
    try:
        result = runner({"request": "still finishes"})
    finally:
        runner.close()

    assert result["task_status"] == "completed"
    evidence = result["artifact_evidence"]
    assert evidence["unavailable"] is True
    assert evidence["items"] == []
    assert evidence["total"] == 0
    assert evidence["truncated"] is False
    assert isinstance(evidence["checked_at"], float)


def test_output_schema_failure_still_carries_evidence(monkeypatch, tmp_path):
    planted = {}

    def run(self, task):
        saved, _path = _plant(self.frame_id, tmp_path, "typed.txt", cell=False)
        planted["version_id"] = saved["version_id"]
        return _submitted({"wrong": 1})

    monkeypatch.setattr(loop_mod.Agent, "run", run)
    runner, _store, _parent = _runner_with_store(child_max_turns=3)
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
    assert _item(result, planted["version_id"])["verdict"] == (
        "verified_version_and_producer"
    )


def test_exception_path_still_carries_evidence(monkeypatch, tmp_path):
    planted = {}

    def run(self, task):
        saved, _path = _plant(self.frame_id, tmp_path, "boom.txt", cell=False)
        planted["version_id"] = saved["version_id"]
        raise RuntimeError("kernel exploded")

    monkeypatch.setattr(loop_mod.Agent, "run", run)
    runner, _store, _parent = _runner_with_store(child_max_turns=3)
    try:
        result = runner({"request": "boom"})
    finally:
        runner.close()

    assert result["task_status"] == "failed"
    assert result["stop_reason"] == "error"
    assert _item(result, planted["version_id"])["verdict"] == (
        "verified_version_and_producer"
    )


def _cell_less_version_with_observation(tmp_path, *, cell_result: dict):
    """An owned version whose producing_cell_id stays empty, plus one observation."""

    store = get_store(get_config().db_path)
    parent = store.new_frame(kind="turn", project_id="science")
    child = store.new_frame(parent_id=parent, kind="delegate", project_id="science")
    payload = b"evidence-bytes"
    path = tmp_path / "reused.txt"
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    first = store.record_cell_artifact(
        path=str(path),
        filename="reused.txt",
        content_type="text/plain",
        size_bytes=len(payload),
        checksum=digest,
        producing_cell_id=None,
        frame_id=child,
        snapshot_path=str(path),
        reuse_matching_head=True,
    )
    cell_id = store.log_cell(
        frame_id=child,
        code="value = 1\n",
        result=cell_result,
        origin="delegate",
    )
    second = store.record_cell_artifact(
        path=str(path),
        filename="reused.txt",
        content_type="text/plain",
        size_bytes=len(payload),
        checksum=digest,
        producing_cell_id=cell_id,
        frame_id=child,
        snapshot_path=str(path),
        reuse_matching_head=True,
    )
    assert second["version_id"] == first["version_id"]
    assert second["capture_kind"] == "head_checksum_reused"
    rows = store.artifact_evidence_rows_for_frame(child, limit=12)
    version = next(
        row for row in rows["versions"] if row["version_id"] == first["version_id"]
    )
    assert not version["producing_cell_id"]
    return rows, child, first["version_id"]


@pytest.mark.parametrize(
    ("cell_result", "status"),
    [({"error": "failed"}, "error"), ({"interrupted": True}, "interrupted")],
)
def test_owned_version_without_a_cell_adopts_a_failed_observation(
    tmp_path, cell_result, status
):
    """A cell-less owned version used to ignore the observation and verify.

    The latest same-frame observation is the producer. A Cell that is not ok
    blocks the verdict.
    """

    rows, child, version_id = _cell_less_version_with_observation(
        tmp_path, cell_result=cell_result
    )
    evidence = _project_artifact_evidence(rows, child)
    item = next(row for row in evidence["items"] if row["version_id"] == version_id)
    assert item["verdict"] == "insufficient_evidence"
    assert item["cell_status"] == status
    assert "cell_failed" in item["reasons"]
    assert isinstance(evidence["checked_at"], float)


def test_a_parent_cell_on_a_child_version_is_other_frame(tmp_path):
    store = get_store(get_config().db_path)
    parent = store.new_frame(kind="turn", project_id="science")
    child = store.new_frame(parent_id=parent, kind="delegate", project_id="science")
    parent_cell = store.log_cell(
        frame_id=parent,
        code="value = 1\n",
        result={},
        origin="agent",
    )
    payload = b"evidence-bytes"
    path = tmp_path / "owned.txt"
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    saved = store.record_cell_artifact(
        path=str(path),
        filename="owned.txt",
        content_type="text/plain",
        size_bytes=len(payload),
        checksum=digest,
        producing_cell_id=parent_cell,
        frame_id=child,
        snapshot_path=str(path),
    )
    rows = store.artifact_evidence_rows_for_frame(child, limit=12)
    evidence = _project_artifact_evidence(rows, child)
    item = next(
        row for row in evidence["items"] if row["version_id"] == saved["version_id"]
    )
    assert item["verdict"] == "insufficient_evidence"
    assert "cell_other_frame" in item["reasons"]


def test_an_unknown_cell_id_is_not_recorded(tmp_path):
    store = get_store(get_config().db_path)
    parent = store.new_frame(kind="turn", project_id="science")
    child = store.new_frame(parent_id=parent, kind="delegate", project_id="science")
    payload = b"evidence-bytes"
    path = tmp_path / "unknown.txt"
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    saved = store.record_cell_artifact(
        path=str(path),
        filename="unknown.txt",
        content_type="text/plain",
        size_bytes=len(payload),
        checksum=digest,
        producing_cell_id="cell-does-not-exist",
        frame_id=child,
        snapshot_path=str(path),
    )
    rows = store.artifact_evidence_rows_for_frame(child, limit=12)
    evidence = _project_artifact_evidence(rows, child)
    item = next(
        row for row in evidence["items"] if row["version_id"] == saved["version_id"]
    )
    assert item["verdict"] == "insufficient_evidence"
    assert "cell_not_recorded" in item["reasons"]


def test_the_worker_injects_the_running_cell_into_prov_record():
    import openai4s.kernel.worker as worker_mod

    previous = worker_mod._ACTIVE_CELL_ID[0]
    worker_mod._ACTIVE_CELL_ID[0] = "cell-from-worker"
    try:
        enriched = worker_mod._attach_cell_context(
            "prov_record",
            [{"path": "out.txt", "producing_cell_id": "cell-forged"}],
        )
        assert enriched[0]["executionCellId"] == "cell-from-worker"
        assert enriched[0]["producing_cell_id"] == "cell-forged"
        untouched = worker_mod._attach_cell_context("query", [{"sql": "select 1"}])
        assert untouched == [{"sql": "select 1"}]
        worker_mod._ACTIVE_CELL_ID[0] = ""
        plain = worker_mod._attach_cell_context(
            "prov_record",
            [{"path": "out.txt", "producing_cell_id": "cell-forged"}],
        )
        assert plain == [{"path": "out.txt", "producing_cell_id": "cell-forged"}]
    finally:
        worker_mod._ACTIVE_CELL_ID[0] = previous


def test_provenance_record_prefers_the_injected_cell_and_keeps_a_direct_claim(tmp_path):
    from types import SimpleNamespace

    from openai4s.host.data import HostDataService

    class _Store:
        def __init__(self) -> None:
            self.fields = None

        def record_cell_artifact(self, **fields):
            self.fields = fields
            return {"version_id": "v-1", "artifact_id": "a-1"}

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "out.bin").write_bytes(b"bytes")
    store = _Store()
    config = SimpleNamespace(
        data_dir=tmp_path / "data",
        artifacts_dir=tmp_path / "artifacts",
        roadmap_features=SimpleNamespace(stage1_trusted_delivery=False),
    )

    def resolve(path, *, must_exist=False):
        result = (workspace / path).resolve()
        if must_exist and not result.exists():
            raise FileNotFoundError(result)
        return result

    service = HostDataService(
        store=store,
        config=config,
        frame_id="frame-1",
        resolve_path=resolve,
    )
    service.provenance_record(
        {
            "path": "out.bin",
            "filename": "out.bin",
            "producing_cell_id": "cell-forged",
            "execution_cell_id": "cell-injected",
        }
    )
    assert store.fields["producing_cell_id"] == "cell-injected"
    service.provenance_record(
        {
            "path": "out.bin",
            "filename": "out.bin",
            "producing_cell_id": "cell-legacy",
        }
    )
    assert store.fields["producing_cell_id"] == "cell-legacy"
    service.provenance_record(
        {
            "path": "out.bin",
            "filename": "out.bin",
            "producing_cell_id": "cell-legacy",
            "execution_cell_id": "",
        }
    )
    assert store.fields["producing_cell_id"] == "cell-legacy"


# --------------------------------------------------------------------------
# a failed child is a failed tool call, not a successful one
# --------------------------------------------------------------------------


def _failed_child(**overrides):
    envelope = {
        "child_id": "child-0-1",
        "name": "GENERAL",
        "task_status": "failed",
        "stop_reason": "error",
        "output": None,
        "error": "LLM connection or response headers failed",
    }
    envelope.update(overrides)
    return envelope


def test_failed_child_envelope_is_a_failed_tool_call():
    """The envelope keeps its structured fields but must report failure.

    Only a single-key ``{"error": ...}`` used to count as a tool error, so a
    failed ``delegate_task`` returned a rich envelope and was marked ok: the
    model, the Action Ledger and the no-progress circuit disagreed with the
    Step card, which already rendered the child as failed.
    """

    tool = DelegateTaskTool()
    assert (
        tool.declared_failure(_failed_child())
        == "LLM connection or response headers failed"
    )


def test_not_done_child_states_are_not_tool_failures():
    """partial/blocked/max_turns carry output the parent can still use."""

    tool = DelegateTaskTool()
    for envelope in (
        {"task_status": "completed", "stop_reason": "submitted"},
        {"task_status": "partial", "stop_reason": "max_turns", "error": "budget"},
        {"task_status": "blocked", "stop_reason": "stopped"},
        {"task_status": "running", "stop_reason": None},
    ):
        assert tool.declared_failure(envelope) is None


def test_single_child_soft_fail_still_counts():
    assert DelegateTaskTool().declared_failure({"error": "boom"}) == "boom"


def test_a_fanout_fails_only_when_every_child_failed():
    tool = DelegateTaskTool()
    bad = _failed_child()
    good = {"task_status": "completed", "stop_reason": "submitted", "output": "ok"}
    assert (
        tool.declared_failure([bad, bad]) == "LLM connection or response headers failed"
    )
    assert tool.declared_failure([good, bad]) is None


def test_execute_tool_call_marks_a_transport_failed_child_as_an_error():
    """The regression, through the seam both CLI and Web use."""

    def dispatcher(method, arguments):
        assert method == "delegate"
        return _failed_child()

    text, ok = execute_tool_call(
        dispatcher,
        {"name": "delegate_task", "arguments": {"request": "inspect the file"}},
    )
    assert ok is False
    assert "error: LLM connection or response headers failed" in text
