"""Stopping and steering a sub-agent from outside the agent.

`_stop_subtree` and `send_message` were already correct and already tested: the
walk follows `parent_child_id`, so a sibling is structurally outside it rather
than spared by a filter somebody has to remember, and steering queues for the
next turn boundary rather than landing mid-tool-call. Neither was reachable.
A user watching a sub-agent tree could see a runaway child and had no way to
stop it without stopping the whole session.

The status codes are the substance here. Three answers that must stay apart:

    404  no such child in the durable record — it never existed here
    409  the record has it, but nothing live can act on it
    200  done

The 409 is ordinary rather than exotic. A daemon restart marks every
pending/running child `stopped` with `stop_reason='daemon_restart'` and
discards queued steering, so any page opened before the restart is holding ids
for children that are gone. Answering 404 would tell the user that work they
watched run never existed; answering 200 would claim a stop that stopped
nothing.
"""

from __future__ import annotations

import json

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

    def post(self, path, body=None):
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
        handler._body = lambda: (body or {})
        handler._route("POST")
        return sent["code"], sent["body"]

    def seed_child(self, child_id, status="running"):
        """A durable child record with no live runner behind it.

        This is exactly the post-restart state: the record survived, the
        process that owned it did not.
        """
        self.store.restore_delegation_tree(
            root_frame_id=self.frame_id,
            owner_instance_id="owner-1",
            runner_instance_id="runner-1",
            budget_limit=48,
        )
        # Rows are created by the reservation (which is also where the spawn
        # cap is enforced); `persist_delegation_child` only ever *updates* one
        # and returns None for a row that does not exist — so seeding through
        # it alone produced an empty tree and a 404 where the test wanted 409.
        reserved = self.store.reserve_delegation_children(
            root_frame_id=self.frame_id,
            owner_instance_id="owner-1",
            runner_instance_id="runner-1",
            count=1,
            depth=1,
            parent_child_id=None,
        )
        real_id = (reserved.get("child_ids") or [None])[0]
        assert real_id, "reservation produced no child id"
        self.store.persist_delegation_child(
            root_frame_id=self.frame_id,
            owner_instance_id="owner-1",
            runner_instance_id="runner-1",
            child={
                "child_id": real_id,
                "name": "worker",
                "status": status,
                "depth": 1,
                "parent_child_id": None,
                "frame_id": None,
                "request": "do the thing",
            },
            messages=[],
        )
        return real_id


@pytest.fixture
def client(tmp_path):
    return _Client(tmp_path)


# --------------------------------------------------------------------------
# the three answers stay apart
# --------------------------------------------------------------------------


def test_a_child_that_never_existed_is_a_not_found(client):
    status, body = client.post(f"/frames/{client.frame_id}/delegations/nope/stop")
    assert status == 404
    assert body["code"] == "not_found"


def test_a_record_whose_runner_is_gone_is_a_conflict_not_a_not_found(client):
    """The post-restart case. Saying 404 would tell the user that work they
    watched run never existed."""
    child_id = client.seed_child("child-1")
    status, body = client.post(f"/frames/{client.frame_id}/delegations/{child_id}/stop")
    assert status == 409
    assert body["code"] == "delegation_record_stale"
    assert "no longer active" in body["error"] or "cannot be steered" in body["error"]


def test_the_same_distinction_holds_for_steering(client):
    child_id = client.seed_child("child-1")
    status, body = client.post(
        f"/frames/{client.frame_id}/delegations/{child_id}/steer",
        {"message": "focus on the second dataset"},
    )
    assert status == 409
    assert body["code"] == "delegation_record_stale"

    status, body = client.post(
        f"/frames/{client.frame_id}/delegations/ghost/steer", {"message": "hello"}
    )
    assert status == 404


def test_a_stop_never_reports_success_it_did_not_achieve(client):
    """The failure this replaces: a 200 for a child nothing can reach reads as
    "stopped" to every client, and the user stops watching."""
    child_id = client.seed_child("child-1")
    status, _body = client.post(
        f"/frames/{client.frame_id}/delegations/{child_id}/stop"
    )
    assert status != 200


# --------------------------------------------------------------------------
# steering input
# --------------------------------------------------------------------------


def test_an_empty_steering_message_is_refused_before_anything_is_looked_up(client):
    """A blank steer would queue a message that says nothing and consume the
    child's next turn boundary to deliver it."""
    status, body = client.post(
        f"/frames/{client.frame_id}/delegations/child-1/steer", {"message": "   "}
    )
    assert status == 400
    assert body["code"] == "bad_request"


