"""Offline contracts for the constrained scientific Reviewer."""

from __future__ import annotations

import io
import json
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from openai4s import review as review_mod
from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.store import get_store


class _Hub:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emitter(self, root_frame_id: str):
        def emit(event: dict) -> None:
            event.setdefault("root_frame_id", root_frame_id)
            self.events.append(event)

        return emit

    def broadcast(self, root_frame_id: str, event: dict) -> None:
        event.setdefault("root_frame_id", root_frame_id)
        self.events.append(event)


def _cfg(tmp_path: Path) -> Config:
    return Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key", model="worker-model"),
        max_turns=3,
    )


def _review_context(tmp_path: Path):
    cfg = _cfg(tmp_path)
    hub = _Hub()
    runner = gateway_mod.SessionRunner(cfg, hub)
    store = get_store(cfg.db_path)
    fid = store.new_frame(kind="turn", project_id="default", status="ready")
    st = gateway_mod.SessionState(fid, "default", runner.workspace_for(fid))
    st.dispatcher = SimpleNamespace(last_output={"answer": "submitted result"})
    return cfg, hub, runner, store, fid, st


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"verdict":"pass","summary":"ok","issues":[]}', "pass"),
        (
            '```json\n{"verdict":"issues","issues":[{"detail":"missing file"}]}\n```',
            "issues",
        ),
        (
            'Preface {not-json} then {"verdict":"pass","issues":[]} trailing prose',
            "pass",
        ),
    ],
)
def test_json_object_accepts_plain_fenced_and_prose_wrapped_json(raw, expected):
    assert review_mod._json_object(raw)["verdict"] == expected


@pytest.mark.parametrize("raw", ["", "no object", "```json\n[1, 2]\n```", "{broken"])
def test_json_object_rejects_responses_without_an_object(raw):
    with pytest.raises(review_mod.ReviewError, match="no valid JSON object"):
        review_mod._json_object(raw)


def test_normalize_review_cleans_caps_and_forces_issue_verdict():
    findings = [
        {
            "severity": "critical" if i == 0 else "LOW",
            "title": "  Finding " + ("x" * 200),
            "detail": f" detail {i} ",
            "evidence": f" evidence {i} ",
            "artifact_id": " artifact-1 " if i == 0 else "",
        }
        for i in range(10)
    ]
    # Findings with neither detail nor evidence, and non-object entries, disappear.
    findings.insert(1, {"title": "empty", "detail": "", "evidence": ""})
    findings.insert(2, "not a finding")

    result = review_mod.normalize_review(
        {
            "verdict": "pass",
            "summary": "  Material evidence problems  ",
            "issues": findings,
        }
    )

    assert result["verdict"] == "issues"
    assert result["summary"] == "Material evidence problems"
    assert len(result["issues"]) == 8
    assert result["issues"][0] == {
        "severity": "medium",
        "title": ("Finding " + ("x" * 200))[:160],
        "detail": "detail 0",
        "evidence": "evidence 0",
        "artifact_id": "artifact-1",
    }
    assert all(
        issue["severity"] in {"high", "medium", "low"} for issue in result["issues"]
    )


def test_normalize_review_pass_is_canonical_and_invalid_schema_is_unavailable():
    result = review_mod.normalize_review(
        {"verdict": "pass", "summary": "model-specific wording", "issues": []}
    )
    assert result == {"verdict": "pass", "summary": "No issues found", "issues": []}

    for invalid in (
        {},
        {"verdict": "unexpected", "issues": []},
        {"verdict": "issues", "issues": []},
        {"verdict": "issues", "issues": [{"title": "missing evidence"}]},
        {"verdict": "pass", "issues": [{"title": "missing evidence"}]},
    ):
        with pytest.raises(review_mod.ReviewError):
            review_mod.normalize_review(invalid)


