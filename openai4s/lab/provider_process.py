"""A private, sandboxed provider process per DevicePort session (stdlib only)."""

from __future__ import annotations

import functools
import os
import re
import secrets
import shutil
import threading
from dataclasses import dataclass, replace
from pathlib import Path

from openai4s.kernel.environment import build_kernel_environment
from openai4s.lab.manifest import load_descriptor
from openai4s.lab.models import (
    DeviceDescriptor,
    Dispatch,
    ErrorCode,
    LabError,
    Receipt,
    SessionOpened,
    SessionOpenRequest,
    StopResult,
)
from openai4s.security.sandbox import create_kernel_sandbox
from openai4s_lab_provider.client import (
    DEFAULT_TIMEOUTS,
    ProviderClient,
    ProviderError,
    ProviderGone,
    ProviderProtocolError,
    ProviderRequestError,
    ProviderTimeout,
)

# What a provider may report in an error frame (CONTRACT §8.3/§9). These keep
# the session; the fatal ones end it, so alive() is False as the table says.
# Any other code - one only the host may raise (persistence_unavailable,
# stale_revision, approval_denied...) or an unknown one - is not the provider's
# to claim: a protocol violation, and the process is killed.
_PROVIDER_SOFT = frozenset(
    {
        ErrorCode.INVALID_PARAMETERS,
        ErrorCode.OUTCOME_UNKNOWN,
        ErrorCode.DEVICE_NOT_FOUND,
    }
)
_PROVIDER_FATAL = {
    ErrorCode.PROVIDER_UNAVAILABLE: ErrorCode.PROVIDER_UNAVAILABLE,
    ErrorCode.ADAPTER_MISMATCH: ErrorCode.ADAPTER_MISMATCH,
    # "No open session" from the provider is a missing session (§9 row 4).
    ErrorCode.RUN_NOT_FOUND: ErrorCode.PROVIDER_UNAVAILABLE,
}


# A run directory names the process that owns it, so whatever a killed daemon
# left behind can be told apart from a live `lab smoke` or doctor run sharing
# this root. Names this process still owns are tracked: a restarted container
# gets its predecessor's PID.
_RUN_DIR = re.compile(r"labrun-([0-9]+)-[0-9a-f]{12}\Z")
_live_runs: set[str] = set()
_live_runs_lock = threading.Lock()


def _owner_alive(pid):
    if os.name == "nt":
        # os.kill(pid, 0) terminates the process there; never reclaim.
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def sweep_orphaned_runs(runs_root):
    """Remove run directories and cache links whose owning process is gone."""
    runs_root = Path(runs_root)
    cache_root = runs_root.parent / "cache"
    names = set()
    for directory in (runs_root, cache_root):
        try:
            names.update(entry.name for entry in directory.iterdir())
        except OSError:
            pass
    for name in names:
        match = _RUN_DIR.match(name)
        if match is None:
            continue
        pid = int(match.group(1))
        with _live_runs_lock:
            mine = name in _live_runs
        if mine or (pid != os.getpid() and _owner_alive(pid)):
            continue
        try:
            (cache_root / name).unlink(missing_ok=True)
        except OSError:
            pass
        shutil.rmtree(runs_root / name, ignore_errors=True)


def _boundary(method):
    @functools.wraps(method)
    def guarded(self, *args, **kwargs):
        with self._lock:
            try:
                return method(self, *args, **kwargs)
            except LabError:
                raise
            except Exception:
                # Neither caller values nor unexpected exception messages are
                # safe public diagnostics (they may contain simulator truth).
                raise LabError(
                    ErrorCode.PROVIDER_UNAVAILABLE, "Provider operation unavailable"
                ) from None

    return guarded


def _safe_stderr(client):
    """Keep only the provider's value-free diagnostic grammar, bounded again.

    Arbitrary native-library stderr is not publishable, even in an error. The
    standalone client retains its private 64 KiB tail; only stack locations and
    the two fixed failure summaries can cross this host boundary.
    """
    lines = []
    for line in client.stderr_tail()[-2048:].splitlines():
        if re.fullmatch(
            r"(?:Backend traceback \(values redacted\):|  [A-Za-z_][A-Za-z_0-9]*:[0-9]+|"
            r"[A-Za-z_][A-Za-z_0-9]*: (?:backend operation failed|provider startup or service failed))",
            line,
        ):
            lines.append(line)
        else:
            lines.append("[provider diagnostic redacted]")
    return "\n".join(lines)[-1000:]


@dataclass
class _Session:
    client: ProviderClient
    sandbox: object
    run_dir: Path
    cache_link: Path


