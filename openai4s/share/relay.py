"""Stateless public relay for web shares (pure stdlib).

A publisher's daemon dials in over WSS and registers share ids; visitors reach
``https://<share-id>.<domain>/`` and the relay forwards each GET/HEAD through the
matching tunnel, streaming the daemon's response back.  The relay persists
nothing: revoke/disconnect makes a share instantly unreachable.

Security posture:
* publisher identity is ``sha256(token)`` — a principal fingerprint; same
  principal may take over a share id, a different principal is refused, and a
  dropped connection releases its registrations by compare-and-delete;
* the relay treats daemon responses as constrained input — status and headers
  are whitelisted, ``Set-Cookie``/``Location``/hop-by-hop are refused, and the
  streamed body length must match the advertised ``Content-Length``;
* visitor requests are GET/HEAD only; an unknown / revoked / offline share and
  any upstream 404 collapse to one byte-identical 404.
"""

from __future__ import annotations

import hashlib
import hmac
import queue
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from openai4s.server import ws_frames
from openai4s.share import protocol as p

_LABEL_RE = __import__("re").compile(r"^[a-z2-7]{26}$")

_NOT_FOUND_BODY = b"This share is unavailable.\n"
_STATUS_WHITELIST = frozenset({200, 204, 206, 304, 404, 405, 416, 500, 503})
_RESPONSE_HEADER_WHITELIST = frozenset(
    {
        "content-type",
        "content-length",
        "content-range",
        "accept-ranges",
        "etag",
        "last-modified",
        "cache-control",
        "content-disposition",
        "content-security-policy",
        "x-content-type-options",
        "referrer-policy",
        "x-robots-tag",
        "cross-origin-resource-policy",
        "x-content-sha256",
    }
)
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "transfer-encoding",
        "upgrade",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
    }
)
_POISON_HEADERS = frozenset({"set-cookie", "location"})
_SECURITY_BASELINE = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
    "Cross-Origin-Resource-Policy": "same-origin",
}

_HEAD_TIMEOUT = 30.0
_BODY_TIMEOUT = 60.0
_MAX_URL = 4096


class RelayConfig:
    def __init__(
        self,
        *,
        base_domain: str,
        tunnel_host: str | None = None,
        tokens: dict[str, str] | None = None,
        tokens_file: str | Path | None = None,
        trust_proxy: bool = False,
        max_inflight: int = p.DEFAULT_MAX_INFLIGHT,
        rate_per_sec: float = 10.0,
        rate_burst: int = 30,
        max_conns_per_ip: int = 32,
    ) -> None:
        self.base_domain = base_domain.strip().lower()
        self.tunnel_host = (tunnel_host or f"share.{base_domain}").strip().lower()
        self._static_tokens = dict(tokens or {})
        self.tokens_file = Path(tokens_file).expanduser() if tokens_file else None
        self.trust_proxy = trust_proxy
        self.max_inflight = max_inflight
        self.rate_per_sec = rate_per_sec
        self.rate_burst = rate_burst
        self.max_conns_per_ip = max_conns_per_ip
        self._tokens_mtime: float | None = None
        self._allowed: dict[str, str] = {}  # sha256(token) -> label
        self._lock = threading.Lock()
        self._load_tokens(force=True)

    def _load_tokens(self, *, force: bool = False) -> None:
        file_mtime: float | None = None
        if self.tokens_file and self.tokens_file.is_file():
            try:
                file_mtime = self.tokens_file.stat().st_mtime
            except OSError:
                file_mtime = None
        # Nothing changed since the last (re)load: keep the current allow set
        # rather than rebuilding it — a bare static rebuild would drop the
        # file-derived tokens.
        if not force and file_mtime == self._tokens_mtime:
            return
        allowed: dict[str, str] = {
            hashlib.sha256(tok.encode()).hexdigest(): label
            for label, tok in self._static_tokens.items()
        }
        self._tokens_mtime = file_mtime
        if self.tokens_file and self.tokens_file.is_file():
            for raw in self.tokens_file.read_text("utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                label, token = (
                    (parts[0], parts[1]) if len(parts) >= 2 else (parts[0], parts[0])
                )
                allowed[hashlib.sha256(token.encode()).hexdigest()] = label
        with self._lock:
            self._allowed = allowed

    def principal_for(self, token: str) -> str | None:
        self._load_tokens()
        fp = hashlib.sha256(token.encode()).hexdigest()
        with self._lock:
            for known_fp in self._allowed:
                if hmac.compare_digest(known_fp, fp):
                    return fp
        return None

    def allowed_fingerprints(self) -> set[str]:
        self._load_tokens()
        with self._lock:
            return set(self._allowed)


