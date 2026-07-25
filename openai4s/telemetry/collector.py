"""The receiving end. A reference implementation, not a deployment.

This is the server that would run at log.openai4s.org. It is here so the wire
format has a second reader and the claims made on the sending side can be
checked against something that actually parses them -- not so this repository
runs it. There is no build step that starts it and no test that binds a public
port.

It mirrors `openai4s/share/relay.py` on purpose: a stdlib `ThreadingHTTPServer`,
a token-bucket rate limiter keyed by client, a body-size cap enforced *before*
the body is read, and a Host allowlist (the relay's history includes a
DNS-rebinding finding from a missing one, so this does not repeat it). No third
party sees the data and no dependency enters the tree.

What it adds, and what a relay has no reason to, is that **it does not trust the
sender**. The client is built not to emit free text, but a collector that
assumes its input is clean is one payload away from storing a prompt fragment
because some other client, or some future version, sent one. So every envelope
is re-validated against the same declaration the client sanitises with, and a
record carrying anything outside its declared domain is dropped, field by
field, on arrival. The wire is checked at both ends.

It stores nothing but counts. There is no per-install log, because a file of
"install X did Y at time T" is the surveillance the whole design exists to
avoid; the aggregate is what a maintainer needs and the most that should exist.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from openai4s.telemetry import schema

#: Reject a body larger than this before reading it. A telemetry envelope is
#: tiny; anything approaching this is abuse, not data.
MAX_BODY_BYTES = 64 * 1024

#: Distinct install ids to remember. A cap, because an unbounded id set is how
#: an anonymous counter turns into a census. Past it, new ids are counted in
#: aggregate but not enumerated.
MAX_INSTALLS = 100_000


class Aggregate:
    """Counts, and nothing that could name a person.

    Keyed by (event, outcome), never by install id and never with a timestamp
    per event. `installs` is a set only so the same id is not double-counted in
    the total; it holds opaque 32-hex strings and is capped.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: dict[tuple[str, str], int] = {}
        self.installs: set[str] = set()
        self.rejected = 0

    def record(self, install_id: str, records: list[dict[str, Any]]) -> None:
        with self._lock:
            if len(self.installs) < MAX_INSTALLS:
                self.installs.add(install_id)
            for rec in records:
                key = (rec.get("event", "?"), rec.get("outcome", ""))
                self.events[key] = self.events.get(key, 0) + int(rec.get("count", 1))

    def note_rejected(self) -> None:
        with self._lock:
            self.rejected += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "installs": len(self.installs),
                "events": {f"{e}/{o}": n for (e, o), n in sorted(self.events.items())},
                "rejected": self.rejected,
            }


def validate_envelope(raw: bytes) -> dict[str, Any] | None:
    """Parse and re-sanitise a received envelope, or None if it is not usable.

    This is the server refusing to trust the client. It applies the same
    declaration the sender does, so a field the client should never have sent
    is dropped here too -- the guarantee holds even against a client that does
    not.
    """
    if len(raw) > MAX_BODY_BYTES:
        return None
    try:
        envelope = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(envelope, dict):
        return None

    clean = schema.sanitise_envelope(envelope)
    if "install_id" not in clean or "schema" not in clean:
        return None

    events = envelope.get("events")
    if not isinstance(events, list) or not events:
        return None
    records = [schema.sanitise_record(e) for e in events if isinstance(e, dict)]
    records = [r for r in records if r.get("event")][: schema.MAX_RECORDS]
    if not records:
        return None

    clean["events"] = records
    return clean


class _RateLimiter:
    """Token bucket per client, mirroring the relay's."""

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


def make_collector_handler(
    aggregate: Aggregate,
    *,
    allowed_hosts: frozenset[str],
    rate_per_sec: float = 20.0,
    rate_burst: int = 60,
    trust_proxy: bool = False,
):
    limiter = _RateLimiter(rate_per_sec, rate_burst)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "openai4s-telemetry/1.0"

        def log_message(self, *a):  # quiet
            pass

        def _client_ip(self) -> str:
            if trust_proxy:
                fwd = self.headers.get("X-Forwarded-For")
                if fwd:
                    return fwd.split(",")[0].strip()
            return self.client_address[0]

        def _host_ok(self) -> bool:
            # A Host allowlist, not an Origin==Host check: the relay's history
            # has a DNS-rebinding finding that came from trusting Host without
            # one, and a collector copying that shape would inherit it.
            host = (self.headers.get("Host") or "").split(":")[0].lower()
            return host in allowed_hosts

        def _simple(self, code: int, body: bytes) -> None:
            self.close_connection = True
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass

        def do_GET(self):
            self._simple(404, b"not found\n")

        def do_POST(self):
            self.close_connection = True
            if not self._host_ok():
                self._simple(421, b"misdirected\n")
                return
            if self.path.split("?", 1)[0] != "/v1/events":
                self._simple(404, b"not found\n")
                return
            if not limiter.allow(self._client_ip(), time.time()):
                self._simple(429, b"rate limited\n")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._simple(411, b"length required\n")
                return
            if length <= 0 or length > MAX_BODY_BYTES:
                # Capped before the body is read, not after: reading an
                # arbitrarily large body to then reject it is the abuse.
                self._simple(413, b"payload too large\n")
                return
            raw = self.rfile.read(length)

            envelope = validate_envelope(raw)
            if envelope is None:
                aggregate.note_rejected()
                self._simple(400, b"rejected\n")
                return
            aggregate.record(envelope["install_id"], envelope["events"])
            self._simple(204, b"")

    return Handler


class CollectorServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        allowed_hosts: frozenset[str],
        trust_proxy: bool = False,
    ) -> None:
        self.aggregate = Aggregate()
        super().__init__(
            address,
            make_collector_handler(
                self.aggregate,
                allowed_hosts=allowed_hosts,
                trust_proxy=trust_proxy,
            ),
        )


__all__ = [
    "Aggregate",
    "CollectorServer",
    "MAX_BODY_BYTES",
    "make_collector_handler",
    "validate_envelope",
]
