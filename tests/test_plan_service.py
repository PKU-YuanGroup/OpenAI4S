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
        # A Chinese plan: this test pins the zh seed. The English one is
        # `test_an_english_plan_executes_and_resumes_under_english_seeds`.
        title="蛋白质设计计划",
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
    # Approve now refuses through `claim_approval`, the same compare-and-swap
    # resume has used since two POSTs were found both executing the same steps.
    # So the refusal carries the plan it lost to, exactly as resume's does --
    # `plan_id`/`plan_status` are `None` here because there is no plan at all.
    assert service.run_execution(session.root_frame_id, "science") == {
        "status": "failed",
        "frame_id": session.root_frame_id,
        "plan_id": None,
        "plan_status": None,
        "error": "no plan to approve",
    }
    assert calls == []

    revision = service.run_revision(
        session.root_frame_id,
        "science",
        "增加一个验证步骤",
        "test-model",
    )
    args, kwargs = calls.pop()
    assert revision == {"status": "completed"}
    assert args[:2] == (session.root_frame_id, "science")
    assert args[3] == "test-model"
    assert "增加一个验证步骤" in args[2]
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
    # Per-status, not one flat "cannot approve": the caller's next move differs
    # between "somebody else is already running it" and "this one is finished".
    assert service.run_execution(session.root_frame_id, "science") == {
        "status": "failed",
        "frame_id": session.root_frame_id,
        "plan_id": plan["plan_id"],
        "plan_status": "executing",
        "error": "only a draft plan can be approved; this one is executing",
    }
    # And the losing claim leaves the row where it was rather than writing over
    # a plan another turn owns -- the whole point of the swap.
    assert store.get_plan(plan["plan_id"])["status"] == "executing"
    store.update_plan(plan["plan_id"], status="completed")
    assert service.run_execution(session.root_frame_id, "science")["error"] == (
        "only a draft plan can be approved; this one is completed"
    )
    assert store.get_plan(plan["plan_id"])["status"] == "completed"
    assert calls == []


def _plan_with_steps(store, frame_id, statuses, title="resumable"):
    """A plan whose steps carry `statuses` (index -> status, None = untouched)."""
    steps = [
        {
            "id": f"s{i + 1}",
            "title": f"step {i + 1}",
            "detail": "do it",
            "deliverables": [f"out{i + 1}.csv"],
        }
        for i in range(len(statuses))
    ]
    plan = store.create_plan(
        frame_id=frame_id,
        project_id="science",
        title=title,
        rationale="r",
        confidence="high",
        steps=steps,
        status="paused",
    )
    for index, status in enumerate(statuses):
        if status:
            store.set_plan_step_status(plan["plan_id"], f"s{index + 1}", status)
    return store.get_plan(plan["plan_id"])


def test_resume_runs_the_unfinished_steps_and_leaves_the_settled_ones(tmp_path):
    """`completed` and `failed` are both settled; `in_progress` is not.

    `failed` is a decision, not an interruption: the execution seed tells the
    agent to mark a step failed with a reason *and carry on*, so re-running it
    would redo work someone already concluded cannot be done. `in_progress` is
    the opposite -- the step was interrupted part-way, nothing records how far
    it got, and assuming it landed is the one guess that silently loses work.
    """
    store, _events, service = _service(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="science")
    plan = _plan_with_steps(
        store, frame_id, ["completed", "failed", "in_progress", None]
    )

    remaining = service.unfinished_steps(plan)
    assert [step["id"] for step in remaining] == ["s3", "s4"]


def test_the_resume_seed_names_the_finished_work_so_it_is_not_redone(tmp_path):
    """Sending only the remainder would leave the agent to infer that earlier
    work exists. A plan whose first steps produced files is one where "start
    from the top" quietly overwrites them."""
    store, _events, service = _service(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="science")
    plan = _plan_with_steps(store, frame_id, ["completed", None], title="可恢复计划")

    seed = service.resume_seed(plan, service.unfinished_steps(plan))
    assert "s1" in seed and "不要重做" in seed
    assert "step 2" in seed
    assert "out2.csv" in seed


def test_only_a_paused_plan_resumes_and_each_refusal_says_why(tmp_path):
    """Refused per-status rather than with one "cannot resume", because the
    caller's next move differs: approve a draft, wait for an executing one, do
    nothing for a finished one."""
    store, _events, service = _service(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="science")

    assert "no plan" in service.resume_execution(frame_id, "science")["error"]

    plan = _plan_with_steps(store, frame_id, [None])
    for status in ("draft", "executing", "completed", "failed", "discarded"):
        store.update_plan(plan["plan_id"], status=status)
        result = service.resume_execution(frame_id, "science")
        assert result["status"] == "failed", status
        assert status in result["error"], result["error"]
        assert result["plan_status"] == status


