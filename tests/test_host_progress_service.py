"""Contracts for session todos and approved-plan progress ticks."""

from __future__ import annotations

import pytest

from openai4s.config import Config
from openai4s.host.progress import PLAN_STEP_STATUSES, ProgressService
from openai4s.host_dispatch import HostDispatcher
from openai4s.store import get_store


def _store(tmp_path):
    return get_store(Config(data_dir=tmp_path).db_path)


def _plan(store, frame_id, title):
    return store.create_plan(
        frame_id=frame_id,
        title=title,
        rationale="",
        confidence="high",
        steps=[{"id": "s1", "title": "Step"}],
    )


def _service(store, state):
    return ProgressService(
        store,
        get_frame_id=lambda: state.get("frame_id"),
        get_plan_sink=lambda: state.get("sink"),
    )


def test_todos_normalize_replace_clear_and_remain_session_local(tmp_path):
    store = _store(tmp_path)
    state = {"frame_id": None, "sink": None}
    service = _service(store, state)
    other = _service(store, state)

    result = service.todo_write(
        {
            "todos": [
                "skip me",
                {"content": "first"},
                {
                    "id": "",
                    "content": None,
                    "status": None,
                    "priority": None,
                },
            ]
        }
    )

    assert result == {
        "ok": True,
        "count": 2,
        "todos": [
            {
                "id": "t1",
                "content": "first",
                "status": "pending",
                "priority": "medium",
            },
            {
                "id": "t2",
                "content": None,
                "status": None,
                "priority": None,
            },
        ],
    }
    assert service.todo_read() == {"todos": result["todos"]}
    assert other.todo_read() == {"todos": []}

    assert service.todo_write({"todos": [{"id": "only"}]})["todos"] == [
        {
            "id": "only",
            "content": "",
            "status": "pending",
            "priority": "medium",
        }
    ]
    assert service.todo_write({}) == {"ok": True, "count": 0, "todos": []}


def test_plan_frame_and_sink_are_late_bound_and_follow_changes(tmp_path):
    store = _store(tmp_path)
    state = {"frame_id": None, "sink": None}
    service = _service(store, state)
    assert service.plan_read() == {"plan": None}

    first_frame = store.new_frame(project_id="science")
    first_plan = _plan(store, first_frame, "First")
    first_events = []
    state.update(frame_id=first_frame, sink=first_events.append)
    first = service.plan_update(
        {"step_id": "s1", "status": "completed", "note": "done"}
    )
    assert first["plan_id"] == first_plan["plan_id"]
    assert first_events == [
        {
            "plan_id": first_plan["plan_id"],
            "step_id": "s1",
            "status": "completed",
            "note": "done",
        }
    ]

    second_frame = store.new_frame(project_id="science")
    second_plan = _plan(store, second_frame, "Second")
    second_events = []
    state.update(frame_id=second_frame, sink=second_events.append)
    second = service.plan_update({"step_id": "s1", "status": "in_progress"})
    assert second["plan_id"] == second_plan["plan_id"]
    assert second_events[0]["plan_id"] == second_plan["plan_id"]
    assert service.plan_read()["plan_id"] == second_plan["plan_id"]


def test_plan_error_precedence_explicit_lookup_and_status_normalization(tmp_path):
    store = _store(tmp_path)
    state = {"frame_id": "missing", "sink": None}
    service = _service(store, state)
    assert service.plan_update({}) == {"error": "no active plan for this session"}

    current_frame = store.new_frame(project_id="science")
    current_plan = _plan(store, current_frame, "Current")
    other_frame = store.new_frame(project_id="science")
    other_plan = _plan(store, other_frame, "Other")
    events = []
    state.update(frame_id=current_frame, sink=events.append)
    assert service.plan_update({}) == {"error": "plan_update requires step_id"}
    assert service.plan_update({"plan_id": "missing-plan", "step_id": "s1"}) == {
        "error": "no active plan for this session"
    }

    result = service.plan_update(
        {
            "plan_id": other_plan["plan_id"],
            "id": "alias-step",
            "status": "not-a-status",
            "note": "normalized",
        }
    )
    assert result == {
        "ok": True,
        "plan_id": other_plan["plan_id"],
        "step_id": "alias-step",
        "status": "in_progress",
    }
    assert events[-1]["status"] == "in_progress"
    assert (
        store.get_plan(other_plan["plan_id"])["step_status"]["alias-step"]["note"]
        == "normalized"
    )
    assert store.get_plan(current_plan["plan_id"])["step_status"] == {}

    def unexpected_frame_lookup():
        raise AssertionError("explicit plan_id must not read the frame getter")

    explicit_service = ProgressService(
        store,
        get_frame_id=unexpected_frame_lookup,
        get_plan_sink=lambda: None,
    )
    assert (
        explicit_service.plan_update(
            {"plan_id": other_plan["plan_id"], "step_id": "direct"}
        )["plan_id"]
        == other_plan["plan_id"]
    )

    with pytest.raises(TypeError):
        service.plan_update({"step_id": "s1", "status": ["bad"]})