class _Pending:
    __slots__ = ("q",)

    def __init__(self) -> None:
        self.q: queue.Queue = queue.Queue()


class _TunnelConn:
    def __init__(self, wfile, principal_fp: str, conn_id: str) -> None:
        self.wfile = wfile
        self.principal_fp = principal_fp
        self.conn_id = conn_id
        self.alive = True
        self._send_lock = threading.Lock()
        self._pending: dict[int, _Pending] = {}
        self._pending_lock = threading.Lock()
        self._next_id = 0
        self.shares: set[str] = set()

    def send_control(self, obj: dict[str, Any]) -> bool:
        try:
            payload = p.encode_control(obj)
        except p.ProtocolError:
            return False
        with self._send_lock:
            try:
                self.wfile.write(ws_frames.ws_encode(payload, 0x1))
                self.wfile.flush()
                return True
            except OSError:
                self.alive = False
                return False

    def send_ping(self) -> None:
        with self._send_lock:
            try:
                self.wfile.write(ws_frames.ws_encode(b"", 0x9))
                self.wfile.flush()
            except OSError:
                self.alive = False

    def open_request(self) -> tuple[int, _Pending]:
        with self._pending_lock:
            self._next_id = (self._next_id + 1) & 0xFFFFFFFF
            req_id = self._next_id
            pending = self._pending[req_id] = _Pending()
        return req_id, pending

    def close_request(self, req_id: int) -> None:
        with self._pending_lock:
            self._pending.pop(req_id, None)

    def route_frame(self, req_id: int, event: tuple) -> None:
        with self._pending_lock:
            pending = self._pending.get(req_id)
        if pending is not None:
            pending.q.put(event)

    def fail_all(self) -> None:
        self.alive = False
        with self._pending_lock:
            for pending in self._pending.values():
                pending.q.put(("abort",))
            self._pending.clear()


class TunnelRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_share: dict[str, _TunnelConn] = {}

    def register(self, share_id: str, conn: _TunnelConn) -> str:
        """Return 'ok' | 'takeover' | 'conflict'."""

        with self._lock:
            existing = self._by_share.get(share_id)
            if existing is not None and existing is not conn:
                if existing.principal_fp != conn.principal_fp:
                    return "conflict"
                # same principal: take over, evict the stale mapping's owner
                existing.shares.discard(share_id)
                existing.route_frame(-1, ("revoked", share_id))
                self._by_share[share_id] = conn
                conn.shares.add(share_id)
                return "takeover"
            self._by_share[share_id] = conn
            conn.shares.add(share_id)
            return "ok"

    def unregister(self, share_id: str, conn: _TunnelConn) -> None:
        with self._lock:
            if self._by_share.get(share_id) is conn:  # compare-and-delete
                del self._by_share[share_id]
            conn.shares.discard(share_id)

    def drop_conn(self, conn: _TunnelConn) -> None:
        with self._lock:
            for share_id in list(conn.shares):
                if self._by_share.get(share_id) is conn:  # compare-and-delete
                    del self._by_share[share_id]
            conn.shares.clear()

    def get(self, share_id: str) -> _TunnelConn | None:
        with self._lock:
            return self._by_share.get(share_id)

    def disconnect_principals_not_in(self, allowed: set[str]) -> None:
        with self._lock:
            conns = {conn for conn in self._by_share.values()}
        for conn in conns:
            if conn.principal_fp not in allowed:
                conn.fail_all()