def test_bounded_packet_preserves_valid_json_artifacts_tools_and_execution():
    noisy = '\\"' * 100_000
    packet = review_mod._bounded_packet(
        {
            "user_request": noisy,
            "final_answer": noisy,
            "submitted_output": {"answer": noisy},
            "changed_artifacts": [
                {
                    "artifact_id": f"artifact-{i}",
                    "filename": noisy,
                    "content_type": "text/plain",
                    "latest_version_id": f"version-{i}",
                    "exists": True,
                    "excerpt": noisy,
                }
                for i in range(80)
            ],
            "execution": [
                {
                    "cell_index": i,
                    "status": "ok",
                    "source": noisy,
                    "stdout": noisy,
                    "stderr": noisy,
                    "files_written": [noisy],
                    "files_read": [noisy],
                }
                for i in range(24)
            ],
            "tool_evidence": [
                {
                    "kind": "search",
                    "title": f"Search {i}",
                    "status": "done",
                    "input": {"query": noisy},
                    "output": {"results": [{"url": noisy, "snippet": noisy}]},
                }
                for i in range(20)
            ],
        }
    )

    decoded = json.loads(packet)
    assert len(packet) <= 60_000
    assert len(decoded["changed_artifacts"]) == 64
    assert decoded["changed_artifacts"][0]["artifact_id"] == "artifact-0"
    assert decoded["changed_artifact_count"] == 80
    assert decoded["omitted_artifact_count"] == 16
    assert decoded["execution"]
    assert decoded["tool_evidence"]
    assert decoded["host_note"] == "[host truncated the evidence packet]"


def test_review_evidence_refuses_omitted_artifacts_without_calling_model(monkeypatch):
    monkeypatch.setattr(
        review_mod,
        "chat",
        lambda *_args, **_kwargs: pytest.fail(
            "model must not review incomplete evidence"
        ),
    )

    with pytest.raises(review_mod.ReviewError, match="omitted changed artifacts"):
        review_mod.review_evidence(
            {
                "changed_artifacts": [
                    {"artifact_id": f"artifact-{i}"} for i in range(64)
                ],
                "changed_artifact_count": 65,
                "omitted_artifact_count": 1,
            },
            LLMConfig(provider="deepseek", api_key="test-key", model="reviewer"),
        )


def test_review_evidence_is_one_bounded_call_with_usage_and_model(monkeypatch):
    calls: list[tuple] = []

    def fake_chat(messages, cfg, **kwargs):
        calls.append((messages, cfg, kwargs))
        return {
            "content": "```json\n"
            '{"verdict":"issues","summary":"One issue","issues":'
            '[{"severity":"high","title":"Unsupported","detail":"No table",'
            '"evidence":"changed_artifacts is empty"}]}\n```',
            "usage": {"prompt_tokens": 101, "completion_tokens": 29},
        }

    monkeypatch.setattr(review_mod, "chat", fake_chat)
    cfg = LLMConfig(
        provider="gemini",
        api_key="test-key",
        model="gemini-reviewer",
        max_tokens=4096,
    )
    result = review_mod.review_evidence(
        {
            "user_request": "x" * 70_000,
            "changed_artifacts": [
                {"artifact_id": "artifact-1", "filename": "report.md"}
            ],
        },
        cfg,
    )

    assert len(calls) == 1
    messages, used_cfg, kwargs = calls[0]
    assert used_cfg is cfg
    assert messages[0] == {
        "role": "system",
        "content": review_mod.REVIEWER_SYSTEM_PROMPT,
    }
    assert messages[1]["role"] == "user"
    assert messages[1]["content"].startswith("Review this completed research turn:\n{")
    packet = json.loads(messages[1]["content"].split("\n", 1)[1])
    assert packet["host_note"] == "[host truncated the evidence packet]"
    assert packet["changed_artifacts"][0]["artifact_id"] == "artifact-1"
    assert len(messages[1]["content"]) < 61_000
    assert kwargs == {"max_tokens": 1800, "temperature": 0.1}
    assert result["verdict"] == "issues"
    assert result["usage"] == {"input_tokens": 101, "output_tokens": 29}
    assert result["model"] == "gemini-reviewer"