def test_a_steering_message_is_bounded_like_any_other_message(client):
    """Same cap as a user turn, for the same reason: it is prepended to the
    child's context and replayed for the rest of its run."""
    status, body = client.post(
        f"/frames/{client.frame_id}/delegations/child-1/steer",
        {"message": "x" * (8 * 1024 * 1024)},
    )
    assert status == 413
    assert body["code"] == "message_too_large"


# --------------------------------------------------------------------------
# the routes exist at all
# --------------------------------------------------------------------------


def test_both_controls_are_posts_on_the_real_surface():
    """They mutate a running agent, so neither may be a GET a browser can make
    on navigation or prefetch."""
    from openai4s.server import contract

    routes = contract.http_routes()
    assert any("delegations" in r and r.endswith("stop") for r in routes)
    assert any("delegations" in r and r.endswith("steer") for r in routes)


# --------------------------------------------------------------------------
# the success path
# --------------------------------------------------------------------------


class _LiveRunner:
    """Stands in for a delegation runner that owns a live child.

    Deliberately a stub. What the routes above add is *status mapping* — which
    refusal becomes 404 and which becomes 409, and that a real stop is not
    reported through the same channel as a failed one. `_stop_subtree`'s own
    behaviour (the walk follows `parent_child_id`, so a sibling is outside it)
    is covered against the real runner and a real kernel in
    `test_delegation_runtime.py`, and re-staging that here would test the
    constructor rather than the wiring.

    Marked `stubbed_backend` so the response-schema recorder is paused: a
    fabricated body frozen into `docs/response-schemas.json` would be
    provenance that is wrong rather than absent.
    """

    class _Lock:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _Tree:
        lock = None

    def __init__(self, child_id):
        self._tree = self._Tree()
        self._tree.lock = self._Lock()
        self._children = {child_id: object()}
        self.stopped: list[str] = []
        self.steered: list[dict] = []

    def _stop_subtree(self, child_id, reason):
        self.stopped.append(child_id)
        return {"child_id": child_id, "status": "stopped", "stop_reason": reason}

    def send_message(self, spec):
        self.steered.append(spec)
        return {
            "ok": True,
            "child_id": spec["child_id"],
            "message_id": 1,
            "status": "queued",
            "queued": 1,
            "delivered": False,
        }


@pytest.mark.stubbed_backend
def test_a_live_child_is_actually_stopped_through_the_route(client):
    """Without this every other test here passes on a handler wired to the
    wrong method — they all assert refusals, and a route that refuses
    everything refuses correctly."""
    child_id = client.seed_child("child-1")
    state = client.runner._state(client.frame_id, client.project_id)
    state.delegation_runner = _LiveRunner(child_id)

    status, body = client.post(f"/frames/{client.frame_id}/delegations/{child_id}/stop")
    assert status == 200
    assert body["status"] == "stopped"
    assert state.delegation_runner.stopped == [child_id]


@pytest.mark.stubbed_backend
def test_a_live_child_receives_the_steering_text_verbatim(client):
    child_id = client.seed_child("child-1")
    state = client.runner._state(client.frame_id, client.project_id)
    state.delegation_runner = _LiveRunner(child_id)

    status, body = client.post(
        f"/frames/{client.frame_id}/delegations/{child_id}/steer",
        {"message": "  use the 2024 release  "},
    )
    assert status == 200
    assert body["status"] == "queued"
    # Trimmed, not reworded: the child acts on this text.
    assert state.delegation_runner.steered == [
        {"child_id": child_id, "message": "use the 2024 release"}
    ]


@pytest.mark.stubbed_backend
def test_a_runner_that_refuses_the_steer_is_a_conflict_not_a_success(client):
    """`send_message` answers a refusal with `{"ok": False, …}`. Returned as
    200 that reads as delivered — the soft-dictionary shape again."""
    child_id = client.seed_child("child-1")
    state = client.runner._state(client.frame_id, client.project_id)
    runner = _LiveRunner(child_id)
    runner.send_message = lambda spec: {
        "ok": False,
        "status": "rejected",
        "reason": "child is stopped",
    }
    state.delegation_runner = runner

    status, body = client.post(
        f"/frames/{client.frame_id}/delegations/{child_id}/steer", {"message": "hello"}
    )
    assert status == 409
    assert body["code"] == "delegation_record_stale"
    assert "child is stopped" in body["error"]
