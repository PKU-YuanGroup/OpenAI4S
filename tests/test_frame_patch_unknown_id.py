"""A rename of a session that does not exist has to say so.

`PATCH /frames/{id}` ran `UPDATE frames ... WHERE frame_id=?` and answered
`_frame_json(store.get_frame(id))`. An UPDATE that names no row is not an
error, and `_frame_json(None)` is `{}`, so renaming a deleted or mistyped
session answered 200 `{}` and broadcast a `frame_update` for it. A body naming
no field on a real session answers the full frame, so the `{}` was not "nothing
changed" either: a client could not tell "renamed" from "no such session", and
the workbench's title editor took the 200 as a saved rename.

The 404 reuses the team scope guard's sentence. Team mode already refused an
unknown id before the handler ran, and INV-13 is that a missing session and
somebody else's read identically, so the scope test holds both modes and both
cases to one body.

`GET /frames/{id}` answers an unknown id with the same 404 (SYNC-17): a `{}`
with a 200 read as a session that exists, so a tab still showing a session
deleted elsewhere could not learn that it was gone.

The ordinary requests use the real handler on a real socket, unmarked, so the
response schema capture observes the 404 and a PATCH of a real session. The
marked race test also checks that deletion during a PATCH cannot publish a
success event before the handler returns 404.
"""

from __future__ import annotations

import http.client
import json
import threading

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth
from tests._ports import bound_gateway_server
from tests.test_team_auth_routes import (  # noqa: F401  (fixture reuse)
    _fast_pbkdf2,
    _login,
    _TeamDaemon,
)

API = "/api/v1"
MISSING = "frame-that-was-never-created"


class _RecordingHub:
    def __init__(self) -> None:
        self.broadcasts: list[tuple[str, dict]] = []

    def emitter(self, root_frame_id):
        def emit(event):
            del event

        return emit

    def broadcast(self, root_frame_id, event):
        self.broadcasts.append((root_frame_id, event))

    def has_subscriber(self, root_frame_id):
        del root_frame_id
        return False

    def drop_frame(self, root_frame_id):
        del root_frame_id


def _call(port, method, path, body=None, *, token=None, cookie=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    try:
        headers = {}
        if token is not None:
            headers[local_auth.TOKEN_HEADER] = token
        if cookie is not None:
            headers["Cookie"] = cookie
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, API + path, body=payload, headers=headers)
        response = conn.getresponse()
        return response.status, json.loads(response.read() or b"{}")
    finally:
        conn.close()


def test_patch_of_an_unknown_session_is_a_404_and_changes_nothing(tmp_path):
    httpd, port = bound_gateway_server()
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        host="127.0.0.1",
        port=port,
    )
    hub = _RecordingHub()
    runner = gateway_mod.SessionRunner(cfg, hub, start_idle_sweeper=False)
    httpd.RequestHandlerClass = gateway_mod.make_handler(cfg, runner.hub, runner)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    token = local_auth.load_or_mint(cfg.data_dir)

    def call(method, path, body=None):
        return _call(port, method, path, body, token=token)

    try:
        status, project = call("POST", "/projects", {"name": "Renames"})
        assert status == 200, project
        status, created = call(
            "POST",
            "/frames",
            {"project_id": project["project_id"], "model": "deepseek-chat"},
        )
        assert status == 200, created
        frame_id = created["id"]

        # The positive control: without it a PATCH route that refused
        # everything would pass every assertion about the unknown id below.
        status, renamed = call("PATCH", f"/frames/{frame_id}", {"name": "Assay 7"})
        assert status == 200, renamed
        assert renamed["id"] == frame_id
        assert renamed["name"] == "Assay 7"

        # Nothing to change on a real session is still the full frame, which
        # is why the old `{}` could only ever have meant "no such session".
        status, unchanged = call("PATCH", f"/frames/{frame_id}", {})
        assert status == 200, unchanged
        assert unchanged["id"] == frame_id
        assert unchanged["name"] == "Assay 7"
        assert [fid for fid, _event in hub.broadcasts] == [frame_id, frame_id]

        status, refused = call("PATCH", f"/frames/{MISSING}", {"name": "Ghost"})
        assert status == 404, refused
        assert refused["error"] == "session not found"
        assert refused["code"] == "not_found"
        assert refused["status"] == 404
        assert refused.get("request_id")
        # Refused before the write and before the event: no subscriber is
        # told about a session that does not exist, and none appears.
        assert [fid for fid, _event in hub.broadcasts] == [frame_id, frame_id]
        assert runner.store.get_frame(MISSING) is None

        # The read refuses it the same way (SYNC-17).
        status, read = call("GET", f"/frames/{MISSING}")
        assert status == 404, read
        assert _refusal(read) == _refusal(refused)
    finally:
        httpd.shutdown()
        httpd.server_close()
        runner.close()


