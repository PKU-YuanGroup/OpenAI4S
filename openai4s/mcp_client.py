"""Minimal MCP (Model Context Protocol) client — pure stdlib.

Speaks newline-delimited JSON-RPC 2.0 to a spawned MCP server process, or lazily
dispatches to the Streamable HTTP sibling transport, enough to power the
Connectors control plane: handshake (initialize + initialized), tools, resources,
and prompts. A process-wide MCPManager caches one live connection per connector
id and non-secret runtime scope so repeated calls reuse the same logical server
session without sharing a credential-bound session across Store generations.

Sampling and server-initiated requests are deliberately outside this client.
"""

from __future__ import annotations

import json
import math
import os
import queue
import select
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Mapping
from typing import Any, Callable

from openai4s.mcp_protocol import (
    MAX_FRAME_BYTES,
    OPENAI4S_PYTHON,
    MCPError,
    MCPOversizedResponse,
    MCPTimeout,
    openai4s_python_module,
)

PROTOCOL_VERSION = "2024-11-05"

#: Absolute deadline for one request. Previously there was none: `_read_reply`
#: called `readline()` in a loop, so a connector that accepted a request and
#: never answered held its caller forever -- and, because the manager takes its
#: own lock across connect, held every other connector too.
DEFAULT_TIMEOUT_S = 60.0
#: The bounds an override is held to. A deadline of zero is not "no deadline",
#: it is a connector that can never answer; one of a day is the unbounded wait
#: this constant was introduced to remove. An out-of-range or unparseable value
#: falls back to the default rather than refusing to start -- an env var should
#: not be able to make the daemon unbootable.
MIN_TIMEOUT_S = 1.0
MAX_TIMEOUT_S = 600.0
#: Published by `docs/v03-decisions.md` D6 as the single override for the MCP
#: request deadline. It named a variable nothing read: the name existed in that
#: table and nowhere else in the tree, so the decision record described a knob
#: that was not there. A documented control that does not exist is worse than an
#: undocumented one, because someone sets it and believes the deadline moved.
DEADLINE_ENV = "OPENAI4S_MCP_DEADLINE_S"

_IN_TREE_PYTHON_MODULES = frozenset(
    {
        "openai4s.mcp_servers.example_server",
        "openai4s.mcp_servers.protein_design",
    }
)