def test_plan_mutation_precedes_sink_and_sink_failure_is_best_effort(tmp_path):
    store = _store(tmp_path)
    frame_id = store.new_frame(project_id="science")
    plan = _plan(store, frame_id, "Ordered")
    observations = []
    state = {"frame_id": frame_id, "sink": None}
    service = _service(store, state)

    def observe(event):
        status = store.get_plan(plan["plan_id"])["step_status"]["s1"]["status"]
        observations.append((status, event))

    state["sink"] = observe
    service.plan_update({"step_id": "s1", "status": "completed"})
    assert observations[0][0] == "completed"

    def fail(_event):
        raise RuntimeError("telemetry unavailable")

    state["sink"] = fail
    result = service.plan_update({"step_id": "s1", "status": "skipped"})
    assert result["status"] == "skipped"
    assert store.get_plan(plan["plan_id"])["step_status"]["s1"]["status"] == "skipped"


def test_dispatcher_progress_wrappers_keep_late_bound_wiring(tmp_path):
    dispatcher = HostDispatcher(Config(data_dir=tmp_path), frame_id=None)
    frame_id = dispatcher.store.new_frame(project_id="science")
    plan = _plan(dispatcher.store, frame_id, "Dispatcher")
    events = []

    assert dispatcher._m_todo_write({"todos": [{"content": "work"}]})["count"] == 1
    assert dispatcher._m_todo_read("ignored")["todos"][0]["id"] == "t1"
    assert dispatcher._m_plan_read("ignored") == {"plan": None}

    dispatcher.frame_id = frame_id
    dispatcher.on_plan = events.append
    result = dispatcher._m_plan_update({"step_id": "s1", "status": "completed"})
    assert result["plan_id"] == plan["plan_id"]
    assert events[0]["plan_id"] == plan["plan_id"]
    assert dispatcher._m_plan_read("ignored")["plan_id"] == plan["plan_id"]
    assert dispatcher._PLAN_STEP_STATUS == PLAN_STEP_STATUSES


def test_review_status_is_bounded_public_read_only_projection(tmp_path):
    store = _store(tmp_path)
    frame_id = store.new_frame(project_id="science")
    state = {"frame_id": frame_id, "sink": None}
    service = _service(store, state)
    store.set_setting(f"review:auto:{frame_id}", "1")
    store.set_setting(f"review:model:{frame_id}", "review-model")
    store.add_step(
        step_id="review-1",
        frame_id=frame_id,
        kind="review",
        title="Evidence review",
        input={"private_evidence": "must not be returned"},
        status="running",
    )
    store.update_step(
        "review-1",
        status="done",
        output={
            "verdict": "issues",
            "summary": "One caveat",
            "issues": [{"detail": "bounded"}],
            "reviewed_artifacts": ["a-1"],
            "raw_provider_payload": "must not be returned",
        },
        summary="One caveat",
    )

    status = service.review_status()

    assert status == {
        "enabled": True,
        "reviewer_model": "review-model",
        "reviews": [
            {
                "step_id": "review-1",
                "status": "done",
                "title": "Evidence review",
                "summary": "One caveat",
                "verdict": "issues",
                "issues_count": 1,
                "reviewed_artifacts": ["a-1"],
                "created_at": status["reviews"][0]["created_at"],
            }
        ],
    }
    assert "private_evidence" not in repr(status)
    assert "raw_provider_payload" not in repr(status)
    dispatcher = HostDispatcher(Config(data_dir=tmp_path / "dispatcher"), frame_id=None)
    assert dispatcher._m_review_status("ignored") == {
        "enabled": False,
        "reviews": [],
    }
