"""A session deleted in one tab is gone for every other tab too.

Two tabs had the same session open. Tab A deleted it; tab B still showed it,
and B's next message was accepted: `POST /frames/{id}/message` answered 202
and the turn really ran, writing `messages` and `action_groups` rows under a
root that no `frames` row names any more -- an orphan turn nothing could ever
show again. `submit_message` builds an ephemeral state for an id it does not
know (in-process callers rely on that), so the route was the only place left
to refuse it, and it took the id on trust.

`GET /frames/{id}` answered the same id `200 {}`. The workbench's readers
already treat a 404 as "no such session" (`locateSession`), but `{}` with a
200 read as a session that exists and has no fields.

Real handler on a real socket, unmarked, so the response capture observes the
404s and a real session's GET.
"""

from __future__ import annotations

import http.client
import json
import sqlite3
import threading

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth
from tests._ports import bound_gateway_server

API = "/api/v1"
NEVER = "frame-that-was-never-created"


class _RecordingHub:
    def __init__(self) -> None:
        self.directory: list[dict] = []

    def emitter(self, root_frame_id):
        def emit(event):
            del event

        return emit

    def broadcast(self, root_frame_id, event):
        del root_frame_id, event

    def notify_directory(self, root_frame_id, project_id, event):
        del root_frame_id, project_id
        self.directory.append(dict(event))

    def has_subscriber(self, root_frame_id):
        del root_frame_id
        return False

    def drop_frame(self, root_frame_id):
        del root_frame_id


def _call(port, token, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    try:
        headers = {local_auth.TOKEN_HEADER: token}
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, API + path, body=payload, headers=headers)
        response = conn.getresponse()
        return response.status, json.loads(response.read() or b"{}")
    finally:
        conn.close()


def _rows(db_path, table, root_frame_id) -> int:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE root_frame_id = ?",
            (root_frame_id,),
        ).fetchone()[0]
    finally:
        conn.close()


def test_a_deleted_session_reads_404_and_takes_no_message(tmp_path):
    httpd, port = bound_gateway_server()
    cfg = Config(
        data_dir=tmp_path,
        # A closed loopback port: if a turn were started anyway it fails fast
        # here instead of reaching a real provider.
        llm=LLMConfig(
            provider="deepseek",
            api_key="test-key",
            base_url="http://127.0.0.1:9/v1",
        ),
        host="127.0.0.1",
        port=port,
    )
    hub = _RecordingHub()
    runner = gateway_mod.SessionRunner(cfg, hub, start_idle_sweeper=False)
    httpd.RequestHandlerClass = gateway_mod.make_handler(cfg, runner.hub, runner)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    token = local_auth.load_or_mint(cfg.data_dir)

    def call(method, path, body=None):
        return _call(port, token, method, path, body)

    try:
        status, project = call("POST", "/projects", {"name": "Two tabs"})
        assert status == 200, project
        status, created = call(
            "POST",
            "/frames",
            {"project_id": project["project_id"], "model": "deepseek-chat"},
        )
        assert status == 200, created
        frame_id = created["id"]
        status, renamed = call("PATCH", f"/frames/{frame_id}", {"name": "Assay"})
        assert status == 200, renamed

        # The positive control: the read answers the session while it exists.
        status, read = call("GET", f"/frames/{frame_id}")
        assert status == 200, read
        assert read["id"] == frame_id
        assert read["name"] == "Assay"

        # Tab A deletes it. Every sidebar is told, which is what tab B's
        # workbench acts on to leave the session.
        status, deleted = call("DELETE", f"/frames/{frame_id}")
        assert (status, deleted) == (200, {"ok": True})
        assert {
            "type": "session_status",
            "frame_id": frame_id,
            "project_id": project["project_id"],
            "status": "deleted",
        } in hub.directory

        for fid in (frame_id, NEVER):
            status, gone = call("GET", f"/frames/{fid}")
            assert status == 404, (fid, gone)
            assert gone["error"] == "session not found"
            assert gone["code"] == "not_found"

            # Tab B sends into it. Refused before anything is reserved,
            # queued or written.
            status, refused = call(
                "POST",
                f"/frames/{fid}/message",
                {"request": "hello from B after A deleted this session", "wait": False},
            )
            assert status == 404, (fid, refused)
            assert refused["error"] == "session not found"
            assert refused["code"] == "not_found"
            assert runner.store.get_frame(fid) is None
            assert not runner.is_running(fid)
            db = tmp_path / "openai4s.db"
            assert _rows(db, "messages", fid) == 0
            assert _rows(db, "action_groups", fid) == 0
    finally:
        httpd.shutdown()
        httpd.server_close()
        runner.close()
