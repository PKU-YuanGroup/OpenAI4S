"""Host-side kernel manager.

Spawns worker.py as a long-lived subprocess and drives the JSON-per-line
protocol. When the worker emits a `host_call` frame mid-execution, this manager
routes it to the host RPC dispatcher and writes back a `host_response` frame —
this is the inner synchronous RPC loop.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from openai4s.kernel.environment import build_kernel_environment
from openai4s.kernel.errors import KernelBusyError, KernelInterruptUnavailable
from openai4s.kernel.sink_drain import CAP_BYTES as _SINK_CAP
from openai4s.kernel.sink_drain import SinkCapture, SinkDirectory
from openai4s.kernel.transport import KernelTransport, PipeTransport
from openai4s.security.sandbox import (
    KernelReadIsolation,
    KernelSandbox,
    SandboxUnavailableError,
    create_kernel_sandbox,
)


def _sandbox_mode() -> str:
    """The requested posture, read the same way the sandbox itself reads it."""
    return (os.environ.get("OPENAI4S_KERNEL_SANDBOX") or "auto").strip().lower()


_WORKER = Path(__file__).resolve().parent / "worker.py"
_KERNEL_RUNTIME_SOURCE_ROOT = Path(__file__).resolve().parents[2]


def _kernel_runtime_read_roots(
    python: str,
    argv: list[str] | None,
    env_root: str | None,
) -> tuple[Path, ...]:
    """Return exact immutable roots needed to start a local worker.

    Team isolation also masks the canonical system-temp directory. Source
    checkouts, virtual environments and CI runtimes can legitimately live
    there, so preserve only the actual worker source/runtime roots rather than
    reopening the whole temp tree (which would expose stale sibling kernels).
    """

    roots: list[Path] = [_KERNEL_RUNTIME_SOURCE_ROOT]
    if env_root:
        roots.append(Path(env_root).expanduser())

    for value in (python, *(argv or ())):
        path = Path(value).expanduser()
        try:
            is_runtime_file = path.is_absolute() and path.is_file()
        except (OSError, ValueError):
            is_runtime_file = False
        if not is_runtime_file:
            continue
        # Keep both a venv's lexical prefix and the resolved interpreter's
        # framework/base prefix. The latter matters when bin/python is a
        # symlink; the former contains the selected environment's packages.
        for executable in (path, path.resolve(strict=False)):
            prefix = executable.parent.parent
            if prefix == Path(prefix.anchor) or not prefix.is_dir():
                continue
            if prefix not in roots:
                roots.append(prefix)
    return tuple(roots)


# A host-call dispatcher: (method:str, args:list) -> data. Raises to signal error.
Dispatcher = Callable[[str, list], Any]


#: The worker's stderr tail, in bytes. Generous enough that a traceback plus a
#: chatty R `system()` fits; the point is that it is a ceiling on what the
#: daemon allocates, not on what the caller is shown.
_STDERR_TAIL_BYTES = 64 * 1024
#: A worker has already authenticated at the transport layer by the time this
#: handshake starts, but it has not yet become a supervisor candidate.  An old
#: or wedged peer must not hold that lifecycle ticket forever.  A timer kills
#: the transport to wake the existing (and only) frame reader; it never starts
#: a competing reader.
_SKILL_SIDECAR_INITIALIZATION_TIMEOUT_S = 15.0


class _StderrTail:
    """The last N bytes of a stream, bounded as it arrives.

    Bytes rather than lines, and a bound rather than a count, because the
    producers named at the drain site emit whatever a child wrote to fd2 --
    including one line of arbitrary length. `deque(maxlen=400)` bounded the
    number of lines and nothing else.

    Reports what it saw, kept and dropped, which is the per-channel accounting
    plan section 7.4 asks every bounded channel for; the kernel-stderr channel
    had none.
    """

    __slots__ = ("_budget", "_buf", "seen_bytes", "dropped_bytes")

    def __init__(self, budget: int) -> None:
        self._budget = int(budget)
        self._buf = bytearray()
        self.seen_bytes = 0
        self.dropped_bytes = 0

    def feed(self, data: bytes) -> None:
        if not data:
            return
        self.seen_bytes += len(data)
        self._buf.extend(data)
        excess = len(self._buf) - self._budget
        if excess > 0:
            del self._buf[:excess]
            self.dropped_bytes += excess

    @property
    def retained_bytes(self) -> int:
        return len(self._buf)

    @property
    def truncated(self) -> bool:
        return self.dropped_bytes > 0

    def text(self) -> str:
        # A budget cut lands wherever the byte count ran out, which is
        # mid-character often enough to matter; `replace` keeps the tail
        # readable rather than raising on the boundary.
        return self._buf.decode("utf-8", "replace")

    # The death path joins the tail with `"".join(...)`, which is what the
    # deque supported. Staying iterable keeps that call site unchanged.
    def __iter__(self):
        return iter((self.text(),))

    def __bool__(self) -> bool:
        return bool(self._buf)


# Re-exported, not redefined: both live in `kernel/errors.py` so that
# `supervisor` -- which this module reaches through the watchdog -- can catch
# them without importing a partially-initialised `manager`. Every existing
# `from openai4s.kernel.manager import KernelBusyError` keeps working.
KernelBusyError = KernelBusyError
KernelInterruptUnavailable = KernelInterruptUnavailable


@dataclass(frozen=True)
class InterruptDelivery:
    """What actually happened when a cell was asked to stop.

    `delivered` is the field to branch on: False means the cell is still
    running and nothing further will stop it before the watchdog replaces the
    worker. `target` names which path was taken (``transport`` for a remote
    allocation, ``sandbox`` for bubblewrap's pinned identity, ``local-process``
    for a direct signal) and `reason` says why a False is False.

    Falsy when nothing was delivered, so the common check reads as
    `if not kernel.interrupt():` and a caller that ignores the result keeps the
    behaviour it had when this method returned None.
    """

    delivered: bool
    target: str
    reason: str | None = None

    def __bool__(self) -> bool:
        return self.delivered


class Kernel:
    def __init__(
        self,
        dispatcher: Dispatcher | None = None,
        cwd: str | None = None,
        mode: str = "repl",
        python: str | None = None,
        env_root: str | None = None,
        env_name: str | None = None,
        argv: list[str] | None = None,
        sandbox: KernelSandbox | None = None,
        read_isolation: KernelReadIsolation | None = None,
        capture_sinks: bool = False,
        transport_factory: Callable[[], KernelTransport] | None = None,
    ):
        self.dispatcher = dispatcher
        self.mode = mode
        self.cwd = cwd
        # Which interpreter runs worker.py, and (for a conda env) its prefix — so
        # cells run in a *selected* prebuilt environment rather than always the
        # daemon's own Python. Defaults to sys.executable (the base kernel).
        self.python = python or sys.executable
        self.env_root = env_root
        self.env_name = env_name
        # Full worker command override. The frame protocol is language-neutral;
        # a non-python worker (kernel/r_kernel.py) supplies its own argv and the
        # manager loop (execute/host_call routing/restart/interrupt) is reused
        # verbatim. Kept across restart() so a respawn preserves the language.
        self.argv = argv
        # How this kernel reaches its worker. None means the local path:
        # a child process over pipes, byte-for-byte what it always was. A
        # factory (not an instance) because `restart()` builds a fresh one,
        # and a transport that could only be created once would make a
        # respawn impossible for exactly the remote case that needs it most.
        self.transport_factory = transport_factory
        self._transport: KernelTransport | None = None
        # The OS boundary is independent of the JSON frame protocol: it only
        # wraps the worker argv and supplies a private temp directory.  Host RPC
        # remains on the existing pipes and is still serviced by this manager's
        # one synchronous reader loop.
        if read_isolation is not None:
            read_isolation = read_isolation.with_allowed_roots(
                _kernel_runtime_read_roots(self.python, self.argv, self.env_root)
            )
        self._sandbox = sandbox or create_kernel_sandbox(
            self.cwd, read_isolation=read_isolation
        )
        # Exactly one host thread may write a request and consume worker frames
        # at a time.  ``inspect_variables`` deliberately acquires this lock
        # without waiting: an inspector is an idle-only read, never a second
        # reader racing an executing Cell's host_call/response loop.
        self._protocol_transaction_lock = threading.Lock()
        self._action_context_local = threading.local()
        self._skill_sidecar_capture_failed = False
        # Chunks that arrived stamped with someone else's cell id. Counted
        # rather than silently dropped: a number nobody can read is the same
        # dropped frame with a better conscience.
        self._stale_stdout_chunks = 0
        # The worker's own diagnostics, bounded. Public because the only point
        # of keeping them is that something can read them back.
        self.worker_log_tail: deque[str] = deque(maxlen=32)
        self._skill_sidecar_attestation_key = b""
        self.generation = 0  # bumped on every (re)spawn
        # Minted here for a local worker, which learns it through
        # `_child_env`. A remote worker cannot: the transport branch of
        # `_spawn` returns before any child environment is built, and by the
        # time this runs its bootstrap credential was already issued. So for
        # a cluster kernel the Host mints the value during the *handshake*
        # instead and the caller replaces this one -- see
        # `adopt_authorization_generation`.
        self.authorization_generation = f"kernel:{uuid.uuid4()}"
        # A worker that cannot bound its own output between top-level
        # expressions (r_worker.R) sinks to a fifo per cell and lets the host
        # do the bounding. Created here, so a temp directory where fifos cannot
        # be made refuses the kernel instead of producing one whose cells
        # silently have no cap.
        self._sinks: "SinkDirectory | None" = None
        if capture_sinks:
            self._sinks = SinkDirectory(self._sandbox.status.temp_dir)
        try:
            self._proc = self._spawn()
        except Exception:
            if self._sinks is not None:
                self._sinks.close()
            self._sandbox.close()
            raise

    def _spawn(self) -> "subprocess.Popen | None":
        """Create this kernel's transport and return its local child, if any.

        Fail closed on an unsupported platform, here rather than in a warning
        at onboarding: every Python and R kernel passes through this method,
        so there is no route that reaches a subprocess without being asked.
        A program that warns and proceeds has made a different promise from
        one that refuses, and a half-working kernel is the worse outcome for
        a product whose claim is that its results can be trusted.

        The transport is where "local pipes" and "a worker that dialled in
        from a compute node" differ; everything above this line — the single
        frame reader, the id-routed host_response, the host-call transaction
        lock — is identical for both and stays that way.
        """
        from openai4s.platform_support import require_supported

        require_supported()
        is_python_worker = self.argv is None
        if is_python_worker:
            self._skill_sidecar_attestation_key = os.urandom(32)
        else:
            self._skill_sidecar_attestation_key = b""

        if self.transport_factory is not None:
            # `enforce` is the posture that promises the boundary is really
            # there and fails closed when it is not. A remote worker is
            # started by a scheduler on another machine, so `wrap_command`
            # and `apply_environment` have nothing to wrap -- the daemon
            # cannot confine a process it does not spawn. Silently running
            # the cell anyway is what turns `enforce` into `auto` for the
            # one execution path furthest from the operator; the boundary
            # for remote work belongs to the resource plane (a job cgroup, a
            # container image), and until it is declared there this refuses.
            if _sandbox_mode() == "enforce":
                raise SandboxUnavailableError(
                    "OPENAI4S_KERNEL_SANDBOX=enforce, but this kernel runs on "
                    "a remote node where the daemon cannot establish an OS "
                    "boundary. Confine the job in the resource plane and set "
                    "the mode to `auto`, or run this session locally."
                )
            self._transport = self.transport_factory()
            self._stderr_tail = self._transport.stderr_tail
            if is_python_worker:
                self._initialize_skill_sidecar_attestation()
            return self._transport.process

        command = self.argv or [self.python, "-u", str(_WORKER)]
        child_environment = self._child_env()
        wrapped_command = self._sandbox.wrap_command(command)
        pass_fds_for = getattr(self._sandbox, "popen_pass_fds", None)
        adopt_process = getattr(self._sandbox, "adopt_process", None)
        pass_fds = tuple(pass_fds_for()) if callable(pass_fds_for) else ()
        self._transport = PipeTransport(
            wrapped_command,
            cwd=self.cwd,
            env=self._sandbox.apply_environment(child_environment),
            stderr_tail_factory=lambda: _StderrTail(_STDERR_TAIL_BYTES),
            pass_fds=pass_fds,
            # The process-adoption callback exists solely for the inherited
            # bubblewrap ``--info-fd`` channel. Keeping it off the ordinary
            # Seatbelt/single-user path preserves the historical Popen
            # contract (including lightweight process fakes in embedders).
            process_started=(
                adopt_process if pass_fds and callable(adopt_process) else None
            ),
        )
        self._stderr_tail = self._transport.stderr_tail
        if is_python_worker:
            self._initialize_skill_sidecar_attestation()
        return self._transport.process

    def _initialize_skill_sidecar_attestation(self) -> None:
        """Give a Python worker its per-generation diagnostic signing key.

        This handshake belongs above the transport boundary: local workers use
        pipes and cluster workers use a socket, but both run the same Python
        worker and must receive the initialization frame before any Cell. The
        key cannot ride in the environment because Linux retains the initial
        environment bytes in ``/proc/self/environ`` after ``unsetenv()``. The
        key detects malformed/accidental protocol traffic only; because it
        lives in the Cell interpreter it is not recovery evidence.
        """
        transport = self._transport
        if transport is None:  # pragma: no cover - `_spawn` establishes it first
            raise RuntimeError("kernel worker transport is unavailable")

        initialization_id = f"initialize-{uuid.uuid4()}"
        initialized = False
        failure: BaseException | None = None
        timed_out = False
        finished = False
        timer_state_lock = threading.Lock()

        def _expire_initialization() -> None:
            nonlocal timed_out
            with timer_state_lock:
                if finished:
                    return
                timed_out = True
            try:
                # `kill` is the transport-neutral way to wake a blocked
                # `read_line`: SIGKILL for a local child, socket shutdown for
                # a remote worker.  No second protocol reader is introduced.
                transport.kill()
            except Exception:  # noqa: BLE001 - cleanup continues below
                pass

        deadline = threading.Timer(
            _SKILL_SIDECAR_INITIALIZATION_TIMEOUT_S,
            _expire_initialization,
        )
        deadline.daemon = True
        deadline.start()
        try:
            self._send(
                {
                    "type": "initialize",
                    "id": initialization_id,
                    "skill_attestation_key": self._skill_sidecar_attestation_key.hex(),
                }
            )
            diagnostic_frames = 0
            while diagnostic_frames <= 8:
                frame = self._readline()
                if not isinstance(frame, dict):
                    break
                if frame.get("type") == "log":
                    diagnostic_frames += 1
                    continue
                initialized = (
                    frame.get("type") == "initialized"
                    and frame.get("id") == initialization_id
                )
                break
        except BaseException as exc:  # cleanup also covers cancellation/exit
            failure = exc
        finally:
            with timer_state_lock:
                finished = True
            deadline.cancel()

        if initialized and not timed_out:
            return
        try:
            transport.close(graceful=True)
        except Exception:  # noqa: BLE001 — initialization already failed
            try:
                transport.kill()
            except Exception:  # noqa: BLE001 — best-effort final cleanup
                pass
        if failure is not None and not isinstance(failure, Exception):
            raise failure
        message = "kernel worker attestation initialization failed"
        if timed_out:
            message += f" after {_SKILL_SIDECAR_INITIALIZATION_TIMEOUT_S:g}s deadline"
        if failure is not None:
            raise RuntimeError(message) from failure
        raise RuntimeError(message)

    def _child_env(self) -> dict:
        # Build from a strict runtime allowlist: daemon LLM/provider keys,
        # cloud credentials and loader-injection variables must never enter a
        # Python/R worker or any subprocess launched from a cell.
        repo_root = str(Path(__file__).resolve().parent.parent.parent)
        return build_kernel_environment(
            mode=self.mode,
            cwd=self.cwd,
            env_root=self.env_root,
            env_name=self.env_name,
            kernel_generation=self.authorization_generation,
            repo_root=repo_root,
        )

    def _send(self, obj: dict) -> None:
        self._transport.write_line(json.dumps(obj, ensure_ascii=False) + "\n")

    def _readline(self) -> dict | None:
        line = self._transport.read_line()
        if not line:
            return None
        line = line.strip()
        if not line:
            return {}
        return json.loads(line)

    def execute(
        self,
        code: str,
        origin: str = "agent",
        on_chunk: Callable[[str], None] | None = None,
        *,
        cell_id: str | None = None,
        action_context: dict[str, Any] | None = None,
    ) -> dict:
        """Run one cell; block until the response frame, servicing host_calls.

        `on_chunk` (if given) is invoked with each live stdout chunk — used by
        the background executor to expose a running cell's output to exec_peek.
        A caller that owns the cell transaction may provide ``cell_id`` so the
        kernel protocol, provenance records, artifact versions, and execution
        log all refer to the same identity.
        """
        with self._protocol_transaction_lock:
            marker = object()
            previous_context = getattr(self, "_active_action_context", marker)
            inherited_context = getattr(self._action_context_local, "value", None)
            self._active_action_context = dict(
                action_context
                if action_context is not None
                else inherited_context or {}
            )
            capture: SinkCapture | None = None
            try:
                if not self.is_alive():
                    raise RuntimeError("kernel worker is not alive")
                cell_id = str(cell_id or uuid.uuid4())
                request: dict[str, Any] = {
                    "type": "execute",
                    "id": cell_id,
                    "code": code,
                    "origin": origin,
                }
                if self._sinks is not None:
                    # Opened before the request is sent, so the worker's
                    # blocking open finds a reader already waiting and never
                    # blocks on one that has not arrived.
                    capture = self._sinks.open(
                        cap=_SINK_CAP, on_chunk=on_chunk if on_chunk else None
                    )
                    request["sink_out"] = capture.out_path
                    request["sink_err"] = capture.err_path
                self._send(request)

                stdout_chunks: list[str] = []
                sidecar_loads: list[dict[str, Any]] = []
                while True:
                    frame = self._readline()
                    if frame is None:
                        # Worker died; surface the drained stderr tail for debugging
                        # (the drain thread owns the pipe — never read it here too).
                        import time as _time

                        _time.sleep(0.05)  # let the drain thread flush the last lines
                        tail = getattr(self, "_stderr_tail", None)
                        err = "".join(tail or [])
                        # The tail's own accounting, which until now was
                        # computed one attribute away and dropped on the floor.
                        # `record_diagnostic` is the reader: an operator handed
                        # 64 KiB of a 20 MB stream, with nothing saying so, is
                        # reading the end of a failure as though it were the
                        # whole of it. Redacted from the user by
                        # `public_exception` before publication, as before.
                        if getattr(tail, "truncated", False):
                            err += (
                                f" (stderr tail: {tail.retained_bytes} of "
                                f"{tail.seen_bytes} bytes kept, "
                                f"{tail.dropped_bytes} dropped)"
                            )
                        raise RuntimeError(f"kernel worker exited unexpectedly: {err}")
                    ftype = frame.get("type")
                    if ftype == "response":
                        if capture is not None and frame.get("sink_capture"):
                            # The worker declares it sank to the host's fifos,
                            # so the host — not the worker — is what has the
                            # cell's output. A worker that did not (the R
                            # protocol fixture) keeps its own fields.
                            frame["stdout"], frame["stderr"] = capture.finish()
                            # What was read and what was kept, reported rather
                            # than inferred. A capped `stdout` looks the same
                            # whether the host read 300 MB and declined 299 of
                            # them or the worker quietly dropped them before
                            # they were ever written — R's fifo() defaults to
                            # non-blocking, and that second reading is what it
                            # produces. These are the only fields that tell
                            # those two apart.
                            usage = frame.get("usage")
                            if isinstance(usage, dict):
                                usage.update(capture.counters())
                        elif stdout_chunks and not frame.get("stdout"):
                            frame["stdout"] = "".join(stdout_chunks)
                        # A Python Cell and its audit hook share one interpreter.
                        # The Cell can therefore recover or invoke any signing
                        # oracle held by the hook. Worker frames are useful only
                        # as private diagnostics; they are never durable recovery
                        # evidence. The recorder independently enforces the same
                        # fail-closed boundary.
                        frame.pop("skill_sidecar_loads", None)
                        if sidecar_loads:
                            frame["skill_sidecar_loads"] = sidecar_loads
                        # Host-side annotation, not a protocol field: the
                        # observation formatter needs somewhere inside the
                        # workspace to spill an oversized stdout, and the
                        # manager is the only layer that knows where that is.
                        # Adding it to the worker's frame would be a protocol
                        # change for information the worker does not have to
                        # produce.
                        frame.setdefault("cwd", str(self.cwd))
                        return frame
                    if ftype == "host_call":
                        self._service_host_call(frame)
                    elif ftype == "stdout_chunk":
                        # A chunk belongs to the cell whose id it carries.
                        # Without this comparison ANY chunk read during this
                        # call was attributed here -- including one stamped
                        # with a previous cell's id, which is what a
                        # `logging.StreamHandler` bound to that cell's
                        # `sys.stdout`, a finalizer, or a background thread
                        # still writing produces. Two things went wrong with
                        # that: the stale text was concatenated into THIS
                        # cell's stdout, and `on_chunk` fired for it -- so a
                        # caller watching for the cell's first output (the
                        # Notebook, `exec_peek`, and the interrupt contract's
                        # own test) could be told user code had started before
                        # this cell had compiled a line.
                        if frame.get("id") != cell_id:
                            self._stale_stdout_chunks += 1
                            continue
                        text = frame.get("text", "")
                        stdout_chunks.append(text)
                        if on_chunk is not None and text:
                            on_chunk(text)
                    elif ftype == "skill_sidecar_load":
                        # A MAC generated inside the untrusted Cell interpreter
                        # cannot attest that a sidecar executed: Python
                        # introspection can recover the key or signing callable.
                        # Do not retain source bytes or event claims here.
                        if not sidecar_loads:
                            sidecar_loads.append(
                                {"event": "untrusted_worker_sidecar_event"}
                            )
                        self._skill_sidecar_capture_failed = True
                    elif ftype == "log":
                        # Retained, bounded, instead of dropped. The worker
                        # emits these for conditions it cannot put in a
                        # response -- "SIGINT could not be armed for this
                        # cell" is the one that matters, because a cell nobody
                        # can stop otherwise looks exactly like a cell that is
                        # merely slow. `openai4s doctor` and the watchdog read
                        # `worker_log_tail`; nothing branches on it.
                        message = frame.get("msg")
                        if isinstance(message, str) and message:
                            self.worker_log_tail.append(message[:2000])
            finally:
                if capture is not None:
                    # Unconditional: an interrupt, a dead worker or a raising
                    # host call all leave a fifo and two reader threads behind,
                    # and the reader is what keeps a blocked writer moving.
                    capture.close()
                if previous_context is marker:
                    try:
                        del self._active_action_context
                    except AttributeError:
                        pass
                else:
                    self._active_action_context = previous_context

    @contextmanager
    def bind_action_context(self, context: dict[str, Any] | None):
        """Bind audit identity without changing the compatible execute shape."""

        marker = object()
        previous = getattr(self._action_context_local, "value", marker)
        self._action_context_local.value = dict(context or {})
        try:
            yield
        finally:
            if previous is marker:
                try:
                    del self._action_context_local.value
                except AttributeError:
                    pass
            else:
                self._action_context_local.value = previous

    def inspect_variables(self, *, limit: int = 200) -> dict[str, Any]:
        """Read a bounded namespace summary from an idle, live worker.

        This is a dedicated protocol request, not a synthetic Cell: it does
        not compile code, allocate a Cell id/revision, emit stdout, or enter
        the execution log.  Busy inspection fails immediately so this method
        can never become a competing frame reader.
        """

        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("variable inspection limit must be an integer")
        if not 1 <= limit <= 500:
            raise ValueError("variable inspection limit must be between 1 and 500")
        if not self.is_alive():
            raise RuntimeError("kernel worker is not alive")
        if not self._protocol_transaction_lock.acquire(blocking=False):
            raise KernelBusyError("kernel worker is busy")
        try:
            # Re-check after acquiring: the worker may have exited between the
            # optimistic status probe and ownership of the protocol channel.
            if not self.is_alive():
                raise RuntimeError("kernel worker is not alive")
            request_id = f"variables-{uuid.uuid4()}"
            self._send({"type": "inspect_variables", "id": request_id, "limit": limit})
            diagnostic_frames = 0
            while True:
                frame = self._readline()
                if frame is None:
                    raise RuntimeError(
                        "kernel worker exited during variable inspection"
                    )
                if frame.get("type") == "log" and diagnostic_frames < 8:
                    # A startup audit-hook diagnostic can precede the first
                    # request.  It is not a second response and is bounded.
                    diagnostic_frames += 1
                    continue
                if (
                    frame.get("type") != "variables_response"
                    or frame.get("id") != request_id
                ):
                    raise RuntimeError(
                        "kernel protocol desynchronized during variable inspection"
                    )
                error = frame.get("error")
                if error is not None:
                    raise RuntimeError(f"variable inspection failed: {error}")
                if not isinstance(frame.get("variables"), list):
                    raise RuntimeError("invalid variables response from kernel worker")
                return frame
        finally:
            self._protocol_transaction_lock.release()

    @property
    def pid(self) -> int | None:
        """The local child's pid, or None for a worker on another machine.

        None rather than a remote pid: callers record this as "a process on
        this host", and a number that means nothing here is worse than an
        absence a reader can see.
        """
        return self._proc.pid if self._proc is not None else None

    @property
    def sandbox_status(self) -> dict[str, Any]:
        """Serializable OS-boundary state for status APIs and the UI.

        A remote kernel reports what is actually true of it, which is that
        this daemon's sandbox does not apply. `create_kernel_sandbox` runs in
        `__init__` for every kernel and self-tests on the *daemon's* host, so
        a cluster session used to render `enforced: true`, `backend:
        "seatbelt"` and `self_test_passed: true` in the Security panel for
        cells running unconfined on a compute node -- and the same values
        were written into the durable generation record. The transport branch
        of `_spawn` never calls `wrap_command` or `apply_environment`, and it
        could not: the process is on another machine, started by a scheduler.
        Saying so is the fix available here; confining it is the resource
        plane's job (a job-level cgroup, a container image), not something
        the daemon can assert from a distance.
        """
        if self.transport_factory is None:
            return self._sandbox.status.to_dict()
        status = dict(self._sandbox.status.to_dict())
        status.update(
            {
                "enforced": False,
                "backend": "remote",
                "self_test_passed": False,
                "reason": (
                    "this kernel runs on a remote node; the daemon's OS "
                    "sandbox does not apply to it"
                ),
            }
        )
        return status

    def adopt_authorization_generation(self, generation: str) -> None:
        """Use the generation a remote worker was admitted under.

        Only meaningful for a transport-backed kernel, and only with a value
        that came from a `Registration` -- which exists solely for a peer
        that presented a valid, unburned, in-epoch bootstrap credential. The
        Host minted it in the handshake and echoed it to the worker there,
        so this is the two ends agreeing on the Host's own value, not the
        Host accepting the worker's.

        Refused on a local kernel: there the environment already carries a
        generation the child was started with, and replacing it afterwards
        would leave the running worker authorizing against one string while
        the Host checked another.
        """
        text = str(generation or "")
        if not text:
            return
        if self.transport_factory is None:
            raise RuntimeError(
                "a local kernel's generation comes from its child environment "
                "and must not be replaced after the worker has started"
            )
        self.authorization_generation = text

    def interrupt(self) -> "InterruptDelivery":
        """Deliver ONE SIGINT to the worker ( exec_interrupt).

        The worker's one-shot handler raises KeyboardInterrupt inside user code
        and self-disarms, so the interrupt stops the cell but keeps the kernel
        (and its namespace) alive.

        Returns what actually happened. This used to return None, so "the
        signal went to the worker" and "no signal was sent at all" were the
        same answer, and every caller reported a cancel it had no evidence for
        -- while the sandbox's own diagnosis of the gap went to stderr, where
        no caller can read it. The result is falsy when nothing was delivered,
        so `if not kernel.interrupt():` is the whole check; callers that ignore
        it behave exactly as before.
        """
        import signal

        # A remote worker has no pid here; its transport knows whether it
        # can deliver an interrupt at all, and says so rather than
        # pretending. Local kernels fall straight through to the signal path
        # they always used.
        proc = self._proc
        if proc is None:
            if not self._transport.interrupt():
                # Nothing delivered it. Silence here would leave a cell
                # apparently cancelled and actually running.
                raise KernelInterruptUnavailable(
                    "no way to interrupt this worker: it is remote and no "
                    "signal delivery was configured for its allocation"
                )
            return InterruptDelivery(True, "transport")
        sender = getattr(self._sandbox, "send_interrupt", None)
        if callable(sender) and sender(proc.pid, signal.SIGINT):
            # The bool said only "this adapter owns delivery". Ask it whether
            # delivery actually happened.
            taker = getattr(self._sandbox, "take_interrupt_gap", None)
            gap = taker() if callable(taker) else None
            return InterruptDelivery(gap is None, "sandbox", gap)
        if proc.poll() is not None:
            # `Popen.send_signal` returns silently for an exited child, so
            # without this the dead-worker case reported a delivered stop.
            return InterruptDelivery(
                False, "local-process", "the worker had already exited"
            )
        try:
            # Popen owns the direct child identity and synchronizes its poll /
            # signal path. Bubblewrap's numeric grandchild never reaches here;
            # KernelSandbox pins that target with a pidfd above.
            proc.send_signal(signal.SIGINT)
        except (ProcessLookupError, OSError) as error:
            return InterruptDelivery(
                False, "local-process", f"{type(error).__name__}: {error}"
            )
        return InterruptDelivery(True, "local-process")

    def kill_worker(self) -> None:
        """Kill this exact worker without spawning or reading frames.

        This is the watchdog's last-resort escape hatch.  Keeping it on the
        manager avoids callers reaching through the private ``_proc`` field;
        recovery or abandonment remains the owner's responsibility.

        ``_proc`` stays the canonical handle for a local child — it is what
        the sandbox signals and what the watchdog's tests substitute — and
        the transport answers only when there is no local process, which is
        the remote case.
        """
        if self._proc is not None:
            try:
                self._proc.kill()
            except (ProcessLookupError, OSError):
                pass
            return
        self._transport.kill()

    def _service_host_call(self, frame: dict) -> None:
        call_id = frame.get("id")
        method = frame.get("method", "")
        args = frame.get("args", [])
        if self.dispatcher is None:
            self._send(
                {
                    "type": "host_response",
                    "id": call_id,
                    "error": "no host dispatcher configured",
                }
            )
            return
        try:
            bind_generation = getattr(self.dispatcher, "bind_bash_generation", None)
            bind_action = getattr(self.dispatcher, "bind_action_context", None)
            action_context = getattr(self, "_active_action_context", None)
            if callable(bind_generation) and callable(bind_action):
                with bind_generation(self.authorization_generation):
                    with bind_action(action_context):
                        data = self.dispatcher(method, args)
            elif callable(bind_generation):
                # HostDispatcher is shared by the session and can service a
                # main and background worker on different reader threads.  A
                # thread-local binding prevents either worker from borrowing
                # the other's shell capability generation.
                with bind_generation(self.authorization_generation):
                    data = self.dispatcher(method, args)
            elif callable(bind_action):
                with bind_action(action_context):
                    data = self.dispatcher(method, args)
            else:
                data = self.dispatcher(method, args)
            # soft-fail contract: a single-key {"error": msg} return is a
            # soft failure the worker must raise, not a normal result.
            if isinstance(data, dict) and set(data.keys()) == {"error"}:
                self._send(
                    {"type": "host_response", "id": call_id, "error": data["error"]}
                )
            else:
                self._send({"type": "host_response", "id": call_id, "data": data})
        except Exception as e:  # noqa: BLE001
            self._send({"type": "host_response", "id": call_id, "error": str(e)})

    def restart(self) -> None:
        """Tear down the worker and spawn a clean one — a brand-new namespace.

        Used after a mid-task ``pip install`` so freshly installed packages are
        picked up by a fresh process, and to clear a wedged/polluted kernel. The
        caller is responsible for re-running any bootstrap (skill sidecars, etc.)
        against the new process — the ``Kernel`` object itself is reused so all
        references held by the session stay valid.
        """
        # Refused *before* anything is torn down. A remote worker is not this
        # process's to spawn, so this can only ever fail for one -- and it
        # used to fail after closing the transport and bumping the
        # generation, which left the supervisor's slot and the durable
        # `kernel_generations` row pointing at a worker whose socket was
        # already gone, with the exception escaping before
        # `_finish_generation` could record anything. The session's kernel
        # was destroyed by a request that answered 500.
        #
        # The caller's correct move is recovery -- a new epoch, state
        # declared lost -- and it can only make it if the kernel it has is
        # still the one it had.
        if self.transport_factory is not None:
            raise RuntimeError(
                "this worker cannot be respawned in place: it dialled in "
                "from elsewhere, so a new one has to be placed and dial back "
                "in. Recover the session (a new epoch) instead of restarting "
                "its kernel."
            )
        # Teardown belongs to the transport: a local child needs a shutdown
        # frame, a wait, a kill and a reap (a restart that skipped the reap
        # leaked a zombie every time); a remote worker has a socket to close
        # and no pid to signal. Each sequence lives with the thing it is a
        # sequence for.
        try:
            self._transport.close(graceful=True)
        except Exception:  # noqa: BLE001
            pass
        self.authorization_generation = f"kernel:{uuid.uuid4()}"
        try:
            self._proc = self._spawn()
        except Exception as exc:
            from openai4s.kernel.errors import KernelRestartFailed

            raise KernelRestartFailed(
                "the old kernel was cleared but its replacement could not "
                f"start: {exc}"
            ) from exc
        # Every respawn bumps the generation: a lease, a watchdog or an
        # in-flight interrupt naming the previous incarnation has to be
        # refused, and this counter is the whole of how it is refused.
        self._skill_sidecar_capture_failed = False
        self.generation += 1
        if not self._transport.alive():
            # A local respawn that produced a dead child. The remote case is
            # refused at the top of this method, before the old worker is
            # torn down, so reaching here means the fresh local process did
            # not come up.
            from openai4s.kernel.errors import KernelRestartFailed

            raise KernelRestartFailed(
                "the restarted worker is not alive: its process failed to "
                "start or exited immediately"
            )

    def is_alive(self) -> bool:
        return self._transport.alive()

    def shutdown(self) -> None:
        try:
            self._transport.close(graceful=True)
        except Exception:  # noqa: BLE001
            self._transport.kill()
        finally:
            if self._sinks is not None:
                self._sinks.close()
            self._sandbox.close()

    def __enter__(self) -> "Kernel":
        return self

    def __exit__(self, *exc: object) -> None:
        self.shutdown()
