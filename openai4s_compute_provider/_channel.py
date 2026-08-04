"""Low-level IO helpers: the fd-3 control channel, the auth handshake reader,
the courtesy stdout/stderr scrubber, and a byte formatter.

None of this knows about providers or ops — it's the transport plumbing the
resident builds on.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
from typing import Any

from ._constants import EXIT_PROTOCOL, FD_CTRL, LINE_CAP


def fmt_bytes(n: int) -> str:
    for unit, sh in (("GiB", 30), ("MiB", 20), ("KiB", 10)):
        if n >= 1 << sh:
            return f"{n / (1 << sh):.1f} {unit}"
    return f"{n} B"


# ── fd-3 control channel (repl mode only) ────────────────────────────────────


def _fd3_write(obj: dict[str, Any]) -> None:
    line = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    os.write(FD_CTRL, line[:LINE_CAP] + b"\n")


def write_ready(*, confined: bool) -> None:
    _fd3_write({"ready": True, "confined": confined})


def write_event(kind: str, **extra: Any) -> None:
    _fd3_write({"event": True, "kind": kind, **extra})


def read_auth(*, fd: int = FD_CTRL) -> dict[str, str]:
    """Block for the single newline-terminated {op:"auth", ...} message the
    host writes then closes. Oneshot reads stdin (fd=0); repl reads fd-3.
    Any other shape (or EOF before newline) is a protocol violation — exit
    rather than run unauthenticated."""
    buf = bytearray()
    while b"\n" not in buf:
        chunk = os.read(fd, 65536)
        if not chunk or len(buf) > LINE_CAP:
            sys.exit(EXIT_PROTOCOL)
        buf.extend(chunk)
    try:
        msg = json.loads(bytes(buf).split(b"\n", 1)[0])
    except ValueError:
        sys.exit(EXIT_PROTOCOL)
    if not isinstance(msg, dict) or msg.get("op") != "auth":
        sys.exit(EXIT_PROTOCOL)
    return {k: v for k, v in msg.items() if k != "op"}


class ScrubWriter(io.TextIOBase):
    """Courtesy filter for naive print(token). Not a control on deliberate
    exfil — the kernel grant already accepts the agent has the token's
    capabilities."""

    def __init__(self, inner: Any, pattern: re.Pattern[str]):
        self._inner = inner
        self._pat = pattern

    def write(self, s: str) -> int:
        return self._inner.write(self._pat.sub("***", s))

    def flush(self) -> None:
        self._inner.flush()

    def fileno(self) -> int:
        return self._inner.fileno()
