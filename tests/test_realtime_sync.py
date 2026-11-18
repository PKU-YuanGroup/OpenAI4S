"""Every tab viewing a session, and a reload of it, converge on one transcript.

A usability review opened one session in five tabs and ended a long turn with
five different transcripts. The pieces that were missing from the stream or
from the stored history are pinned here:

* the user's own message was inserted only into the sending tab's DOM, so every
  other tab watched the agent answer a question it never saw;
* approval decisions lived only in the live `await_permission` /
  `permission_resolved` events, so a reload showed none of them -- not even a
  denial and the reason typed with it.
"""

from __future__ import annotations

import http.client
import json
import threading
from types import SimpleNamespace

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth
from openai4s.server.gateway import WSHub
from tests._ports import bound_gateway_server

API = "/api/v1"


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

    def has_subscriber(self, root_frame_id: str) -> bool:
        del root_frame_id
        return False

    def drop_frame(self, root_frame_id: str) -> None:
        del root_frame_id


class _Conn:
    def __init__(self) -> None:
        self.subs: set[str] = set()
        self.alive = True
        self.sent: list[dict] = []

    def send_json(self, obj: dict) -> None:
        self.sent.append(obj)

    def refresh_visibility(self, root_frame_id: str):
        del root_frame_id
        return None

    def may_receive(self, root_frame_id: str) -> bool:
        del root_frame_id
        return True


def _cfg(tmp_path, **extra) -> Config:
    return Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        **extra,
    )


# --------------------------------------------------------------------------
# the user's message is an event
# --------------------------------------------------------------------------


