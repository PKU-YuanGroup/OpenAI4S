#!/usr/bin/env python3
"""Persistent Python kernel worker for openai4s.

Implements the hard parts of a robust in-process kernel protocol:

 dup2 fd swap....... the REAL protocol stdin/stdout are moved to high,
 non-inheritable fds and PUBLISHED on sys._openai4s_protocol_stdin/stdout;
 fd 1 (stdout) is aliased to stderr (dup2(2,1)) so any raw C-level write or
 stray print lands in stderr, never on the protocol wire. Consumers
 RE-RESOLVE the published handles on every call (never cache) so a crash
 recovery that republishes is seen by everyone.
 two locks......... _PROTOCOL_WRITE_LOCK (held only while writing a frame,
 shared by worker responses + SDK host_calls) and _HOST_CALL_LOCK (held for
 a whole host_call request/response transaction so only one RPC is in flight
 and the readline that returns is provably ours).
 15MB wire cap + bounded (8) discard desync guard.
 SIGINT discipline. one-shot self-clearing handler, _in_user_code gating,
 _sigint_delivered distinguishes a DELIVERED signal (interrupted=True,
 lineno=None) from a user `raise KeyboardInterrupt` (normal error w/ lineno).
 crash recovery.... before AND after each blocking protocol read we verify
 the fd's (st_dev, st_ino) still matches the identity recorded at startup;
 on mismatch we get ONE os.dup(reserve) rebuild budget; stale wrappers are
 destroyed only if provably still ours (ino match), else PARKED.

Protocol (JSON-per-line):
 protocol IN (host -> worker): execute requests AND host_response frames
 protocol OUT (worker -> host): host_call / stdout_chunk / final response frames
"""

from __future__ import annotations

import hashlib
import io
import json
import linecache
import math
import os
import resource
import signal
import sys
import threading
import time
import traceback
from functools import partial

# A remote source-checkout worker is launched by its absolute script path with
# a deliberately sparse environment.  Python then puts `openai4s/kernel`, not
# the repository root, on `sys.path`, so mandatory audit-hook and Host imports
# fail before the first Cell.  Derive the package parent from the trusted
# executable path itself; never rely on an inherited, attacker-selectable
# PYTHONPATH to make the worker importable.
_TRUSTED_PACKAGE_PARENT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
)
if (
    os.path.isfile(os.path.join(_TRUSTED_PACKAGE_PARENT, "openai4s", "__init__.py"))
    and _TRUSTED_PACKAGE_PARENT not in sys.path
):
    sys.path.insert(0, _TRUSTED_PACKAGE_PARENT)

from openai4s.kernel.protocol import (  # noqa: E402 - trusted path fixed above
    JSON_WORST_BYTES_PER_CHAR as _JSON_WORST_BYTES_PER_CHAR,
)
from openai4s.kernel.protocol import (  # noqa: E402 - trusted path fixed above
    MAX_FRAME_BYTES as _MAX_FRAME_BYTES,
)
from openai4s.kernel.protocol import (  # noqa: E402 - trusted path fixed above
    MAX_OUTPUT_CHARS as MAX_OUTPUT,
)

_DISCARD_BUDGET = 8  # bounded discard for desync
_HOST_CALL_WIRE_CAP = 15_000_000  # 15MB host_call payload cap
#: A Cell can open an unbounded number of paths.  ``files_read`` is evidence
#: metadata carried in the one response frame, so bound both its cardinality
#: and each retained spelling at the producer.  Omitting later observations is
#: conservative (it cannot invent a lineage edge); retaining an unbounded list
#: would let ordinary user code grow the protocol frame without limit.
_MAX_FILES_READ = 256
_MAX_FILE_READ_PATH_CHARS = 1_024
#: One streamed chunk. A `write()` used to become one frame of whatever size it
#: was handed, so `print("x" * 200_000_000)` put a single ~200MB JSON line on
#: the pipe and the host's `readline()` materialised it whole -- ~200MB
#: allocated on both sides at once, from one ordinary statement.
_MAX_CHUNK_CHARS = 64_000
#: Hard backstop for every outbound frame, whatever its type. The inbound
#: direction has had `_HOST_CALL_WIRE_CAP` all along; this side had nothing.
#:
#: Derived, not chosen. It was a flat 8_000_000 with a comment claiming it sat
#: "above the largest legitimate frame (a response carries stdout and stderr,
#: each capped at MAX_OUTPUT)" -- true only for ASCII. MAX_OUTPUT counts
#: CHARACTERS and this counts BYTES, and one character is up to 4 bytes in
#: UTF-8 and up to 6 in JSON's `\uXXXX` escape. Measured: both streams filled
#: to the cap with CJK text, or with control characters, serialise to
#: 12,000,059 bytes -- so a cell whose output obeyed every documented limit had
#: its whole frame replaced by a drop note, taking stderr, the exception text,
#: `error_lineno`, `guards` and `usage` with it. Only stdout survived, and only
#: because the manager backfills it from the streamed chunks.
#:
#: Twelve, not six. `\uXXXX` is six bytes and that is what a CJK character or a
#: control character costs -- but Python counts an astral character (an emoji,
#: say) as ONE character while JSON must emit it as a surrogate pair,
#: `\ud83d\ude00`, which is twelve. Six was the first value here and the test
#: below caught it: `MAX_OUTPUT` characters of emoji is 12 MB per stream, and a
#: cap derived from six would have gone on dropping exactly the frames this
#: change exists to stop dropping.
#:
#: It still bounds the allocation the backstop is for: a
#: `print("x" * 200_000_000)` is stopped just the same.
#: One spelling of the marker, so the streamed tail and the captured result
#: cannot disagree about what happened.
_TRUNCATION_MARKER = f"\n...(truncated at {MAX_OUTPUT} characters)"
_MAX_CACHED_CELLS = 128  # linecache retention, evicted by counter

# --- protocol channel setup (dup2 swap + publish) ---------------------


#: Where a remote worker finds the daemon, and the file holding its proof.
#: Both are plain paths/addresses on purpose: the credential itself travels
#: as a 0600 FILE and only its path is given to the scheduler, so nothing
#: secret ever appears in a job's environment (INV-9).
_CONNECT_ENV = "OPENAI4S_WORKER_CONNECT"
_CREDENTIAL_ENV = "OPENAI4S_WORKER_BOOTSTRAP_PATH"
_CREDENTIAL_TEMPLATE_ENV = "OPENAI4S_WORKER_BOOTSTRAP_PATH_TEMPLATE"
_RANK_ENV_NAME_ENV = "OPENAI4S_WORKER_RANK_ENV"


def _remote_credential_path() -> str:
    template = (os.environ.get(_CREDENTIAL_TEMPLATE_ENV) or "").strip()
    if not template:
        return (os.environ.get(_CREDENTIAL_ENV) or "").strip()
    rank_env = (os.environ.get(_RANK_ENV_NAME_ENV) or "").strip()
    if not rank_env:
        raise ValueError(
            f"{_CREDENTIAL_TEMPLATE_ENV} is set but {_RANK_ENV_NAME_ENV} is not"
        )
    raw_rank = (os.environ.get(rank_env) or "").strip()
    try:
        rank = int(raw_rank)
    except ValueError as exc:
        raise ValueError(f"invalid worker rank in {rank_env}: {raw_rank!r}") from exc
    if rank < 0 or "{rank}" not in template:
        raise ValueError("worker credential template/rank is invalid")
    return template.replace("{rank}", str(rank))