def test_resume_runs_the_turn_and_reports_how_many_steps_it_took(tmp_path):
    store, _events, service = _service(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="science")
    plan = _plan_with_steps(store, frame_id, ["completed", None, None])

    seen = {}

    def _run_message(root_frame_id, project_id, seed, model, plan=False):
        seen["seed"] = seed
        # The plan is `executing` while the turn runs, not before and not after.
        seen["status_during"] = store.get_plan(plan_id)["status"]
        return {"status": "completed", "frame_id": root_frame_id}

    plan_id = plan["plan_id"]
    service.run_message = _run_message
    result = service.resume_execution(frame_id, "science")

    assert result["resumed_steps"] == 2
    assert result["plan_status"] == "completed"
    assert seen["status_during"] == "executing"
    assert store.get_plan(plan_id)["status"] == "completed"


def test_a_cancelled_resume_pauses_again_rather_than_sticking_on_executing(tmp_path):
    """The same reasoning as the approve path: a stuck `executing` row shadows
    every new draft for the session, because `get_by_frame` prefers the newest
    non-discarded plan."""
    store, _events, service = _service(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="science")
    plan = _plan_with_steps(store, frame_id, [None, None])

    service.run_message = lambda *a, **k: {"status": "cancelled"}
    result = service.resume_execution(frame_id, "science")

    assert result["plan_status"] == "paused"
    assert store.get_plan(plan["plan_id"])["status"] == "paused"
    # ...and it can be resumed again, which is the point of pausing.
    service.run_message = lambda *a, **k: {"status": "completed"}
    assert service.resume_execution(frame_id, "science")["plan_status"] == "completed"


def test_a_paused_plan_with_nothing_left_completes_instead_of_running_a_turn(tmp_path):
    """Otherwise it sits paused forever, shadowing new drafts, and the resume
    button starts an agent turn with an empty step list."""
    store, _events, service = _service(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="science")
    plan = _plan_with_steps(store, frame_id, ["completed", "failed"])

    ran = []
    service.run_message = lambda *a, **k: ran.append(1) or {"status": "completed"}
    result = service.resume_execution(frame_id, "science")

    assert ran == [], "it started a turn with no steps to run"
    assert result["resumed_steps"] == 0
    assert result["plan_status"] == "completed"
    assert store.get_plan(plan["plan_id"])["status"] == "completed"


def test_a_skipped_step_is_settled_and_not_re_run(tmp_path):
    """`skipped` is a more explicit decision than `failed`, and was left out.

    The comment on `_SETTLED_STEP_STATUSES` argued exactly why `failed` counts
    as a decision rather than an interruption, and then listed two members. The
    status is in `PLAN_STEP_STATUSES`, `host.plan_update` accepts it without
    coercion, and `app.js` gives it its own glyph -- so an agent could mark a
    step skipped from a cell, see it rendered as skipped, and have resume run it
    again anyway.
    """
    store, _events, service = _service(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="science")
    plan = _plan_with_steps(store, frame_id, ["skipped", "completed", None])

    remaining = service.unfinished_steps(plan)
    assert [step["id"] for step in remaining] == ["s3"]


def test_the_resume_seed_names_a_skipped_step_among_the_settled(tmp_path):
    """Settled means "do not redo", and the seed is where that is said."""
    store, _events, service = _service(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="science")
    plan = _plan_with_steps(store, frame_id, ["skipped", None], title="可恢复计划")

    seed = service.resume_seed(plan, service.unfinished_steps(plan))
    assert "s1" in seed and "不要重做" in seed


def test_every_step_status_is_either_settled_or_deliberately_not(tmp_path):
    """The partition has to cover the vocabulary, or the next status added is
    unsettled by accident -- which is how `skipped` behaved for its whole life.

    Asserted against `PLAN_STEP_STATUSES` rather than a second literal list, so
    adding a status without deciding which side it falls on fails here.
    """
    from openai4s.host.progress import PLAN_STEP_STATUSES
    from openai4s.server.plans import PlanService

    settled = PlanService._SETTLED_STEP_STATUSES
    unsettled = frozenset({"pending", "in_progress"})

    assert settled | unsettled == frozenset(PLAN_STEP_STATUSES)
    assert settled & unsettled == frozenset()