class _RateLimiter:
    def __init__(self, rate: float, burst: int) -> None:
        self._rate = rate
        self._burst = burst
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, now: float) -> bool:
        with self._lock:
            tokens, last = self._buckets.get(key, (float(self._burst), now))
            tokens = min(self._burst, tokens + (now - last) * self._rate)
            if tokens < 1.0:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - 1.0, now)
            return True

    def sweep(self, now: float) -> None:
        with self._lock:
            for key in list(self._buckets):
                _, last = self._buckets[key]
                if now - last > 300:
                    del self._buckets[key]


def make_relay_handler(config: RelayConfig, registry: TunnelRegistry):
    limiter = _RateLimiter(config.rate_per_sec, config.rate_burst)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "openai4s-relay/1.0"

        def log_message(self, *a):  # quiet
            pass

        # ---- entry ----
        def do_GET(self):
            self._route("GET")

        def do_HEAD(self):
            self._route("HEAD")

        def do_POST(self):
            self._simple(405, b"method not allowed\n")

        do_PUT = do_DELETE = do_PATCH = do_POST

        def _route(self, method: str) -> None:
            self.close_connection = True
            if len(self.path) > _MAX_URL:
                self._simple(414, b"uri too long\n")
                return
            host = self._host()
            # The publisher tunnel is identified by path + upgrade + bearer token,
            # not by Host (the daemon dials the relay IP directly).  Visitors never
            # request /tunnel and never carry a valid publisher token.
            if self.path.split("?", 1)[0] == "/tunnel" and self._is_upgrade():
                self._tunnel()
                return
            label = self._share_label(host)
            if label is None:
                self._not_found()
                return
            client = self._client_ip()
            if not limiter.allow(client, time.time()):
                self._simple(429, b"rate limited\n", extra={"Retry-After": "1"})
                return
            self._forward(method, label, client)

        # ---- tunnel (publisher) ----
        def _is_upgrade(self) -> bool:
            up = str(self.headers.get("Upgrade", "")).lower()
            conn = str(self.headers.get("Connection", "")).lower()
            return up == "websocket" and "upgrade" in conn

        def _tunnel(self) -> None:
            token = self._bearer()
            principal = config.principal_for(token) if token else None
            key = self.headers.get("Sec-WebSocket-Key")
            if principal is None or not key:
                self._simple(401, b"unauthorized\n")
                return
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", ws_frames.ws_accept(key))
            self.end_headers()
            try:
                self.wfile.flush()
            except OSError:
                return
            conn = _TunnelConn(self.wfile, principal, secrets.token_hex(8))
            try:
                self._tunnel_loop(conn)
            finally:
                registry.drop_conn(conn)
                conn.fail_all()

        def _tunnel_loop(self, conn: _TunnelConn) -> None:
            self.connection.settimeout(max(5.0, 2.5 * p.DEFAULT_HEARTBEAT_S))
            while conn.alive:
                frame = ws_frames.ws_read_frame(
                    self.rfile, expect_mask=True, max_len=p.MAX_FRAME_BYTES
                )
                if frame is None:
                    return
                opcode, payload = frame
                if opcode == 0x8:
                    return
                if opcode == 0x9:
                    conn.send_control({"type": p.PONG})
                    continue
                if opcode == 0xA:
                    continue
                if opcode == 0x2:  # DATA from daemon
                    try:
                        req_id, flags, chunk = p.decode_data(payload)
                    except p.ProtocolError:
                        return
                    if flags & p.FLAG_ABORT:
                        conn.route_frame(req_id, ("abort",))
                    elif flags & p.FLAG_END:
                        if chunk:
                            conn.route_frame(req_id, ("chunk", chunk))
                        conn.route_frame(req_id, ("end",))
                    else:
                        conn.route_frame(req_id, ("chunk", chunk))
                    continue
                if opcode != 0x1:
                    continue
                try:
                    msg = p.decode_control(payload)
                except p.ProtocolError:
                    return
                self._tunnel_control(conn, msg)

        def _tunnel_control(self, conn: _TunnelConn, msg: dict[str, Any]) -> None:
            kind = msg.get("type")
            if kind == p.HELLO:
                conn.send_control(
                    {
                        "type": p.WELCOME,
                        "proto": p.PROTO_VERSION,
                        "session": conn.conn_id,
                        "chunk_bytes": p.DEFAULT_CHUNK_BYTES,
                        "init_credit": p.DEFAULT_INIT_CREDIT,
                        "max_inflight": config.max_inflight,
                        "heartbeat_s": p.DEFAULT_HEARTBEAT_S,
                    }
                )
            elif kind == p.SHARE_REGISTER:
                share_id = str(msg.get("share_id") or "")
                if not _LABEL_RE.match(share_id):
                    conn.send_control(
                        {
                            "type": p.SHARE_REGISTER_ERROR,
                            "share_id": share_id,
                            "code": "invalid_id",
                        }
                    )
                    return
                result = registry.register(share_id, conn)
                if result == "conflict":
                    conn.send_control(
                        {
                            "type": p.SHARE_REGISTER_ERROR,
                            "share_id": share_id,
                            "code": "conflict",
                        }
                    )
                else:
                    conn.send_control(
                        {
                            "type": p.SHARE_REGISTERED,
                            "share_id": share_id,
                            "url": f"https://{share_id}.{config.base_domain}/",
                        }
                    )
            elif kind == p.SHARE_UNREGISTER:
                registry.unregister(str(msg.get("share_id") or ""), conn)
            elif kind == p.HTTP_RESPONSE:
                conn.route_frame(
                    int(msg.get("id") or -1),
                    (
                        "head",
                        int(msg.get("status") or 502),
                        msg.get("headers") or {},
                        bool(msg.get("has_body")),
                    ),
                )
            elif kind == p.PING:
                conn.send_control({"type": p.PONG, "ts": msg.get("ts")})

        # ---- visitor forward ----
        def _forward(self, method: str, share_id: str, client: str) -> None:
            conn = registry.get(share_id)
            if conn is None or not conn.alive:
                self._not_found()
                return
            headers = p.filter_request_headers(
                {k.lower(): v for k, v in self.headers.items()}
            )
            req_id, pending = conn.open_request()
            ok = conn.send_control(
                {
                    "type": p.HTTP_REQUEST,
                    "id": req_id,
                    "share_id": share_id,
                    "method": method,
                    "path": self.path.split("?", 1)[0],
                    "query": self.path.split("?", 1)[1] if "?" in self.path else "",
                    "headers": headers,
                    "remote": client,
                }
            )
            if not ok:
                conn.close_request(req_id)
                self._not_found()
                return
            try:
                self._relay_response(method, conn, req_id, pending)
            finally:
                conn.close_request(req_id)

        def _relay_response(
            self, method: str, conn: _TunnelConn, req_id: int, pending: _Pending
        ) -> None:
            try:
                head = pending.q.get(timeout=_HEAD_TIMEOUT)
            except queue.Empty:
                conn.send_control(
                    {"type": p.HTTP_CANCEL, "id": req_id, "reason": "timeout"}
                )
                self._simple(504, b"gateway timeout\n")
                return
            if head[0] != "head":
                self._simple(502, b"bad gateway\n")
                return
            _, status, raw_headers, has_body = head
            if status not in _STATUS_WHITELIST:
                self._simple(502, b"bad gateway\n")
                return
            clean, poisoned = self._sanitize_headers(raw_headers)
            if poisoned:
                self._simple(502, b"bad gateway\n")
                return
            content_length = clean.get("content-length")
            no_body = method == "HEAD" or status in (204, 304)

            self.send_response(status)
            for name, value in clean.items():
                self.send_header(name, value)
            for name, value in _SECURITY_BASELINE.items():
                if name.lower() not in clean:
                    self.send_header(name, value)
            self.send_header("Connection", "close")
            self.end_headers()

            if no_body or not has_body:
                # Drain any stray body frames the daemon may still emit.
                self._drain(pending)
                return
            self._stream(conn, req_id, pending, content_length)

        def _stream(
            self,
            conn: _TunnelConn,
            req_id: int,
            pending: _Pending,
            content_length: str | None,
        ) -> None:
            expected = (
                int(content_length)
                if content_length and content_length.isdigit()
                else None
            )
            written = 0
            while True:
                try:
                    event = pending.q.get(timeout=_BODY_TIMEOUT)
                except queue.Empty:
                    conn.send_control(
                        {"type": p.HTTP_CANCEL, "id": req_id, "reason": "timeout"}
                    )
                    self.close_connection = True
                    return
                kind = event[0]
                if kind == "chunk":
                    chunk = event[1]
                    try:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    except OSError:
                        conn.send_control(
                            {
                                "type": p.HTTP_CANCEL,
                                "id": req_id,
                                "reason": "client_gone",
                            }
                        )
                        return
                    written += len(chunk)
                    conn.send_control({"type": p.CREDIT, "id": req_id, "add": 1})
                elif kind == "end":
                    if expected is not None and written != expected:
                        self.close_connection = True  # truncation is detectable
                    return
                elif kind == "abort":
                    self.close_connection = True
                    return

        def _drain(self, pending: _Pending) -> None:
            while True:
                try:
                    event = pending.q.get_nowait()
                except queue.Empty:
                    return
                if event[0] in ("end", "abort"):
                    return

        # ---- helpers ----
        @staticmethod
        def _sanitize_headers(raw: Any) -> tuple[dict[str, str], bool]:
            clean: dict[str, str] = {}
            if not isinstance(raw, dict):
                return clean, False
            for key, value in raw.items():
                lk = str(key).lower()
                sval = str(value)
                if "\r" in sval or "\n" in sval or len(sval) > 1024:
                    continue
                if lk in _POISON_HEADERS:
                    return {}, True
                if lk in _HOP_BY_HOP:
                    continue
                if lk in _RESPONSE_HEADER_WHITELIST:
                    clean[lk] = sval
            return clean, False

        def _host(self) -> str:
            raw = str(self.headers.get("Host", "")).strip().lower()
            return raw.split(":", 1)[0] if raw else ""

        def _share_label(self, host: str) -> str | None:
            suffix = f".{config.base_domain}"
            if not host.endswith(suffix):
                return None
            label = host[: -len(suffix)]
            return label if _LABEL_RE.match(label) else None

        def _bearer(self) -> str | None:
            raw = str(self.headers.get("Authorization", ""))
            if raw.lower().startswith("bearer "):
                return raw[7:].strip()
            return None

        def _client_ip(self) -> str:
            peer = self.client_address[0] if self.client_address else ""
            if config.trust_proxy and peer in ("127.0.0.1", "::1", "localhost"):
                xff = str(self.headers.get("X-Forwarded-For", "")).strip()
                if xff:
                    last = xff.split(",")[-1].strip()
                    if last and " " not in last and "," not in last:
                        return last
            return peer

        def _simple(
            self, status: int, body: bytes, *, extra: dict | None = None
        ) -> None:
            self.close_connection = True
            self.send_response(status)
            for name, value in _SECURITY_BASELINE.items():
                self.send_header(name, value)
            for name, value in (extra or {}).items():
                self.send_header(name, value)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

        def _not_found(self) -> None:
            self._simple(404, _NOT_FOUND_BODY)

    return Handler


class RelayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], config: RelayConfig) -> None:
        self.config = config
        self.registry = TunnelRegistry()
        super().__init__(address, make_relay_handler(config, self.registry))
        self._janitor = threading.Thread(target=self._sweep, daemon=True)
        self._janitor_stop = threading.Event()
        self._janitor.start()

    def _sweep(self) -> None:
        while not self._janitor_stop.wait(300):
            self.config._load_tokens()
            self.registry.disconnect_principals_not_in(
                self.config.allowed_fingerprints()
            )

    def server_close(self) -> None:
        self._janitor_stop.set()
        super().server_close()


def serve_relay(
    *,
    host: str,
    port: int,
    config: RelayConfig,
    block: bool = True,
) -> RelayServer:
    server = RelayServer((host, port), config)
    if block:
        try:
            server.serve_forever()
        finally:
            server.server_close()
    else:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


__all__ = ["RelayConfig", "RelayServer", "TunnelRegistry", "serve_relay"]