def test_run_reviewer_pass_persists_evidence_step_and_usage(monkeypatch, tmp_path):
    _cfg_obj, hub, runner, store, fid, st = _review_context(tmp_path)
    store.set_setting(f"review:model:{fid}", "reviewer-model")

    old_path = st.workspace / "old.txt"
    old_path.write_text("unchanged evidence", encoding="utf-8")
    old = runner._register_file(st, old_path, "cell-old", lambda _event: None)
    assert old is not None
    store.log_cell(
        frame_id=fid,
        root_frame_id=fid,
        project_id="default",
        cell_index=1,
        code="print('old')",
        result={"stdout": "old\n", "stderr": "", "error": None},
    )

    new_path = st.workspace / "report.md"
    new_path.write_text("# Evidence-backed report", encoding="utf-8")
    new = runner._register_file(st, new_path, "cell-new", lambda _event: None)
    assert new is not None
    store.log_cell(
        frame_id=fid,
        root_frame_id=fid,
        project_id="default",
        cell_index=2,
        code="Path('report.md').write_text(report)",
        result={"stdout": "wrote report.md\n", "stderr": "", "error": None},
        files_read=["source.csv"],
        files_written=["report.md"],
    )
    store.add_step(
        step_id="search-step",
        frame_id=fid,
        kind="search",
        title="Search primary sources",
        input={"query": "NIF3 evidence"},
        status="running",
    )
    store.update_step(
        "search-step",
        status="done",
        output={
            "results": [
                {"url": "https://example.test/paper", "snippet": "primary evidence"}
            ]
        },
        summary="1 source",
    )

    captured: dict = {}

    def fake_review_evidence(evidence, cfg):
        captured["evidence"] = evidence
        captured["cfg"] = cfg
        return {
            "verdict": "pass",
            "summary": "No issues found",
            "issues": [],
            "usage": {"input_tokens": 17, "output_tokens": 4},
            "model": cfg.model,
        }

    monkeypatch.setattr(gateway_mod, "review_evidence", fake_review_evidence)
    result = runner._run_reviewer(
        st,
        hub.emitter(fid),
        user_text="Create and verify a report",
        assistant_text="The report was created and verified.",
        artifact_versions_before={old["artifact_id"]: old["version_id"]},
        cell_count_before=1,
        mode="auto",
    )

    assert result is not None and result["verdict"] == "pass"
    assert result["reviewed_artifacts"] == [new["artifact_id"]]
    assert captured["cfg"].model == "reviewer-model"
    evidence = captured["evidence"]
    assert evidence["user_request"] == "Create and verify a report"
    assert evidence["final_answer"] == "The report was created and verified."
    assert evidence["submitted_output"] == {"answer": "submitted result"}
    assert [item["artifact_id"] for item in evidence["changed_artifacts"]] == [
        new["artifact_id"]
    ]
    assert evidence["changed_artifacts"][0]["exists"] is True
    assert evidence["changed_artifacts"][0]["excerpt"] == "# Evidence-backed report"
    assert evidence["changed_artifact_count"] == 1
    assert evidence["omitted_artifact_count"] == 0
    assert [cell["cell_index"] for cell in evidence["execution"]] == [2]
    assert evidence["execution"][0]["files_written"] == ["report.md"]
    assert evidence["tool_evidence"] == [
        {
            "kind": "search",
            "title": "Search primary sources",
            "status": "done",
            "summary": "1 source",
            "input": {"query": "NIF3 evidence"},
            "output": {
                "results": [
                    {
                        "url": "https://example.test/paper",
                        "snippet": "primary evidence",
                    }
                ]
            },
        }
    ]

    step = next(step for step in store.list_steps(fid) if step["kind"] == "review")
    assert step["kind"] == "review"
    assert step["input"] == {"mode": "auto", "model": "reviewer-model"}
    assert step["status"] == "done"
    assert step["summary"] == "No issues found"
    assert step["output"]["reviewed_artifacts"] == [new["artifact_id"]]
    frame = store.get_frame(fid)
    assert frame["input_tokens"] == 17
    assert frame["output_tokens"] == 4
    assert [
        event["status"] for event in hub.events if event["type"].startswith("step")
    ] == [
        "running",
        "done",
    ]


def test_run_reviewer_issues_are_persisted_and_streamed(monkeypatch, tmp_path):
    _cfg_obj, hub, runner, store, fid, st = _review_context(tmp_path)
    finding = {
        "severity": "high",
        "title": "Unsupported numeric claim",
        "detail": "The final answer reports 42%, but no execution output supports it.",
        "evidence": "execution is empty",
    }
    monkeypatch.setattr(
        gateway_mod,
        "review_evidence",
        lambda evidence, cfg: {
            "verdict": "issues",
            "summary": "1 issue found",
            "issues": [finding],
            "usage": {"input_tokens": 7, "output_tokens": 9},
            "model": cfg.model,
        },
    )

    result = runner._run_reviewer(
        st,
        hub.emitter(fid),
        user_text="Estimate the effect",
        assistant_text="The effect is 42%.",
        artifact_versions_before={},
        cell_count_before=0,
    )

    assert result is not None
    assert result["verdict"] == "issues"
    assert result["issues"] == [finding]
    step = store.list_steps(fid)[0]
    assert step["status"] == "done"
    assert step["summary"] == "1 issue found"
    assert step["output"]["issues"] == [finding]
    update = [event for event in hub.events if event["type"] == "step_update"][-1]
    assert update["status"] == "done"
    assert update["output"]["verdict"] == "issues"