class ProviderProcessDevice:
    def __init__(
        self,
        device_id,
        backend,
        *,
        python,
        package_dir,
        runs_root,
        sandbox_mode,
        timeouts=DEFAULT_TIMEOUTS,
    ):
        self.device_id = device_id
        self.backend = backend
        self.python = str(python) if python else None
        self.package_dir = Path(package_dir).absolute()
        self.runs_root = Path(runs_root).absolute()
        self.sandbox_mode = sandbox_mode
        self.timeouts = dict(DEFAULT_TIMEOUTS, **timeouts)
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.RLock()
        self.sandbox_status = {
            "mode": sandbox_mode,
            "state": "not_started",
            "enforced": False,
        }

    def _new(self):
        if not self.python:
            raise LabError(
                ErrorCode.PROVIDER_UNAVAILABLE, "Run openai4s lab setup chemgymrl first"
            )
        ident = "labsession-" + secrets.token_hex(6)
        sweep_orphaned_runs(self.runs_root)
        run_dir = self.runs_root / f"labrun-{os.getpid()}-{secrets.token_hex(6)}"
        cache_link = self.runs_root.parent / "cache" / run_dir.name
        sandbox = client = None
        with _live_runs_lock:
            _live_runs.add(run_dir.name)
        try:
            run_dir.mkdir(parents=True, mode=0o700)
            (run_dir / "cache" / "numba").mkdir(parents=True)
            (run_dir / "cache" / "matplotlib").mkdir()
            cache_link.parent.mkdir(parents=True, exist_ok=True)
            # Cache names live under lab/cache, but resolve INSIDE this run's
            # writable sandbox. Never widen the boundary to all lab data.
            cache_link.symlink_to(run_dir / "cache", target_is_directory=True)
            sandbox = create_kernel_sandbox(
                run_dir, mode=self.sandbox_mode, allow_raw_network=False
            )
            self.sandbox_status = sandbox.status.to_dict()
            if self.sandbox_mode == "enforce" and not sandbox.status.enforced:
                raise LabError(
                    ErrorCode.PROVIDER_UNAVAILABLE,
                    "Required provider sandbox unavailable",
                    {"reason": "sandbox"},
                )
            argv = sandbox.wrap_command(
                [
                    self.python,
                    "-I",
                    # Never write bytecode into the (host) package directory,
                    # including when an auto sandbox degraded.
                    "-B",
                    str(self.package_dir / "__main__.py"),
                    "--backend",
                    self.backend,
                ]
            )
            env = build_kernel_environment(
                mode="lab-provider", cwd=str(run_dir), interpreter=self.python
            )
            env.pop("VIRTUAL_ENV", None)
            env.pop("PYTHONPATH", None)
            env = sandbox.apply_environment(env)
            env.update(
                MPLBACKEND="Agg",
                NUMBA_CACHE_DIR=str(cache_link / "numba"),
                MPLCONFIGDIR=str(cache_link / "matplotlib"),
                PYTHONNOUSERSITE="1",
            )
            client = ProviderClient(
                argv, env=env, cwd=run_dir, pass_fds=sandbox.popen_pass_fds()
            )
            self._sessions[ident] = _Session(client, sandbox, run_dir, cache_link)
            client.start()
            hello = self._request(ident, "hello", {})
            if (
                not isinstance(hello, dict)
                or type(hello.get("protocol")) is not int
                or hello.get("protocol") != 1
                or hello.get("backend") != self.backend
            ):
                self._invalid(ident)
            return ident
        except Exception:
            if ident in self._sessions:
                self._discard(ident)
            else:
                if sandbox is not None:
                    sandbox.close()
                cache_link.unlink(missing_ok=True)
                shutil.rmtree(run_dir, ignore_errors=True)
                with _live_runs_lock:
                    _live_runs.discard(run_dir.name)
            raise

    def _session(self, ident):
        session = self._sessions.get(ident)
        if session is None:
            raise LabError(
                ErrorCode.PROVIDER_UNAVAILABLE, "Provider session unavailable"
            )
        return session

    def _discard(self, ident):
        session = self._sessions.pop(ident, None)
        if session is None:
            return
        try:
            session.client.close(timeout=self.timeouts["close"])
        finally:
            try:
                session.sandbox.close()
            finally:
                session.cache_link.unlink(missing_ok=True)
                try:
                    shutil.rmtree(session.run_dir)
                finally:
                    # Whatever could not be removed is reclaimed by a sweep.
                    with _live_runs_lock:
                        _live_runs.discard(session.run_dir.name)

    def _invalid(self, ident, code=ErrorCode.PROVIDER_PROTOCOL_ERROR):
        self._discard(ident)
        raise LabError(code, "Provider returned an invalid or mismatched result")

    def _request(self, ident, op, args):
        client = self._session(ident).client
        try:
            return client.request(op, args, timeout=self.timeouts[op])
        except ProviderError as exc:
            fatal = isinstance(
                exc, (ProviderTimeout, ProviderGone, ProviderProtocolError)
            )
            try:
                code = ErrorCode(exc.code)
            except ValueError:
                code, fatal = ErrorCode.PROVIDER_PROTOCOL_ERROR, True
            if not fatal and code not in _PROVIDER_SOFT:
                code = _PROVIDER_FATAL.get(code, ErrorCode.PROVIDER_PROTOCOL_ERROR)
                fatal = True
            detail = _safe_stderr(client)
            if fatal:
                self._discard(ident)
            raise LabError(
                code,
                f"Provider {op} failed ({code.value})"
                + (f": {detail}" if detail else ""),
            ) from None
        except ProviderRequestError:
            # Encoding failed before a byte was sent. Keep the healthy session.
            raise LabError(
                ErrorCode.INVALID_PARAMETERS, "Provider request cannot be encoded"
            ) from None
        except Exception:
            self._discard(ident)
            raise LabError(
                ErrorCode.PROVIDER_UNAVAILABLE, "Provider transport unavailable"
            ) from None

    def _decode(self, ident, loader, result):
        try:
            return loader(result)
        except LabError as exc:
            self._invalid(
                ident,
                (
                    ErrorCode.ADAPTER_MISMATCH
                    if exc.code == ErrorCode.ADAPTER_MISMATCH
                    else ErrorCode.PROVIDER_PROTOCOL_ERROR
                ),
            )
        except Exception:
            self._invalid(ident)

    def _descriptor(self, ident, descriptor, profile):
        if (descriptor.device_id, descriptor.backend, descriptor.profile) != (
            self.device_id,
            self.backend,
            profile,
        ):
            self._invalid(ident)
        status = self._session(ident).sandbox.status
        return replace(
            descriptor,
            assumptions=descriptor.assumptions
            + (
                f"provider sandbox: mode={status.mode}; state={status.state}; network={status.network_policy}",
            ),
        )

    @_boundary
    def describe(self, profile: str) -> DeviceDescriptor:
        ident = self._new()
        try:
            descriptor = self._decode(
                ident,
                load_descriptor,
                self._request(ident, "describe", {"profile": profile}),
            )
            return self._descriptor(ident, descriptor, profile)
        finally:
            self._discard(ident)

    @_boundary
    def open(self, request: SessionOpenRequest) -> SessionOpened:
        payload = request.to_dict()
        ident = self._new()
        try:
            opened = self._decode(
                ident, SessionOpened.from_dict, self._request(ident, "open", payload)
            )
            descriptor = self._decode(
                ident, load_descriptor, opened.descriptor.to_dict()
            )
            if descriptor.capability_revision != request.expected_capability_revision:
                self._invalid(ident, ErrorCode.ADAPTER_MISMATCH)
            return replace(
                opened,
                session_id=ident,
                descriptor=self._descriptor(ident, descriptor, request.profile),
            )
        except Exception:
            self._discard(ident)
            raise

    def _receipt(self, ident, result, command_id):
        receipt = self._decode(ident, Receipt.from_dict, result)
        if receipt.provider_command_id != command_id:
            self._invalid(ident)
        return receipt

    @_boundary
    def execute(self, session_id: str, dispatch: Dispatch) -> Receipt:
        return self._receipt(
            session_id,
            self._request(session_id, "execute", dispatch.to_dict()),
            dispatch.provider_command_id,
        )

    @_boundary
    def query(self, session_id: str, provider_command_id: str) -> Receipt | None:
        result = self._request(
            session_id, "query", {"provider_command_id": provider_command_id}
        )
        if result == {"known": False} and type(result.get("known")) is bool:
            return None
        if (
            result == {"known": True, "receipt": None}
            and type(result.get("known")) is bool
        ):
            raise LabError(
                ErrorCode.OUTCOME_UNKNOWN,
                "Provider received the command but no longer retains its receipt",
            )
        return self._receipt(session_id, result, provider_command_id)

    @_boundary
    def stop(self, session_id: str, reason: str) -> StopResult:
        return self._decode(
            session_id,
            StopResult.from_dict,
            self._request(session_id, "stop", {"reason": reason}),
        )

    def close(self, session_id: str) -> None:
        # Every request holds the device lock for its whole exchange (execute
        # may wait 60 s). Close must not inherit that wait: the manager's stop
        # and daemon shutdown rely on it. Kill the group first; the request
        # that owns the process sees EOF, reaps it and fails as lost.
        if not self._lock.acquire(blocking=False):
            session = self._sessions.get(session_id)
            if session is not None:
                session.client.abort()
            self._lock.acquire()
        try:
            self._discard(session_id)
        except LabError:
            raise
        except Exception:
            raise LabError(
                ErrorCode.PROVIDER_UNAVAILABLE, "Provider operation unavailable"
            ) from None
        finally:
            self._lock.release()

    def alive(self, session_id: str) -> bool:
        with self._lock:
            try:
                session = self._sessions.get(session_id)
                return session is not None and session.client.alive()
            except Exception:
                return False
