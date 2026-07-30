"""PlanRepository parity behind the public Store facade."""

from __future__ import annotations

import pytest

from openai4s.config import Config
from openai4s.storage.plans import PLAN_STATUSES
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


def test_only_known_plan_statuses_can_be_written(tmp_path):
    """The enum lives in Python, not as a SQL CHECK.

    `plans` is created with `CREATE TABLE IF NOT EXISTS`, so a constraint added
    now would apply to new databases and silently not to any existing one --
    a guard that protects whoever needs it least.
    """
    from openai4s.storage.plans import PLAN_STATUSES

    store = get_store(tmp_path / "plans.db")
    frame = store.new_frame(kind="turn")
    plan = store.create_plan(
        frame_id=frame, title="t", rationale="r", confidence="high", steps=[]
    )

    for status in sorted(PLAN_STATUSES):
        store.update_plan(plan["plan_id"], status=status)
        assert store.get_plan(plan["plan_id"])["status"] == status

    with pytest.raises(ValueError, match="unknown plan status"):
        store.update_plan(plan["plan_id"], status="in_progress")
    store.close()


def test_a_plan_left_executing_by_a_dead_daemon_is_paused_at_startup(tmp_path):
    """The stuck-plan bug, at the seam that unsticks it.

    A plan reaches `executing` only while a turn runs, and a turn cannot
    outlive its process. So on a fresh boot an `executing` row is orphaned by
    definition -- and `get_by_frame` prefers the newest non-discarded plan, so
    that row shadowed every new draft for its session until someone edited the
    database by hand.

    Paused, not failed: the steps that completed did complete, and a plan whose
    turn was interrupted stopped rather than went wrong.
    """
    store = get_store(tmp_path / "orphan.db")
    frame = store.new_frame(kind="turn")
    stuck = store.create_plan(
        frame_id=frame, title="t", rationale="r", confidence="high", steps=[]
    )
    store.update_plan(stuck["plan_id"], status="executing")
    done = store.create_plan(
        frame_id=frame, title="t2", rationale="r", confidence="high", steps=[]
    )
    store.update_plan(done["plan_id"], status="completed")

    moved = store.pause_orphaned_executing_plans()

    assert moved == 1
    assert store.get_plan(stuck["plan_id"])["status"] == "paused"
    # A terminal plan is left exactly as it was.
    assert store.get_plan(done["plan_id"])["status"] == "completed"
    store.close()


def test_create_enforces_the_status_enum_the_same_way_update_does(tmp_path):
    """The enum was checked on update and not on create.

    That asymmetry looked cosmetic and was not, because of who calls create:
    session import passes the status straight out of an uploaded package, so
    any string in a user-supplied ZIP reached the column. A row whose status is
    not a status is not inert -- `get_by_frame` prefers the newest
    non-discarded plan, so it shadows every new draft for that session
    permanently, the UI's status switch falls through to a card with no
    controls, and the orphan sweep (`WHERE status='executing'`) never matches
    it either. Three separate mechanisms all quietly decline to handle it.
    """
    store = get_store(Config(data_dir=tmp_path).db_path)
    frame_id = store.new_frame(kind="turn", project_id="p")

    for bogus in ("not-a-status", "DRAFT", "executing ", "0"):
        with pytest.raises(ValueError) as refused:
            store.create_plan(
                frame_id=frame_id,
                project_id="p",
                title="t",
                rationale="r",
                confidence="high",
                steps=[],
                status=bogus,
            )
        assert bogus in str(refused.value) or "unknown plan status" in str(
            refused.value
        )

    for good in sorted(PLAN_STATUSES):
        row = store.create_plan(
            frame_id=frame_id,
            project_id="p",
            title="t",
            rationale="r",
            confidence="high",
            steps=[],
            status=good,
        )
        assert row["status"] == good


def test_an_imported_plan_cannot_claim_to_be_executing(tmp_path):
    """`executing` means a turn is running. After an import, none is.

    The exporting daemon's turn is not running in this one, so importing the
    status verbatim recreates the stuck row that `paused` and the startup sweep
    exist to eliminate -- and it stays stuck until the next restart, because
    that sweep only runs at boot. Arriving `paused` keeps the finished steps
    finished and leaves the user able to resume.
    """
    from openai4s.server.session_package import _imported_plan_status

    assert _imported_plan_status("executing") == "paused"
    # Anything unrecognised becomes a draft rather than reaching the column.
    for junk in ("not-a-status", "", None, "DISCARDED", 0):
        assert _imported_plan_status(junk) == "draft"
    # Real statuses survive, including surrounding whitespace from a hand-edited
    # package -- " completed " and "completed" are the same claim.
    for good in ("draft", "paused", "completed", "failed", "discarded"):
        assert _imported_plan_status(f"  {good} ") == good
