"""What a session accepts while a turn is already running.

Three refusals that all used to arrive in the wrong place, or in the wrong
shape, or not at all:

* A message had no text limit. The only bound was `_MAX_JSON_BODY_BYTES` — the
  *session archive* cap, 128 MiB — standing in as a chat-message cap. The
  message is persisted and replayed into every later turn, so an 8 MiB paste
  is eight times the whole context window on its own and the session is
  bricked. Compaction cannot rescue it, because summarising the message means
  sending it.
* A session pinned to a deleted model revision was refused by
  `bind_model_revision` with a 409 — inside the worker thread, after the
  client had already been told the turn was accepted. Over HTTP it arrived as
  **200** with `{"status": "failed", "error": …}`, the same soft-dictionary
  shape six other routes were fixed for.
* A full queue raised `QueueDepthExceeded`, which reached the client as **500
  internal_error**. Nothing failed internally; the user asked for more than the
  cap, and a client that retries 5xx would loop against a queue that cannot
  accept anything until they wait or cancel.

Everything here drives the real handler, because every one of these bugs lived
in the gap between a function that behaved correctly and the route that
reported it.
"""

from __future__ import annotations

import json
import threading

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


class _Client:
    """The real Handler with only its byte sink replaced.

    Calling `SessionRunner.submit_message` directly would have passed against
    every defect above: each one is a mismatch between what the method does and
    what the route reports.
    """

    def __init__(self, tmp_path):
        self.cfg = Config(
            data_dir=tmp_path,
            llm=LLMConfig(provider="deepseek", api_key="test-key"),
            max_turns=1,
        )
        self.runner = gateway_mod.SessionRunner(self.cfg, _Hub())
        self.store = self.runner.store
        self.store.create_project(name="p", description="", context="")
        self.project_id = [p["project_id"] for p in self.store.list_projects()][0]
        self.frame_id = self.runner.create_session(self.project_id)
        self._handler_class = gateway_mod.make_handler(self.cfg, _Hub(), self.runner)
        self._token = local_auth.read_token(tmp_path) or ""

    def post(self, path, body):
        handler = object.__new__(self._handler_class)
        handler._correlation_id = "req-1"
        sent: dict = {}

        def _send(code, payload, ctype, extra=None):
            sent["code"] = code
            sent["body"] = json.loads(payload.decode("utf-8"))

        handler._send = _send
        handler.command = "POST"
        handler.path = f"/api/v1{path}"
        handler.headers = {
            "Content-Length": "0",
            local_auth.TOKEN_HEADER: self._token,
        }
        handler._body = lambda: body
        handler._route("POST")
        return sent["code"], sent["body"]

    def message(self, text, **extra):
        return self.post(f"/frames/{self.frame_id}/message", {"request": text, **extra})


@pytest.fixture
def client(tmp_path):
    return _Client(tmp_path)


# --------------------------------------------------------------------------
# the text budget
# --------------------------------------------------------------------------


def test_a_message_that_would_brick_the_session_is_refused(client):
    """Fixed sizes, not multiples of the constant under test.

    8 MiB is what the old path accepted; 200,000 is the limit. Writing this as
    `MAX_MESSAGE_CHARS + 1` would keep passing if the cap were raised to 128
    MiB, which is the state being fixed.
    """
    status, body = client.message("x" * (8 * 1024 * 1024))
    assert status == 413
    assert body["code"] == "message_too_large"
    assert "8,388,608" in body["error"] and "200,000" in body["error"]


def test_the_refusal_happens_before_anything_is_written_or_queued(client):
    """The point of refusing at admission. Persisting first and refusing after
    would leave the message in the history it was refused for — replayed into
    every later turn, exactly the outcome the cap exists to prevent."""
    client.message("x" * (8 * 1024 * 1024))
    assert client.store.list_messages(client.frame_id) == []
    assert client.runner.executions.snapshot(client.frame_id).get("queue") == []


def test_a_client_that_does_not_wait_is_refused_just_as_loudly(client):
    """`wait=false` answers 202 before the turn runs. If the check lived in the
    worker thread, this caller would be told "accepted" and would never learn
    otherwise."""
    status, body = client.message("x" * (8 * 1024 * 1024), wait=False)
    assert status == 413
    assert body["code"] == "message_too_large"


