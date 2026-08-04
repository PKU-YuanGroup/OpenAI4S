"""Reaching the part of a project, and of a conversation, that is off the page.

Three separate states hid behind one heading, and only two of them were broken
in the same place:

  * The **session list** cursor was real on the server and had no caller. The
    route has answered with `next_cursor`/`has_more` since it was written and
    no line in `app.js` read either field, so the sidebar stopped at the newest
    100 sessions and offered nothing to press. This file proves the walk the
    client now performs: 260 sessions, every one of them, none twice, and a
    session arriving mid-walk that does not disturb the cursor.

  * The **message** page was newest-first on the server, with `next_before_seq`
    and `has_earlier` beside it, and again no caller — so in a 640-message
    session the older 340 were paged for and unreachable.

  * The **export** was reported as already carrying every branch. It does, and
    it is exercised here rather than read: the whole archive, from a live
    route, first message and last, both branches.

The one server defect the walk exposed is the message route's page size. A
`?limit=banana` raised `ValueError` out of the route and reached the browser as
a 500 `internal_error`, while `before_seq=banana` on the *same route* answered
400 — and `?limit=-5` answered 200 with an empty page and `has_earlier: false`,
which a paging client reads as the start of history. Both are asserted below
through the real handler, because a `GatewayError` raised from a method call
says nothing about the status the browser receives.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth

#: The plan's stated exit criteria, as the two numbers.
MANY_SESSIONS = 260
LONG_SESSION = 640


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


class _Client:
    """Drives the real request path, not a route method.

    A `GatewayError` raised out of a directly called handler method has already
    been observed to reach HTTP as a 200 elsewhere in this server, so every
    assertion about a status here goes through `_route`.
    """

    def __init__(self, cfg, runner, data_dir):
        self._handler = gateway_mod.make_handler(cfg, _Hub(), runner)
        self._token = local_auth.read_token(data_dir) or ""

    def raw(self, path):
        handler = object.__new__(self._handler)
        handler._correlation_id = "req-paging"
        sent: dict = {}
        handler._send = lambda code, body, ctype, extra=None: sent.update(
            code=code, body=body, ctype=ctype
        )
        handler.command = "GET"
        handler.path = f"/api/v1{path}"
        handler.headers = {
            "Content-Length": "0",
            local_auth.TOKEN_HEADER: self._token,
        }
        handler._route("GET")
        return sent

    def get(self, path):
        sent = self.raw(path)
        return sent["code"], json.loads(sent["body"].decode("utf-8"))


@pytest.fixture
def server(tmp_path):
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=1,
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    try:
        yield cfg, runner, _Client(cfg, runner, tmp_path)
    finally:
        runner.close()


def _project(runner, name="paging"):
    runner.store.create_project(name=name, description="", context="")
    return [p["project_id"] for p in runner.store.list_projects() if p["name"] == name][
        0
    ]


def _walk_sessions(client, project_id, *, page=100, max_pages=40):
    """Exactly the walk `loadSessions` now performs."""
    seen: list[str] = []
    cursor = None
    pages = 0
    while pages < max_pages:
        query = f"/frames?limit={page}&project_id={project_id}"
        if cursor:
            query += f"&cursor={cursor}"
        status, body = client.get(query)
        assert status == 200, body
        seen.extend(f["id"] for f in body["frames"] if not f.get("parent_frame_id"))
        pages += 1
        if not body["has_more"]:
            return seen, body, pages
        cursor = body["next_cursor"]
        assert cursor, "has_more with no cursor is a walk that cannot continue"
    raise AssertionError("the session walk did not terminate")


def test_the_session_cursor_reaches_all_260_sessions_exactly_once(server):
    _cfg, runner, client = server
    pid = _project(runner)
    created = [runner.create_session(pid) for _ in range(MANY_SESSIONS)]
    for index, frame in enumerate(created):
        runner.store.add_message(root_frame_id=frame, role="user", content=f"s{index}")

    first_status, first_page = client.get(f"/frames?limit=100&project_id={pid}")
    assert first_status == 200
    # The state the client was stuck in: one page, and 160 sessions with no
    # control that could reach them.
    assert len(first_page["frames"]) == 100
    assert first_page["has_more"] is True

    seen, last, pages = _walk_sessions(client, pid)
    assert pages == 3
    assert last["has_more"] is False
    assert len(seen) == MANY_SESSIONS, "the walk lost sessions"
    assert len(set(seen)) == MANY_SESSIONS, "the walk returned a session twice"
    assert set(seen) == set(created)


def test_a_session_created_mid_walk_does_not_disturb_the_cursor(server):
    """Why it is a keyset cursor and not an offset.

    These sessions are created in a tight loop, so many share a millisecond —
    the tie the `(created_at, frame_id)` key exists to break. With an offset,
    one arrival between page one and page two shifts every later page by a row.
    """
    _cfg, runner, client = server
    pid = _project(runner)
    created = [runner.create_session(pid) for _ in range(MANY_SESSIONS)]
    for index, frame in enumerate(created):
        runner.store.add_message(root_frame_id=frame, role="user", content=f"s{index}")

    status, page_one = client.get(f"/frames?limit=100&project_id={pid}")
    assert status == 200
    held = page_one["next_cursor"]
    seen = [f["id"] for f in page_one["frames"]]

    arrival = runner.create_session(pid)
    runner.store.add_message(root_frame_id=arrival, role="user", content="arrives now")

    cursor = held
    while cursor:
        status, body = client.get(f"/frames?limit=100&project_id={pid}&cursor={cursor}")
        assert status == 200
        seen.extend(f["id"] for f in body["frames"])
        cursor = body["next_cursor"] if body["has_more"] else None

    assert len(set(seen)) == len(seen), "the arrival made the walk repeat a row"
    # The arrival itself belongs to page one, which was already read; every
    # session that existed when the walk began is still accounted for.
    assert set(created) <= set(seen)
    assert arrival not in seen


def _seed_conversation(runner, project_id, count=LONG_SESSION):
    frame = runner.create_session(project_id)
    for index in range(count):
        runner.store.add_message(
            root_frame_id=frame,
            frame_id=frame,
            role="user",
            content=f"message {index}",
        )
    return frame


def test_walking_back_through_640_messages_covers_them_exactly_once(server):
    _cfg, runner, client = server
    frame = _seed_conversation(runner, _project(runner))

    status, newest = client.get(f"/frames/{frame}/messages?newest_first=1&limit=300")
    assert status == 200
    assert newest["messages"][0]["content"] == f"message {LONG_SESSION - 1}"
    assert newest["has_earlier"] is True

    collected = [m["content"] for m in newest["messages"]]
    cursor = newest["next_before_seq"]
    pages = 1
    while cursor is not None and pages < 20:
        status, body = client.get(
            f"/frames/{frame}/messages?limit=300&before_seq={cursor}"
        )
        assert status == 200
        collected.extend(m["content"] for m in body["messages"])
        pages += 1
        if not body["has_earlier"]:
            break
        cursor = body["next_before_seq"]

    assert len(collected) == LONG_SESSION, "the walk lost messages"
    assert len(set(collected)) == LONG_SESSION, "the walk returned a message twice"
    assert set(collected) == {f"message {i}" for i in range(LONG_SESSION)}


def test_a_message_arriving_mid_walk_does_not_shift_the_page(server):
    _cfg, runner, client = server
    frame = _seed_conversation(runner, _project(runner), count=400)

    status, newest = client.get(f"/frames/{frame}/messages?newest_first=1&limit=300")
    assert status == 200
    cursor = newest["next_before_seq"]

    status, before = client.get(
        f"/frames/{frame}/messages?limit=50&before_seq={cursor}"
    )
    assert status == 200
    runner.store.add_message(
        root_frame_id=frame, frame_id=frame, role="user", content="ARRIVES NOW"
    )
    status, after = client.get(f"/frames/{frame}/messages?limit=50&before_seq={cursor}")
    assert status == 200
    assert [m["seq"] for m in before["messages"]] == [
        m["seq"] for m in after["messages"]
    ]


def test_a_malformed_page_size_is_refused_instead_of_crashing(server):
    """`?limit=banana` reached the browser as a 500 `internal_error`.

    The same route already answered 400 `invalid_cursor` for `before_seq=banana`,
    and the session list answers 400 `invalid_limit`. A paging client cannot
    distinguish a 500 from a server that has fallen over, so it stops walking.
    """
    _cfg, runner, client = server
    frame = _seed_conversation(runner, _project(runner), count=5)

    status, body = client.get(f"/frames/{frame}/messages?limit=banana")
    assert status == 400, body
    assert body["code"] == "invalid_limit"

    status, body = client.get(f"/frames/{frame}/messages?from=banana")
    assert status == 400, body
    assert body["code"] == "invalid_limit"


def test_a_negative_page_size_is_not_reported_as_the_start_of_history(server):
    """`?limit=-5` answered 200, zero messages and `has_earlier: false`.

    That is the exact shape of "you have reached the beginning", so a client
    walking back would stop and show a conversation that starts in the middle.
    """
    _cfg, runner, client = server
    frame = _seed_conversation(runner, _project(runner), count=5)

    status, body = client.get(f"/frames/{frame}/messages?newest_first=1&limit=-5")
    assert status == 200
    assert body["messages"], "an empty page here is indistinguishable from the end"
    assert body["messages"][0]["content"] == "message 4"


def test_a_page_size_beyond_the_ceiling_is_clamped(server):
    """An unbounded `limit` is a whole-branch projection from a query string."""
    _cfg, runner, client = server
    frame = _seed_conversation(runner, _project(runner))

    status, body = client.get(
        f"/frames/{frame}/messages?newest_first=1&limit=99999999999"
    )
    assert status == 200
    assert len(body["messages"]) <= gateway_mod.MAX_MESSAGE_PAGE
    # Still a real page, and still the newest end of it.
    assert body["messages"][0]["content"] == f"message {LONG_SESSION - 1}"


def test_the_exported_package_carries_the_first_and_last_message_of_every_branch(
    server,
):
    """Exercised, not read.

    The claim under review was that the export already carries every branch's
    messages. It does — including the first and the last of a 640-message
    session and a forked branch's own message — and the archive is pulled from
    the live route rather than from the service, so the download path is part
    of what is asserted.
    """
    _cfg, runner, client = server
    store = runner.store
    frame = _seed_conversation(runner, _project(runner))
    first = store.list_messages(frame, limit=1)
    assert first and first[0]["content"] == "message 0"

    domain = runner.session_domain
    checkpoint = domain.create_checkpoint(frame)
    domain.fork_branch(
        frame,
        from_checkpoint_id=checkpoint["checkpoint_id"],
        branch_id="alternative",
        name="Alternative",
    )
    store.add_message(
        root_frame_id=frame,
        frame_id=frame,
        branch_id="alternative",
        role="user",
        content="ONLY ON THE FORK",
    )

    sent = client.raw(f"/frames/{frame}/session/export")
    assert sent["code"] == 200
    with zipfile.ZipFile(io.BytesIO(sent["body"])) as archive:
        session = json.loads(archive.read("session.json"))

    messages = session["messages"]
    contents = [m["content"] for m in messages]
    assert contents.count("message 0") == 1, "the first message is missing"
    assert contents.count(f"message {LONG_SESSION - 1}") == 1, "the last is missing"
    assert "ONLY ON THE FORK" in contents, "a branch's own messages were dropped"
    assert len(contents) == LONG_SESSION + 1
    assert len(set(m["message_id"] for m in messages)) == len(messages)
    # The identity must belong to the row it is attached to: the export zips two
    # independent reads together by position, so a misordered one would hand
    # every message someone else's id.
    truth = {
        row["message_id"]: row["content"]
        for row in store.list_message_boundaries(frame, limit=None)
    }
    assert {m["message_id"]: m["content"] for m in messages} == truth
    assert {m["branch_id"] for m in messages} == {frame, "alternative"}