# ------------------ seeds follow the plan's language (en / zh) -------------- #
def _english_plan(store, frame_id, status="draft"):
    return store.create_plan(
        frame_id=frame_id,
        project_id="science",
        title="Two-group synthetic data comparison",
        rationale="compare means",
        confidence="high",
        steps=[
            {
                "id": "s1",
                "title": "Generate data",
                "detail": "seed 7, 40 values",
                "deliverables": ["means.json"],
            },
            {"id": "s2", "title": "Compare", "detail": "t-test", "deliverables": []},
        ],
        status=status,
    )


def test_an_english_plan_executes_and_resumes_under_english_seeds(tmp_path):
    """The seeds were hard-coded Chinese, and every localised projection of the
    turn takes its language from the seed -- so an English session's plan ran
    with Chinese narrations and `指标：/完成内容：` completion headings."""
    from openai4s.server.completions import completion_message, response_language

    store, _events, service = _service(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="science")
    plan = _english_plan(store, frame_id)

    seed = service.execution_seed(plan)
    assert response_language(seed) == "en", seed
    assert "[s1] Generate data: seed 7, 40 values" in seed
    assert "means.json" in seed and "(no files specified)" in seed
    assert seed.index("[s1]") < seed.index("[s2]")
    for rule in (
        'host.plan_update("<step_id>", "in_progress")',
        'host.plan_update("<step_id>", "completed")',
        'host.plan_update("<step_id>", "failed", note=',
        "host.submit_output(...)",
    ):
        assert rule in seed

    store.set_plan_step_status(plan["plan_id"], "s1", "completed")
    plan = store.get_plan(plan["plan_id"])
    resume = service.resume_seed(plan, service.unfinished_steps(plan))
    assert response_language(resume) == "en", resume
    assert "[s1] Generate data (completed)" in resume
    assert "do not redo" in resume.lower()
    assert "[s2] Compare" in resume
    assert 'host.plan_update("<step_id>", "in_progress")' in resume

    headings = completion_message(
        {
            "output": {"summary": "Done.", "metrics": {"n": 40}},
            "completion_bullets": ["wrote means.json"],
        },
        language=response_language(seed),
    )
    assert "Metrics:" in headings
    assert "指标" not in headings and "完成内容" not in headings


def test_a_chinese_plan_still_gets_chinese_seeds(tmp_path):
    from openai4s.server.completions import response_language

    store, _events, service = _service(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="science")
    plan = store.create_plan(
        frame_id=frame_id,
        project_id="science",
        title="两组合成数据比较",
        rationale="",
        confidence="high",
        steps=[{"id": "s1", "title": "生成数据", "detail": "40 个值"}],
    )
    seed = service.execution_seed(plan)
    assert response_language(seed) == "zh"
    assert seed.startswith("已批准计划「两组合成数据比较」")
    assert "执行规则" in seed and "（无指定文件）" in seed


def test_the_plan_language_is_carried_from_the_drafting_request(tmp_path):
    """A plan the model happened to write in English for a Chinese request
    executes in the request's language: the draft turn spoke Chinese, so the
    execution turn does too. Read from the stored request, so it survives a
    daemon restart."""
    from openai4s.server.completions import response_language

    store, _events, service = _service(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="science")
    store.add_message(
        root_frame_id=frame_id,
        role="user",
        content="比较两组合成数据的均值",
        frame_id=frame_id,
    )
    plan = _english_plan(store, frame_id)
    assert response_language(service.execution_seed(plan)) == "zh"
    assert response_language(
        service.resume_seed(plan, service.unfinished_steps(plan))
    ) == ("zh")

    # A request stored *after* the plan was drafted is not its drafting
    # request (an execution seed, a follow-up): it does not relabel the plan.
    other = store.new_frame(kind="turn", project_id="science")
    english = _english_plan(store, other)
    store.add_message(
        root_frame_id=other,
        role="user",
        content="后来的消息",
        frame_id=other,
        created_at=int(english["created_at"]) + 1000,
    )
    assert response_language(service.execution_seed(english)) == "en"


def test_the_revision_seed_follows_the_plan_and_is_not_instructed_twice(tmp_path):
    """The revise turn runs with plan=True, and a plan turn now appends the
    plan-draft instruction unless the request already carries one -- so the
    revision seed must open with the plan-mode marker in either language."""
    from openai4s.server.completions import response_language
    from openai4s.server.plans import plan_draft_instruction

    calls = []

    def run_message(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "completed"}

    store, _events, service = _service(tmp_path, run_message)
    frame_id = store.new_frame(kind="turn", project_id="science")
    _english_plan(store, frame_id)
    service.run_revision(frame_id, "science", "add a validation step")
    args, kwargs = calls.pop()
    seed = args[2]
    assert kwargs == {"plan": True}
    assert response_language(seed) == "en", seed
    assert seed.startswith("[Plan Mode]")
    assert "add a validation step" in seed
    assert "```json" in seed and "Do not execute" in seed
    assert plan_draft_instruction(seed) is None

    zh_frame = store.new_frame(kind="turn", project_id="science")
    store.create_plan(
        frame_id=zh_frame,
        project_id="science",
        title="中文计划",
        rationale="",
        confidence="high",
        steps=[{"id": "s1", "title": "步骤"}],
    )
    service.run_revision(zh_frame, "science", "add a validation step")
    args, _kwargs = calls.pop()
    assert args[2].startswith("[计划模式]")
    assert "不要执行、不要调用任何工具" in args[2]
    assert plan_draft_instruction(args[2]) is None


