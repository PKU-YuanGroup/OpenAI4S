"""M-73: deleting a branch that is neither the current one nor the main line.

Driven over real HTTP against a real ``ThreadingHTTPServer``: a refusal raised
as a ``GatewayError`` only becomes a status in the dispatcher's envelope, and a
direct ``_api`` call would see an exception where the client sees a body.

A deleted branch is a tombstone. Every row it recorded stays, because a branch
forked from it projects those rows as its own prefix; these tests read that
prefix before and after the deletion and require it unchanged.
"""

from __future__ import annotations

import http.client
import json
import threading
import time
from urllib.parse import quote

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth, response_capture
from openai4s.server.session_package import session_import_quarantine_key
from tests._ports import bound_gateway_server

PROJECT = "project-branch-delete"


class _Hub:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emitter(self, root_frame_id):
        def emit(event):
            event.setdefault("root_frame_id", root_frame_id)
            self.events.append(event)

        return emit

    def broadcast(self, root_frame_id, event):
        self.emitter(root_frame_id)(event)

    def has_subscriber(self, root_frame_id):
        del root_frame_id
        return False

    def drop_frame(self, root_frame_id):
        del root_frame_id


class _Daemon:
    """A real gateway on a loopback port, with a turn loop that writes no LLM."""

    def __init__(self, tmp_path) -> None:
        self.httpd, port = bound_gateway_server()
        self.port = port
        self.cfg = Config(
            data_dir=tmp_path,
            llm=LLMConfig(provider="deepseek", api_key="test-key"),
            host="127.0.0.1",
            port=port,
        )
        self.hub = _Hub()
        self.runner = gateway_mod.SessionRunner(
            self.cfg, self.hub, start_idle_sweeper=False
        )
        self.store = self.runner.store
        self.frame_id = self.store.new_frame(
            kind="turn", project_id=PROJECT, status="ready"
        )
        self.store.update_frame(self.frame_id, name="branch deletion")
        self.httpd.RequestHandlerClass = gateway_mod.make_handler(
            self.cfg, self.hub, self.runner
        )
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.token = local_auth.load_or_mint(self.cfg.data_dir)
        turns = {"n": 0}

        def loop(st, _emit, _visible):
            turns["n"] += 1
            name = f"turn-{turns['n']}.txt"
            (st.workspace / name).write_text(name + "\n", encoding="utf-8")
            self.store.add_step(
                step_id=f"step-{turns['n']}",
                frame_id=self.frame_id,
                kind="artifact",
                title=f"Saving {name}",
                status="done",
            )
            st.dispatcher.last_output = {"output": "done"}
            return "submitted"

        self.runner._loop = loop

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            payload = None if body is None else json.dumps(body).encode("utf-8")
            headers = {local_auth.TOKEN_HEADER: self.token}
            if payload is not None:
                headers["Content-Type"] = "application/json"
            conn.request(method, "/api/v1" + path, body=payload, headers=headers)
            response = conn.getresponse()
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
        finally:
            conn.close()

    def turn(self, text):
        result = self.runner.run_message(self.frame_id, PROJECT, text)
        assert result["status"] == "completed", result

    def user_message_id(self, text):
        code, body = self.request("GET", f"/frames/{self.frame_id}/messages")
        assert code == 200, body
        return next(
            m["message_id"]
            for m in body["messages"]
            if m["role"] == "user" and m["content"] == text
        )

    def fork_from_message(self, text):
        code, body = self.request(
            "POST",
            f"/frames/{self.frame_id}/branches/fork",
            {"from_message_id": self.user_message_id(text)},
        )
        assert code == 200, body
        return body["branch_id"]

    def activate(self, branch_id):
        code, body = self.request(
            "POST",
            f"/frames/{self.frame_id}/branches/{quote(branch_id, safe='')}/activate",
            {},
        )
        assert code == 200, body

    def delete(self, branch_id, frame_id=None):
        frame = frame_id or self.frame_id
        return self.request(
            "DELETE", f"/frames/{frame}/branches/{quote(branch_id, safe='')}"
        )

    def listed(self):
        code, body = self.request("GET", f"/frames/{self.frame_id}/branches")
        assert code == 200, body
        return [branch["branch_id"] for branch in body["branches"]], body

    def history(self):
        code, messages = self.request("GET", f"/frames/{self.frame_id}/messages")
        assert code == 200, messages
        code, steps = self.request("GET", f"/frames/{self.frame_id}/steps")
        assert code == 200, steps
        return (
            [m["content"] for m in messages["messages"] if m["role"] == "user"],
            [step["title"] for step in steps["steps"]],
        )

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.runner.close()


@pytest.fixture
def daemon(tmp_path):
    instance = _Daemon(tmp_path)
    try:
        yield instance
    finally:
        instance.close()


def _fork_chain(daemon):
    """main: one, two. B forked at two: three. C forked at three: four.

    C's head is the cursor checkpoint B took for "message three", so C's
    history inherits a row only B ever wrote. C is left active.
    """

    daemon.turn("message one")
    daemon.turn("message two")
    branch_b = daemon.fork_from_message("message two")
    daemon.activate(branch_b)
    daemon.turn("message three")
    branch_c = daemon.fork_from_message("message three")
    daemon.activate(branch_c)
    daemon.turn("message four")
    return branch_b, branch_c


