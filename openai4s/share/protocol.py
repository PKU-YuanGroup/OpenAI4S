"""Wire protocol for the daemon⇄relay tunnel (pure stdlib).

Two frame families ride one WebSocket:

* **control** — a WS *text* frame carrying one JSON object with a whitelisted
  ``type``; and
* **data** — a WS *binary* frame with a fixed 6-byte header
  ``category(1) | request_id(4, big-endian) | flags(1)`` followed by a body
  chunk, so large artifact bytes never pay base64 overhead.

The daemon treats the relay as untrusted input: every JSON control frame is
size-bounded and type-checked, every data frame is header-validated, and unknown
types are rejected rather than acted upon.
"""

from __future__ import annotations

import json
import struct
from typing import Any

PROTO_VERSION = 1

# ---- negotiated defaults (relay announces the authoritative values) ----------
DEFAULT_CHUNK_BYTES = 256 * 1024
DEFAULT_MAX_INFLIGHT = 16
DEFAULT_INIT_CREDIT = 8
DEFAULT_HEARTBEAT_S = 20

# ---- hard caps (independent of negotiation) ----------------------------------
MAX_CONTROL_JSON = 64 * 1024
MAX_DATA_CHUNK = 256 * 1024
MAX_FRAME_BYTES = 1 << 20  # single WS frame ceiling on the tunnel

# ---- data frame header -------------------------------------------------------
_DATA_HEADER = struct.Struct(">BIB")  # category, request_id, flags
DATA_CATEGORY_BODY = 0x01
FLAG_END = 0x01
FLAG_ABORT = 0x02

# ---- control frame types (whitelist) -----------------------------------------
HELLO = "hello"
WELCOME = "welcome"
SHARE_REGISTER = "share_register"
SHARE_REGISTERED = "share_registered"
SHARE_REGISTER_ERROR = "share_register_error"
SHARE_UNREGISTER = "share_unregister"
SHARE_REVOKED = "share_revoked"
HTTP_REQUEST = "http_request"
HTTP_RESPONSE = "http_response"
HTTP_CANCEL = "http_cancel"
CREDIT = "credit"
PING = "ping"
PONG = "pong"
ERROR = "error"

CONTROL_TYPES = frozenset(
    {
        HELLO,
        WELCOME,
        SHARE_REGISTER,
        SHARE_REGISTERED,
        SHARE_REGISTER_ERROR,
        SHARE_UNREGISTER,
        SHARE_REVOKED,
        HTTP_REQUEST,
        HTTP_RESPONSE,
        HTTP_CANCEL,
        CREDIT,
        PING,
        PONG,
        ERROR,
    }
)


class ProtocolError(ValueError):
    """A malformed or out-of-contract tunnel frame."""


# ---- control ------------------------------------------------------------------
def encode_control(obj: dict[str, Any]) -> bytes:
    if not isinstance(obj, dict) or not isinstance(obj.get("type"), str):
        raise ProtocolError("control frame must be an object with a string type")
    if obj["type"] not in CONTROL_TYPES:
        raise ProtocolError(f"unknown control type: {obj['type']!r}")
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_CONTROL_JSON:
        raise ProtocolError("control frame exceeds size limit")
    return payload


def decode_control(payload: bytes) -> dict[str, Any]:
    if len(payload) > MAX_CONTROL_JSON:
        raise ProtocolError("control frame exceeds size limit")
    try:
        obj = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ProtocolError("control frame is not valid JSON") from error
    if not isinstance(obj, dict):
        raise ProtocolError("control frame must be a JSON object")
    kind = obj.get("type")
    if not isinstance(kind, str) or kind not in CONTROL_TYPES:
        raise ProtocolError(f"unknown control type: {kind!r}")
    return obj


# ---- data --------------------------------------------------------------------
def encode_data(
    request_id: int,
    chunk: bytes,
    *,
    end: bool = False,
    abort: bool = False,
) -> bytes:
    if not (0 <= request_id <= 0xFFFFFFFF):
        raise ProtocolError("request_id out of range")
    if len(chunk) > MAX_DATA_CHUNK:
        raise ProtocolError("data chunk exceeds size limit")
    flags = (FLAG_END if end else 0) | (FLAG_ABORT if abort else 0)
    return _DATA_HEADER.pack(DATA_CATEGORY_BODY, request_id, flags) + chunk


def decode_data(payload: bytes) -> tuple[int, int, bytes]:
    """Return ``(request_id, flags, chunk)`` or raise :class:`ProtocolError`."""

    if len(payload) < _DATA_HEADER.size:
        raise ProtocolError("data frame is too short")
    category, request_id, flags = _DATA_HEADER.unpack_from(payload)
    if category != DATA_CATEGORY_BODY:
        raise ProtocolError(f"unknown data category: {category}")
    chunk = payload[_DATA_HEADER.size :]
    if len(chunk) > MAX_DATA_CHUNK:
        raise ProtocolError("data chunk exceeds size limit")
    return request_id, flags, chunk


# ---- request/response header hygiene -----------------------------------------
# The daemon only forwards these visitor request headers to the ShareRouter.
REQUEST_HEADER_ALLOWLIST = frozenset(
    {"accept", "range", "if-none-match", "if-modified-since"}
)


def filter_request_headers(headers: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if not isinstance(headers, dict):
        return out
    for key, value in headers.items():
        lk = str(key).lower()
        if lk in REQUEST_HEADER_ALLOWLIST and isinstance(value, str):
            out[lk] = value
    return out


__all__ = [
    "CONTROL_TYPES",
    "CREDIT",
    "DATA_CATEGORY_BODY",
    "DEFAULT_CHUNK_BYTES",
    "DEFAULT_HEARTBEAT_S",
    "DEFAULT_INIT_CREDIT",
    "DEFAULT_MAX_INFLIGHT",
    "ERROR",
    "FLAG_ABORT",
    "FLAG_END",
    "HELLO",
    "HTTP_CANCEL",
    "HTTP_REQUEST",
    "HTTP_RESPONSE",
    "MAX_CONTROL_JSON",
    "MAX_DATA_CHUNK",
    "MAX_FRAME_BYTES",
    "PING",
    "PONG",
    "PROTO_VERSION",
    "ProtocolError",
    "REQUEST_HEADER_ALLOWLIST",
    "SHARE_REGISTER",
    "SHARE_REGISTERED",
    "SHARE_REGISTER_ERROR",
    "SHARE_REVOKED",
    "SHARE_UNREGISTER",
    "WELCOME",
    "decode_control",
    "decode_data",
    "encode_control",
    "encode_data",
    "filter_request_headers",
]
