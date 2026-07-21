"""Minimal stdlib WebSocket client for the outbound share tunnel.

The daemon dials the relay; TLS is mandatory for any non-loopback host and the
certificate chain + hostname are always verified (there is no opt-out).  Only a
101 upgrade is accepted — no redirect is ever followed — and the connect target
is the single configured relay host, never an attacker-supplied URL.
"""

from __future__ import annotations

import socket
import ssl
from urllib.parse import urlparse

from openai4s.server import ws_frames


class WSClientError(RuntimeError):
    """The tunnel handshake or transport failed."""


class WSClient:
    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._rfile = sock.makefile("rb")
        self._closed = False

    # ------------------------------------------------------------------ connect
    @classmethod
    def connect(
        cls,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 15.0,
        allow_insecure: bool = False,
    ) -> "WSClient":
        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()
        host = parsed.hostname or ""
        if not host:
            raise WSClientError("relay url has no host")
        loopback = host in ("127.0.0.1", "localhost", "::1")
        if scheme not in ("ws", "wss"):
            raise WSClientError("relay url must be ws:// or wss://")
        if scheme == "ws" and not (loopback and allow_insecure):
            raise WSClientError("plaintext ws:// is only allowed on loopback for tests")
        port = parsed.port or (443 if scheme == "wss" else 80)
        path = parsed.path or "/tunnel"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        raw = socket.create_connection((host, port), timeout=timeout)
        try:
            if scheme == "wss":
                ctx = ssl.create_default_context()
                # check_hostname + CERT_REQUIRED are the defaults; never relax
                # them. Pin a TLS 1.2 floor explicitly (no legacy protocols).
                ctx.minimum_version = ssl.TLSVersion.TLSv1_2
                sock: socket.socket = ctx.wrap_socket(raw, server_hostname=host)
            else:
                sock = raw
        except (ssl.SSLError, OSError) as error:
            raw.close()
            raise WSClientError(f"tunnel TLS/connect failed: {error}") from error

        try:
            client = cls(sock)
            client._handshake(host, port, path, headers or {})
            return client
        except Exception:
            sock.close()
            raise

    def _handshake(
        self, host: str, port: int, path: str, headers: dict[str, str]
    ) -> None:
        key = ws_frames.ws_client_key()
        host_header = host if port in (80, 443) else f"{host}:{port}"
        lines = [
            f"GET {path} HTTP/1.1",
            f"Host: {host_header}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
        ]
        for name, value in headers.items():
            lines.append(f"{name}: {value}")
        request = ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")
        self._sock.sendall(request)

        status_line = self._rfile.readline(4096)
        if not status_line:
            raise WSClientError("relay closed during handshake")
        parts = status_line.decode("latin-1", "replace").split(" ", 2)
        if len(parts) < 2 or parts[1] != "101":
            self._drain_error_body()
            raise WSClientError(f"tunnel upgrade rejected: {status_line!r}")
        accept = ""
        total = 0
        while True:
            line = self._rfile.readline(4096)
            if not line or line in (b"\r\n", b"\n"):
                break
            total += len(line)
            if total > 16384:
                raise WSClientError("handshake headers too large")
            name, _, value = line.decode("latin-1", "replace").partition(":")
            if name.strip().lower() == "sec-websocket-accept":
                accept = value.strip()
        if accept != ws_frames.ws_accept(key):
            raise WSClientError("bad Sec-WebSocket-Accept")

    def _drain_error_body(self) -> None:
        try:
            self._rfile.read(4096)
        except OSError:
            pass

    # ------------------------------------------------------------------ frames
    def send_text(self, payload: bytes) -> None:
        self._send(payload, 0x1)

    def send_binary(self, payload: bytes) -> None:
        self._send(payload, 0x2)

    def send_pong(self, payload: bytes) -> None:
        self._send(payload, 0xA)

    def send_ping(self, payload: bytes = b"") -> None:
        self._send(payload, 0x9)

    def _send(self, payload: bytes, opcode: int) -> None:
        if self._closed:
            raise WSClientError("tunnel is closed")
        # Client frames MUST be masked (RFC 6455 §5.3).
        frame = ws_frames.ws_encode(payload, opcode, mask=True)
        try:
            self._sock.sendall(frame)
        except OSError as error:
            raise WSClientError(f"tunnel send failed: {error}") from error

    def recv(self, *, max_len: int) -> tuple[int, bytes] | None:
        """Return one server frame ``(opcode, payload)`` or ``None`` on close."""

        return ws_frames.ws_read_frame(self._rfile, expect_mask=False, max_len=max_len)

    def settimeout(self, timeout: float | None) -> None:
        try:
            self._sock.settimeout(timeout)
        except OSError:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._sock.sendall(ws_frames.ws_encode(b"", 0x8, mask=True))
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


__all__ = ["WSClient", "WSClientError"]
