import base64
import hashlib
import io
import json
import re
import struct
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

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


def test_a_fresh_daemon_seeds_no_demo_and_the_variable_now_opts_in(monkeypatch):
    """The default was `"1"`, and on a fresh data dir that meant: bind the port,
    then start a Python kernel, execute six cells, call the UniProt and RCSB
    REST APIs, spawn the bundled MCP connector and write four artifacts --
    before the user had typed anything. Every one of those is something this
    application otherwise asks permission for.

    The variable keeps its name and reverses sense, which is the cheap part.
    The load-bearing part is that a fresh boot now does *nothing*.
    """
    monkeypatch.delenv("OPENAI4S_SEED_DEMO", raising=False)
    assert gateway_mod._demo_seed_enabled() is False
    for value in ("0", "false", "NO", "off", "", "  "):
        monkeypatch.setenv("OPENAI4S_SEED_DEMO", value)
        assert gateway_mod._demo_seed_enabled() is False
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv("OPENAI4S_SEED_DEMO", value)
        assert gateway_mod._demo_seed_enabled() is True


def test_a_fresh_boot_starts_no_kernel_and_executes_no_cell(tmp_path, monkeypatch):
    """The behavioural half of the test above.

    Asserting the flag is off proves the flag is off. This asserts the thing
    the flag was gating: build the server on a brand-new data dir and let the
    background threads have a moment, and no cell ran, no kernel spawned and no
    artifact exists. Driven through `build_app_server` rather than the seeder,
    because the defect was in the wiring, not in `_seed_demo_session`.
    """
    monkeypatch.delenv("OPENAI4S_SEED_DEMO", raising=False)
    monkeypatch.setenv("OPENAI4S_REQUIRE_TOKEN", "0")
    cfg = _cfg(tmp_path)
    cfg.port = 0  # ask the OS for a free port; do not fight a live daemon

    executed: list[object] = []
    monkeypatch.setattr(
        gateway_mod.SessionRunner,
        "run_repl",
        lambda self, *a, **k: executed.append(a),
    )
    spawned: list[object] = []
    monkeypatch.setattr(
        gateway_mod.SessionRunner,
        "_spawn_kernel",
        lambda self, st: spawned.append(st),
    )

    httpd = gateway_mod.build_app_server(cfg)
    try:
        time.sleep(0.4)  # a seeding thread would have started by now
        store = get_store(cfg.db_path)
        assert executed == [], "a fresh boot executed a cell"
        assert spawned == [], "a fresh boot spawned a kernel"
        assert store.list_artifacts({}) == [], "a fresh boot created an artifact"
        roots = store.browse_frames(project_id="proj_example", roots_only=True)
        assert roots == [], "a fresh boot created a session"
    finally:
        httpd.server_close()
        httpd.runner.close()


def test_the_example_seed_is_on_demand_idempotent_and_single_flight(
    tmp_path, monkeypatch
):
    """`_seed_demo_session` is idempotent by session name, which stops it
    duplicating the example but not two concurrent requests both *starting* it:
    the name check and the insert are not one transaction, and the seed runs for
    as long as six live cells take. Two clicks would have run twelve cells and
    two sets of API calls.
    """
    monkeypatch.delenv("OPENAI4S_SEED_DEMO", raising=False)
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())

    release = threading.Event()
    runs: list[int] = []

    def _slow_seed(_cfg, _runner):
        runs.append(1)
        release.wait(5)

    monkeypatch.setattr(gateway_mod, "_seed_demo_session", _slow_seed)
    try:
        assert runner.example_seed.start(cfg, runner) is True
        for _ in range(50):  # wait for the thread to actually enter the seed
            if runs:
                break
            time.sleep(0.01)
        assert runner.example_seed.running() is True
        # The second caller is refused, and refused distinguishably: `started`
        # false with `running` true is "someone else is doing it", which the UI
        # shows differently from a failure.
        assert runner.example_seed.start(cfg, runner) is False
        assert runs == [1]
    finally:
        release.set()
        runner.close()


def test_the_example_seed_route_reports_state_and_surfaces_its_error(
    tmp_path, monkeypatch
):
    """A background seed that fails has nowhere to report to, so it reports
    here. Without this the UI's only signal is that the example never appears,
    which is indistinguishable from a slow network."""
    monkeypatch.delenv("OPENAI4S_SEED_DEMO", raising=False)
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    try:
        handler = object.__new__(handler_cls)
        handler.path = "/api/v1/example/session"
        seen: list[tuple[dict, int]] = []
        handler._json = lambda obj, code=200: seen.append((obj, code))
        handler._body = lambda: {}

        handler._api("GET", "/example/session")
        body, code = seen[-1]
        assert code == 200
        assert body["seeded"] is False and body["running"] is False
        assert body["started"] is False  # a GET never starts anything
        assert body["seeds_at_startup"] is False

        # An unconfirmed POST refuses and, more to the point, seeds nothing.
        # This is what keeps a generic surface driver -- the contract capture,
        # a route-coverage sweep -- from executing six cells and calling two
        # external APIs just by enumerating verbs. Asserting the 400 alone
        # would not prove it: the check has to happen *before* the start.
        ran: list[int] = []
        monkeypatch.setattr(gateway_mod, "_seed_demo_session", lambda *a: ran.append(1))
        with pytest.raises(gateway_mod.GatewayError) as refused:
            handler._api("POST", "/example/session")
        assert refused.value.code == 400
        assert refused.value.error_code == "confirmation_required"
        time.sleep(0.1)
        assert ran == [], "an unconfirmed POST started the example seed"

        def _boom(_cfg, _runner):
            raise RuntimeError("uniprot unreachable")

        monkeypatch.setattr(gateway_mod, "_seed_demo_session", _boom)
        handler._body = lambda: {"confirm": True}
        handler._api("POST", "/example/session")
        assert seen[-1][0]["started"] is True
        for _ in range(200):
            if runner.example_seed.last_error():
                break
            time.sleep(0.01)
        handler._api("GET", "/example/session")
        assert "uniprot unreachable" in (seen[-1][0]["error"] or "")
    finally:
        runner.close()


def test_the_example_seed_route_reports_the_already_seeded_state(tmp_path, monkeypatch):
    """The idempotent path the API docs describe and nothing exercised.

    `POST /example/session` on an install that already has the example must not
    start anything: `existing is not None` skips `start()` entirely, so the
    response reports the frame that is there and whatever error the last attempt
    left behind. Worth a test on its own -- it is the difference between a
    second click costing nothing and a second click running six cells.

    It also closes a hole that surfaced somewhere unexpected. The frozen
    `POST /example/session [ok]` shape in `docs/response-schemas.json` was built
    from the single observation the suite happened to make, and that observation
    sits immediately after `start()` has cleared `_last_error` and spawned a
    thread that may or may not have failed yet. Both threads then contend for
    the same lock, so `error` was captured as `string` on Linux/CI and `null` on
    macOS -- a scheduler outcome published as a contract, in a file whose whole
    claim is that it describes the API. The field is `str | None` on both verbs
    (one dict literal, fed by `last_error()`), and so is `frame_id`
    (`str` once seeded, `null` before). The two observations below pin both
    halves of each, deterministically, without depending on which thread wins.
    """
    monkeypatch.delenv("OPENAI4S_SEED_DEMO", raising=False)
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    seen: list[tuple[dict, int]] = []

    def _handler_for(active_runner):
        handler = object.__new__(gateway_mod.make_handler(cfg, _Hub(), active_runner))
        handler.path = "/api/v1/example/session"
        handler._json = lambda obj, code=200: seen.append((obj, code))
        handler._body = lambda: {"confirm": True}
        return handler

    try:
        # A real error, recorded through the real state object.
        def _boom(_cfg, _runner):
            raise RuntimeError("uniprot unreachable")

        monkeypatch.setattr(gateway_mod, "_seed_demo_session", _boom)
        handler = _handler_for(runner)
        handler._api("POST", "/example/session")
        for _ in range(200):
            if runner.example_seed.last_error():
                break
            time.sleep(0.01)
        assert runner.example_seed.last_error()

        # The example exists now. Written the way the seeder writes it -- a real
        # store row, not a patched lookup, so the response is still the real
        # handler's answer and the shape it publishes stays honest.
        store = get_store(cfg.db_path)
        fid = store.new_frame(
            kind="turn", project_id="proj_example", status="done", model=cfg.llm.model
        )
        store.update_frame(fid, name=gateway_mod._DEMO_SESSION_NAME)

        handler._api("POST", "/example/session")
        body, code = seen[-1]
        assert code == 200
        assert body["seeded"] is True
        assert body["started"] is False, "a POST on a seeded install started a run"
        assert body["frame_id"] == fid
        assert "uniprot unreachable" in (body["error"] or "")

        # Same seeded install, a runner that has never failed: the null half of
        # the same contract, and the one CI could not observe.
        fresh = gateway_mod.SessionRunner(cfg, _Hub())
        try:
            for verb in ("GET", "POST"):
                _handler_for(fresh)._api(verb, "/example/session")
                body, code = seen[-1]
                assert code == 200
                assert body["seeded"] is True
                assert body["started"] is False
                assert body["frame_id"] == fid
                assert body["error"] is None
        finally:
            fresh.close()
    finally:
        runner.close()


def test_ws_resume_buffer_replaces_notebook_drafts_and_keeps_live_cell_events():
    hub = gateway_mod.WSHub()
    root = "root-draft-replay"
    hub.broadcast(root, {"type": "text_reset", "frame_id": root})
    hub.broadcast(
        root,
        {
            "type": "notebook_cell_draft",
            "frame_id": root,
            "draft_id": "draft-1",
            "revision": 1,
            "source": "x =",
        },
    )
    hub.broadcast(
        root,
        {
            "type": "notebook_cell_draft",
            "frame_id": root,
            "draft_id": "draft-1",
            "revision": 2,
            "source": "x = 1",
        },
    )
    hub.broadcast(
        root,
        {"type": "notebook_cell_start", "frame_id": root, "cell_id": "cell-1"},
    )
    hub.broadcast(
        root,
        {
            "type": "notebook_cell_chunk",
            "frame_id": root,
            "cell_id": "cell-1",
            "chunk": "1\n",
        },
    )

    events = hub._live[root]["events"]
    drafts = [event for event in events if event["type"] == "notebook_cell_draft"]
    assert len(drafts) == 1
    assert drafts[0]["revision"] == 2
    assert drafts[0]["source"] == "x = 1"
    assert {event["type"] for event in events} >= {
        "notebook_cell_start",
        "notebook_cell_chunk",
    }