def _refusal(body: dict) -> dict:
    """The part of an error body that may not vary with why it was refused."""
    return {key: value for key, value in body.items() if key != "request_id"}


def test_missing_and_not_yours_read_the_same_in_both_modes(tmp_path):
    """INV-13: which sessions exist is the information being protected.

    Team mode answers both cases from the scope guard before the handler runs;
    single-user mode answers the unknown id from the handler. Three refusals,
    one body -- otherwise the wording alone says which path produced it.
    """
    solo = _TeamDaemon(tmp_path / "solo", team_mode=False)
    try:
        status, alone = _call(
            solo.port,
            "PATCH",
            f"/frames/{MISSING}",
            {"name": "Ghost"},
            token=solo.token,
        )
    finally:
        solo.close()
    assert status == 404, alone

    team = _TeamDaemon(tmp_path / "team")
    try:
        team.seed_user("alice", "fake-pw-a")
        team.seed_user("bob", "fake-pw-b")
        project = team.store.create_project(name="p", description="", context="")
        alice = _login(team, "alice", "fake-pw-a")
        bob = _login(team, "bob", "fake-pw-b")
        status, created = _call(
            team.port,
            "POST",
            "/frames",
            {"project_id": project["project_id"]},
            cookie=alice,
        )
        assert status == 200, created
        frame_id = created["id"]

        # The owner's rename lands, so bob's 404 below is about bob.
        status, renamed = _call(
            team.port, "PATCH", f"/frames/{frame_id}", {"name": "Mine"}, cookie=alice
        )
        assert (status, renamed["name"]) == (200, "Mine"), renamed

        status, not_yours = _call(
            team.port, "PATCH", f"/frames/{frame_id}", {"name": "Bob's"}, cookie=bob
        )
        assert status == 404, not_yours
        status, missing = _call(
            team.port, "PATCH", f"/frames/{MISSING}", {"name": "Ghost"}, cookie=bob
        )
        assert status == 404, missing
        assert team.store.get_frame(frame_id)["name"] == "Mine"
    finally:
        team.close()

    assert _refusal(alone) == _refusal(not_yours) == _refusal(missing)


@pytest.mark.stubbed_backend
@pytest.mark.parametrize("delete_before_write", [True, False])
def test_delete_during_patch_does_not_broadcast_success(
    tmp_path, monkeypatch, delete_before_write
):
    """A refused rename must not tell WebSocket subscribers it succeeded.

    Inject a real deletion on either side of the real write, after the
    handler's existence check. Mark the deterministic fault injection so it
    cannot contribute a response schema.
    """
    daemon = _TeamDaemon(tmp_path, team_mode=False)
    try:
        project = daemon.store.create_project(name="Renames")
        frame_id = daemon.runner.create_session(project["project_id"])
        broadcasts = []
        original_broadcast = daemon.hub.broadcast
        original_update = daemon.store.update_frame

        def record_broadcast(root_frame_id, event):
            broadcasts.append((root_frame_id, event))
            return original_broadcast(root_frame_id, event)

        def update_with_delete(fid, **fields):
            assert fid == frame_id
            assert fields == {"name": "Too late"}
            if delete_before_write:
                daemon.store.delete_frame(fid)
            original_update(fid, **fields)
            if not delete_before_write:
                daemon.store.delete_frame(fid)

        monkeypatch.setattr(daemon.hub, "broadcast", record_broadcast)
        monkeypatch.setattr(daemon.store, "update_frame", update_with_delete)
        status, refused = _call(
            daemon.port,
            "PATCH",
            f"/frames/{frame_id}",
            {"name": "Too late"},
            token=daemon.token,
        )
        assert status == 404, refused
        assert refused["error"] == "session not found"
        assert refused["code"] == "not_found"
        assert daemon.store.get_frame(frame_id) is None
        assert broadcasts == []
    finally:
        daemon.close()