def test_run_reviewer_error_is_nonfatal_and_marks_step_unavailable(
    monkeypatch, tmp_path
):
    _cfg_obj, hub, runner, store, fid, st = _review_context(tmp_path)

    def unavailable(_evidence, _cfg):
        raise RuntimeError("review provider unavailable")

    monkeypatch.setattr(gateway_mod, "review_evidence", unavailable)
    result = runner._run_reviewer(
        st,
        hub.emitter(fid),
        user_text="request",
        assistant_text="answer",
        artifact_versions_before={},
        cell_count_before=0,
    )

    assert result is None
    step = store.list_steps(fid)[0]
    assert step["status"] == "error"
    assert step["summary"] == "Review unavailable"
    assert step["output"] == {
        "error": "review provider unavailable",
        "verdict": "unavailable",
    }
    frame = store.get_frame(fid)
    assert (frame.get("input_tokens") or 0) == 0
    assert (frame.get("output_tokens") or 0) == 0
    update = [event for event in hub.events if event["type"] == "step_update"][-1]
    assert update["status"] == "error"
    assert update["summary"] == "Review unavailable"


def test_reviewer_keeps_metadata_beyond_twelve_changed_artifacts(monkeypatch, tmp_path):
    _cfg_obj, hub, runner, _store, fid, st = _review_context(tmp_path)
    for index in range(13):
        path = st.workspace / f"artifact-{index}.txt"
        path.write_text(f"evidence {index}", encoding="utf-8")
        assert runner._register_file(st, path, f"cell-{index}", lambda _event: None)

    captured: dict = {}

    def fake_review(evidence, cfg):
        captured.update(evidence)
        return {
            "verdict": "pass",
            "summary": "No issues found",
            "issues": [],
            "usage": {},
            "model": cfg.model,
        }

    monkeypatch.setattr(gateway_mod, "review_evidence", fake_review)
    result = runner._run_reviewer(
        st,
        hub.emitter(fid),
        user_text="verify all outputs",
        assistant_text="created 13 outputs",
        artifact_versions_before={},
        cell_count_before=0,
    )

    assert result and result["verdict"] == "pass"
    assert captured["changed_artifact_count"] == 13
    assert captured["omitted_artifact_count"] == 0
    assert len(captured["changed_artifacts"]) == 13
    assert sum("excerpt" in item for item in captured["changed_artifacts"]) == 12


def test_manual_review_reserves_operation_and_honors_pre_state_cancel(
    monkeypatch, tmp_path
):
    _cfg_obj, _hub, runner, store, fid, _st = _review_context(tmp_path)
    # Remove the helper-created state so cancel() must rely on the synchronous
    # operation reservation rather than an already-existing SessionState.
    runner._sessions.pop(fid, None)
    original_state = runner._state
    state_entered = threading.Event()
    allow_state = threading.Event()

    def delayed_state(root_frame_id, project_id):
        state_entered.set()
        allow_state.wait(2)
        return original_state(root_frame_id, project_id)

    monkeypatch.setattr(runner, "_state", delayed_state)
    provider_calls: list[int] = []
    monkeypatch.setattr(
        gateway_mod,
        "review_evidence",
        lambda *_args, **_kwargs: provider_calls.append(1),
    )

    job = runner.submit_review(fid, "default")
    assert state_entered.wait(1)
    assert runner.review_call_inflight(fid) is True
    with pytest.raises(gateway_mod.GatewayError) as busy:
        runner.submit_review(fid, "default")
    assert busy.value.code == 409

    runner.cancel_review(fid)
    allow_state.set()
    result = job.wait_result()

    assert result["status"] == "cancelled"
    assert provider_calls == []
    review_step = next(
        step for step in store.list_steps(fid) if step["kind"] == "review"
    )
    assert review_step["status"] == "cancelled"
    assert review_step["output"] == {
        "verdict": "cancelled",
        "provider_call": "not_started",
    }
    assert runner.review_call_inflight(fid) is False


