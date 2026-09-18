"""Structured plan → review → auto-execute (plan mode).

Covers the plan-JSON parsing helpers, the store `plans` CRUD + cascade, the
plan-mode turn that emits `plan_ready`, approve→auto-execute→completed, the
`host.plan_update` step-ticking path, and discard.
"""

from types import SimpleNamespace

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.store import get_store


class _Hub:
    def __init__(self):
        self.events = []

    def emitter(self, root_frame_id):
        def emit(event):
            event.setdefault("root_frame_id", root_frame_id)
            self.events.append(event)

        return emit

    def broadcast(self, root_frame_id, event):
        event.setdefault("root_frame_id", root_frame_id)
        self.events.append(event)


def _cfg(tmp_path):
    return Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=3,
    )


def _fake_ensure(st):
    st.dispatcher = SimpleNamespace(last_output=None)
    st.messages = [{"role": "system", "content": "sys"}]
    st.booted = True


# ------------------------------- pure helpers ------------------------------ #
def test_extract_plan_json_from_fence():
    reply = (
        "Here is my plan.\n\n```json\n"
        '{"title":"T","rationale":"why","confidence":"high",'
        '"steps":[{"id":"s1","title":"A","detail":"do a",'
        '"deliverables":["a.csv"]}]}\n```'
    )
    raw = gateway_mod._extract_plan_json(reply)
    assert raw and raw["title"] == "T"
    plan = gateway_mod._normalize_plan(raw)
    assert plan["confidence"] == "high"
    assert plan["steps"][0]["deliverables"] == ["a.csv"]


def test_extract_plan_bare_object_and_numeric_confidence():
    # no fence, numeric confidence, string deliverable, missing ids
    raw = {
        "title": "T",
        "confidence": 0.9,
        "steps": [{"title": "x", "deliverables": "one.csv"}],
    }
    plan = gateway_mod._normalize_plan(raw)
    assert plan["confidence"] == "high"  # 0.9 → high
    assert plan["steps"][0]["id"] == "s1"  # auto-assigned
    assert plan["steps"][0]["deliverables"] == ["one.csv"]  # coerced to list


def test_normalize_plan_prose_fallback():
    prose = "1. First step — do A\n2. Second step — do B\n"
    plan = gateway_mod._normalize_plan(None, prose=prose, task_hint="my task")
    assert len(plan["steps"]) == 2
    assert plan["steps"][0]["title"] == "First step"
    assert plan["steps"][0]["detail"] == "do A"
    assert plan["title"].startswith("my task")


def test_plan_public_merges_step_status():
    plan = {
        "plan_id": "p1",
        "title": "t",
        "steps": [{"id": "s1", "title": "a"}, {"id": "s2", "title": "b"}],
        "step_status": {"s1": {"status": "completed"}},
        "status": "executing",
    }
    pub = gateway_mod._plan_public(plan)
    assert pub["steps"][0]["status"] == "completed"
    assert pub["steps"][1]["status"] == "pending"


# ----------------------------- store CRUD + cascade ------------------------ #
def test_store_plan_crud_and_cascade(tmp_path):
    store = get_store(_cfg(tmp_path).db_path)
    fid = store.new_frame(kind="turn", project_id="default", status="ready")
    p = store.create_plan(
        frame_id=fid,
        title="T",
        rationale="r",
        confidence="high",
        steps=[{"id": "s1", "title": "A", "detail": "d", "deliverables": ["a.csv"]}],
    )
    assert p["status"] == "draft"
    assert store.get_plan_by_frame(fid)["plan_id"] == p["plan_id"]
    merged = store.set_plan_step_status(p["plan_id"], "s1", "completed")
    assert merged["step_status"]["s1"]["status"] == "completed"
    store.update_plan(p["plan_id"], status="completed")
    assert store.get_plan(p["plan_id"])["status"] == "completed"
    store.delete_frame(fid)
    assert store.get_plan_by_frame(fid) is None


def test_get_plan_by_frame_prefers_non_discarded(tmp_path):
    store = get_store(_cfg(tmp_path).db_path)
    fid = store.new_frame(kind="turn", project_id="default", status="ready")
    p1 = store.create_plan(
        frame_id=fid,
        title="one",
        rationale="",
        confidence="low",
        steps=[{"id": "s1", "title": "a"}],
    )
    store.update_plan(p1["plan_id"], status="discarded")
    p2 = store.create_plan(
        frame_id=fid,
        title="two",
        rationale="",
        confidence="high",
        steps=[{"id": "s1", "title": "b"}],
    )
    assert store.get_plan_by_frame(fid)["plan_id"] == p2["plan_id"]