def _deadline_default() -> float:
    """The per-request deadline, from the environment, clamped.

    Read at call time rather than captured at import, so a test that sets the
    variable does not have to reload the module -- and so `DEFAULT_TIMEOUT_S`
    stays monkeypatchable, which several tests rely on.
    """
    raw = str(os.environ.get(DEADLINE_ENV) or "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_S
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_S
    if not math.isfinite(value) or value <= 0:
        return DEFAULT_TIMEOUT_S
    return min(max(value, MIN_TIMEOUT_S), MAX_TIMEOUT_S)


#: Compatibility alias retained for focused tests and the bounded stdio reader.
#: The public limit lives in ``mcp_protocol`` so sibling transports do not
#: reach into this facade's private implementation.
_MAX_FRAME_BYTES = MAX_FRAME_BYTES
#: Bounded diagnostic tail. stderr used to go to DEVNULL: no deadlock, and also
#: no way to say why a connector failed.
_STDERR_TAIL_LINES = 200
#: How many replies to ids nobody asked for before treating the channel as
#: desynchronised. Staying attached to such a server only defers the failure.
_MAX_INVALID_IDS = 64
#: Longest stderr line retained, in bytes, applied *while* the line is being
#: read. Only the line COUNT was bounded before, so one connector writing a
#: 20 MB newline-free diagnostic materialised all 20 MB inside the daemon -- and
#: `stderr_tail()` then built a second full copy before anyone truncated it.
#: Bounding at the source is the only place the large allocation never happens.
_MAX_STDERR_LINE_BYTES = 4096
#: What one read off a connector pipe may hand back: the reader's peak
#: allocation per syscall, and the granularity a dropped line is discarded at.
_READ_CHUNK_BYTES = 65_536
#: What `stderr_tail()` hands out; callers used to slice this themselves.
_STDERR_TAIL_CHARS = 500
#: Requests allowed in flight at once. A connector that answers nothing still
#: accepts work, and each caller leaves a waiter here and later an id in
#: `_abandoned` -- growth the agent drives and the server decides never to undo.
_MAX_PENDING = 256
#: Timed-out ids remembered, oldest evicted first.
_MAX_ABANDONED = 1024
#: Per-signal grace before escalating TERM -> KILL, and how long `close()` then
#: waits for each reader thread.
_TERMINATE_WAIT_S = 3.0
_READER_JOIN_S = 5.0


class _BoundedLineReader:
    """One newline-delimited line at a time, bounded in bytes that are real.

    The predecessor counted characters off a ``text=True`` pipe against a
    constant named ``_MAX_FRAME_BYTES``, which is two different problems
    wearing one number. A four-million-character line of three-byte characters
    is twelve megabytes, not four -- and it accumulated them in a ``list`` of
    four million single-character strings, each with a ``str`` object's fifty-
    odd bytes of overhead, so the peak allocation for one frame *at the limit*
    was some hundreds of megabytes. The bound was respected and meant nothing.

    Reading the pipe raw fixes both at once: the budget is bytes because bytes
    are what arrive, and they land in a ``bytearray`` that grows by the chunk
    rather than by the character. Decoding happens once, on a buffer already
    known to be within budget.

    An over-budget *frame* is dropped rather than truncated -- half a JSON
    object is not a frame, and returning it would desynchronise the next read.
    ``keep_partial`` is for stderr, where there is no frame to desynchronise
    and a truncated prefix is still the diagnostic the user needs.

    ``frames_dropped`` / ``bytes_dropped`` exist because the refusal used to be
    invisible. A dropped frame came back as ``""``, the read loop skipped it as
    it skips a blank line, and the only thing the caller ever saw was its own
    deadline expiring -- so a connector that answered promptly and too largely
    was reported, and torn down, as one that did not answer at all.
    """

    def __init__(
        self,
        stream: Any,
        *,
        limit: int = _MAX_FRAME_BYTES,
        keep_partial: bool = False,
    ) -> None:
        self._stream = stream
        self._limit = int(limit)
        self._keep_partial = bool(keep_partial)
        self._buf = bytearray()
        self.frames_dropped = 0
        self.bytes_dropped = 0

    def _finish(self, kept: bytearray, over: bool, discarded: int = 0) -> str:
        if over and not self._keep_partial:
            # The prefix goes with the rest: nothing of this frame is kept, so
            # the loss is the whole line, not the tail that did not fit.
            self.frames_dropped += 1
            self.bytes_dropped += len(kept) + discarded
            return ""
        if over:
            self.bytes_dropped += discarded
        data = bytes(kept)
        if over:
            # A budget cut lands wherever the byte count ran out, which is
            # mid-character often enough to matter. Trailing incomplete bytes
            # are dropped rather than decoded to U+FFFD, because that character
            # is itself three bytes: a line cut to ten would come back as
            # twelve, and the caller measuring the result against the budget
            # would be right to call it a violation.
            for back in range(4):
                head = data[: len(data) - back] if back else data
                try:
                    return head.decode("utf-8")
                except UnicodeDecodeError:
                    continue
        # Whole lines can still contain bytes no encoding explains; that is the
        # connector's problem to show, not the reader's to hide.
        return data.decode("utf-8", "replace")

    def readline(self) -> str | None:
        """The next line, ``""`` for one dropped as over-budget, ``None`` at EOF."""
        if self._stream is None:
            return None
        kept = bytearray()
        over = False
        discarded = 0

        def _absorb(data: bytes | bytearray) -> None:
            nonlocal over, discarded
            if over:
                discarded += len(data)
                return
            room = self._limit - len(kept)
            if len(data) > room:
                kept.extend(data[:room])
                discarded += len(data) - room
                over = True
            else:
                kept.extend(data)

        while True:
            newline = self._buf.find(b"\n")
            if newline >= 0:
                _absorb(self._buf[:newline])
                del self._buf[: newline + 1]
                return self._finish(kept, over, discarded)
            _absorb(self._buf)
            # Cleared whether or not it was absorbed: past the budget the rest
            # of this line is discarded as it arrives, which is the only way the
            # over-budget case never allocates the thing it is refusing.
            self._buf.clear()
            chunk = self._stream.read(_READ_CHUNK_BYTES)
            if not chunk:
                if not kept and not over:
                    return None
                return self._finish(kept, over, discarded)
            self._buf.extend(chunk)


def _write_all(stream: Any, payload: bytes, deadline: float | None = None) -> None:
    """Write every byte to an unbuffered pipe.

    ``bufsize=0`` makes stdin a raw ``FileIO``, whose ``write`` is one
    ``write(2)`` and may report a short count on a pipe. The text-mode writer
    this replaces looped internally; here it has to be done explicitly, or a
    long request is silently cut in half and the connector sees a broken frame.
    """
    view = memoryview(payload)
    fileno = None
    if deadline is not None:
        try:
            fileno = stream.fileno()
        except (AttributeError, OSError):  # pragma: no cover - not a real pipe
            fileno = None
    while view:
        if fileno is not None:
            # The write is the one phase of a request the deadline did not
            # cover. A connector that never drains its stdin fills the pipe
            # buffer and blocks this `write` forever -- and `_request` holds the
            # connection lock across it, so the caller hangs past its own
            # timeout and every other request to that connector queues behind
            # it. `select` bounds the wait by whatever budget is left.
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([], [fileno], [], remaining)[1]:
                raise MCPTimeout(
                    "connector did not read its stdin within the request deadline"
                )
        written = stream.write(view)
        if not written:
            raise OSError("connector stdin accepted no bytes")
        view = view[written:]
    stream.flush()


# A connector is third-party code.  Never copy the daemon's complete environment
# into it: that would silently expose provider keys, cloud credentials, and
# unrelated application secrets.  These variables are the small cross-platform
# runtime substrate needed to locate a command, create temporary files, select a
# locale, and use the host trust store.  Connector-specific credentials must be
# supplied explicitly in the persisted connector ``env`` mapping.
_CONNECTOR_RUNTIME_ENV = frozenset(
    {
        "PATH",
        "HOME",
        "USERPROFILE",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TMPDIR",
        "TMP",
        "TEMP",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "NODE_EXTRA_CA_CERTS",
    }
)


def _connector_environment(
    explicit: Mapping[str, Any] | None = None,
    *,
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a fresh least-privilege environment for one MCP subprocess.

    ``source`` exists for deterministic tests.  Explicit connector values are
    intentionally allowed to contain credentials: they are the connector's
    declared secret boundary, unlike arbitrary variables inherited by the
    daemon from the user's shell.
    """

    host = os.environ if source is None else source
    env = {
        name: str(host[name])
        for name in _CONNECTOR_RUNTIME_ENV
        if name in host and host[name] is not None
    }
    env.setdefault("PATH", os.defpath)
    env["PYTHONUNBUFFERED"] = "1"
    if explicit is None:
        return env
    if not isinstance(explicit, Mapping):
        raise MCPError("connector env must be an object")
    for raw_name, raw_value in explicit.items():
        if not isinstance(raw_name, str) or not raw_name:
            raise MCPError("connector env names must be non-empty strings")
        if "=" in raw_name or "\x00" in raw_name:
            raise MCPError(f"invalid connector env name: {raw_name!r}")
        if raw_value is None:
            raise MCPError(f"connector env value for {raw_name!r} cannot be null")
        value = str(raw_value)
        if "\x00" in value:
            raise MCPError(f"connector env value for {raw_name!r} contains NUL")
        env[raw_name] = value
    return env


class MCPConnection:
    def __init__(
        self,
        command: list[str],
        env: dict | None = None,
        cwd: str | None = None,
        *,
        timeout: float | None = None,
    ):
        self.command = command
        self._id = 0
        self._lock = threading.Lock()
        # An explicit argument still wins; the env only supplies the default.
        self._timeout = float(timeout) if timeout is not None else _deadline_default()
        #: id -> the waiter that asked for it. A dedicated reader thread routes
        #: replies here instead of every caller racing on `readline`.
        self._pending: dict[int, "queue.Queue[dict]"] = {}
        #: ids whose caller has already given up. A server may still answer a
        #: timed-out request, and without this the reply would be sitting in the
        #: pipe for the NEXT request to read as its own -- which is why adding a
        #: timeout to the old per-call `readline` would have been a correctness
        #: regression rather than a fix.
        #: A dict, not a set, because it has to be evictable in insertion
        #: order: as a set it grew by one entry per unanswered request and was
        #: pruned only when the server chose to answer late, so the connector
        #: that answers nothing -- the one case this exists for -- grew it
        #: without bound.
        self._abandoned: dict[int, None] = {}
        self._closed = threading.Event()
        self._failure: str | None = None
        #: Set by the reader thread when it refuses a frame. `_request` reads
        #: the timestamp, not the count: the question it has to answer is not
        #: "did this connector ever overflow" but "did it overflow after I
        #: sent", which is the only ordering that makes the drop *this*
        #: request's answer rather than an earlier one's.
        self._oversized_frames = 0
        self._oversized_at = 0.0
        self._stderr_tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)
        self._proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            # Piped so a failing connector can say why, and drained
            # concurrently below. A pipe nobody reads fills at 64 KB and blocks
            # the child in `write` -- the two halves of this change cannot be
            # separated.
            stderr=subprocess.PIPE,
            # Raw pipes. `text=True` put a decoder between the reader and the
            # bytes, so the byte budgets below could only ever be approximated
            # in characters -- and `bufsize=1` (line buffering) is what made
            # the old reader consume a byte at a time to avoid blocking.
            text=False,
            bufsize=0,
            env=env,
            cwd=cwd,
            # Its own session, so the child leads a process group we can signal
            # as a unit. Connectors are routinely launched through a wrapper
            # (`npx`, `uv run`, `sh -c`); signalling the leader alone left the
            # real server running and still holding the stdio pipes, so the
            # reader never saw EOF and "closed" meant "the wrapper is gone".
            # Ignored on Windows, where there is no process group to signal.
            start_new_session=True,
        )
        self._reader = threading.Thread(
            target=self._read_loop, name="mcp-reader", daemon=True
        )
        self._reader.start()
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, name="mcp-stderr", daemon=True
        )
        self._stderr_thread.start()
        try:
            self._init()
        except BaseException as exc:
            # A failed handshake used to leave the child and both reader threads
            # running with nothing referencing them: the constructor raised, so
            # no MCPConnection existed for `_conns`, `_probes`, `disconnect` or
            # `shutdown` to reach afterwards. Every "Test" click on a connector
            # that hangs, or that answers `initialize` with an error, leaked a
            # process and two threads for the life of the daemon. `close()` is
            # the same teardown a live connection gets; the only extra work is
            # carrying the stderr tail out with the error, because after the
            # close the caller has no object left to ask.
            detail = self.stderr_tail()
            self.close()
            if detail and isinstance(exc, MCPError):
                raise type(exc)(f"{exc} (connector stderr: {detail})") from None
            raise

    # -- wire ----------------------------------------------------------------
    def _send(self, obj: dict, deadline: float | None = None) -> None:
        assert self._proc.stdin is not None
        _write_all(
            self._proc.stdin,
            (json.dumps(obj) + "\n").encode("utf-8"),
            deadline=deadline,
        )

    def _drain_stderr(self) -> None:
        stream = self._proc.stderr
        if stream is None:
            return
        # One reader per stream, constructed once: it carries the residual
        # bytes of a chunk that ran past a newline, so a fresh one per line
        # would drop everything after the first.
        reader = _BoundedLineReader(
            stream, limit=_MAX_STDERR_LINE_BYTES, keep_partial=True
        )
        try:
            while True:
                line = reader.readline()
                if line is None:
                    break
                self._stderr_tail.append(line.rstrip("\n"))
        except (OSError, ValueError):
            pass

    def stderr_tail(self) -> str:
        """The end of the connector's diagnostics, already bounded.

        Callers used to be handed the whole retained blob and slice it
        themselves, so the oversized string existed before anyone decided it was
        too big. Assembled from the newest line backwards for the same reason.
        """
        parts: list[str] = []
        size = 0
        for line in reversed(self._stderr_tail):
            parts.append(line)
            size += len(line) + 1
            if size >= _STDERR_TAIL_CHARS:
                break
        parts.reverse()
        return "\n".join(parts)[-_STDERR_TAIL_CHARS:]

    def _fail_all(self, reason: str) -> None:
        """Wake every waiter once the channel can no longer answer them."""
        self._failure = reason
        self._closed.set()
        with self._lock:
            waiters = list(self._pending.items())
            self._pending.clear()
        for _mid, waiter in waiters:
            try:
                waiter.put_nowait({"__closed__": reason})
            except Exception:  # noqa: BLE001 - a waiter that already gave up
                pass

    def _read_loop(self) -> None:
        """The only reader. One thread owns the pipe; callers own their ids."""
        stream = self._proc.stdout
        reader = _BoundedLineReader(stream)
        invalid_ids = 0
        dropped = 0
        try:
            while True:
                line = reader.readline()
                if line is None:
                    break
                if reader.frames_dropped > dropped:
                    # Counted here rather than inferred from the `""` return:
                    # a blank line reads as `""` too, and calling that an
                    # oversized frame would convert an idle connector's
                    # keepalive into a refusal nobody made.
                    dropped = reader.frames_dropped
                    with self._lock:
                        self._oversized_frames = dropped
                        self._oversized_at = time.monotonic()
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue
                mid = msg.get("id")
                if mid is None:
                    continue  # a notification; nobody is waiting on it
                with self._lock:
                    waiter = self._pending.pop(mid, None)
                    if waiter is None:
                        if mid in self._abandoned:
                            # Exactly what this set exists for: discard the
                            # late answer instead of letting it be mistaken
                            # for the next request's.
                            self._abandoned.pop(mid, None)
                            continue
                        invalid_ids += 1
                if waiter is not None:
                    try:
                        waiter.put_nowait(msg)
                    except Exception:  # noqa: BLE001
                        pass
                elif invalid_ids > _MAX_INVALID_IDS:
                    # A server answering ids nobody asked for is desynchronised,
                    # and staying attached to it only defers the failure.
                    self._failure = (
                        f"MCP server answered {invalid_ids} unrequested ids; "
                        "the channel is desynchronised"
                    )
                    break
        except (OSError, ValueError):
            pass
        finally:
            self._fail_all(self._failure or "MCP server closed the connection")
            # Detaching used to be the entire response: this loop returned and
            # the child kept running, holding its pipes, while the manager went
            # on handing the same dead connection to every caller. Nothing on
            # this channel can be answered once the loop ends, so the process
            # ends with it and the next call reconnects on a new pid.
            self._terminate()

    def _request(self, method: str, params: dict | None = None) -> dict:
        deadline = time.monotonic() + self._timeout
        sent_at = 0.0
        with self._lock:
            if self._closed.is_set():
                raise MCPError(self._failure or "MCP server closed the connection")
            if len(self._pending) >= _MAX_PENDING:
                # Refusing at the door is what keeps one wedged connector from
                # becoming an unbounded, agent-driven allocation: every caller
                # that gets in leaves a waiter here and, on timeout, an id in
                # `_abandoned`, and the server has already shown it will free
                # neither.
                raise MCPError(
                    f"MCP connector has {_MAX_PENDING} requests in flight; "
                    f"refusing {method!r}"
                )
            self._id += 1
            mid = self._id
            waiter: "queue.Queue[dict]" = queue.Queue(maxsize=1)
            self._pending[mid] = waiter
            try:
                self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": mid,
                        "method": method,
                        "params": params or {},
                    },
                    deadline=deadline,
                )
            except Exception:
                self._pending.pop(mid, None)
                raise
            sent_at = time.monotonic()
        try:
            msg = waiter.get(timeout=max(0.0, deadline - time.monotonic()))
        except queue.Empty:
            with self._lock:
                self._pending.pop(mid, None)
                self._abandoned[mid] = None
                while len(self._abandoned) > _MAX_ABANDONED:
                    # Oldest first. Forgetting an id only risks mistaking a very
                    # late reply for a live one, and an id this old stopped
                    # being plausible thousands of requests ago.
                    self._abandoned.pop(next(iter(self._abandoned)), None)
                # Same bookkeeping either way -- the id is abandoned whether the
                # answer never came or came and was refused. Only the state the
                # caller is handed differs, and it differs because the two are
                # different facts about the connector.
                refused = self._oversized_at > sent_at
            if refused:
                raise MCPOversizedResponse(
                    f"MCP connector answered {method!r} with a frame larger "
                    f"than the {_MAX_FRAME_BYTES:,}-byte limit; the reply was "
                    "refused, not lost"
                ) from None
            raise MCPTimeout(
                f"MCP request {method!r} exceeded {self._timeout:g}s"
            ) from None
        if "__closed__" in msg:
            raise MCPError(str(msg["__closed__"]))
        if "error" in msg and msg["error"] is not None:
            error = msg["error"]
            detail = error.get("message") if isinstance(error, dict) else None
            raise MCPError(str(detail or error))
        return msg.get("result") or {}

    def _notify(self, method: str, params: dict | None = None) -> None:
        with self._lock:
            self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # -- lifecycle -----------------------------------------------------------
    def _init(self) -> None:
        self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "openai4s", "version": "1.0.0"},
            },
        )
        try:
            self._notify("notifications/initialized")
        except Exception:  # noqa: BLE001
            pass

    def alive(self) -> bool:
        return self._proc.poll() is None

    def faulted(self) -> bool:
        """True once this connection can never answer another request.

        `alive()` was the only health question the manager asked, and a process
        can be perfectly alive while its channel is dead -- a server that closed
        stdout, or one this client gave up on as desynchronised. The cached
        entry then answered every later call with the same stored failure, so
        one bad minute poisoned the connector id until the daemon restarted.
        """
        return self._closed.is_set() or not self.alive()

    def failure(self) -> str | None:
        """Why this connection stopped, if it has. Read by `MCPManager` to
        report children it could not kill."""
        return self._failure

    def _signal_tree(self, sig: int, fallback: Callable[[], None]) -> None:
        """Signal the child's whole process group, or failing that the child.

        Only when the child actually leads a group of its own:
        ``start_new_session`` can fail, and Windows has no ``killpg`` at all.
        Signalling a group we merely belong to would take the daemon down with
        the connector.
        """
        killpg = getattr(os, "killpg", None)
        if killpg is not None:
            try:
                if os.getpgid(self._proc.pid) == self._proc.pid:
                    killpg(self._proc.pid, sig)
                    return
            except Exception:  # noqa: BLE001 - already exited, or no groups here
                pass
        try:
            fallback()
        except Exception:  # noqa: BLE001
            pass

    def _terminate(self) -> bool:
        """TERM then KILL the child's process group, and reap it. Idempotent.

        Returns whether the child is actually gone. `terminate()` alone reached
        only the leader, so a connector launched through a wrapper left its real
        worker alive and holding the stdio pipes -- the reason a "closed"
        connection could still block this process's reader forever.
        """
        proc = self._proc
        if proc.poll() is not None:
            return True
        kill_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
        for sig, fallback in (
            (signal.SIGTERM, proc.terminate),
            (kill_signal, proc.kill),
        ):
            self._signal_tree(sig, fallback)
            try:
                # `kill()` only delivers the signal; without the wait the child
                # stays a zombie, and a connector that has to be killed is
                # exactly the one that gets closed repeatedly.
                proc.wait(timeout=_TERMINATE_WAIT_S)
                return True
            except Exception:  # noqa: BLE001 - TimeoutExpired: escalate
                continue
        if proc.poll() is None:
            self._failure = (
                f"MCP connector {self.command[0]!r} (pid {proc.pid}) "
                "survived SIGKILL"
            )
            return False
        return True

    def _join_readers(self, timeout: float) -> None:
        """Wait for the reader threads -- never for the calling one.

        `_read_loop`'s own `finally` terminates the child, so teardown has to
        tolerate being reached from inside a reader.
        """
        current = threading.current_thread()
        for thread in (self._reader, self._stderr_thread):
            if thread is not None and thread is not current:
                thread.join(timeout=timeout)

    def close(self) -> bool:
        """Stop the child and its reader threads. Returns whether it is gone.

        The order matters and is not the obvious one. Closing `stdout` first to
        unblock the reader is the natural first attempt, and it deadlocks: the
        reader holds the buffered stream's lock while parked in `read`, so this
        thread blocks acquiring that lock and never returns -- reproducibly,
        whenever a grandchild kept the write end open so no EOF ever arrived.
        Killing the process group is what actually produces the EOF, after which
        the readers exit and the streams close uneventfully. The threads were
        never joined at all before, so `close()` returned while they were still
        running against a half-dismantled connection.
        """
        self._fail_all("MCP connection closed")
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        reaped = self._terminate()
        self._join_readers(_READER_JOIN_S)
        for thread, stream in (
            (self._reader, self._proc.stdout),
            (self._stderr_thread, self._proc.stderr),
        ):
            if stream is None or (thread is not None and thread.is_alive()):
                # Still parked in `read`: see above. That thread holds the fd
                # either way, so closing here buys nothing and risks the hang.
                continue
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                pass
        return reaped

    # -- tools ---------------------------------------------------------------
    def list_tools(self) -> list[dict]:
        res = self._request("tools/list")
        return res.get("tools", []) if isinstance(res, dict) else []

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        res = self._request("tools/call", {"name": name, "arguments": arguments or {}})
        # normalize content blocks -> plain text for the agent
        text_parts = []
        for block in res.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block.get("text", ""))
        return {
            "is_error": bool(res.get("isError")),
            "text": "\n".join(text_parts),
            "raw": res,
        }

    # -- resources -----------------------------------------------------------
    def list_resources(self, cursor: str | None = None) -> dict:
        params = {"cursor": cursor} if cursor is not None else None
        res = self._request("resources/list", params)
        if not isinstance(res, dict):
            raise MCPError("resources/list returned a non-object result")
        return res

    def read_resource(self, uri: str) -> dict:
        res = self._request("resources/read", {"uri": uri})
        if not isinstance(res, dict):
            raise MCPError("resources/read returned a non-object result")
        return res

    # -- prompts -------------------------------------------------------------
    def list_prompts(self, cursor: str | None = None) -> dict:
        params = {"cursor": cursor} if cursor is not None else None
        res = self._request("prompts/list", params)
        if not isinstance(res, dict):
            raise MCPError("prompts/list returned a non-object result")
        return res

    def get_prompt(self, name: str, arguments: dict | None = None) -> dict:
        params: dict[str, Any] = {"name": name}
        if arguments is not None:
            params["arguments"] = arguments
        res = self._request("prompts/get", params)
        if not isinstance(res, dict):
            raise MCPError("prompts/get returned a non-object result")
        return res


class MCPManager:
    """One live connection per connector id and runtime cache scope."""

    def __init__(self) -> None:
        self._conns: dict[tuple[str, str], MCPConnection] = {}
        #: Live probes. They are not cached by connector id -- a probe is
        #: deliberately a fresh connection -- but they are still children this
        #: process owns, and `shutdown` has to be able to reach them.
        self._probes: set[MCPConnection] = set()
        self._lock = threading.Lock()
        #: One lock per connector id/scope, so a connect is single-flighted without
        #: the *global* lock being held across it. `self._lock` guards this
        #: dict and the two above; it is never held while a child is spawned.
        self._connect_locks: dict[tuple[str, str], threading.Lock] = {}
        #: Generations invalidate connections that were still initializing
        #: while shutdown or a connector-specific disconnect ran.  Without
        #: them an old DataPro Store's header-provider closure could finish
        #: late and write itself back into the process-wide cache.
        self._lifecycle_epoch = 0
        self._connector_epochs: dict[tuple[str, str], int] = {}

    @staticmethod
    def _cache_scope(config: dict | None) -> str:
        """Return the non-secret runtime partition carried by one config.

        The empty scope preserves the historical behavior for stdio/custom
        connectors. Managed integrations may supply an opaque Store-generation
        identity; a credential or its fingerprint must never be used here.
        """

        if not config or config.get("cache_scope") in (None, ""):
            return ""
        scope = config.get("cache_scope")
        if (
            not isinstance(scope, str)
            or len(scope) > 256
            or any(character in scope for character in ("\r", "\n", "\x00"))
        ):
            raise MCPError("connector cache scope is invalid")
        return scope

    @classmethod
    def _cache_key(cls, connector_id: str, config: dict | None) -> tuple[str, str]:
        return (str(connector_id), cls._cache_scope(config))

    @staticmethod
    def _argv(config: dict) -> list[str]:
        cmd = config.get("command")
        args = config.get("args") or []
        if isinstance(cmd, list):
            argv = list(cmd) + list(args)
        elif isinstance(cmd, str) and cmd.strip():
            argv = cmd.split() + list(args)
        else:
            raise MCPError("connector has no command")
        in_tree_module = (
            len(argv) >= 3 and argv[1] == "-m" and argv[2] in _IN_TREE_PYTHON_MODULES
        )
        if argv and (argv[0] == OPENAI4S_PYTHON or in_tree_module):
            argv[0] = sys.executable
        return argv

    def _connect(self, config: dict) -> MCPConnection:
        transport = str(config.get("transport") or "stdio").strip().lower()
        if transport in ("streamable_http", "streamable-http"):
            # Lazy to keep the default stdio import path free of networking
            # setup and to avoid a module cycle: the HTTP transport shares the
            # MCP error classes and deadline constants defined above.
            from openai4s.mcp_http import MCPHTTPConnection

            return MCPHTTPConnection(
                config.get("url"),
                headers=config.get("headers"),
                headers_provider=config.get("headers_provider"),
                timeout=(
                    _deadline_default()
                    if config.get("timeout") is None
                    else config.get("timeout")
                ),
            )
        if transport != "stdio":
            raise MCPError("unsupported MCP connector transport")
        env = _connector_environment(config.get("env"))
        return MCPConnection(self._argv(config), env=env, cwd=config.get("cwd"))

    def _evict(
        self,
        connector_id: str,
        conn: MCPConnection,
        *,
        cache_scope: str = "",
    ) -> bool:
        """Drop `conn` from the cache -- but only if it is still the cached one.

        A compare-and-swap, not a `pop` by id: between the fault and this call
        another thread may already have replaced the entry with a freshly
        connected child, and evicting by id alone would close that healthy
        newcomer, turning one bad request into a respawn loop for every caller.
        """
        cache_key = self._cache_key(connector_id, {"cache_scope": cache_scope})
        with self._lock:
            if self._conns.get(cache_key) is not conn:
                return False
            del self._conns[cache_key]
        # Outside the lock: `close()` waits on a TERM->KILL and joins two reader
        # threads, and every other connector queues behind this same lock.
        conn.close()
        return True

    def _invoke(
        self,
        connector_id: str,
        config: dict,
        call: Callable[[MCPConnection], Any],
    ) -> Any:
        """Run one operation, evicting the connection if the fault was the
        connection's.

        Nothing evicted on a fault before this: a connector whose stdout closed
        kept its cache entry, `alive()` stayed true because the process was
        still running, and every later call was handed the same stored failure.
        The connector id stayed poisoned until the daemon restarted.

        A JSON-RPC error result is deliberately not a fault. An unknown tool
        name or bad arguments must not restart the server the agent is talking
        to -- that is the connector working, not failing.
        """
        cache_scope = self._cache_scope(config)
        conn = self.get(connector_id, config)
        try:
            return call(conn)
        except MCPTimeout as exc:
            # Nothing will ever consume the reply this request may still get,
            # and a server that missed its deadline is not one the next caller
            # should inherit mid-conversation.
            if self._evict(connector_id, conn, cache_scope=cache_scope):
                raise MCPTimeout(
                    f"{exc}; connector dropped, the next call reconnects"
                ) from None
            raise
        except MCPError:
            # `MCPOversizedResponse` arrives here, and deliberately has no
            # clause of its own. What decides eviction is the connection's
            # state, not the size of the reply the daemon refused: a connector
            # still serving is kept -- tearing it down would turn one refused
            # answer into a respawn, the exact mistake `_evict`'s docstring is
            # about -- and one that emitted the frame and then exited is
            # dropped, which a dedicated `raise` clause would have prevented.
            # That is the whole reason the class is not a `MCPTimeout`.
            if conn.faulted():
                self._evict(connector_id, conn, cache_scope=cache_scope)
            raise

    def _connect_lock(
        self, connector_id: str, *, cache_scope: str = ""
    ) -> threading.Lock:
        cache_key = self._cache_key(connector_id, {"cache_scope": cache_scope})
        with self._lock:
            lock = self._connect_locks.get(cache_key)
            if lock is None:
                lock = threading.Lock()
                self._connect_locks[cache_key] = lock
            return lock

    def get(self, connector_id: str, config: dict) -> MCPConnection:
        """The live connection for one connector, connecting it if needed.

        `_connect` spawns a child and runs the `initialize` handshake, which is
        bounded by the connector's own timeout and can therefore take seconds.
        It used to happen inside `self._lock` -- the one lock every connector's
        `get`, `_evict`, `disconnect` and `shutdown` need -- so one slow or
        hanging server stalled every other connector in the process, including
        ones that were already connected and merely being looked up.

        Single-flighted per connector and runtime scope instead: two callers
        asking for the same pair still produce one connection, because the
        second waits on that pair's lock and adopts what the first cached.
        Callers asking for a different id or scope do not wait on each other at
        all. The global lock is taken only for dict reads and writes, and is
        never held across a spawn.
        """
        cache_scope = self._cache_scope(config)
        cache_key = self._cache_key(connector_id, config)
        stale: MCPConnection | None = None
        with self._lock:
            conn = self._conns.get(cache_key)
            if conn is not None:
                if not conn.faulted():
                    return conn
                del self._conns[cache_key]
                stale = conn
        if stale is not None:
            # Outside the lock, like `_evict`: `close()` waits on a TERM->KILL
            # and joins two reader threads.
            stale.close()
        with self._connect_lock(connector_id, cache_scope=cache_scope):
            # Re-checked under the per-connector/scope lock: another caller may
            # have connected while this one waited, and adopting it is the
            # whole point of single-flighting.
            with self._lock:
                conn = self._conns.get(cache_key)
                if conn is not None and not conn.faulted():
                    return conn
                lifecycle_epoch = self._lifecycle_epoch
                connector_epoch = self._connector_epochs.get(cache_key, 0)
            made = self._connect(config)
            with self._lock:
                valid = (
                    lifecycle_epoch == self._lifecycle_epoch
                    and connector_epoch == self._connector_epochs.get(cache_key, 0)
                )
                if valid:
                    self._conns[cache_key] = made
            if not valid:
                # A new Store generation or a credential/config edit won the
                # race while this connection initialized.  It must never be
                # published after that boundary, even if a new generation has
                # already cached its own connection under the same id.
                made.close()
                raise MCPError(
                    "MCP connector initialization was invalidated by disconnect or "
                    "shutdown"
                )
            return made

    def probe(self, config: dict) -> dict:
        """Connect fresh, list tools, close. Returns {ok, tools|error}."""
        try:
            conn = self._connect(config)
        except Exception as e:  # noqa: BLE001
            # No `conn` to read a stderr tail off any more: a failed handshake
            # now closes its own process, so it carries the tail out in the
            # error instead of leaving one running for this line to interrogate.
            return {"ok": False, "error": str(e)}
        # Registered while it lives, so `shutdown` can reach it. A probe used
        # to connect without registering anywhere: if it hung, its `finally`
        # never ran and the orphaned connector process was invisible to both
        # `disconnect` and `shutdown` forever -- one leaked child per hung
        # "Test" click, unreapable.
        with self._lock:
            self._probes.add(conn)
        try:
            tools = conn.list_tools()
            return {"ok": True, "tools": tools}
        except Exception as e:  # noqa: BLE001
            detail = str(e)
            tail = conn.stderr_tail()
            if tail:
                detail = f"{detail} (connector stderr: {tail})"
            return {"ok": False, "error": detail}
        finally:
            with self._lock:
                self._probes.discard(conn)
            conn.close()

    def list_tools(self, connector_id: str, config: dict) -> list[dict]:
        return self._invoke(connector_id, config, lambda c: c.list_tools())

    def call_tool(
        self, connector_id: str, config: dict, tool: str, arguments: dict | None = None
    ) -> dict:
        return self._invoke(
            connector_id, config, lambda c: c.call_tool(tool, arguments)
        )

    def list_resources(
        self,
        connector_id: str,
        config: dict,
        cursor: str | None = None,
    ) -> dict:
        return self._invoke(connector_id, config, lambda c: c.list_resources(cursor))

    def read_resource(self, connector_id: str, config: dict, uri: str) -> dict:
        return self._invoke(connector_id, config, lambda c: c.read_resource(uri))

    def list_prompts(
        self,
        connector_id: str,
        config: dict,
        cursor: str | None = None,
    ) -> dict:
        return self._invoke(connector_id, config, lambda c: c.list_prompts(cursor))

    def get_prompt(
        self,
        connector_id: str,
        config: dict,
        name: str,
        arguments: dict | None = None,
    ) -> dict:
        return self._invoke(
            connector_id, config, lambda c: c.get_prompt(name, arguments)
        )

    def disconnect(self, connector_id: str, cache_scope: str | None = None) -> None:
        """Drop one connector's cached session(s).

        ``cache_scope=None`` retains the legacy one-argument behavior and
        invalidates every runtime scope for this connector. Passing a scope
        invalidates only that Store generation, so its credential rotation
        cannot interrupt another concurrently hosted Store.
        """

        connector_id = str(connector_id)
        with self._lock:
            if cache_scope is None:
                keys = {
                    key
                    for mapping in (
                        self._conns,
                        self._connect_locks,
                        self._connector_epochs,
                    )
                    for key in mapping
                    if key[0] == connector_id
                }
                # Preserve the old unscoped epoch even when there is no live
                # entry. This also makes a one-argument disconnect before the
                # first custom-connector call deterministic.
                keys.add((connector_id, ""))
            else:
                keys = {
                    self._cache_key(
                        connector_id,
                        {"cache_scope": cache_scope},
                    )
                }
            conns = []
            for cache_key in keys:
                self._connector_epochs[cache_key] = (
                    self._connector_epochs.get(cache_key, 0) + 1
                )
                conn = self._conns.pop(cache_key, None)
                if conn is not None:
                    conns.append(conn)
        for conn in conns:
            conn.close()

    def shutdown(self) -> list[str]:
        """Close every child. Returns a line per child that would not die.

        Teardown used to report success unconditionally, so a connector that
        ignored SIGKILL was indistinguishable from one that exited -- and the
        next `serve` started against orphans still holding whatever they held.
        """
        with self._lock:
            self._lifecycle_epoch += 1
            conns = list(self._conns.values()) + list(self._probes)
            self._conns.clear()
            self._probes.clear()
            # A per-connector lock outlives its connection otherwise, and a
            # long-lived daemon accumulates one per id it ever spoke to.
            self._connect_locks.clear()
            self._connector_epochs.clear()
        survivors: list[str] = []
        for c in conns:
            if not c.close():
                survivors.append(c.failure() or f"{c.command[0]!r} survived close()")
        return survivors


# a process-wide manager (the daemon is single-process)
_MANAGER: MCPManager | None = None


def manager() -> MCPManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = MCPManager()
    return _MANAGER


def disconnect_if_initialized(
    connector_id: str, *, cache_scope: str | None = None
) -> bool:
    """Disconnect from the process manager without creating one.

    Store teardown uses this narrow hook: most Stores never touched MCP, so
    closing one must not instantiate process-wide connector state merely to
    discover that there is nothing to release.  ``True`` means an existing
    manager received the scoped invalidation; it does not imply a connection
    had already been cached.
    """

    current = _MANAGER
    if current is None:
        return False
    current.disconnect(connector_id, cache_scope=cache_scope)
    return True


def example_server_config() -> dict:
    """Config for the bundled example server (always available)."""
    return {"command": [sys.executable, "-m", "openai4s.mcp_servers.example_server"]}


def protein_design_server_config(root: str | None = None) -> dict:
    """Config for the bundled atomic protein-design server.

    Model environments and immutable revision/checkpoint settings remain
    explicit connector configuration; importing this helper starts nothing.
    """
    config = {
        "command": [
            sys.executable,
            "-m",
            "openai4s.mcp_servers.protein_design",
        ]
    }
    if root is not None:
        config["env"] = {"OPENAI4S_PROTEIN_DESIGN_ROOT": str(root)}
    return config
