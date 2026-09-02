"""Project directory keyset pages, escaped name/description LIKE, scoped cursors.

GET /projects used to ignore limit/offset, load every project, then COUNT/MAX
each one. This module is the page: 1000 real project rows still answer in
slices of at most 100, a filter or principal change invalidates the previous
cursor with 400 (never a silent restart at page one), and team visibility is
a WHERE conjunct so hidden rows cannot occupy slots or forge the end of the
list.
"""

from __future__ import annotations

import json
from urllib.parse import urlencode

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth
from openai4s.storage.frames import (
    PROJECT_PAGE_MAX,
    decode_project_cursor,
    encode_project_cursor,
    project_filter_fingerprint,
)
from openai4s.store import get_store

MANY_PROJECTS = 1000


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


class _Client:
    """Drives the real request path, not a route method."""

    def __init__(self, cfg, runner, data_dir):
        self._handler = gateway_mod.make_handler(cfg, _Hub(), runner)
        self._token = local_auth.read_token(data_dir) or ""

    def raw(self, path, *, visible_to=None):
        handler = object.__new__(self._handler)
        handler._correlation_id = "req-project-paging"
        sent: dict = {}
        handler._send = (
            lambda code, body, ctype, extra=None, security=None: sent.update(
                code=code, body=body, ctype=ctype
            )
        )
        handler.command = "GET"
        handler.path = f"/api/v1{path}"
        handler.headers = {
            "Content-Length": "0",
            local_auth.TOKEN_HEADER: self._token,
        }
        if visible_to is not None:
            handler._team_visibility_filter = lambda: visible_to
        handler._route("GET")
        return sent

    def get(self, path, *, visible_to=None):
        sent = self.raw(path, visible_to=visible_to)
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


def _seed_projects(store, n: int = MANY_PROJECTS) -> list[str]:
    """Insert ``n`` real project rows plus a root frame for last_active_at.

    Uses the live SQLite connection, not a mocked repository. A cluster of
    five shares one timestamp so the ``project_id`` tiebreaker is load-bearing.
    """
    now = 1_700_000_000_000
    tie_stamp = 1_111_111_111_111
    project_rows = []
    frame_rows = []
    ids: list[str] = []
    for index in range(n):
        project_id = f"p{index:04d}"
        ids.append(project_id)
        project_rows.append(
            (
                project_id,
                f"proj-{index:04d}",
                f"desc-{index:04d}",
                "",
                0,
                now,
                now,
            )
        )
        stamp = tie_stamp if index < 5 else 2_000_000_000_000 - index
        frame_id = f"f{index:04d}"
        frame_rows.append(
            (
                frame_id,
                None,
                project_id,
                frame_id,
                "turn",
                None,
                None,
                "ready",
                0,
                stamp,
                stamp,
            )
        )
    with store._lock:
        store._conn.executemany(
            "INSERT INTO projects(project_id,name,description,context,"
            "is_example,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            project_rows,
        )
        store._conn.executemany(
            "INSERT INTO frames(frame_id,parent_id,project_id,root_frame_id,"
            "kind,name,model,status,depth,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            frame_rows,
        )
        store._conn.commit()
    return ids


def _walk(client, **params):
    seen: list[str] = []
    cursor = None
    pages = 0
    while pages < 40:
        query = {"limit": params.get("limit", 100), **params}
        query.pop("cursor", None)
        if cursor:
            query["cursor"] = cursor
        path = "/projects?" + urlencode(query)
        status, body = client.get(path)
        assert status == 200, body
        seen.extend(row["project_id"] for row in body["projects"])
        pages += 1
        if not body["has_more"]:
            return seen, body, pages
        cursor = body["next_cursor"]
        assert cursor, "has_more with no cursor is a walk that cannot continue"
    raise AssertionError("the project walk did not terminate")


def _selects(statements: list[str]) -> list[str]:
    return [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith("SELECT")
        and "projects" in statement.lower()
    ]