def test_ws_resume_coalesces_notebook_and_activity_stdout_independently():
    hub = gateway_mod.WSHub()
    root = "root-chunk-coalesce"
    hub.broadcast(root, {"type": "text_reset", "frame_id": root})
    hub.broadcast(
        root,
        {
            "type": "notebook_cell_start",
            "frame_id": root,
            "producing_cell_id": "cell-1",
        },
    )
    # The activity header is not a stdout echo and must remain replayable.
    hub.broadcast(
        root,
        {
            "type": "text_chunk",
            "frame_id": root,
            "block_type": "tool",
            "producing_cell_id": "cell-1",
            "cell_index": 1,
            "chunk": "cell header\n",
        },
    )
    for chunk in ("alpha", " beta"):
        hub.broadcast(
            root,
            {
                "type": "notebook_cell_chunk",
                "frame_id": root,
                "producing_cell_id": "cell-1",
                "stream": "stdout",
                "chunk": chunk,
            },
        )
        hub.broadcast(
            root,
            {
                "type": "text_chunk",
                "frame_id": root,
                "block_type": "tool",
                "producing_cell_id": "cell-1",
                "chunk": chunk,
            },
        )

    # A semantic event boundary starts a fresh pair; stdout is never moved
    # across a step merely to improve compression.
    hub.broadcast(root, {"type": "step", "frame_id": root, "ordinal": 1})
    for event_type in ("notebook_cell_chunk", "text_chunk"):
        event = {
            "type": event_type,
            "frame_id": root,
            "producing_cell_id": "cell-1",
            "chunk": " gamma",
        }
        if event_type == "notebook_cell_chunk":
            event["stream"] = "stdout"
        else:
            event["block_type"] = "tool"
        hub.broadcast(root, event)

    events = hub._live[root]["events"]
    chunks = [event for event in events if event["type"] == "notebook_cell_chunk"]
    assert [event["chunk"] for event in chunks] == ["alpha beta", " gamma"]
    tool_text = [
        event
        for event in events
        if event["type"] == "text_chunk" and event.get("block_type") == "tool"
    ]
    assert [event["chunk"] for event in tool_text] == [
        "cell header\n",
        "alpha beta",
        " gamma",
    ]


def test_ws_stray_state_does_not_create_phantom_live_buffer_and_repl_is_bounded():
    hub = gateway_mod.WSHub()
    root = "root-stray-state"

    for event in (
        {"type": "kernel_status", "frame_id": root, "status": "started"},
        {"type": "frame_update", "frame_id": root, "status": "updated"},
        {"type": "frame_update", "frame_id": root, "status": "ready"},
    ):
        hub.broadcast(root, event)
    assert root not in hub._live
    assert hub.is_running(root) is False

    hub.broadcast(
        root,
        {
            "type": "notebook_cell_start",
            "frame_id": root,
            "producing_cell_id": "repl-cell",
            "origin": "user",
        },
    )
    assert hub.is_running(root) is True
    hub.broadcast(
        root,
        {
            "type": "notebook_cell_finished",
            "frame_id": root,
            "producing_cell_id": "repl-cell",
            "origin": "user",
            "status": "ok",
        },
    )
    assert hub.is_running(root) is False

    # If preparation fails after start but before a Cell finish projection, the
    # execution coordinator's terminal state still closes the REPL window.
    hub.broadcast(
        root,
        {
            "type": "notebook_cell_start",
            "frame_id": root,
            "producing_cell_id": "repl-failed",
            "origin": "user",
        },
    )
    assert hub.is_running(root) is True
    hub.broadcast(
        root,
        {
            "type": "execution_state",
            "frame_id": root,
            "execution_id": "repl-failed",
            "status": "failed",
        },
    )
    assert hub.is_running(root) is False


def test_ws_resume_trim_preserves_active_cell_start_before_tail_chunks():
    hub = gateway_mod.WSHub()
    hub._BUFFER_CAP = 6
    root = "root-trim-active-cell"
    hub.broadcast(root, {"type": "text_reset", "frame_id": root})
    hub.broadcast(
        root,
        {
            "type": "notebook_cell_start",
            "frame_id": root,
            "producing_cell_id": "cell-live",
        },
    )
    for ordinal in range(12):
        hub.broadcast(
            root,
            {"type": "step", "frame_id": root, "ordinal": ordinal},
        )
    hub.broadcast(
        root,
        {
            "type": "notebook_cell_chunk",
            "frame_id": root,
            "producing_cell_id": "cell-live",
            "chunk": "still running",
        },
    )

    events = hub._live[root]["events"]
    assert len(events) <= hub._BUFFER_CAP
    types = [event["type"] for event in events]
    assert types[0] == "text_reset"
    assert "notebook_cell_start" in types
    assert "notebook_cell_chunk" in types
    assert types.index("notebook_cell_start") < types.index("notebook_cell_chunk")


def test_ws_subscribe_replay_enqueue_is_atomic_with_live_broadcast():
    hub = gateway_mod.WSHub()
    root = "root-atomic-replay"
    hub.broadcast(root, {"type": "text_reset", "frame_id": root})
    hub.broadcast(root, {"type": "text_chunk", "frame_id": root, "chunk": "old"})

    class BlockingConnection:
        def __init__(self):
            self.alive = True
            self.subs = set()
            self.events = []
            self.replay_started = threading.Event()
            self.release_replay = threading.Event()

        def send_json(self, event):
            self.events.append(dict(event))
            if event.get("type") == "replay_begin":
                self.replay_started.set()
                assert self.release_replay.wait(2)

    conn = BlockingConnection()
    hub.add(conn)
    subscribe = threading.Thread(target=hub.subscribe, args=(root, conn))
    subscribe.start()
    assert conn.replay_started.wait(2)

    live = threading.Thread(
        target=hub.broadcast,
        args=(root, {"type": "text_chunk", "frame_id": root, "chunk": "new"}),
    )
    live.start()
    # The live producer is waiting on the replay's enqueue transaction.
    live.join(0.05)
    assert live.is_alive()
    conn.release_replay.set()
    subscribe.join(2)
    live.join(2)
    assert not subscribe.is_alive()
    assert not live.is_alive()

    assert [event.get("type") for event in conn.events] == [
        "replay_begin",
        "text_reset",
        "text_chunk",
        "replay_end",
        "text_chunk",
    ]
    assert [
        event.get("chunk") for event in conn.events if event.get("type") == "text_chunk"
    ] == ["old", "new"]


def test_ws_outbound_queue_covers_a_complete_resume_envelope():
    assert gateway_mod.WSConnection._QUEUE_CAP >= (
        gateway_mod.WSHub._BUFFER_CAP + gateway_mod._WS_REPLAY_ENVELOPE_EVENTS
    )
    assert gateway_mod.WSConnection._QUEUE_BYTE_CAP >= (
        gateway_mod.WSHub._BUFFER_BYTE_CAP + gateway_mod._WS_REPLAY_QUEUE_BYTE_HEADROOM
    )


def test_ws_connection_byte_budget_drops_and_clears_queued_accounting():
    class PausedConnection(gateway_mod.WSConnection):
        def __init__(self):
            self.release_writer = threading.Event()
            super().__init__(io.BytesIO())

        def _drain(self):
            assert self.release_writer.wait(2)
            super()._drain()

    conn = PausedConnection()
    conn._QUEUE_BYTE_CAP = 10
    conn._enqueue(b"123456")
    conn._enqueue(b"7890")
    assert conn.alive is True
    assert conn._queued_bytes == 10

    conn._enqueue(b"!")
    assert conn.alive is False
    assert conn._queued_bytes == 0
    conn.release_writer.set()
    conn._writer.join(2)
    assert not conn._writer.is_alive()


def test_ws_connection_counts_a_socket_blocked_frame_against_byte_budget():
    class BlockingWriter:
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()

        def write(self, frame):
            self.started.set()
            assert self.release.wait(2)
            return len(frame)

        def flush(self):
            return None

    writer = BlockingWriter()
    conn = gateway_mod.WSConnection(writer)
    conn._QUEUE_BYTE_CAP = 10
    conn._enqueue(b"123456")
    assert writer.started.wait(2)
    assert conn._queued_bytes == 6

    # The six bytes currently blocked in write() still consume the budget.
    conn._enqueue(b"78901")
    assert conn.alive is False
    assert conn._queued_bytes == 0
    writer.release.set()
    conn._writer.join(2)
    assert not conn._writer.is_alive()


def test_ws_resume_byte_budget_keeps_reset_active_start_and_latest_tail():
    hub = gateway_mod.WSHub()
    hub._BUFFER_CAP = 100
    hub._BUFFER_BYTE_CAP = 900
    root = "root-byte-trim"
    hub.broadcast(root, {"type": "text_reset", "frame_id": root})
    hub.broadcast(
        root,
        {
            "type": "notebook_cell_start",
            "frame_id": root,
            "producing_cell_id": "cell-byte",
        },
    )
    for sequence in range(6):
        hub.broadcast(
            root,
            {
                "type": "text_chunk",
                "frame_id": root,
                "block_type": "text",
                "sequence": sequence,
                "chunk": str(sequence) + ("x" * 300),
            },
        )

    buf = hub._live[root]
    assert buf["event_bytes"] == sum(
        hub._event_wire_size(event) for event in buf["events"]
    )
    assert buf["event_bytes"] <= hub._BUFFER_BYTE_CAP
    assert len(buf["event_sizes"]) == len(buf["events"])
    assert len(buf["events"]) < 8  # count cap=100; only byte pressure trimmed it
    types = [event["type"] for event in buf["events"]]
    assert types[0] == "text_reset"
    assert "notebook_cell_start" in types
    assert types.index("notebook_cell_start") < len(types) - 1
    assert buf["events"][-1]["sequence"] == 5


def test_ws_live_frame_limit_is_hard_even_when_every_buffer_is_running():
    hub = gateway_mod.WSHub()
    hub._MAX_LIVE_FRAMES = 2
    for root in ("root-live-1", "root-live-2", "root-live-3"):
        hub.broadcast(root, {"type": "text_reset", "frame_id": root})

    assert list(hub._live) == ["root-live-2", "root-live-3"]
    assert all(buf["running"] for buf in hub._live.values())
    assert hub.is_running("root-live-1") is False


def _auth_headers(cfg, extra: dict | None = None) -> dict:
    """Headers a client presents now that the token gate is on by default.

    Tests that drive `_route` go through the gate; tests that call `_api`
    directly do not. Rather than each remembering that distinction, this makes
    the credential explicit wherever `_route` is used -- which is also what a
    real client does.
    """
    from openai4s.server import local_auth

    headers = {local_auth.TOKEN_HEADER: local_auth.load_or_mint(cfg.data_dir)}
    headers.update(extra or {})
    return headers


