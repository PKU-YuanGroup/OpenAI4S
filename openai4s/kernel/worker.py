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

MAX_OUTPUT = 1_000_000  # 1MB head cap on captured cell output
_DISCARD_BUDGET = 8  # bounded discard for desync
_HOST_CALL_WIRE_CAP = 15_000_000  # 15MB host_call payload cap
_MAX_CACHED_CELLS = 128  # linecache retention, evicted by counter

# --- protocol channel setup (dup2 swap + publish) ---------------------


def _setup_protocol_channels() -> None:
    """Move the real protocol streams to high fds and alias fd1->stderr.

    After this runs, sys.stdout writes (fd 1) go to STDERR; the true protocol
    channels live on non-inheritable high fds, wrapped and published on
    sys._openai4s_protocol_stdin / sys._openai4s_protocol_stdout. A reserve dup of
    the input fd is stashed on sys._openai4s_proto_in_reserve for recovery.
    """
    # Duplicate the inherited protocol fds to fresh (high) fds.
    proto_in_fd = os.dup(0)
    proto_out_fd = os.dup(1)
    reserve_fd = os.dup(0)  # spare for one-shot recovery
    for fd in (proto_in_fd, proto_out_fd, reserve_fd):
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
    if reserve is None:
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
    with _write_lock():
        out = _proto_out()
        out.write(line)
        out.flush()


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


def _attach_cell_context(method: str, args: list) -> list:
    """Add worker-owned cell identity to cell-scoped host calls.

    The model may still pass an explicit ``producing_cell_id`` for backwards
    compatibility. The hidden execution id remains authoritative for capture
    identity, while the public value is preserved on the wire. Keep both on
    the existing argument rather than adding another frame reader or protocol
    message type.
    """
    cell_id = _ACTIVE_CELL_ID[0]
    if method != "save_artifact" or not cell_id or not args:
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

# SIGINT discipline
_in_user_code = [False]
_sigint_delivered = [False]


def _sigint_swallow(signum, frame):  # noqa: ANN001, ARG001
    """Post-fire handler: swallow a second SIGINT during cleanup."""
    return None


def _sigint_handler(signum, frame):  # noqa: ANN001, ARG001
    # one-shot: immediately disarm so a second signal during unwinding is eaten
    signal.signal(signal.SIGINT, _sigint_swallow)
    if _in_user_code[0]:
        _sigint_delivered[0] = True
        raise KeyboardInterrupt
    # else: we're in the loop skeleton — swallow, keep the worker alive


