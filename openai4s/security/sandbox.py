"""Operating-system sandbox adapter for scientific kernel subprocesses.

The application-level safety classifier and path checks are useful policy
layers, but they are not a process isolation boundary.  This module adds that
boundary at the one place where a Python/R worker is created:

* macOS uses ``sandbox-exec`` (Seatbelt),
* Linux uses ``bwrap`` (bubblewrap),
* other platforms report an explicit unsupported status.

The default policy is intentionally small and auditable.  The worker may read
the host filesystem (its interpreter and scientific packages live there), but
may write only its session workspace and a newly-created private temporary
directory.  Raw network access is denied; Host RPC web tools run in the daemon
and therefore remain available.  ``OPENAI4S_KERNEL_ALLOW_RAW_NETWORK=1`` is a
trusted, host-global compatibility escape hatch.

``OPENAI4S_KERNEL_SANDBOX`` accepts:

``auto`` (default)
    Enforce a sandbox after detection and a real startup self-test.  If the OS
    facility is missing or unusable, continue unsandboxed with a high-visibility
    warning and a machine-readable degraded status.
``enforce``
    The same detection and self-test, but fail closed before a worker starts.
``off``
    Explicitly disable the OS boundary.  This is visible in status and never
    happens implicitly.

The adapter is pure stdlib.  Detection and command execution are injectable so
the supported paths can be tested even inside a parent sandbox that forbids
nested sandbox creation.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_SANDBOX_ENV = "OPENAI4S_KERNEL_SANDBOX"
_RAW_NETWORK_ENV = "OPENAI4S_KERNEL_ALLOW_RAW_NETWORK"
_VALID_MODES = frozenset({"auto", "enforce", "off"})

Runner = Callable[..., Any]
Which = Callable[[str], str | None]


class SandboxError(RuntimeError):
    """Base error for a malformed or unavailable kernel sandbox."""


class SandboxConfigurationError(SandboxError):
    """Raised for an invalid trusted global sandbox setting."""


class SandboxUnavailableError(SandboxError):
    """Raised when ``enforce`` cannot establish the requested boundary."""


@dataclass(frozen=True)
class SandboxStatus:
    """Serializable truth about the boundary around one Kernel instance."""

    mode: str
    state: str
    backend: str | None
    enforced: bool
    self_test_passed: bool | None
    network_policy: str
    workspace: str
    temp_dir: str | None
    detail: str
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_bool(value: str | None, *, name: str, default: bool = False) -> bool:
    if value is None or not str(value).strip():
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise SandboxConfigurationError(
        f"{name} must be one of 1/0, true/false, yes/no, or on/off"
    )


def _sandbox_mode(value: str | None) -> str:
    mode = str(value if value is not None else os.environ.get(_SANDBOX_ENV, "auto"))
    mode = mode.strip().lower() or "auto"
    if mode not in _VALID_MODES:
        allowed = ", ".join(sorted(_VALID_MODES))
        raise SandboxConfigurationError(f"{_SANDBOX_ENV} must be one of: {allowed}")
    return mode


def _canonical_dir(path: str | os.PathLike[str]) -> Path:
    resolved = Path(path).expanduser().resolve(strict=False)
    if "\x00" in str(resolved):
        raise SandboxConfigurationError("sandbox paths cannot contain NUL bytes")
    if not resolved.is_dir():
        raise SandboxConfigurationError(
            f"kernel sandbox directory does not exist: {resolved}"
        )
    return resolved


def _seatbelt_string(value: str | os.PathLike[str]) -> str:
    """Quote a path as one non-injectable Seatbelt/Scheme string literal."""

    text = str(value)
    if "\x00" in text:
        raise SandboxConfigurationError("Seatbelt paths cannot contain NUL bytes")
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


_DENY_READ_KINDS = ("literal", "prefix", "subpath")


def _default_secret_read_denials(
    workspace: str | os.PathLike[str],
) -> tuple[tuple[str, str], ...]:
    """Concrete secret files a cell must never read.

    General filesystem reads stay allowed (legit science reads system/data
    files); only these specific credential locations are denied.  Read fresh
    from the environment so the per-test ``OPENAI4S_DATA_DIR`` redirect is
    honoured — never the ``config`` singleton.
    """

    entries: list[tuple[str, str]] = []
    # The daemon SQLite DB: its settings/connectors tables hold the LLM API
    # keys and MCP tokens that host.query's QUERY_DENYLIST deliberately hides.
    data_env = os.environ.get("OPENAI4S_DATA_DIR")
    data_dir = Path(data_env).expanduser() if data_env else Path.home() / ".openai4s"
    entries.append(("prefix", str(data_dir / "openai4s.db")))
    # The daemon's access token, which is a sibling of the DB rather than a
    # prefix of it -- so the entry above never covered it. Verified under an
    # enforced sandbox: the DB was blocked and this file was read. It is the
    # credential that gates the whole HTTP API, so a cell holding it can drive
    # every route the daemon serves, including the ones that execute code.
    entries.append(("literal", str(data_dir / "access-token")))
    # Shares carry relay credentials, and the per-share tokens are what make a
    # read-only snapshot reachable from outside this machine.
    entries.append(("subpath", str(data_dir / "shares")))
    # The worker-bootstrap signing secret and its replay fence -- siblings of
    # the DB, exactly like `access-token` above, and missed for the same
    # reason. `BootstrapAuthority.verify` is pure HMAC plus expiry, epoch and
    # nonce: it never asks whether the allocation exists, so anyone holding
    # these 32 bytes can mint a credential for an arbitrary
    # (allocation_id, epoch, rank) and have `WorkerGateway` hand them an
    # `OutboundTcpTransport` -- which carries, per its own module docstring,
    # "arbitrary Host RPC" inside another tenant's session. The fence is
    # denied alongside it because a cell that can *write* it un-burns every
    # consumed nonce and drops every epoch a recovery fenced off.
    # Imported rather than spelled, so a rename cannot reopen the read.
    from openai4s.orchestration.bootstrap import SECRET_FILENAME, STATE_FILENAME

    entries.append(("literal", str(data_dir / SECRET_FILENAME)))
    entries.append(("literal", str(data_dir / STATE_FILENAME)))
    # The per-allocation credential files themselves, written into each
    # workload's runtime directory. Same credential, one indirection away.
    entries.append(("subpath", str(data_dir / "cluster-workspaces")))
    # The git-ignored daemon .env, discovered the same way config._load_dotenv
    # walks for it.
    try:
        here = Path(__file__).resolve()
        for base in (here.parent, *here.parents):
            candidate = base / ".env"
            if candidate.is_file():
                entries.append(("literal", str(candidate)))
                break
    except OSError:
        pass
    # Ambient user credentials that live outside the workspace.
    home = Path.home()
    entries.append(("subpath", str(home / ".ssh")))
    entries.append(("literal", str(home / ".netrc")))
    entries.append(("literal", str(home / ".pgpass")))
    # Canonicalize (follow symlinks): the OS sandbox matches on the real path,
    # e.g. macOS resolves /var -> /private/var, so an unresolved prefix would
    # never match the file the cell actually opens.
    resolved: list[tuple[str, str]] = []
    for kind, path in entries:
        try:
            resolved.append((kind, str(Path(path).resolve())))
        except OSError:
            resolved.append((kind, path))
    try:
        ws = str(Path(workspace).resolve())
    except OSError:
        ws = str(workspace)
    # Never deny a path that IS or CONTAINS the workspace, or the kernel's own
    # boundary would be unreadable under a pathological data_dir/workspace layout.
    return tuple(
        (kind, path)
        for kind, path in resolved
        if not (path == ws or ws.startswith(path + os.sep))
    )


def build_seatbelt_profile(
    workspace: str | os.PathLike[str],
    temp_dir: str | os.PathLike[str],
    *,
    allow_raw_network: bool = False,
    deny_read: Sequence[tuple[str, str]] = (),
) -> str:
    """Return the complete Seatbelt profile for a kernel worker.

    ``allow default`` keeps interpreter/runtime IPC compatible while the two
    security-sensitive resource classes are replaced with explicit policy.
    This mirrors Apple's own service profiles: a broad file-write deny followed
    by narrower path allows.  ``deny_read`` appends targeted ``file-read*``
    denies (SBPL is last-match-wins, so they beat the leading ``allow default``).
    """

    workspace_q = _seatbelt_string(workspace)
    temp_q = _seatbelt_string(temp_dir)
    lines = [
        "(version 1)",
        "(allow default)",
        "(deny file-write*)",
        "(allow file-write*",
        f"    (subpath {workspace_q})",
        f"    (subpath {temp_q})",
        '    (literal "/dev/null")',
        '    (literal "/dev/zero")',
        # The R worker opens its already-inherited protocol output descriptor
        # through this fd path.  Seatbelt otherwise treats that open as a new
        # filesystem write and blocks the worker before its first frame.  This
        # grants no path outside the inherited pipe and keeps stdout/stderr
        # separate from the protocol channel.
        '    (literal "/dev/fd/3"))',
    ]
    if not allow_raw_network:
        lines.insert(2, "(deny network*)")
    for kind, path in deny_read:
        if kind not in _DENY_READ_KINDS:
            raise SandboxConfigurationError(f"unknown deny-read kind: {kind!r}")
        lines.append(f"(deny file-read* ({kind} {_seatbelt_string(path)}))")
    lines.extend(_KEYCHAIN_DENIES)
    return "\n".join(lines) + "\n"


#: Cutting the cell off from the macOS keychain.
#:
#: `OPENAI4S_SECRET_STORE` defaults to the keychain on macOS, so the LLM API
#: key lives there. Under an enforced sandbox a cell could still run
#: `/usr/bin/security` and reach it: verified before this existed, with
#: `security list-keychains` returning the user's keychain path from inside the
#: sandbox.
#:
#: Denying the keychain *files* alone does not do it. `securityd` is a separate
#: daemon that opens them on the caller's behalf, so the file rules never
#: apply to it; the mach-lookup denies are what actually close the door. The
#: file denies stay as well, because the keychain database is also worth
#: something to an attacker who can read it directly and attack it offline.
#:
#: The obvious worry is TLS: on macOS the Security framework is what validates
#: certificates, so cutting off securityd might break every HTTPS fetch a
#: science cell makes. Measured rather than assumed — under exactly these
#: rules, `security list-keychains` fails while `curl https://example.com` and
#: `urllib.request.urlopen` both return 200. Certificate validation does not go
#: through the paths denied here.
_KEYCHAIN_DENIES: tuple[str, ...] = (
    '(deny mach-lookup (global-name "com.apple.SecurityServer"))',
    '(deny mach-lookup (global-name "com.apple.securityd.xpc"))',
    '(deny file-read* (subpath "/Library/Keychains"))',
    '(deny file-read* (regex #"^/Users/[^/]+/Library/Keychains"))',
)


def wrap_seatbelt_command(
    command: Sequence[str],
    *,
    executable: str,
    workspace: str | os.PathLike[str],
    temp_dir: str | os.PathLike[str],
    allow_raw_network: bool = False,
    deny_read: Sequence[tuple[str, str]] = (),
) -> list[str]:
    profile = build_seatbelt_profile(
        workspace, temp_dir, allow_raw_network=allow_raw_network, deny_read=deny_read
    )
    return [str(executable), "-p", profile, *[str(part) for part in command]]


def _bwrap_read_masks(deny_read: Sequence[tuple[str, str]]) -> list[str]:
    """Mask concrete secret paths so a bwrap cell cannot read them.

    bwrap cannot deny a subpath under the read-only root bind, so mask each
    existing target instead: a directory with an empty ``--tmpfs`` and a file
    with ``--ro-bind /dev/null``.  Non-existent targets are skipped (bwrap
    cannot create a mount point under the read-only root).
    """

    masks: list[str] = []
    seen: set[str] = set()
    for kind, path in deny_read:
        targets = [path]
        if kind == "prefix":
            targets = [path, path + "-wal", path + "-shm", path + "-journal"]
        for target in targets:
            if target in seen:
                continue
            seen.add(target)
            if os.path.isdir(target):
                masks.extend(["--tmpfs", target])
            elif os.path.exists(target):
                masks.extend(["--ro-bind", "/dev/null", target])
    return masks


def _bwrap_daemon_environ_mask() -> list[str]:
    """Hide the daemon's own environment from a cell that shares its PID namespace.

    The sandbox deliberately keeps the host PID namespace. bubblewrap remains
    as the process returned by ``Popen`` and starts the worker as its direct
    child; ``KernelSandbox.send_interrupt()`` pins and validates that child
    before ``Kernel.interrupt()`` delivers SIGINT. The cost is that
    `/proc` still shows every host process, so a Linux cell can read
    `/proc/<daemon>/environ` — and that is where the daemon's API keys live,
    since the child's own environment is allowlisted clean.

    Closing the whole PID-namespace gap needs `--unshare-pid` plus an explicit
    child-pid channel. This closes the part that carries the credentials without
    changing the current process tree: one file, bound over with /dev/null.

    The mount order and interrupt path are both covered by real Linux/WSL tests.
    The wider exposure — that a cell can still see other processes exist — is
    recorded in the ledger rather than fixed.
    """
    environ = f"/proc/{os.getpid()}/environ"
    if not os.path.exists(environ):  # not Linux, or /proc unavailable
        return []
    return ["--ro-bind", "/dev/null", environ]


def wrap_bwrap_command(
    command: Sequence[str],
    *,
    executable: str,
    workspace: str | os.PathLike[str],
    temp_dir: str | os.PathLike[str],
    allow_raw_network: bool = False,
    deny_read: Sequence[tuple[str, str]] = (),
) -> list[str]:
    """Wrap ``command`` in a read-only-root bubblewrap mount namespace."""

    workspace_s = str(workspace)
    temp_s = str(temp_dir)
    wrapped = [
        str(executable),
        "--die-with-parent",
        "--new-session",
        # Deliberately keep the host PID namespace. bubblewrap remains the
        # Popen process and supervises one direct worker child even without a
        # PID namespace. KernelSandbox.send_interrupt() validates the child and
        # pins its identity with a pidfd so SIGINT reaches user code without
        # killing the wrapper. PID-ns isolation is deferred to a change with an
        # explicit child-pid channel; the masks below already block credential
        # payloads.
        "--unshare-ipc",
        "--unshare-uts",
    ]
    if not allow_raw_network:
        wrapped.append("--unshare-net")
    wrapped.extend(
        [
            "--ro-bind",
            "/",
            "/",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--bind",
            workspace_s,
            workspace_s,
            "--bind",
            temp_s,
            temp_s,
        ]
    )
    # Mask secret paths after the workspace/temp binds (so a denial still wins
    # when the workspace nests a secret) and before --chdir/--.
    wrapped.extend(_bwrap_read_masks(deny_read))
    # ...and after `--proc /proc` above, which is what makes this reachable:
    # the mount would otherwise replace whatever was bound over it.
    wrapped.extend(_bwrap_daemon_environ_mask())
    wrapped.extend(
        [
            "--chdir",
            workspace_s,
            "--",
            *[str(part) for part in command],
        ]
    )
    return wrapped


_SELF_TEST_CODE = r"""
import json
import socket
import sys
from pathlib import Path

