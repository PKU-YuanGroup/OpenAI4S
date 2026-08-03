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
from openai4s.kernel.sink_drain import CAP_BYTES as _SINK_CAP
from openai4s.kernel.sink_drain import SinkCapture, SinkDirectory
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


class KernelBusyError(RuntimeError):
    """The worker protocol is owned by an in-flight cell transaction."""


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

    def _spawn(self) -> "subprocess.Popen":
        # Fail closed on an unsupported platform, here rather than in a warning
        # at onboarding: every Python and R kernel passes through this method,
        # so there is no route that reaches a subprocess without being asked.
        # A program that warns and proceeds has made a different promise from
        # one that refuses, and a half-working kernel is the worse outcome for
        # a product whose claim is that its results can be trusted.
        from openai4s.platform_support import require_supported

        require_supported()
        command = self.argv or [self.python, "-u", str(_WORKER)]
        proc = subprocess.Popen(
            self._sandbox.wrap_command(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=self.cwd,
            env=self._sandbox.apply_environment(self._child_env()),
        )
        # Drain stderr continuously into a bounded tail. Without this, a cell
        # whose child processes write to inherited fd2 (R `system()`, an
        # uncaptured subprocess in python) fills the 64KB pipe and deadlocks
        # the cell forever — nothing used to read stderr until worker death.
        # The tail keeps the death diagnostics the old blocking read provided.
        #
        # Bounded in BYTES, at the read. This was `for line in stream` into a
        # `deque(maxlen=400)`: an unbounded `readline()` whose only cap was a
        # line COUNT applied after the allocation, so one producer emitting a
        # single enormous line -- which is what the comment above says reaches
        # here -- allocated all of it before any limit applied. That is the
        # pattern `mcp_client.py` and `jobs.py` were both changed to remove,
        # and this drain was written after those fixes without adopting them.
        #
        # `.buffer` because the pipe is `text=True` for the stdout protocol
        # frames and cannot be opened per-stream; the raw reader underneath it
        # is where a byte budget can mean bytes.
        self._stderr_tail = _StderrTail(_STDERR_TAIL_BYTES)
        tail = self._stderr_tail
        # `os.read` on the descriptor, not `BufferedReader.read`. Both give
        # bytes, and only one of them is safe here: this thread is a daemon, and
        # a daemon parked inside a buffered read holds that buffer's lock when
        # the interpreter finalises. The whole suite passed and then aborted
        # with `_enter_buffered_busy: could not acquire lock ... at interpreter
        # shutdown` -- a clean exit turned into SIGABRT by the drain alone.
        # `os.read` also returns as soon as anything is available rather than
        # waiting to fill the request, which is what a drain wants.
        try:
            stderr_fd = proc.stderr.fileno()
        except (AttributeError, OSError, ValueError):  # pragma: no cover
            stderr_fd = -1

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
        return proc

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
        assert self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

    def _readline(self) -> dict | None:
        assert self._proc.stdout is not None
        line = self._proc.stdout.readline()
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
    def pid(self) -> int:
        return self._proc.pid

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
        import os
        import signal

        try:
            os.kill(self._proc.pid, signal.SIGINT)
        except (ProcessLookupError, OSError):
            pass

    def kill_worker(self) -> None:
        """Kill this exact worker process without spawning or reading frames.

        This is the watchdog's last-resort escape hatch.  Keeping it on the
        manager avoids callers reaching through the private ``_proc`` field;
        recovery or abandonment remains the owner's responsibility.
        """
        try:
            self._proc.kill()
        except (ProcessLookupError, OSError):
            pass

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
        old = self._proc
        try:
            old.stdin and old.stdin.write(json.dumps({"type": "shutdown"}) + "\n")
            old.stdin and old.stdin.flush()
        except Exception:  # noqa: BLE001
            pass
        try:
            old.wait(timeout=3)
        except Exception:  # noqa: BLE001
            try:
                old.kill()
                old.wait(timeout=2)  # reap so we don't leak a zombie per restart
            except Exception:  # noqa: BLE001
                pass
        for stream in (old.stdin, old.stdout, old.stderr):
            try:
                stream and stream.close()
            except Exception:  # noqa: BLE001
                pass
        self.authorization_generation = f"kernel:{uuid.uuid4()}"
        self._proc = self._spawn()
        self.generation += 1

    def is_alive(self) -> bool:
        return self._proc.poll() is None

    def shutdown(self) -> None:
        try:
            self._send({"type": "shutdown"})
            self._proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            self._proc.kill()
        finally:
            # close the pipe wrappers now — a dead worker's buffered stdin
            # otherwise raises BrokenPipeError at GC-time flush
            for stream in (self._proc.stdin, self._proc.stdout, self._proc.stderr):
                try:
                    stream and stream.close()
                except Exception:  # noqa: BLE001
                    pass
            if self._sinks is not None:
                self._sinks.close()
            self._sandbox.close()

    def __enter__(self) -> "Kernel":
        return self

    def __exit__(self, *exc: object) -> None:
        self.shutdown()
