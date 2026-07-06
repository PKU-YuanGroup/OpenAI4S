import threading
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


def test_gateway_plain_answer_completes_without_code(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    hub = _Hub()
    runner = gateway_mod.SessionRunner(cfg, hub)
    store = get_store(cfg.db_path)
    fid = store.new_frame(kind="turn", project_id="default", status="ready")
    calls = []

    def fake_chat(messages, cfg, on_delta=None, **kwargs):
        calls.append(messages)
        if on_delta:
            on_delta("Short answer.")
        return {"content": "Short answer.", "usage": {}}

    def fake_ensure(st):
        st.dispatcher = SimpleNamespace(last_output=None)
        st.messages = [{"role": "system", "content": "sys"}]
        st.booted = True

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_ensure_kernel", fake_ensure)
    # the background title-summary chat would also land in `calls` and race the
    # count; it is orthogonal to the plain-answer path under test
    monkeypatch.setattr(runner, "_spawn_title_summary", lambda *a, **k: None)

    result = runner.run_message(fid, "default", "What is OpenAI4S?")

    assert result["status"] == "completed"
    assert len(calls) == 1
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
    reply (no code, no submit_output) is pushed back on — bounded at 3 nudges —
    instead of silently ending the turn."""
    cfg = _cfg(tmp_path)
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

    assert result["status"] == "completed"
    # protocol appended to the in-conversation user message (not the stored one)
    assert "[EXPLORE MODE" in calls[0][-1]["content"]
    assert store.list_messages(fid)[0]["content"] == "探索地球磁场如何演化"
    # 1 initial call + 3 bounded nudges, then the loop gives up gracefully
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


def test_midtask_prose_stall_is_gated_and_nudged(monkeypatch, tmp_path):
    """Normal mode: after a code cell ran, a prose-only reply that concludes
    nothing actionable gets a stall nudge (conclusion gate says NO) instead of
    ending the turn half-done."""
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    store = get_store(cfg.db_path)
    fid = store.new_frame(kind="turn", project_id="default", status="ready")
    replies = iter(
        [
            "Running step 1.\n```python\nprint('x')\n```",
            "Now let me look into the data files.",  # stall (gate -> NO)
            "Done: the answer is 42, analysis complete.",  # conclusion (gate -> YES)
        ]
    )
    gate_calls = []

    def fake_chat(messages, cfg, on_delta=None, **kwargs):
        return {"content": next(replies), "usage": {}}

    def fake_ensure(st):
        st.dispatcher = SimpleNamespace(last_output=None)
        st.messages = [{"role": "system", "content": "sys"}]
        st.booted = True

    def fake_exec(st, code, origin, emit, stream=True):
        return {"result": {"stdout": "x\n", "stderr": "", "error": None}}

    def fake_gate(prose, llm_cfg):
        gate_calls.append(prose)
        return "42" in prose  # first prose stalls, second concludes

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_ensure_kernel", fake_ensure)
    monkeypatch.setattr(runner, "_execute_and_log", fake_exec)
    monkeypatch.setattr(runner, "_prose_concludes", fake_gate)
    monkeypatch.setattr(runner, "_spawn_title_summary", lambda *a, **k: None)

    result = runner.run_message(fid, "default", "analyze something")

    assert result["status"] == "completed"
    assert len(gate_calls) == 2  # gated both prose-only replies
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
        return {"result": {"stdout": "a\n", "stderr": "", "error": None}}

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_ensure_kernel", fake_ensure)
    monkeypatch.setattr(runner, "_execute_and_log", fake_exec)
    # gate says "concludes" so, absent the warning, turn 1 would end the run
    monkeypatch.setattr(runner, "_prose_concludes", lambda p, c: True)
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