def _cfg(tmp_path):
    return Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=3,
    )


def test_permission_resolution_does_not_create_live_turn_buffer():
    hub = gateway_mod.WSHub()
    hub.broadcast(
        "root-restart",
        {
            "type": "permission_resolved",
            "frame_id": "root-restart",
            "decision_id": "perm-restart",
            "resolution_context": "after_restart",
            "requires_continue": True,
        },
    )
    assert hub.is_running("root-restart") is False


def test_completion_nudge_falls_back_to_code_for_no_tool_endpoint(monkeypatch):
    monkeypatch.setattr(
        gateway_mod,
        "get_model_capabilities",
        lambda *args, **kwargs: SimpleNamespace(tool_calling=False),
    )

    nudge = gateway_mod._submit_nudge_for(
        SimpleNamespace(
            provider="local-openai",
            model="local-model",
            base_url="http://127.0.0.1:11434/v1",
        )
    )

    assert "without native tool calling" in nudge
    assert "host.submit_output" in nudge
    assert "finalize_response" not in nudge


def test_gateway_plain_answer_is_nudged_to_structured_finalize_without_kernel(
    monkeypatch, tmp_path
):
    cfg = _cfg(tmp_path)
    hub = _Hub()
    runner = gateway_mod.SessionRunner(cfg, hub)
    store = get_store(cfg.db_path)
    fid = store.new_frame(kind="turn", project_id="default", status="ready")
    calls = []

    finalize_arguments = {
        "summary": "Short answer.",
        "completion_bullets": ["Answered the question"],
    }
    finalize_call = {
        "id": "final-plain-answer",
        "wire_id": "final-plain-answer",
        "name": "finalize_response",
        "ordinal": 0,
        "raw_arguments": json.dumps(finalize_arguments),
        "arguments": finalize_arguments,
        "parse_error": None,
        "provider_meta": {},
    }
    replies = iter(
        [
            {"content": "Short answer.", "usage": {}},
            {
                "content": "",
                "tool_calls": [finalize_call],
                "assistant_message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [finalize_call],
                },
                "usage": {},
            },
        ]
    )

    def fake_chat(messages, cfg, on_delta=None, **kwargs):
        calls.append(messages)
        reply = next(replies)
        if on_delta and reply.get("content"):
            on_delta(reply["content"])
        return reply

    def fake_ensure(st):
        st.dispatcher = SimpleNamespace(last_output=None)
        st.messages = [{"role": "system", "content": "sys"}]
        st.booted = True

    def fake_exec(*args, **kwargs):
        raise AssertionError(f"conversational finalization started a Cell: {args!r}")

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_ensure_runtime", fake_ensure)
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
    nudge = next(
        m["content"]
        for m in calls[1]
        if m["role"] == "user" and "Prose is not a completion signal" in m["content"]
    )
    assert "finalize_response" in nudge
    assert "do NOT start a Python/R kernel merely to finish" in nudge
    assert runner._state(fid, "default").kernel is None
    messages = store.list_messages(fid)
    assert [m["role"] for m in messages] == ["user", "assistant", "assistant"]
    assert messages[-2]["content"] == "Short answer."
    assert "Answered the question" in messages[-1]["content"]
    assert hub.events[-1]["type"] == "frame_update"
    assert hub.events[-1]["status"] == "completed"


def test_gateway_projects_submit_only_result_as_live_and_persisted_final_message(
    monkeypatch, tmp_path
):
    cfg = _cfg(tmp_path)
    hub = _Hub()
    runner = gateway_mod.SessionRunner(cfg, hub)
    store = get_store(cfg.db_path)
    fid = store.new_frame(kind="turn", project_id="default", status="ready")

    def fake_ensure(st):
        st.dispatcher = SimpleNamespace(last_output=None)
        st.messages = [{"role": "system", "content": "sys"}]
        st.booted = True

    def finish_without_model_prose(st, emit, visible):
        del emit, visible
        st.dispatcher.last_output = {
            "output": {"summary": "已完成真实数据分析。"},
            "completion_bullets": ["生成了结果表", "总结了关键发现"],
        }
        st.last_model_prose = ""
        return "submitted"

    monkeypatch.setattr(runner, "_ensure_runtime", fake_ensure)
    monkeypatch.setattr(runner, "_loop", finish_without_model_prose)
    monkeypatch.setattr(runner, "_spawn_title_summary", lambda *a, **k: None)

    result = runner.run_message(fid, "default", "分析这些真实数据")

    assert result["status"] == "completed"
    messages = store.list_messages(fid)
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert "已完成真实数据分析" in messages[-1]["content"]
    assert "生成了结果表" in messages[-1]["content"]
    assert "no textual response" not in messages[-1]["content"]
    final_text_index = max(
        index
        for index, event in enumerate(hub.events)
        if event.get("type") == "text_chunk"
        and "已完成真实数据分析" in event.get("chunk", "")
    )
    terminal_index = max(
        index
        for index, event in enumerate(hub.events)
        if event.get("type") == "frame_update" and event.get("status") == "completed"
    )
    assert final_text_index < terminal_index


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
        # What the item was ACCEPTED under, carried from the request thread. The
        # freeze used to be written only to the frame, whose pin the rebind route
        # rewrites by design -- so a queued follow-up could adopt it after 202.
        frozen_binding=None,
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


def test_restore_version_appends_fresh_current_version(tmp_path):
    cfg, runner, store, fid, st = _runner_frame(tmp_path)
    f = st.workspace / "fig.txt"
    f.write_text("ALPHA")
    rec1 = runner._register_file(st, f, "c1", lambda e: None)
    f.write_text("BETA")
    rec2 = runner._register_file(st, f, "c2", lambda e: None)

    source_before = store.version_meta(rec1["version_id"])
    res = runner.restore_version(rec1["artifact_id"], rec1["version_id"])
    assert res.get("ok")
    restored_version_id = res["version_id"]
    assert restored_version_id not in {rec1["version_id"], rec2["version_id"]}
    assert res["restored_from_version_id"] == rec1["version_id"]
    assert res["artifact"]["version_id"] == restored_version_id
    # the live workspace file is reverted so the agent sees the old content
    assert f.read_text() == "ALPHA"
    # Restore is append-only: a fresh version becomes current, never the old row.
    a = store.get_artifact(rec1["artifact_id"])
    assert a["latest_version_id"] == restored_version_id
    assert Path(store.resolve_artifact_path(rec1["artifact_id"])).read_text() == "ALPHA"
    assert store.version_meta(rec1["version_id"]) == source_before
    assert store.lineage_edges_for(restored_version_id, "up") == [rec1["version_id"]]
    assert len(store.list_versions(rec1["artifact_id"])) == 3
    # Both historical versions still serve their own immutable bytes.
    assert Path(store.resolve_artifact_path(rec1["version_id"])).read_text() == "ALPHA"
    assert Path(store.resolve_artifact_path(rec2["version_id"])).read_text() == "BETA"

    # a nonexistent / foreign version_id is rejected, not silently applied
    assert runner.restore_version(rec1["artifact_id"], "v-nope").get("error")
    g = st.workspace / "other.txt"
    g.write_text("G")
    other = runner._register_file(st, g, "c3", lambda e: None)
    assert runner.restore_version(rec1["artifact_id"], other["version_id"]).get("error")


def test_restore_route_returns_fresh_identity_and_surfaces_verification_error(
    tmp_path,
):
    cfg, runner, store, fid, st = _runner_frame(tmp_path)
    hub = _Hub()
    handler_cls = gateway_mod.make_handler(cfg, hub, runner)
    handler = object.__new__(handler_cls)
    replies = []
    handler._query = lambda: {}
    handler._body = lambda: {}
    handler._json = lambda obj, code=200: replies.append((code, obj))

    path = st.workspace / "route.txt"
    path.write_bytes(b"ALPHA")
    first = runner._register_file(st, path, "c1", lambda event: None)
    path.write_bytes(b"BETA")
    second = runner._register_file(st, path, "c2", lambda event: None)

    route = (
        f"/artifacts/{first['artifact_id']}/versions/" f"{first['version_id']}/restore"
    )
    handler._api("POST", route)

    code, response = replies[-1]
    assert code == 200
    assert response["version_id"] not in {
        first["version_id"],
        second["version_id"],
    }
    assert response["restored_from_version_id"] == first["version_id"]
    assert response["artifact"]["version_id"] == response["version_id"]
    assert path.read_bytes() == b"ALPHA"

    source = store.version_meta(first["version_id"])
    Path(source["snapshot_path"]).write_bytes(b"tampered")
    handler._api("POST", route)
    assert replies[-1][0] == 404
    assert "checksum verification failed" in replies[-1][1]["error"]


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
    monkeypatch.setattr(runner, "_ensure_runtime", fake_ensure)
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
        # What the item was ACCEPTED under, carried from the request thread. The
        # freeze used to be written only to the frame, whose pin the rebind route
        # rewrites by design -- so a queued follow-up could adopt it after 202.
        frozen_binding=None,
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

    def fake_exec(st, code, origin, emit, stream=True, language="python"):
        if "host.submit_output" in code:
            st.dispatcher.last_output = {"output": {"answer": 42}}
        return {"result": {"stdout": "x\n", "stderr": "", "error": None}}

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_ensure_runtime", fake_ensure)
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

    def fake_exec(st, code, origin, emit, stream=True, language="python"):
        if "host.submit_output" in code:
            st.dispatcher.last_output = {"output": {"ok": True}}
        return {"result": {"stdout": "a\n", "stderr": "", "error": None}}

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_ensure_runtime", fake_ensure)
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


def test_model_profile_mask_and_empty_defaults_ignore_placeholder_keys(tmp_path):
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    handler = object.__new__(handler_cls)
    store = get_store(cfg.db_path)

    assert not handler._mask_profile({"api_key": "your-api-key-here"})["has_api_key"]
    assert handler._mask_profile({"api_key": "sk-real"})["has_api_key"]

    store.set_setting("llm_api_key", "your-api-key-here")
    payload = handler._model_profiles_payload()
    assert payload["profiles"] == []
    # Every protocol the LLM layer can dispatch. `gemini` and
    # `openai_responses` were dispatchable and unlisted, so a user holding a
    # Gemini key had no way to select it; `test_model_profile_readiness.py`
    # now fails if a provider is neither offered nor declared withheld.
    assert payload["protocols"] == [
        "chatgpt",
        "claude",
        "ark",
        "gemini",
        "openai_responses",
    ]
    assert store.list_model_profiles() == []


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


