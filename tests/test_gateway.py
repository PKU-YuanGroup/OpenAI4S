import base64
import io
import json
import re
import struct
import threading
import time
from pathlib import Path
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


def test_gateway_plain_answer_is_nudged_until_structured_submit(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    hub = _Hub()
    runner = gateway_mod.SessionRunner(cfg, hub)
    store = get_store(cfg.db_path)
    fid = store.new_frame(kind="turn", project_id="default", status="ready")
    calls = []

    replies = iter(
        [
            "Short answer.",
            "```python\nhost.submit_output({'answer': 'Short answer.'}, ['done'])\n```",
        ]
    )

    def fake_chat(messages, cfg, on_delta=None, **kwargs):
        calls.append(messages)
        content = next(replies)
        if on_delta:
            on_delta(content)
        return {"content": content, "usage": {}}

    def fake_ensure(st):
        st.dispatcher = SimpleNamespace(last_output=None)
        st.messages = [{"role": "system", "content": "sys"}]
        st.booted = True

    def fake_exec(st, code, origin, emit, stream=True):
        st.dispatcher.last_output = {"output": {"answer": "Short answer."}}
        return {"result": {"stdout": "", "stderr": "", "error": None}}

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_ensure_kernel", fake_ensure)
    monkeypatch.setattr(runner, "_execute_and_log", fake_exec)
    # the background title-summary chat would also land in `calls` and race the
    # count; it is orthogonal to the plain-answer path under test
    monkeypatch.setattr(runner, "_spawn_title_summary", lambda *a, **k: None)

    result = runner.run_message(fid, "default", "What is OpenAI4S?")

    assert result["status"] == "completed"
    assert len(calls) == 2
    assert any(
        "Prose is not a completion signal" in m["content"]
        for m in calls[1]
        if m["role"] == "user"
    )
    messages = store.list_messages(fid)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[-1]["content"] == "Short answer."
    assert hub.events[-1]["type"] == "frame_update"
    assert hub.events[-1]["status"] == "completed"


def test_submit_message_runs_turn_in_background(tmp_path):
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    started = threading.Event()
    release = threading.Event()

    def fake_run(
        root_frame_id,
        project_id,
        user_text,
        model=None,
        plan=False,
        annos=None,
        explore=False,
    ):
        started.set()
        assert root_frame_id == "f-test"
        assert project_id == "default"
        assert user_text == "long task"
        assert model == "model-x"
        release.wait(2)
        return {"status": "completed", "frame_id": root_frame_id}

    runner.run_message = fake_run

    job = runner.submit_message("f-test", "default", "long task", "model-x")

    assert started.wait(1)
    assert not job.done.is_set()
    release.set()
    assert job.wait_result()["status"] == "completed"
    assert job.result["job_id"] == job.job_id


def test_annotation_store_crud_and_send_folding(tmp_path):
    cfg = _cfg(tmp_path)
    store = get_store(cfg.db_path)
    fid = store.new_frame(kind="turn", project_id="default", status="ready")

    a1 = store.add_annotation(
        root_frame_id=fid,
        artifact_id="fx-1",
        artifact_name="top5_overview.png",
        rel_x=0.63,
        rel_y=0.38,
        body="把第 3 个柱子的标签改成红色",
    )
    a2 = store.add_annotation(
        root_frame_id=fid,
        artifact_id="fx-1",
        artifact_name="top5_overview.png",
        rel_x=1.4,
        rel_y=-0.2,
        body="这个区域配色太浅",
    )
    # pin numbers increment per (frame, artifact); coords clamp to [0,1]
    assert a1["number"] == 1 and a2["number"] == 2
    assert a2["rel_x"] == 1.0 and a2["rel_y"] == 0.0

    listed = store.list_annotations(fid, artifact_id="fx-1")
    assert [x["number"] for x in listed] == [1, 2]
    assert all(x["status"] == "open" for x in listed)

    # the prompt fold — the remote agent must see file + location + comment
    block = gateway_mod._format_annotations_block(
        [
            store.get_annotation(a1["annotation_id"]),
            store.get_annotation(a2["annotation_id"]),
        ]
    )
    assert "top5_overview.png" in block
    assert "把第 3 个柱子的标签改成红色" in block
    assert "[1]" in block and "[2]" in block

    # sending marks them sent (so the composer badge clears) but keeps the pins
    store.mark_annotations_sent([a1["annotation_id"], a2["annotation_id"]])
    assert store.list_annotations(fid, status="open") == []
    assert len(store.list_annotations(fid, status="sent")) == 2

    # delete + cascade on frame delete
    store.delete_annotation(a1["annotation_id"])
    assert store.get_annotation(a1["annotation_id"]) is None
    store.delete_frame(fid)
    assert store.list_annotations(fid) == []


# --- artifact version management ----------------------------------------
def _runner_frame(tmp_path):
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    store = runner.store
    fid = store.new_frame(kind="turn", project_id="default", status="ready")
    st = gateway_mod.SessionState(fid, "default", runner.workspace_for(fid))
    return cfg, runner, store, fid, st


def test_auto_capture_preserves_version_bytes(tmp_path):
    """A file the agent writes then OVERWRITES keeps real per-version history:
    each version_id resolves to its own bytes, not the current live-file content."""
    cfg, runner, store, fid, st = _runner_frame(tmp_path)
    f = st.workspace / "out.txt"

    f.write_text("VERSION-ONE")
    rec1 = runner._register_file(st, f, "cell-1", lambda e: None)
    f.write_text("VERSION-TWO-longer")
    rec2 = runner._register_file(st, f, "cell-2", lambda e: None)

    # same logical artifact, two distinct versions
    assert rec1["artifact_id"] == rec2["artifact_id"]
    assert rec1["version_id"] != rec2["version_id"]
    versions = store.list_versions(rec1["artifact_id"])
    assert [v["ordinal"] for v in versions] == [2, 1]
    assert versions[0]["is_latest"] and not versions[1]["is_latest"]

    # each version resolves to ITS OWN bytes (history is real, not aliased)
    assert (
        Path(store.resolve_artifact_path(rec1["version_id"])).read_text()
        == "VERSION-ONE"
    )
    assert (
        Path(store.resolve_artifact_path(rec2["version_id"])).read_text()
        == "VERSION-TWO-longer"
    )
    # the artifact_id resolves to the latest bytes
    assert (
        Path(store.resolve_artifact_path(rec1["artifact_id"])).read_text()
        == "VERSION-TWO-longer"
    )

    # overwriting the live file yet again must NOT rewrite the old snapshots
    f.write_text("VERSION-THREE")
    assert (
        Path(store.resolve_artifact_path(rec1["version_id"])).read_text()
        == "VERSION-ONE"
    )
    assert (
        Path(store.resolve_artifact_path(rec2["version_id"])).read_text()
        == "VERSION-TWO-longer"
    )


def test_restore_version_reverts_live_and_latest(tmp_path):
    cfg, runner, store, fid, st = _runner_frame(tmp_path)
    f = st.workspace / "fig.txt"
    f.write_text("ALPHA")
    rec1 = runner._register_file(st, f, "c1", lambda e: None)
    f.write_text("BETA")
    rec2 = runner._register_file(st, f, "c2", lambda e: None)

    res = runner.restore_version(rec1["artifact_id"], rec1["version_id"])
    assert res.get("ok")
    # the live workspace file is reverted so the agent sees the old content
    assert f.read_text() == "ALPHA"
    # the latest pointer moved back to the restored version
    a = store.get_artifact(rec1["artifact_id"])
    assert a["latest_version_id"] == rec1["version_id"]
    assert Path(store.resolve_artifact_path(rec1["artifact_id"])).read_text() == "ALPHA"
    # history is preserved — the superseded version still serves its own bytes
    assert Path(store.resolve_artifact_path(rec2["version_id"])).read_text() == "BETA"

    # a nonexistent / foreign version_id is rejected, not silently applied
    assert runner.restore_version(rec1["artifact_id"], "v-nope").get("error")
    g = st.workspace / "other.txt"
    g.write_text("G")
    other = runner._register_file(st, g, "c3", lambda e: None)
    assert runner.restore_version(rec1["artifact_id"], other["version_id"]).get("error")


def test_save_artifact_atomic_and_delete_cleans_snapshots(tmp_path):
    cfg, runner, store, fid, st = _runner_frame(tmp_path)
    f = st.workspace / "data.csv"
    f.write_text("a")
    rec1 = runner._register_file(st, f, "c1", lambda e: None)
    f.write_text("bb")
    runner._register_file(st, f, "c2", lambda e: None)

    # latest_version_id always references a real version row (single-commit write)
    a = store.get_artifact(rec1["artifact_id"])
    assert store.version_meta(a["latest_version_id"]) is not None

    # immutable per-version snapshots live under the versions dir
    vdir = cfg.data_dir / "artifact-versions"
    assert len(list(vdir.glob("*"))) >= 2

    # deleting the artifact hands back its snapshot files for cleanup + drops rows
    stale = store.delete_artifact(rec1["artifact_id"])
    assert any(str(vdir) in p for p in stale)
    assert store.get_artifact(rec1["artifact_id"]) is None
    assert store.list_versions(rec1["artifact_id"]) == []


def test_explore_mode_injects_protocol_and_nudges_prose_stalls(monkeypatch, tmp_path):
    """Explore mode: the protocol rides on the user message, and a prose-only
    reply (no code, no submit_output) is pushed back on until the turn limit,
    then fails instead of silently reporting completion."""
    cfg = _cfg(tmp_path)
    cfg.explore_max_turns = 4
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    store = get_store(cfg.db_path)
    fid = store.new_frame(kind="turn", project_id="default", status="ready")
    calls = []

    def fake_chat(messages, cfg, on_delta=None, **kwargs):
        calls.append([dict(m) for m in messages])
        return {"content": "I think I'm done exploring.", "usage": {}}

    def fake_ensure(st):
        st.dispatcher = SimpleNamespace(last_output=None)
        st.messages = [{"role": "system", "content": "sys"}]
        st.booted = True

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_ensure_kernel", fake_ensure)
    # silence the background title-summary chat (it would race `calls`)
    monkeypatch.setattr(runner, "_spawn_title_summary", lambda *a, **k: None)

    result = runner.run_message(fid, "default", "探索地球磁场如何演化", explore=True)

    assert result["status"] == "failed"
    assert "without calling host.submit_output" in result["error"]
    # protocol appended to the in-conversation user message (not the stored one)
    assert "[EXPLORE MODE" in calls[0][-1]["content"]
    assert store.list_messages(fid)[0]["content"] == "探索地球磁场如何演化"
    # 1 initial call + 3 visible nudges before the configured limit is reached.
    assert len(calls) == 4
    nudges = [
        m for m in calls[-1] if m["role"] == "user" and "Explore mode" in m["content"]
    ]
    assert len(nudges) == 3


def test_explore_flag_passes_through_submit_message(tmp_path):
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    seen = {}

    def fake_run(
        root_frame_id,
        project_id,
        user_text,
        model=None,
        plan=False,
        annos=None,
        explore=False,
    ):
        seen["explore"] = explore
        return {"status": "completed", "frame_id": root_frame_id}

    runner.run_message = fake_run
    job = runner.submit_message("f-x", "default", "task", None, explore=True)
    assert job.wait_result()["status"] == "completed"
    assert seen["explore"] is True


def test_midtask_prose_conclusion_still_requires_structured_submit(
    monkeypatch, tmp_path
):
    """Even conclusive prose after real work is not a completion signal."""
    cfg = _cfg(tmp_path)
    cfg.max_turns = 4
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    store = get_store(cfg.db_path)
    fid = store.new_frame(kind="turn", project_id="default", status="ready")
    replies = iter(
        [
            "Running step 1.\n```python\nprint('x')\n```",
            "Now let me look into the data files.",
            "Done: the answer is 42, analysis complete.",
            "```python\nhost.submit_output({'answer': 42}, ['done'])\n```",
        ]
    )
    chat_calls = []

    def fake_chat(messages, cfg, on_delta=None, **kwargs):
        chat_calls.append([dict(m) for m in messages])
        return {"content": next(replies), "usage": {}}

    def fake_ensure(st):
        st.dispatcher = SimpleNamespace(last_output=None)
        st.messages = [{"role": "system", "content": "sys"}]
        st.booted = True

    def fake_exec(st, code, origin, emit, stream=True):
        if "host.submit_output" in code:
            st.dispatcher.last_output = {"output": {"answer": 42}}
        return {"result": {"stdout": "x\n", "stderr": "", "error": None}}

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_ensure_kernel", fake_ensure)
    monkeypatch.setattr(runner, "_execute_and_log", fake_exec)
    monkeypatch.setattr(runner, "_spawn_title_summary", lambda *a, **k: None)

    result = runner.run_message(fid, "default", "analyze something")

    assert result["status"] == "completed"
    assert len(chat_calls) == 4
    assert (
        sum(
            "Prose is not a completion signal" in m["content"]
            for m in chat_calls[-1]
            if m["role"] == "user"
        )
        == 2
    )
    msgs = store.list_messages(fid)
    assert "the answer is 42" in msgs[-1]["content"]


def test_batched_code_blocks_warn_only_first_ran(monkeypatch, tmp_path):
    """A reply that batches several ```python blocks must run only the FIRST and
    feed back an explicit warning that the rest did NOT run. Otherwise the model
    treats the un-run cells (and any output it already narrated for them) as done
    and 'concludes' the whole task after one cell — the false-completion bug that
    leaves a deliverable task with an empty working directory."""
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    store = get_store(cfg.db_path)
    fid = store.new_frame(kind="turn", project_id="default", status="ready")
    seen = []
    replies = iter(
        [
            # turn 0: the model batches TWO cells + fabricated narration in one reply
            "Fetch then analyze.\n```python\nprint('a')\n```\nSaved.\n"
            "```python\nprint('b')\n```",
            # turn 1: with only the first cell actually run, the model tries to bail out
            "All done — everything succeeded.",
            # prose cannot complete the task; the next turn submits structurally
            "```python\nhost.submit_output({'ok': True}, ['done'])\n```",
        ]
    )

    def fake_chat(messages, cfg, on_delta=None, **kwargs):
        seen.append([dict(m) for m in messages])
        return {"content": next(replies), "usage": {}}

    def fake_ensure(st):
        st.dispatcher = SimpleNamespace(last_output=None)
        st.messages = [{"role": "system", "content": "sys"}]
        st.booted = True

    def fake_exec(st, code, origin, emit, stream=True):
        if "host.submit_output" in code:
            st.dispatcher.last_output = {"output": {"ok": True}}
        return {"result": {"stdout": "a\n", "stderr": "", "error": None}}

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_ensure_kernel", fake_ensure)
    monkeypatch.setattr(runner, "_execute_and_log", fake_exec)
    monkeypatch.setattr(runner, "_spawn_title_summary", lambda *a, **k: None)

    runner.run_message(fid, "default", "do a multi-step task")

    # the observation fed into turn 1 must warn that only the first block ran
    turn1_msgs = seen[1]
    warnings = [
        m
        for m in turn1_msgs
        if m["role"] == "user" and "only the FIRST" in m["content"]
    ]
    assert warnings, "batched-cell warning was not fed back to the model"


def test_effective_api_key_ignores_persisted_placeholder(tmp_path):
    # a stub persisted to settings before the config-level filter existed
    # (e.g. activating a profile seeded with `your-api-key-here`) must not
    # make the UI banner report a configured key
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    store = get_store(cfg.db_path)

    store.set_setting("llm_api_key", "your-api-key-here")
    assert runner.effective_api_key() == "test-key"  # falls back to cfg

    store.set_setting("llm_api_key", "sk-real")
    assert runner.effective_api_key() == "sk-real"


def test_llm_cfg_ignores_persisted_placeholder_runtime_key(tmp_path):
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    store = get_store(cfg.db_path)

    store.set_setting("llm_api_key", "your-api-key-here")
    assert runner.effective_api_key() == "test-key"
    assert runner._llm_cfg().api_key == "test-key"


def test_model_profile_mask_and_seed_ignore_placeholder_keys(tmp_path):
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    handler = object.__new__(handler_cls)
    store = get_store(cfg.db_path)

    assert not handler._mask_profile({"api_key": "your-api-key-here"})["has_api_key"]
    assert handler._mask_profile({"api_key": "sk-real"})["has_api_key"]

    store.set_setting("llm_api_key", "your-api-key-here")
    handler._model_profiles_payload()
    profiles = store.list_model_profiles()
    ark_keys = [p.get("api_key") for p in profiles if p.get("provider") == "ark"]
    assert ark_keys
    assert "your-api-key-here" not in ark_keys
    assert set(ark_keys) == {"test-key"}


def test_model_profile_activate_moves_to_front_and_sanitizes_key(tmp_path):
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    handler = object.__new__(handler_cls)
    store = get_store(cfg.db_path)
    store.set_model_profiles(
        [
            {
                "id": "mp-a",
                "name": "A",
                "provider": "ark",
                "base_url": "",
                "model": "glm-5.2",
                "api_key": "sk-a",
            },
            {
                "id": "mp-b",
                "name": "B",
                "provider": "ark",
                "base_url": "",
                "model": "kimi-k2.6",
                "api_key": "your-api-key-here",
            },
        ]
    )
    replies = []
    handler._query = lambda: {}
    handler._body = lambda: {}
    handler._json = lambda obj, code=200: replies.append((code, obj))

    handler._api("POST", "/model-profiles/mp-b/activate")

    assert replies[-1][0] == 200
    assert replies[-1][1]["active_id"] == "mp-b"
    assert [p["id"] for p in store.list_model_profiles()] == ["mp-b", "mp-a"]
    assert store.get_setting("active_model_profile") == "mp-b"
    assert store.get_setting("llm_api_key") == ""


# --- API contract assertions (documented in docs/webapp-api.md) ------------
def test_api_unknown_route_returns_error_envelope(tmp_path):
    """The catch-all error envelope is {"error": ...} — NOT {"detail": ...}.
    (The frontend api() helper reads j.detail; docs/webapp-api.md records the
    mismatch. This locks the backend side of the contract.)"""
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    handler = object.__new__(handler_cls)
    replies = []
    handler._query = lambda: {}
    handler._body = lambda: {}
    handler._json = lambda obj, code=200: replies.append((code, obj))

    handler._api("GET", "/definitely-not-a-route")

    code, body = replies[-1]
    assert code == 404
    assert body["error"] == "not found"
    assert body["path"] == "/definitely-not-a-route"
    assert body["method"] == "GET"
    assert "detail" not in body


def test_projects_route_has_no_pagination_semantics(tmp_path):
    """GET /api/projects ignores ?limit&offset (the frontend sends them):
    every project is always returned and `total` is just the list length."""
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    handler = object.__new__(handler_cls)
    store = get_store(cfg.db_path)
    for i in range(3):
        store.create_project(name=f"p{i}", description="", context="")
    replies = []
    # limit=1&offset=1 as parse_qs would deliver them — must have no effect
    handler._query = lambda: {"limit": ["1"], "offset": ["1"]}
    handler._body = lambda: {}
    handler._json = lambda obj, code=200: replies.append((code, obj))

    handler._api("GET", "/projects")

    code, body = replies[-1]
    assert code == 200
    names = {p["name"] for p in body["projects"]}
    assert {"p0", "p1", "p2"} <= names
    assert body["total"] == len(body["projects"])


def test_serializers_expose_dual_id_keys(tmp_path):
    """Frontend-compat contract: artifact/project serializers duplicate the
    typed id under a plain `id` key, and _artifact_json.version_id is the
    LATEST version id (the UI cache-bust key)."""
    cfg, runner, store, fid, st = _runner_frame(tmp_path)
    f = st.workspace / "plot.txt"
    f.write_text("v1")
    rec = runner._register_file(st, f, "cell-1", lambda e: None)

    aj = gateway_mod._artifact_json(store.get_artifact(rec["artifact_id"]))
    assert aj["id"] == aj["artifact_id"] == rec["artifact_id"]
    assert aj["version_id"] == rec["version_id"]
    assert aj["root_frame_id"] == fid
    assert aj["is_user_upload"] is False

    p = store.create_project(name="proj", description="", context="")
    pj = gateway_mod._project_json(store.get_project(p["project_id"]) or p)
    assert pj["id"] == pj["project_id"] == p["project_id"]

    fj = gateway_mod._frame_json(store.get_frame(fid), store)
    assert fj["id"] == fid
    assert fj["root_frame_id"] == fid
    assert fj["conversation_type"] == "agent"


def test_auto_capture_artifact_created_event_shape(tmp_path):
    """The auto-capture emit site sends the RICH artifact_created form —
    a nested `artifact` object with duplicated id/artifact_id and a
    version_id. Other emit sites send partial/flat/bare forms, so consumers
    must treat every field as optional (docs/webapp-api.md §3)."""
    cfg, runner, store, fid, st = _runner_frame(tmp_path)
    events = []
    f = st.workspace / "fig.txt"
    f.write_text("bytes")
    rec = runner._register_file(st, f, "cell-9", events.append)

    created = [e for e in events if e.get("type") == "artifact_created"]
    assert created, "auto-capture did not emit artifact_created"
    art = created[-1]["artifact"]
    assert art["id"] == art["artifact_id"] == rec["artifact_id"]
    assert art["version_id"] == rec["version_id"]
    assert art["filename"] == "fig.txt"
    assert art["root_frame_id"] == fid


def test_edit_rename_upload_artifact_created_shapes(tmp_path):
    """The PARTIAL artifact_created forms (docs/webapp-api.md §3, shape 2):
    edit → {id,filename,version_id,root_frame_id}; rename → {id,filename,
    root_frame_id} (no version_id); upload → {id,filename,content_type,
    root_frame_id} (no version_id). Consumers must treat every field as
    optional — this locks each emit site's exact key set."""
    cfg, runner, store, fid, st = _runner_frame(tmp_path)
    hub = _Hub()
    handler_cls = gateway_mod.make_handler(cfg, hub, runner)
    handler = object.__new__(handler_cls)
    f = st.workspace / "notes.txt"
    f.write_text("v1")
    rec = runner._register_file(st, f, "c1", lambda e: None)
    aid = rec["artifact_id"]

    def _created():
        return [e for e in hub.events if e.get("type") == "artifact_created"]

    res = handler._edit_artifact(aid, "v2 content")
    art = _created()[-1]["artifact"]
    assert set(art) == {"id", "filename", "version_id", "root_frame_id"}
    assert art["id"] == aid
    assert art["version_id"] == res["version_id"]
    assert art["root_frame_id"] == fid

    handler._rename_artifact(aid, "renamed.txt")
    art = _created()[-1]["artifact"]
    assert set(art) == {"id", "filename", "root_frame_id"}  # NO version_id
    assert art["filename"] == "renamed.txt"

    handler._upload(
        {
            "filename": "up.txt",
            "content_base64": base64.b64encode(b"hello").decode(),
            "frame_id": fid,
        }
    )
    art = _created()[-1]["artifact"]
    assert set(art) == {"id", "filename", "content_type", "root_frame_id"}
    assert art["filename"] == "up.txt"
    assert art["root_frame_id"] == fid


def test_plan_flat_and_bare_artifact_created_shapes(tmp_path):
    """Shapes 3 and 4 of artifact_created (docs/webapp-api.md §3): the plan
    artifact emits a FLAT event (no nested `artifact` key), and delete /
    version-restore emit a BARE {"type","root_frame_id"} refresh signal."""
    cfg = _cfg(tmp_path)
    hub = _Hub()
    runner = gateway_mod.SessionRunner(cfg, hub)
    store = runner.store
    fid = store.new_frame(kind="turn", project_id="default", status="ready")
    st = gateway_mod.SessionState(fid, "default", runner.workspace_for(fid))

    # shape 3: flat plan artifact — no nested `artifact` object at all
    events = []
    plan = {"title": "My Plan", "rationale": "r", "confidence": 0.9, "steps": []}
    rec = runner._write_plan_artifact(st, plan, None, events.append)
    ev = [e for e in events if e.get("type") == "artifact_created"][-1]
    assert "artifact" not in ev
    assert set(ev) == {"type", "frame_id", "artifact_id", "filename"}
    assert ev["artifact_id"] == rec["artifact_id"]
    assert ev["filename"].startswith("plan_") and ev["filename"].endswith(".json")

    # shape 4a: version restore — bare refresh signal via runner.hub
    f = st.workspace / "fig.txt"
    f.write_text("ALPHA")
    r1 = runner._register_file(st, f, "c1", lambda e: None)
    f.write_text("BETA")
    runner._register_file(st, f, "c2", lambda e: None)
    hub.events.clear()
    assert runner.restore_version(r1["artifact_id"], r1["version_id"]).get("ok")
    ev = [e for e in hub.events if e.get("type") == "artifact_created"][-1]
    assert set(ev) == {"type", "root_frame_id"}
    assert ev["root_frame_id"] == fid

    # shape 4b: DELETE /api/artifacts/{aid} — same bare form + {"ok": true}
    handler_cls = gateway_mod.make_handler(cfg, hub, runner)
    handler = object.__new__(handler_cls)
    replies = []
    handler._query = lambda: {}
    handler._body = lambda: {}
    handler._json = lambda obj, code=200: replies.append((code, obj))
    hub.events.clear()
    handler._api("DELETE", f"/artifacts/{r1['artifact_id']}")
    assert replies[-1] == (200, {"ok": True})
    ev = [e for e in hub.events if e.get("type") == "artifact_created"][-1]
    assert set(ev) == {"type", "root_frame_id"}
    assert store.get_artifact(r1["artifact_id"]) is None


def test_frame_update_status_literal_vocabulary(tmp_path):
    """Source-level lock on the frame_update status vocabulary documented in
    docs/webapp-api.md §3. Literal statuses in gateway.py emit sites are
    exactly {processing, titled, failed, success, updated}; the run_message
    terminal site emits a VARIABLE status ∈ {completed, failed, cancelled}
    (asserted behaviorally by the structured-submit and max-turn tests above).
    If this fails, a status was added/removed — update docs/webapp-api.md."""
    src = Path(gateway_mod.__file__).read_text(encoding="utf-8")
    sites = list(re.finditer(r'"type": "frame_update"', src))
    assert len(sites) >= 7  # the emit sites documented today
    literals = set()
    for m in sites:
        window = src[m.end() : m.end() + 250]
        s = re.search(r'"status": "([a-z_]+)"', window)
        if s:
            literals.add(s.group(1))
    assert literals == {"processing", "titled", "failed", "success", "updated"}


def test_auto_title_broadcasts_titled_frame_update(monkeypatch, tmp_path):
    """The background auto-title thread emits frame_update status="titled"
    with an extra task_summary field (the only frame_update variant carrying
    one) — docs/webapp-api.md §3."""
    cfg = _cfg(tmp_path)
    hub = _Hub()
    runner = gateway_mod.SessionRunner(cfg, hub)
    store = runner.store
    fid = store.new_frame(kind="turn", project_id="default", status="ready")
    placeholder = "analyze the sales da…"
    store.update_frame(fid, task_summary=placeholder)

    monkeypatch.setattr(
        gateway_mod,
        "chat",
        lambda messages, cfg, **kw: {"content": "Sales data analysis", "usage": {}},
    )
    runner._spawn_title_summary(
        fid, "analyze the sales data please", cfg.llm, placeholder
    )

    deadline = time.time() + 3
    titled = []
    while time.time() < deadline and not titled:
        titled = [
            e
            for e in hub.events
            if e.get("type") == "frame_update" and e.get("status") == "titled"
        ]
        time.sleep(0.01)
    assert titled, "no frame_update status=titled was broadcast"
    ev = titled[-1]
    assert ev["frame_id"] == fid
    assert ev["task_summary"] == "Sales data analysis"
    assert store.get_frame(fid)["task_summary"] == "Sales data analysis"


def test_token_gate_401_and_cookie_redirect(monkeypatch, tmp_path, capsys):
    """The token gate (docs/webapp-api.md §1): with OPENAI4S_REQUIRE_TOKEN=1,
    a request without the token gets a 401 {"error": ...} envelope; a GET
    carrying a valid ?token= gets 303 Location:/ + Set-Cookie os_token;
    /health stays exempt."""
    monkeypatch.setenv("OPENAI4S_REQUIRE_TOKEN", "1")
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    printed = capsys.readouterr().out
    tok = re.search(r"\?token=([0-9a-f]{32})", printed)
    assert tok, "gateway did not print the access token"
    token = tok.group(1)

    handler = object.__new__(handler_cls)
    handler.headers = {}  # no Cookie, no Origin
    replies = []
    handler._json = lambda obj, code=200: replies.append((code, obj))

    # no token → 401 with the {"error": ...} envelope
    handler.path = "/api/frames"
    handler._route("GET")
    code, body = replies[-1]
    assert code == 401
    assert body["error"].startswith("unauthorized")

    # wrong token → still 401
    handler.path = "/api/frames?token=deadbeef"
    handler._route("GET")
    assert replies[-1][0] == 401

    # /health is exempt from the gate
    handler.path = "/health"
    handler._route("GET")
    code, body = replies[-1]
    assert code == 200 and body["status"] == "ok"

    # valid ?token= on a GET → 303 to / with the os_token cookie set
    resp = {"code": None, "headers": {}}
    handler.send_response = lambda c: resp.__setitem__("code", c)
    handler.send_header = lambda k, v: resp["headers"].__setitem__(k, v)
    handler.end_headers = lambda: None
    handler.path = f"/?token={token}"
    handler._route("GET")
    assert resp["code"] == 303
    assert resp["headers"]["Location"] == "/"
    assert resp["headers"]["Set-Cookie"].startswith(f"os_token={token}")
    assert "HttpOnly" in resp["headers"]["Set-Cookie"]


def test_gateway_error_maps_to_error_envelope(tmp_path):
    """A GatewayError(code, message) raised anywhere under /api/* is serialized
    by _route as {"error": message} with its HTTP code (docs/webapp-api.md §2).
    Contract test before any extraction of gateway routing."""
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    handler = object.__new__(handler_cls)
    handler.headers = {}
    replies = []
    handler._json = lambda obj, code=200: replies.append((code, obj))

    def boom(method, sub):
        raise gateway_mod.GatewayError(418, "teapot")

    handler._api = boom
    handler.path = "/api/anything"
    handler._route("GET")

    assert replies[-1] == (418, {"error": "teapot"})


def test_unhandled_exception_maps_to_500_error_envelope(tmp_path, capsys):
    """A non-GatewayError exception under /api/* becomes a 500 with the
    same {"error": str(e)} envelope (and never a raw traceback body)."""
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    handler = object.__new__(handler_cls)
    handler.headers = {}
    replies = []
    handler._json = lambda obj, code=200: replies.append((code, obj))

    def boom(method, sub):
        raise RuntimeError("kaput")

    handler._api = boom
    handler.path = "/api/anything"
    handler._route("GET")

    assert replies[-1] == (500, {"error": "kaput"})
    capsys.readouterr()  # swallow the printed traceback


def test_cross_origin_api_write_is_refused(tmp_path):
    """CSRF guard: a mutating /api request whose Origin host differs from the
    Host header is rejected 403 with the {"error": ...} envelope BEFORE any
    route logic runs."""
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    handler = object.__new__(handler_cls)
    handler.headers = {
        "Origin": "http://evil.example",
        "Host": "127.0.0.1:8760",
    }
    replies = []
    handler._json = lambda obj, code=200: replies.append((code, obj))
    handler._api = lambda method, sub: replies.append(("api-was-called", None))
    handler.path = "/api/frames"
    handler._route("POST")

    assert replies == [(403, {"error": "cross-origin request refused"})]


def test_execution_log_route_serializer_contract(tmp_path):
    """GET /api/frames/{fid}/execution-log — the Notebook data contract: each
    entry carries exactly source/stdout/stderr/error/status/figures/
    files_written/files_read/cpu_seconds/peak_rss_kb (+ cell_index/kernel_id/
    language), with code→source and cpu_s→cpu_seconds renames and ""/[] (never
    null) defaults."""
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    handler = object.__new__(handler_cls)
    store = get_store(cfg.db_path)
    fid = store.new_frame(kind="turn", project_id="default", status="ready")

    store.log_cell(
        frame_id=fid,
        root_frame_id=fid,
        code="print('hi')",
        result={
            "id": "cell-1",
            "stdout": "hi\n",
            "stderr": "",
            "error": None,
            "interrupted": False,
            "usage": {"wall_s": 0.5, "cpu_s": 0.25, "peak_rss_kb": 2048},
        },
        cell_index=1,
        kernel_id="python",
        language="python",
        figures=["fig1.png"],
        files_read=["in.csv"],
        files_written=["out.csv"],
    )
    store.log_cell(
        frame_id=fid,
        root_frame_id=fid,
        code="1/0",
        result={
            "id": "cell-2",
            "stdout": "",
            "stderr": "",
            "error": "ZeroDivisionError",
        },
        cell_index=2,
    )

    replies = []
    handler._query = lambda: {}
    handler._body = lambda: {}
    handler._json = lambda obj, code=200: replies.append((code, obj))
    handler._api("GET", f"/frames/{fid}/execution-log")

    code, body = replies[-1]
    assert code == 200
    assert body["kernels"] == ["python"]  # deduped, first-seen order
    assert len(body["entries"]) == 2
    e1, e2 = body["entries"]
    assert set(e1) == {
        "cell_index",
        "kernel_id",
        "language",
        "source",
        "stdout",
        "stderr",
        "error",
        "status",
        "figures",
        "files_written",
        "files_read",
        "cpu_seconds",
        "peak_rss_kb",
    }
    assert e1["source"] == "print('hi')"  # code -> source rename
    assert e1["status"] == "ok"
    assert e1["cpu_seconds"] == 0.25  # cpu_s -> cpu_seconds rename
    assert e1["peak_rss_kb"] == 2048
    assert e1["figures"] == ["fig1.png"]
    assert e1["files_read"] == ["in.csv"]
    assert e1["files_written"] == ["out.csv"]
    assert e1["error"] == ""  # null-free default
    assert e2["status"] == "error"
    assert e2["error"] == "ZeroDivisionError"
    assert e2["figures"] == [] and e2["files_written"] == []


def test_lineage_serializer_producing_cell_and_inputs(tmp_path):
    """The artifact lineage payload (UI provenance view): a produced artifact
    reports its producing cell interaction + save event, and
    dependency_mappings.inputs = files_read minus files_written minus itself.
    An unknown artifact returns the same shape, empty."""
    cfg, runner, store, fid, st = _runner_frame(tmp_path)
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    handler = object.__new__(handler_cls)

    store.log_cell(
        frame_id=fid,
        root_frame_id=fid,
        code="plot(df)",
        result={"id": "cell-7", "stdout": "", "stderr": "", "error": None},
        cell_index=3,
        files_read=["raw.csv", "fig.txt"],
        files_written=["fig.txt"],
    )
    f = st.workspace / "fig.txt"
    f.write_text("bytes")
    rec = runner._register_file(st, f, "cell-7", lambda e: None)

    lin = handler._lineage(rec["artifact_id"])
    assert lin["artifact_id"] == rec["artifact_id"]
    assert lin["filename"] == "fig.txt"
    kinds = [i["kind"] for i in lin["interactions"]]
    assert kinds == ["cell", "save"]
    cell = lin["interactions"][0]
    assert cell["cell_index"] == 3
    assert cell["source"] == "plot(df)"
    assert cell["exit_status"] == "ok"
    assert cell["files_written"] == ["fig.txt"]
    # inputs exclude what the cell itself wrote and the artifact's own filename
    assert lin["dependency_mappings"] == {"inputs": ["raw.csv"]}

    empty = handler._lineage("a-does-not-exist")
    assert empty == {
        "artifact_id": "a-does-not-exist",
        "filename": None,
        "interactions": [],
        "dependency_mappings": {"inputs": []},
    }


def test_upload_base64_decode_and_raw_fallback(tmp_path):
    """POST /api/uploads decode reality (docs/webapp-api.md §2): valid base64
    decodes; non-alphabet chars are silently DISCARDED (not an error); only a
    residual padding/length error falls back to storing the raw UTF-8 text."""
    cfg, runner, store, fid, st = _runner_frame(tmp_path)
    hub = _Hub()
    handler_cls = gateway_mod.make_handler(cfg, hub, runner)
    handler = object.__new__(handler_cls)

    def _bytes(res):
        return Path(store.resolve_artifact_path(res["artifact_id"])).read_bytes()

    # valid base64 → decoded bytes stored
    res = handler._upload(
        {
            "filename": "a.bin",
            "content_base64": base64.b64encode(b"\x00\x01binary").decode(),
            "frame_id": fid,
        }
    )
    assert res["id"] == res["artifact_id"] and res["filename"] == "a.bin"
    assert _bytes(res) == b"\x00\x01binary"

    # non-alphabet chars silently dropped, remainder decoded ("Zm9v!YmFy" → foobar)
    res = handler._upload(
        {"filename": "b.bin", "content_base64": "Zm9v!YmFy", "frame_id": fid}
    )
    assert _bytes(res) == b"foobar"

    # padding/length error → the ORIGINAL string's UTF-8 bytes stored as-is
    res = handler._upload(
        {"filename": "c.bin", "content_base64": "%%% not base64 %%%", "frame_id": fid}
    )
    assert _bytes(res) == "%%% not base64 %%%".encode("utf-8")


# --- hand-rolled WebSocket wire format (risk register: payload drift) -------
def test_ws_encode_frame_length_ladder():
    """RFC 6455 server frames: FIN|opcode first byte, then the 7-bit /
    16-bit / 64-bit length ladder switching at exactly 126 and 65536 —
    and server frames are NEVER masked (no 0x80 bit on byte 1)."""
    small = gateway_mod._ws_encode(b"hello")
    assert small[0] == 0x81  # FIN + text opcode
    assert small[1] == 5  # 7-bit length, mask bit clear
    assert small[2:] == b"hello"

    edge = gateway_mod._ws_encode(b"x" * 126)
    assert edge[1] == 126
    assert edge[2:4] == struct.pack(">H", 126)
    assert edge[4:] == b"x" * 126

    big = gateway_mod._ws_encode(b"y" * 65536, opcode=0x2)
    assert big[0] == 0x82  # FIN + binary opcode
    assert big[1] == 127
    assert big[2:10] == struct.pack(">Q", 65536)
    assert len(big) == 10 + 65536


def test_ws_read_frame_unmasks_and_roundtrips():
    """_ws_read_frame unmasks client frames, passes opcodes through, returns
    None on a truncated header, and round-trips every _ws_encode length
    class — the encode/decode pair cannot drift apart silently."""
    payload = b'{"type":"ping"}'
    mask = bytes([0x12, 0x34, 0x56, 0x78])
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    frame = bytes([0x81, 0x80 | len(payload)]) + mask + masked
    assert gateway_mod._ws_read_frame(io.BytesIO(frame)) == (0x1, payload)

    # masked frame in the 16-bit length class
    payload2 = b"z" * 300
    masked2 = bytes(b ^ mask[i % 4] for i, b in enumerate(payload2))
    frame2 = bytes([0x82, 0x80 | 126]) + struct.pack(">H", 300) + mask + masked2
    assert gateway_mod._ws_read_frame(io.BytesIO(frame2)) == (0x2, payload2)

    # unmasked server frames round-trip across all three length classes
    for n in (0, 1, 125, 126, 65535, 65536):
        enc = gateway_mod._ws_encode(b"a" * n)
        assert gateway_mod._ws_read_frame(io.BytesIO(enc)) == (0x1, b"a" * n)

    # control frame opcode passes through untouched
    close = gateway_mod._ws_encode(b"", opcode=0x8)
    assert gateway_mod._ws_read_frame(io.BytesIO(close)) == (0x8, b"")

    # truncated header → None (connection treated as closed)
    assert gateway_mod._ws_read_frame(io.BytesIO(b"")) is None
    assert gateway_mod._ws_read_frame(io.BytesIO(b"\x81")) is None


# --- raw-bytes artifact routes ----------------------------------------------
def _bytes_handler(cfg, runner, hub=None):
    """Handler with _send captured — bytes routes bypass _json entirely."""
    handler_cls = gateway_mod.make_handler(cfg, hub or _Hub(), runner)
    handler = object.__new__(handler_cls)
    sends = []
    handler._send = lambda code, body, ctype, extra=None: sends.append(
        (code, body, ctype)
    )
    handler._query = lambda: {}
    handler._body = lambda: {}
    return handler, sends


def test_serve_artifact_three_way_resolution_and_bytes_contract(tmp_path):
    """GET /api/artifacts/{ident} resolution order: version_id →
    artifact_id → filename. A version id serves ITS OWN historical bytes
    (even when a file named like that id also exists), an artifact id serves
    the latest bytes, Content-Type comes from the stored row, and an unknown
    ident gets a JSON {"error": ...} 404 on this otherwise-bytes route."""
    cfg, runner, store, fid, st = _runner_frame(tmp_path)
    handler, sends = _bytes_handler(cfg, runner)

    f = st.workspace / "table.csv"
    f.write_text("v1")
    rec1 = runner._register_file(st, f, "c1", lambda e: None)
    f.write_text("v2-longer")
    runner._register_file(st, f, "c2", lambda e: None)

    # version_id → that version's own snapshot bytes + its stored content_type
    handler._api("GET", f"/artifacts/{rec1['version_id']}")
    code, body, ctype = sends[-1]
    assert (code, body) == (200, b"v1")
    assert ctype == store.version_meta(rec1["version_id"])["content_type"]

    # artifact_id → the LATEST version's bytes (GET on a bare id is bytes,
    # not JSON — only DELETE matches the JSON route above it)
    handler._api("GET", f"/artifacts/{rec1['artifact_id']}")
    assert sends[-1][:2] == (200, b"v2-longer")

    # filename → artifact_by_filename fallback, serving the live path
    handler._api("GET", "/artifacts/table.csv")
    assert sends[-1][:2] == (200, b"v2-longer")

    # ORDER: a registered artifact literally NAMED like rec1's version id
    # must not shadow it — version_id resolution wins over filename
    trap = st.workspace / rec1["version_id"]
    trap.write_text("filename-shadow")
    runner._register_file(st, trap, "c3", lambda e: None)
    handler._api("GET", f"/artifacts/{rec1['version_id']}")
    assert sends[-1][:2] == (200, b"v1")

    # the wart: unknown ident answers this bytes route with a JSON envelope
    handler._api("GET", "/artifacts/no-such-ident")
    code, body, ctype = sends[-1]
    assert code == 404
    assert json.loads(body) == {"error": "artifact not found"}
    assert ctype.startswith("application/json")


def test_preview_route_forces_html_content_type(tmp_path):
    """GET /preview/{ident} serves the same resolved bytes but ALWAYS stamps
    text/html, whatever the stored content_type says."""
    cfg, runner, store, fid, st = _runner_frame(tmp_path)
    handler, sends = _bytes_handler(cfg, runner)
    handler.headers = {}  # _route consults Origin/Cookie headers

    f = st.workspace / "report.md"
    f.write_text("# hi")
    rec = runner._register_file(st, f, "c1", lambda e: None)

    handler.path = f"/preview/{rec['artifact_id']}"
    handler._route("GET")
    code, body, ctype = sends[-1]
    assert (code, body) == (200, b"# hi")
    assert ctype == "text/html; charset=utf-8"


def test_upload_without_frame_id_stores_file_but_never_broadcasts(tmp_path):
    """POST /api/uploads with NO frame_id: the file lands under
    data_dir/uploads, the artifact row has no root_frame_id, and no
    artifact_created event is broadcast — only frame-scoped uploads notify."""
    cfg, runner, store, fid, st = _runner_frame(tmp_path)
    hub = _Hub()
    handler_cls = gateway_mod.make_handler(cfg, hub, runner)
    handler = object.__new__(handler_cls)

    res = handler._upload(
        {
            "filename": "loose.bin",
            "content_base64": base64.b64encode(b"data!").decode(),
        }
    )
    assert res["id"] == res["artifact_id"] and res["filename"] == "loose.bin"
    assert (cfg.data_dir / "uploads" / "loose.bin").read_bytes() == b"data!"

    a = store.get_artifact(res["artifact_id"])
    assert a["root_frame_id"] is None
    assert a["is_user_upload"] == 1
    assert [e for e in hub.events if e.get("type") == "artifact_created"] == []
    # the sessionless artifact still resolves and serves by id
    assert Path(store.resolve_artifact_path(res["artifact_id"])).read_bytes() == (
        b"data!"
    )


def test_body_malformed_json_treated_as_empty_dict(tmp_path):
    """_body() contract: an unparseable JSON body, an empty body, and a
    missing Content-Length all collapse to {} — route handlers never see a
    parse error and treat the request as field-less."""
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    handler = object.__new__(handler_cls)

    def _with(raw: bytes):
        handler.headers = {"Content-Length": str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        return handler._body()

    assert _with(b'{"a": 1}') == {"a": 1}  # valid JSON parses
    assert _with(b"this is not json") == {}  # malformed → {}
    assert _with(b"{truncated") == {}
    assert _with(b"") == {}  # Content-Length: 0 → {} without reading

    handler.headers = {}  # no Content-Length header at all
    handler.rfile = io.BytesIO(b'{"ignored": true}')
    assert handler._body() == {}


# --- runtime-env labelling + resume (frames.runtime_env) ---------------------
def test_kernel_id_labels_default_env_as_python_and_names_switches(tmp_path):
    """Phase 1: _kernel_id groups Notebook cells under a runtime segment —
    'python' for the default/base env, 'python — <env>' for a switched
    prebuilt env. This is the kernel_id stamped on every logged cell."""
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    st = runner._state("f-kid", "default")

    for default_like in (None, "python", "base"):
        st.env_name = default_like
        assert runner._kernel_id(st) == "python"

    st.env_name = "struct"
    assert runner._kernel_id(st) == "python — struct"
    # the syntax language is always python across the prebuilt envs
    assert runner._kernel_language(st) == "python"


def test_persisted_env_roundtrip_and_resume_seeds_new_session(monkeypatch, tmp_path):
    """Phase 2: the runtime env a session selected is pinned on
    frames.runtime_env so a resumed session (fresh kernel, same conversation)
    starts back in it. _persist_env writes it, _persisted_env reads it, and
    _resolve_env seeds a brand-new SessionState from it."""
    from openai4s.kernel import environments as envmod

    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    store = runner.store
    rid = store.new_frame(kind="turn", project_id="default", status="ready")

    # nothing pinned yet
    assert runner._persisted_env(rid) is None
    assert store.get_frame(rid)["runtime_env"] is None

    runner._persist_env(rid, "struct")
    assert runner._persisted_env(rid) == "struct"
    assert store.get_frame(rid)["runtime_env"] == "struct"

    # a fresh session for the same conversation resolves back into the pinned
    # env (kernel not spawned — _resolve_env only selects, it never launches)
    fake_env = SimpleNamespace(name="struct", interpreter="/usr/bin/python3")
    monkeypatch.setattr(
        envmod, "get_environment", lambda name: fake_env if name == "struct" else None
    )
    st = gateway_mod.SessionState(rid, "default", runner.workspace_for(rid))
    env = runner._resolve_env(st)
    assert env is fake_env
    assert st.env_name == "struct"


# --- read-only Notebook: the REPL routes are gated by cfg.notebook_repl ------
def test_notebook_repl_execute_route_gated_by_flag(monkeypatch, tmp_path):
    """Phase 3: POST /frames/{fid}/kernel/execute is refused 403 when
    cfg.notebook_repl is False (the default read-only Notebook) and never
    reaches runner.run_repl; with the flag on it proceeds to run_repl."""
    # disabled by default → 403 error envelope, run_repl short-circuited
    cfg = _cfg(tmp_path)
    assert cfg.notebook_repl is False
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    store = get_store(cfg.db_path)
    fid = store.new_frame(kind="turn", project_id="default", status="ready")
    called = []
    runner.run_repl = lambda *a, **k: called.append((a, k)) or {"ok": True}

    handler = object.__new__(gateway_mod.make_handler(cfg, _Hub(), runner))
    replies = []
    handler._query = lambda: {}
    handler._body = lambda: {"code": "print(1)"}
    handler._json = lambda obj, code=200: replies.append((code, obj))

    handler._api("POST", f"/frames/{fid}/kernel/execute")
    assert replies[-1][0] == 403
    assert "disabled" in replies[-1][1]["error"]
    assert called == []  # the gate fired before the kernel path

    # enabled (OPENAI4S_NOTEBOOK_REPL=1) → proceeds to runner.run_repl
    monkeypatch.setenv("OPENAI4S_NOTEBOOK_REPL", "1")
    cfg2 = _cfg(tmp_path)
    assert cfg2.notebook_repl is True
    runner2 = gateway_mod.SessionRunner(cfg2, _Hub())
    sentinel = {"cell": {"cell_index": 1}}
    hits = []
    runner2.run_repl = (
        lambda rfid, pid, code: hits.append((rfid, pid, code)) or sentinel
    )

    handler2 = object.__new__(gateway_mod.make_handler(cfg2, _Hub(), runner2))
    replies2 = []
    handler2._query = lambda: {}
    handler2._body = lambda: {"code": "print(2)"}
    handler2._json = lambda obj, code=200: replies2.append((code, obj))

    handler2._api("POST", f"/frames/{fid}/kernel/execute")
    assert hits == [(fid, "default", "print(2)")]
    assert replies2[-1] == (200, sentinel)


def test_resolve_env_does_not_clobber_pin_when_env_unresolvable(monkeypatch, tmp_path):
    """Regression: a transiently-unresolvable pinned env must fall back to base
    for THIS spawn WITHOUT overwriting frames.runtime_env — so a later spawn,
    once the env is discoverable again, still resumes the original selection."""
    from openai4s.kernel import environments as envmod

    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    store = runner.store
    rid = store.new_frame(kind="turn", project_id="default", status="ready")
    runner._persist_env(rid, "struct")  # a valid prior selection

    base_env = SimpleNamespace(name="base", interpreter="/usr/bin/python3")
    struct_env = SimpleNamespace(name="struct", interpreter="/usr/bin/python3")
    available = {"struct": False}

    def get_environment(name):
        if name == "base":
            return base_env
        if name == "struct" and available["struct"]:
            return struct_env
        return None

    # 'struct' momentarily undiscoverable (e.g. conda envs not yet scanned)
    monkeypatch.setattr(envmod, "get_environment", get_environment)
    st = gateway_mod.SessionState(rid, "default", runner.workspace_for(rid))
    env = runner._resolve_env(st)

    assert env is base_env
    assert st.env_name == "base"  # runs on base for this spawn
    assert store.get_frame(rid)["runtime_env"] == "struct"  # pin PRESERVED

    # Retry the desired pin on a later spawn in this SAME SessionState. The
    # active base fallback must never become the new desired environment.
    available["struct"] = True
    env = runner._resolve_env(st)
    assert env is struct_env
    assert st.env_name == "struct"
    assert store.get_frame(rid)["runtime_env"] == "struct"


def test_restart_respawns_when_active_env_is_only_a_pin_fallback(monkeypatch, tmp_path):
    """Restart must re-resolve desired!=active instead of reusing base Python."""
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    st = runner._state("f-restart-pin", "default")
    calls = []

    class FallbackKernel:
        generation = 1

        def shutdown(self):
            calls.append("shutdown")

        def restart(self):
            calls.append("restart")

    st.kernel = FallbackKernel()
    st.env_name = "base"
    st.desired_env = "struct"

    def spawn(state):
        calls.append("spawn")
        state.env_name = state.desired_env
        state.kernel = SimpleNamespace(generation=2)

    monkeypatch.setattr(runner, "_spawn_kernel", spawn)
    result = runner.restart_kernel(st.root_frame_id, st.project_id)

    assert calls == ["shutdown", "spawn"]
    assert st.env_name == "struct"
    assert result["generation"] == 2


def test_tool_batch_applies_env_switch_before_following_bash(monkeypatch, tmp_path):
    """env_use then bash in one reply must use the rebuilt dispatcher."""
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    st = runner._state("f-env-batch", "default")
    st.messages = [{"role": "system", "content": "sys"}]
    calls = []

    class Dispatcher:
        last_output = None

        def __init__(self, label):
            self.label = label

        def __call__(self, method, args):
            calls.append((self.label, method))
            if method == "env_use":
                st.pending_env = args[0]["name"]
            return {"ok": True}

    st.dispatcher = Dispatcher("old")
    replies = iter(
        [
            '```tool\n{"name":"env_use","arguments":{"name":"struct"}}\n```\n'
            '```tool\n{"name":"bash","arguments":{"command":"python -V"}}\n```',
            "```python\nhost.submit_output({'ok': True}, ['done'])\n```",
        ]
    )

    def fake_chat(messages, cfg, on_delta=None, **kwargs):
        return {"content": next(replies), "usage": {}}

    def apply_pending(state, emit):
        calls.append(("apply", state.pending_env))
        state.env_name = state.pending_env
        state.pending_env = None
        state.dispatcher = Dispatcher("new")

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_apply_pending_env", apply_pending)

    def fake_exec(state, code, origin, emit, stream=True):
        state.dispatcher.last_output = {"output": {"ok": True}}
        return {"result": {"stdout": "", "stderr": "", "error": None}}

    monkeypatch.setattr(runner, "_execute_and_log", fake_exec)

    runner._loop(st, lambda event: None, [])

    assert calls == [("old", "env_use"), ("apply", "struct"), ("new", "bash")]


def test_env_summary_exposes_canonical_kernel_id(tmp_path):
    """Regression: kernel_status.env carries a canonical kernel_id computed by
    the SAME rule the server labels persisted cells with, so the frontend labels
    live cells identically instead of re-deriving from the raw env name."""
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    st = runner._state("f-envsum", "default")

    st.env_name = "python"
    assert runner._env_summary(st)["kernel_id"] == "python"
    st.env_name = "struct"
    summary = runner._env_summary(st)
    assert summary["name"] == "struct"
    assert summary["kernel_id"] == "python — struct"  # matches _kernel_id(st)
    assert runner._kernel_id(st) == summary["kernel_id"]


def test_prose_streamer_hides_nested_tool_example_inside_python_cell():
    """Live prose and persisted prose use the same nesting-aware fence view."""
    inner = '```tool\n{"name": "list_dir", "arguments": {}}\n```\n'
    for outer, info in (("```", "python"), ("````", "python"), ("~~~", "text")):
        events = []
        streamer = gateway_mod._ProseStreamer(events.append, "f-stream")
        reply = (
            "Before.\n"
            + outer
            + info
            + "\nreadme = '''\n"
            + inner
            + "'''\nprint(readme)\n"
            + outer
            + "\nAfter."
        )
        for i in range(0, len(reply), 7):
            streamer.feed(reply[i : i + 7])
        streamer.finalize()

        visible = "".join(e["chunk"] for e in events)
        assert visible == "Before.\nAfter."
        assert "list_dir" not in visible


def test_kernel_install_route_is_not_gated_by_notebook_repl(tmp_path):
    """Regression: prebuilt-env package install (Customize → Compute) is a
    separate affordance from the code REPL and must stay reachable in the
    default read-only build — it must NOT return 403."""
    cfg = _cfg(tmp_path)
    assert cfg.notebook_repl is False
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    store = get_store(cfg.db_path)
    fid = store.new_frame(kind="turn", project_id="default", status="ready")
    hits = []
    runner.install_packages = lambda pkgs, **k: hits.append((pkgs, k)) or {
        "ok": True,
        "installed": pkgs,
    }

    handler = object.__new__(gateway_mod.make_handler(cfg, _Hub(), runner))
    replies = []
    handler._query = lambda: {}
    handler._body = lambda: {"packages": ["seaborn"]}
    handler._json = lambda obj, code=200: replies.append((code, obj))

    handler._api("POST", f"/frames/{fid}/kernel/install")
    assert replies[-1][0] == 200  # not 403
    assert hits and hits[0][0] == ["seaborn"]