def _connect_remote_protocol():
    """Dial the daemon and authenticate; return (in_fd, out_fd) or None.

    None is the ordinary case — a local worker inherits its pipes and this
    whole path is skipped. When the variables ARE set, failure exits rather
    than falling back: a worker that cannot prove who it is must not go on
    to run cells, and a silent fallback to inherited fds on a compute node
    would be a worker talking to nothing.
    """
    target = (os.environ.get(_CONNECT_ENV) or "").strip()
    if not target:
        return None
    import json as _json
    import socket as _socket

    try:
        credential_path = _remote_credential_path()
        if not credential_path:
            raise ValueError(
                f"{_CONNECT_ENV} is set but {_CREDENTIAL_ENV} is not; refusing "
                "to connect without a credential"
            )
        with open(credential_path, encoding="utf-8") as handle:
            credential = handle.read().strip()
        host, _, port = target.rpartition(":")
        sock = _socket.create_connection((host or "127.0.0.1", int(port)), timeout=60)
        sock.sendall((credential + "\n").encode("utf-8"))
        # One byte at a time, deliberately. A chunked read consumes whatever
        # arrived in the same segment as the handshake reply, and the previous
        # version then threw the remainder away -- so a protocol frame the
        # daemon wrote immediately afterwards (registration wakes a thread
        # that builds the Kernel and executes at once, so "immediately" is the
        # normal case) was lost before the reader wrapper existed to see it,
        # and the daemon blocked forever on a reply to a cell the worker never
        # received. The reply is one short line; the syscalls are cheap and
        # happen once.
        reply = b""
        while not reply.endswith(b"\n"):
            chunk = sock.recv(1)
            if not chunk:
                raise OSError("daemon closed during handshake")
            reply += chunk
            if len(reply) > 65536:
                raise OSError("handshake reply too long")
        answer = _json.loads(reply.rstrip(b"\n").decode("utf-8"))
        if not answer.get("ok"):
            raise OSError(f"daemon refused this worker: {answer.get('error')}")
        # The generation the Host admitted this connection under. A local
        # worker learns it from `OPENAI4S_KERNEL_GENERATION` in its
        # environment; a remote one has no such environment, so the
        # handshake response is where it arrives. Published on `sys` for
        # `main()` to hand to the Host it builds -- before the Host and its
        # BashExecutor exist, so the executor is constructed knowing it
        # rather than guessing `worker:<pid>` and being refused.
        generation = answer.get("generation")
        if generation:
            sys._openai4s_remote_generation = str(  # type: ignore[attr-defined]
                generation
            )
    except Exception as exc:  # noqa: BLE001
        print(f"worker bootstrap failed: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(70) from exc
    sock.settimeout(None)
    # Two independent descriptors over the one connection, so the reader and
    # the writer can be wrapped separately exactly as the pipe path wraps
    # its two pipes.
    in_fd = os.dup(sock.fileno())
    out_fd = os.dup(sock.fileno())
    sock.detach()
    return in_fd, out_fd


def _setup_protocol_channels() -> None:
    """Move the real protocol streams to high fds and alias fd1->stderr.

    After this runs, sys.stdout writes (fd 1) go to STDERR; the true protocol
    channels live on non-inheritable high fds, wrapped and published on
    sys._openai4s_protocol_stdin / sys._openai4s_protocol_stdout. A reserve dup of
    the input fd is stashed on sys._openai4s_proto_in_reserve for recovery.
    """
    remote = _connect_remote_protocol()
    if remote is not None:
        # A worker placed on a compute node has no inherited protocol pipes:
        # it dialled the daemon, and both directions ride that one socket.
        # Everything downstream — _readline_protocol, _write_frame, the
        # host_call transaction — is unchanged, because what they consume is
        # the pair of file objects published below, not their provenance.
        proto_in_fd, proto_out_fd = remote
        reserve_fd = -1
    else:
        # Duplicate the inherited protocol fds to fresh (high) fds.
        proto_in_fd = os.dup(0)
        proto_out_fd = os.dup(1)
        reserve_fd = os.dup(0)  # spare for one-shot recovery
    for fd in (proto_in_fd, proto_out_fd, reserve_fd):
        if fd < 0:
            continue
        try:
            os.set_inheritable(fd, False)
        except OSError:
            pass

    # Alias fd 1 -> fd 2: stray writes to stdout now hit stderr, never the wire.
    try:
        os.dup2(2, 1)
    except OSError:
        pass

    proto_in = os.fdopen(proto_in_fd, "r", buffering=1, encoding="utf-8", newline="\n")
    proto_out = os.fdopen(
        proto_out_fd, "w", buffering=1, encoding="utf-8", newline="\n"
    )

    # PUBLISH on sys — consumers re-resolve these every call (never cache).
    sys._openai4s_protocol_stdin = proto_in  # type: ignore[attr-defined]
    sys._openai4s_protocol_stdout = proto_out  # type: ignore[attr-defined]
    sys._openai4s_proto_in_reserve = reserve_fd  # type: ignore[attr-defined]
    sys._openai4s_protocol_ident = _fd_ident(proto_in_fd)  # type: ignore[attr-defined]
    sys._openai4s_parked_wrappers = []  # type: ignore[attr-defined]

    # Shared locks, published so every SDK fragment grabs the SAME singletons.
    sys._openai4s_protocol_lock = threading.Lock()  # type: ignore[attr-defined]
    sys._openai4s_host_call_lock = threading.Lock()  # type: ignore[attr-defined]


def _fd_ident(fd: int) -> tuple[int, int]:
    st = os.fstat(fd)
    return (st.st_dev, st.st_ino)


def _proto_out():
    return sys._openai4s_protocol_stdout  # type: ignore[attr-defined]


def _proto_in():
    return sys._openai4s_protocol_stdin  # type: ignore[attr-defined]


def _write_lock() -> threading.Lock:
    return sys._openai4s_protocol_lock  # type: ignore[attr-defined]


def _host_call_lock() -> threading.Lock:
    return sys._openai4s_host_call_lock  # type: ignore[attr-defined]


# --- protocol stream identity + one-shot recovery -------------------


def _recover_protocol_in() -> None:
    """One-shot rebuild of the protocol IN wrapper from the reserve fd.

    A user `os.close(N)` / fd-scan / reassignment can recycle the protocol fd,
    which would make readline block forever on someone else's file. We get ONE
    rebuild from the reserve dup. The stale wrapper is destroyed only if it is
    PROVABLY still our pipe (ino matches); otherwise it is PARKED (never closed)
    so CPython refcount finalization can't slam an fd now owned by user code.
    """
    reserve = getattr(sys, "_openai4s_proto_in_reserve", None)
    # `< 0` as well as None. The remote branch publishes -1 as its "no
    # reserve" sentinel (the fd-closing loop above already tests `fd < 0`
    # for the same value), and testing only `is None` let it through to
    # `os.dup(-1)` -- a bare EBADF out of the read loop instead of this
    # deliberate message, after the live wrapper had already been parked,
    # and with no stderr tail on a remote worker to explain it.
    if reserve is None or reserve < 0:
        raise RuntimeError("protocol IN corrupted and no reserve fd to recover")
    old = getattr(sys, "_openai4s_protocol_stdin", None)
    ident = getattr(sys, "_openai4s_protocol_ident", None)
    # decide destroy-vs-park for the old wrapper
    if old is not None:
        try:
            if ident is not None and _fd_ident(old.fileno()) == ident:
                old.close()  # provably ours -> safe to close
            else:
                sys._openai4s_parked_wrappers.append(old)  # type: ignore[attr-defined]
        except (OSError, ValueError):
            sys._openai4s_parked_wrappers.append(old)  # type: ignore[attr-defined]
    new_fd = os.dup(reserve)
    try:
        os.set_inheritable(new_fd, False)
    except OSError:
        pass
    sys._openai4s_protocol_stdin = os.fdopen(  # type: ignore[attr-defined]
        new_fd, "r", buffering=1, encoding="utf-8", newline="\n"
    )
    sys._openai4s_protocol_ident = _fd_ident(new_fd)  # type: ignore[attr-defined]
    # spend the recovery budget: no reserve remains after one use
    sys._openai4s_proto_in_reserve = None  # type: ignore[attr-defined]


def _readline_protocol() -> str:
    """Blocking read of one protocol line, with identity checks."""
    ident = getattr(sys, "_openai4s_protocol_ident", None)
    stream = _proto_in()
    # read-BEFORE identity check: a recycled fd must not send us into a
    # permanent block on an unrelated file/socket.
    if ident is not None:
        try:
            if _fd_ident(stream.fileno()) != ident:
                _recover_protocol_in()
                stream = _proto_in()
        except (OSError, ValueError):
            _recover_protocol_in()
            stream = _proto_in()
    line = stream.readline()
    # read-AFTER identity check: an fd recycle DURING the block can hand back a
    # stale wrapper's bytes as if legitimate.
    ident2 = getattr(sys, "_openai4s_protocol_ident", None)
    if ident2 is not None:
        try:
            if _fd_ident(stream.fileno()) != ident2:
                _recover_protocol_in()
        except (OSError, ValueError):
            _recover_protocol_in()
    return line


def _write_frame(obj: dict) -> None:
    line = json.dumps(obj, ensure_ascii=False) + "\n"
    if len(line.encode("utf-8", "replace")) > _MAX_FRAME_BYTES:
        # Never put a frame on the wire that the host would have to
        # materialise whole. Replaced rather than truncated: a truncated JSON
        # line is not a frame at all, and the reader would desynchronise on it.
        note = (
            f"kernel dropped an oversized {obj.get('type', 'unknown')!r} "
            f"frame (>{_MAX_FRAME_BYTES} bytes)"
        )
        # A dropped `response` leaves the host waiting for an id that will
        # never arrive -- `Kernel.execute` blocks until the watchdog kills the
        # worker, which reads to the user as a hang rather than as a refusal.
        # So the replacement keeps the contract: same type, same id, no
        # payload, and an error that says what happened. Capping the fields
        # above is what stops this being reached; this is what stops the next
        # unbounded field being a hang instead of a message.
        if obj.get("type") == "response" and obj.get("id"):
            replacement: dict = {
                "type": "response",
                "id": obj.get("id"),
                "stdout": "",
                "stderr": "",
                "error": note,
                "interrupted": bool(obj.get("interrupted")),
            }
        else:
            replacement = {"type": "log", "msg": note}
        line = json.dumps(replacement, ensure_ascii=False) + "\n"
    # A frame is written and flushed as one thing, or the host reads a line
    # that is not a frame. `out.write(line)` fills a buffer and `out.flush()`
    # is what puts it on the wire, so a KeyboardInterrupt raised between them
    # leaves a partial line behind -- and the next flush concatenates it with
    # whatever frame follows. `Kernel._readline` hands that to `json.loads`,
    # so an interrupt the worker handled correctly reaches the caller as a
    # JSONDecodeError from a desynchronised stream instead. A cell's stdout
    # goes out through exactly this path, which is where a stop lands.
    #
    # So the signal is deferred across the write and raised the moment it is
    # done. Deferring, not masking: the interrupt is owed and paid microseconds
    # later, still inside user code, so the cell ends with interrupted=True.
    deferred = _in_user_code[0]
    if deferred:
        _in_user_code[0] = False
    try:
        with _write_lock():
            out = _proto_out()
            out.write(line)
            out.flush()
    finally:
        if deferred:
            _in_user_code[0] = True
    if deferred:
        _raise_if_sigint_pending()


# --- resource accounting -------------------------------------------


def _cpu_seconds() -> float:
    s = resource.getrusage(resource.RUSAGE_SELF)
    c = resource.getrusage(resource.RUSAGE_CHILDREN)
    return s.ru_utime + s.ru_stime + c.ru_utime + c.ru_stime


def _reset_peak_rss() -> None:
    try:
        with open("/proc/self/clear_refs", "w") as f:
            f.write("5")
    except OSError:
        pass  # non-Linux / not permitted; best-effort


def _peak_rss_kb() -> int:
    try:
        with open("/proc/self/status") as f:
            for row in f:
                if row.startswith("VmHWM:"):
                    return int(row.split()[1])
    except OSError:
        pass
    ru = resource.getrusage(resource.RUSAGE_SELF)
    rss = ru.ru_maxrss
    return rss // 1024 if sys.platform == "darwin" else rss


# --- synchronous host RPC ---------------------------------

_HOST_CALL_SEQ = 0
_ACTIVE_CELL_ID: list[str | None] = [None]
_ACTIVE_CELL_ORIGIN = [""]
# The audit hook installed at worker initialization owns the observer.  A
# Cell only opens/closes this narrow collection window around its actual
# eval/exec.  ``tag`` is the compiled Cell filename and is also the caller-stack
# proof that an ``open`` event belongs to synchronous user execution rather
# than to worker housekeeping or a persistent background thread.
_FILE_READ_STATE: dict[str, object] = {
    "tag": None,
    "paths": [],
    "seen": set(),
}


def _begin_file_read_observation(tag: str) -> None:
    _FILE_READ_STATE["tag"] = tag
    _FILE_READ_STATE["paths"] = []
    _FILE_READ_STATE["seen"] = set()


def _finish_file_read_observation() -> list[str]:
    paths = _FILE_READ_STATE.get("paths")
    _FILE_READ_STATE["tag"] = None
    _FILE_READ_STATE["paths"] = []
    _FILE_READ_STATE["seen"] = set()
    if type(paths) is not list:
        return []
    return [value for value in paths if type(value) is str]


def _attach_cell_context(method: str, args: list) -> list:
    """Add worker-owned cell identity to cell-scoped host calls.

    The model may still pass an explicit ``producing_cell_id`` for backwards
    compatibility. The hidden execution id remains authoritative for capture
    identity, while the public value is preserved on the wire. Keep both on
    the existing argument rather than adding another frame reader or protocol
    message type.
    """
    cell_id = _ACTIVE_CELL_ID[0]
    # `materialise_artifact` too. It writes a version like `save_artifact` does,
    # and without the cell identity that version carries `producing_cell_id`
    # NULL -- which is the exact column the end-of-cell capture matches on, so
    # the capture could never reuse it and made a second version of the same
    # bytes. The lineage edge stayed on the first, leaving the artifact head
    # with no inputs.
    if method not in ("save_artifact", "materialise_artifact") or not cell_id:
        return args
    if not args:
        return args
    spec = args[0]
    if not isinstance(spec, dict):
        return args
    enriched = dict(spec)
    enriched["executionCellId"] = cell_id
    if "producingCellId" not in spec and "producing_cell_id" not in spec:
        enriched["producingCellId"] = cell_id
    return [enriched, *args[1:]]


def host_call(method: str, args: list) -> object:
    """Synchronous RPC to the host, usable mid-execution.

    Holds _HOST_CALL_LOCK for the whole transaction (only one RPC in flight),
    _PROTOCOL_WRITE_LOCK only while writing. Bounded-discard on id-mismatch.
    """
    global _HOST_CALL_SEQ
    _HOST_CALL_SEQ += 1
    call_id = f"hc-{int(time.time())}-{_HOST_CALL_SEQ}"
    args = _attach_cell_context(method, args)
    payload = json.dumps(
        {"type": "host_call", "id": call_id, "method": method, "args": args},
        ensure_ascii=False,
    )
    nbytes = len(payload.encode("utf-8"))
    if nbytes > _HOST_CALL_WIRE_CAP:
        raise ValueError(
            f"host call '{method}' payload is {nbytes} bytes, exceeding the "
            f"15MB wire cap (the host rejects oversized frames)"
        )

    with _host_call_lock():
        with _write_lock():
            out = _proto_out()
            out.write(payload + "\n")
            out.flush()

        discarded = 0
        while True:
            line = _readline_protocol()
            if not line:
                raise RuntimeError("host channel closed during host_call")
            try:
                resp = json.loads(line)
            except json.JSONDecodeError:
                discarded += 1
                if discarded > _DISCARD_BUDGET:
                    raise RuntimeError(
                        f"host.{method}: protocol desync, too many "
                        f"out-of-order frames"
                    )
                continue
            if not isinstance(resp, dict) or resp.get("id") != call_id:
                discarded += 1
                if discarded > _DISCARD_BUDGET:
                    raise RuntimeError(
                        f"host.{method}: protocol desync, too many "
                        f"out-of-order frames"
                    )
                continue
            if resp.get("type") == "host_ack":
                continue  # ack is a pre-response; keep waiting for the real one
            if "error" in resp and resp["error"] is not None:
                raise RuntimeError(f"host.{method} error: {resp['error']}")
            return resp.get("data")


# --- cell bookkeeping ------------------------------------------------------

_NS: dict = {"__name__": "__openai4s__", "__builtins__": __builtins__}
_CELL_SEQ = 0
_LIVE_TAGS: list[str] = []
_SKILL_LOAD_EVENT_STATE: list[object] = [None, 0]
_SKILL_LOAD_PROTOCOL_FRAMES = [0]


def _publish_skill_sidecar_event(
    event: object,
    _emit=_write_frame,
    _cell_state=_ACTIVE_CELL_ID,
    _frame_count=_SKILL_LOAD_PROTOCOL_FRAMES,
) -> None:
    """Publish one audit-observed import as a private diagnostic.

    The CPython audit hook calls this sink only when the caller's code object is
    the loader registered by a system/recovery bootstrap Cell. This is not an
    attestation boundary: arbitrary Python in the same interpreter can recover
    the sink/signing objects, so the manager and recorder fail closed on it.
    """

    payload = dict(event) if type(event) is dict else {}
    event_name = payload.get("event")
    attestation_id = payload.get("attestation_id")
    attestation_mac = payload.get("attestation_mac")
    if (
        type(attestation_mac) is not str
        or len(attestation_mac) != 64
        or any(char not in "0123456789abcdef" for char in attestation_mac)
    ):
        payload = {"event": "invalid_sidecar_event"}
        event_name = "invalid_sidecar_event"
        attestation_id = ""
    if event_name == "sidecar_capture_started":
        source_sha256 = payload.get("sha256")
        if (
            type(attestation_id) is not str
            or not attestation_id
            or type(source_sha256) is not str
            or len(source_sha256) != 64
            or any(char not in "0123456789abcdef" for char in source_sha256)
        ):
            payload = {"event": "invalid_sidecar_event"}
    elif event_name == "invalid_sidecar_event":
        payload = {
            "event": "invalid_sidecar_event",
            "attestation_id": (attestation_id if type(attestation_id) is str else ""),
            **(
                {"attestation_mac": attestation_mac}
                if type(attestation_mac) is str
                else {}
            ),
        }
    else:
        source_b64 = payload.get("source_b64")
        if (
            type(attestation_id) is not str
            or not attestation_id
            or type(source_b64) is not str
            or len(source_b64) > 2_666_672
        ):
            payload = {"event": "invalid_sidecar_event"}
    _emit(
        {
            "type": "skill_sidecar_load",
            "id": _cell_state[0],
            "event": payload,
        }
    )
    _frame_count[0] += 1


def _complete_skill_sidecar_attestations(_audit=sys.audit) -> None:
    """End one Cell's hidden loader attestations before its response frame."""

    _audit("openai4s.skill_cell_complete")


# SIGINT discipline
#
# One delivered SIGINT must end exactly one cell, and must never end the
# worker. Three states carry that:
#
#   _in_user_code     the handler may raise right now
#   _sigint_pending   a signal arrived while it could not, and is owed
#   _sigint_delivered this cell's KeyboardInterrupt came from a SIGNAL, not
#                     from user code raising KeyboardInterrupt itself
#
# `_sigint_pending` is the half that was missing. `Kernel.interrupt()` sends
# ONE signal; every window in which the handler could not raise it used to
# swallow it AND disarm, so a stop pressed a millisecond early left the cell
# running to completion with nothing anywhere saying the interrupt had been
# dropped. Deferring instead of dropping keeps the one signal the host sent.
_in_user_code = [False]
_sigint_delivered = [False]
_sigint_pending = [False]


def _sigint_swallow(signum, frame):  # noqa: ANN001, ARG001
    """Post-fire handler: swallow a second SIGINT during cleanup."""
    return None


def _disarm_sigint() -> None:
    try:
        signal.signal(signal.SIGINT, _sigint_swallow)
    except (ValueError, OSError):  # pragma: no cover - not main thread
        pass


def _sigint_handler(signum, frame):  # noqa: ANN001, ARG001
    if _in_user_code[0]:
        # one-shot: disarm so a second signal during unwinding is eaten
        _disarm_sigint()
        _sigint_delivered[0] = True
        raise KeyboardInterrupt
    # Not raisable yet: either the cell has not reached its first bytecode, or
    # user code is inside a protocol write and unwinding through the write lock
    # is not safe. Record it and STAY ARMED -- the previous code disarmed here,
    # which turned "too early" into "uninterruptible for the rest of the cell".
    _sigint_pending[0] = True


def _arm_sigint() -> None:
    _sigint_delivered[0] = False
    _sigint_pending[0] = False
    try:
        signal.signal(signal.SIGINT, _sigint_handler)
    except (ValueError, OSError):
        pass  # not main thread / unsupported


def _raise_if_sigint_pending() -> None:
    """Raise a SIGINT that arrived while the handler could not raise it.

    Called at the first instruction of user code and immediately after every
    protocol write user code can cause, so a deferred signal is owed for
    microseconds rather than for the length of the cell.
    """
    if not _sigint_pending[0]:
        return
    _sigint_pending[0] = False
    _disarm_sigint()
    _sigint_delivered[0] = True
    raise KeyboardInterrupt


def _register_cell(code: str, tag: str) -> None:
    lines = [ln + "\n" for ln in code.split("\n")]
    linecache.cache[tag] = (len(code), None, lines, tag)
    _LIVE_TAGS.append(tag)
    # evict by counter order (not dict order), retaining the newest N.
    while len(_LIVE_TAGS) > _MAX_CACHED_CELLS:
        old = _LIVE_TAGS.pop(0)
        linecache.cache.pop(old, None)


def _error_lineno(tb, tag: str) -> tuple[int | None, str | None]:
    lineno = None
    call = None
    for frame, ln in traceback.walk_tb(tb):
        if frame.f_code.co_filename == tag:
            lineno = ln
            call = frame.f_code.co_name
    return lineno, (None if call in (None, "<module>") else call)


def _drain_skill_sidecar_loads() -> list[dict]:
    """Return worker-generated successful imports not reported by prior Cells.

    Current loaders publish a separate protocol frame as soon as execution
    succeeds.  The visible list remains as compatibility result metadata for
    an older bootstrap hook; the manager prefers an already-received event
    frame when both are present.
    """

    protocol_frames = _SKILL_LOAD_PROTOCOL_FRAMES[0]
    _SKILL_LOAD_PROTOCOL_FRAMES[0] = 0
    events = _NS.get("__openai4s_skill_load_events__")
    if type(events) is not list:
        _SKILL_LOAD_EVENT_STATE[:] = [None, 0]
        return []
    identity = id(events)
    if _SKILL_LOAD_EVENT_STATE[0] != identity:
        _SKILL_LOAD_EVENT_STATE[:] = [identity, 0]
    cursor = _SKILL_LOAD_EVENT_STATE[1]
    if type(cursor) is not int or cursor < 0 or cursor > len(events):
        cursor = 0
    pending = events[cursor:]
    captured = [dict(item) for item in pending if type(item) is dict]
    if len(captured) != len(pending):
        captured.append({"event": "invalid_sidecar_event"})
    _SKILL_LOAD_EVENT_STATE[1] = len(events)
    return [] if protocol_frames else captured


def _install_host(ns: dict, *, mode: str) -> None:
    try:
        from openai4s.sdk.host import build_host

        # splice gate: the host surface is trimmed by kernel mode. The
        # manager sets OPENAI4S_KERNEL_MODE ("repl" control-plane vs "python"/"R"
        # analysis). An analysis kernel is spliced without frames/query/mcp/
        # delegate — those symbols are genuinely absent (AttributeError).
        # A remote worker has no OPENAI4S_KERNEL_GENERATION -- the transport
        # branch of `Kernel._spawn` never builds a child environment -- so it
        # takes the value the Host handed back on the handshake. Passed at
        # construction because `BashExecutor` resolves its fallback once, in
        # `__init__`, and a value set afterwards would arrive too late.
        generation = getattr(sys, "_openai4s_remote_generation", None)
        ns["host"] = build_host(host_call, mode=mode, generation=generation)
        ns["openai4s"] = ns["host"]  # openai4s alias
    except Exception as e:  # noqa: BLE001 - keep kernel alive
        _write_frame({"type": "log", "msg": f"host sdk unavailable: {e}"})
        # A marker, but only for a worker that was *supposed* to have a host.
        #
        # `log` frames are dropped by the manager, so this failure was silent
        # where it matters most. A direct worker launch now derives the package
        # root from this script's trusted path, but a partial or damaged remote
        # installation can still lack the SDK. Raise where `host` is *used* so
        # that failure is visible rather than silently running a diminished
        # kernel.
        #
        # Scoped to the remote case because absence is a real contract
        # elsewhere: the Jupyter bridge deliberately exposes no `host` (it is
        # not a second outer loop), and `'host' in globals()` being False is
        # what its tests assert. Installing a raising stand-in there would
        # turn a documented absence into a present-but-broken name.
        if (os.environ.get(_CONNECT_ENV) or "").strip():
            detail = (
                f"the host SDK could not be imported in this worker ({e}). "
                "Install a complete OpenAI4S package beside kernel/worker.py; "
                "see docs/team-server.md."
            )

            class _HostUnavailable:
                def __getattr__(self, name):
                    raise RuntimeError(detail)

                def __call__(self, *a, **k):
                    raise RuntimeError(detail)

            ns["host"] = _HostUnavailable()
            ns["openai4s"] = ns["host"]
    # provenance: monkeypatch readers/writers to track object-level lineage.
    try:
        from openai4s.kernel import provenance

        provenance.install(host_call)
    except Exception as e:  # noqa: BLE001
        _write_frame({"type": "log", "msg": f"provenance unavailable: {e}"})


class _BoundedBuffer(io.StringIO):
    """A capture buffer that stops RETAINING at `MAX_OUTPUT`.

    Bounding at the producer and bounding at the response builder yield the
    same string and a completely different peak, which is why nothing caught
    this: the visible result was always correct. `_cap` ran once the whole
    payload was already in worker RAM. Measured on this file before the change,
    a cell doing 200 x `sys.stderr.write('x' * 1_000_000)` peaked at 452 MB to
    keep 1 MB of it. Peak here is the cap plus at most one incoming chunk.

    An `io.StringIO` subclass rather than a bare `io.TextIOBase`: this object
    stands in for `sys.stderr` inside user code, and a cell is entitled to find
    the same surface the plain `StringIO` gave it before.
    """

    def __init__(self) -> None:
        super().__init__()
        self._retained = 0
        #: Whether anything was dropped. Retaining exactly `MAX_OUTPUT` makes
        #: the result indistinguishable from output that simply ended there, so
        #: the buffer has to say so itself -- otherwise the captured stream is
        #: silently short, which is the failure this whole change is about.
        self.truncated = False

    @property
    def full(self) -> bool:
        """Whether anything further would be dropped.

        Read by `_bounded_format_exc`, which uses it to stop pulling chunks out
        of the traceback generator rather than format frames it would throw
        away one line later.
        """
        return self._retained >= MAX_OUTPUT

    def write(self, s: str) -> int:  # type: ignore[override]
        if not s:
            return 0
        room = MAX_OUTPUT - self._retained
        if room > 0:
            kept = s if len(s) <= room else s[:room]
            super().write(kept)
            self._retained += len(kept)
            if len(kept) < len(s):
                self.truncated = True
        else:
            self.truncated = True
        # Report the length HANDED OVER, never the length retained. A short
        # return means "partial write" to every stdlib caller: `print` treats it
        # as a failed write and retries the remainder, which would turn a
        # bounded stream into a loop.
        return len(s)

    def captured(self) -> str:
        value = self.getvalue()
        # Exactly one marker per stream, appended here rather than at each
        # write, so a stream cut across a hundred writes still says so once.
        return value + _TRUNCATION_MARKER if self.truncated else value


class _StreamingStdout(_BoundedBuffer):
    """Captures stdout AND streams stdout_chunk frames live, both bounded.

    Charged as the output is produced rather than when the response is built.
    `_cap` at the end of the cell was the only bound, and it runs far too late
    to matter: by then the whole string is already in worker RAM, and every
    intermediate `write` has already been forwarded verbatim as its own frame.

    The retention half of that now lives in `_BoundedBuffer`, shared with cell
    stderr. Two near-identical bounded writers in one file is how the two
    streams drifted apart in the first place -- stdout got the fix and stderr
    did not.
    """

    def __init__(self, cell_id: str) -> None:
        super().__init__()
        self._cell_id = cell_id
        self._streamed = 0
        self._marked = False

    def _emit(self, text: str) -> None:
        _write_frame({"type": "stdout_chunk", "id": self._cell_id, "text": text})

    def write(self, s: str) -> int:  # type: ignore[override]
        if not s:
            return 0
        super().write(s)  # retention bound and `truncated` live in the base

        budget = MAX_OUTPUT - self._streamed
        if budget > 0:
            payload = s[:budget]
            self._streamed += len(payload)
            for start in range(0, len(payload), _MAX_CHUNK_CHARS):
                self._emit(payload[start : start + _MAX_CHUNK_CHARS])
        if self._streamed >= MAX_OUTPUT and not self._marked:
            # Exactly one marker per cell, matching the R worker's contract.
            self._marked = True
            self._emit(_TRUNCATION_MARKER)

        # Report the full length. `print` treats a short return as a failed
        # write and retries, which would turn a bounded stream into a loop.
        return len(s)


def _cap(s: str) -> str:
    """Head-cap a string that is ALREADY materialised.

    The last resort, not the strategy: every stream a cell can grow without
    bound is now bounded while it is being written. What is left for this are
    the short sentences the worker composes itself, plus one that is not short
    -- a trapped `SystemExit`'s message, which exists whole in the cell's own
    frame before the worker ever sees it.

    Module level because the `except` clauses need it, and a nested definition
    further down `_run_cell` does not exist yet when they run.

    `len` on a str counts characters, so the old "bytes" in this message was
    wrong -- and the R worker, which does gate on bytes, says so in its own
    units. Named for what it measures rather than made to agree by coincidence.
    """
    if len(s) <= MAX_OUTPUT:
        return s
    return s[:MAX_OUTPUT] + _TRUNCATION_MARKER


def _bounded_format_exc(exc: BaseException) -> str:
    """Format an exception without ever holding the whole traceback.

    `traceback.format_exc()` joins every chunk first and hands the result to
    `_cap`, which keeps the first megabyte -- so a large traceback was paid for
    in full before anything refused it, and paid for again when the same string
    was serialised into the response frame. Measured on a sixty-deep exception
    chain whose links share one 1 MB message: 122 MB peak to produce 61 MB of
    text, against 3 MB here.

    `TracebackException.format()` is a generator, so the frames past the cap
    are never formatted at all. What this does NOT bound is a single yielded
    chunk: an exception's own message arrives as one string, so
    `raise ValueError('x' * 200_000_000)` still costs 200 MB transiently --
    though that string already exists in the cell's frame either way, and it is
    never retained here. Bounding it would mean reimplementing
    `format_exception_only`, which is a larger promise than this one makes.
    """
    buf = _BoundedBuffer()
    try:
        chunks = traceback.TracebackException.from_exception(exc).format()
        for chunk in chunks:
            buf.write(chunk)
            if buf.full:
                # Everything still inside the generator would be dropped, so
                # stop formatting it -- that is the whole reason for using the
                # generator form. One further chunk is pulled to tell "ended
                # exactly at the cap" from "was cut", so the marker is never
                # attached to complete output.
                if next(chunks, None) is not None:
                    buf.truncated = True
                break
    except BaseException:  # noqa: BLE001 - a hostile __repr__ must not eat the cell
        buf.write(f"{type(exc).__name__}: <traceback could not be formatted>\n")
    return buf.captured()


def _run_cell(
    code: str,
    cell_id: str,
    origin: str = "agent",
    *,
    kernel_mode: str | None = None,
) -> dict:
    global _CELL_SEQ
    _CELL_SEQ += 1
    tag = f"<kernel:{_CELL_SEQ}>"
    _register_cell(code, tag)
    _ACTIVE_CELL_ID[0] = cell_id
    _ACTIVE_CELL_ORIGIN[0] = origin

    # Armed HERE, at the top of the cell, and not thirty lines further down.
    # `sys.stdout` is swapped to the chunk-emitting buffer below, and between
    # that swap and the old arming point sat `GuardBundle.before_cell()`, whose
    # `import matplotlib.pyplot` has been measured at 18.4 seconds against a
    # cold font cache. A host watching for the cell's first stdout chunk can
    # therefore see output -- and send its one interrupt -- while the worker is
    # still in that phase. Arming first means such a signal is LATCHED rather
    # than lost; it is raised at the first instruction of user code below.
    _arm_sigint()

    # The optional Jupyter adapter is a standalone language kernel, not a
    # second Agent/Host control plane.  Its public contract promises no Host
    # RPC or Gateway provenance capture, so do not install either facade in
    # that mode.  Other modes keep the long-standing lazy installation.
    # Protocol workers receive the startup-captured value from ``main``.
    # Direct in-process callers retain the historical environment fallback.
    mode = kernel_mode or os.environ.get("OPENAI4S_KERNEL_MODE", "repl")
    if mode != "jupyter" and "host" not in _NS:
        _install_host(_NS, mode=mode)

    # tell the provenance layer which cell any lineage writes belong to
    try:
        from openai4s.kernel import provenance

        provenance.set_cell_id(cell_id)
    except Exception:  # noqa: BLE001
        pass

    out_buf = _StreamingStdout(cell_id)
    # Bounded as it is written. This was a plain `io.StringIO` capped in the
    # response builder -- exactly the mistake `_StreamingStdout` exists to fix
    # on the other stream, left standing on this one. Not streamed: there is no
    # `stderr_chunk` frame in the protocol and inventing one would be a
    # protocol change this does not need.
    err_buf = _BoundedBuffer()
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out_buf, err_buf

    error_str = None
    error_lineno = None
    error_call = None
    interrupted = False
    files_read: list[str] = []

    # isolation guards: snapshot fragile global state before user code.
    guard = None
    try:
        from openai4s.kernel.guards import GuardBundle

        # Don't autoclose figures: the gateway captures unsaved matplotlib
        # figures after each cell (it savefig's + closes them itself). Autoclosing
        # here would destroy them before capture. The guard still reports leaks.
        guard = GuardBundle(autoclose_figs=False)
        guard.before_cell()
    except Exception:  # noqa: BLE001
        guard = None

    _reset_peak_rss()
    t0 = time.time()
    cpu0 = _cpu_seconds()
    _begin_file_read_observation(tag)
    try:
        try:
            compiled = compile(code, tag, "eval")
            is_expr = True
        except SyntaxError:
            compiled = compile(code, tag, "exec")
            is_expr = False

        _in_user_code[0] = True
        # From here the handler may raise. Everything before it -- the guard
        # phase, both `compile()` calls, whose cost scales with the source --
        # could only latch, so anything the host sent while this cell was
        # getting ready is owed to it, and is owed before the first user
        # statement runs. The comment this replaces called the gap "the
        # 1-bytecode arming window", which it has not been for a long time.
        _raise_if_sigint_pending()
        if is_expr:
            result = eval(compiled, _NS)  # noqa: S307 - intentional in kernel
            if result is not None:
                # Inside user code on purpose. `repr()` runs the object's own
                # `__repr__`, and this print is the ONLY stdout an expression
                # cell produces -- clearing the flag first made every chunk a
                # host can see for such a cell arrive in the one state where an
                # interrupt could not be raised. The `finally` below clears it
                # unconditionally, so nothing here needs to.
                print(repr(result))
        else:
            exec(compiled, _NS)  # noqa: S102 - intentional in kernel
    except KeyboardInterrupt as e:
        _in_user_code[0] = False
        if _sigint_delivered[0]:
            # DELIVERED signal (host.exec_interrupt): interrupted, no lineno.
            interrupted = True
            error_str = "Interrupted"
        else:
            # user code did `raise KeyboardInterrupt`: normal error w/ lineno.
            tb = sys.exc_info()[2]
            error_str = _bounded_format_exc(e)
            error_lineno, error_call = _error_lineno(tb, tag)
            error_str = error_str or f"KeyboardInterrupt: {e}"
    except (SystemExit, GeneratorExit) as e:
        # exit/quit must NOT kill the worker — trap and report.
        _in_user_code[0] = False
        # `_cap` around the MESSAGE, not around the finished sentence. A
        # `SystemExit('x' * 200_000_000)` would otherwise be interpolated whole
        # before anything looked at its size -- and capping the sentence
        # afterwards would append a second truncation marker to a string this
        # call already marked.
        error_str = f"{type(e).__name__} trapped (worker kept alive): " + _cap(str(e))
    except BaseException as exc:  # noqa: BLE001 - capture everything for the agent
        _in_user_code[0] = False
        tb = sys.exc_info()[2]
        error_str = _bounded_format_exc(exc)
        error_lineno, error_call = _error_lineno(tb, tag)
    finally:
        _in_user_code[0] = False
        files_read = _finish_file_read_observation()
        _disarm_sigint()  # outside user code, a signal must not raise here
        sys.stdout, sys.stderr = real_out, real_err

    wall = time.time() - t0
    cpu = _cpu_seconds() - cpu0

    guard_report = {}
    if guard is not None:
        try:
            guard_report = guard.after_cell()
        except Exception:  # noqa: BLE001
            guard_report = {}

    # Any loader that announced a source compile but did not prove execution
    # remains unmatched in the manager and makes the generation unrecoverable.
    # Clear the audit hook's frame references now that no loader frame can
    # legitimately complete after this Cell response.
    _complete_skill_sidecar_attestations()

    response = {
        "type": "response",
        "id": cell_id,
        # All three were bounded while they were produced, so each already
        # carries its own single truncation marker; capping again here would
        # append a second one to a string that says it was cut once.
        "stdout": out_buf.captured(),
        "stderr": err_buf.captured(),
        # `error` used to be capped right here and nowhere else -- and before
        # that, not at all: an exception carrying a large message --
        # `raise ValueError("x" * 12_000_000)`, or a traceback quoting a big
        # repr -- pushed the whole response frame past `_MAX_FRAME_BYTES`.
        # `_write_frame` then correctly refused to put it on the wire and sent
        # a `log` in its place, so no response for this cell id ever arrived
        # and `Kernel.execute` blocked until the watchdog killed the kernel.
        # Verified: the cell had not returned after 90s. The bound now lives at
        # the producer, which fixes the allocation the late cap never could.
        "error": error_str,
        "interrupted": interrupted,
        "trace": {"error_lineno": error_lineno, "error_call": error_call},
        "guards": guard_report,
        "files_read": files_read,
        "usage": {
            "wall_s": round(wall, 4),
            "cpu_s": round(cpu, 4),
            "peak_rss_kb": _peak_rss_kb(),
        },
    }
    sidecar_loads = _drain_skill_sidecar_loads()
    if sidecar_loads:
        response["skill_sidecar_loads"] = sidecar_loads
    return response


# --- read-only variable inspection -----------------------------------------

_INSPECT_HIDDEN = frozenset({"__name__", "__builtins__", "host", "openai4s"})
_SAFE_SCALAR_TYPES = (type(None), bool, int, float, str, bytes)
_SAFE_CONTAINER_TYPES = (list, tuple, dict, set, frozenset)
_INSPECT_SAMPLE_ITEMS = 12
_INSPECT_HASH_BYTES = 32_768


def _safe_type_name(value: object) -> str:
    """Return a type name without invoking the value or its metaclass hooks."""

    value_type = type(value)
    try:
        name = type.__getattribute__(value_type, "__name__")
    except BaseException:  # noqa: BLE001 - even hostile metaclasses stay opaque
        return "object"
    return name[:160] if type(name) is str and name else "object"


def _bounded_bytes(value: bytes) -> bytes:
    if len(value) <= _INSPECT_HASH_BYTES * 2:
        return value
    return (
        value[:_INSPECT_HASH_BYTES]
        + b"<...>"
        + value[-_INSPECT_HASH_BYTES:]
        + str(len(value)).encode("ascii")
    )


def _primitive_token(value: object) -> bytes | None:
    value_type = type(value)
    if value is None:
        return b"none"
    if value_type is bool:
        return b"bool:1" if value else b"bool:0"
    if value_type is int:
        bits = int.bit_length(value)
        if bits > 4096:
            tail = value & ((1 << 256) - 1)
            return (
                b"int-bounded:"
                + str(bits).encode("ascii")
                + b":"
                + str(tail).encode("ascii")
                + (b":negative" if value < 0 else b":positive")
            )
        return b"int:" + str(value).encode("ascii")
    if value_type is float:
        if math.isnan(value):
            return b"float:nan"
        if math.isinf(value):
            return b"float:+inf" if value > 0 else b"float:-inf"
        return b"float:" + value.hex().encode("ascii")
    if value_type is str:
        if len(value) > _INSPECT_HASH_BYTES * 2:
            raw = (
                value[:_INSPECT_HASH_BYTES].encode("utf-8", "surrogatepass")
                + b"<...>"
                + value[-_INSPECT_HASH_BYTES:].encode("utf-8", "surrogatepass")
                + str(len(value)).encode("ascii")
            )
        else:
            raw = value.encode("utf-8", "surrogatepass")
        return b"str:" + _bounded_bytes(raw)
    if value_type is bytes:
        return b"bytes:" + _bounded_bytes(value)
    return None


def _primitive_preview(value: object) -> object:
    value_type = type(value)
    if value is None or value_type is bool:
        return value
    if value_type is int:
        bits = int.bit_length(value)
        return value if bits <= 4096 else f"<integer {bits} bits>"
    if value_type is float:
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "+Infinity" if value > 0 else "-Infinity"
        return value
    if value_type is str:
        return value if len(value) <= 240 else value[:239] + "…"
    if value_type is bytes:
        head = value[:48].hex()
        return "0x" + head + ("…" if len(value) > 48 else "")
    raise TypeError("unsafe primitive preview")


def _container_sample(value: object) -> tuple[list[object], int]:
    """Sample exact built-in containers without calling subclass hooks."""

    value_type = type(value)
    if value_type is list:
        length = list.__len__(value)
        return [
            list.__getitem__(value, i)
            for i in range(min(length, _INSPECT_SAMPLE_ITEMS))
        ], length
    if value_type is tuple:
        length = tuple.__len__(value)
        return [
            tuple.__getitem__(value, i)
            for i in range(min(length, _INSPECT_SAMPLE_ITEMS))
        ], length
    if value_type is dict:
        length = dict.__len__(value)
        items = []
        iterator = dict.items(value).__iter__()
        for _ in range(min(length, _INSPECT_SAMPLE_ITEMS)):
            try:
                items.append(next(iterator))
            except StopIteration:
                break
        return items, length
    if value_type is set:
        length = set.__len__(value)
        iterator = set.__iter__(value)
    elif value_type is frozenset:
        length = frozenset.__len__(value)
        iterator = frozenset.__iter__(value)
    else:
        raise TypeError("unsafe container")
    items = []
    for _ in range(min(length, _INSPECT_SAMPLE_ITEMS)):
        try:
            items.append(next(iterator))
        except StopIteration:
            break
    return items, length


def _safe_container_summary(value: object) -> tuple[str, int, str, str] | None:
    sample, length = _container_sample(value)
    value_type = type(value)
    tokens: list[bytes] = []
    previews: list[str] = []
    for item in sample:
        if value_type is dict:
            key, member = item
            key_token = _primitive_token(key)
            member_token = _primitive_token(member)
            if key_token is None or member_token is None:
                return None
            tokens.append(key_token + b"=>" + member_token)
            previews.append(
                json.dumps(_primitive_preview(key), ensure_ascii=False)
                + ": "
                + json.dumps(_primitive_preview(member), ensure_ascii=False)
            )
        else:
            token = _primitive_token(item)
            if token is None:
                return None
            tokens.append(token)
            previews.append(json.dumps(_primitive_preview(item), ensure_ascii=False))
    if value_type in {set, frozenset}:
        tokens.sort()
        previews.sort()
    opening, closing = {
        list: ("[", "]"),
        tuple: ("(", ")"),
        dict: ("{", "}"),
        set: ("{", "}"),
        frozenset: ("frozenset({", "})"),
    }[value_type]
    suffix = ", …" if length > len(sample) else ""
    preview = opening + ", ".join(previews) + suffix + closing
    canonical = (
        _safe_type_name(value).encode("ascii", "backslashreplace")
        + b":"
        + str(length).encode("ascii")
        + b":"
        + b"|".join(tokens)
    )
    kind = (
        "mapping"
        if value_type is dict
        else ("set" if value_type in {set, frozenset} else "sequence")
    )
    return kind, length, preview[:240], hashlib.sha256(canonical).hexdigest()


def _inspect_one(name: str, value: object) -> dict:
    entry = {"name": name[:160], "type": _safe_type_name(value)}
    value_type = type(value)
    if value_type in _SAFE_SCALAR_TYPES:
        token = _primitive_token(value)
        entry["kind"] = (
            "scalar"
            if value_type not in {str, bytes}
            else ("text" if value_type is str else "bytes")
        )
        if value_type in {str, bytes}:
            entry["length"] = len(value)
        entry["preview"] = _primitive_preview(value)
        if token is not None:
            entry["fingerprint"] = hashlib.sha256(token).hexdigest()
    elif value_type in _SAFE_CONTAINER_TYPES:
        summary = _safe_container_summary(value)
        if summary is not None:
            kind, length, preview, fingerprint = summary
            entry.update(
                kind=kind,
                length=length,
                preview=preview,
                fingerprint=fingerprint,
            )
        else:
            # The top-level exact built-in remains safe to size.  A custom
            # member makes preview/fingerprint unavailable, never executable.
            _sample, length = _container_sample(value)
            entry.update(kind="container", length=length)
    return entry


def _inspect_namespace(limit: int) -> dict:
    names = sorted(
        name
        for name in _NS
        if type(name) is str
        and name not in _INSPECT_HIDDEN
        and not name.startswith("__")
    )
    selected = names[:limit]
    return {
        "variables": [
            _inspect_one(name, dict.__getitem__(_NS, name)) for name in selected
        ],
        "truncated": len(names) > len(selected),
        "limit": limit,
    }


def _workspace_relative_read_path(
    raw_path: str,
    *,
    root: str,
    cwd: str,
    isabs,
    normpath,
    relpath,
    separator: str,
) -> str | None:
    """Return one lexical workspace-relative path, for any host path module.

    ``ntpath`` can exercise the Windows rules on a non-Windows test runner.
    Deliberately do not resolve symlinks here: the worker reports the spelling
    that was actually opened, while the Host remains the authority that
    resolves and scopes that spelling against durable Artifact versions.
    """

    try:
        if isabs(raw_path):
            absolute = normpath(raw_path)
        else:
            absolute = normpath(cwd.rstrip(separator) + separator + raw_path)
        relative = relpath(absolute, normpath(root))
    except (OSError, ValueError):
        # Different Windows drives, malformed paths, and unavailable cwd state
        # are absence of evidence, never reasons to fail a Cell.
        return None
    if (
        not relative
        or relative == "."
        or isabs(relative)
        or relative == ".."
        or relative.startswith(".." + separator)
    ):
        return None
    if separator != "/":
        relative = relative.replace(separator, "/")
    return relative


def _install_file_read_audit_hook() -> None:
    """Observe synchronous, workspace-local file reads made by a Cell.

    CPython's ``open`` audit event fires at the operation, unlike source
    inspection: a dead branch emits nothing.  The event also exposes the mode
    or OS flags, so write-only opens are excluded.  We additionally require the
    active Cell's synthetic filename to appear in the caller stack.  That keeps
    worker housekeeping and a persistent thread left behind by an earlier Cell
    from being attributed to whichever Cell happens to be running now.

    The hook is observational and must never turn an unreadable path or a
    malformed third-party path object into a Cell failure.  All dependencies
    are captured before user code exists; the state is bounded at insertion.
    """

    # Capture the platform's real path primitives before any Cell exists.
    # Unlike importing posix/posixpath, this is valid for a Windows worker too.
    getcwd = os.getcwd
    path_isabs = os.path.isabs
    path_normpath = os.path.normpath
    path_relpath = os.path.relpath
    path_separator = os.sep
    fsdecode = os.fsdecode
    root = path_normpath(getcwd())
    read_state = _FILE_READ_STATE
    max_files = _MAX_FILES_READ
    max_chars = _MAX_FILE_READ_PATH_CHARS

    def _observe_file_read(
        event,
        args,
        *,
        _state=read_state,
        _root=root,
        _relative_path=_workspace_relative_read_path,
        _isabs=path_isabs,
        _normpath=path_normpath,
        _relpath=path_relpath,
        _separator=path_separator,
        _getcwd=getcwd,
        _fsdecode=fsdecode,
        _getframe=sys._getframe,
        _o_accmode=getattr(os, "O_ACCMODE", os.O_WRONLY | os.O_RDWR),
        _o_wronly=os.O_WRONLY,
        _max_files=max_files,
        _max_chars=max_chars,
    ):  # noqa: ANN001 - CPython owns the audit-hook signature
        if event != "open":
            return
        try:
            tag = _state.get("tag")
            if type(tag) is not str or not tag:
                return
            if not args:
                return
            raw_path = args[0]
            if isinstance(raw_path, bytes):
                raw_path = _fsdecode(raw_path)
            if type(raw_path) is not str or not raw_path or "\x00" in raw_path:
                return

            mode = args[1] if len(args) > 1 else None
            flags = args[2] if len(args) > 2 else None
            if type(mode) is str:
                # r/rb and every update mode read.  w/a/x without '+' are
                # write-only and must not become input evidence.
                if "r" not in mode and "+" not in mode:
                    return
            elif type(flags) is int:
                if (flags & _o_accmode) == _o_wronly:
                    return
            else:
                # An unknown mode is not evidence of a read.
                return

            # Require a synchronous call chain rooted in this Cell.  Bound the
            # walk so a deliberately deep stack cannot make one audit event
            # unbounded work.  Child/background threads have no such frame and
            # are conservatively omitted rather than mis-attributed.
            frame = _getframe(1)
            matched = False
            for _ in range(128):
                if frame is None:
                    break
                if frame.f_code.co_filename == tag:
                    matched = True
                    break
                frame = frame.f_back
            if not matched:
                return

            relative = _relative_path(
                raw_path,
                root=_root,
                cwd=_getcwd(),
                isabs=_isabs,
                normpath=_normpath,
                relpath=_relpath,
                separator=_separator,
            )
            if not relative or len(relative) > _max_chars:
                return

            paths = _state.get("paths")
            seen = _state.get("seen")
            if type(paths) is not list or type(seen) is not set:
                return
            if relative in seen or len(paths) >= _max_files:
                return
            seen.add(relative)
            paths.append(relative)
        except BaseException:  # noqa: BLE001 - observation never blocks an open
            return

    sys.addaudithook(_observe_file_read)


def _install_audit_hook(event_key: bytes) -> bool:
    """Arm the in-kernel dlopen guard and Skill-load diagnostic hook.

    Runs inside THIS worker process — an audit hook only sees events raised in
    its own interpreter. ``OPENAI4S_SAFETY_AUDIT_HOOK=0`` disables the dlopen
    policy, but not the diagnostic event channel. Best-effort: a failure here
    must never stop the kernel from serving cells.
    """
    dlopen_enabled = os.environ.get(
        "OPENAI4S_SAFETY_AUDIT_HOOK", "1"
    ).strip().lower() not in ("0", "false", "no", "off")
    try:
        from openai4s.security.audit_hook import install

        def skill_event_origin() -> str:
            return str(_ACTIVE_CELL_ORIGIN[0])

        event_sink = _publish_skill_sidecar_event
        install(
            enabled=dlopen_enabled,
            skill_event_sink=event_sink,
            skill_event_origin=skill_event_origin,
            skill_event_key=event_key,
        )
        _install_file_read_audit_hook()
        # Remove the convenient global name, while making no security claim:
        # Python introspection can still recover the hook's live references.
        # The Host therefore rejects these frames as recovery evidence.
        globals().pop("_publish_skill_sidecar_event", None)
        return True
    except Exception as e:  # noqa: BLE001
        _write_frame({"type": "log", "msg": f"audit hook unavailable: {e}"})
        return False


def _initialize_manager_attestation() -> bool:
    """Consume the one pre-Cell manager handshake and arm the hidden hook."""

    raw_line = _readline_protocol()
    if not raw_line:
        return False
    try:
        request = json.loads(raw_line)
    except ValueError:
        return False
    request_id = request.get("id", "unknown")
    raw_key = request.get("skill_attestation_key")
    if request.get("type") != "initialize" or type(raw_key) is not str:
        _write_frame(
            {
                "type": "initialization_error",
                "id": request_id,
                "error": "missing manager attestation handshake",
            }
        )
        return False
    try:
        event_key = bytes.fromhex(raw_key)
    except ValueError:
        event_key = b""
    # Drop the convenient protocol references before any user frame exists.
    # The hook still retains the key inside the same interpreter, so this is
    # hygiene rather than a security boundary.
    request.clear()
    raw_line = ""
    raw_key = ""
    if len(event_key) != 32 or not _install_audit_hook(event_key):
        _write_frame(
            {
                "type": "initialization_error",
                "id": request_id,
                "error": "invalid manager attestation handshake",
            }
        )
        return False
    _write_frame({"type": "initialized", "id": request_id})
    return True


def main() -> None:
    # First statement, before any handshake: an idle worker must survive a
    # signal. Until the first cell armed one, SIGINT kept Python's default
    # disposition, so a stop delivered before or between cells raised
    # KeyboardInterrupt straight out of this loop and killed the worker --
    # taking the namespace with it, which is the one thing the interrupt
    # contract promises not to do. Placing it ahead of the attestation makes
    # "the manager has a live worker" imply "that worker is signal-safe".
    _disarm_sigint()
    _setup_protocol_channels()
    if not _initialize_manager_attestation():
        return
    # Kernel mode is process identity established by the manager at spawn.
    # Capture both it and the runner object in this driver frame. This keeps
    # ordinary cell mutations of ``os.environ`` and ``__main__`` globals from
    # changing how a later protocol request is admitted. The manager's absent
    # dispatcher remains the independent authority that rejects Jupyter Host
    # RPC even if arbitrary in-process Python tampers with implementation
    # details beyond this namespace-isolation contract.
    kernel_mode = os.environ.get("OPENAI4S_KERNEL_MODE", "repl")
    run_cell = partial(_run_cell, kernel_mode=kernel_mode)
    while True:
        raw_line = _readline_protocol()
        if not raw_line:
            break
        line = raw_line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            _write_frame(
                {
                    "type": "response",
                    "id": "unknown",
                    "stdout": "",
                    "stderr": "",
                    "error": "invalid JSON request",
                    "interrupted": False,
                    "trace": {"error_lineno": None, "error_call": None},
                    "usage": {},
                }
            )
            continue

        rtype = req.get("type", "execute")
        if rtype == "shutdown":
            break
        if rtype == "execute":
            resp = run_cell(
                req.get("code", ""),
                req.get("id", "unknown"),
                req.get("origin", "agent"),
            )
            _write_frame(resp)
        elif rtype == "inspect_variables":
            request_id = req.get("id", "unknown")
            limit = req.get("limit", 200)
            if type(limit) is not int or not 1 <= limit <= 500:
                _write_frame(
                    {
                        "type": "variables_response",
                        "id": request_id,
                        "variables": [],
                        "truncated": False,
                        "limit": 0,
                        "error": "invalid variable inspection limit",
                    }
                )
                continue
            try:
                inspected = _inspect_namespace(limit)
                _write_frame(
                    {
                        "type": "variables_response",
                        "id": request_id,
                        **inspected,
                    }
                )
            except BaseException:  # noqa: BLE001 - fail closed, keep worker alive
                _write_frame(
                    {
                        "type": "variables_response",
                        "id": request_id,
                        "variables": [],
                        "truncated": False,
                        "limit": limit,
                        "error": "variable inspection failed closed",
                    }
                )
        # host_response frames only arrive inside host_call's read loop; a
        # leak to the main loop is stale desync — ignore.


if __name__ == "__main__":
    main()
