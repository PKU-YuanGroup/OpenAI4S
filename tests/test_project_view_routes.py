"""Invalid project-view limits are client errors, including zero and negatives.

Exercise the real HTTP handler, authentication, Store, and view services. Valid
limits must still select real records; malformed requests must not turn an
inaccessible project into an existence oracle.
"""

from __future__ import annotations

import http.client
import json

import pytest

from openai4s.server import local_auth
from tests.test_team_auth_routes import (  # noqa: F401 (fixture reuse)
    _fast_pbkdf2,
    _login,
    _TeamDaemon,
)

REQUEST_ID = "project-view-route-test"


def _get(daemon, path, *, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", daemon.port, timeout=20)
    if headers is None:
        headers = {local_auth.TOKEN_HEADER: daemon.token}
    try:
        conn.request(
            "GET",
            "/api/v1/projects/" + path,
            headers={**headers, "X-Request-Id": REQUEST_ID},
        )
        response = conn.getresponse()
        assert response.getheader("Content-Type") == "application/json; charset=utf-8"
        return response.status, json.loads(response.read())
    finally:
        conn.close()


@pytest.fixture()
def solo(tmp_path):
    daemon = _TeamDaemon(tmp_path, team_mode=False)
    try:
        yield daemon
    finally:
        daemon.close()


@pytest.mark.parametrize("view", ["action-timeline", "lineage"])
@pytest.mark.parametrize(
    "limit,message",
    [
        ("abc", "limit must be an integer"),
        ("1.5", "limit must be an integer"),
        ("0", "limit must be a positive integer"),
        ("-1", "limit must be a positive integer"),
    ],
)
def test_project_view_rejects_an_invalid_limit(solo, view, limit, message):
    status, body = _get(solo, f"default/{view}?limit={limit}")
    assert status == 400, body
    assert body == {
        "error": message,
        "code": "invalid_limit",
        "status": 400,
        "request_id": REQUEST_ID,
    }


@pytest.mark.parametrize("view", ["action-timeline", "lineage"])
def test_project_view_applies_valid_limits_to_real_records(solo, view):
    store = solo.store
    root = store.new_frame(project_id="default", name="Limit regression")
    group_ids = []
    version_ids = []
    for index in range(3):
        group = store.append_action_group(
            root_frame_id=root,
            turn_id=f"turn-{index}",
            kind="user",
            assistant_message={"role": "user", "content": f"Step {index}"},
            created_at=index + 1,
        )
        group_ids.append(group["group_id"])
        artifact = store.save_artifact(
            path=str(solo.data_dir / f"result-{index}.txt"),
            filename=f"result-{index}.txt",
            content_type="text/plain",
            size_bytes=1,
            checksum=str(index) * 64,
            frame_id=root,
            root_frame_id=root,
            project_id="default",
        )
        version_ids.append(artifact["version_id"])

    # Missing/blank retain their defaults; large positive integers stay capped
    # by the service. A small limit actually cuts the result set.
    for query, expected in (
        ("", 3),
        ("?limit=", 3),
        ("?limit=1", 1),
        ("?limit=2", 2),
        ("?limit=999999999999999999999999999999", 3),
    ):
        status, body = _get(solo, f"default/{view}{query}")
        assert status == 200, (query, body)
        assert body["project_id"] == "default"
        if view == "action-timeline":
            assert body["count"] == expected, (query, body)
            assert body["total_count"] == 3
            assert [g["group_id"] for g in body["groups"]] == group_ids[-expected:]
            assert body["truncated"] == (expected < 3)
        else:
            assert body["version_count"] == expected, (query, body)
            assert body["artifact_count"] == 3
            assert len(body["nodes"]) == expected
            assert {node["id"] for node in body["nodes"]} <= set(version_ids)


@pytest.mark.parametrize("view", ["action-timeline", "lineage"])
def test_authentication_precedes_limit_validation(solo, view):
    status, body = _get(solo, f"default/{view}?limit=abc", headers={})
    assert status == 401, body


@pytest.mark.parametrize("view", ["action-timeline", "lineage"])
def test_inaccessible_and_missing_projects_refuse_before_limit_validation(
    tmp_path, view
):
    daemon = _TeamDaemon(tmp_path)
    try:
        member = daemon.seed_user("member", "fake-pw-member")
        cookie = _login(daemon, "member", "fake-pw-member")
        project = daemon.store.create_project(name="Visible only to its member")
        pid = project["project_id"]
        daemon.store.governance.set_member(pid, member["id"], "member")
        headers = {"Cookie": cookie}
        for limit in ("abc", "0", "-1"):
            status, body = _get(daemon, f"{pid}/{view}?limit={limit}", headers=headers)
            assert status == 400, body
            assert body["code"] == "invalid_limit"

        daemon.store.governance.remove_member(pid, member["id"])
        for limit in ("abc", "0", "-1", "1"):
            status, hidden = _get(
                daemon, f"{pid}/{view}?limit={limit}", headers=headers
            )
            assert status == 404, hidden
            status, missing = _get(
                daemon, f"never-created/{view}?limit={limit}", headers=headers
            )
            assert status == 404, missing
            assert (
                hidden
                == missing
                == {
                    "error": "project not found",
                    "code": "not_found",
                    "status": 404,
                    "request_id": REQUEST_ID,
                }
            )
    finally:
        daemon.close()