def test_run_reviewer_cancel_stops_waiting_and_marks_step_cancelled(
    monkeypatch, tmp_path
):
    _cfg_obj, hub, runner, store, fid, st = _review_context(tmp_path)
    started = threading.Event()
    release = threading.Event()
    calls: list[int] = []

    def slow_review(_evidence, _cfg):
        calls.append(1)
        started.set()
        release.wait(2)
        return {
            "verdict": "pass",
            "summary": "No issues found",
            "issues": [],
            "usage": {},
        }

    monkeypatch.setattr(gateway_mod, "review_evidence", slow_review)
    result_box: dict = {}
    outer = threading.Thread(
        target=lambda: result_box.setdefault(
            "result",
            runner._run_reviewer(
                st,
                hub.emitter(fid),
                user_text="request",
                assistant_text="answer",
                artifact_versions_before={},
                cell_count_before=0,
            ),
        )
    )
    outer.start()
    assert started.wait(1)
    st.cancel.set()
    outer.join(1)

    assert not outer.is_alive()
    assert result_box["result"] is None
    step = store.list_steps(fid)[0]
    assert step["status"] == "cancelled"
    assert step["summary"] == "Review cancelled · provider request finishing"
    assert step["output"] == {
        "verdict": "cancelled",
        "provider_call": "finishing",
    }
    update = [event for event in hub.events if event["type"] == "step_update"][-1]
    assert update["status"] == "cancelled"
    assert runner.review_call_inflight(fid) is True

    # A second click cannot stack another billable provider call while the first
    # uncancellable HTTP request is winding down.
    assert (
        runner._run_reviewer(
            st,
            hub.emitter(fid),
            user_text="request again",
            assistant_text="answer again",
            artifact_versions_before={},
            cell_count_before=0,
        )
        is None
    )
    assert calls == [1]
    assert store.list_steps(fid)[1]["status"] == "cancelled"
    assert store.list_steps(fid)[1]["output"]["provider_call"] == "not_started"

    release.set()
    for _ in range(50):
        if not runner.review_call_inflight(fid):
            break
        threading.Event().wait(0.01)
    assert runner.review_call_inflight(fid) is False
    final_step = store.list_steps(fid)[0]
    assert final_step["summary"] == "Review cancelled"
    assert final_step["output"]["provider_call"] == "finished"


def test_submit_manual_review_preserves_frame_status_and_joins_assistant_blocks(
    monkeypatch, tmp_path
):
    _cfg_obj, hub, runner, store, fid, _st = _review_context(tmp_path)
    store.update_frame(fid, status="failed")
    for role, content in (
        ("user", "older request"),
        ("assistant", "older answer"),
        ("user", "latest request"),
        ("assistant", "analysis block"),
        ("assistant", "final answer block"),
    ):
        store.add_message(root_frame_id=fid, role=role, content=content)

    captured: dict = {}

    def fake_reviewer(_st, _emit, **kwargs):
        captured.update(kwargs)
        return {"verdict": "pass"}

    monkeypatch.setattr(runner, "_run_reviewer", fake_reviewer)
    job = runner.submit_review(fid, "default")
    result = job.wait_result()

    assert result["status"] == "completed"
    assert captured["mode"] == "manual"
    assert captured["user_text"] == "latest request"
    assert captured["assistant_text"] == "analysis block\n\nfinal answer block"
    assert store.get_frame(fid)["status"] == "failed"
    statuses = [
        event["status"] for event in hub.events if event.get("type") == "frame_update"
    ]
    assert statuses == ["processing", "failed"]


def test_submit_manual_review_repairs_stale_processing_status(monkeypatch, tmp_path):
    _cfg_obj, hub, runner, store, fid, _st = _review_context(tmp_path)
    store.update_frame(fid, status="processing")
    monkeypatch.setattr(
        runner, "_run_reviewer", lambda *_args, **_kwargs: {"verdict": "pass"}
    )

    result = runner.submit_review(fid, "default").wait_result()

    assert result["status"] == "completed"
    assert store.get_frame(fid)["status"] == "ready"
    statuses = [
        event["status"] for event in hub.events if event.get("type") == "frame_update"
    ]
    assert statuses == ["processing", "ready"]


