from __future__ import annotations

from pathlib import Path

from openai4s.config import Config, LLMConfig, ShareConfig
from openai4s.server import gateway as gateway_mod
from openai4s.store import get_store


class _Hub:
    def __init__(self):
        self.events = []

    def emitter(self, root_frame_id):
        def emit(event):
            event.setdefault("root_frame_id", root_frame_id)
            self.events.append(event)

        return emit

    def broadcast(self, root_frame_id, event):
        event.setdefault("root_frame_id", root_frame_id)
        self.events.append(event)

    def drop_frame(self, root_frame_id):
        pass


def _runner(tmp_path: Path, share: ShareConfig | None = None):
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=3,
        share=share or ShareConfig(),
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    return cfg, runner


def _seed_frame(runner):
    store = runner.store
    fid = store.new_frame(kind="turn", project_id="default", status="done")
    store.add_message(
        root_frame_id=fid,
        branch_id=fid,
        frame_id=fid,
        role="user",
        content="hello share",
    )
    store.log_cell(
        frame_id=fid,
        root_frame_id=fid,
        project_id="default",
        code="x = 1",
        result={"id": "c1", "stdout": "ok\n", "stderr": "", "error": None},
        cell_index=1,
        state_revision=1,
        visibility="scientific",
    )
    return fid


def test_sharing_off_by_default_no_tunnel(tmp_path):
    _, runner = _runner(tmp_path)
    try:
        assert runner._share_enabled() is False
        assert runner._share_tunnel is None
        assert runner.share_status()["state"] == "disabled"
    finally:
        runner.close()


def test_enable_unconfigured_reports_missing(tmp_path):
    _, runner = _runner(tmp_path)  # default ShareConfig has no relay/token
    try:
        status = runner.set_sharing_enabled(True)
        assert status["state"] == "unconfigured"
        assert "relay_url" in status["missing"]
        assert runner._share_tunnel is None  # no network thread when unconfigured
    finally:
        runner.close()


def test_create_and_revoke_share_without_tunnel(tmp_path):
    _, runner = _runner(tmp_path)
    try:
        fid = _seed_frame(runner)
        rec = runner.shares.create(fid, title="demo")
        assert rec["status"] == "ready"
        sid = rec["share_id"]
        assert runner.store.get_share(sid)["status"] == "ready"
        # tunnel is None (sharing not enabled) — create still publishes locally
        assert runner._share_tunnel is None
        runner.shares.revoke(sid)
        assert runner.store.get_share(sid)["status"] == "revoked"
    finally:
        runner.close()


def test_delete_session_revokes_shares(tmp_path):
    _, runner = _runner(tmp_path)
    try:
        fid = _seed_frame(runner)
        rec = runner.shares.create(fid)
        sid = rec["share_id"]
        assert runner.store.get_share(sid) is not None
        runner.deletions.delete_session(fid)
        # share row and snapshot dir are gone with the session
        assert runner.store.get_share(sid) is None
        assert not (tmp_path / "shares" / sid).exists()
    finally:
        runner.close()


def test_storage_cascade_drops_share_rows(tmp_path):
    # Defense in depth: even a share row left behind (no revoke callback) is
    # removed by the low-level session deletion aggregate.
    _, runner = _runner(tmp_path)
    try:
        fid = _seed_frame(runner)
        rec = runner.shares.create(fid)
        sid = rec["share_id"]
        runner.store.delete_frame(fid)
        assert runner.store.get_share(sid) is None
    finally:
        runner.close()


def test_enable_configured_creates_tunnel(tmp_path):
    share = ShareConfig(
        relay_url="ws://127.0.0.1:9/tunnel",  # unreachable; just exercise wiring
        auth_token="secret-token-abcdefgh",
        base_domain="localtest.me",
        allow_insecure=True,
    )
    _, runner = _runner(tmp_path, share=share)
    try:
        assert share.configured is True
        status = runner.set_sharing_enabled(True)
        assert status["state"] in ("connecting", "connected")
        assert runner._share_tunnel is not None
        # disable takes it offline and drops the network thread
        runner.set_sharing_enabled(False)
        assert runner._share_tunnel is None
        assert runner.share_status()["state"] == "disabled"
    finally:
        runner.close()


def _put_share_settings(node, body: dict):
    return _call(node, "PUT", "/api/v1/share/settings", body)


def _call(node, method: str, path: str, body: dict):
    import json

    from openai4s.server import local_auth
    from tests.test_team_auth_routes import _speak

    payload = json.dumps(body).encode("utf-8")
    head = (
        f"{method} {path} HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{node.port}\r\n"
        f"{local_auth.TOKEN_HEADER}: {node.token}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(payload)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    status, raw = _speak(node.port, head + payload)
    return status, json.loads(raw.split(b"\r\n\r\n", 1)[1])


def test_enabling_without_a_relay_is_refused_and_nothing_is_kept(tmp_path):
    """`share enable` with no relay answered `unconfigured` (exit 0) and stored
    "enabled", so the next relay configuration connected by itself and the
    Share dialog skipped its Enable step."""
    from tests.test_team_auth_routes import _TeamDaemon

    node = _TeamDaemon(tmp_path, team_mode=False)
    try:
        status, body = _put_share_settings(node, {"enabled": True})

        assert status == 409
        assert body["code"] == "sharing_unconfigured"
        assert body["missing"] == [
            "OPENAI4S_SHARE_RELAY_URL",
            "OPENAI4S_SHARE_AUTH_TOKEN",
        ]
        assert "docs/webshare.md" in body["error"]
        assert node.runner._share_enabled() is False
        assert node.store.get_setting("sharing_enabled") is None
        # Turning it off is always allowed.
        status, body = _put_share_settings(node, {"enabled": False})
        assert status == 200 and body["state"] == "disabled"
    finally:
        node.close()


def test_creating_a_share_without_a_relay_names_the_missing_setup(tmp_path):
    """It answered "sharing is disabled; enable it" -- advice that cannot work
    while enabling is refused for want of a relay."""
    from tests.test_team_auth_routes import _TeamDaemon

    node = _TeamDaemon(tmp_path, team_mode=False)
    try:
        fid = node.store.new_frame(kind="turn", project_id="default", status="done")

        status, body = _call(node, "POST", f"/api/v1/frames/{fid}/shares", {})

        assert status == 409
        assert body["code"] == "sharing_unconfigured"
        assert "OPENAI4S_SHARE_RELAY_URL" in body["error"]
        assert "docs/webshare.md" in body["error"]
    finally:
        node.close()
