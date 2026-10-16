"""Project-local archives are browsable after the original session is deleted."""

from __future__ import annotations

import http.client
from urllib.parse import quote

from openai4s.server import local_auth
from tests.test_project_patch_unknown_id import _call
from tests.test_team_auth_routes import _TeamDaemon


def test_history_http_versions_download_and_relink_after_deletion(tmp_path):
    source = tmp_path / "research"
    source.mkdir()
    (source / "source.csv").write_text("sample,value\na,2\n")
    daemon = _TeamDaemon(tmp_path / "daemon", team_mode=False)

    def call(method, path, body=None):
        return _call(daemon.port, method, path, body, token=daemon.token)

    def download(pid, sid, revision, path):
        conn = http.client.HTTPConnection("127.0.0.1", daemon.port, timeout=20)
        try:
            conn.request(
                "GET",
                f"/api/v1/projects/{pid}/history/{sid}/file"
                f"?revision={quote(revision)}&path={quote(path)}",
                headers={local_auth.TOKEN_HEADER: daemon.token},
            )
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    try:
        status, project = call(
            "POST",
            "/projects",
            {
                "name": "Retained research",
                "context": "Use measured data",
                "folder_path": str(source),
            },
        )
        assert status == 200, project
        pid = project["project_id"]
        status, frame = call("POST", "/frames", {"project_id": pid})
        assert status == 200, frame
        sid = frame["id"]
        daemon.store.add_message(
            root_frame_id=sid, role="user", content="Analyze the measurements"
        )
        workspace = daemon.runner.active_workspace_for(sid)
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "result.csv").write_bytes(b"mean\n2\n")
        status, saved = call("POST", f"/projects/{pid}/history", {})
        assert status == 200, saved
        assert saved["status"] == "saved", saved
        assert saved["directory"] == str(source / ".openai4s")
        row = next(item for item in saved["sessions"] if item["session_id"] == sid)
        assert row["can_continue"] is True
        assert row["message_count"] == 1
        first_revision = row["revision"]
        status, snapshot = call(
            "GET", f"/projects/{pid}/history/{sid}?revision={first_revision}"
        )
        assert status == 200, snapshot
        assert snapshot["read_only"] is True and snapshot["untrusted"] is True
        assert snapshot["messages"][0]["content"] == "Analyze the measurements"
        assert snapshot["settings"]["context"] == "Use measured data"
        assert any(item["path"] == "workspace/result.csv" for item in snapshot["files"])
        status, headers, data = download(
            pid, sid, first_revision, "workspace/result.csv"
        )
        assert status == 200 and data == b"mean\n2\n"
        assert headers["Content-Type"] == "application/octet-stream"
        assert headers["Content-Disposition"].startswith("attachment;")
        assert headers["X-Content-Type-Options"] == "nosniff"

        daemon.store.add_message(
            root_frame_id=sid, role="assistant", content="The mean is 3"
        )
        (workspace / "result.csv").write_bytes(b"mean\n3\n")
        status, saved = call("POST", f"/projects/{pid}/history", {})
        assert status == 200, saved
        row = next(item for item in saved["sessions"] if item["session_id"] == sid)
        assert row["revision"] != first_revision
        assert (
            download(pid, sid, first_revision, "workspace/result.csv")[2]
            == b"mean\n2\n"
        )
        assert (
            download(pid, sid, row["revision"], "workspace/result.csv")[2]
            == b"mean\n3\n"
        )
        assert download(pid, sid, row["revision"], "../source.csv")[0] in (
            400,
            403,
            404,
        )
        status, _ = call("GET", f"/projects/{pid}/history/nonexistent")
        assert status == 404
        status, _ = call("GET", f"/projects/{pid}/history/{sid}?revision=../outside")
        assert status in (400, 403, 404)

        status, deleted = call("DELETE", f"/projects/{pid}")
        assert status == 200, deleted
        assert (source / ".openai4s").is_dir()
        assert (source / "source.csv").read_text() == "sample,value\na,2\n"
        status, rebound = call(
            "POST", "/projects", {"name": "Reopened", "folder_path": str(source)}
        )
        assert status == 200, rebound
        pid = rebound["project_id"]
        status, listing = call("GET", f"/projects/{pid}/history")
        assert status == 200, listing
        assert (
            next(item for item in listing["sessions"] if item["session_id"] == sid)[
                "can_continue"
            ]
            is False
        )
        status, retained = call(
            "GET", f"/projects/{pid}/history/{sid}?revision={first_revision}"
        )
        assert status == 200, retained
        assert retained["can_continue"] is False
        assert retained["messages"][0]["content"] == "Analyze the measurements"
        assert retained["settings"]["name"] == "Retained research"
        assert (
            download(pid, sid, first_revision, "workspace/result.csv")[2]
            == b"mean\n2\n"
        )
    finally:
        daemon.close()


def test_history_routes_require_a_local_bound_project(tmp_path):
    daemon = _TeamDaemon(tmp_path / "daemon", team_mode=False)
    try:
        project = daemon.store.create_project(name="Unbound")
        pid = project["project_id"]
        for method in ("GET", "POST"):
            status, body = _call(
                daemon.port, method, f"/projects/{pid}/history", token=daemon.token
            )
            assert status == 409 and body["code"] == "no_project_folder"
        status, body = _call(
            daemon.port, "GET", "/projects/missing/history", token=daemon.token
        )
        assert status == 404 and body["error"] == "project not found"
    finally:
        daemon.close()


def test_history_save_error_is_visible_and_retry_does_not_follow_metadata_symlink(
    tmp_path,
):
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel.txt").write_text("untouched")
    daemon = _TeamDaemon(tmp_path / "daemon", team_mode=False)

    def call(method, path, body=None):
        return _call(daemon.port, method, path, body, token=daemon.token)

    history = source / ".openai4s"
    retained = source / "retained-history"
    try:
        _, project = call(
            "POST", "/projects", {"name": "Failures", "folder_path": str(source)}
        )
        pid = project["project_id"]
        _, frame = call("POST", "/frames", {"project_id": pid})
        sid = frame["id"]
        daemon.store.add_message(root_frame_id=sid, role="user", content="Retain this")
        status, saved = call("POST", f"/projects/{pid}/history", {})
        assert status == 200, saved
        history.rename(retained)
        history.symlink_to(outside, target_is_directory=True)
        status, failed = call("POST", f"/projects/{pid}/history", {})
        assert status == 503, failed
        assert failed["status"] == 503 and failed["error"]
        assert failed["history"]["status"] == "error"
        status, visible = call("GET", f"/projects/{pid}/history")
        assert status == 200 and visible["status"] == "error"
        assert [path.name for path in outside.iterdir()] == ["sentinel.txt"]
        history.unlink()
        retained.rename(history)
        status, retried = call("POST", f"/projects/{pid}/history", {})
        assert status == 200 and retried["status"] == "saved", retried
        assert retried["sessions"][0]["message_count"] == 1
    finally:
        if history.is_symlink():
            history.unlink()
        if retained.exists():
            retained.rename(history)
        daemon.close()