def test_submit_manual_review_reads_tail_of_long_conversation(monkeypatch, tmp_path):
    _cfg_obj, _hub, runner, store, fid, _st = _review_context(tmp_path)
    list_call: dict = {}

    def tail_messages(_fid, *, branch_id, limit):
        list_call.update(branch_id=branch_id, limit=limit)
        return [
            *(
                {"role": "assistant", "content": f"old {index}"}
                for index in range(1_003)
            ),
            {"role": "user", "content": "latest request"},
            {"role": "assistant", "content": "latest answer"},
        ]

    monkeypatch.setattr(store, "list_branch_messages", tail_messages)
    captured: dict = {}
    monkeypatch.setattr(
        runner,
        "_run_reviewer",
        lambda _st, _emit, **kwargs: captured.update(kwargs) or {"verdict": "pass"},
    )

    runner.submit_review(fid, "default").wait_result()

    assert list_call == {"branch_id": fid, "limit": None}
    assert captured["user_text"] == "latest request"
    assert captured["assistant_text"] == "latest answer"


def test_ws_resume_buffer_reopens_for_manual_review_and_closes_on_ready():
    hub = gateway_mod.WSHub()
    fid = "frame-review"
    hub._record(fid, {"type": "text_reset"})
    hub._record(fid, {"type": "frame_update", "status": "completed"})
    assert hub.is_running(fid) is False

    processing = {"type": "frame_update", "status": "processing"}
    review_step = {"type": "step", "kind": "review", "status": "running"}
    hub._record(fid, processing)
    hub._record(fid, review_step)

    assert hub.is_running(fid) is True
    assert hub._live[fid]["events"] == [processing, review_step]

    ready = {"type": "frame_update", "status": "ready"}
    hub._record(fid, ready)
    assert hub.is_running(fid) is False
    assert hub._live[fid]["events"][-1] == ready


def test_review_model_agent_sentinel_ignores_global_override(tmp_path):
    _cfg_obj, _hub, runner, store, fid, st = _review_context(tmp_path)
    store.set_setting("reviewer_model", "global-reviewer")
    store.set_setting(f"review:model:{fid}", "__agent__")

    cfg = runner._review_llm_cfg(st)

    assert cfg.provider == "deepseek"
    assert cfg.model == "worker-model"
    assert cfg.timeout_s <= 45

    store.set_model_profiles(
        [
            {
                "id": "review-profile",
                "provider": "gemini",
                "model": "gemini-2.5-pro",
                "base_url": "https://review.example",
                "api_key": "sk-review",
            }
        ]
    )
    store.set_setting(f"review:model:{fid}", "gemini-2.5-pro")

    cross_provider = runner._review_llm_cfg(st)

    assert cross_provider.provider == "gemini"
    assert cross_provider.model == "gemini-2.5-pro"
    assert cross_provider.base_url == "https://review.example"
    assert cross_provider.api_key == "sk-review"


@pytest.mark.parametrize(
    ("enabled", "plan", "expected_calls"),
    [(True, False, 1), (False, False, 0), (True, True, 0)],
)
def test_run_message_auto_review_hook_runs_only_for_completed_non_plan_turns(
    monkeypatch, tmp_path, enabled, plan, expected_calls
):
    _cfg_obj, hub, runner, store, fid, _st = _review_context(tmp_path)
    store.update_frame(fid, name="Existing session")
    store.set_setting(f"review:auto:{fid}", "1" if enabled else "0")

    def ensure_kernel(st):
        st.dispatcher = SimpleNamespace(last_output={"ok": True})

    monkeypatch.setattr(runner, "_ensure_kernel", ensure_kernel)
    monkeypatch.setattr(runner, "_wire_delegation", lambda _st: None)

    def finish_loop(_st, _emit, visible):
        visible.append({"at": 123, "text": "Evidence-backed final answer"})
        return "submitted"

    monkeypatch.setattr(runner, "_loop", finish_loop)
    calls: list[dict] = []

    def fake_reviewer(_st, emit, **kwargs):
        calls.append(kwargs)
        emit({"type": "step", "kind": "review", "status": "running"})
        return {"verdict": "pass"}

    monkeypatch.setattr(runner, "_run_reviewer", fake_reviewer)
    result = runner.run_message(fid, "default", "Do the research", plan=plan)

    assert result["status"] == "completed"
    assert len(calls) == expected_calls
    if expected_calls:
        assert calls[0]["user_text"] == "Do the research"
        assert calls[0]["assistant_text"] == "Evidence-backed final answer"
        review_index = next(
            i for i, event in enumerate(hub.events) if event.get("kind") == "review"
        )
        completed_index = next(
            i
            for i, event in enumerate(hub.events)
            if event.get("status") == "completed"
        )
        assert review_index < completed_index


