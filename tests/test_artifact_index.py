"""Project Artifact index: keyset pages, escaped filename LIKE, filter-bound cursors.

The legacy ``GET /projects/{pid}/artifacts`` array route stays a bare array.
This module is the new page: 500 rows still answer in slices of at most 50
(and never more than 100), a filter change invalidates the previous cursor,
and team visibility is a WHERE conjunct so a page of hidden rows cannot
masquerade as the end of the listing.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from urllib.parse import urlencode

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth
from openai4s.storage.migrations import (
    SCHEMA_VERSION,
    MigrationError,
    current_version,
    run_migrations,
)
from openai4s.store import Store, get_store

MANY_ARTIFACTS = 500


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


class _Client:
    def __init__(self, cfg, runner, data_dir):
        self._handler = gateway_mod.make_handler(cfg, _Hub(), runner)
        self._token = local_auth.read_token(data_dir) or ""

    def raw(self, path):
        handler = object.__new__(self._handler)
        handler._correlation_id = "req-artifact-index"
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


def _project(store, name="artifact-index"):
    store.create_project(name=name, description="", context="")
    return [p["project_id"] for p in store.list_projects() if p["name"] == name][0]


def _save(
    store,
    *,
    root_frame_id,
    project_id,
    filename,
    payload=b"x",
    content_type="text/plain",
    is_user_upload=False,
    artifact_id=None,
):
    return store.save_artifact(
        path=f"/tmp/{filename}",
        filename=filename,
        content_type=content_type,
        size_bytes=len(payload),
        checksum=hashlib.sha256(payload).hexdigest(),
        root_frame_id=root_frame_id,
        project_id=project_id,
        is_user_upload=is_user_upload,
        artifact_id=artifact_id,
    )


def _set_created_at(store, artifact_id: str, created_at: int) -> None:
    with store._lock:
        store._conn.execute(
            "UPDATE artifacts SET created_at=? WHERE artifact_id=?",
            (created_at, artifact_id),
        )
        store._conn.commit()


def _walk(client, project_id, **params):
    seen: list[str] = []
    cursor = None
    pages = 0
    while pages < 40:
        query = {"limit": params.get("limit", 50), **params}
        query.pop("cursor", None)
        if cursor:
            query["cursor"] = cursor
        path = f"/projects/{project_id}/artifact-index?{urlencode(query)}"
        status, body = client.get(path)
        assert status == 200, body
        seen.extend(row["artifact_id"] for row in body["artifacts"])
        pages += 1
        if not body["has_more"]:
            return seen, body, pages
        cursor = body["next_cursor"]
        assert cursor, "has_more with no cursor is a walk that cannot continue"
    raise AssertionError("the artifact-index walk did not terminate")


def test_500_artifacts_first_page_is_at_most_50_and_a_request_never_exceeds_100(
    server,
):
    _cfg, runner, client = server
    pid = _project(runner.store)
    frame = runner.create_session(pid)
    created = [
        _save(
            runner.store,
            root_frame_id=frame,
            project_id=pid,
            filename=f"file-{index:04d}.txt",
            payload=f"{index}".encode(),
        )["artifact_id"]
        for index in range(MANY_ARTIFACTS)
    ]

    status, first = client.get(f"/projects/{pid}/artifact-index")
    assert status == 200
    assert len(first["artifacts"]) == 50
    assert first["has_more"] is True
    assert first["next_cursor"]

    status, capped = client.get(f"/projects/{pid}/artifact-index?limit=500")
    assert status == 200
    assert len(capped["artifacts"]) == 100
    assert capped["has_more"] is True

    seen, last, pages = _walk(client, pid, limit=50)
    assert pages == 10
    assert last["has_more"] is False
    assert len(seen) == MANY_ARTIFACTS
    assert len(set(seen)) == MANY_ARTIFACTS
    assert set(seen) == set(created)


def test_keyset_covers_a_same_timestamp_tie_exactly_once(server):
    _cfg, runner, client = server
    pid = _project(runner.store)
    frame = runner.create_session(pid)
    stamp = 1_700_000_000_000
    created = []
    for index in range(5):
        row = _save(
            runner.store,
            root_frame_id=frame,
            project_id=pid,
            filename=f"tie-{index}.txt",
        )
        _set_created_at(runner.store, row["artifact_id"], stamp)
        created.append(row["artifact_id"])

    seen, last, pages = _walk(client, pid, limit=2)
    assert pages == 3
    assert last["has_more"] is False
    assert seen == sorted(created, reverse=True)
    assert len(seen) == 5


def test_escaped_like_treats_percent_and_underscore_as_literals(server):
    _cfg, runner, client = server
    pid = _project(runner.store)
    frame = runner.create_session(pid)
    _save(
        runner.store,
        root_frame_id=frame,
        project_id=pid,
        filename="foo_bar.csv",
        content_type="text/csv",
    )
    _save(
        runner.store,
        root_frame_id=frame,
        project_id=pid,
        filename="fooXbar.csv",
        content_type="text/csv",
    )
    _save(
        runner.store,
        root_frame_id=frame,
        project_id=pid,
        filename="foo%bar.csv",
        content_type="text/csv",
    )
    _save(
        runner.store,
        root_frame_id=frame,
        project_id=pid,
        filename="notes.txt",
        content_type="text/csv",
        payload=b"not-a-filename-hit",
    )

    status, body = client.get(
        f"/projects/{pid}/artifact-index?{urlencode({'q': 'foo_bar'})}"
    )
    assert status == 200
    assert [row["filename"] for row in body["artifacts"]] == ["foo_bar.csv"]

    status, body = client.get(
        f"/projects/{pid}/artifact-index?{urlencode({'q': 'foo%bar'})}"
    )
    assert status == 200
    assert [row["filename"] for row in body["artifacts"]] == ["foo%bar.csv"]

    status, body = client.get(
        f"/projects/{pid}/artifact-index?{urlencode({'q': 'csv'})}"
    )
    assert status == 200
    assert {row["filename"] for row in body["artifacts"]} == {
        "foo_bar.csv",
        "fooXbar.csv",
        "foo%bar.csv",
    }
    assert "notes.txt" not in {row["filename"] for row in body["artifacts"]}


def test_filters_combine_and_a_changed_filter_invalidates_the_cursor(server):
    _cfg, runner, client = server
    pid = _project(runner.store)
    frame = runner.create_session(pid)
    for index, (name, ctype, uploaded) in enumerate(
        (
            ("alpha.csv", "text/csv", True),
            ("alpha.txt", "text/plain", True),
            ("beta.csv", "text/csv", False),
            ("gamma.csv", "text/csv", True),
        )
    ):
        row = _save(
            runner.store,
            root_frame_id=frame,
            project_id=pid,
            filename=name,
            content_type=ctype,
            is_user_upload=uploaded,
            payload=str(index).encode(),
        )
        _set_created_at(runner.store, row["artifact_id"], 2_000 + index)

    status, page = client.get(
        f"/projects/{pid}/artifact-index?"
        + urlencode(
            {
                "q": "a",
                "content_type": "text/csv",
                "origin": "uploaded",
                "limit": 1,
            }
        )
    )
    assert status == 200
    assert [row["filename"] for row in page["artifacts"]] == ["gamma.csv"]
    held = page["next_cursor"]
    assert page["has_more"] is True

    status, next_page = client.get(
        f"/projects/{pid}/artifact-index?"
        + urlencode(
            {
                "q": "a",
                "content_type": "text/csv",
                "origin": "uploaded",
                "limit": 1,
                "cursor": held,
            }
        )
    )
    assert status == 200, next_page
    assert [row["filename"] for row in next_page["artifacts"]] == ["alpha.csv"]

    for params in (
        {"q": "beta", "cursor": held},
        {"content_type": "text/plain", "cursor": held, "q": "a"},
        {"origin": "generated", "cursor": held, "q": "a", "content_type": "text/csv"},
    ):
        status, body = client.get(f"/projects/{pid}/artifact-index?{urlencode(params)}")
        assert status == 400, body
        assert body["code"] == "invalid_cursor"


def test_a_cursor_from_another_project_is_invalid(server):
    _cfg, runner, client = server
    first = _project(runner.store, "one")
    second = _project(runner.store, "two")
    frame_one = runner.create_session(first)
    frame_two = runner.create_session(second)
    for index in range(3):
        _save(
            runner.store,
            root_frame_id=frame_one,
            project_id=first,
            filename=f"one-{index}.txt",
        )
        _save(
            runner.store,
            root_frame_id=frame_two,
            project_id=second,
            filename=f"two-{index}.txt",
        )
    status, page = client.get(f"/projects/{first}/artifact-index?limit=1")
    assert status == 200
    status, body = client.get(
        f"/projects/{second}/artifact-index?limit=1&cursor={page['next_cursor']}"
    )
    assert status == 400
    assert body["code"] == "invalid_cursor"


def test_same_filename_across_sessions_and_versions_is_not_merged(server):
    _cfg, runner, client = server
    pid = _project(runner.store)
    session_a = runner.create_session(pid)
    session_b = runner.create_session(pid)
    first = _save(
        runner.store,
        root_frame_id=session_a,
        project_id=pid,
        filename="report.csv",
        content_type="text/csv",
        payload=b"a",
    )
    second = _save(
        runner.store,
        root_frame_id=session_b,
        project_id=pid,
        filename="report.csv",
        content_type="text/csv",
        payload=b"b",
    )
    revised = _save(
        runner.store,
        root_frame_id=session_a,
        project_id=pid,
        filename="report.csv",
        content_type="text/csv",
        payload=b"a2",
        artifact_id=first["artifact_id"],
    )

    status, body = client.get(
        f"/projects/{pid}/artifact-index?{urlencode({'q': 'report.csv'})}"
    )
    assert status == 200
    rows = body["artifacts"]
    assert len(rows) == 2
    by_id = {row["artifact_id"]: row for row in rows}
    assert by_id[first["artifact_id"]]["root_frame_id"] == session_a
    assert by_id[second["artifact_id"]]["root_frame_id"] == session_b
    assert by_id[first["artifact_id"]]["version_id"] == revised["version_id"]
    assert by_id[first["artifact_id"]]["version_id"] != first["version_id"]
    assert by_id[second["artifact_id"]]["version_id"] == second["version_id"]
    assert {row["filename"] for row in rows} == {"report.csv"}


def test_team_visibility_is_applied_before_limit(tmp_path):
    """A page of hidden newer rows must not look like the end of the list."""
    cfg = Config(data_dir=tmp_path)
    store = get_store(cfg.db_path)
    try:
        pid = _project(store)
        alice = store.team.create_user(username="alice", password="fake-a")
        bob = store.team.create_user(username="bob", password="fake-b")
        alice_ids = []
        bob_ids = []
        for index in range(60):
            frame = store.new_frame(kind="turn", project_id=pid, status="ready")
            store.team.set_session_owner(
                frame, alice["id"], project_id=pid, visibility="private"
            )
            row = _save(
                store,
                root_frame_id=frame,
                project_id=pid,
                filename=f"alice-{index:02d}.txt",
            )
            _set_created_at(store, row["artifact_id"], 1_000 + index)
            alice_ids.append(row["artifact_id"])
        for index in range(60):
            frame = store.new_frame(kind="turn", project_id=pid, status="ready")
            store.team.set_session_owner(
                frame, bob["id"], project_id=pid, visibility="private"
            )
            row = _save(
                store,
                root_frame_id=frame,
                project_id=pid,
                filename=f"bob-{index:02d}.txt",
            )
            _set_created_at(store, row["artifact_id"], 2_000 + index)
            bob_ids.append(row["artifact_id"])

        page = store.browse_artifacts(
            project_id=pid, limit=50, visible_to_user_id=alice["id"]
        )
        ids = [row["artifact_id"] for row in page]
        assert len(ids) == 50
        assert set(ids) <= set(alice_ids)
        assert set(ids).isdisjoint(bob_ids)

        hidden_newest = store.browse_artifacts(project_id=pid, limit=50)
        assert {row["artifact_id"] for row in hidden_newest} <= set(bob_ids)

        alice_all = store.browse_artifacts(
            project_id=pid, limit=200, visible_to_user_id=alice["id"]
        )
        assert {row["artifact_id"] for row in alice_all} == set(alice_ids)
        bob_all = store.browse_artifacts(
            project_id=pid, limit=200, visible_to_user_id=bob["id"]
        )
        assert {row["artifact_id"] for row in bob_all} == set(bob_ids)
    finally:
        store.close()


def test_the_legacy_array_route_is_still_a_bare_array(server):
    _cfg, runner, client = server
    pid = _project(runner.store)
    frame = runner.create_session(pid)
    row = _save(
        runner.store,
        root_frame_id=frame,
        project_id=pid,
        filename="legacy.txt",
    )
    status, body = client.get(f"/projects/{pid}/artifacts")
    assert status == 200
    assert isinstance(body, list)
    assert "next_cursor" not in body
    assert "has_more" not in body
    assert body[0]["artifact_id"] == row["artifact_id"]
    assert body[0]["filename"] == "legacy.txt"
    assert set(body[0]) == {
        "id",
        "artifact_id",
        "filename",
        "content_type",
        "size_bytes",
        "version_id",
        "checksum",
        "project_id",
        "root_frame_id",
        "priority",
        "created_at",
        "is_user_upload",
    }

    status, indexed = client.get(f"/projects/{pid}/artifact-index")
    assert status == 200
    assert indexed["artifacts"][0] == body[0]
    assert indexed["has_more"] is False
    assert indexed["next_cursor"] is None


def test_malformed_limit_and_origin_are_refused(server):
    _cfg, runner, client = server
    pid = _project(runner.store)
    status, body = client.get(f"/projects/{pid}/artifact-index?limit=banana")
    assert status == 400
    assert body["code"] == "invalid_limit"
    status, body = client.get(f"/projects/{pid}/artifact-index?limit=-5")
    assert status == 400
    assert body["code"] == "invalid_limit"
    status, body = client.get(f"/projects/{pid}/artifact-index?origin=harvested")
    assert status == 400
    assert body["code"] == "invalid_origin"
    status, body = client.get(f"/projects/{pid}/artifact-index?cursor=not-a-cursor")
    assert status == 400
    assert body["code"] == "invalid_cursor"


def test_the_browse_index_exists_and_dropping_it_does_not_touch_rows(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    try:
        assert store.schema_state()["version"] == SCHEMA_VERSION == 31
        names = {
            row[1] for row in store._conn.execute("PRAGMA index_list('artifacts')")
        }
        assert "ix_artifacts_project_created" in names
        pid = _project(store)
        frame = store.new_frame(kind="turn", project_id=pid, status="ready")
        row = _save(
            store,
            root_frame_id=frame,
            project_id=pid,
            filename="kept.txt",
        )
        before = store.list_artifacts({"project_id": pid})
        store._conn.execute("DROP INDEX IF EXISTS ix_artifacts_project_created")
        store._conn.commit()
        after = store.list_artifacts({"project_id": pid})
        assert after == before
        assert after[0]["artifact_id"] == row["artifact_id"]
        store._apply_artifact_browse_index(store._conn)
        names = {
            row[1] for row in store._conn.execute("PRAGMA index_list('artifacts')")
        }
        assert "ix_artifacts_project_created" in names
    finally:
        store.close()


def test_the_browse_index_step_rolls_back_when_it_fails_after_creating(tmp_path):
    db = tmp_path / "t.db"
    store = Store(db)
    store.close()
    conn = sqlite3.connect(str(db))
    conn.execute("DROP INDEX IF EXISTS ix_artifacts_project_created")
    conn.execute("DELETE FROM schema_migrations WHERE version>=31")
    conn.execute("PRAGMA user_version = 30")
    conn.commit()

    def interrupted(connection):
        connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_artifacts_project_created "
            "ON artifacts(project_id, created_at DESC, artifact_id DESC)"
        )
        raise RuntimeError("killed after index")

    try:
        with pytest.raises(MigrationError, match="killed after index"):
            run_migrations(
                conn,
                db,
                {31: ("artifact_browse_index", interrupted)},
                target=31,
            )
        names = {row[1] for row in conn.execute("PRAGMA index_list('artifacts')")}
        assert "ix_artifacts_project_created" not in names
        assert current_version(conn) == 30
    finally:
        conn.close()
