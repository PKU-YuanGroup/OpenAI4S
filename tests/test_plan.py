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

    def fake_exec(st, code, origin, emit, stream=True):
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