def test_local_model_discovery_route_is_explicit_and_non_mutating(
    monkeypatch, tmp_path
):
    class Discovery:
        def discover(self, *, force=False):
            return {
                "endpoints": [{"kind": "ollama", "models": ["qwen3:8b"]}],
                "probed": 4,
                "cached": not force,
                "mutated_settings": False,
            }

    monkeypatch.setattr(gateway_mod, "LocalModelDiscoveryService", Discovery)
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    handler = object.__new__(handler_cls)
    replies = []
    handler._query = lambda: {"force": ["true"]}
    handler._json = lambda obj, code=200: replies.append((code, obj))

    handler._api("GET", "/model-endpoints/discover")

    assert replies == [
        (
            200,
            {
                "endpoints": [{"kind": "ollama", "models": ["qwen3:8b"]}],
                "probed": 4,
                "cached": False,
                "mutated_settings": False,
            },
        )
    ]


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


def test_plan_restore_and_delete_artifact_created_shapes(tmp_path):
    """Plan stays flat, restore carries its fresh cache identity, delete bare."""
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

    # Version restore carries the fresh append-only identity for cache busting.
    f = st.workspace / "fig.txt"
    f.write_text("ALPHA")
    r1 = runner._register_file(st, f, "c1", lambda e: None)
    f.write_text("BETA")
    runner._register_file(st, f, "c2", lambda e: None)
    hub.events.clear()
    restored = runner.restore_version(r1["artifact_id"], r1["version_id"])
    assert restored.get("ok")
    ev = [e for e in hub.events if e.get("type") == "artifact_created"][-1]
    assert set(ev) == {"type", "root_frame_id", "artifact"}
    assert ev["root_frame_id"] == fid
    assert ev["artifact"]["id"] == ev["artifact"]["artifact_id"] == r1["artifact_id"]
    assert ev["artifact"]["version_id"] == restored["version_id"]
    assert ev["artifact"]["restored_from_version_id"] == r1["version_id"]

    # DELETE /api/artifacts/{aid} retains the bare refresh form + {"ok": true}.
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
    If this fails, a status was added/removed — update docs/webapp-api.md.

    The *vocabulary* is what docs/webapp-api.md promises, so the vocabulary is
    what is locked. This used to also require at least seven emit sites, which
    made deduplication look like a contract change: folding two copies of the
    terminal failure event into `_terminal_failure_event` reddened it while
    emitting exactly the same statuses. Collapsing a literal into the helper
    that owns it is the direction this file should encourage, so the terminal
    failure status is now asserted at that helper instead of counted.
    """
    from openai4s.server import titles as titles_mod

    src = Path(gateway_mod.__file__).read_text(encoding="utf-8")
    src += Path(titles_mod.__file__).read_text(encoding="utf-8")
    sites = list(re.finditer(r'"type": "frame_update"', src))
    assert sites, "no frame_update emit site is visible; this test sees nothing"
    literals = set()
    for m in sites:
        window = src[m.end() : m.end() + 250]
        s = re.search(r'"status": "([a-z_]+)"', window)
        if s:
            literals.add(s.group(1))
    assert literals == {"processing", "titled", "failed", "success", "updated"}

    # The one status no longer written at more than one emit site. It is built
    # by a named helper, so it is checked by calling it -- which also pins that
    # a failed turn's terminal event carries the ids the client needs to tell
    # it from the next turn's.
    job = gateway_mod.MessageJob("job-vocab", "root-vocab")
    job.execution_id = "exec-vocab"
    terminal = gateway_mod.SessionRunner._terminal_failure_event(
        None, "root-vocab", job
    )
    assert terminal["type"] == "frame_update"
    assert terminal["status"] == "failed"
    assert terminal["request_id"] == job.request_id
    assert terminal["execution_id"] == "exec-vocab"


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
    """The token gate: no credential is a 401 envelope, a valid `?token=` on a
    GET sets the cookie and redirects with the token stripped, `/health` and
    `/auth/status` stay reachable so a client can discover it needs one.

    Three things changed here and each was a defect on its own. The token was
    minted per boot into a closure, so every restart invalidated every cookie
    already issued. Comparison was `==`, which leaks a secret's prefix through
    timing. And the redirect went to "/" unconditionally, so a bookmarked deep
    link carrying a token landed on the dashboard instead of its target.
    """
    monkeypatch.setenv("OPENAI4S_REQUIRE_TOKEN", "1")
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    captured = capsys.readouterr()
    # stderr, and this assertion is the point rather than a detail. On `print`
    # to stdout the banner is block-buffered whenever stdout is not a TTY, so
    # under nohup, systemd, Docker or any redirect to a log file the one line a
    # user needs in order to open their own daemon never appeared. It showed in
    # a terminal, which is exactly why it survived review -- the configuration
    # that hides it is the one nobody develops in. Found by running a real
    # daemon with stdout redirected, not by reading the code.
    assert (
        "?token=" not in captured.out
    ), "the access token went to stdout, which is block-buffered off a TTY"
    tok = re.search(r"\?token=([A-Za-z0-9_-]{20,})", captured.err)
    assert tok, "gateway did not print the access token to stderr"
    token = tok.group(1)

    # Persisted, so a second daemon on the same data dir uses the same token
    # rather than invalidating the first one's cookies.
    from openai4s.server import local_auth

    assert local_auth.read_token(cfg.data_dir) == token
    gateway_mod.make_handler(cfg, _Hub(), runner)
    assert local_auth.read_token(cfg.data_dir) == token

    handler = object.__new__(handler_cls)
    handler.headers = {}  # no Cookie, no Origin
    replies = []
    handler._json = lambda obj, code=200: replies.append((code, obj))

    # no token → 401 with the {"error": ...} envelope
    handler.path = "/api/v1/frames"
    handler._route("GET")
    code, body = replies[-1]
    assert code == 401
    assert body["error"].startswith("unauthorized")

    # wrong token → still 401
    handler.path = "/api/v1/frames?token=deadbeef"
    handler._route("GET")
    assert replies[-1][0] == 401

    # A mutation may not authenticate from the query string at all. A URL
    # carrying a credential is logged by proxies, kept in history and leaked by
    # Referer, and a mutation is the request least able to afford that.
    handler.path = f"/api/v1/frames?token={token}"
    handler._route("POST")
    assert replies[-1][0] == 401

    # ...but the header works for a non-browser client.
    handler.headers = {"X-OpenAI4S-Token": token}
    handler.path = "/health"
    handler._route("GET")
    assert replies[-1][0] == 200
    handler.headers = {}

    # /health is exempt from the gate
    handler.path = "/health"
    handler._route("GET")
    code, body = replies[-1]
    assert code == 200 and body["status"] == "ok"
    assert "data_dir" not in body

    # /auth/status is reachable unauthenticated, and tells the truth. It used
    # to answer `auth_mode: "none"` even with the gate on, so the frontend had
    # no way to learn a token was required.
    handler.path = "/api/v1/auth/status"
    handler._route("GET")
    code, body = replies[-1]
    assert code == 200
    assert body["auth_mode"] == "token"
    assert body["authenticated"] is False
    assert token not in json.dumps(body)

    # valid ?token= on a GET → 303 with the os_token cookie, token stripped
    # from the URL but the rest of the path and query preserved.
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

    resp["headers"].clear()
    handler.path = f"/?token={token}&mode=raw"
    handler._route("GET")
    assert resp["code"] == 303
    assert resp["headers"]["Location"] == "/?mode=raw"

    # ...but a path that answers with data may not be bootstrapped at all.
    # `/preview/<id>` streams artifact bytes, so a link carrying a token there
    # used to set the cookie and then hand the file to whoever held the link.
    resp["headers"].clear()
    handler.path = f"/preview/abc?token={token}&mode=raw"
    handler._route("GET")
    assert replies[-1][0] == 401
    assert "Set-Cookie" not in resp["headers"]


def test_gateway_error_maps_to_error_envelope(tmp_path):
    """A GatewayError(code, message) raised anywhere under /api/* is serialized
    by _route as {"error": message} with its HTTP code (docs/webapp-api.md §2).
    Contract test before any extraction of gateway routing."""
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    handler = object.__new__(handler_cls)
    handler.headers = _auth_headers(cfg)
    replies = []
    handler._json = lambda obj, code=200: replies.append((code, obj))

    def boom(method, sub):
        raise gateway_mod.GatewayError(418, "teapot")

    handler._api = boom
    handler.path = "/api/v1/anything"
    handler._route("GET")

    assert replies[-1] == (418, {"error": "teapot"})


def test_unhandled_exception_maps_to_500_error_envelope(tmp_path, capsys):
    """A non-GatewayError exception under /api/* becomes a 500 whose body says
    nothing about the exception.

    This used to assert `{"error": str(e)}` -- it pinned the leak. An
    exception nobody wrote a message for carries whatever the raising code
    happened to interpolate, which in practice is a path, an argv or a
    credential, so the projector replaces it. See
    tests/test_public_exception_projector.py for the canary that proves it.
    """
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    handler = object.__new__(handler_cls)
    handler.headers = _auth_headers(cfg)
    replies = []
    handler._json = lambda obj, code=200: replies.append((code, obj))

    def boom(method, sub):
        raise RuntimeError("kaput")

    handler._api = boom
    handler.path = "/api/v1/anything"
    handler._route("GET")

    code, body = replies[-1]
    assert code == 500
    assert "kaput" not in json.dumps(body)
    assert body["error"] == gateway_mod.INTERNAL_ERROR_MESSAGE
    assert body["code"] == "internal_error"
    assert body["request_id"]
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
    handler.path = "/api/v1/frames"
    handler._route("POST")

    assert replies == [(403, {"error": "cross-origin request refused"})]


def test_cross_origin_ws_upgrade_is_refused(tmp_path):
    """The /api/ws upgrade (a GET) gets the same Origin==Host CSRF check as
    mutating verbs, since WebSocket handshakes bypass CORS and the socket
    accepts state-changing commands + streams session output."""
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    handler = object.__new__(handler_cls)
    handler.headers = {"Origin": "http://evil.example", "Host": "127.0.0.1:8760"}
    replies = []
    upgraded = []
    handler._json = lambda obj, code=200: replies.append((code, obj))
    handler._handle_ws = lambda: upgraded.append(True)
    handler.path = "/api/v1/ws"
    handler._route("GET")

    assert upgraded == []
    assert replies == [(403, {"error": "cross-origin request refused"})]


def test_ws_upgrade_allows_absent_and_same_origin(tmp_path):
    """A same-origin browser upgrade (Origin == Host) and an Origin-less client
    (CLI/tests) both pass the WS CSRF gate."""
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    for headers in (
        _auth_headers(cfg, {"Host": "127.0.0.1:8760"}),
        _auth_headers(
            cfg, {"Origin": "http://127.0.0.1:8760", "Host": "127.0.0.1:8760"}
        ),
    ):
        handler = object.__new__(handler_cls)
        handler.headers = headers
        upgraded = []
        handler._json = lambda obj, code=200: None
        handler._handle_ws = lambda: upgraded.append(True)
        handler.path = "/api/v1/ws"
        handler._route("GET")
        assert upgraded == [True]


def test_dns_rebinding_host_header_is_rejected(tmp_path):
    """DNS-rebinding defense (GHSA-fm3g-2c7x-8qj8): the Origin==Host CSRF guard
    alone is bypassed when an attacker rebinds evil.test→127.0.0.1 so the browser
    sends Origin==Host==evil.test (equal → the guard passes) while the request
    still lands on the loopback daemon. A Host-header allowlist, checked on EVERY
    request BEFORE routing, rejects any Host that is not a loopback address we
    bind — closing the path to the unauthenticated /compute/jobs command sink."""
    cfg = _cfg(tmp_path)  # default bind 127.0.0.1:8760
    assert (cfg.host, cfg.port) == ("127.0.0.1", 8760)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)

    def _run(headers, method, path):
        handler = object.__new__(handler_cls)
        # Authenticated on purpose: the Host allowlist must reject a rebind
        # even for a caller holding a valid credential, because the browser in
        # this attack *has* the user's cookie. A test that relied on the token
        # gate to produce the 403 would prove nothing about the Host check.
        handler.headers = _auth_headers(cfg, headers)
        replies = []
        api_calls = []
        handler._json = lambda obj, code=200: replies.append((code, obj))
        handler._api = lambda m, sub: api_calls.append((m, sub))
        handler.path = path
        handler._route(method)
        return replies, api_calls

    # the exact bypass: forged Host == Origin == an attacker rebind domain.
    # Origin==Host, so the CSRF guard would pass, but the Host is not a loopback
    # address we serve → 403 "host not allowed", BEFORE the command sink runs.
    replies, api_calls = _run(
        {"Host": "evil.test:8760", "Origin": "http://evil.test:8760"},
        "POST",
        "/api/v1/compute/jobs",
    )
    assert replies == [(403, {"error": "host not allowed"})]
    assert api_calls == []  # the command sink is never reached

    # the allowlist covers GET too: a rebound page is same-origin and can read
    # GET bodies, and origin-less GETs skip the Origin guard entirely
    replies, api_calls = _run({"Host": "evil.test:8760"}, "GET", "/api/v1/frames")
    assert replies == [(403, {"error": "host not allowed"})]
    assert api_calls == []

    # legitimate loopback Hosts on the bound port reach routing (incl. IPv6)
    for host in ("127.0.0.1:8760", "localhost:8760", "[::1]:8760", "LocalHost:8760"):
        replies, api_calls = _run({"Host": host}, "GET", "/api/v1/frames")
        assert api_calls == [("GET", "/frames")], f"{host} should route"
        assert replies == []

    # right hostname, wrong port → still rejected
    replies, api_calls = _run({"Host": "127.0.0.1:9999"}, "GET", "/api/v1/frames")
    assert replies == [(403, {"error": "host not allowed"})]
    assert api_calls == []

    # absent Host (non-browser client: curl/CLI) passes — a browser rebind
    # always carries a Host, so an empty Host is not the attack vector
    replies, api_calls = _run({}, "GET", "/api/v1/frames")
    assert api_calls == [("GET", "/frames")]
    assert replies == []


def test_execution_log_route_serializer_contract(tmp_path):
    """GET /api/frames/{fid}/execution-log — the Notebook data contract: each
    entry carries immutable identity plus source/stdout/stderr/error/status,
    artifacts/resources and retry-projection metadata, with code→source and
    cpu_s→cpu_seconds renames and ""/[] (never null) defaults."""
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
        "producing_cell_id",
        "fork_checkpoint_id",
        "cell_index",
        "state_revision",
        "generation_id",
        "kernel_id",
        "language",
        "origin",
        "source",
        "code_hash",
        "visibility",
        "pin",
        "replay_policy",
        "variable_reads",
        "variable_writes",
        "variable_deletes",
        "mutation_uncertain",
        "stale",
        "stale_reasons",
        "stdout",
        "stderr",
        "error",
        "status",
        "figures",
        "files_written",
        "files_read",
        "cpu_seconds",
        "peak_rss_kb",
        "attempt_group_id",
        "attempt",
        "revision_of",
        "is_latest_attempt",
        "attempt_count",
    }
    assert e1["producing_cell_id"] == "cell-1"
    assert e1["fork_checkpoint_id"] is None
    assert e1["state_revision"] == 1
    assert e1["generation_id"] is None
    assert e1["attempt_group_id"] == "cell-1"
    assert e1["source"] == "print('hi')"  # code -> source rename
    assert e1["code_hash"] == hashlib.sha256(b"print('hi')").hexdigest()
    assert e1["visibility"] == "scientific"
    assert e1["pin"] is False
    assert e1["replay_policy"] == "conditional"
    assert e1["variable_reads"] == ["print"]
    assert e1["variable_writes"] == []
    assert e1["variable_deletes"] == []
    assert e1["mutation_uncertain"] is False
    assert e1["stale"] is False and e1["stale_reasons"] == []
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
    reports its producing cell interaction + save event, and merges legacy
    execution-log reads with real version lineage before filtering outputs.
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
        files_read=["legacy.csv", "fig.txt", "raw.csv"],
        files_written=["fig.txt"],
    )
    raw = store.save_artifact(
        path=str(st.workspace / "raw.csv"),
        filename="raw.csv",
        content_type="text/csv",
        size_bytes=3,
        checksum="raw",
        frame_id=fid,
    )
    edge_only = store.save_artifact(
        path=str(st.workspace / "edge.csv"),
        filename="edge.csv",
        content_type="text/csv",
        size_bytes=4,
        checksum="edge",
        frame_id=fid,
    )
    f = st.workspace / "fig.txt"
    f.write_text("bytes")
    rec = runner._register_file(st, f, "cell-7", lambda e: None)
    store.add_lineage_edge(
        input_version_id=raw["version_id"],
        output_version_id=rec["version_id"],
        producing_cell_id="cell-7",
        frame_id=fid,
    )
    store.add_lineage_edge(
        input_version_id=edge_only["version_id"],
        output_version_id=rec["version_id"],
        producing_cell_id="cell-7",
        frame_id=fid,
    )

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
    assert cell["files_read"] == ["legacy.csv", "fig.txt", "raw.csv", "edge.csv"]
    # inputs exclude what the cell itself wrote and the artifact's own filename,
    # while retaining both legacy telemetry and Store-backed lineage edges.
    assert lin["dependency_mappings"] == {
        "inputs": ["legacy.csv", "raw.csv", "edge.csv"]
    }

    empty = handler._lineage("a-does-not-exist")
    assert empty == {
        "artifact_id": "a-does-not-exist",
        "filename": None,
        "interactions": [],
        "dependency_mappings": {"inputs": []},
    }
    replies = []
    handler._query = lambda: {}
    handler._json = lambda obj, code=200: replies.append((code, obj))
    handler._api("GET", "/artifacts/a-does-not-exist/lineage")
    assert replies[-1] == (200, empty)


def test_lineage_serializer_follows_latest_and_restored_version_edges(tmp_path):
    cfg, runner, store, fid, st = _runner_frame(tmp_path)
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    handler = object.__new__(handler_cls)
    sources = []
    for name in ("input-a.txt", "input-b.txt"):
        path = st.workspace / name
        path.write_text(name)
        sources.append(
            store.save_artifact(
                path=str(path),
                filename=name,
                content_type="text/plain",
                size_bytes=len(name),
                checksum=name,
                frame_id=fid,
            )
        )

    output = st.workspace / "result.txt"
    versions = []
    for index, source in enumerate(sources, start=1):
        cell_id = f"cell-{index}"
        store.log_cell(
            frame_id=fid,
            root_frame_id=fid,
            code=f"write version {index}",
            result={"id": cell_id, "stdout": "", "stderr": "", "error": None},
            cell_index=index,
            files_read=[],
            files_written=["result.txt"],
        )
        output.write_text(f"version {index}")
        record = runner._register_file(st, output, cell_id, lambda event: None)
        versions.append(record)
        store.add_lineage_edge(
            input_version_id=source["version_id"],
            output_version_id=record["version_id"],
            producing_cell_id=cell_id,
            frame_id=fid,
        )

    latest = handler._lineage(versions[0]["artifact_id"])
    assert latest["dependency_mappings"] == {"inputs": ["input-b.txt"]}
    assert latest["interactions"][0]["files_read"] == ["input-b.txt"]

    store.set_latest_version(versions[0]["artifact_id"], versions[0]["version_id"])
    restored = handler._lineage(versions[0]["artifact_id"])
    assert restored["dependency_mappings"] == {"inputs": ["input-a.txt"]}
    assert restored["interactions"][0]["files_read"] == ["input-a.txt"]


def test_upload_decodes_base64_or_refuses_it(tmp_path):
    """`POST /api/uploads` no longer reinterprets what it cannot decode.

    It used to call `b64decode` without `validate=True`, so non-alphabet
    characters were silently discarded and the payload decoded to *different
    bytes* with no error -- the artifact then carried a checksum over content
    nobody sent. When decoding did fail outright, it stored the raw string's
    UTF-8 bytes: upload a `.npy` whose payload lost a character and the
    artifact contained the base64 text, versioned and hashed and
    indistinguishable from data.

    This test asserted both behaviours, and the API doc recorded them as a
    documented wart. A wart that silently rewrites scientific input is a
    defect with a nicer name.
    """
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

    # Line wrapping is transport formatting and still decodes.
    wrapped = base64.b64encode(b"\x00\x01binary").decode()
    res = handler._upload(
        {
            "filename": "wrapped.bin",
            "content_base64": "\n".join(
                wrapped[i : i + 4] for i in range(0, len(wrapped), 4)
            ),
            "frame_id": fid,
        }
    )
    assert _bytes(res) == b"\x00\x01binary"

    # A stray non-alphabet character is corruption. It used to be dropped, and
    # "Zm9v!YmFy" decoded to b"foobar" -- plausible bytes, wrong content.
    with pytest.raises(gateway_mod.GatewayError) as dropped:
        handler._upload(
            {"filename": "b.bin", "content_base64": "Zm9v!YmFy", "frame_id": fid}
        )
    assert dropped.value.code == 400

    # And text that is not base64 at all is refused rather than stored as-is.
    with pytest.raises(gateway_mod.GatewayError) as raw:
        handler._upload(
            {
                "filename": "c.bin",
                "content_base64": "%%% not base64 %%%",
                "frame_id": fid,
            }
        )
    assert raw.value.code == 400


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
    envelope = json.loads(body)
    assert envelope["error"] == "artifact not found"
    # Contract v1 enriches every error with a stable machine code and the
    # request's correlation id. `request_id` is null here because this test
    # drives _api directly, so no request scope was ever entered — the honest
    # answer rather than a fabricated id.
    assert envelope["code"] == "not_found"
    assert envelope["status"] == 404
    assert envelope["request_id"] is None
    assert ctype.startswith("application/json")


def test_preview_route_forces_html_content_type(tmp_path):
    """GET /preview/{ident} serves the same resolved bytes but ALWAYS stamps
    text/html, whatever the stored content_type says."""
    cfg, runner, store, fid, st = _runner_frame(tmp_path)
    handler, sends = _bytes_handler(cfg, runner)
    handler.headers = _auth_headers(cfg)  # _route consults Origin/Cookie headers

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


def test_body_rejects_unparseable_json_with_an_explicit_4xx(tmp_path):
    """_body() contract: unparseable input is a 400, absent input is not.

    This used to collapse a malformed body to {} so "route handlers never see a
    parse error". Every route reads its fields with b.get(...), so that did not
    mean "lenient" — it meant a truncated or mistyped body silently became "the
    client supplied nothing", the request no-opped, and it returned 200. A
    client cannot tell that from success; the bug lands on whoever later wonders
    why their setting never saved.

    An empty body stays {}: routes whose fields are all optional legitimately
    accept one. The distinction is unparseable vs absent.
    """
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    handler = object.__new__(handler_cls)

    def _with(raw: bytes):
        handler.headers = {"Content-Length": str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        return handler._body()

    assert _with(b'{"a": 1}') == {"a": 1}
    assert _with(b"") == {}  # Content-Length: 0 → {} without reading
    assert _with(b"{}") == {}

    for malformed in (b"this is not json", b"{truncated", b"{'py': 'repr'}"):
        with pytest.raises(gateway_mod.GatewayError) as e:
            _with(malformed)
        assert e.value.code == 400
        assert "not valid JSON" in e.value.message

    # `[1,2]` parses, then AttributeErrors on the first .get() — a 500 for what
    # is squarely a client error.
    for wrong_shape in (b"[1,2]", b'"str"', b"42"):
        with pytest.raises(gateway_mod.GatewayError) as e:
            _with(wrong_shape)
        assert e.value.code == 400
        assert "must be a JSON object" in e.value.message

    handler.headers = _auth_headers(cfg)  # no Content-Length header at all
    handler.rfile = io.BytesIO(b'{"ignored": true}')
    assert handler._body() == {}


def test_ignored_json_body_is_buffered_before_next_keepalive_request(tmp_path):
    """A no-argument POST may still carry ``{}``; its bytes must not prefix
    the next HTTP/1.1 request line on the persistent connection."""

    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    handler = object.__new__(handler_cls)
    handler.headers = {"Content-Length": "2"}
    handler.rfile = io.BytesIO(b"{}GET /health HTTP/1.1\r\n")
    handler.close_connection = False
    handler._request_body_tracking_active = True
    handler._request_body_ready = False
    handler._request_body_payload = b""

    handler._prepare_request_body("/api/v1/artifacts/a-1/versions/v-1/restore", "POST")

    assert handler._body() == {}
    assert handler.rfile.read() == b"GET /health HTTP/1.1\r\n"
    assert handler.close_connection is False


def test_request_body_rejects_ambiguous_or_unsupported_framing(tmp_path):
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    handler = object.__new__(handler_cls)
    handler.rfile = io.BytesIO(b"")
    handler.close_connection = False

    handler.headers = {"Content-Length": "-1"}
    with pytest.raises(gateway_mod.GatewayError, match="invalid Content-Length"):
        handler._body()
    assert handler.close_connection is True

    handler.close_connection = False
    handler.headers = {"Transfer-Encoding": "chunked"}
    with pytest.raises(gateway_mod.GatewayError, match="Transfer-Encoding"):
        handler._body()
    assert handler.close_connection is True

    class DuplicateLengthHeaders(dict):
        def get_all(self, name):
            return ["2", "2"] if name == "Content-Length" else None

    handler.close_connection = False
    handler.headers = DuplicateLengthHeaders({"Content-Length": "2"})
    with pytest.raises(gateway_mod.GatewayError, match="ambiguous Content-Length"):
        handler._body()
    assert handler.close_connection is True


def test_request_body_cache_is_released_after_keepalive_dispatch(tmp_path):
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    handler = object.__new__(handler_cls)
    handler.path = "/ignored"
    handler.headers = _auth_headers(cfg, {"Content-Length": "2"})
    handler.rfile = io.BytesIO(b"{}")
    handler.close_connection = False
    replies = []
    handler._json = lambda obj, code=200: replies.append((code, obj))

    handler._route("POST")

    assert replies == [(404, {"error": "not found"})]
    assert handler._request_body_payload == b""
    assert handler._request_body_ready is False
    assert handler._request_body_tracking_active is False
    assert handler.close_connection is False


def test_websocket_upgrade_is_never_reused_as_http_keepalive(tmp_path):
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    handler = object.__new__(handler_cls)
    handler.path = "/api/v1/ws"
    handler.headers = _auth_headers(cfg)
    handler.close_connection = False
    upgraded = []
    handler._handle_ws = lambda: upgraded.append(True)

    handler._route("GET")

    assert upgraded == [True]
    assert handler.close_connection is True


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
    reaches runner.submit_repl; with the flag on it returns an async ticket."""
    # disabled by default → 403 error envelope, submit_repl short-circuited
    cfg = _cfg(tmp_path)
    assert cfg.notebook_repl is False
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    store = get_store(cfg.db_path)
    fid = store.new_frame(kind="turn", project_id="default", status="ready")
    called = []
    runner.submit_repl = lambda *a, **k: called.append((a, k)) or None

    handler = object.__new__(gateway_mod.make_handler(cfg, _Hub(), runner))
    replies = []
    handler._query = lambda: {}
    handler._body = lambda: {"code": "print(1)"}
    handler._json = lambda obj, code=200: replies.append((code, obj))

    for action in ("execute", "env", "restart", "stop", "start", "interrupt"):
        handler._api("POST", f"/frames/{fid}/kernel/{action}")
        assert replies[-1][0] == 403
        assert "disabled" in replies[-1][1]["error"]
    assert called == []  # the gate fired before the kernel path

    # enabled (OPENAI4S_NOTEBOOK_REPL=1) → queues and returns immediately
    monkeypatch.setenv("OPENAI4S_NOTEBOOK_REPL", "1")
    cfg2 = _cfg(tmp_path)
    assert cfg2.notebook_repl is True
    runner2 = gateway_mod.SessionRunner(cfg2, _Hub())
    hits = []
    ticket = SimpleNamespace(
        job_id="job-repl",
        execution_id="repl-client-exact",
        execution_owner={"kind": "user_repl", "id": "repl-client-exact"},
        wait_result=lambda: {"cell": {"cell_index": 1}},
    )
    runner2.submit_repl = lambda rfid, pid, code, **kwargs: (
        hits.append((rfid, pid, code, kwargs)) or ticket
    )

    handler2 = object.__new__(gateway_mod.make_handler(cfg2, _Hub(), runner2))
    replies2 = []
    handler2._query = lambda: {}
    handler2._body = lambda: {
        "code": "print(2)",
        "language": "python",
        "execution_id": "repl-client-exact",
    }
    handler2._json = lambda obj, code=200: replies2.append((code, obj))

    handler2._api("POST", f"/frames/{fid}/kernel/execute")
    assert hits == [
        (
            fid,
            "default",
            "print(2)",
            {"language": "python", "execution_id": "repl-client-exact"},
        )
    ]
    assert replies2[-1][0] == 202
    assert replies2[-1][1] == {
        "status": "accepted",
        "frame_id": fid,
        "job_id": "job-repl",
        "execution_id": "repl-client-exact",
        "owner": {"kind": "user_repl", "id": "repl-client-exact"},
        "queue_position": None,
    }

    interrupts = []
    runner2.interrupt_kernel = lambda rfid, **kwargs: (
        interrupts.append((rfid, kwargs)) or {"ok": True}
    )
    handler2._body = lambda: {
        "execution_id": "repl-client-exact",
        "owner": {"kind": "user_repl", "id": "repl-client-exact"},
    }
    handler2._api("POST", f"/frames/{fid}/kernel/interrupt")
    assert interrupts == [
        (
            fid,
            {
                "execution_id": "repl-client-exact",
                "owner": {"kind": "user_repl", "id": "repl-client-exact"},
                "owner_id": "repl-client-exact",
            },
        )
    ]
    assert replies2[-1] == (200, {"ok": True})


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
        def shutdown(self):
            calls.append("shutdown")

        def restart(self):
            calls.append("restart")

    st.kernels.ensure("python", "base", FallbackKernel)
    st.env_name = "base"
    st.desired_env = "struct"

    def spawn(state):
        calls.append("spawn")
        state.env_name = state.desired_env
        return state.kernels.ensure(
            "python",
            "struct",
            lambda: SimpleNamespace(shutdown=lambda: None),
        )

    monkeypatch.setattr(runner, "_spawn_kernel", spawn)
    result = runner.restart_kernel(st.root_frame_id, st.project_id)

    # Replacement is build-first: the old base worker is shut down only after
    # the recovered target worker exists.
    assert calls == ["spawn", "shutdown"]
    assert st.env_name == "struct"
    assert result["generation"] == 1


def test_restart_still_clears_namespace_when_pin_remains_unavailable(
    monkeypatch, tmp_path
):
    """A fallback resolving to the same base key must not turn Restart into reuse."""
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    st = runner._state("f-restart-still-fallback", "default")
    calls = []

    class FallbackKernel:
        def restart(self):
            calls.append("restart")

        def shutdown(self):
            calls.append("shutdown")

    kernel = FallbackKernel()
    st.kernels.ensure("python", "base", lambda: kernel)
    st.env_name = "base"
    st.desired_env = "struct"

    def unresolved(state):
        calls.append("resolve-base")
        return state.kernels.ensure("python", "base", lambda: FallbackKernel())

    monkeypatch.setattr(runner, "_spawn_kernel", unresolved)
    monkeypatch.setattr(
        runner,
        "_run_bootstrap",
        lambda state, target=None: calls.append("bootstrap"),
    )

    result = runner.restart_kernel(st.root_frame_id, st.project_id)

    assert st.kernel is kernel
    assert calls == ["resolve-base", "restart", "bootstrap"]
    assert result["generation"] == 1


def test_tool_batch_applies_env_switch_before_following_call(monkeypatch, tmp_path):
    """env_use then another tool in one reply must use the rebuilt dispatcher."""
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
            '```tool\n{"name":"list_dir","arguments":{"path":"."}}\n```',
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

    def fake_exec(state, code, origin, emit, stream=True, language="python"):
        state.dispatcher.last_output = {"output": {"ok": True}}
        return {"result": {"stdout": "", "stderr": "", "error": None}}

    monkeypatch.setattr(runner, "_execute_and_log", fake_exec)

    runner._loop(st, lambda event: None, [])

    assert calls == [("old", "env_use"), ("apply", "struct"), ("new", "list_dir")]


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


def test_live_cell_start_event_carries_canonical_kernel_id(monkeypatch, tmp_path):
    """Live cell labels come from the server event, not an async UI cache."""
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    fid = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    st = runner._state(fid, "default")
    st.env_name = "struct"
    st.kernels.ensure(
        "python",
        "struct",
        lambda: SimpleNamespace(is_alive=lambda: True, shutdown=lambda: None),
    )
    events = []
    monkeypatch.setattr(runner, "_safety_refusal", lambda *a, **k: "blocked")

    runner._execute_and_log(st, "print('x')", "agent", events.append, stream=True)

    start = next(e for e in events if e.get("chunk", "").startswith("⚙"))
    assert start["cell_index"] == 1
    assert start["kernel_id"] == "python — struct"
    assert start["language"] == "python"


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


# --------------------------------------------------------------------------
# a resume cursor is only meaningful inside the daemon run that issued it
# --------------------------------------------------------------------------


class _Recorder:
    """Collects everything the hub sends to one client."""

    def __init__(self):
        self.alive = True
        self.subs = set()
        self.events = []

    def send_json(self, event):
        self.events.append(dict(event))

    def replay_begin(self):
        return next((e for e in self.events if e.get("type") == "replay_begin"), None)


def _live_turn(hub, root, count=3):
    hub.broadcast(root, {"type": "text_reset", "frame_id": root})
    for i in range(count):
        hub.broadcast(root, {"type": "text_chunk", "frame_id": root, "chunk": str(i)})
    return max(int(e.get("seq") or 0) for e in hub._live[root]["events"])


def test_a_cursor_from_a_previous_daemon_run_is_reported_as_a_gap():
    """The silent failure this exists for. `_seq` is in-process, so a restart
    puts it back to zero while the client still holds a cursor from the
    previous run. Nothing was replayed and no gap was declared, so the client
    sat there believing it was caught up on a stream it had entirely missed.
    """
    before = gateway_mod.WSHub()
    root = "root-restart"
    last_seq = _live_turn(before, root)
    assert last_seq > 0

    restarted = gateway_mod.WSHub()  # a fresh process: empty buffer, seq at 0
    conn = _Recorder()
    restarted.add(conn)
    restarted.subscribe(root, conn, last_seq, before.epoch)

    begin = conn.replay_begin()
    assert begin is not None, "silence let the client believe it was caught up"
    assert begin["gap"] is True
    assert begin["epoch"] == restarted.epoch != before.epoch


def test_a_restart_is_detected_even_without_a_client_epoch():
    """Detection must not depend on the client having been updated.

    The counter sitting below the cursor was the original proof, and it only
    covers cursors this daemon has not reached. A cursor it *has* reached is
    indistinguishable from one of its own, so an epoch-less cursor is a gap
    whatever the counter says — see the paired test below."""
    restarted = gateway_mod.WSHub()
    conn = _Recorder()
    restarted.add(conn)
    restarted.subscribe("root-old-client", conn, 500)  # no epoch sent

    begin = conn.replay_begin()
    assert begin is not None
    assert begin["gap"] is True


def test_an_epochless_cursor_the_counter_has_reached_is_still_a_gap():
    """Codex P1. The numeric check cannot see this one: once the new daemon has
    emitted at least as many events as the cursor names, the cursor looks
    placeable, and replay silently filters the new stream's early events out as
    already seen."""
    restarted = gateway_mod.WSHub()
    root = "root-old-tab"
    _live_turn(restarted, root, count=4)

    conn = _Recorder()
    restarted.add(conn)
    restarted.subscribe(root, conn, 2)  # no epoch, and 2 <= our own counter

    begin = conn.replay_begin()
    assert begin is not None
    assert begin["gap"] is True


def test_a_cursor_within_the_same_run_replays_only_what_was_missed():
    """The ordinary case must keep working: no gap, and only the tail."""
    hub = gateway_mod.WSHub()
    root = "root-same-run"
    _live_turn(hub, root, count=4)
    events = hub._live[root]["events"]
    cursor = int(events[1]["seq"])

    conn = _Recorder()
    hub.add(conn)
    hub.subscribe(root, conn, cursor, hub.epoch)

    begin = conn.replay_begin()
    assert begin["gap"] is False
    replayed = [e for e in conn.events if e.get("type") == "text_chunk"]
    assert replayed, "the missed tail must still arrive"
    assert all(int(e["seq"]) > cursor for e in replayed)


def test_a_fresh_subscriber_with_no_cursor_is_not_a_gap():
    hub = gateway_mod.WSHub()
    root = "root-fresh"
    _live_turn(hub, root)

    conn = _Recorder()
    hub.add(conn)
    hub.subscribe(root, conn, 0)

    begin = conn.replay_begin()
    assert begin["gap"] is False


def test_a_cursor_older_than_the_retained_window_is_a_gap():
    """The pre-existing case: the buffer aged past the cursor."""
    hub = gateway_mod.WSHub()
    root = "root-aged"
    _live_turn(hub, root, count=3)
    # Pretend the client's cursor predates everything still retained.
    hub._live[root]["events"] = hub._live[root]["events"][-1:]

    conn = _Recorder()
    hub.add(conn)
    hub.subscribe(root, conn, 1, hub.epoch)

    assert conn.replay_begin()["gap"] is True


def test_every_hub_instance_has_its_own_epoch():
    assert gateway_mod.WSHub().epoch != gateway_mod.WSHub().epoch


def test_a_stale_cursor_declares_the_gap_without_replaying_anything():
    """Where two invariants meet. The client must learn it is out of sync, and
    a cursor we cannot place must not wrap around into a full replay — the
    client refetches on `gap`, so anything sent here is rendered and then
    immediately discarded."""
    before = gateway_mod.WSHub()
    root = "root-stale-no-replay"
    last = _live_turn(before, root, count=3)

    restarted = gateway_mod.WSHub()
    _live_turn(restarted, root, count=3)  # a new turn, new numbering
    conn = _Recorder()
    restarted.add(conn)
    restarted.subscribe(root, conn, last + 500, before.epoch)

    assert conn.replay_begin()["gap"] is True
    assert not [
        e for e in conn.events if e.get("type") == "text_chunk"
    ], "a cursor this process cannot place must not trigger a full replay"


def test_the_access_token_is_minted_once_and_survives_a_restart(tmp_path):
    """A token in a closure changed on every boot.

    That is tolerable while the gate is off by default and intolerable once it
    is on: every cookie already issued stops working, and the user is locked
    out of their own daemon by a restart. It also has to be readable by the
    CLI, which must present a credential and cannot import the web server to
    find out what it is.
    """
    from openai4s.server import local_auth

    first = local_auth.load_or_mint(tmp_path)
    assert first
    assert local_auth.load_or_mint(tmp_path) == first
    assert local_auth.read_token(tmp_path) == first

    # Owner-only on POSIX; the file holds a live credential.
    import os as _os

    mode = (tmp_path / local_auth.TOKEN_FILENAME).stat().st_mode & 0o777
    if _os.name == "posix":
        assert mode == 0o600, oct(mode)

    # No temporary left behind by the atomic write.
    assert not [p.name for p in tmp_path.glob(".*tmp*")]

    # A different data dir is a different daemon.
    other = tmp_path / "elsewhere"
    assert local_auth.load_or_mint(other) != first


def test_token_comparison_is_constant_time_and_refuses_empties():
    """`==` on a secret leaks its prefix through timing -- weak over loopback,
    real over a tunnel. An absent value must never compare equal to an absent
    expectation, or a daemon with no token would accept anyone."""
    from openai4s.server import local_auth

    assert local_auth.matches("abc", "abc") is True
    assert local_auth.matches("abc", "abd") is False
    assert local_auth.matches(None, "abc") is False
    assert local_auth.matches("abc", None) is False
    assert local_auth.matches(None, None) is False
    assert local_auth.matches("", "") is False


def test_the_loopback_gate_is_required_by_default(tmp_path, monkeypatch):
    """It used to be opt-in on loopback.

    The reasoning was that a single-user local tool needs no gate. But the
    daemon exposes unauthenticated code execution -- `kernel/execute`,
    `compute/jobs`, `host.bash` -- and "local" includes every other process on
    the machine. The Host and Origin guards cover the browser; they do not
    cover a local process.

    `OPENAI4S_REQUIRE_TOKEN=0` is the escape hatch, and it lives for one minor
    release. Same variable that used to opt *in*, sense reversed, so a script
    setting it to 1 keeps working and simply asks for what is now the default.
    """
    from openai4s.server import local_auth

    monkeypatch.delenv("OPENAI4S_REQUIRE_TOKEN", raising=False)
    cfg = _cfg(tmp_path / "default")
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    gateway_mod.make_handler(cfg, _Hub(), runner)
    assert local_auth.read_token(cfg.data_dir), "loopback did not require a token"

    # The legacy opt-out, honoured on loopback.
    monkeypatch.setenv("OPENAI4S_REQUIRE_TOKEN", "0")
    relaxed = _cfg(tmp_path / "relaxed")
    relaxed_runner = gateway_mod.SessionRunner(relaxed, _Hub())
    gateway_mod.make_handler(relaxed, _Hub(), relaxed_runner)
    assert local_auth.read_token(relaxed.data_dir) is None

    # ...and ignored off loopback. A bind anything can route to has no
    # configuration under which it should answer without a credential.
    exposed = Config(
        data_dir=tmp_path / "exposed",
        host="0.0.0.0",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=3,
    )
    exposed_runner = gateway_mod.SessionRunner(exposed, _Hub())
    gateway_mod.make_handler(exposed, _Hub(), exposed_runner)
    assert local_auth.read_token(exposed.data_dir), "non-loopback honoured the opt-out"


def test_the_cli_presents_the_daemon_credential(tmp_path, monkeypatch):
    """Every daemon-backed subcommand 401s without this.

    `_daemon_request` sent no credential at all and leaned on a comment saying
    the CSRF guard passes non-browser clients -- true, and unrelated to the
    token gate. `OPENAI4S_TOKEN` exists because the token file is owner-only:
    a daemon under another account (a systemd unit) writes a file this user
    cannot read, and without an override the CLI would need a chmod or a `su`.
    """
    from openai4s.cli.main import _daemon_credential_hint, _daemon_token
    from openai4s.server import local_auth

    cfg = _cfg(tmp_path)
    monkeypatch.delenv("OPENAI4S_TOKEN", raising=False)

    # Nothing minted yet: the hint names the path and what to do.
    assert _daemon_token(cfg) is None
    assert "OPENAI4S_TOKEN" in _daemon_credential_hint(cfg)

    minted = local_auth.load_or_mint(cfg.data_dir)
    assert _daemon_token(cfg) == minted

    # The override wins, for the cross-account case it exists for.
    monkeypatch.setenv("OPENAI4S_TOKEN", "supplied-by-the-operator")
    assert _daemon_token(cfg) == "supplied-by-the-operator"

    # And the gate accepts what the CLI sends.
    monkeypatch.delenv("OPENAI4S_TOKEN", raising=False)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    handler = object.__new__(handler_cls)
    handler.headers = {local_auth.TOKEN_HEADER: _daemon_token(cfg)}
    handler.path = "/api/v1/frames"
    reached = []
    handler._json = lambda obj, code=200: reached.append(("json", code))
    handler._api = lambda method, sub: reached.append(("api", sub))
    handler._route("GET")
    assert reached and reached[-1][0] == "api"


def _probe_route(handler_cls, headers, path, method="GET"):
    """Drive `_route` and report what it did: an int status, or ("api", sub)."""
    handler = object.__new__(handler_cls)
    handler.headers = headers
    handler.path = path
    seen: list[object] = []
    handler._json = lambda obj, code=200: seen.append(code)
    handler._api = lambda m, sub: seen.append(("api", sub))
    handler.send_response = lambda code: seen.append(code)
    handler.send_header = lambda k, v: None
    handler.end_headers = lambda: None
    handler._prepare_request_body = lambda *a, **k: None
    handler._route(method)
    return seen[-1] if seen else None


def test_a_query_token_bootstraps_only_the_root_page(tmp_path, monkeypatch):
    """A URL with a credential in it is a shareable credential.

    It gets pasted into chat, logged by a proxy and kept in browser history.
    The gate accepted `?token=` on *any* GET, so
    `/api/v1/artifacts/<id>/download?token=…` was a link that hands over the
    file to whoever holds it -- no redirect, no cookie hand-off, the response
    body is the payload. Excluding `/api/v1/` and `/static/` narrowed that but
    did not close it: `/preview/<id>` is neither, and it answers with artifact
    bytes. Only the root page -- the one URL the product ever prints -- may be
    bootstrapped, and the 303 strips the credential immediately.
    """
    from openai4s.server import local_auth

    monkeypatch.delenv("OPENAI4S_REQUIRE_TOKEN", raising=False)
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    token = local_auth.read_token(cfg.data_dir)
    try:
        assert _probe_route(handler_cls, {}, f"/?token={token}") == 303
        assert _probe_route(handler_cls, {}, f"/index.html?token={token}") == 303
        # Everything else: refused, even with a valid token in the query.
        # `/preview/<id>` is the one that mattered -- it is not under the API
        # prefix, so the old subtractive rule bootstrapped it.
        assert _probe_route(handler_cls, {}, f"/session/abc?token={token}") == 401
        assert _probe_route(handler_cls, {}, f"/preview/abc?token={token}") == 401
        assert _probe_route(handler_cls, {}, f"/api/v1/frames?token={token}") == 401
        assert _probe_route(handler_cls, {}, f"/static/app.js?token={token}") == 401
        # And a mutation is refused on every path, navigation or not.
        assert (
            _probe_route(handler_cls, {}, f"/session/abc?token={token}", "POST") == 401
        )
    finally:
        runner.close()


def test_the_gate_accepts_bearer_and_the_explicit_header(tmp_path, monkeypatch):
    """`Authorization: Bearer` is what a generic client reaches for unprompted.

    Neither spelling is preferred. `X-OpenAI4S-Token` stays because something
    upstream may already own `Authorization`, and the CLI sends it; Bearer is
    here so `curl -H` and any SDK work without reading the docs first.

    Both go through one parser, which `/auth/status` also calls -- a status
    route that answers from its own reasoning is how the old hardcoded "none"
    survived a gate that was actually on.
    """
    from openai4s.server import local_auth

    monkeypatch.delenv("OPENAI4S_REQUIRE_TOKEN", raising=False)
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    token = local_auth.read_token(cfg.data_dir)
    try:
        for headers in (
            {"Authorization": f"Bearer {token}"},
            {"Authorization": f"bearer {token}"},  # RFC 7235: scheme is caseless
            {local_auth.TOKEN_HEADER: token},
        ):
            assert _probe_route(handler_cls, headers, "/api/v1/frames") == (
                "api",
                "/frames",
            ), headers
        for headers in (
            {"Authorization": f"Basic {token}"},  # right value, wrong scheme
            {"Authorization": "Bearer "},
            {"Authorization": token},  # bare, no scheme
            {"Authorization": "Bearer not-the-token"},
        ):
            assert _probe_route(handler_cls, headers, "/api/v1/frames") == 401, headers

        # /auth/status reports through the same parser, and never leaks the
        # token itself -- only whether one was accepted.
        handler = object.__new__(handler_cls)
        handler.headers = {"Authorization": f"Bearer {token}"}
        handler.path = "/api/v1/auth/status"
        seen: list[dict] = []
        handler._json = lambda obj, code=200: seen.append(obj)
        handler._api("GET", "/auth/status")
        assert seen[-1]["authenticated"] is True
        assert seen[-1]["auth_mode"] == "token"
        assert token not in json.dumps(seen[-1])
    finally:
        runner.close()


def test_the_correlation_id_reaches_the_job_thread():
    """`contextvars` do not cross a `threading.Thread`.

    The comment above `_correlation_id` said the opposite -- that a ContextVar
    was chosen "because the gateway hands requests to threads *and* the value
    has to survive into anything those threads schedule". A new thread starts
    with an empty context, so every structured log line emitted from a turn, a
    plan or a REPL job carried an empty `request_id`: the id a user quotes off
    a failed request matched nothing in the log for the work that failed, which
    is the one place it was supposed to help.

    Two halves, because a unit test of the helper would prove the helper and
    the defect was that the spawn sites did not use one: the behaviour is
    asserted here, and that the three request-serving spawns actually go
    through it is asserted in the companion test below.
    """
    from openai4s.observability import (
        carry_context,
        correlation_id,
        new_correlation_id,
        reset_correlation_id,
        set_correlation_id,
    )

    request_id = new_correlation_id()
    token = set_correlation_id(request_id)
    try:
        captured: list[str] = []
        thread = threading.Thread(
            target=carry_context(lambda: captured.append(correlation_id()))
        )
        thread.start()
        thread.join(5)
        assert captured == [request_id], "the spawn helper did not carry the id"

        # ...and a bare thread still does not, which is what makes the helper
        # load-bearing rather than decorative.
        bare: list[str] = []
        plain = threading.Thread(target=lambda: bare.append(correlation_id()))
        plain.start()
        plain.join(5)
        assert bare == [""], "a bare thread carried the id; the helper is moot"

        # The job records the id it was built under, so the failure a user
        # reads and the log line for the failed work share one id.
        job = gateway_mod.MessageJob("job-1", "root-1")
        assert job.request_id == request_id
        job.finish(error="boom")
        assert job.wait_result()["request_id"] == request_id
    finally:
        reset_correlation_id(token)


def test_every_request_serving_spawn_goes_through_the_helper():
    """The half that would have caught the original defect.

    `carry_context` working proves nothing if the spawn sites do not call it,
    and that is exactly the state this started in. Read as source because the
    threads are created inside closures that a real turn would have to reach --
    and a test that has to run a whole turn to check one keyword argument
    tends not to be written at all.
    """
    import inspect
    import re as _re

    source = inspect.getsource(gateway_mod)
    unwrapped = []
    for name in ("openai4s-turn-", "openai4s-plan-", "openai4s-repl-"):
        index = source.find(name)
        assert index > 0, f"the {name} spawn site moved; this test cannot see it"
        window = source[max(0, index - 400) : index]
        # The nearest preceding `target=` is this Thread's.
        targets = _re.findall(r"target=(\w+)", window)
        if not targets or targets[-1] != "carry_context":
            unwrapped.append(name)
    assert not unwrapped, (
        "these request-serving threads do not carry the caller's correlation "
        f"id: {unwrapped}"
    )


def test_a_job_built_outside_a_request_mints_its_own_id_rather_than_none():
    """A direct submit -- the CLI, a recovery replay -- has no HTTP request
    behind it, and this used to leave the field off entirely. That was the
    smaller of two wrongs: `run_message` minted its own id for the socket
    regardless, so the 202 and the job query were nameless while the stream
    carried an id nothing else knew. One id the caller can quote everywhere
    beats an honest absence that the next layer contradicts."""
    from openai4s.observability import reset_correlation_id, set_correlation_id

    token = set_correlation_id("")
    try:
        job = gateway_mod.MessageJob("job-2", "root-2")
        assert job.request_id, "a job built outside a request has no id at all"
        job.finish(error="boom")
        result = job.wait_result()
        assert result["request_id"] == job.request_id
        assert result["error"] == "boom"

        # Portable: it travels in a header, a JSON body and a log line, so it
        # has to survive all three unescaped.
        assert re.fullmatch(r"[A-Za-z0-9_-]{8,64}", job.request_id), job.request_id

        # And minted per job, not shared. Two turns that cannot be told apart
        # are the defect this id exists to close.
        other = gateway_mod.MessageJob("job-3", "root-2")
        assert other.request_id != job.request_id
    finally:
        reset_correlation_id(token)


def test_daemon_lifetime_threads_do_not_inherit_a_request_id():
    """The sweepers are deliberately left alone.

    A thread that lives as long as the daemon is not serving the request that
    happened to start it. Stamping every later sweep with that request's id
    would be a false attribution, which is worse than a missing one because it
    gets believed -- the same failure this whole batch has been removing.
    """
    import inspect

    source = inspect.getsource(gateway_mod)
    for name in ("openai4s-kernel-idle-sweeper", "openai4s-share-sweeper"):
        index = source.find(name)
        if index < 0:
            continue
        window = source[max(0, index - 400) : index]
        assert "carry_context" not in window, (
            f"{name} is a daemon-lifetime thread and must not inherit a "
            "request's correlation id"
        )
