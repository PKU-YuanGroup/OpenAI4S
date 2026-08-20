"""WebSocket event isolation across users (M1-7, INV-13).

A real WS client over a real socket: B subscribing to A's session is refused
before the hub ever registers the subscription, so the replay buffer, pending
approval prompts, and the queue snapshot stay behind one check — and a
broadcast to A's session never reaches B's connection. The one mutating WS
inbound (cancel) answers like an unknown session. All passwords are fake.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from openai4s.server.ws_frames import ws_encode, ws_read_frame
from tests.test_team_auth_routes import (  # noqa: F401  (fixture reuse)
    _fast_pbkdf2,
    _login,
    _post,
    _TeamDaemon,
)


@pytest.fixture()
def daemon(tmp_path: Path):
    node = _TeamDaemon(tmp_path)
    node.seed_user("alice", "fake-pw-a")
    node.seed_user("bob", "fake-pw-b")
    node.store.create_project(name="p", description="", context="")
    try:
        yield node
    finally:
        node.close()


class _RawStream:
    """A recv-backed reader that survives timeouts.

    ``sock.makefile("rb")`` poisons itself after one timed-out read (CPython
    raises "cannot read from timed out object" forever after), and these
    tests deliberately poll-until-quiet.
    """

    def __init__(self, sock: socket.socket, initial: bytes = b""):
        self.sock = sock
        self.buf = bytearray(initial)

    def read(self, n: int) -> bytes:
        while len(self.buf) < n:
            block = self.sock.recv(65536)
            if not block:
                break
            self.buf += block
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out


class _WSClient:
    """A minimal masked-frame WebSocket client for one connection."""

    def __init__(self, port: int, cookie: str):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        handshake = (
            "GET /api/v1/ws HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"Cookie: {cookie}\r\n\r\n"
        )
        self.sock.sendall(handshake.encode("ascii"))
        raw = b""
        while b"\r\n\r\n" not in raw:
            block = self.sock.recv(4096)
            if not block:
                break
            raw += block
        head, _, rest = raw.partition(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0]
        assert b"101" in status, raw[:200]
        # bytes past the handshake are the first WS frames — keep them
        self.reader = _RawStream(self.sock, rest)

    def send(self, obj: dict) -> None:
        payload = json.dumps(obj).encode("utf-8")
        # Clients must mask; the server reads masked frames.
        self.sock.sendall(ws_encode(payload, 0x1, mask=True))

    def recv_json(self, timeout: float = 5.0) -> dict | None:
        self.sock.settimeout(timeout)
        try:
            frame = ws_read_frame(self.reader)
        except (socket.timeout, OSError):
            return None
        if frame is None:
            return None
        opcode, data = frame
        if opcode != 0x1:
            return self.recv_json(timeout)
        try:
            return json.loads(data.decode("utf-8"))
        except ValueError:
            return None

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def _session_for(daemon, cookie: str) -> str:
    pid = str(daemon.store.list_projects()[0]["project_id"])
    status, raw = _post(
        daemon.port, "/api/v1/frames", {"project_id": pid}, cookie=cookie
    )
    assert status == 200
    body = json.loads(raw.split(b"\r\n\r\n", 1)[1])
    return str(body.get("frame_id") or body.get("id"))


def test_cross_user_subscription_is_denied(daemon):
    a = _login(daemon, "alice", "fake-pw-a")
    b = _login(daemon, "bob", "fake-pw-b")
    fid_a = _session_for(daemon, a)

    ws = _WSClient(daemon.port, b)
    try:
        ws.send({"type": "view_session", "root_frame_id": fid_a})
        reply = ws.recv_json()
        assert reply is not None
        assert reply["type"] == "view_denied", reply
        assert reply["reason"] == "session not found"
        # nothing further arrives: no replay, no queue snapshot
        assert ws.recv_json(timeout=0.8) is None
    finally:
        ws.close()


def test_own_subscription_streams_and_foreign_broadcast_does_not_arrive(daemon):
    a = _login(daemon, "alice", "fake-pw-a")
    b = _login(daemon, "bob", "fake-pw-b")
    fid_a = _session_for(daemon, a)
    fid_b = _session_for(daemon, b)

    ws_b = _WSClient(daemon.port, b)
    try:
        ws_b.send({"type": "view_session", "root_frame_id": fid_b})
        first = ws_b.recv_json()
        assert first is not None and first["type"] != "view_denied"
        # drain until quiet
        while ws_b.recv_json(timeout=0.5) is not None:
            pass

        # a broadcast into A's session must not reach B's connection
        daemon.runner.hub.broadcast(fid_a, {"type": "frame_update", "frame_id": fid_a})
        daemon.runner.hub.broadcast(fid_b, {"type": "frame_update", "frame_id": fid_b})
        seen = []
        while True:
            msg = ws_b.recv_json(timeout=1.0)
            if msg is None:
                break
            seen.append(msg)
        frames = {m.get("frame_id") for m in seen if m.get("type") == "frame_update"}
        assert fid_b in frames
        assert fid_a not in frames
    finally:
        ws_b.close()


def test_admin_may_subscribe_to_any_session(daemon):
    daemon.seed_user("root", "fake-pw-r", role="admin")
    a = _login(daemon, "alice", "fake-pw-a")
    fid_a = _session_for(daemon, a)
    root_cookie = _login(daemon, "root", "fake-pw-r")

    ws = _WSClient(daemon.port, root_cookie)
    try:
        ws.send({"type": "view_session", "root_frame_id": fid_a})
        reply = ws.recv_json()
        assert reply is not None and reply["type"] != "view_denied"
    finally:
        ws.close()


def test_unknown_session_id_cannot_be_pre_subscribed(daemon):
    """A guessed id parked on a future session must not stream it later."""
    b = _login(daemon, "bob", "fake-pw-b")
    ws = _WSClient(daemon.port, b)
    try:
        ws.send({"type": "view_session", "root_frame_id": "f-doesnotexist"})
        reply = ws.recv_json()
        assert reply is not None and reply["type"] == "view_denied"
    finally:
        ws.close()


def test_cross_user_ws_cancel_is_refused(daemon):
    a = _login(daemon, "alice", "fake-pw-a")
    b = _login(daemon, "bob", "fake-pw-b")
    fid_a = _session_for(daemon, a)

    ws = _WSClient(daemon.port, b)
    try:
        ws.send(
            {
                "type": "cancel_execution",
                "root_frame_id": fid_a,
                "execution_id": "x",
                "owner": "user",
                "owner_id": "y",
            }
        )
        reply = ws.recv_json()
        assert reply is not None
        assert reply["type"] == "execution_cancel_result"
        assert reply["ok"] is False
        assert reply["reason"] == "session not found"
    finally:
        ws.close()


def test_a_live_stream_stops_when_the_subscription_is_revoked(daemon, monkeypatch):
    """Subscribing was checked once and never again.

    `test_ws_subscription_stops_working_after_the_user_is_disabled` in
    tests/test_team_isolation_hardening.py asserts that a *new* subscribe and
    a *mutating* inbound are refused after revocation — which they were. The
    standing subscription was not: `WSHub.broadcast` fanned out on
    `root_frame_id in c.subs` alone, so the socket kept delivering cell code,
    stdout and pending approval prompts to an account that had just been
    disabled, for as long as the tab stayed open.

    The re-check is cached for a few seconds so a chatty turn does not run an
    ownership query per chunk per viewer; pinned to 0 here so the test is
    about the rule and not about the clock.
    """
    from openai4s.server.gateway import WSConnection

    monkeypatch.setattr(WSConnection, "_VIS_TTL_S", 0.0)

    a = _login(daemon, "alice", "fake-pw-a")
    fid_a = _session_for(daemon, a)

    ws = _WSClient(daemon.port, a)
    try:
        ws.send({"type": "view_session", "root_frame_id": fid_a})
        first = ws.recv_json()
        assert first is not None and first["type"] != "view_denied"
        while ws.recv_json(timeout=0.5) is not None:
            pass

        # Still hers: the broadcast arrives.
        daemon.runner.hub.broadcast(fid_a, {"type": "frame_update", "frame_id": fid_a})
        seen = []
        while True:
            msg = ws.recv_json(timeout=1.0)
            if msg is None:
                break
            seen.append(msg)
        assert any(m.get("type") == "frame_update" for m in seen), seen

        # Revoked. The socket is still open and still subscribed.
        uid = daemon.store.team.get_user_by_username("alice")["id"]
        daemon.store.team.set_disabled(uid, True)

        daemon.runner.hub.broadcast(fid_a, {"type": "frame_update", "frame_id": fid_a})
        after = []
        while True:
            msg = ws.recv_json(timeout=1.0)
            if msg is None:
                break
            after.append(msg)
        assert not [m for m in after if m.get("type") == "frame_update"], after
    finally:
        ws.close()