def test_review_settings_route_inherits_then_persists_frame_overrides(tmp_path):
    cfg, _hub, runner, store, fid, _st = _review_context(tmp_path)
    store.set_setting("auto_review_enabled", "1")
    store.set_setting("reviewer_model", "global-reviewer")
    handler = object.__new__(gateway_mod.make_handler(cfg, _Hub(), runner))
    replies: list[tuple[int, dict]] = []
    handler._query = lambda: {}
    handler._body = lambda: {}
    handler._json = lambda obj, code=200: replies.append((code, obj))

    handler._api("GET", f"/frames/{fid}/review-settings")
    assert replies[-1] == (
        200,
        {
            "auto_review": True,
            "reviewer_model": "global-reviewer",
            "delegation_enabled": True,
            "inherits_auto_review": True,
        },
    )

    handler._body = lambda: {
        "auto_review": False,
        "reviewer_model": "  frame-reviewer  ",
        "delegation_enabled": False,
    }
    handler._api("PATCH", f"/frames/{fid}/review-settings")
    assert replies[-1] == (
        200,
        {
            "auto_review": False,
            "reviewer_model": "frame-reviewer",
            "delegation_enabled": False,
            "inherits_auto_review": False,
        },
    )
    assert store.get_setting(f"review:auto:{fid}") == "0"
    assert store.get_setting(f"review:model:{fid}") == "frame-reviewer"
    assert store.get_setting(f"delegation:{fid}") == "0"

    handler._body = lambda: {"reviewer_model": ""}
    handler._api("PATCH", f"/frames/{fid}/review-settings")
    assert replies[-1][1]["reviewer_model"] == ""
    assert store.get_setting(f"review:model:{fid}") == "__agent__"

    handler._api("GET", "/frames/no-such-frame/review-settings")
    assert replies[-1] == (404, {"error": "frame not found"})


def test_manual_review_route_rejects_duplicate_provider_call(monkeypatch, tmp_path):
    cfg, _hub, runner, _store, fid, _st = _review_context(tmp_path)
    handler = object.__new__(gateway_mod.make_handler(cfg, _Hub(), runner))
    replies: list[tuple[int, dict]] = []
    handler._query = lambda: {}
    handler._body = lambda: {}
    handler._json = lambda obj, code=200: replies.append((code, obj))
    monkeypatch.setattr(runner, "review_call_inflight", lambda _fid: True)

    handler._api("POST", f"/frames/{fid}/review")

    assert replies[-1] == (
        409,
        {"error": "a previous review call is still finishing"},
    )


def test_project_artifact_zip_contains_current_files_and_deduplicates_names(tmp_path):
    cfg = _cfg(tmp_path)
    hub = _Hub()
    runner = gateway_mod.SessionRunner(cfg, hub)
    store = get_store(cfg.db_path)
    for content in ("first report", "second report"):
        fid = store.new_frame(kind="turn", project_id="default", status="ready")
        st = gateway_mod.SessionState(fid, "default", runner.workspace_for(fid))
        nested = st.workspace / "nested"
        nested.mkdir()
        path = nested / "report.txt"
        path.write_text(content, encoding="utf-8")
        assert runner._register_file(st, path, "cell", lambda _event: None)

    handler = object.__new__(gateway_mod.make_handler(cfg, hub, runner))
    sends: list[tuple] = []
    handler._query = lambda: {}
    handler._body = lambda: {}
    handler._stream_file = lambda path, ctype, extra=None: sends.append(
        (200, path.read_bytes(), ctype, extra or {})
    )

    handler._api("GET", "/projects/default/artifacts.zip")

    code, body, ctype, headers = sends[-1]
    assert code == 200
    assert ctype == "application/zip"
    assert headers["Content-Disposition"] == (
        'attachment; filename="project-default-artifacts.zip"'
    )
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        assert set(archive.namelist()) == {
            "nested/report.txt",
            "nested/report-2.txt",
        }
        assert {archive.read(name).decode() for name in archive.namelist()} == {
            "first report",
            "second report",
        }
