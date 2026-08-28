"""Which end of a long conversation the workbench opens on.

`newest_first`, `before_seq`, `next_before_seq` and `has_earlier` were built
through the store, the repository and the route. `app.js` contained neither
`newest_first` nor `before_seq`: all four message fetches sent
`?from=0&limit=300|500`, which is the OLDEST page. A 640-message session opened
on messages 0–299 and the work you came back for was off the end.

`gateway.py` even carried a comment describing "the client asks for the newest
page" — describing a client nobody had written. That is the shape this whole
review round kept finding: the capability is real, tested and served, and
nothing reaches it.

Not fixed here, and worth saying: this fetches the newest page rather than
adding infinite scroll upward. `has_earlier` and `next_before_seq` are still
unread, so a session longer than the page still has older messages that the UI
cannot reach — but it now hides the OLD ones rather than the recent ones, which
is the right way round for a session you just reopened.
"""

from __future__ import annotations

import io
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
def long_session(tmp_path):
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=1,
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    project = runner.store.create_project(name="p", description="", context="")
    if isinstance(project, dict):
        project = project["project_id"]
    frame = runner.create_session(project)
    for index in range(640):
        runner.store.add_message(
            root_frame_id=frame, role="user", content=f"message-{index}"
        )

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
        handler.rfile = io.BytesIO(b"")
        handler._route("GET")
        return sent["body"]

    return frame, get


def test_the_old_query_really_did_show_the_beginning(long_session):
    """The defect, as the page it returned. Kept as a test so nobody re-adopts
    it thinking it was equivalent."""
    frame, get = long_session
    body = get(f"/frames/{frame}/messages?from=0&limit=300")
    contents = [m["content"] for m in body["messages"]]
    assert contents[0] == "message-0"
    assert "message-639" not in contents


def test_the_newest_page_contains_the_end_of_the_conversation(long_session):
    frame, get = long_session
    body = get(f"/frames/{frame}/messages?newest_first=1&limit=300")
    contents = [m["content"] for m in body["messages"]]
    assert "message-639" in contents
    assert "message-0" not in contents


def test_the_route_says_whether_older_messages_exist(long_session):
    """Reported rather than inferred from a short page — a page can be short
    because the branch projection hid rows."""
    frame, get = long_session
    body = get(f"/frames/{frame}/messages?newest_first=1&limit=300")
    assert body["has_earlier"] is True
    assert body["next_before_seq"]


# --------------------------------------------------------------------------
# the client
# --------------------------------------------------------------------------


def test_no_message_fetch_still_asks_for_the_oldest_page():
    """All four call sites, not just the one you notice first: opening a
    session, the export, and two panel reads."""
    for line in APP_JS.splitlines():
        if "/messages?" in line and not line.strip().startswith("//"):
            assert "from=0" not in line, line.strip()


def test_the_client_asks_for_the_newest_page():
    assert "newest_first=1" in APP_JS


def test_the_rows_are_put_back_into_reading_order():
    """The route returns them descending. Rendering that unsorted would show
    the conversation backwards — a worse bug than the one being fixed, and one
    that looks like a server fault."""
    start = APP_JS.index("async function fetchRecentMessages(")
    body = APP_JS[start : APP_JS.index("\n}", start)]
    assert "sort(" in body
    assert "a.seq" in body and "b.seq" in body


def test_every_call_site_goes_through_the_one_helper():
    """Four hand-rolled fetches are four chances to forget the sort."""
    assert APP_JS.count("fetchRecentMessages(") == 5  # 1 definition + 4 uses
