"""PlanRepository parity behind the public Store facade."""

from __future__ import annotations

from openai4s.config import Config
from openai4s.store import get_store


def _store(tmp_path):
    return get_store(Config(data_dir=tmp_path).db_path)


def test_plan_repository_shares_store_connection_lock_and_return_shape(tmp_path):
    store = _store(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="science")
    plan = store.create_plan(
        frame_id=frame_id,
        project_id="science",
        title="Plan",
        rationale="because",
        confidence="high",
        steps=[{"id": "s1", "title": "First"}],
    )

    assert store._plans._connection is store._conn
    assert store._plans._lock is store._lock
    assert store.get_plan(plan["plan_id"]) == store._plans.get(plan["plan_id"])
    assert store.get_plan_by_frame(frame_id) == plan
    assert store.list_plans(frame_id) == [plan]


def test_plan_repository_preserves_malformed_json_fallback_and_none_updates(tmp_path):
    store = _store(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="default")
    plan = store.create_plan(
        frame_id=frame_id,
        title="Original",
        rationale="reason",
        confidence="low",
        steps=[{"id": "s1", "title": "First"}],
    )
    before = store.get_plan(plan["plan_id"])
    store.update_plan(
        plan["plan_id"],
        title=None,
        rationale=None,
        confidence=None,
        steps=None,
        status=None,
        step_status=None,
        artifact_id=None,
    )
    assert store.get_plan(plan["plan_id"]) == before

    with store._lock:
        store._conn.execute(
            "UPDATE plans SET steps=?,step_status=? WHERE plan_id=?",
            ("not-json", "[]", plan["plan_id"]),
        )
        store._conn.commit()
    malformed = store.get_plan(plan["plan_id"])
    assert malformed["steps"] == []
    assert malformed["step_status"] == {}


def test_plan_repository_facade_preserves_status_merge_and_delete(tmp_path):
    store = _store(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="default")
    plan = store.create_plan(
        frame_id=frame_id,
        title="Plan",
        rationale="",
        confidence="medium",
        steps=[{"id": "s1", "title": "First"}],
    )

    updated = store.set_plan_step_status(plan["plan_id"], "s1", "completed", "done")
    assert updated["step_status"]["s1"]["status"] == "completed"
    assert updated["step_status"]["s1"]["note"] == "done"
    assert store.set_plan_step_status("missing", "s1", "completed") is None

    store.delete_plans_for_frame(frame_id)
    assert store.get_plan(plan["plan_id"]) is None
