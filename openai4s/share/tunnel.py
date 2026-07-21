"""Daemon-side outbound tunnel client.

Maintains one WSS connection to the relay, (re)registers a desired set of share
ids, and services relay-forwarded visitor requests by calling an injected
read-only handler (the ShareRouter).  The relay is treated as untrusted: frames
are size-bounded and schema-checked, and the handler is the only thing that ever
touches share bytes.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from openai4s.share import protocol as p
from openai4s.share.ws_client import WSClient, WSClientError

# handler(request:{share_id,method,path,query,headers,remote}) ->
#   {status:int, headers:dict[str,str], body: bytes | Iterator[bytes] | None}
ShareHandler = Callable[[dict[str, Any]], dict[str, Any]]

_BACKOFF_MIN = 1.0
_BACKOFF_MAX = 60.0


class _Req:
    __slots__ = ("credit", "cancelled")

    def __init__(self, credit: int) -> None:
        self.credit = threading.Semaphore(max(1, credit))
        self.cancelled = threading.Event()


class TunnelClient:
    def __init__(
        self,
        relay_url: str,
        token: str,
        handler: ShareHandler,
        *,
        allow_insecure: bool = False,
        max_workers: int = p.DEFAULT_MAX_INFLIGHT,
    ) -> None:
        self._relay_url = relay_url
        self._token = token
        self._handler = handler
        self._allow_insecure = allow_insecure
        self._max_workers = max_workers

        self._desired: dict[str, dict[str, Any]] = {}
        self._desired_lock = threading.Lock()
        self._ws: WSClient | None = None
        self._send_lock = threading.Lock()
        self._pending: dict[int, _Req] = {}
        self._pending_lock = threading.Lock()
        self._pool: ThreadPoolExecutor | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._last_error = ""
        self._chunk_bytes = p.DEFAULT_CHUNK_BYTES
        self._init_credit = p.DEFAULT_INIT_CREDIT

    # ------------------------------------------------------------------ public
    def set_shares(self, shares: dict[str, dict[str, Any]]) -> None:
        with self._desired_lock:
            self._desired = dict(shares)
            desired = dict(self._desired)
        if desired and self._thread is None:
            self.ensure_connected()
        elif not desired:
            self.close()
            return
        # push a full desired-state re-registration if already connected
        if self._connected.is_set():
            self._register_all(desired)

    def add_share(self, share_id: str, meta: dict[str, Any] | None = None) -> None:
        with self._desired_lock:
            self._desired[share_id] = dict(meta or {})
        if self._thread is None:
            self.ensure_connected()
        elif self._connected.is_set():
            self._safe_send_control(
                {"type": p.SHARE_REGISTER, "share_id": share_id, "meta": meta or {}}
            )

    def remove_share(self, share_id: str) -> None:
        with self._desired_lock:
            self._desired.pop(share_id, None)
            empty = not self._desired
        if self._connected.is_set():
            self._safe_send_control({"type": p.SHARE_UNREGISTER, "share_id": share_id})
        if empty:
            self.close()

    def ensure_connected(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._pool = ThreadPoolExecutor(max_workers=self._max_workers)
        self._thread = threading.Thread(
            target=self._run, name="openai4s-share-tunnel", daemon=True
        )
        self._thread.start()

    def status(self) -> dict[str, Any]:
        with self._desired_lock:
            active = len(self._desired)
        return {
            "connected": self._connected.is_set(),
            "active_shares": active,
            "last_error": self._last_error,
            "relay_url": self._relay_url,
        }

    def close(self) -> None:
        self._stop.set()
        ws = self._ws
        if ws is not None:
            ws.close()
        pool = self._pool
        if pool is not None:
            pool.shutdown(wait=False)
            self._pool = None
        self._connected.clear()
        self._thread = None

    def wait_connected(self, timeout: float = 5.0) -> bool:
        return self._connected.wait(timeout)

    # ------------------------------------------------------------------ manager
    def _run(self) -> None:
        backoff = _BACKOFF_MIN
        while not self._stop.is_set():
            try:
                self._session()
                backoff = _BACKOFF_MIN
            except WSClientError as error:
                self._last_error = str(error)
            except Exception as error:  # noqa: BLE001 - never kill the manager
                self._last_error = f"{type(error).__name__}: {error}"
            self._connected.clear()
            self._cancel_all_pending()
            if self._stop.is_set():
                break
            with self._desired_lock:
                if not self._desired:
                    break
            delay = min(backoff, _BACKOFF_MAX) * (0.8 + 0.4 * random.random())
            if self._stop.wait(delay):
                break
            backoff = min(backoff * 2, _BACKOFF_MAX)

    def _session(self) -> None:
        ws = WSClient.connect(
            self._relay_url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "X-OpenAI4S-Tunnel-Proto": str(p.PROTO_VERSION),
            },
            allow_insecure=self._allow_insecure,
        )
        self._ws = ws
        try:
            self._safe_send_control(
                {"type": p.HELLO, "proto": p.PROTO_VERSION, "agent": "openai4s"}
            )
            frame = ws.recv(max_len=p.MAX_FRAME_BYTES)
            if frame is None or frame[0] != 0x1:
                raise WSClientError("relay did not send a welcome")
            welcome = p.decode_control(frame[1])
            if welcome.get("type") != p.WELCOME:
                raise WSClientError("unexpected first control frame")
            self._chunk_bytes = min(
                int(welcome.get("chunk_bytes") or p.DEFAULT_CHUNK_BYTES),
                p.MAX_DATA_CHUNK,
            )
            self._init_credit = max(
                1, int(welcome.get("init_credit") or p.DEFAULT_INIT_CREDIT)
            )
            heartbeat = float(welcome.get("heartbeat_s") or p.DEFAULT_HEARTBEAT_S)
            ws.settimeout(max(5.0, 2.5 * heartbeat))
            with self._desired_lock:
                desired = dict(self._desired)
            self._register_all(desired)
            self._connected.set()
            self._read_loop(ws)
        finally:
            self._connected.clear()
            ws.close()
            if self._ws is ws:
                self._ws = None

    def _read_loop(self, ws: WSClient) -> None:
        while not self._stop.is_set():
            frame = ws.recv(max_len=p.MAX_FRAME_BYTES)
            if frame is None:
                return
            opcode, payload = frame
            if opcode == 0x9:  # ping -> pong
                self._safe_send_pong(payload)
                continue
            if opcode == 0xA:  # pong
                continue
            if opcode != 0x1:  # relay only sends control frames
                continue
            try:
                msg = p.decode_control(payload)
            except p.ProtocolError:
                return  # untrusted relay sent garbage -> drop the session
            self._dispatch(msg)

    def _dispatch(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == p.HTTP_REQUEST:
            if self._pool is not None:
                self._pool.submit(self._serve, msg)
        elif kind == p.HTTP_CANCEL:
            self._cancel(int(msg.get("id") or -1))
        elif kind == p.CREDIT:
            self._grant_credit(int(msg.get("id") or -1), int(msg.get("add") or 0))
        elif kind == p.PING:
            self._safe_send_control({"type": p.PONG, "ts": msg.get("ts")})

    # ------------------------------------------------------------------ serve
    def _serve(self, msg: dict[str, Any]) -> None:
        req_id = int(msg.get("id") or 0)
        req = _Req(self._init_credit)
        with self._pending_lock:
            self._pending[req_id] = req
        method = str(msg.get("method") or "GET").upper()
        try:
            request = {
                "share_id": str(msg.get("share_id") or ""),
                "method": method,
                "path": str(msg.get("path") or "/"),
                "query": str(msg.get("query") or ""),
                "headers": p.filter_request_headers(msg.get("headers")),
                "remote": str(msg.get("remote") or ""),
            }
            try:
                response = self._handler(request)
            except Exception:  # noqa: BLE001 - never leak a traceback to a visitor
                response = {"status": 500, "headers": {}, "body": None}
            status = int(response.get("status") or 500)
            headers = {
                str(k): str(v) for k, v in (response.get("headers") or {}).items()
            }
            body = response.get("body")
            has_body = body is not None and method != "HEAD"
            self._safe_send_control(
                {
                    "type": p.HTTP_RESPONSE,
                    "id": req_id,
                    "status": status,
                    "headers": headers,
                    "has_body": has_body,
                }
            )
            if has_body:
                self._stream_body(req_id, req, body)
        finally:
            with self._pending_lock:
                self._pending.pop(req_id, None)

    def _stream_body(self, req_id: int, req: _Req, body: Any) -> None:
        try:
            for chunk in self._iter_body(body):
                for piece in self._split(chunk):
                    if req.cancelled.is_set():
                        self._safe_send_data(req_id, b"", abort=True)
                        return
                    req.credit.acquire()
                    if req.cancelled.is_set():
                        self._safe_send_data(req_id, b"", abort=True)
                        return
                    self._safe_send_data(req_id, piece)
            self._safe_send_data(req_id, b"", end=True)
        except Exception:  # noqa: BLE001 - abort the transfer, keep the tunnel
            self._safe_send_data(req_id, b"", abort=True)

    @staticmethod
    def _iter_body(body: Any) -> Iterator[bytes]:
        if isinstance(body, (bytes, bytearray)):
            yield bytes(body)
            return
        for chunk in body:
            if chunk:
                yield bytes(chunk)

    def _split(self, chunk: bytes) -> Iterator[bytes]:
        size = self._chunk_bytes
        for start in range(0, len(chunk), size):
            yield chunk[start : start + size]

    # ------------------------------------------------------------------ helpers
    def _register_all(self, desired: dict[str, dict[str, Any]]) -> None:
        for share_id, meta in desired.items():
            self._safe_send_control(
                {"type": p.SHARE_REGISTER, "share_id": share_id, "meta": meta or {}}
            )

    def _cancel(self, req_id: int) -> None:
        with self._pending_lock:
            req = self._pending.get(req_id)
        if req is not None:
            req.cancelled.set()
            req.credit.release()  # unblock a worker parked on flow control

    def _grant_credit(self, req_id: int, add: int) -> None:
        if add <= 0:
            return
        with self._pending_lock:
            req = self._pending.get(req_id)
        if req is not None:
            for _ in range(add):
                req.credit.release()

    def _cancel_all_pending(self) -> None:
        with self._pending_lock:
            reqs = list(self._pending.values())
            self._pending.clear()
        for req in reqs:
            req.cancelled.set()
            req.credit.release()

    def _safe_send_control(self, obj: dict[str, Any]) -> None:
        ws = self._ws
        if ws is None:
            return
        try:
            payload = p.encode_control(obj)
        except p.ProtocolError:
            return
        with self._send_lock:
            try:
                ws.send_text(payload)
            except WSClientError:
                pass

    def _safe_send_data(
        self, req_id: int, chunk: bytes, *, end: bool = False, abort: bool = False
    ) -> None:
        ws = self._ws
        if ws is None:
            return
        try:
            payload = p.encode_data(req_id, chunk, end=end, abort=abort)
        except p.ProtocolError:
            return
        with self._send_lock:
            try:
                ws.send_binary(payload)
            except WSClientError:
                pass

    def _safe_send_pong(self, payload: bytes) -> None:
        ws = self._ws
        if ws is None:
            return
        with self._send_lock:
            try:
                ws.send_pong(payload)
            except WSClientError:
                pass


__all__ = ["ShareHandler", "TunnelClient"]
