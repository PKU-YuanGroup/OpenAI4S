"""How a Kernel reaches its worker: pipes locally, a socket across a cluster.

The manager's protocol discipline — one frame reader, id-routed
`host_response`, the host-call transaction lock — is unchanged by this file
and must stay that way. What moves here is only *how bytes get to the other
end*, so that a worker running on a compute node is the same conversation
over a different pipe.

`PipeTransport` is the local path, moved rather than rewritten: the Popen
call, the stderr drain thread and the shutdown sequence are the ones that
were in `manager.py`, with their comments, because each of them records a
failure that was paid for once already (a filled stderr pipe deadlocking a
cell; a daemon thread parked in a buffered read turning a clean exit into
SIGABRT; a restart leaking a zombie).

`OutboundTcpTransport` is the remote path: the daemon listens, the worker
dials in from wherever the scheduler put it, and the connection is accepted
only after it proves it holds a credential this daemon issued. Two
properties are deliberate:

* **It listens, the worker dials.** A compute node is usually reachable from
  nothing; the daemon usually is. Making the worker the client means no
  inbound firewall rule on the cluster and no address for the daemon to
  guess.
* **An unauthenticated connection is closed, never served.** The socket
  carries `host_call` traffic, which is arbitrary Host RPC. A transport that
  accepted first and checked later would be a remote execution surface for
  the duration of "later".

Interrupt is the one operation that does not survive the move unchanged. A
SIGINT to a local child is a signal to a pid this process owns; there is no
such pid across a cluster, so the remote transport takes an explicit hook
(the scheduler's own signal delivery) and reports honestly when it has none
— rather than returning success for something that did not happen.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
from typing import Any, Callable, Protocol

#: How long to wait for a remote worker to dial in before giving up. A
#: queued job may sit for hours, so this is not the queue wait — the caller
#: does not construct the transport until the allocation is running.
DEFAULT_CONNECT_TIMEOUT_S = 300.0

#: Cap on one protocol line from a remote worker. The local path is bounded
#: by the worker's own outbound cap; a socket has no such courtesy, and an
#: unbounded readline on a network peer is a memory exhaustion primitive.
MAX_LINE_BYTES = 16 * 1024 * 1024


class KernelTransport(Protocol):
    """The bytes-and-liveness half of talking to a worker."""

    def write_line(self, line: str) -> None: ...

    def read_line(self) -> str:
        """One line, or "" at end of stream."""
        ...

    def alive(self) -> bool: ...

    def interrupt(self) -> bool:
        """Deliver an interrupt. False = could not, so the caller must not
        report success."""
        ...

    def kill(self) -> None: ...

    def close(self, *, graceful: bool = True) -> None: ...

    @property
    def process(self) -> Any:
        """The local child, when there is one. None for a remote worker —
        and callers must treat it as optional rather than assume a pid."""
        ...

    @property
    def stderr_tail(self) -> Any:
        """Bounded tail of the worker's stderr, when this transport can see
        it. None when it cannot, which is not the same as empty."""
        ...


class PipeTransport:
    """The local worker: a child process over three pipes.

    Owns the Popen so that `Kernel` does not have to know whether its worker
    is local; `process` is still exposed because the local path's callers
    (signals, pid, the sandbox) legitimately need the child itself.
    """

    def __init__(
        self,
        command: list[str],
        *,
        cwd: str | None,
        env: dict[str, str],
        stderr_tail_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=cwd,
            env=env,
        )
        self._stderr_tail = (
            stderr_tail_factory() if stderr_tail_factory is not None else None
        )
        if self._stderr_tail is not None:
            self._start_stderr_drain()

    def _start_stderr_drain(self) -> None:
        # Drain stderr continuously into a bounded tail. Without this, a cell
        # whose child processes write to inherited fd2 (R `system()`, an
        # uncaptured subprocess in python) fills the 64KB pipe and deadlocks
        # the cell forever — nothing used to read stderr until worker death.
        # The tail keeps the death diagnostics the old blocking read provided.
        #
        # Bounded in BYTES, at the read: a line COUNT applied after the
        # allocation is no bound at all when one producer emits a single
        # enormous line, which is exactly what reaches here.
        #
        # `os.read` on the descriptor, not `BufferedReader.read`. Both give
        # bytes, and only one of them is safe: this thread is a daemon, and a
        # daemon parked inside a buffered read holds that buffer's lock when
        # the interpreter finalises — a clean exit turned into SIGABRT by the
        # drain alone. `os.read` also returns as soon as anything is
        # available, which is what a drain wants.
        try:
            stderr_fd = self._proc.stderr.fileno()
        except (AttributeError, OSError, ValueError):  # pragma: no cover
            stderr_fd = -1
        tail = self._stderr_tail

        def _drain(fd: int = stderr_fd, sink=tail) -> None:
            if fd < 0:
                return
            try:
                while True:
                    chunk = os.read(fd, 8192)
                    if not chunk:
                        return
                    sink.feed(chunk)
            except Exception:  # noqa: BLE001 — EOF/close ends the drain
                pass

        threading.Thread(target=_drain, name="os-kernel-stderr", daemon=True).start()

    # --- protocol ---------------------------------------------------------

    def write_line(self, line: str) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(line)
        self._proc.stdin.flush()

    def read_line(self) -> str:
        assert self._proc.stdout is not None
        return self._proc.stdout.readline()

    # --- lifecycle --------------------------------------------------------

    def alive(self) -> bool:
        return self._proc.poll() is None

    def interrupt(self) -> bool:
        """Signals are the manager's business here: it owns the sandbox that
        may need to redirect them. Reporting False keeps that decision there
        rather than duplicating the sandbox check in two places."""
        return False

    def kill(self) -> None:
        try:
            self._proc.kill()
        except (ProcessLookupError, OSError):
            pass

    def close(self, *, graceful: bool = True) -> None:
        proc = self._proc
        if graceful:
            try:
                proc.stdin and proc.stdin.write(json.dumps({"type": "shutdown"}) + "\n")
                proc.stdin and proc.stdin.flush()
            except Exception:  # noqa: BLE001
                pass
            try:
                proc.wait(timeout=3)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                    # reap, so a restart does not leak a zombie each time
                    proc.wait(timeout=2)
                except Exception:  # noqa: BLE001
                    pass
        # Close the pipe wrappers now: a dead worker's buffered stdin
        # otherwise raises BrokenPipeError at GC-time flush.
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                stream and stream.close()
            except Exception:  # noqa: BLE001
                pass

    @property
    def process(self) -> Any:
        return self._proc

    @property
    def stderr_tail(self) -> Any:
        return self._stderr_tail


class WorkerConnectionRefused(RuntimeError):
    """A worker dialled in and did not prove it was ours."""


class OutboundTcpTransport:
    """A worker that dials in to this daemon from wherever it was placed.

    Constructed around an already-accepted, already-authenticated socket:
    admission is the listener's job (see `orchestration/worker_gateway.py`),
    so this class cannot be handed an unverified peer by accident.
    """

    def __init__(
        self,
        sock: socket.socket,
        *,
        peer: str = "",
        interrupt_hook: Callable[[], bool] | None = None,
        remote_pid: int | None = None,
    ) -> None:
        self._sock = sock
        self._peer = peer
        self._interrupt_hook = interrupt_hook
        self._remote_pid = remote_pid
        self._alive = True
        self._lock = threading.Lock()
        # The reader is *binary* and the writer text. Both produce the same
        # framing as the pipe path; the asymmetry is about `MAX_LINE_BYTES`
        # meaning bytes. `TextIOWrapper.readline(size)` bounds **characters**,
        # so a text reader let a peer spend 4 bytes per character and turn a
        # 16 MiB cap into a 64 MiB allocation -- against a constant whose own
        # comment calls the unbounded case "a memory exhaustion primitive".
        # `BufferedReader.readline(size)` bounds bytes, which is what was
        # meant.
        self._reader = sock.makefile("rb")
        self._writer = sock.makefile("w", encoding="utf-8", newline="\n", buffering=1)

    def write_line(self, line: str) -> None:
        with self._lock:
            self._writer.write(line)
            self._writer.flush()

    def read_line(self) -> str:
        raw = self._reader.readline(MAX_LINE_BYTES)
        if not raw:
            self._alive = False
            return ""
        if len(raw) >= MAX_LINE_BYTES and not raw.endswith(b"\n"):
            # A peer that never sends a newline would otherwise be an
            # unbounded allocation. Treat it as a dead connection rather
            # than as a frame: a truncated frame is not a frame.
            self._alive = False
            raise WorkerConnectionRefused(
                f"remote worker {self._peer} sent a line over {MAX_LINE_BYTES} bytes"
            )
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            # U+FFFD is legal inside a JSON string, so replacement decoding
            # silently changed source code, paths and host responses while
            # still producing a valid frame.  Protocol bytes are exact.
            self._alive = False
            raise WorkerConnectionRefused(
                f"remote worker {self._peer} sent invalid UTF-8"
            ) from exc

    def alive(self) -> bool:
        """Latched, and honest about being latched.

        There is no `poll()` for a process on another machine, so this can
        only report what the last read saw. Two things make the latch usable
        rather than misleading: `SO_KEEPALIVE` on the accepted socket (see
        `orchestration/worker_gateway.py`), which turns a vanished node into
        an EOF the reader will actually reach, and the lease reclaimer, which
        ends the allocation on its own clock.
        """
        return self._alive

    def interrupt(self) -> bool:
        """Only if somebody gave us a way. A remote worker has no pid here,
        so an interrupt is the scheduler's to deliver; claiming success for
        an interrupt that was never sent would leave a cell apparently
        cancelled and actually running."""
        if self._interrupt_hook is None:
            return False
        try:
            return bool(self._interrupt_hook())
        except Exception:  # noqa: BLE001
            return False

    def kill(self) -> None:
        """Dropping the connection is what we can do from here; the resource
        itself is released by cancelling the allocation."""
        self.close(graceful=False)

    def close(self, *, graceful: bool = True) -> None:
        if graceful and self._alive:
            try:
                self.write_line(json.dumps({"type": "shutdown"}) + "\n")
            except Exception:  # noqa: BLE001
                pass
        self._alive = False
        # A makefile reader may be blocked in ``readline`` on another thread.
        # Closing that BufferedReader first can wait forever for the read to
        # return; shutting down the socket is what wakes it.  Only then is it
        # safe to close the wrappers and their shared descriptor.
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        for handle in (self._reader, self._writer):
            try:
                handle.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            self._sock.close()
        except OSError:
            pass

    @property
    def process(self) -> Any:
        """None: there is no local child. Callers that want a pid must cope
        — writing the remote pid into a column that means "a process on this
        machine" would be a lie a later reader believes."""
        return None

    @property
    def stderr_tail(self) -> Any:
        """None, and deliberately not an empty tail: this transport cannot
        see the worker's stderr, and "nothing was written" is a different
        claim from "we were not looking"."""
        return None

    @property
    def remote_pid(self) -> int | None:
        return self._remote_pid


__all__ = [
    "DEFAULT_CONNECT_TIMEOUT_S",
    "MAX_LINE_BYTES",
    "KernelTransport",
    "OutboundTcpTransport",
    "PipeTransport",
    "WorkerConnectionRefused",
]
