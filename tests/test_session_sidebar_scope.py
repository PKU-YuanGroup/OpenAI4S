"""Whether a project's sessions are visible when you open that project.

`loadSessions` fetched `/frames?limit=100` — globally, across every project —
and filtered by project in the browser. A project whose sessions sat outside
that global page therefore appeared empty, and `openProject` reads "empty" as
a reason to call `newSession()`. Switching to a quiet project presented a
blank new session while the user's work sat intact in SQLite.

Measured: 120 sessions in one project push a three-session project entirely
out of the global page. 0 of 3 visible before, 3 of 3 after.

The server has supported `project_id` — and cursor paging, and `next_cursor` —
the whole time. This was one unused query parameter.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth

APP_JS = Path("openai4s/server/webui/app.js").read_text(encoding="utf-8")


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


@pytest.fixture
def crowded(tmp_path):
    """A busy project that fills the page, and a quiet one that predates it."""
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=1,
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    store = runner.store
    store.create_project(name="busy", description="", context="")
    store.create_project(name="quiet", description="", context="")
    ids = {p["name"]: p["project_id"] for p in store.list_projects()}

    for index in range(3):
        frame = runner.create_session(ids["quiet"])
        store.add_message(root_frame_id=frame, role="user", content=f"quiet {index}")
    for index in range(120):
        frame = runner.create_session(ids["busy"])
        store.add_message(root_frame_id=frame, role="user", content=f"busy {index}")

    handler_class = gateway_mod.make_handler(cfg, _Hub(), runner)
    token = local_auth.read_token(tmp_path) or ""

    def get(path):
        handler = object.__new__(handler_class)
        handler._correlation_id = "req-1"
        sent: dict = {}
        handler._send = (
            lambda code, body, ctype, extra=None, security=None: sent.update(
                code=code, body=json.loads(body.decode("utf-8"))
            )
        )
        handler.command = "GET"
        handler.path = f"/api/v1{path}"
        handler.headers = {"Content-Length": "0", local_auth.TOKEN_HEADER: token}
        handler._route("GET")
        return sent["body"]

    return get, ids


def test_a_quiet_projects_sessions_survive_a_busy_neighbour(crowded):
    """The defect, as the two numbers that differ."""
    get, ids = crowded
    unscoped = get("/frames?limit=100")
    hidden = [
        f for f in unscoped.get("frames", []) if f.get("project_id") == ids["quiet"]
    ]
    assert hidden == [], "the busy project no longer fills the page; test is stale"

    scoped = get(f"/frames?limit=100&project_id={ids['quiet']}")
    visible = [f for f in scoped.get("frames", []) if not f.get("parent_frame_id")]
    assert len(visible) == 3


def test_the_client_asks_for_the_project_it_is_showing():
    """The server side always worked. The defect was entirely in which query
    the client sent, so this is where the regression would return."""
    start = APP_JS.index("async function loadSessions(")
    body = APP_JS[start : APP_JS.index("\n}", start)]
    assert "project_id=" in body, "loadSessions fetches every project's sessions"
    assert "S.project" in body


def test_the_project_is_set_before_the_sessions_are_fetched():
    """`openProject` assigns `S.project` and then loads. Reversed, the scope
    would be one project behind — which looks like the bug being fixed, only
    intermittently."""
    start = APP_JS.index("async function openProject(")
    body = APP_JS[start : APP_JS.index("\n}", start)]
    assert body.index("S.project = id") < body.index("loadSessions()")


def test_an_empty_project_may_still_open_a_new_session():
    """The fallthrough is not the bug and stays. Once the query is scoped,
    "no sessions" means the project has none — which is exactly when starting
    one is the right thing to do."""
    start = APP_JS.index("async function openProject(")
    body = APP_JS[start : APP_JS.index("\n}", start)]
    assert "newSession()" in body
