"""Direct contract tests for the structured-plan service boundary."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openai4s.config import Config
from openai4s.server.plans import PlanService
from openai4s.store import get_store


def _session(tmp_path, store, project_id="science"):
    frame_id = store.new_frame(kind="turn", project_id=project_id)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return SimpleNamespace(
        root_frame_id=frame_id,
        project_id=project_id,
        workspace=workspace,
        messages=[{"role": "user", "content": "  Design a protein   workflow  "}],
    )


def _reply(title: str, step_title: str) -> str:
    return (
        "Plan follows.\n```json\n"
        + json.dumps(
            {
                "title": title,
                "rationale": "testable",
                "confidence": "high",
                "steps": [
                    {
                        "id": "s1",
                        "title": step_title,
                        "detail": "run it",
                        "deliverables": ["result.csv"],
                    }
                ],
            }
        )
        + "\n```"
    )


def _service(tmp_path, run_message=None):
    store = get_store(Config(data_dir=tmp_path).db_path)
    events = []
    service = PlanService(
        store=store,
        emitter_for=lambda _frame_id: events.append,
        run_message=run_message or (lambda *args, **kwargs: {}),
    )
    return store, events, service


def test_finalize_reuses_draft_row_artifact_and_resets_progress(tmp_path):
    store, events, service = _service(tmp_path)
    session = _session(tmp_path, store)

    service.finalize(session, _reply("First title", "Prepare"), "prose", events.append)

    first = store.get_plan_by_frame(session.root_frame_id)
    artifact = store.get_artifact(first["artifact_id"])
    assert [event["type"] for event in events] == [
        "artifact_created",
        "plan_ready",
    ]
    assert set(events[0]) == {
        "type",
        "frame_id",
        "artifact_id",
        "filename",
    }
    assert set(events[1]) == {
        "type",
        "frame_id",
        "plan_id",
        "status",
        "plan",
        "artifact_id",
    }
    assert events[1]["plan"]["steps"][0]["status"] == "pending"
    first_body = json.loads((session.workspace / artifact["filename"]).read_text())
    assert set(first_body) == {
        "title",
        "rationale",
        "confidence",
        "steps",
    }
    assert first_body["title"] == "First title"
    assert first_body["steps"][0]["title"] == "Prepare"
    assert events[0]["artifact_id"] == artifact["artifact_id"]
    assert events[0]["filename"] == artifact["filename"]
    assert events[1]["plan_id"] == first["plan_id"]
    assert events[1]["artifact_id"] == artifact["artifact_id"]
    assert events[1]["status"] == "draft"

    store.set_plan_step_status(first["plan_id"], "s1", "completed")
    events.clear()
    service.finalize(session, _reply("Revised title", "Validate"), "new", events.append)

    revised = store.get_plan_by_frame(session.root_frame_id)
    revised_artifact = store.get_artifact(revised["artifact_id"])
    assert revised["plan_id"] == first["plan_id"]
    assert revised["artifact_id"] == first["artifact_id"]
    assert revised_artifact["filename"] == artifact["filename"]
    assert revised["title"] == "Revised title"
    assert revised["step_status"] == {}
    assert len(store.list_versions(revised["artifact_id"])) == 2
    revised_body = json.loads(
        (session.workspace / revised_artifact["filename"]).read_text()
    )
    assert revised_body["title"] == "Revised title"
    assert revised_body["steps"][0]["title"] == "Validate"
    assert [event["type"] for event in events] == [
        "artifact_created",
        "plan_ready",
    ]


def test_finalize_survives_artifact_failure(monkeypatch, tmp_path):
    store, events, service = _service(tmp_path)
    session = _session(tmp_path, store)
    save_artifact = store.save_artifact

    def fail_artifact(**kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(store, "save_artifact", fail_artifact)
    service.finalize(session, _reply("Plan", "Step"), "prose", events.append)

    plan = store.get_plan_by_frame(session.root_frame_id)
    assert plan is not None
    assert plan["artifact_id"] is None
    assert [event["type"] for event in events] == ["plan_ready"]

    monkeypatch.setattr(store, "save_artifact", save_artifact)
    service.finalize(session, _reply("Recovered", "Step"), "prose", events.append)
    recovered = store.get_plan(plan["plan_id"])
    assert recovered["artifact_id"] is not None

    monkeypatch.setattr(store, "save_artifact", fail_artifact)
    service.finalize(session, _reply("Still revised", "Step"), "prose", events.append)
    revised = store.get_plan(plan["plan_id"])
    assert revised["title"] == "Still revised"
    assert revised["artifact_id"] == recovered["artifact_id"]


def test_state_and_discard_keep_soft_failure_and_event_contract(tmp_path):
    store, events, service = _service(tmp_path)
    session = _session(tmp_path, store)

    service.finalize(session, "No structured plan", "No steps here", events.append)
    assert events == []
    assert service.get_state(session.root_frame_id) == {
        "frame_id": session.root_frame_id,
        "plan_id": None,
        "status": None,
        "plan": None,
    }
    assert service.discard(session.root_frame_id) == {
        "ok": False,
        "error": "no plan for this session",
    }

    service.finalize(session, _reply("Plan", "Step"), "prose", events.append)
    plan = store.get_plan_by_frame(session.root_frame_id)
    events.clear()
    assert service.discard(session.root_frame_id) == {
        "ok": True,
        "plan_id": plan["plan_id"],
        "status": "discarded",
    }
    assert events[0]["type"] == "plan_ready"
    assert events[0]["status"] == "discarded"

    events.clear()
    service.finalize(session, _reply("Replacement", "New step"), "prose", events.append)
    replacement = store.get_plan_by_frame(session.root_frame_id)
    assert replacement["plan_id"] != plan["plan_id"]
    assert replacement["artifact_id"] != plan["artifact_id"]


@pytest.mark.parametrize(
    ("turn_status", "plan_status"),
    [
        ("completed", "completed"),
        ("failed", "failed"),
        # Cancelling used to leave the plan on `executing`, which was not a
        # preserved semantic so much as an absent one: nothing was going to
        # move it afterwards, and `get_by_frame` prefers the newest
        # non-discarded plan, so that row shadowed every later draft for the
        # session. A cancelled plan is paused -- stopped, with work left.
        ("cancelled", "paused"),
    ],
)
def test_execution_uses_normal_turn_and_preserves_status_semantics(
    tmp_path, turn_status, plan_status
):
    calls = []

    def run_message(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": turn_status, "frame_id": args[0]}

    store, events, service = _service(tmp_path, run_message)
    session = _session(tmp_path, store)
    plan = store.create_plan(
        frame_id=session.root_frame_id,
        project_id=session.project_id,
        title="Protein plan",
        rationale="",
        confidence="high",
        steps=[
            {
                "id": "s1",
                "title": "Score",
                "detail": "rank candidates",
                "deliverables": ["scores.csv"],
            },
            {
                "title": "Review",
                "detail": "inspect results",
                "deliverables": [],
            },
        ],
    )

    result = service.run_execution(
        session.root_frame_id,
        session.project_id,
        "test-model",
    )

    args, kwargs = calls[0]
    assert args[:2] == (session.root_frame_id, session.project_id)
    assert args[3] == "test-model"
    assert kwargs == {"plan": False}
    seed = args[2]
    assert "[s1] Score：rank candidates" in seed
    assert seed.index("[s1] Score") < seed.index("[s2] Review")
    assert "scores.csv" in seed
    assert "（无指定文件）" in seed
    assert 'host.plan_update("<step_id>", "in_progress")' in seed
    assert 'host.plan_update("<step_id>", "completed")' in seed
    assert 'host.plan_update("<step_id>", "failed"' in seed
    assert "host.submit_output(...)" in seed
    assert result["plan_id"] == plan["plan_id"]
    assert result["plan_status"] == plan_status
    assert store.get_plan(plan["plan_id"])["status"] == plan_status
    assert [event["status"] for event in events] == ["executing", plan_status]


def test_execution_guards_and_revision_prompt(tmp_path):
    calls = []

    def run_message(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "completed"}

    store, _events, service = _service(tmp_path, run_message)
    session = _session(tmp_path, store)
    assert service.run_execution(session.root_frame_id, "science") == {
        "status": "failed",
        "frame_id": session.root_frame_id,
        "error": "no plan to approve",
    }
    assert calls == []

    revision = service.run_revision(
        session.root_frame_id,
        "science",
        "add a validation step",
        "test-model",
    )
    args, kwargs = calls.pop()
    assert revision == {"status": "completed"}
    assert args[:2] == (session.root_frame_id, "science")
    assert args[3] == "test-model"
    assert "add a validation step" in args[2]
    assert "不要执行、不要调用任何工具" in args[2]
    assert (
        "{title, rationale, confidence, steps:[{id,title,detail,deliverables}]}"
        in args[2]
    )
    assert kwargs == {"plan": True}

    plan = store.create_plan(
        frame_id=session.root_frame_id,
        project_id="science",
        title="Done",
        rationale="",
        confidence="high",
        steps=[{"id": "s1", "title": "Step"}],
        status="executing",
    )
    assert service.run_execution(session.root_frame_id, "science") == {
        "status": "failed",
        "frame_id": session.root_frame_id,
        "error": "plan already executing",
    }
    store.update_plan(plan["plan_id"], status="completed")
    assert service.run_execution(session.root_frame_id, "science")["error"] == (
        "plan already completed"
    )
    assert store.get_plan(plan["plan_id"])["status"] == "completed"
    assert calls == []