def test_1000_projects_first_page_is_at_most_100_and_a_request_never_exceeds_100(
    server,
):
    _cfg, runner, client = server
    created = _seed_projects(runner.store)

    status, first = client.get("/projects?limit=100")
    assert status == 200
    assert len(first["projects"]) == 100
    assert first["has_more"] is True
    assert first["next_cursor"]
    assert first["total"] == MANY_PROJECTS

    status, capped = client.get("/projects?limit=500")
    assert status == 200
    assert len(capped["projects"]) == PROJECT_PAGE_MAX
    assert capped["has_more"] is True

    seen, last, pages = _walk(client, limit=100)
    assert pages == 10
    assert last["has_more"] is False
    assert last["next_cursor"] is None
    assert len(seen) == MANY_PROJECTS
    assert len(set(seen)) == MANY_PROJECTS
    assert set(seen) == set(created)


def test_unparameterized_request_still_returns_the_full_list(server):
    _cfg, runner, client = server
    created = _seed_projects(runner.store, n=12)
    status, body = client.get("/projects")
    assert status == 200
    assert "next_cursor" not in body
    assert "has_more" not in body
    assert body["total"] == len(body["projects"]) == 12
    assert {row["project_id"] for row in body["projects"]} == set(created)


def test_offset_is_executed_during_the_compat_window(server):
    _cfg, runner, client = server
    _seed_projects(runner.store, n=8)
    status, first = client.get("/projects?limit=3&offset=0")
    assert status == 200
    assert len(first["projects"]) == 3
    assert first["total"] == 8
    assert first["has_more"] is True
    status, second = client.get("/projects?limit=3&offset=3")
    assert status == 200
    first_ids = [row["project_id"] for row in first["projects"]]
    second_ids = [row["project_id"] for row in second["projects"]]
    assert not set(first_ids) & set(second_ids)
    status, tail = client.get("/projects?limit=3&offset=6")
    assert status == 200
    assert len(tail["projects"]) == 2
    assert tail["has_more"] is False


def test_keyset_covers_a_same_timestamp_tie_exactly_once(server):
    _cfg, runner, client = server
    created = _seed_projects(runner.store, n=5)
    seen, last, pages = _walk(client, limit=2)
    assert pages == 3
    assert last["has_more"] is False
    assert len(seen) == 5
    assert len(set(seen)) == 5
    assert set(seen) == set(created)
    assert seen == sorted(created, reverse=True)


def test_empty_last_active_at_sorts_last(server):
    _cfg, runner, client = server
    store = runner.store
    active = store.create_project(project_id="active", name="Active")
    idle = store.create_project(project_id="idle", name="Idle")
    frame = store.new_frame(
        project_id=active["project_id"], kind="turn", status="ready"
    )
    with store._lock:
        store._conn.execute(
            "UPDATE frames SET created_at=?, updated_at=? WHERE frame_id=?",
            (9_000, 9_000, frame),
        )
        store._conn.commit()
    status, body = client.get("/projects?limit=10")
    assert status == 200
    ids = [row["project_id"] for row in body["projects"]]
    assert ids.index(active["project_id"]) < ids.index(idle["project_id"])


def test_a_page_issues_at_most_two_selects_against_projects(server):
    _cfg, runner, client = server
    _seed_projects(runner.store, n=30)
    statements: list[str] = []
    runner.store._conn.set_trace_callback(statements.append)
    try:
        status, body = client.get("/projects?limit=10")
    finally:
        runner.store._conn.set_trace_callback(None)
    assert status == 200
    assert len(body["projects"]) == 10
    assert len(_selects(statements)) <= 2