def _arm_sigint() -> None:
    _sigint_delivered[0] = False
    try:
        signal.signal(signal.SIGINT, _sigint_handler)
    except (ValueError, OSError):
        pass  # not main thread / unsupported


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

    Bootstrap can replace its event list when a generation is reinitialized;
    list identity resets the cursor.  This is result metadata only—no extra
    protocol reader, Host call, or sidecar execution happens here.
    """

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
    return captured


def _install_host(ns: dict) -> None:
    try:
        from openai4s.sdk.host import build_host

        # splice gate: the host surface is trimmed by kernel mode. The
        # manager sets OPENAI4S_KERNEL_MODE ("repl" control-plane vs "python"/"R"
        # analysis). An analysis kernel is spliced without frames/query/mcp/
        # delegate — those symbols are genuinely absent (AttributeError).
        mode = os.environ.get("OPENAI4S_KERNEL_MODE", "repl")
        ns["host"] = build_host(host_call, mode=mode)
        ns["openai4s"] = ns["host"]  # openai4s alias
    except Exception as e:  # noqa: BLE001 - keep kernel alive
        _write_frame({"type": "log", "msg": f"host sdk unavailable: {e}"})
    # provenance: monkeypatch readers/writers to track object-level lineage.
    try:
        from openai4s.kernel import provenance

        provenance.install(host_call)
    except Exception as e:  # noqa: BLE001
        _write_frame({"type": "log", "msg": f"provenance unavailable: {e}"})


class _StreamingStdout(io.StringIO):
    """Captures stdout AND streams stdout_chunk frames live."""

    def __init__(self, cell_id: str) -> None:
        super().__init__()
        self._cell_id = cell_id

    def write(self, s: str) -> int:  # type: ignore[override]
        n = super().write(s)
        if s:
            _write_frame({"type": "stdout_chunk", "id": self._cell_id, "text": s})
        return n


def _run_cell(code: str, cell_id: str, origin: str = "agent") -> dict:
    global _CELL_SEQ
    _CELL_SEQ += 1
    tag = f"<kernel:{_CELL_SEQ}>"
    _register_cell(code, tag)
    _ACTIVE_CELL_ID[0] = cell_id

    if "host" not in _NS:
        _install_host(_NS)

    # tell the provenance layer which cell any lineage writes belong to
    try:
        from openai4s.kernel import provenance

        provenance.set_cell_id(cell_id)
    except Exception:  # noqa: BLE001
        pass

    out_buf = _StreamingStdout(cell_id)
    err_buf = io.StringIO()
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out_buf, err_buf

    error_str = None
    error_lineno = None
    error_call = None
    interrupted = False

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
    _arm_sigint()
    try:
        try:
            compiled = compile(code, tag, "eval")
            is_expr = True
        except SyntaxError:
            compiled = compile(code, tag, "exec")
            is_expr = False

        _in_user_code[0] = True  # narrow the 1-bytecode arming window
        if is_expr:
            result = eval(compiled, _NS)  # noqa: S307 - intentional in kernel
            _in_user_code[0] = False
            if result is not None:
                print(repr(result))
        else:
            exec(compiled, _NS)  # noqa: S102 - intentional in kernel
            _in_user_code[0] = False
    except KeyboardInterrupt as e:
        _in_user_code[0] = False
        if _sigint_delivered[0]:
            # DELIVERED signal (host.exec_interrupt): interrupted, no lineno.
            interrupted = True
            error_str = "Interrupted"
        else:
            # user code did `raise KeyboardInterrupt`: normal error w/ lineno.
            tb = sys.exc_info()[2]
            error_str = traceback.format_exc()
            error_lineno, error_call = _error_lineno(tb, tag)
            error_str = error_str or f"KeyboardInterrupt: {e}"
    except (SystemExit, GeneratorExit) as e:
        # exit/quit must NOT kill the worker — trap and report.
        _in_user_code[0] = False
        error_str = f"{type(e).__name__} trapped (worker kept alive): {e}"
    except BaseException:  # noqa: BLE001 - capture everything for the agent
        _in_user_code[0] = False
        tb = sys.exc_info()[2]
        error_str = traceback.format_exc()
        error_lineno, error_call = _error_lineno(tb, tag)
    finally:
        _in_user_code[0] = False
        signal.signal(signal.SIGINT, _sigint_swallow)  # disarm outside user code
        sys.stdout, sys.stderr = real_out, real_err

    wall = time.time() - t0
    cpu = _cpu_seconds() - cpu0

    guard_report = {}
    if guard is not None:
        try:
            guard_report = guard.after_cell()
        except Exception:  # noqa: BLE001
            guard_report = {}

    def _cap(s: str) -> str:
        if len(s) <= MAX_OUTPUT:
            return s
        return s[:MAX_OUTPUT] + f"\n...(truncated at {MAX_OUTPUT} bytes)"

    response = {
        "type": "response",
        "id": cell_id,
        "stdout": _cap(out_buf.getvalue()),
        "stderr": _cap(err_buf.getvalue()),
        "error": error_str,
        "interrupted": interrupted,
        "trace": {"error_lineno": error_lineno, "error_call": error_call},
        "guards": guard_report,
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


def _install_audit_hook() -> None:
    """Arm the in-kernel dlopen guard (defense layer 3).

    Runs inside THIS worker process — an audit hook only sees events raised in
    its own interpreter. Opt out with OPENAI4S_SAFETY_AUDIT_HOOK=0. Best-effort:
    a failure here must never stop the kernel from serving cells.
    """
    if os.environ.get("OPENAI4S_SAFETY_AUDIT_HOOK", "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return
    try:
        from openai4s.security.audit_hook import install

        install(enabled=True)
    except Exception as e:  # noqa: BLE001
        _write_frame({"type": "log", "msg": f"audit hook unavailable: {e}"})


def main() -> None:
    _setup_protocol_channels()
    _install_audit_hook()
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
            resp = _run_cell(
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
