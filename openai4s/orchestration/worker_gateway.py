"""Where remote workers dial in (plan M3b-1, daemon side).

Off unless `OPENAI4S_WORKER_LISTEN` says otherwise: a default install
creates no socket, binds no port and starts no thread. That is not
politeness — a listener on by default is an attack surface on every
single-user laptop that will never run a cluster job.

The sequence, and why each step is where it is:

1. **accept** — the worker is the client, because a compute node is usually
   reachable from nothing while the daemon usually is.
2. **read one line, with a deadline** — a peer that connects and says
   nothing must not hold a slot forever, and the line is bounded because an
   unauthenticated peer's message length is an unauthenticated peer's
   choice.
3. **verify and burn the credential** — before anything else is read. The
   socket carries `host_call` traffic, which is arbitrary Host RPC; a
   listener that served first and checked later would be a remote execution
   surface for the duration of "later".
4. **hand the socket over** — registration resolves the waiter that a
   session is blocked on, and the transport takes it from there.

Binding defaults to all interfaces because a compute node cannot reach
`127.0.0.1` on the daemon's host — and that is precisely why step 3 exists
and is not optional.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from openai4s.kernel.transport import OutboundTcpTransport
from openai4s.orchestration.bootstrap import (
    BootstrapAuthority,
    BootstrapCredential,
    BootstrapError,
)

#: The env var that turns this on. `host:port`, or just `port`.
LISTEN_ENV = "OPENAI4S_WORKER_LISTEN"

#: How long an accepted peer has to present its credential. Short: it has
#: nothing else to do, and a slow one is indistinguishable from a squatter.
HANDSHAKE_TIMEOUT_S = 30.0

#: Cap on the handshake line. An unauthenticated peer does not get to choose
#: how much memory this daemon allocates.
MAX_HANDSHAKE_BYTES = 64 * 1024


@dataclass(frozen=True)
class Registration:
    """One authenticated worker, ready to be driven."""

    allocation_id: str
    epoch: int
    rank: int
    transport: OutboundTcpTransport
    peer: str


def parse_listen(spec: str | None) -> tuple[str, int] | None:
    """`"8765"` or `"0.0.0.0:8765"` -> (host, port); anything else -> None.

    Returning None rather than raising for an unset value keeps "the
    feature is off" on the quiet path; a *malformed* value does raise,
    because an operator who typed a port wrong should not silently get no
    listener at all.
    """
    text = (spec or "").strip()
    if not text:
        return None
    host, _, port_text = text.rpartition(":")
    if not port_text.isdigit():
        raise ValueError(f"{LISTEN_ENV} must be 'port' or 'host:port', got {spec!r}")
    port = int(port_text)
    if not (0 < port < 65536):
        raise ValueError(f"{LISTEN_ENV} port out of range: {port}")
    # A compute node cannot reach the daemon's loopback, so the default bind
    # is every interface. The credential check is what makes that safe.
    return (host or "0.0.0.0", port)


class WorkerGateway:
    """Accepts authenticated worker connections and parks them for pickup."""

    def __init__(
        self,
        authority: BootstrapAuthority,
        *,
        bind: tuple[str, int],
        on_register: Callable[[Registration], None] | None = None,
        interrupt_hook_for: Callable[[str], Callable[[], bool]] | None = None,
    ) -> None:
        self._authority = authority
        self._bind = bind
        self._on_register = on_register
        self._interrupt_hook_for = interrupt_hook_for
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._waiters: dict[tuple[str, int], threading.Event] = {}
        # A *list* per attempt, not one registration. A multi-node job
        # places one worker per rank and all of them present a credential
        # for the same (allocation, epoch); keyed by that pair alone, rank 1
        # silently replaced rank 0 and a two-node session looked like a
        # one-node session that worked. Gang readiness (M4-3) is counting
        # these, so losing them is losing the count.
        self._arrived: dict[tuple[str, int], list[Registration]] = {}
        self.rejected = 0
        self.accepted = 0

    # --- lifecycle --------------------------------------------------------

    @property
    def address(self) -> tuple[str, int] | None:
        return self._server.getsockname() if self._server is not None else None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(self._bind)
        server.listen(16)
        server.settimeout(0.5)  # so stop() is prompt rather than eventual
        self._server = server
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._serve, name="openai4s-worker-gateway", daemon=True
        )
        self._thread.start()

    def stop(self, *, timeout_s: float = 5.0) -> None:
        self._stop.set()
        server, self._server = self._server, None
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=timeout_s)

    def _serve(self) -> None:
        while not self._stop.is_set():
            server = self._server
            if server is None:
                return
            try:
                conn, addr = server.accept()
            except socket.timeout:  # noqa: UP041 — stdlib alias
                continue
            except OSError:
                return
            # One thread per handshake so a slow or hostile peer cannot block
            # the accept loop for everyone else.
            threading.Thread(
                target=self._handshake,
                args=(conn, addr),
                name="openai4s-worker-handshake",
                daemon=True,
            ).start()

    # --- admission --------------------------------------------------------

    def _handshake(self, conn: socket.socket, addr: Any) -> None:
        peer = f"{addr[0]}:{addr[1]}" if isinstance(addr, tuple) else str(addr)
        conn.settimeout(HANDSHAKE_TIMEOUT_S)
        try:
            line = self._read_handshake_line(conn)
            credential = BootstrapCredential.from_json(line)
            # Verify and burn before a single protocol byte is exchanged.
            self._authority.consume(credential)
        except (BootstrapError, OSError, ValueError) as exc:
            with self._lock:
                self.rejected += 1
            # The peer is told it was refused, not why: the difference
            # between "expired", "replayed" and "forged" is an oracle for
            # somebody guessing.
            try:
                conn.sendall(
                    (json.dumps({"ok": False, "error": "refused"}) + "\n").encode()
                )
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass
            self._note_rejection(peer, exc)
            return

        try:
            conn.sendall((json.dumps({"ok": True}) + "\n").encode())
        except OSError:
            try:
                conn.close()
            except OSError:
                pass
            return
        # Back to blocking for the protocol itself: a cell may legitimately
        # take hours, and a socket timeout there would look like a dead
        # worker every time somebody ran a real computation.
        conn.settimeout(None)

        hook = None
        if self._interrupt_hook_for is not None:
            try:
                hook = self._interrupt_hook_for(credential.allocation_id)
            except Exception:  # noqa: BLE001
                hook = None
        transport = OutboundTcpTransport(conn, peer=peer, interrupt_hook=hook)
        registration = Registration(
            allocation_id=credential.allocation_id,
            epoch=credential.epoch,
            rank=credential.rank,
            transport=transport,
            peer=peer,
        )
        key = (credential.allocation_id, credential.epoch)
        with self._lock:
            self.accepted += 1
            self._arrived.setdefault(key, []).append(registration)
            waiter = self._waiters.get(key)
        if waiter is not None:
            waiter.set()
        if self._on_register is not None:
            try:
                self._on_register(registration)
            except Exception:  # noqa: BLE001 — a listener must not kill the gateway
                pass

    @staticmethod
    def _read_handshake_line(conn: socket.socket) -> str:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                raise BootstrapError("peer closed before presenting a credential")
            chunks.append(chunk)
            total += len(chunk)
            if b"\n" in chunk:
                break
            if total > MAX_HANDSHAKE_BYTES:
                raise BootstrapError("handshake line too long")
        return b"".join(chunks).split(b"\n", 1)[0].decode("utf-8", "replace")

    def _note_rejection(self, peer: str, exc: BaseException) -> None:
        # stderr, not a broadcast: this is daemon-level and there is no
        # session it belongs to.
        print(
            f"[openai4s] worker gateway refused {peer}: {exc}",
            file=__import__("sys").stderr,
            flush=True,
        )

    # --- waiting ----------------------------------------------------------

    def await_worker(
        self, allocation_id: str, epoch: int, *, timeout_s: float
    ) -> Registration | None:
        """Block until the worker for this exact attempt dials in.

        Keyed by (allocation, epoch) so a straggler from a previous epoch
        cannot satisfy a wait for the current one — the same fencing the
        credential check enforces, applied to the rendezvous.
        """
        arrivals = self.await_workers(
            allocation_id, epoch, expected=1, timeout_s=timeout_s
        )
        return arrivals[0] if arrivals else None

    def await_workers(
        self, allocation_id: str, epoch: int, *, expected: int, timeout_s: float
    ) -> list[Registration]:
        """Block until `expected` workers for this exact attempt have dialled in.

        M4-3, gang readiness: a multi-node session is ready when every rank
        has registered, not when the first one has. Returning early on rank
        0 is how a distributed run starts against a job whose other nodes
        are still being placed — the failure then surfaces inside the user's
        computation, where it looks like their bug.

        Returns what actually arrived on timeout rather than raising, so a
        caller can say "3 of 4" instead of "not ready", which is the
        difference between a diagnosis and a spinner.
        """
        key = (allocation_id, int(epoch))
        deadline = time.monotonic() + max(0.0, timeout_s)
        while True:
            with self._lock:
                have = list(self._arrived.get(key) or ())
                if len(have) >= expected:
                    self._arrived.pop(key, None)
                    self._waiters.pop(key, None)
                    return have
                waiter = self._waiters.get(key)
                if waiter is None:
                    waiter = threading.Event()
                    self._waiters[key] = waiter
                waiter.clear()
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not waiter.wait(timeout=remaining):
                with self._lock:
                    self._waiters.pop(key, None)
                    # Hand back the partial set. It is the caller's to
                    # release, and pretending nothing arrived would strand
                    # workers that are connected and waiting.
                    return list(self._arrived.pop(key, []) or [])


def gateway_from_environment(
    authority: BootstrapAuthority, **kwargs: Any
) -> WorkerGateway | None:
    """A gateway if the operator asked for one, else None (the default)."""
    bind = parse_listen(os.environ.get(LISTEN_ENV))
    if bind is None:
        return None
    return WorkerGateway(authority, bind=bind, **kwargs)


__all__ = [
    "HANDSHAKE_TIMEOUT_S",
    "LISTEN_ENV",
    "MAX_HANDSHAKE_BYTES",
    "Registration",
    "WorkerGateway",
    "gateway_from_environment",
    "parse_listen",
]