def test_escaped_like_and_ascii_case_and_non_ascii_exact(server):
    _cfg, runner, client = server
    store = runner.store
    store.create_project(project_id="p-under", name="foo_bar", description="x")
    store.create_project(project_id="p-x", name="fooXbar", description="x")
    store.create_project(project_id="p-pct", name="foo%bar", description="x")
    store.create_project(project_id="p-case", name="Alpha Lab", description="notes")
    store.create_project(project_id="p-cafe", name="Café", description="latte")
    store.create_project(
        project_id="p-desc", name="other", description="needle in description"
    )
    store.create_project(project_id="p-bs", name="a\\b", description="x")
    store.create_project(
        project_id="p-ctx", name="zzz", description="", context="secret-context"
    )

    status, body = client.get("/projects?" + urlencode({"q": "foo_bar"}))
    assert status == 200
    assert [row["name"] for row in body["projects"]] == ["foo_bar"]
    assert body["total"] == 1

    status, body = client.get("/projects?" + urlencode({"q": "foo%bar"}))
    assert status == 200
    assert [row["name"] for row in body["projects"]] == ["foo%bar"]

    status, body = client.get("/projects?" + urlencode({"q": "  alpha  "}))
    assert status == 200
    assert [row["project_id"] for row in body["projects"]] == ["p-case"]

    status, body = client.get("/projects?" + urlencode({"q": "ALPHA"}))
    assert status == 200
    assert [row["project_id"] for row in body["projects"]] == ["p-case"]

    status, body = client.get("/projects?" + urlencode({"q": "café"}))
    assert status == 200
    assert [row["project_id"] for row in body["projects"]] == ["p-cafe"]

    status, body = client.get("/projects?" + urlencode({"q": "CAFÉ"}))
    assert status == 200
    assert body["projects"] == []
    assert body["total"] == 0

    status, body = client.get("/projects?" + urlencode({"q": "needle"}))
    assert status == 200
    assert [row["project_id"] for row in body["projects"]] == ["p-desc"]

    status, body = client.get("/projects?" + urlencode({"q": "a\\b"}))
    assert status == 200
    assert [row["project_id"] for row in body["projects"]] == ["p-bs"]

    status, body = client.get("/projects?" + urlencode({"q": "secret-context"}))
    assert status == 200
    assert body["projects"] == []
    assert body["total"] == 0


def test_q_longer_than_128_code_points_is_refused(server):
    _cfg, runner, client = server
    runner.store.create_project(name="ok")
    too_long = "é" * 129
    status, body = client.get("/projects?" + urlencode({"q": too_long}))
    assert status == 400
    assert body["code"] == "invalid_q"
    allowed = "é" * 128
    status, body = client.get("/projects?" + urlencode({"q": allowed}))
    assert status == 200


def test_changed_q_or_principal_invalidates_the_cursor(server):
    _cfg, runner, client = server
    store = runner.store
    for name in ("alpha-one", "alpha-two", "beta-one"):
        store.create_project(name=name, description=name)
    status, page = client.get("/projects?" + urlencode({"q": "alpha", "limit": 1}))
    assert status == 200
    held = page["next_cursor"]
    assert page["has_more"] is True

    status, next_page = client.get(
        "/projects?" + urlencode({"q": "alpha", "limit": 1, "cursor": held})
    )
    assert status == 200, next_page
    assert next_page["projects"]

    status, body = client.get("/projects?" + urlencode({"q": "beta", "cursor": held}))
    assert status == 400, body
    assert body["code"] == "invalid_cursor"
    assert "projects" not in body


def test_malformed_cursor_is_400_not_a_silent_first_page(server):
    _cfg, runner, client = server
    runner.store.create_project(name="visible")
    status, body = client.get("/projects?limit=10&cursor=not-a-cursor")
    assert status == 400
    assert body["code"] == "invalid_cursor"
    assert "projects" not in body


def test_limit_and_offset_errors(server):
    _cfg, runner, client = server
    runner.store.create_project(name="x")
    status, body = client.get("/projects?limit=banana")
    assert status == 400
    assert body["code"] == "invalid_limit"
    status, body = client.get("/projects?limit=-5")
    assert status == 400
    assert body["code"] == "invalid_limit"
    status, body = client.get("/projects?limit=1&offset=-1")
    assert status == 400
    assert body["code"] == "invalid_offset"
    fingerprint = project_filter_fingerprint(q="", team_scope="")
    cursor = encode_project_cursor(
        last_active_at=1, project_id="p0000", fingerprint=fingerprint
    )
    status, body = client.get(f"/projects?limit=1&offset=0&cursor={cursor}")
    assert status == 400
    assert body["code"] == "bad_request"


