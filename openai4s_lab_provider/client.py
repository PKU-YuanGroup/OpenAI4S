"""Minimal POSIX pipe client. W2-B owns environment construction and sandboxing."""

import math
import os
import selectors
import signal
import subprocess
import threading
import time
import uuid

from .protocol import MAX_FRAME_BYTES, ProtocolError, decode_frame, encode_frame

DEFAULT_TIMEOUTS = {
    "hello": 30,
    "describe": 120,
    "open": 180,
    "execute": 60,
    "query": 10,
    "stop": 30,
    "close": 10,
}


class ProviderError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class ProviderTimeout(ProviderError):
    def __init__(self):
        super().__init__("provider_timeout", "provider request timed out")


class ProviderProtocolError(ProviderError):
    def __init__(self, message="invalid provider frame"):
        super().__init__("provider_protocol_error", message)


class ProviderGone(ProviderError):
    def __init__(self, returncode, stderr_tail):
        super().__init__(
            "provider_unavailable", "provider process exited or closed its pipe"
        )
        self.returncode = returncode
        self.stderr_tail = stderr_tail


class ProviderClient:
    def __init__(
        self,
        argv,
        *,
        env,
        cwd,
        max_frame_bytes=MAX_FRAME_BYTES,
        stderr_tail_bytes=64 * 1024,
    ):
        self.argv = list(argv)
        self.env = dict(env)
        self.cwd = cwd
        self.max_frame_bytes = max_frame_bytes
        self.stderr_tail_bytes = stderr_tail_bytes
        if max_frame_bytes < 1 or stderr_tail_bytes < 0:
            raise ValueError("invalid buffer size")
        self.process = None
        self._lock = threading.Lock()
        self._stderr_lock = threading.Lock()
        self._stderr = bytearray()
        self._buffer = bytearray()
        self._stderr_thread = None
        self._disposed = False

    def start(self):
        with self._lock:
            if self.process is not None:
                raise RuntimeError("client already started")
            self.process = subprocess.Popen(
                self.argv,
                env=self.env,
                cwd=self.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                start_new_session=True,
                bufsize=0,
            )
            os.set_blocking(self.process.stdin.fileno(), False)
            os.set_blocking(self.process.stdout.fileno(), False)
            self._stderr_thread = threading.Thread(
                target=self._drain_stderr, daemon=True
            )
            self._stderr_thread.start()
        return self

    def _drain_stderr(self):
        try:
            while True:
                chunk = self.process.stderr.read(8192)
                if not chunk:
                    return
                with self._stderr_lock:
                    self._stderr.extend(chunk)
                    del self._stderr[
                        : max(0, len(self._stderr) - self.stderr_tail_bytes)
                    ]
        except (OSError, ValueError):
            return

    def stderr_tail(self):
        with self._stderr_lock:
            return bytes(self._stderr).decode("utf-8", errors="replace")

    def alive(self):
        with self._lock:
            return self._alive_locked()

    def _alive_locked(self):
        # Observe exit without reaping: even a status read must not release the
        # PID reservation before we have disposed the provider's descendants.
        return self.process is not None and not self._wait_unreaped(0)

    def _signal_group(self, sig):
        if (
            self.process is None
            or self._disposed
            or self.process.returncode is not None
        ):
            return
        try:
            # An unreaped leader still pins the PID; a reaped one no longer does.
            os.killpg(self.process.pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            # macOS can report EPERM for a group containing only its zombie
            # leader. Observe without reaping so no reused PGID is signalled.
            if not self._wait_unreaped(0):
                raise

    def _wait_unreaped(self, timeout):
        """Give the leader time to exit while retaining its PID reservation."""
        deadline = time.monotonic() + timeout
        while self.process.returncode is None:
            try:
                exited = os.waitid(
                    os.P_PID, self.process.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG
                )
            except ChildProcessError:
                # Someone else reaped it. poll records that fact before any
                # further group signal (Popen handles ECHILD itself).
                self.process.poll()
                return True
            if exited is not None:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.01, remaining))
        return True

    def _terminate(self, *, graceful=False):
        if self.process is None:
            return
        if graceful:
            self._signal_group(signal.SIGTERM)
            self._wait_unreaped(0.25)
        # Reap only AFTER the last group signal. wait()/poll() here could free
        # the leader PID while a TERM-resistant descendant still holds pipes.
        self._signal_group(signal.SIGKILL)
        self.process.wait()
        self._disposed = True
        if self._stderr_thread:
            self._stderr_thread.join(timeout=1)

    def _gone(self):
        # EOF while the leader is alive is still a broken provider. Reap it and descendants.
        if self.process is not None:
            self._wait_unreaped(0.1)
            self._terminate()
        return ProviderGone(
            self.process.returncode if self.process else None, self.stderr_tail()
        )

    @staticmethod
    def _wait(file, event, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderTimeout()
        with selectors.DefaultSelector() as selector:
            selector.register(file, event)
            if not selector.select(remaining):
                raise ProviderTimeout()

    def _exchange(self, request_id, data, timeout):
        deadline = time.monotonic() + timeout
        offset = 0
        while offset < len(data):
            self._wait(self.process.stdin, selectors.EVENT_WRITE, deadline)
            try:
                offset += os.write(self.process.stdin.fileno(), data[offset:])
            except BlockingIOError:
                continue
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self._buffer[: newline + 1])
                del self._buffer[: newline + 1]
                frame = decode_frame(
                    line, response=True, max_frame_bytes=self.max_frame_bytes
                )
                if frame["id"] == "invalid" and not frame["ok"]:
                    # The provider could not read our frame and is closing.
                    raise ProtocolError("provider rejected an unreadable request")
                if frame["id"] != request_id:
                    if time.monotonic() >= deadline:
                        raise ProviderTimeout()
                    continue
                if not frame["ok"]:
                    if frame["error"]["code"] == "provider_protocol_error":
                        raise ProtocolError("provider reported a protocol error")
                    raise ProviderError(
                        frame["error"]["code"], frame["error"]["message"]
                    )
                return frame["result"]
            if len(self._buffer) >= self.max_frame_bytes:
                raise ProtocolError("frame exceeds byte limit")
            self._wait(self.process.stdout, selectors.EVENT_READ, deadline)
            try:
                chunk = os.read(
                    self.process.stdout.fileno(),
                    min(65536, self.max_frame_bytes + 1 - len(self._buffer)),
                )
            except BlockingIOError:
                continue
            if not chunk:
                if self._buffer:
                    raise ProtocolError("incomplete provider frame")
                raise self._gone()
            self._buffer.extend(chunk)

    def request(self, op, args, *, timeout):
        with self._lock:
            return self._request_locked(op, args, timeout=timeout)

    def _request_locked(self, op, args, *, timeout):
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout must be finite and positive")
        request_id = uuid.uuid4().hex
        try:
            data = encode_frame(
                {"v": 1, "id": request_id, "op": op, "args": args},
                max_frame_bytes=self.max_frame_bytes,
            )
        except ProtocolError as exc:
            # Nothing was written: a host-side argument error must not kill a
            # healthy provider (and be recorded as a lost one).
            raise ValueError(f"request cannot be encoded: {exc}") from None
        if not self._alive_locked():
            raise self._gone()
        try:
            return self._exchange(request_id, data, timeout)
        except ProviderTimeout:
            self._terminate(graceful=op == "close")
            raise
        except ProtocolError as exc:
            self._terminate()
            raise ProviderProtocolError(str(exc)) from None
        except (BrokenPipeError, OSError):
            raise self._gone() from None

    def close(self, timeout=10):
        with self._lock:
            if self.process is None:
                return
            try:
                if self._alive_locked():
                    deadline = time.monotonic() + timeout
                    try:
                        self._request_locked("close", {}, timeout=timeout)
                        self._wait_unreaped(max(0, deadline - time.monotonic()))
                        # The backend has acknowledged close; dispose its group
                        # before reaping the leader, including lingering children.
                        self._terminate()
                    except (ProviderError, subprocess.TimeoutExpired):
                        self._terminate(graceful=True)
                # Never signal a group whose leader has already been reaped.
                self._signal_group(signal.SIGKILL)
                self.process.wait()
                self._disposed = True
                if self._stderr_thread:
                    self._stderr_thread.join(timeout=1)
            finally:
                for pipe in (
                    self.process.stdin,
                    self.process.stdout,
                    self.process.stderr,
                ):
                    pipe.close()