workspace_file, temp_file, outside_file, expect_network_blocked = sys.argv[1:5]

def can_write(name):
    try:
        path = Path(name)
        path.write_text("openai4s-sandbox-self-test", encoding="utf-8")
        path.unlink()
        return True
    except OSError:
        return False

network_blocked = None
if expect_network_blocked == "1":
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP connect only selects a route; it sends no packet.  A private
        # bubblewrap network namespace has no route, and Seatbelt rejects it.
        sock.connect(("198.51.100.1", 9))
        network_blocked = False
    except OSError:
        network_blocked = True
    finally:
        sock.close()

checks = {
    "workspace_write": can_write(workspace_file),
    "temp_write": can_write(temp_file),
    "outside_write_blocked": not can_write(outside_file),
    "network_blocked": network_blocked,
}
ok = all(
    value is True
    for key, value in checks.items()
    if key != "network_blocked" or expect_network_blocked == "1"
)
print(json.dumps({"ok": ok, "checks": checks}, sort_keys=True))
raise SystemExit(0 if ok else 23)
""".strip()


def _default_runner(command: Sequence[str], **kwargs: Any) -> Any:
    return subprocess.run(command, **kwargs)


_failed_self_tests: dict[tuple[str, str, bool], str] = {}
_self_test_lock = threading.Lock()
_warned_details: set[str] = set()
_warning_lock = threading.Lock()


def _warn_once(message: str) -> None:
    with _warning_lock:
        if message in _warned_details:
            return
        _warned_details.add(message)
    warnings.warn(message, RuntimeWarning, stacklevel=3)


def _bounded_diagnostic(value: object, limit: int = 600) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _globally_unavailable(detail: str) -> bool:
    """Whether a self-test failure is independent of workspace policy.

    Cache only facility-level failures.  A read-only workspace or a malformed
    path may be specific to one session and must not disable sandbox attempts
    for every later session in the daemon.
    """

    lowered = detail.lower()
    return any(
        marker in lowered
        for marker in (
            "operation not permitted",
            "user namespace is not allowed",
            "user namespaces are not enabled",
            "no permissions to create a new namespace",
            "creating new namespace failed",
        )
    )


def _detect_backend(
    *, platform_name: str, which: Which
) -> tuple[str | None, str | None, str]:
    if platform_name == "darwin":
        executable = which("sandbox-exec")
        if executable:
            return "seatbelt", str(executable), "sandbox-exec detected"
        return None, None, "macOS sandbox-exec was not found"
    if platform_name.startswith("linux"):
        executable = which("bwrap")
        if executable:
            return "bubblewrap", str(executable), "bubblewrap detected"
        return None, None, "Linux bubblewrap (bwrap) was not found"
    return None, None, f"OS sandbox is unsupported on platform {platform_name!r}"


def _allocate_outside_probe(workspace: Path, temp_dir: Path) -> Path:
    """Create a writable host directory outside both allowed write roots."""

    candidates = [Path(tempfile.gettempdir()), workspace.parent, Path.home()]
    failures: list[str] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            parent = candidate.expanduser().resolve(strict=False)
        except OSError as exc:
            failures.append(str(exc))
            continue
        if parent in seen:
            continue
        seen.add(parent)
        if parent == workspace or parent.is_relative_to(workspace):
            continue
        if parent == temp_dir or parent.is_relative_to(temp_dir):
            continue
        try:
            return Path(
                tempfile.mkdtemp(prefix="openai4s-sandbox-deny-", dir=str(parent))
            ).resolve()
        except OSError as exc:
            failures.append(f"{parent}: {exc}")
    detail = "; ".join(failures) or "no path exists outside the allowed roots"
    raise OSError(f"could not allocate an outside-write probe: {detail}")


class KernelSandbox:
    """One Kernel's immutable sandbox policy and owned temporary directory."""

    def __init__(
        self,
        *,
        status: SandboxStatus,
        executable: str | None = None,
        temp_dir: str | None = None,
        allow_raw_network: bool = False,
        owns_temp_dir: bool = False,
        deny_read: Sequence[tuple[str, str]] = (),
    ) -> None:
        self.status = status
        self._executable = executable
        self._temp_dir = temp_dir
        self._allow_raw_network = allow_raw_network
        self._owns_temp_dir = owns_temp_dir
        self._deny_read = tuple(deny_read)
        self._closed = False
        self._interrupt_gap_reported = False

    def wrap_command(self, command: Sequence[str]) -> list[str]:
        argv = [str(part) for part in command]
        if not self.status.enforced:
            return argv
        if not self._executable or not self._temp_dir:
            raise SandboxUnavailableError("enabled sandbox has no runtime boundary")
        if self.status.backend == "seatbelt":
            return wrap_seatbelt_command(
                argv,
                executable=self._executable,
                workspace=self.status.workspace,
                temp_dir=self._temp_dir,
                allow_raw_network=self._allow_raw_network,
                deny_read=self._deny_read,
            )
        if self.status.backend == "bubblewrap":
            return wrap_bwrap_command(
                argv,
                executable=self._executable,
                workspace=self.status.workspace,
                temp_dir=self._temp_dir,
                allow_raw_network=self._allow_raw_network,
                deny_read=self._deny_read,
            )
        raise SandboxUnavailableError(
            f"unknown enabled sandbox backend: {self.status.backend!r}"
        )

    def apply_environment(self, environment: Mapping[str, str]) -> dict[str, str]:
        env = {str(key): str(value) for key, value in environment.items()}
        if self.status.enforced and self._temp_dir:
            env["TMPDIR"] = self._temp_dir
            env["TMP"] = self._temp_dir
            env["TEMP"] = self._temp_dir
            env["MPLCONFIGDIR"] = str(Path(self._temp_dir) / "matplotlib")
        return env

    def interrupt_target_pid(
        self,
        launcher_pid: int,
        *,
        proc_root: str | os.PathLike[str] = "/proc",
    ) -> int:
        """Return the process that should receive a worker SIGINT.

        Seatbelt and an unsandboxed worker execute at ``Popen.pid``. On Linux,
        bubblewrap stays at that PID and supervises the real Python/R worker as
        one direct child. Signalling bubblewrap terminates the wrapper instead
        of raising ``KeyboardInterrupt`` in the cell, so resolve the child from
        procfs and independently confirm its PPid before returning it.

        Any absent, changing, or malformed procfs state falls back to the
        launcher. This keeps the adapter portable and preserves the previous
        best-effort behaviour when procfs is hidden by host policy.
        """

        launcher = int(launcher_pid)
        if not (
            launcher > 0
            and self.status.enforced
            and self.status.backend == "bubblewrap"
        ):
            return launcher

        root = Path(proc_root)
        try:
            children_text = (
                root / str(launcher) / "task" / str(launcher) / "children"
            ).read_text(encoding="ascii")
            child_tokens = children_text.split()
            if len(child_tokens) != 1:
                return launcher
            child = int(child_tokens[0], 10)
            if child <= 0 or child == launcher:
                return launcher

            status_text = (root / str(child) / "status").read_text(
                encoding="utf-8", errors="replace"
            )
            parent = None
            for line in status_text.splitlines():
                if line.startswith("PPid:"):
                    parent = int(line.partition(":")[2].strip(), 10)
                    break
            if parent != launcher:
                return launcher
        except (OSError, UnicodeError, ValueError):
            return launcher
        return child

    def send_interrupt(
        self,
        launcher_pid: int,
        signum: int,
        *,
        proc_root: str | os.PathLike[str] = "/proc",
    ) -> bool:
        """Safely deliver a signal through bubblewrap's supervising process.

        The return value means that this adapter owns signal delivery, not that
        a signal necessarily reached a live worker.  An enforced bubblewrap
        path never hands a numeric child PID back to the caller: a pidfd pins
        the process identity across the second procfs validation and the send,
        so PID reuse cannot redirect SIGINT to an unrelated process.

        Python builds or kernels without pidfd support fail safe here.  The
        request becomes a best-effort no-op rather than falling back to the
        racy numeric child PID; the watchdog can still replace a stuck worker.
        """

        launcher = int(launcher_pid)
        if not (
            launcher > 0
            and self.status.enforced
            and self.status.backend == "bubblewrap"
        ):
            return False

        pidfd_open = getattr(os, "pidfd_open", None)
        pidfd_send_signal = getattr(signal, "pidfd_send_signal", None)
        if not callable(pidfd_open) or not callable(pidfd_send_signal):
            self._report_interrupt_gap("this Python/kernel has no pidfd support")
            return True

        child = self.interrupt_target_pid(launcher, proc_root=proc_root)
        if child == launcher:
            self._report_interrupt_gap("procfs did not name one direct worker child")
            return True

        pidfd = None
        try:
            pidfd = pidfd_open(child, 0)
            # Opening the pidfd pins the identity. Re-read procfs afterwards so
            # the fd is used only if that exact PID is still the wrapper's sole
            # direct child. If it exited, pidfd_send_signal would be safe too,
            # but there is no longer an intended worker to interrupt.
            if self.interrupt_target_pid(launcher, proc_root=proc_root) != child:
                return True
            pidfd_send_signal(pidfd, int(signum), None, 0)
        except (OSError, ValueError):
            pass
        finally:
            if pidfd is not None:
                try:
                    os.close(pidfd)
                except OSError:
                    pass
        return True

    def _report_interrupt_gap(self, reason: str) -> None:
        """Make a structurally unreachable interrupt visible, once per kernel.

        These branches deliberately drop the SIGINT rather than fall back to a
        racy numeric PID, which leaves the user's stop request doing nothing
        while the cell keeps running until the watchdog replaces the worker.
        That trade is only acceptable when it is visible.
        """
        if self._interrupt_gap_reported:
            return
        self._interrupt_gap_reported = True
        print(
            "[openai4s] cell interrupt cannot reach the sandboxed worker "
            f"({reason}); the stop request is dropped and a stuck cell is "
            "recovered by the watchdog instead",
            file=sys.stderr,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_temp_dir and self._temp_dir:
            shutil.rmtree(self._temp_dir, ignore_errors=True)


def _run_self_test(
    *,
    backend: str,
    executable: str,
    workspace: Path,
    temp_dir: Path,
    allow_raw_network: bool,
    runner: Runner,
    deny_read: Sequence[tuple[str, str]] = (),
) -> tuple[bool, str]:
    token = f"{os.getpid()}-{threading.get_ident()}"
    workspace_file = workspace / f".openai4s-sandbox-test-{token}"
    temp_file = temp_dir / f"self-test-{token}"
    try:
        outside_root = _allocate_outside_probe(workspace, temp_dir)
    except OSError as exc:
        return False, f"self-test could not allocate deny probe: {exc}"
    outside_file = outside_root / "must-not-write"
    probe = [
        sys.executable,
        "-I",
        "-c",
        _SELF_TEST_CODE,
        str(workspace_file),
        str(temp_file),
        str(outside_file),
        "0" if allow_raw_network else "1",
    ]
    if backend == "seatbelt":
        command = wrap_seatbelt_command(
            probe,
            executable=executable,
            workspace=workspace,
            temp_dir=temp_dir,
            allow_raw_network=allow_raw_network,
            deny_read=deny_read,
        )
    else:
        command = wrap_bwrap_command(
            probe,
            executable=executable,
            workspace=workspace,
            temp_dir=temp_dir,
            allow_raw_network=allow_raw_network,
            deny_read=deny_read,
        )
    try:
        completed = runner(
            command,
            cwd=str(workspace),
            env={
                "PATH": os.defpath,
                "LANG": os.environ.get("LANG", "C.UTF-8"),
                "TMPDIR": str(temp_dir),
                "TMP": str(temp_dir),
                "TEMP": str(temp_dir),
            },
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"self-test could not start: {_bounded_diagnostic(exc)}"
    finally:
        for candidate in (workspace_file, temp_file):
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass
        shutil.rmtree(outside_root, ignore_errors=True)

    stdout = str(getattr(completed, "stdout", "") or "")
    stderr = str(getattr(completed, "stderr", "") or "")
    payload: dict[str, Any] | None = None
    for line in reversed(stdout.splitlines()):
        try:
            decoded = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(decoded, dict):
            payload = decoded
            break
    returncode = int(getattr(completed, "returncode", 1))
    if returncode == 0 and payload and payload.get("ok") is True:
        checks = payload.get("checks") or {}
        return True, f"self-test passed: {json.dumps(checks, sort_keys=True)}"
    diagnostic = _bounded_diagnostic(stderr or stdout or f"exit {returncode}")
    return False, f"self-test failed (exit {returncode}): {diagnostic}"


def _degraded_status(
    *, mode: str, workspace: Path, backend: str | None, detail: str
) -> SandboxStatus:
    warning = "OPENAI4S SECURITY WARNING: OS kernel sandbox is not enforced; " + detail
    return SandboxStatus(
        mode=mode,
        state="unavailable",
        backend=backend,
        enforced=False,
        self_test_passed=False if backend else None,
        network_policy="not_enforced",
        workspace=str(workspace),
        temp_dir=None,
        detail=detail,
        warning=warning,
    )


def create_kernel_sandbox(
    workspace: str | os.PathLike[str] | None = None,
    *,
    mode: str | None = None,
    allow_raw_network: bool | None = None,
    platform_name: str | None = None,
    which: Which = shutil.which,
    runner: Runner = _default_runner,
) -> KernelSandbox:
    """Detect, self-test and construct the sandbox for one Kernel.

    The returned object owns its private temp directory and must be closed with
    the Kernel.  ``runner`` and platform probes are injected only for offline
    tests; production callers use the defaults.
    """

    requested_mode = _sandbox_mode(mode)
    workspace_path = _canonical_dir(workspace or os.getcwd())
    if allow_raw_network is None:
        allow_network = _parse_bool(
            os.environ.get(_RAW_NETWORK_ENV), name=_RAW_NETWORK_ENV, default=False
        )
    else:
        allow_network = bool(allow_raw_network)

    if requested_mode == "off":
        return KernelSandbox(
            status=SandboxStatus(
                mode="off",
                state="disabled",
                backend=None,
                enforced=False,
                self_test_passed=None,
                network_policy="not_enforced",
                workspace=str(workspace_path),
                temp_dir=None,
                detail=f"explicitly disabled by {_SANDBOX_ENV}=off",
            )
        )

    platform_value = platform_name or sys.platform
    backend, executable, detection_detail = _detect_backend(
        platform_name=platform_value, which=which
    )
    if not backend or not executable:
        status = _degraded_status(
            mode=requested_mode,
            workspace=workspace_path,
            backend=None,
            detail=detection_detail,
        )
        if requested_mode == "enforce":
            raise SandboxUnavailableError(status.warning)
        _warn_once(status.warning or status.detail)
        return KernelSandbox(status=status)

    cache_key = (backend, executable, allow_network)
    if runner is _default_runner:
        with _self_test_lock:
            cached_failure = _failed_self_tests.get(cache_key)
        if cached_failure:
            status = _degraded_status(
                mode=requested_mode,
                workspace=workspace_path,
                backend=backend,
                detail=cached_failure,
            )
            if requested_mode == "enforce":
                raise SandboxUnavailableError(status.warning)
            _warn_once(status.warning or status.detail)
            return KernelSandbox(status=status)

    try:
        temp_path = Path(tempfile.mkdtemp(prefix="openai4s-kernel-")).resolve()
    except OSError as exc:
        detail = f"{detection_detail}; private temp allocation failed: {exc}"
        status = _degraded_status(
            mode=requested_mode,
            workspace=workspace_path,
            backend=backend,
            detail=detail,
        )
        if requested_mode == "enforce":
            raise SandboxUnavailableError(status.warning) from exc
        _warn_once(status.warning or status.detail)
        return KernelSandbox(status=status)
    deny_read = _default_secret_read_denials(workspace_path)
    passed, self_test_detail = _run_self_test(
        backend=backend,
        executable=executable,
        workspace=workspace_path,
        temp_dir=temp_path,
        allow_raw_network=allow_network,
        runner=runner,
        deny_read=deny_read,
    )
    if not passed:
        shutil.rmtree(temp_path, ignore_errors=True)
        detail = f"{detection_detail}; {self_test_detail}"
        if runner is _default_runner and _globally_unavailable(detail):
            with _self_test_lock:
                _failed_self_tests[cache_key] = detail
        status = _degraded_status(
            mode=requested_mode,
            workspace=workspace_path,
            backend=backend,
            detail=detail,
        )
        if requested_mode == "enforce":
            raise SandboxUnavailableError(status.warning)
        _warn_once(status.warning or status.detail)
        return KernelSandbox(status=status)

    status = SandboxStatus(
        mode=requested_mode,
        state="enabled",
        backend=backend,
        enforced=True,
        self_test_passed=True,
        network_policy="raw_allowed" if allow_network else "blocked",
        workspace=str(workspace_path),
        temp_dir=str(temp_path),
        detail=f"{detection_detail}; {self_test_detail}",
    )
    return KernelSandbox(
        status=status,
        executable=executable,
        temp_dir=str(temp_path),
        allow_raw_network=allow_network,
        owns_temp_dir=True,
        deny_read=deny_read,
    )


__all__ = [
    "KernelSandbox",
    "SandboxConfigurationError",
    "SandboxError",
    "SandboxStatus",
    "SandboxUnavailableError",
    "build_seatbelt_profile",
    "create_kernel_sandbox",
    "wrap_bwrap_command",
    "wrap_seatbelt_command",
]