def test_team_visibility_is_applied_before_limit_and_does_not_fill_slots(server):
    """Hidden newer rows must not look like the end of the list, or occupy slots."""
    _cfg, runner, client = server
    store = runner.store
    alice = store.team.create_user(username="alice", password="fake-a")
    bob = store.team.create_user(username="bob", password="fake-b")
    alice_ids = []
    bob_ids = []
    for index in range(10):
        pid = f"alice-{index:02d}"
        store.create_project(project_id=pid, name=pid)
        store.governance.set_member(pid, alice["id"])
        frame = store.new_frame(project_id=pid, kind="turn", status="ready")
        with store._lock:
            store._conn.execute(
                "UPDATE frames SET created_at=?, updated_at=? WHERE frame_id=?",
                (1_000 + index, 1_000 + index, frame),
            )
            store._conn.commit()
        alice_ids.append(pid)
    for index in range(10):
        pid = f"bob-{index:02d}"
        store.create_project(project_id=pid, name=pid)
        store.governance.set_member(pid, bob["id"])
        frame = store.new_frame(project_id=pid, kind="turn", status="ready")
        with store._lock:
            store._conn.execute(
                "UPDATE frames SET created_at=?, updated_at=? WHERE frame_id=?",
                (2_000 + index, 2_000 + index, frame),
            )
            store._conn.commit()
        bob_ids.append(pid)

    status, page = client.get("/projects?limit=7", visible_to=alice["id"])
    assert status == 200
    ids = [row["project_id"] for row in page["projects"]]
    assert len(ids) == 7
    assert set(ids) <= set(alice_ids)
    assert set(ids).isdisjoint(bob_ids)
    assert page["total"] == 10
    assert page["has_more"] is True

    status, hidden_newest = client.get("/projects?limit=7")
    assert status == 200
    newest_ids = [row["project_id"] for row in hidden_newest["projects"]]
    assert set(newest_ids) <= set(bob_ids)

    alice_all = store._frames.list_projects(limit=200, visible_to_user_id=alice["id"])
    assert {row["project_id"] for row in alice_all} == set(alice_ids)
    bob_all = store._frames.list_projects(limit=200, visible_to_user_id=bob["id"])
    assert {row["project_id"] for row in bob_all} == set(bob_ids)
    assert store._frames.count_projects(visible_to_user_id=alice["id"]) == 10
    assert store._frames.count_projects(visible_to_user_id=bob["id"]) == 10


def test_another_principal_cannot_reuse_a_cursor(server):
    _cfg, runner, client = server
    store = runner.store
    alice = store.team.create_user(username="alice", password="fake-a")
    bob = store.team.create_user(username="bob", password="fake-b")
    for index in range(3):
        pid = f"shared-{index}"
        store.create_project(project_id=pid, name=pid)
        store.governance.set_member(pid, alice["id"])
        store.governance.set_member(pid, bob["id"])
        store.new_frame(project_id=pid, kind="turn", status="ready")
    status, page = client.get("/projects?limit=1", visible_to=alice["id"])
    assert status == 200
    held = page["next_cursor"]
    assert held
    status, body = client.get(f"/projects?limit=1&cursor={held}", visible_to=bob["id"])
    assert status == 400
    assert body["code"] == "invalid_cursor"
    assert "projects" not in body


def test_no_args_list_projects_still_aggregates_without_n_plus_one(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    try:
        first = store.create_project(project_id="alpha", name="Alpha")
        store.create_project(project_id="beta", name="Beta")
        store.new_frame(project_id=first["project_id"], kind="turn", status="ready")
        statements: list[str] = []
        store._conn.set_trace_callback(statements.append)
        try:
            rows = store.list_projects()
        finally:
            store._conn.set_trace_callback(None)
        by_id = {row["project_id"]: row for row in rows}
        assert by_id["alpha"]["conversation_count"] == 1
        assert by_id["beta"]["conversation_count"] == 0
        assert by_id["beta"]["last_active_at"] == by_id["beta"]["updated_at"]
        assert len(_selects(statements)) == 1
    finally:
        store.close()


def test_decode_rejects_a_fingerprint_mismatch_without_returning_none():
    fingerprint = project_filter_fingerprint(q="alpha", team_scope="user-a")
    other = project_filter_fingerprint(q="beta", team_scope="user-a")
    cursor = encode_project_cursor(
        last_active_at=1, project_id="p1", fingerprint=fingerprint
    )
    with pytest.raises(ValueError, match="filter mismatch"):
        decode_project_cursor(cursor, fingerprint=other)
    assert decode_project_cursor("", fingerprint=fingerprint) is None
    assert decode_project_cursor(None, fingerprint=fingerprint) is None
