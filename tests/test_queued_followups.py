"""What a *queued* follow-up is allowed to say about itself, and what
cancelling one is allowed to touch.

The FIFO admission path already accepted follow-ups typed while a turn was
running -- it queued them, numbered them, and could cancel exactly one. None of
that was reachable from a browser, because a queued item had no description:
the ticket carried `{"reason": "user message"}` and nothing else, so three
queued follow-ups projected as three indistinguishable rows and "cancel the
middle one" had no way to say which one the middle one was.

Everything here drives the real handler. `SessionRunner.submit_message` and
`SessionRunner.cancel` were already correct in isolation; the gap was between
what they knew and what the route projected, which is exactly the gap a
direct method call cannot see. The last three tests pass on the unfixed tree on
purpose: they are the evidence that the exact, sibling-safe cancel this feature
is built on top of already works, so a later change that breaks it is caught
here rather than in the browser.
"""

from __future__ import annotations

import json
import threading

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth
from openai4s.server.model_profiles import ModelProfileService

pytestmark = pytest.mark.stubbed_backend


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


class _Client:
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

    def _call(self, method, path, body=None):
        handler = object.__new__(self._handler_class)
        handler._correlation_id = "req-queue"
        sent: dict = {}

        def _send(code, payload, ctype, extra=None):
            sent["code"] = code
            sent["body"] = json.loads(payload.decode("utf-8"))

        handler._send = _send
        handler.command = method
        handler.path = f"/api/v1{path}"
        handler.headers = {
            "Content-Length": "0",
            local_auth.TOKEN_HEADER: self._token,
        }
        handler._body = lambda: (body or {})
        handler._route(method)
        return sent["code"], sent["body"]

    def message(self, text):
        return self._call(
            "POST", f"/frames/{self.frame_id}/message", {"request": text, "wait": False}
        )

    def queue(self):
        code, body = self._call("GET", f"/frames/{self.frame_id}/execution-queue")
        assert code == 200, body
        return body

    def cancel(self, execution_id, owner):
        return self._call(
            "POST",
            f"/frames/{self.frame_id}/cancel",
            {"execution_id": execution_id, "owner": owner},
        )

    def use_a_model_profile(self):
        """Give the session something to freeze, and return its id.

        Without a profile every binding is `("", 0)` and an assertion that the
        queue reports the right one would hold for the wrong reason.
        """
        service = ModelProfileService(
            self.store, self.cfg, providers=lambda: gateway_mod.PROVIDERS
        )
        profile = service.create(
            {
                "name": "queued-under-this",
                "provider": "chatgpt",
                "base_url": "https://example.invalid/v1",
                "model": "gpt-test",
                "api_key": "sk-queue-test",
            }
        )
        service.activate(profile["id"])
        return profile["id"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A session whose turns never actually run.

    The queue is the subject; letting an admitted turn execute would spawn a
    real agent loop against a store the fixture then closes and would attempt a
    live LLM call from an offline suite.
    """
    monkeypatch.setattr(
        gateway_mod.SessionRunner,
        "run_message",
        lambda self, *a, **k: {"status": "ok", "frame_id": a[0] if a else ""},
    )
    return _Client(tmp_path)


def _hold_the_head(client):
    """Occupy the queue head so accepted turns stay queued and never run."""

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
        # Drain first, release second. Releasing the head with items still
        # queued promotes one of them, and the teardown would then be racing a
        # turn it just told the test had never started.
        client.runner.executions.drain_queued(client.frame_id, reason="test teardown")
        release.set()
        thread.join(10)
        assert not thread.is_alive()

    return ticket, _finish


def _queue_three(client):
    accepted = []
    for text in ("first follow-up", "second follow-up", "third follow-up"):
        code, body = client.message(text)
        assert code == 202, body
        accepted.append(body)
    return accepted


# --------------------------------------------------------------------------
# a queued item describes itself
# --------------------------------------------------------------------------


def test_a_queued_follow_up_carries_its_id_preview_and_position(client):
    """The three things a cancel control needs before it can offer a choice.

    Position alone is not enough: it renumbers the moment anything ahead
    finishes, so a row identified only by its position names a different item a
    second later than it did when the user read it.
    """
    _held, finish = _hold_the_head(client)
    try:
        accepted = _queue_three(client)
        queue = client.queue()["queue"]
        assert [item["queue_position"] for item in queue] == [1, 2, 3]
        assert [item["execution_id"] for item in queue] == [
            body["execution_id"] for body in accepted
        ]
        assert [item["metadata"]["preview"] for item in queue] == [
            "first follow-up",
            "second follow-up",
            "third follow-up",
        ]
        assert [item["owner"] for item in queue] == [
            body["owner"] for body in accepted
        ], "the queue must name the exact owner the 202 told the client to cancel with"
    finally:
        finish()


def test_a_queued_item_reports_the_profile_it_was_admitted_under(client):
    """Not the frame's pin. `POST /frames/{id}/model-binding` rewrites that
    while an item waits -- it is the documented answer to a dangling pin -- so a
    projection that re-read the frame would show a queued item running under a
    configuration it was never accepted under, after the client had already
    been answered 202 under the other one."""
    profile_id = client.use_a_model_profile()
    _held, finish = _hold_the_head(client)
    try:
        code, _body = client.message("queued under the original profile")
        assert code == 202
        frozen = client.queue()["queue"][0]["metadata"]
        assert frozen["model_profile_id"] == profile_id
        assert frozen["model_profile_revision"] >= 1

        client.store.update_frame(
            client.frame_id,
            model_profile_id="mp-somethingelse",
            model_profile_revision=99,
        )
        still = client.queue()["queue"][0]["metadata"]
        assert (still["model_profile_id"], still["model_profile_revision"]) == (
            frozen["model_profile_id"],
            frozen["model_profile_revision"],
        )
    finally:
        finish()


def test_a_queued_item_reports_the_branch_it_was_admitted_on(client):
    branch = client.runner._state(client.frame_id, client.project_id).branch_id
    _held, finish = _hold_the_head(client)
    try:
        client.message("on this branch")
        assert client.queue()["queue"][0]["branch_id"] == branch
    finally:
        finish()


def test_the_preview_is_one_short_line_and_not_the_whole_message(client):
    """The snapshot is rebroadcast to every subscriber on every queue change,
    so the preview is bounded independently of `MAX_MESSAGE_CHARS`."""
    assert gateway_mod.queue_preview("  a\n b  ") == "a b"
    long_line = "x" * 5_000
    assert len(gateway_mod.queue_preview(long_line)) == (
        gateway_mod.QUEUE_PREVIEW_CHARS
    )
    _held, finish = _hold_the_head(client)
    try:
        client.message("keep\nthis\nshort " + long_line)
        preview = client.queue()["queue"][0]["metadata"]["preview"]
        assert len(preview) <= gateway_mod.QUEUE_PREVIEW_CHARS
        assert preview.startswith("keep this short ")
    finally:
        finish()


# --------------------------------------------------------------------------
# cancelling exactly one of them
# --------------------------------------------------------------------------


def test_cancelling_the_middle_queued_item_leaves_the_rest_in_fifo_order(client):
    """The acceptance case: queue three, drop the middle one, and the first and
    third must still run, in that order."""
    _held, finish = _hold_the_head(client)
    try:
        accepted = _queue_three(client)
        code, result = client.cancel(accepted[1]["execution_id"], accepted[1]["owner"])
        assert code == 200 and result["ok"] is True, result
        assert result["scope"] == "queued"

        queue = client.queue()["queue"]
        assert [item["metadata"]["preview"] for item in queue] == [
            "first follow-up",
            "third follow-up",
        ]
        assert [item["queue_position"] for item in queue] == [1, 2]
    finally:
        finish()


def test_cancelling_a_queued_item_does_not_stop_the_running_one(client):
    """A queued cancellation is not a Stop. The running execution keeps its
    ticket and its cancellation flag stays clear."""
    held, finish = _hold_the_head(client)
    try:
        accepted = _queue_three(client)
        client.cancel(accepted[1]["execution_id"], accepted[1]["owner"])
        snapshot = client.queue()
        assert snapshot["owner"]["execution_id"] == held.execution_id
        assert snapshot["owner"]["cancel_requested"] is False
        assert held.cancellation.is_set() is False
    finally:
        finish()


def test_cancelling_a_queued_item_needs_that_item_s_own_owner(client):
    """Exactness is the whole safety property. An execution id paired with a
    sibling's owner must be refused rather than resolved to whichever ticket
    matched one half."""
    _held, finish = _hold_the_head(client)
    try:
        accepted = _queue_three(client)
        _code, result = client.cancel(accepted[1]["execution_id"], accepted[0]["owner"])
        assert result["ok"] is False
        assert [item["queue_position"] for item in client.queue()["queue"]] == [1, 2, 3]
    finally:
        finish()
