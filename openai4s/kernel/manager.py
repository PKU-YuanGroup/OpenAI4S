"""Host-side kernel manager.

Spawns worker.py as a long-lived subprocess and drives the JSON-per-line
protocol. When the worker emits a `host_call` frame mid-execution, this manager
routes it to the host RPC dispatcher and writes back a `host_response` frame —
this is the inner synchronous RPC loop.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Callable

from openai4s.kernel.environment import build_kernel_environment
from openai4s.security.sandbox import KernelSandbox, create_kernel_sandbox

_WORKER = Path(__file__).resolve().parent / "worker.py"

# A host-call dispatcher: (method:str, args:list) -> data. Raises to signal error.
Dispatcher = Callable[[str, list], Any]


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
        self.generation = 0  # bumped on every (re)spawn
        self.authorization_generation = f"kernel:{uuid.uuid4()}"
        try:
            self._proc = self._spawn()
        except Exception:
            self._sandbox.close()
            raise

    def _spawn(self) -> "subprocess.Popen":
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
        tail: deque[str] = deque(maxlen=400)
        self._stderr_tail = tail

        def _drain(stream=proc.stderr, sink=tail) -> None:
            try:
                for line in stream:
                    sink.append(line)
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
    ) -> dict:
        """Run one cell; block until the response frame, servicing host_calls.

        `on_chunk` (if given) is invoked with each live stdout chunk — used by
        the background executor to expose a running cell's output to exec_peek.
        A caller that owns the cell transaction may provide ``cell_id`` so the
        kernel protocol, provenance records, artifact versions, and execution
        log all refer to the same identity.
        """
        cell_id = str(cell_id or uuid.uuid4())
        self._send({"type": "execute", "id": cell_id, "code": code, "origin": origin})

        stdout_chunks: list[str] = []
        while True:
            frame = self._readline()
            if frame is None:
                # Worker died; surface the drained stderr tail for debugging
                # (the drain thread owns the pipe — never read it here too).
                import time as _time

                _time.sleep(0.05)  # let the drain thread flush the last lines
                err = "".join(getattr(self, "_stderr_tail", []) or [])
                raise RuntimeError(f"kernel worker exited unexpectedly: {err}")
            ftype = frame.get("type")
            if ftype == "response":
                if stdout_chunks and not frame.get("stdout"):
                    frame["stdout"] = "".join(stdout_chunks)
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
            bind_generation = getattr(
                self.dispatcher, "bind_bash_generation", None
            )
            if callable(bind_generation):
                # HostDispatcher is shared by the session and can service a
                # main and background worker on different reader threads.  A
                # thread-local binding prevents either worker from borrowing
                # the other's shell capability generation.
                with bind_generation(self.authorization_generation):
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
            self._sandbox.close()

    def __enter__(self) -> "Kernel":
        return self

    def __exit__(self, *exc: object) -> None:
        self.shutdown()