def test_a_turn_broadcasts_the_user_message_it_stored(monkeypatch, tmp_path):
    hub = _Hub()
    runner = gateway_mod.SessionRunner(_cfg(tmp_path), hub)
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    runner.store.update_frame(frame_id, name="Existing test session")
    dispatcher = SimpleNamespace(last_output=None)

    def ensure_runtime(state):
        state.dispatcher = dispatcher
        state.messages = [{"role": "system", "content": "sys"}]
        return dispatcher

    def finish_silently(state, emit, visible):
        del emit, visible
        state.last_engine_completion = {"output": {"summary": "Done."}}
        state.last_model_prose = ""
        return "submitted"

    monkeypatch.setattr(runner, "_ensure_runtime", ensure_runtime)
    monkeypatch.setattr(runner, "_spawn_title_summary", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_loop", finish_silently)

    result = runner.run_message(frame_id, "default", "  compute the fold change  ")
    assert result["status"] == "completed"

    stored = [
        m
        for m in runner.store.list_branch_message_boundaries(frame_id)
        if m["role"] == "user"
    ]
    assert len(stored) == 1
    events = [e for e in hub.events if e.get("type") == "user_message"]
    assert len(events) == 1, [e.get("type") for e in hub.events]
    event = events[0]
    # The identity a reopened transcript and the sending tab match against.
    assert event["message_id"] == stored[0]["message_id"]
    assert event["content"] == stored[0]["content"]
    assert event["role"] == "user"
    assert event["frame_id"] == frame_id
    assert event["created_at"].endswith("Z")
    assert event["request_id"]
    # Before the answer starts: a tab that renders events in arrival order
    # shows the question above the narration.
    order = [e.get("type") for e in hub.events]
    assert order.index("user_message") < order.index("text_reset")
    processing = [
        e
        for e in hub.events
        if e.get("type") == "frame_update" and e.get("status") == "processing"
    ]
    assert processing and processing[0]["request_id"] == event["request_id"]


# --------------------------------------------------------------------------
# ... and the replay window keeps it
# --------------------------------------------------------------------------


def _turn_start(hub: WSHub, frame_id: str, execution_id: str = "exec-1") -> None:
    hub.broadcast(
        frame_id,
        {
            "type": "frame_update",
            "frame_id": frame_id,
            "status": "processing",
            "execution_id": execution_id,
        },
    )
    hub.broadcast(
        frame_id,
        {
            "type": "user_message",
            "frame_id": frame_id,
            "message_id": "m-1",
            "content": "q",
            "execution_id": execution_id,
        },
    )
    hub.broadcast(frame_id, {"type": "text_reset", "frame_id": frame_id})
    hub.broadcast(frame_id, {"type": "text_chunk", "frame_id": frame_id, "chunk": "A"})


def test_the_replay_window_keeps_the_turns_user_message_across_text_reset():
    hub = WSHub()
    _turn_start(hub, "f1")

    late = _Conn()
    hub.add(late)
    hub.subscribe("f1", late)
    replayed = [e["type"] for e in late.sent]
    assert replayed[0] == "replay_begin"
    assert replayed[1:4] == ["user_message", "text_reset", "text_chunk"]

    # A tab that saw the `processing` boundary and then dropped resumes with no
    # hole: the user message is the next number it is missing.
    back = _Conn()
    hub.add(back)
    hub.subscribe("f1", back, since_seq=1, epoch=hub.epoch)
    begin = back.sent[0]
    assert begin["type"] == "replay_begin"
    assert begin["gap"] is False
    assert [e["seq"] for e in back.sent[1:-1]] == [2, 3, 4]


def test_another_executions_user_message_is_not_carried_into_a_new_window():
    hub = WSHub()
    _turn_start(hub, "f1", execution_id="exec-1")
    hub.broadcast(
        "f1", {"type": "frame_update", "frame_id": "f1", "status": "completed"}
    )
    _turn_start(hub, "f1", execution_id="exec-2")
    late = _Conn()
    hub.add(late)
    hub.subscribe("f1", late)
    messages = [e for e in late.sent if e.get("type") == "user_message"]
    assert [e["execution_id"] for e in messages] == ["exec-2"]


# --------------------------------------------------------------------------
# approvals are history
# --------------------------------------------------------------------------


def _call(port, method, path, *, token):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    try:
        conn.request(method, API + path, headers={local_auth.TOKEN_HEADER: token})
        response = conn.getresponse()
        return response.status, json.loads(response.read() or b"{}")
    finally:
        conn.close()


def test_the_approvals_route_returns_every_decision_in_order(tmp_path):
    httpd, port = bound_gateway_server()
    cfg = _cfg(tmp_path, host="127.0.0.1", port=port)
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    httpd.RequestHandlerClass = gateway_mod.make_handler(cfg, runner.hub, runner)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    token = local_auth.load_or_mint(cfg.data_dir)
    store = runner.store
    try:
        frame_id = store.new_frame(kind="turn", project_id="default", status="ready")
        for index, (decision_id, created) in enumerate(
            (("perm-allowed", 1_000), ("perm-denied", 2_000), ("perm-open", 3_000))
        ):
            store.create_permission_request(
                decision_id=decision_id,
                root_frame_id=frame_id,
                frame_id=frame_id,
                project_id="default",
                tool="bash",
                target=f"echo {index}",
                payload={
                    "type": "await_permission",
                    "frame_id": frame_id,
                    "decision_id": decision_id,
                    "tool": "bash",
                    "kind": "bash",
                    "title": f"Run command {index}",
                    "input": {"command": f"echo {index}"},
                    "target": f"echo {index}",
                    "scopes": ["once", "conversation"],
                    # Ledger identity the card never showed.
                    "action_group_id": "ag-internal",
                },
                created_at=created,
                expires_at=10**13,
            )
        store.resolve_permission_request(
            "perm-allowed", state="allowed", scope="once", resolved_at=1_500
        )
        store.resolve_permission_request(
            "perm-denied",
            state="denied",
            scope="once",
            message="not on the shared drive",
            resolved_at=2_500,
        )

        status, body = _call(port, "GET", f"/frames/{frame_id}/approvals", token=token)
        assert status == 200, body
        assert body["root_frame_id"] == frame_id
        approvals = body["approvals"]
        assert [a["decision_id"] for a in approvals] == [
            "perm-allowed",
            "perm-denied",
            "perm-open",
        ]
        assert [a["state"] for a in approvals] == ["allowed", "denied", "pending"]
        denied = approvals[1]
        assert denied["message"] == "not on the shared drive"
        assert denied["title"] == "Run command 1"
        assert denied["input"] == {"command": "echo 1"}
        assert denied["created_at"].endswith("Z")
        assert denied["resolved_at"].endswith("Z")
        assert "action_group_id" not in denied

        status, missing = _call(port, "GET", "/frames/f-never/approvals", token=token)
        assert status == 404, missing
    finally:
        httpd.shutdown()
        httpd.server_close()
        runner.close()
