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

import dataclasses
import json
import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
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

#: How many connections may be mid-handshake at once, before any of them has
#: proved anything. A gang job dials one worker per rank simultaneously, so
#: this has to sit comfortably above the widest allocation a site runs; it is
#: a bound on *unauthenticated* concurrency, not on how many workers a daemon
#: may end up driving.
MAX_PENDING_HANDSHAKES = 64

#: How long an arrived-but-unawaited registration is kept before it is closed
#: and dropped. Generous, because the ordinary reason a worker waits is that
#: its session is between attach attempts; short enough that a straggler from
#: a superseded epoch does not hold a socket for the daemon's lifetime.
ORPHAN_REGISTRATION_TTL_S = 15 * 60.0


@dataclass(frozen=True)
class Registration:
    """One authenticated worker, ready to be driven."""

    allocation_id: str
    epoch: int
    rank: int
    transport: OutboundTcpTransport
    peer: str
    #: When this worker was admitted, on the monotonic clock. Used only to
    #: reap registrations nobody ever awaits; see `_reap_locked`.
    #:
    #: A `default_factory`, not `0.0`: a plain zero default means "admitted
    #: at the epoch", so any Registration built without an explicit value
    #: would be instantly past the reap cutoff and closed under a caller
    #: still using it. The safe default for "when did this arrive" is now.
    arrived_at: float = field(default_factory=time.monotonic)
    #: The `authorization_generation` this connection was admitted under,
    #: minted by the Host after the credential verified and echoed to the
    #: worker in the handshake response. It is on the Registration because
    #: that is the object the daemon trusts: it exists only for a peer that
    #: presented a valid, unburned, in-epoch credential, so a Kernel built
    #: from one may adopt the value without asking anything further.
    generation: str = ""


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


def _enable_keepalive(conn: socket.socket) -> None:
    """Turn on TCP keepalive, as aggressively as this platform allows.

    Best-effort by design: the per-idle/interval/count options are named
    differently on Linux and macOS and are absent elsewhere, and a socket
    without them is still better off with plain `SO_KEEPALIVE` than with
    nothing. Every setting is individually guarded so an unsupported one
    cannot cost us the ones that did apply.
    """
    for level, option, value in (
        (socket.SOL_SOCKET, getattr(socket, "SO_KEEPALIVE", None), 1),
        # Linux: idle seconds before the first probe, then interval and count.
        (socket.IPPROTO_TCP, getattr(socket, "TCP_KEEPIDLE", None), 60),
        (socket.IPPROTO_TCP, getattr(socket, "TCP_KEEPINTVL", None), 20),
        (socket.IPPROTO_TCP, getattr(socket, "TCP_KEEPCNT", None), 6),
        # macOS spells the idle timer differently.
        (socket.IPPROTO_TCP, getattr(socket, "TCP_KEEPALIVE", None), 60),
    ):
        if option is None:
            continue
        try:
            conn.setsockopt(level, option, value)
        except OSError:  # pragma: no cover - platform dependent
            pass


