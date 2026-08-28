"""Worker-side executor for capability-authorized ``host.bash``.

All subprocess creation remains in the scientific kernel.  Before spawning,
the executor obtains a host-issued capability over the existing synchronous
``host_call`` channel, validates every binding locally, marks the token used,
and asks the host to atomically consume it.  A missing or legacy dispatcher is
therefore a clear fail-closed error, never an implicit authorization.
"""

from __future__ import annotations

import math
import os
import secrets
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from openai4s.bash_capability import CAPABILITY_VERSION, command_digest
from openai4s.execution.budget import channel_counters
from openai4s.execution.process_group import (
    await_group_exit,
    group_alive,
    stop_process_group,
)

#: What the caller is shown, and now also what is retained. The tail is kept:
#: for a command that failed, the end is what explains it.
_STDOUT_BUDGET_CHARS = 30_000
_STDERR_BUDGET_CHARS = 8_000


class _BoundedTail:
    """Retain the last N characters of a stream as it arrives.

    Draining and *retaining* are two different requirements. The pipes must be
    read continuously or the child blocks on a full one; only the tail needs
    keeping.
    """

    __slots__ = ("_budget", "_parts", "_length", "truncated", "seen")

    def __init__(self, budget: int) -> None:
        self._budget = budget
        self._parts: deque[str] = deque()
        self._length = 0
        self.truncated = False
        #: Everything that arrived, counted before anything is dropped. The
        #: retained length alone cannot answer "was this cut?" -- it is exactly
        #: the budget both for a command that overran it and for one that
        #: stopped on it.
        self.seen = 0

    def feed(self, text: str) -> None:
        if not text:
            return
        self.seen += len(text)
        self._parts.append(text)
        self._length += len(text)
        while self._length > self._budget and len(self._parts) > 1:
            self._length -= len(self._parts.popleft())
            self.truncated = True
        if self._length > self._budget:
            head = self._parts.popleft()
            self._parts.append(head[-self._budget :])
            self._length = self._budget
            self.truncated = True

    @property
    def retained(self) -> int:
        return self._length

    def value(self) -> str:
        return "".join(self._parts)


def _drain(stream: Any, sink: "_BoundedTail") -> None:
    if stream is None:
        return
    try:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                return
            sink.feed(chunk)
    except (OSError, ValueError):
        return


HostCall = Callable[[str, list], Any]
_MAX_LOCAL_TOKEN_TTL_MS = 60_000
_MAX_SNAPSHOT_FILES = 5000
_MAX_DIFF_PATHS = 100
_MAX_RECENT_TOKENS = 1024


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _capability_error(result: Any, fallback: str) -> RuntimeError:
    if isinstance(result, dict) and result.get("error"):
        return RuntimeError(str(result["error"]))
    return RuntimeError(fallback)