def test_an_ordinary_long_paste_still_goes_through(client):
    """A limit nobody can reach is not a limit; one that catches real use is a
    bug. 199,000 characters is a very long paste and must be accepted."""
    _state, finish = _hold_the_head(client)
    try:
        status, _body = client.message("x" * 199_000, wait=False)
        assert status == 202
    finally:
        finish()


def test_the_cap_governs_typed_text_and_not_referenced_files(client):
    """`@name` references are expanded inside `run_message`, long after
    admission, and carry their own `MAX_REF_BYTES` budget. If the cap were
    applied to the expanded text instead, eight legitimate references would be
    refused as one oversized message."""
    from openai4s.server import artifact_refs

    assert artifact_refs.MAX_REFS * artifact_refs.MAX_REF_BYTES > (
        gateway_mod.MAX_MESSAGE_CHARS
    ), "a full set of references exceeds the message cap, so the two must be separate"
    _state, finish = _hold_the_head(client)
    try:
        status, _body = client.message("look at @a.csv and @b.csv", wait=False)
        assert status == 202
    finally:
        finish()


# --------------------------------------------------------------------------
# frozen model identity
# --------------------------------------------------------------------------


def test_a_dangling_model_pin_is_a_conflict_not_a_two_hundred(client):
    """The defect this file was opened for. `bind_model_revision` raised 409
    correctly; it ran in the worker thread, so the client saw 200 and a failure
    dictionary — indistinguishable from success to anything checking status."""
    client.store.update_frame(
        client.frame_id, model_profile_id="prof-gone", model_profile_revision=7
    )
    status, body = client.message("continue please")
    assert status == 409
    assert body["code"] == "model_revision_unavailable"


def test_the_identity_is_frozen_when_send_is_pressed(client):
    """Not when the turn reaches the head of the queue. A follow-up typed
    against one configuration must not adopt another because the user changed
    profiles while it waited."""
    client.store.update_frame(
        client.frame_id, model_profile_id="prof-gone", model_profile_revision=7
    )
    status, _body = client.message("queued behind something", wait=False)
    assert status == 409, "the refusal waited for the dequeue instead of the send"


# --------------------------------------------------------------------------
# a full queue
# --------------------------------------------------------------------------


def _hold_the_head(client):
    """Occupy the queue head so an accepted turn stays queued and never runs.

    Every test here is about *admission*. Letting an accepted turn actually
    execute would spawn a real turn thread that outlives the test, write to a
    store the fixture then closes -- which surfaced as an intermittent
    `Cannot operate on a closed database` in whatever test ran next -- and
    attempt a live LLM call from an offline suite.
    """
    state = client.runner._state(client.frame_id, client.project_id)
    ticket = client.runner._queue_execution(
        state, owner="lifecycle", owner_id="hold", reason="test hold"
    )
    running = threading.Event()
    release = threading.Event()

    def _hold():
        with client.runner.executions.admitted(ticket, cancel_event=state.cancel):
            running.set()
            release.wait(30)

    thread = threading.Thread(target=_hold, daemon=True)
    thread.start()
    assert running.wait(5)

    def _finish():
        release.set()
        thread.join(10)
        assert not thread.is_alive()

    return state, _finish


def _fill_the_queue(client):
    state, finish = _hold_the_head(client)
    for index in range(64):
        client.runner._queue_execution(
            state, owner="agent", owner_id=f"j{index}", reason="fill"
        )
    return finish


def test_a_full_queue_says_so_instead_of_claiming_a_server_fault(client):
    finish = _fill_the_queue(client)
    try:
        status, body = client.message("one more", wait=False)
        assert (
            status == 429
        ), "500 tells a client to retry something that cannot succeed"
        assert body["code"] == "queue_full"
        assert "cancel a queued one" in body["error"]
    finally:
        finish()


def test_the_cap_is_reached_by_queueing_and_not_by_a_single_message(client):
    """A depth cap that a long message could trip would conflate two different
    limits and give the user the wrong instruction for whichever they hit."""
    finish = _fill_the_queue(client)
    try:
        status, body = client.message("x" * (8 * 1024 * 1024), wait=False)
        # text is checked first, because that refusal is about this message
        # rather than about the queue behind it
        assert status == 413
        assert body["code"] == "message_too_large"
    finally:
        finish()