# --------------------- integration: plan-mode turn ------------------------- #
def test_plan_mode_turn_emits_plan_ready(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    hub = _Hub()
    runner = gateway_mod.SessionRunner(cfg, hub)
    store = get_store(cfg.db_path)
    fid = store.new_frame(kind="turn", project_id="default", status="ready")
    reply = (
        "I will build a DUF89 tree.\n\n```json\n"
        '{"title":"DUF89 phylogenomics","rationale":"good target",'
        '"confidence":"high","steps":['
        '{"id":"s1","title":"Pick target","detail":"choose DUF",'
        '"deliverables":["duf_candidates.csv"]},'
        '{"id":"s2","title":"Build tree","detail":"iqtree",'
        '"deliverables":["duf89.treefile"]}]}\n```'
    )

    def fake_chat(messages, cfg, on_delta=None, **kw):
        if on_delta:
            on_delta("I will build a DUF89 tree.")
        return {"content": reply, "usage": {}}

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_ensure_kernel", _fake_ensure)

    res = runner.run_message(
        fid, "default", "Give me proteins of unknown function...", plan=True
    )
    assert res["status"] == "completed"
    plan = store.get_plan_by_frame(fid)
    assert plan and plan["status"] == "draft"
    assert [s["title"] for s in plan["steps"]] == ["Pick target", "Build tree"]
    ready = [e for e in hub.events if e["type"] == "plan_ready"]
    assert ready and ready[-1]["status"] == "draft"
    assert len(ready[-1]["plan"]["steps"]) == 2
    # the plan was captured as a plan_*.json artifact (shows up in Files)
    assert any(a["filename"].startswith("plan_") for a in store.list_artifacts())
    # plan mode never executes code
    assert store.cell_count(fid) == 0
    # ...and a captured plan says so on the turn result, with no miss event.
    assert res["plan_captured"] is True
    assert not [e for e in hub.events if e["type"] == "plan_not_captured"]


# ------------- plan mode is instructed by the server, not the client ------- #
def _agent_calls(calls):
    """Model inputs of the agent turn, without the background title call."""
    return [
        messages
        for messages in calls
        if not any("Output the title only" in str(m.get("content")) for m in messages)
    ]


def _last_user_content(messages) -> str:
    user = [m for m in messages if m.get("role") == "user"]
    content = user[-1]["content"] if user else ""
    return content if isinstance(content, str) else str(content)


def _plan_turn(monkeypatch, tmp_path, request, reply):
    cfg = _cfg(tmp_path)
    hub = _Hub()
    runner = gateway_mod.SessionRunner(cfg, hub)
    store = get_store(cfg.db_path)
    fid = store.new_frame(kind="turn", project_id="default", status="ready")
    calls = []

    def fake_chat(messages, cfg, on_delta=None, **kw):
        calls.append([dict(m) for m in messages])
        if on_delta:
            on_delta(reply)
        return {"content": reply, "usage": {}}

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_ensure_kernel", _fake_ensure)
    res = runner.run_message(fid, "default", request, plan=True)
    return res, hub, store, fid, _agent_calls(calls)


_PROSE_PLAN_WITHOUT_STEPS = (
    "Here's a concise 3-step plan for the analysis:\n\n"
    "**Step 1 — Generate the dataset**\nCreate 100 samples.\n\n"
    "**Step 2 — Compute statistics**\nMean and SD.\n\n"
    "Would you like me to execute this plan now?"
)


def test_plan_mode_instructs_model_server_side(monkeypatch, tmp_path):
    """`plan:true` from a REST client carries no workbench prefix. The server
    used to withhold tools and nothing else, so the model never saw the plan
    format it had to produce, and whether a plan was captured depended on how
    it happened to format prose."""
    request = "Plan a small analysis. Keep the plan to at most 3 steps."
    res, _hub, store, fid, agent_calls = _plan_turn(
        monkeypatch, tmp_path, request, _PROSE_PLAN_WITHOUT_STEPS
    )
    assert res["status"] == "completed"
    assert agent_calls, "the agent turn never called the model"
    model_input = _last_user_content(agent_calls[0])
    assert model_input.startswith(request)
    assert "[Plan Mode]" in model_input
    assert "```json" in model_input and '"steps"' in model_input
    assert "Wait for user approval" in model_input
    # The durable user row is what the user typed, not the instruction.
    stored = [m for m in store.list_messages(fid) if m["role"] == "user"]
    assert [m["content"] for m in stored] == [request]


def test_plan_mode_instruction_follows_request_language(monkeypatch, tmp_path):
    request = "为两组合成数据的比较制定一个计划，最多三步。"
    _res, _hub, _store, _fid, agent_calls = _plan_turn(
        monkeypatch, tmp_path, request, _PROSE_PLAN_WITHOUT_STEPS
    )
    model_input = _last_user_content(agent_calls[0])
    assert "[计划模式]" in model_input and "[Plan Mode]" not in model_input
    assert "```json" in model_input and "等待用户批准" in model_input


def test_plan_mode_does_not_double_the_workbench_prompt(monkeypatch, tmp_path):
    """The workbench (and the legacy app.js) already prepend a localised plan
    prompt; the server must not stack a second one on top of it."""
    for prefixed in (
        "[Plan Mode] Do not execute or call any tools yet. ...\n\nTask: compare groups",
        "[计划模式] 请先不要执行、不要调用任何工具。...\n\n任务：比较两组",
    ):
        _res, _hub, _store, _fid, agent_calls = _plan_turn(
            monkeypatch, tmp_path / str(len(prefixed)), prefixed, "ok"
        )
        model_input = _last_user_content(agent_calls[0])
        assert model_input.count("[Plan Mode]") + model_input.count("[计划模式]") == 1
        assert "```json" not in model_input


#: The workbench's `planModePayload` as `frontend/src/i18n/{en,zh}.ts` spell it
#: (intro + part1 + part2 + jsonSchema + part3), without the task.
_WORKBENCH_PLAN_PROMPTS = {
    "en": (
        "[Plan Mode] Do not execute or call any tools yet. Devise a structured "
        "execution plan for the task below, and output only two parts:\n"
        "1) A brief description of the approach (prose, explaining your chosen "
        "goal/approach and the main analytical thread);\n"
        "2) Immediately followed by a ```json code block, strictly using the "
        "following structure:\n"
        '{"title":"Plan title","rationale":"One-sentence rationale",'
        '"confidence":"high|medium|low","steps":[{"id":"s1","title":"Step title",'
        '"detail":"What this step does","deliverables":["intermediate-table.csv",'
        '"figure.png"]}]}\n'
        "Each step must have a unique id, a clear title, a brief description, and "
        "a list of expected output filenames for that step. Wait for user approval "
        "before executing.\n\nTask: "
    ),
    "zh": (
        "[计划模式] 请先不要执行、不要调用任何工具。为下面的任务制定一个结构化执行计划，"
        "并只输出两部分：\n1) 一段简短的方案说明；\n2) 紧接着一个 ```json 代码块。"
        "等待用户批准后再执行。\n\n任务："
    ),
}


def test_a_workbench_plan_prompt_does_not_title_the_session(monkeypatch, tmp_path):
    """UI5-F2. The workbench sends its plan-mode prompt in front of the task, and
    the title placeholder was the first 80 characters of that request -- "[Plan
    Mode] Do not execute or call any tools yet. Devise a structured execution "
    -- while the summarizer, handed the same prompt, followed its instructions
    and ran out of tokens, so the placeholder became the permanent title.

    The title is the user's task. What the model receives and the stored row are
    unchanged."""
    for lang, task in (
        ("en", "Compute the mean of the integers 1 through 20"),
        ("zh", "计算 1 到 20 的平均值"),
    ):
        request = _WORKBENCH_PLAN_PROMPTS[lang] + task
        cfg = _cfg(tmp_path / lang)
        runner = gateway_mod.SessionRunner(cfg, _Hub())
        store = get_store(cfg.db_path)
        fid = store.new_frame(kind="turn", project_id="default", status="ready")
        calls = []
        titled = []

        def fake_chat(messages, cfg, on_delta=None, **kw):
            calls.append([dict(m) for m in messages])
            return {"content": "ok", "usage": {}}

        monkeypatch.setattr(gateway_mod, "chat", fake_chat)
        monkeypatch.setattr(runner, "_ensure_kernel", _fake_ensure)
        monkeypatch.setattr(
            runner,
            "_spawn_title_summary",
            lambda root, text, llm_cfg, placeholder: titled.append((text, placeholder)),
        )
        runner.run_message(fid, "default", request, plan=True)

        summary = store.get_frame(fid)["task_summary"]
        assert summary == task, summary
        assert titled == [(task, task)]
        # Model input and the durable row are exactly what they were.
        agent = _agent_calls(calls)
        assert agent and _last_user_content(agent[0]).startswith(request)
        stored = [m for m in store.list_messages(fid) if m["role"] == "user"]
        assert [m["content"] for m in stored] == [request]


def test_plan_mode_request_text_leaves_other_text_alone():
    from openai4s.server.plans import plan_mode_request_text

    assert plan_mode_request_text("Plan a small analysis.") == "Plan a small analysis."
    # A delimiter without the marker is the user's own text.
    assert plan_mode_request_text("Notes\n\nTask: x") == "Notes\n\nTask: x"
    # The marker with no delimiter has no separable task: nothing is cut.
    assert plan_mode_request_text("[Plan Mode] hello") == "[Plan Mode] hello"
    # Only the scaffold's own delimiter is cut; the task keeps a later one.
    assert (
        plan_mode_request_text("[Plan Mode] x\n\nTask: a\n\nTask: b") == "a\n\nTask: b"
    )
    assert (
        plan_mode_request_text("[Plan Mode] Revise ...\n\nChange requests: drop s2")
        == "drop s2"
    )


def test_plan_mode_uncaptured_plan_is_not_silent(monkeypatch, tmp_path):
    """A plan turn whose reply yields no steps used to end `completed`, with
    no error, no plan row and no `plan_ready` -- an API client had nothing to
    approve and nothing telling it so."""
    res, hub, store, fid, _calls = _plan_turn(
        monkeypatch,
        tmp_path,
        "Plan a small analysis.",
        _PROSE_PLAN_WITHOUT_STEPS,
    )
    assert store.get_plan_by_frame(fid) is None
    assert res["status"] == "completed"
    assert res["plan_captured"] is False
    types = [e["type"] for e in hub.events]
    assert "plan_ready" not in types
    missed = [e for e in hub.events if e["type"] == "plan_not_captured"]
    assert len(missed) == 1
    assert missed[0]["frame_id"] == fid
    assert missed[0]["request_id"] == res["request_id"]
    assert missed[0]["reason"] == "no_plan_steps"
    # Announced before the terminal event, so a client reading the stream in
    # order learns it while the turn is still the current one.
    assert types.index("plan_not_captured") < len(types) - 1
    assert types[-1] == "frame_update"


def test_a_normal_turn_result_carries_no_plan_field(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    hub = _Hub()
    runner = gateway_mod.SessionRunner(cfg, hub)
    store = get_store(cfg.db_path)
    fid = store.new_frame(kind="turn", project_id="default", status="ready")

    def fake_chat(messages, cfg, on_delta=None, **kw):
        return {"content": "Just an answer.", "usage": {}}

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_ensure_kernel", _fake_ensure)
    res = runner.run_message(fid, "default", "hello")
    assert "plan_captured" not in res
    assert not [e for e in hub.events if e["type"] == "plan_not_captured"]


# --------------- integration: approve → auto-execute → completed ----------- #
def test_approve_runs_execution_and_marks_completed(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    hub = _Hub()
    runner = gateway_mod.SessionRunner(cfg, hub)
    store = get_store(cfg.db_path)
    fid = store.new_frame(kind="turn", project_id="default", status="ready")
    store.create_plan(
        frame_id=fid,
        title="T",
        rationale="r",
        confidence="high",
        steps=[{"id": "s1", "title": "A", "detail": "d", "deliverables": []}],
    )

    def fake_chat(messages, cfg, on_delta=None, **kw):
        return {
            "content": "```python\nhost.submit_output({'ok': True}, ['done'])\n```",
            "usage": {},
        }

    def fake_exec(st, code, origin, emit, stream=True, language="python"):
        st.dispatcher.last_output = {"output": {"ok": True}}
        return {"result": {"stdout": "", "stderr": "", "error": None}}

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_ensure_kernel", _fake_ensure)
    monkeypatch.setattr(runner, "_execute_and_log", fake_exec)

    res = runner.run_plan_execution(fid, "default")
    assert res["status"] == "completed"
    assert store.get_plan_by_frame(fid)["status"] == "completed"
    statuses = [e["status"] for e in hub.events if e["type"] == "plan_ready"]
    assert "executing" in statuses and "completed" in statuses


def test_an_english_plan_is_drafted_and_executed_in_english(monkeypatch, tmp_path):
    """Through the real turn path: a REST `plan:true` draft in English, then
    approval. The execution seed used to be hard-coded Chinese, and the turn
    takes its completion headings and narrations from the seed's language."""
    cfg = _cfg(tmp_path)
    hub = _Hub()
    runner = gateway_mod.SessionRunner(cfg, hub)
    store = get_store(cfg.db_path)
    fid = store.new_frame(kind="turn", project_id="default", status="ready")
    draft = (
        "Compare two synthetic groups.\n\n```json\n"
        '{"title":"Two-group comparison","rationale":"simple","confidence":"high",'
        '"steps":[{"id":"s1","title":"Generate data","detail":"40 values",'
        '"deliverables":["means.json"]}]}\n```'
    )
    submit = "```python\nhost.submit_output({'summary': 'Done.'}, ['done'])\n```"
    replies = {"reply": draft}

    def fake_chat(messages, cfg, on_delta=None, **kw):
        if any("Output the title only" in str(m.get("content")) for m in messages):
            return {"content": "Two-group comparison", "usage": {}}
        return {"content": replies["reply"], "usage": {}}

    def fake_exec(st, code, origin, emit, stream=True, language="python"):
        st.dispatcher.last_output = {
            "output": {"summary": "Done.", "metrics": {"n": 40}},
            "completion_bullets": ["wrote means.json"],
        }
        return {"result": {"stdout": "", "stderr": "", "error": None}}

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_ensure_kernel", _fake_ensure)
    monkeypatch.setattr(runner, "_execute_and_log", fake_exec)

    drafted = runner.run_message(
        fid, "default", "Compare two synthetic groups.", plan=True
    )
    assert drafted["plan_captured"] is True
    replies["reply"] = submit
    res = runner.run_plan_execution(fid, "default")
    assert res["status"] == "completed"
    assert store.get_plan_by_frame(fid)["status"] == "completed"

    users = [m["content"] for m in store.list_messages(fid) if m["role"] == "user"]
    assert len(users) == 2
    assert users[1].startswith('Plan "Two-group comparison" is approved')
    streamed = "".join(
        str(e.get("chunk") or "") for e in hub.events if e["type"] == "text_chunk"
    )
    assert "Metrics:" in streamed
    for chinese in ("指标", "完成内容", "已批准计划", "我已经准备好"):
        assert chinese not in streamed


# ------------- host.plan_update ticks a step + emits plan_progress --------- #
def test_host_plan_update_ticks_step(tmp_path):
    cfg = _cfg(tmp_path)
    store = get_store(cfg.db_path)
    fid = store.new_frame(kind="turn", project_id="default", status="ready")
    p = store.create_plan(
        frame_id=fid,
        title="T",
        rationale="r",
        confidence="high",
        steps=[{"id": "s1", "title": "A", "detail": "", "deliverables": []}],
    )
    from openai4s.host_dispatch import HostDispatcher

    disp = HostDispatcher(cfg=cfg, frame_id=fid)
    ticks = []
    disp.on_plan = lambda ev: ticks.append(ev)
    out = disp._m_plan_update({"step_id": "s1", "status": "completed"})
    assert out["ok"] and out["step_id"] == "s1"
    assert store.get_plan(p["plan_id"])["step_status"]["s1"]["status"] == "completed"
    assert ticks and ticks[0]["step_id"] == "s1" and ticks[0]["status"] == "completed"


def test_host_plan_update_without_plan_soft_fails(tmp_path):
    cfg = _cfg(tmp_path)
    store = get_store(cfg.db_path)
    fid = store.new_frame(kind="turn", project_id="default", status="ready")
    from openai4s.host_dispatch import HostDispatcher

    disp = HostDispatcher(cfg=cfg, frame_id=fid)
    out = disp._m_plan_update({"step_id": "s1", "status": "completed"})
    assert "error" in out


# ------------------------------- discard ----------------------------------- #
def test_discard_plan(tmp_path):
    cfg = _cfg(tmp_path)
    hub = _Hub()
    runner = gateway_mod.SessionRunner(cfg, hub)
    store = get_store(cfg.db_path)
    fid = store.new_frame(kind="turn", project_id="default", status="ready")
    store.create_plan(
        frame_id=fid,
        title="T",
        rationale="r",
        confidence="high",
        steps=[{"id": "s1", "title": "A"}],
    )
    out = runner.discard_plan(fid)
    assert out["ok"] and out["status"] == "discarded"
    assert runner.get_plan_state(fid)["status"] == "discarded"
