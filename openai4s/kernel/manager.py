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
from pathlib import Path
from typing import Any, Callable

from openai4s.kernel.environment import build_kernel_environment
from openai4s.kernel.errors import KernelBusyError, KernelInterruptUnavailable
from openai4s.kernel.sink_drain import CAP_BYTES as _SINK_CAP
from openai4s.kernel.sink_drain import SinkCapture, SinkDirectory
from openai4s.kernel.transport import KernelTransport, PipeTransport
from openai4s.security.sandbox import KernelSandbox, create_kernel_sandbox

_WORKER = Path(__file__).resolve().parent / "worker.py"

# A host-call dispatcher: (method:str, args:list) -> data. Raises to signal error.
Dispatcher = Callable[[str, list], Any]


#: The worker's stderr tail, in bytes. Generous enough that a traceback plus a
#: chatty R `system()` fits; the point is that it is a ceiling on what the
#: daemon allocates, not on what the caller is shown.
_STDERR_TAIL_BYTES = 64 * 1024


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
        self._sandbox = sandbox or create_kernel_sandbox(self.cwd)
        # Exactly one host thread may write a request and consume worker frames
        # at a time.  ``inspect_variables`` deliberately acquires this lock
        # without waiting: an inspector is an idle-only read, never a second
        # reader racing an executing Cell's host_call/response loop.
        self._protocol_transaction_lock = threading.Lock()
        self._action_context_local = threading.local()
        self.generation = 0  # bumped on every (re)spawn
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
        if self.transport_factory is not None:
            self._transport = self.transport_factory()
            self._stderr_tail = self._transport.stderr_tail
            return self._transport.process

        command = self.argv or [self.python, "-u", str(_WORKER)]
        self._transport = PipeTransport(
            self._sandbox.wrap_command(command),
            cwd=self.cwd,
            env=self._sandbox.apply_environment(self._child_env()),
            stderr_tail_factory=lambda: _StderrTail(_STDERR_TAIL_BYTES),
        )
        self._stderr_tail = self._transport.stderr_tail
        return self._transport.process

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
                        text = frame.get("text", "")
                        stdout_chunks.append(text)
                        if on_chunk is not None and text:
                            on_chunk(text)
                    elif ftype == "log":
                        # diagnostic from worker; ignore or log
                        pass
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
        """Serializable OS-boundary state for status APIs and the UI."""

        return self._sandbox.status.to_dict()

    def interrupt(self) -> None:
        """Deliver ONE SIGINT to the worker ( exec_interrupt).

        The worker's one-shot handler raises KeyboardInterrupt inside user code
        and self-disarms, so the interrupt stops the cell but keeps the kernel
        (and its namespace) alive.
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
            return
        sender = getattr(self._sandbox, "send_interrupt", None)
        if callable(sender) and sender(proc.pid, signal.SIGINT):
            return
        try:
            # Popen owns the direct child identity and synchronizes its poll /
            # signal path. Bubblewrap's numeric grandchild never reaches here;
            # KernelSandbox pins that target with a pidfd above.
            proc.send_signal(signal.SIGINT)
        except (ProcessLookupError, OSError):
            pass

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
        self._proc = self._spawn()
        # Every respawn bumps the generation: a lease, a watchdog or an
        # in-flight interrupt naming the previous incarnation has to be
        # refused, and this counter is the whole of how it is refused.
        self.generation += 1
        if not self._transport.alive():
            # A local child is respawned by spawning it. A remote worker is
            # not this process's to spawn: something has to place a new one
            # and let it dial back in. Say so, rather than returning a
            # Kernel that looks restarted and fails on its next cell — the
            # caller's correct move is recovery (a new epoch, state lost),
            # and it can only make it if it is told.
            raise RuntimeError(
                "this worker cannot be respawned in place: it dialled in from "
                "elsewhere, so a new one has to be placed and reconnected"
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