def test_deleting_a_fork_keeps_the_prefix_its_own_fork_inherits(daemon):
    branch_b, branch_c = _fork_chain(daemon)
    root = daemon.frame_id
    listed, before_view = daemon.listed()
    assert set(listed) == {root, branch_b, branch_c}
    assert before_view["current_branch_id"] == branch_c
    before = daemon.history()
    # The test is only meaningful if C really shows a row B wrote.
    assert "message three" in before[0]
    assert "message four" in before[0]

    code, body = daemon.delete(branch_b)
    assert code == 200, body
    assert body["ok"] is True
    assert body["branch_id"] == branch_b
    assert isinstance(body["deleted_at"], int)

    listed, after_view = daemon.listed()
    assert listed == [root, branch_c]
    assert after_view["current_branch_id"] == branch_c
    # Nothing C inherited from B went with it.
    assert daemon.history() == before
    # The open UI is told to refresh its branch list.
    assert any(
        event.get("type") == "branch_deleted" and event.get("branch_id") == branch_b
        for event in daemon.hub.events
    )

    # Gone for every purpose: not deletable twice, not switchable to.
    code, body = daemon.delete(branch_b)
    assert (code, body["code"]) == (404, "branch_not_found")
    code, _body = daemon.request(
        "POST",
        f"/frames/{root}/branches/{quote(branch_b, safe='')}/activate",
        {},
    )
    assert code != 200
    assert daemon.store.active_session_branch(root) == branch_c


def test_the_current_branch_and_the_main_line_are_refused_with_409(daemon):
    branch_b, branch_c = _fork_chain(daemon)
    root = daemon.frame_id

    code, body = daemon.delete(branch_c)
    assert (code, body["code"]) == (409, "branch_active")
    code, body = daemon.delete(root)
    assert (code, body["code"]) == (409, "branch_main_line")
    assert set(daemon.listed()[0]) == {root, branch_b, branch_c}

    # The main line stays undeletable when it is not the current branch.
    daemon.activate(branch_b)
    code, body = daemon.delete(root)
    assert (code, body["code"]) == (409, "branch_main_line")
    # And the branch that was current a moment ago now can go.
    code, body = daemon.delete(branch_c)
    assert code == 200, body
    assert daemon.listed()[0] == [root, branch_b]


def test_unknown_branches_and_sessions_are_404(daemon):
    daemon.turn("message one")
    root = daemon.frame_id

    code, body = daemon.delete("br-0000000000000000")
    assert (code, body["code"]) == (404, "branch_not_found")
    other = daemon.store.new_frame(kind="turn", project_id=PROJECT, status="ready")
    branch = daemon.fork_from_message("message one")
    # Another session's branch is not this one's.
    code, body = daemon.delete(branch, frame_id=other)
    assert (code, body["code"]) == (404, "branch_not_found")
    code, body = daemon.delete(branch, frame_id="f-missing")
    assert code == 404
    assert body["error"] == "session not found"
    assert set(daemon.listed()[0]) == {root, branch}


def test_a_running_turn_refuses_the_deletion_with_409(daemon):
    daemon.turn("message one")
    branch = daemon.fork_from_message("message one")
    entered = threading.Event()
    release = threading.Event()
    plain_loop = daemon.runner._loop

    def held_loop(st, emit, visible):
        entered.set()
        release.wait(30)
        return plain_loop(st, emit, visible)

    daemon.runner._loop = held_loop
    worker = threading.Thread(target=daemon.turn, args=("message two",))
    worker.start()
    try:
        assert entered.wait(30)
        code, body = daemon.delete(branch)
        assert (code, body["code"]) == (409, "session_busy")
        assert branch in daemon.listed()[0]
    finally:
        release.set()
        worker.join(30)
    assert not worker.is_alive()
    deadline = time.time() + 10
    while time.time() < deadline and daemon.runner._execution_active(daemon.frame_id):
        time.sleep(0.05)
    code, body = daemon.delete(branch)
    assert code == 200, body


def test_a_quarantined_imported_session_refuses_the_deletion(daemon):
    daemon.turn("message one")
    branch = daemon.fork_from_message("message one")
    daemon.store.set_setting(session_import_quarantine_key(daemon.frame_id), "1")

    code, body = daemon.delete(branch)
    assert code == 423, body
    daemon.store.delete_setting(session_import_quarantine_key(daemon.frame_id))
    assert branch in daemon.listed()[0]


def test_a_package_round_trip_keeps_the_branch_deleted(daemon):
    branch_b, branch_c = _fork_chain(daemon)
    code, body = daemon.delete(branch_b)
    assert code == 200, body

    domain = daemon.runner.session_domain
    imported = domain.session_import(domain.session_export(daemon.frame_id)["data"])
    new_root = imported["root_frame_id"]
    view = domain.branches(new_root)
    # Three rows came across (C's parent must exist for C to be rebuilt), and
    # the one deleted at the source is deleted here too.
    assert len(daemon.store.list_session_branches(new_root)) == 3
    assert len(view["branches"]) == 2
    assert view["current_branch_id"] == imported["active_branch_id"]
    assert {branch["branch_id"] for branch in view["branches"]} == {
        new_root,
        imported["active_branch_id"],
    }


def test_the_route_does_not_take_over_its_neighbours_in_the_response_capture():
    """The schema capture files a path under the matching route with the most
    literal characters outside groups. A bare ``branches/([^/]+)`` pattern
    would out-rank the alternations that serve these three paths, and their
    frozen response shapes would be published under the delete route."""

    delete_route = r"/frames/([^/]+)/(?:branches/([^/]+))"
    assert response_capture.route_for("/frames/f-1/branches/br-1") == delete_route
    for neighbour in ("checkpoints", "revert", "revert-preview", "fork"):
        assert (
            response_capture.route_for(f"/frames/f-1/branches/{neighbour}")
            != delete_route
        ), neighbour