def validate_capability(
    capability: Any,
    *,
    command: str,
    cwd: str,
    generation: str,
    challenge: str,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Validate a host response against the worker's just-created proposal."""

    if not isinstance(capability, dict):
        raise RuntimeError("bash: host authorization returned an invalid capability")
    if capability.get("error"):
        raise RuntimeError(str(capability["error"]))
    required = {
        "version",
        "token",
        "command_sha256",
        "cwd",
        "workspace",
        "allowed_root",
        "generation",
        "challenge",
        "issued_at_ms",
        "expires_at_ms",
    }
    missing = sorted(required - set(capability))
    if missing:
        raise RuntimeError(
            "bash: host authorization omitted capability fields: " + ", ".join(missing)
        )
    if capability["version"] != CAPABILITY_VERSION:
        raise RuntimeError("bash: unsupported host capability version")
    token = capability.get("token")
    if not isinstance(token, str) or len(token) < 24:
        raise RuntimeError("bash: host authorization returned an invalid token")
    expected = {
        "command_sha256": command_digest(command),
        "cwd": cwd,
        "generation": generation,
        "challenge": challenge,
    }
    for key, value in expected.items():
        if capability.get(key) != value:
            raise RuntimeError(f"bash: host capability binding mismatch ({key})")
    try:
        canonical_cwd = Path(cwd).resolve(strict=True)
        workspace = Path(str(capability["workspace"])).resolve(strict=True)
        allowed_root = Path(str(capability["allowed_root"])).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise RuntimeError("bash: host capability contains an invalid path") from exc
    valid_directories = (
        canonical_cwd.is_dir() and workspace.is_dir() and allowed_root.is_dir()
    )
    if not valid_directories:
        raise RuntimeError("bash: host capability path is not a directory")
    if not _within(canonical_cwd, allowed_root):
        raise RuntimeError("bash: host capability does not contain the workdir")
    # The workspace itself must contain the ordinary execution root.  An
    # explicit extra allowed root is accepted only when the host names it as
    # both allowed_root and the exact ancestor of cwd.
    if allowed_root == workspace and not _within(canonical_cwd, workspace):
        raise RuntimeError("bash: host capability workdir escapes the workspace")

    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    try:
        issued = int(capability["issued_at_ms"])
        expires = int(capability["expires_at_ms"])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("bash: host capability has an invalid expiry") from exc
    if expires <= now:
        raise RuntimeError("bash: host capability expired before execution")
    expiry_outside_policy = (
        issued > now + 5000
        or expires <= issued
        or expires - issued > _MAX_LOCAL_TOKEN_TTL_MS
    )
    if expiry_outside_policy:
        raise RuntimeError("bash: host capability expiry is outside policy")
    return capability


def _workspace_snapshot(workspace: Path) -> tuple[dict[str, tuple[int, int]], bool]:
    """Return a bounded file metadata snapshot without following symlink dirs."""

    files: dict[str, tuple[int, int]] = {}
    truncated = False
    try:
        for root, dirs, names in os.walk(workspace, followlinks=False):
            dirs[:] = [
                name
                for name in dirs
                if not (Path(root) / name).is_symlink()
                and name not in {".git", ".venv", "node_modules", "__pycache__"}
            ]
            for name in names:
                if len(files) >= _MAX_SNAPSHOT_FILES:
                    truncated = True
                    return files, truncated
                path = Path(root) / name
                try:
                    if path.is_symlink() or not path.is_file():
                        continue
                    stat = path.stat()
                    rel = path.relative_to(workspace).as_posix()
                    files[rel] = (stat.st_mtime_ns, stat.st_size)
                except (OSError, ValueError):
                    continue
    except OSError:
        truncated = True
    return files, truncated


def _safe_diff_path(path: str) -> str:
    lowered = Path(path).name.lower()
    if (
        lowered == ".env"
        or lowered.startswith(".env.")
        or lowered.endswith((".pem", ".key"))
        or lowered in {"id_rsa", "id_ed25519", ".netrc", ".pgpass"}
    ):
        return "<secret-path>"
    return path[:500]


def _workspace_diff(
    before: dict[str, tuple[int, int]],
    after: dict[str, tuple[int, int]],
    *,
    truncated: bool,
) -> dict[str, Any]:
    created = sorted(set(after) - set(before))
    deleted = sorted(set(before) - set(after))
    modified = sorted(
        path for path in set(before) & set(after) if before[path] != after[path]
    )

    def bounded(items: list[str]) -> list[str]:
        return [_safe_diff_path(path) for path in items[:_MAX_DIFF_PATHS]]

    return {
        "created": bounded(created),
        "modified": bounded(modified),
        "deleted": bounded(deleted),
        "truncated": bool(
            truncated
            or len(created) > _MAX_DIFF_PATHS
            or len(modified) > _MAX_DIFF_PATHS
            or len(deleted) > _MAX_DIFF_PATHS
        ),
    }


class BashExecutor:
    """Execute shell commands only after a one-shot Host authorization."""

    def __init__(
        self,
        host_call: HostCall,
        *,
        authorization_call: HostCall | None = None,
        generation: str | None = None,
    ) -> None:
        self._host_call = host_call
        self._authorization_call = authorization_call or host_call
        self._generation = (
            generation
            or os.environ.get("OPENAI4S_KERNEL_GENERATION")
            or f"worker:{os.getpid()}"
        )
        self._used_tokens: set[str] = set()
        self._used_token_order: deque[str] = deque()
        self._lock = threading.Lock()

    def run(
        self,
        command: str,
        *,
        timeout: float = 120,
        workdir: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(command, str) or not command.strip():
            raise RuntimeError("bash: empty command")
        if "\x00" in command:
            raise RuntimeError("bash: command contains a NUL byte")
        try:
            timeout_s = float(timeout or 120)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("bash: timeout must be a number") from exc
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise RuntimeError("bash: timeout must be finite and positive")

        # Keep the existing worker-side gates as defense-in-depth.  Host policy
        # repeats both checks before issuing the capability.
        try:
            from openai4s.security.shellcheck import precheck_command

            reason = precheck_command(command)
        except Exception:  # noqa: BLE001 — host authorization remains fail closed
            reason = None
        if reason:
            raise RuntimeError(f"bash: blocked by static safety precheck: {reason}")

        domains: list[str] = []
        try:
            from openai4s import egress

            domains = egress.command_domains(command)
        except Exception:  # noqa: BLE001
            domains = []
        if domains:
            blocked_message = self._egress_verdict(command, domains)
            if blocked_message:
                raise RuntimeError(blocked_message)

        cwd = Path.cwd()
        if workdir:
            candidate = Path(str(workdir)).expanduser()
            if not candidate.is_absolute():
                candidate = cwd / candidate
            try:
                cwd = candidate.resolve(strict=True)
            except (OSError, RuntimeError, ValueError) as exc:
                raise RuntimeError(f"bash: workdir not found: {workdir}") from exc
            if not cwd.is_dir():
                raise RuntimeError(f"bash: workdir not found: {workdir}")
        else:
            cwd = cwd.resolve(strict=True)

        worker_workspace = os.environ.get("OPENAI4S_WORKSPACE")
        challenge = secrets.token_urlsafe(24)
        binding = {
            "command": command,
            "command_sha256": command_digest(command),
            "cwd": str(cwd),
            "workspace": worker_workspace,
            "generation": self._generation,
            "challenge": challenge,
            "timeout": timeout_s,
        }
        try:
            # Skill network admission runs on the Host in authorize_bash;
            # the worker never decides whether a loaded Skill may use the
            # network and cannot widen egress by omitting that check.
            raw_capability = self._authorization_call("authorize_bash", [binding])
        except Exception as exc:  # noqa: BLE001 — legacy dispatcher: deny
            raise RuntimeError(
                "bash: host authorization unavailable; command was not executed"
            ) from exc
        capability = validate_capability(
            raw_capability,
            command=command,
            cwd=str(cwd),
            generation=self._generation,
            challenge=challenge,
        )
        token = capability["token"]
        consume_spec = {
            "token": token,
            "command_sha256": binding["command_sha256"],
            "cwd": str(cwd),
            "generation": self._generation,
            "challenge": challenge,
        }

        # Mark locally before the consuming RPC and before subprocess creation.
        # A failed consume is intentionally not retryable with the same token.
        self._mark_token_used(token)
        try:
            consumed = self._authorization_call(
                "consume_bash_authorization", [consume_spec]
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "bash: host capability could not be consumed; command was not executed"
            ) from exc
        if not isinstance(consumed, dict) or not consumed.get("ok"):
            raise _capability_error(
                consumed,
                "bash: host capability was not consumed; command was not executed",
            )

        workspace = Path(str(capability["workspace"])).resolve(strict=True)
        before, before_truncated = _workspace_snapshot(workspace)
        started = time.monotonic()
        status = "completed"
        exit_code = -1
        stdout = ""
        stderr = ""
        timeout_error: RuntimeError | None = None
        launch_error: RuntimeError | None = None
        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                # Its own group, so the deadline can reach what the shell
                # started. `subprocess.run(timeout=)` kills the shell alone:
                # `bash -c "python train.py"` lost the shell and kept the
                # python, which is the process actually holding the resources.
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            status = "launch_failed"
            stderr = str(exc)
            launch_error = RuntimeError(f"bash: failed to launch command: {exc}")
        else:
            try:
                pgid: int | None = os.getpgid(proc.pid)
            except (OSError, AttributeError):
                pgid = None
            # Drained as it is produced. `capture_output=True` held the whole
            # of both streams in worker memory before the slices below ever
            # ran, so the cap described what the caller saw and not what was
            # allocated.
            out_sink = _BoundedTail(_STDOUT_BUDGET_CHARS)
            err_sink = _BoundedTail(_STDERR_BUDGET_CHARS)
            drains = [
                threading.Thread(
                    target=_drain, args=(proc.stdout, out_sink), daemon=True
                ),
                threading.Thread(
                    target=_drain, args=(proc.stderr, err_sink), daemon=True
                ),
            ]
            for thread in drains:
                thread.start()
            # The deadline is on the GROUP, not the leader. `proc.wait()`
            # answers about the leader alone -- `stop_process_group`'s own
            # docstring says so, two files away -- and this call site believed
            # it. Measured: `( sleep 6; ... ) & exit 0` under a 2s deadline
            # returned from `proc.wait` in 0.00s with no TimeoutExpired, so the
            # group was never stopped, the drain threads stayed blocked on
            # pipes the survivor held (one leak per call), the work finished
            # six seconds later, and `host.bash` reported `completed` rc=0 --
            # a terminal state for a job that was still running.
            #
            # Spawning under `start_new_session=True` was already done for
            # exactly this, and the comment above says why. Only the wait was
            # never switched to match.
            deadline = time.monotonic() + timeout_s
            try:
                proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                pass
            remaining = deadline - time.monotonic()
            if remaining > 0 and group_alive(pgid):
                await_group_exit(proc, pgid, remaining)
            if proc.poll() is None or group_alive(pgid):
                status = "timed_out"
                stop_process_group(proc, pgid)
                timeout_error = RuntimeError(f"bash: timed out after {timeout_s:g}s")
            for thread in drains:
                # The pipes are closed once the group is gone, so this is a
                # join on threads that are already finishing, not a wait on
                # the command.
                thread.join(timeout=5.0)
            exit_code = int(proc.returncode if proc.returncode is not None else -1)
            stdout = out_sink.value()
            stderr = err_sink.value()
        duration_ms = int((time.monotonic() - started) * 1000)
        after, after_truncated = _workspace_snapshot(workspace)
        result_spec = {
            **consume_spec,
            "status": status,
            "exit_code": exit_code,
            # Already bounded by `_BoundedTail`, so the slice was a no-op that
            # spelled the same two budgets a second time, in digits.
            "stdout": stdout[-_STDOUT_BUDGET_CHARS:],
            "stderr": stderr[-_STDERR_BUDGET_CHARS:],
            # The cut, stated. Without these the audit below recorded
            # `chars: 30000` and a sha256 of the *tail* as the command's stdout
            # digest -- a record indistinguishable from a command that printed
            # thirty thousand characters, which is a wrong answer rather than a
            # missing one. `workspace_diff` two lines down has reported its own
            # truncation all along; the rule was stated here and applied only to
            # the member that had not been cut.
            **channel_counters(
                prefix="stdout_",
                unit="chars",
                seen=out_sink.seen,
                retained=out_sink.retained,
            ),
            **channel_counters(
                prefix="stderr_",
                unit="chars",
                seen=err_sink.seen,
                retained=err_sink.retained,
            ),
            "duration_ms": duration_ms,
            "workspace_diff": _workspace_diff(
                before,
                after,
                truncated=before_truncated or after_truncated,
            ),
        }
        audit_recorded = True
        try:
            record = self._authorization_call("record_bash_result", [result_spec])
            audit_recorded = bool(isinstance(record, dict) and record.get("ok"))
        except Exception:  # noqa: BLE001 — the process has already run
            audit_recorded = False

        if timeout_error is not None:
            raise timeout_error
        if launch_error is not None:
            raise launch_error
        return {
            "exit_code": exit_code,
            "stdout": stdout[-_STDOUT_BUDGET_CHARS:],
            "stderr": stderr[-_STDERR_BUDGET_CHARS:],
            # The agent that ran the command is the reader with the most to
            # lose: it reasons over this text and, told nothing, reasons over a
            # tail as though it were the whole output.
            **channel_counters(
                prefix="stdout_",
                unit="chars",
                seen=out_sink.seen,
                retained=out_sink.retained,
            ),
            **channel_counters(
                prefix="stderr_",
                unit="chars",
                seen=err_sink.seen,
                retained=err_sink.retained,
            ),
            "workdir": str(cwd),
            "duration_ms": duration_ms,
            "workspace_diff": result_spec["workspace_diff"],
            "audit_recorded": audit_recorded,
        }

    def _egress_verdict(self, command: str, domains: list[str]) -> str | None:
        verdict = None
        try:
            verdict = self._host_call("egress_check", [{"domains": domains}])
        except Exception:  # noqa: BLE001 — authorization repeats it host-side
            verdict = None
        if isinstance(verdict, dict) and verdict.get("blocked"):
            return verdict.get("message") or (
                f"bash: domain {verdict['blocked']} is outside the egress allowlist"
            )
        if verdict is None:
            try:
                from openai4s import egress

                blocked = egress.scan_command(command)
                if blocked is not None:
                    return egress.blocked_error(blocked).get("error")
            except Exception:  # noqa: BLE001 — authorization remains fail closed
                return None
        return None

    def _mark_token_used(self, token: str) -> None:
        """Remember a consumed token in a bounded local replay window."""

        with self._lock:
            if token in self._used_tokens:
                raise RuntimeError("bash: host capability token replayed")
            if len(self._used_token_order) >= _MAX_RECENT_TOKENS:
                oldest = self._used_token_order.popleft()
                self._used_tokens.discard(oldest)
            self._used_tokens.add(token)
            self._used_token_order.append(token)

    @staticmethod
    def _coerce_output(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)


__all__ = ["BashExecutor", "HostCall", "validate_capability"]