class WorkerGateway:
    """Accepts authenticated worker connections and parks them for pickup."""

    def __init__(
        self,
        authority: BootstrapAuthority,
        *,
        bind: tuple[str, int],
        on_register: Callable[[Registration], None] | None = None,
        interrupt_hook_for: Callable[[str], Callable[[], bool]] | None = None,
        max_pending_handshakes: int = MAX_PENDING_HANDSHAKES,
    ) -> None:
        self._authority = authority
        self._bind = bind
        self._on_register = on_register
        self._interrupt_hook_for = interrupt_hook_for
        self._registration_is_expected: Callable[[str, int], bool] | None = None
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._generation = 0
        self._handshakes: dict[threading.Thread, socket.socket] = {}
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
        #: Sockets closed because every pre-auth slot was busy. Counted
        #: separately from `rejected`, which means "presented something and
        #: it did not verify" -- conflating a refused credential with a full
        #: pool would hide exactly the saturation an operator is looking for.
        self.refused_busy = 0
        #: Registrations closed because nobody awaited them in time.
        self.reaped = 0
        self._handshake_slots = threading.Semaphore(max(1, int(max_pending_handshakes)))

    # --- lifecycle --------------------------------------------------------

    @property
    def address(self) -> tuple[str, int] | None:
        return self._server.getsockname() if self._server is not None else None

    def set_registration_expectation(
        self, predicate: Callable[[str, int], bool] | None
    ) -> None:
        """Install the durable ownership check used by orphan housekeeping."""
        with self._lock:
            self._registration_is_expected = predicate

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
        with self._lock:
            self._generation += 1
        self._thread = threading.Thread(
            target=self._serve, name="openai4s-worker-gateway", daemon=True
        )
        self._thread.start()

    def stop(self, *, timeout_s: float = 5.0) -> None:
        deadline = time.monotonic() + max(0.0, timeout_s)
        self._stop.set()
        server, self._server = self._server, None
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        # Accepted sockets are not owned by the listener any more. Close every
        # in-flight handshake to wake recv/send, then join those threads before
        # clearing parked registrations. Otherwise a verified peer can publish
        # into `_arrived` *after* stop() has emptied it.
        with self._lock:
            handshakes = list(self._handshakes.items())
        for _handshake, conn in handshakes:
            try:
                conn.close()
            except OSError:
                pass
        for handshake, _conn in handshakes:
            handshake.join(timeout=max(0.0, deadline - time.monotonic()))
        # Close what nobody collected. `stop()` used to close only the
        # listening socket, so every parked registration's accepted socket
        # and its two makefile wrappers survived the gateway that owned them
        # -- which in a test suite is one fd triple per gateway fixture.
        with self._lock:
            parked = [r for group in self._arrived.values() for r in group]
            self._arrived.clear()
            self._waiters.clear()
        for registration in parked:
            try:
                registration.transport.close(graceful=False)
            except Exception:  # noqa: BLE001 — shutdown must not raise
                pass

    def _serve(self) -> None:
        while not self._stop.is_set():
            server = self._server
            if server is None:
                return
            try:
                conn, addr = server.accept()
            except socket.timeout:  # noqa: UP041 — stdlib alias
                # The accept timeout is also the gateway's housekeeping tick.
                # Reaping only from `await_workers` or another arrival leaves
                # the final straggler parked forever when the daemon becomes
                # otherwise idle -- exactly the case an orphan timeout is
                # supposed to bound.
                with self._lock:
                    self._reap_locked()
                continue
            except OSError:
                return
            # One thread per handshake so a slow or hostile peer cannot block
            # the accept loop for everyone else -- but a *bounded* number of
            # them. Unbounded, this is an unauthenticated thread-exhaustion
            # path: the thread is allocated before the credential is checked,
            # so anyone who can reach the listener can hold one per socket,
            # for the whole handshake deadline, at the cost of a TCP connect.
            # Excess sockets are closed here rather than queued, because a
            # queue behind a full pool is the same resource under a different
            # name and a worker whose dial is refused simply retries.
            if not self._handshake_slots.acquire(blocking=False):
                with self._lock:
                    self.refused_busy += 1
                try:
                    conn.close()
                except OSError:
                    pass
                continue
            with self._lock:
                generation = self._generation
            handshake = threading.Thread(
                target=self._handshake_bounded,
                args=(conn, addr, generation),
                name="openai4s-worker-handshake",
                daemon=True,
            )
            with self._lock:
                if self._stop.is_set() or generation != self._generation:
                    self._handshake_slots.release()
                    try:
                        conn.close()
                    except OSError:
                        pass
                    continue
                self._handshakes[handshake] = conn
            handshake.start()

    def _handshake_bounded(
        self, conn: socket.socket, addr: Any, generation: int
    ) -> None:
        """`_handshake` with the pre-auth slot released the moment it ends.

        Released here rather than inside `_handshake` so that every exit
        path -- refusal, success, an unexpected raise -- gives the slot
        back. A slot leaked on an error path would turn a bounded pool into
        a smaller unbounded one, one connection at a time.
        """
        try:
            self._handshake(conn, addr, gateway_generation=generation)
        finally:
            with self._lock:
                self._handshakes.pop(threading.current_thread(), None)
            self._handshake_slots.release()

    # --- admission --------------------------------------------------------

    def _handshake(
        self, conn: socket.socket, addr: Any, *, gateway_generation: int | None = None
    ) -> None:
        peer = f"{addr[0]}:{addr[1]}" if isinstance(addr, tuple) else str(addr)
        conn.settimeout(HANDSHAKE_TIMEOUT_S)
        try:
            line = self._read_handshake_line(
                conn, time.monotonic() + HANDSHAKE_TIMEOUT_S
            )
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

        # Minted here, once the credential has verified, and handed back in
        # the same breath. `host.bash` binds its one-shot authorization to
        # the worker's generation, and a remote worker had no way to learn
        # one: the local path gets it through `_child_env`, which the
        # transport branch of `Kernel._spawn` returns before ever reaching.
        # So every `host.bash` from a cluster session was refused with
        # "worker generation does not match the active Host generation".
        #
        # The Host is the one minting it, so the check is not relaxed: it
        # still compares against a value only the Host chose. Delivering it
        # on the handshake response rather than in the job's environment
        # also keeps it out of the submission -- an environment variable
        # rides in `--export` and lands in the scheduler's job record, where
        # a credential-shaped value is exactly what INV-9 keeps out.
        #
        # Fresh per authenticated connection, so a re-auth, a new epoch and
        # a recovery each get their own: reusing one across attempts would
        # let a worker from the epoch before a recovery keep authorizing
        # against the generation the new one is using.
        worker_generation = f"kernel:{uuid.uuid4()}"
        try:
            conn.sendall(
                (
                    json.dumps({"ok": True, "generation": worker_generation}) + "\n"
                ).encode()
            )
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
        # Blocking forever is right for a cell that runs for hours; being
        # unable to notice a peer that stopped existing is not. Without
        # keepalive a remote node that dies without closing TCP (a fence, a
        # power loss, a severed network) leaves the daemon's reader parked in
        # `recv` with `alive()` still answering True, holding the kernel's
        # protocol transaction lock for the life of the process.
        _enable_keepalive(conn)

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
            generation=worker_generation,
        )
        key = (credential.allocation_id, credential.epoch)
        admitted = False
        with self._lock:
            # Reap here as well as in `await_workers`. The leak `_reap_locked`
            # was written for is a straggler nobody ever awaits -- a fenced-off
            # epoch, a session released while its job was still queued -- and
            # the only caller was the wait, so on a daemon where nothing waits
            # again the fd triple it drops was never dropped. Arrival is the
            # other moment the dict changes, so it is the other moment to sweep.
            if not self._stop.is_set() and (
                gateway_generation is None or gateway_generation == self._generation
            ):
                self._reap_locked()
                self.accepted += 1
                self._arrived.setdefault(key, []).append(registration)
                waiter = self._waiters.get(key)
                admitted = True
            else:
                waiter = None
        if not admitted:
            try:
                transport.close(graceful=False)
            except Exception:  # noqa: BLE001 — shutdown race must not publish
                pass
            return
        if waiter is not None:
            waiter.set()
        if self._on_register is not None:
            try:
                self._on_register(registration)
            except Exception:  # noqa: BLE001 — a listener must not kill the gateway
                pass

    @staticmethod
    def _read_handshake_line(conn: socket.socket, deadline: float) -> str:
        """One line, under a *total* deadline rather than a per-recv one.

        `settimeout` bounds each individual `recv`, which is not the same
        promise: a peer sending one byte every 29 seconds never trips a 30
        second socket timeout, and the 64 KiB cap is only reached after
        ~65,000 of those -- about three weeks holding a thread. The module
        docstring's "read one line, with a deadline" describes this, and the
        per-operation timeout did not implement it.
        """
        chunks: list[bytes] = []
        total = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BootstrapError("handshake deadline exceeded")
            conn.settimeout(remaining)
            chunk = conn.recv(4096)
            if not chunk:
                raise BootstrapError("peer closed before presenting a credential")
            chunks.append(chunk)
            total += len(chunk)
            if b"\n" in chunk:
                break
            if total > MAX_HANDSHAKE_BYTES:
                raise BootstrapError("handshake line too long")
        return b"".join(chunks).split(b"\n", 1)[0].decode("utf-8")

    def _reap_locked(self) -> int:
        """Close and drop arrivals nobody is going to await. Caller holds the lock.

        `_arrived` was only ever pruned by `await_workers`, and only for the
        key it was called with, so anything that arrived for a key nobody
        waits on stayed forever — holding a live `OutboundTcpTransport`, its
        two `makefile` wrappers and the accepted socket. Two ordinary paths
        produce those: a recovery bumps the epoch, so a straggler from the
        old one lands under `(alloc, old_epoch)`; and a session released
        while its job was still queued leaves its worker dialling in to
        nobody. One leaked fd triple per straggler, for the daemon's
        lifetime, ending in EMFILE on the accept loop.

        Age is measured from arrival rather than from last use because these
        are by definition unused: a registration a caller took ownership of
        was popped out of this dict.
        """
        if not self._arrived:
            return 0
        cutoff = time.monotonic() - ORPHAN_REGISTRATION_TTL_S
        dropped = 0
        for key in [k for k in self._arrived if k not in self._waiters]:
            expected = self._registration_is_expected
            if expected is not None:
                try:
                    if expected(key[0], key[1]):
                        # A live session may legitimately sit between worker
                        # registration and its first Cell longer than the
                        # orphan TTL. Its lease, not user think-time here,
                        # owns that resource.
                        continue
                except Exception:  # noqa: BLE001 — uncertainty must not kill it
                    # Storage can be briefly busy or closing.  Reaping a valid
                    # scheduler worker is irreversible; keeping it until the
                    # next housekeeping tick is bounded by the same TTL and
                    # becomes decidable again once storage recovers.
                    continue
            keep: list[Registration] = []
            for registration in self._arrived.get(key) or ():
                # `getattr`, because `_arrived` is also written directly by
                # tests that stand in their own registration double. An
                # object this gateway did not build has no arrival time to
                # judge, and closing somebody else's transport on a guess is
                # worse than keeping it: in production every entry here came
                # from `_handshake`, so the guess would never be needed.
                arrived = getattr(registration, "arrived_at", None)
                if arrived is None or arrived > cutoff:
                    keep.append(registration)
                    continue
                dropped += 1
                try:
                    registration.transport.close(graceful=False)
                except Exception:  # noqa: BLE001 — reaping must not raise
                    pass
            if keep:
                self._arrived[key] = keep
            else:
                self._arrived.pop(key, None)
        if dropped:
            self.reaped += dropped
        return dropped

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
                self._reap_locked()
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
                    # Sweep while this key still has its waiter: a partial gang
                    # belongs to the caller below and must be restamped, while
                    # an unrelated registration that arrived after the sweep at
                    # the top of the loop is now old and unclaimed. Without this
                    # second sweep, the ack-before-parking race could miss that
                    # orphan until some future gateway activity (or forever).
                    self._reap_locked()
                    self._waiters.pop(key, None)
                    # Hand back a *copy* of the partial set and keep it.
                    #
                    # This used to `pop`, which destroyed the ranks that had
                    # already arrived — and `attach_worker` assigns rather
                    # than merges, so the next attempt started from whatever
                    # the second call happened to see. With a 5s attach
                    # timeout and rank 0 at t=1s, rank 1 at t=6s: attempt one
                    # took [r0] and dropped it, attempt two saw only [r1],
                    # and `workers_registered >= workers_expected` was
                    # unreachable forever — rank 0's socket orphaned with
                    # nothing left holding a reference to close it.
                    #
                    # Accumulating instead makes a retry additive, which is
                    # what "3 of 4, waiting for the rest" has to mean. The
                    # reaper below is what stops a set nobody ever awaits
                    # from living forever.
                    #
                    # But the reaper's rule is "old and unclaimed", and the
                    # waiter is gone by the time it looks -- so a partial gang
                    # held across the TTL had rank 0's transport closed
                    # underneath a session that was still counting it, and the
                    # last rank's arrival then built a Kernel over a closed
                    # socket. Restamp what we hand out: for a partial set the
                    # clock that matters is "how long since anybody asked",
                    # not "how long since it arrived", and that restores the
                    # reaper's own premise that a registration still in this
                    # dict is one nobody has taken. A caller that stops asking
                    # still ages out on the same TTL.
                    now = time.monotonic()
                    claimed = [
                        dataclasses.replace(registration, arrived_at=now)
                        for registration in (self._arrived.get(key) or ())
                    ]
                    if claimed:
                        self._arrived[key] = claimed
                    return list(claimed)


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
