"""RFC 6455 frame codec — pure stdlib, role-aware, bounded.

Extracted from ``gateway.py`` so both the daemon's server-side WebSocket and the
share tunnel's client/relay sides share one hardened implementation.  The reader
validates FIN, RSV, opcode, canonical length, the 64-bit length high bit, mask
direction, control-frame size, and an explicit maximum payload — none of which
the original inline reader did.

``expect_mask``:
  * ``True``  — peer MUST mask (relay reading a client/daemon frame),
  * ``False`` — peer MUST NOT mask (client reading a server frame),
  * ``None``  — lenient gateway-compat mode: accept either direction and skip
    text UTF-8 enforcement, matching the daemon's original tolerant reader.
"""

from __future__ import annotations

import base64
import hashlib
import os
import struct

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# A conservative default cap; callers pass an explicit ``max_len`` for their tier.
DEFAULT_MAX_FRAME = 16 << 20

_CONTROL_OPCODES = frozenset({0x8, 0x9, 0xA})
_DATA_OPCODES = frozenset({0x1, 0x2})
_VALID_OPCODES = _CONTROL_OPCODES | _DATA_OPCODES


def ws_accept(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()


def ws_client_key() -> str:
    """A fresh 16-byte base64 Sec-WebSocket-Key for a client handshake."""

    return base64.b64encode(os.urandom(16)).decode("ascii")


def frame_header_size(payload_len: int) -> int:
    """Unmasked server-frame header byte count for a payload of this length."""

    if payload_len < 126:
        return 2
    if payload_len <= 0xFFFF:
        return 4
    return 10


def _mask(payload: bytes, mask: bytes) -> bytes:
    if not payload:
        return payload
    # int-XOR fast path: orders of magnitude faster than a per-byte loop for the
    # 256 KiB data chunks the tunnel moves.
    repeated = (mask * (len(payload) // 4 + 1))[: len(payload)]
    return (int.from_bytes(payload, "big") ^ int.from_bytes(repeated, "big")).to_bytes(
        len(payload), "big"
    )


def ws_encode(
    payload: bytes,
    opcode: int = 0x1,
    *,
    mask: bool = False,
    fin: bool = True,
) -> bytes:
    frame = bytearray()
    frame.append((0x80 if fin else 0x00) | (opcode & 0x0F))
    n = len(payload)
    mask_bit = 0x80 if mask else 0x00
    if n < 126:
        frame.append(mask_bit | n)
    elif n <= 0xFFFF:
        frame.append(mask_bit | 126)
        frame += struct.pack(">H", n)
    else:
        frame.append(mask_bit | 127)
        frame += struct.pack(">Q", n)
    if mask:
        key = os.urandom(4)
        frame += key
        frame += _mask(payload, key)
    else:
        frame += payload
    return bytes(frame)


def _read_exact(stream, n: int) -> bytes | None:
    if n == 0:
        return b""
    chunks: list[bytes] = []
    remaining = n
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def ws_read_frame(
    stream,
    *,
    expect_mask: bool | None = None,
    max_len: int = DEFAULT_MAX_FRAME,
) -> tuple[int, bytes] | None:
    """Read and validate one frame; return ``(opcode, payload)`` or ``None``.

    ``None`` means end-of-stream or any protocol violation; the caller closes.
    v1 does not support continuation frames (FIN must be set).
    """

    try:
        header = _read_exact(stream, 2)
        if header is None:
            return None
        b0, b1 = header[0], header[1]
        fin = b0 & 0x80
        rsv = b0 & 0x70
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        length = b1 & 0x7F

        if rsv:
            return None
        if opcode not in _VALID_OPCODES:
            return None
        if not fin:
            # No fragmentation support; control frames must always be final.
            return None
        if expect_mask is True and not masked:
            return None
        if expect_mask is False and masked:
            return None

        if length == 126:
            ext = _read_exact(stream, 2)
            if ext is None:
                return None
            length = struct.unpack(">H", ext)[0]
            if length < 126:  # non-canonical
                return None
        elif length == 127:
            ext = _read_exact(stream, 8)
            if ext is None:
                return None
            length = struct.unpack(">Q", ext)[0]
            if length & 0x8000000000000000:  # high bit must be zero
                return None
            if length < 0x10000:  # non-canonical
                return None

        if opcode in _CONTROL_OPCODES and length > 125:
            return None
        if length > max_len:
            return None

        mask_key = _read_exact(stream, 4) if masked else b"\x00\x00\x00\x00"
        if mask_key is None:
            return None
        data = _read_exact(stream, length) if length else b""
        if data is None:
            return None
        if masked:
            data = _mask(data, mask_key)

        # Strict mode enforces text/close UTF-8; lenient (gateway) mode tolerates
        # a bad text frame so the caller can skip it instead of dropping the peer.
        if expect_mask is not None and opcode == 0x1:
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                return None
        return opcode, data
    except (OSError, struct.error, ValueError):
        return None


__all__ = [
    "DEFAULT_MAX_FRAME",
    "WS_GUID",
    "frame_header_size",
    "ws_accept",
    "ws_client_key",
    "ws_encode",
    "ws_read_frame",
]