def _draft_with_two_steps(store, frame_id):
    return store.create_plan(
        frame_id=frame_id,
        project_id="science",
        title="Mean and standard deviation",
        rationale="",
        confidence="high",
        steps=[
            {"id": "s1", "title": "Compute", "detail": "numbers", "deliverables": []},
            {"id": "s2", "title": "Report results", "detail": "", "deliverables": []},
        ],
    )


def _assert_not_complete_with_a_step_in_progress(store, events, plan_id):
    row = store.get_plan(plan_id)
    in_progress = [
        step_id
        for step_id, entry in (row["step_status"] or {}).items()
        if (entry or {}).get("status") == "in_progress"
    ]
    assert in_progress == ["s2"], row["step_status"]
    assert row["status"] == "paused", row
    last = [event for event in events if event.get("type") == "plan_ready"][-1]
    assert last["status"] == "paused"
    assert [step["status"] for step in last["plan"]["steps"]] == [
        "completed",
        "in_progress",
    ]


def test_a_completed_turn_that_left_a_step_in_progress_does_not_complete_the_plan(
    tmp_path,
):
    """The plan card read PLAN COMPLETE next to an in-progress step, and the row
    said the same: status completed, s2 in_progress, for good -- only a paused
    plan can resume. The model marked the last step in progress and called
    host.submit_output in the same Cell, which ended the turn, and a completed
    turn was mapped to a completed plan without looking at the steps.

    Nothing records that the step's work landed, so it is not settled on the
    agent's behalf. The plan pauses instead: the step stays in progress, the
    card says a step remains, and Resume can finish it."""
    store, events, service = _service(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="science")
    plan = _draft_with_two_steps(store, frame_id)
    plan_id = plan["plan_id"]

    def run_message(root_frame_id, project_id, seed, model, plan=False):
        # The host.plan_update sequence recorded for the Chromium frame.
        store.set_plan_step_status(plan_id, "s1", "in_progress")
        store.set_plan_step_status(plan_id, "s1", "completed")
        store.set_plan_step_status(plan_id, "s2", "in_progress")
        return {"status": "completed", "frame_id": root_frame_id}

    service.run_message = run_message
    result = service.run_execution(frame_id, "science")

    assert result["status"] == "completed"
    assert result["plan_status"] == "paused"
    _assert_not_complete_with_a_step_in_progress(store, events, plan_id)
    assert service.claim_resume(frame_id)["ok"] is True


def test_a_completed_resume_that_left_a_step_in_progress_pauses_again(tmp_path):
    store, events, service = _service(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="science")
    plan = _draft_with_two_steps(store, frame_id)
    plan_id = plan["plan_id"]
    store.set_plan_step_status(plan_id, "s1", "completed")
    store.update_plan(plan_id, status="paused")

    def run_message(root_frame_id, project_id, seed, model, plan=False):
        store.set_plan_step_status(plan_id, "s2", "in_progress")
        return {"status": "completed", "frame_id": root_frame_id}

    service.run_message = run_message
    result = service.resume_execution(frame_id, "science")

    assert result["plan_status"] == "paused"
    _assert_not_complete_with_a_step_in_progress(store, events, plan_id)


def test_a_completed_turn_that_settled_every_step_still_completes_the_plan(tmp_path):
    store, events, service = _service(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="science")
    plan_id = _draft_with_two_steps(store, frame_id)["plan_id"]

    def run_message(root_frame_id, project_id, seed, model, plan=False):
        store.set_plan_step_status(plan_id, "s1", "completed")
        store.set_plan_step_status(plan_id, "s2", "in_progress")
        store.set_plan_step_status(plan_id, "s2", "completed")
        return {"status": "completed", "frame_id": root_frame_id}

    service.run_message = run_message
    assert service.run_execution(frame_id, "science")["plan_status"] == "completed"
    assert store.get_plan(plan_id)["status"] == "completed"
    assert events[-1]["status"] == "completed"
