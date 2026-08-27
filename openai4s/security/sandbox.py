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
import select
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_SANDBOX_ENV = "OPENAI4S_KERNEL_SANDBOX"
_RAW_NETWORK_ENV = "OPENAI4S_KERNEL_ALLOW_RAW_NETWORK"
_VALID_MODES = frozenset({"auto", "enforce", "off"})
_WORKSPACE_LINK_SCAN_MAX_ENTRIES = 100_000
_WORKSPACE_LINK_SCAN_TIMEOUT_S = 5.0
_BWRAP_INFO_MAX_BYTES = 4096
_BWRAP_INFO_TIMEOUT_S = 5.0
_BWRAP_CHILD_TIMEOUT_S = 5.0
_BWRAP_CHILD_POLL_S = 0.01
_BWRAP_PROC_MAX_BYTES = 64 * 1024

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


@dataclass(frozen=True)
class KernelReadIsolation:
    """Read-denied shared roots plus exact read-only exceptions for one Cell.

    The workspace is always an implicit exception and remains the only
    writable bind. ``allowed_roots`` is for trusted runtime inputs that happen
    to live below a protected root (an immutable environment generation, the
    current user's personal data area, or an authorized Skill sidecar).
    """

    roots: tuple[str | os.PathLike[str], ...]
    allowed_roots: tuple[str | os.PathLike[str], ...] = ()

    def with_allowed_roots(
        self, roots: Sequence[str | os.PathLike[str] | None]
    ) -> "KernelReadIsolation":
        return KernelReadIsolation(
            roots=self.roots,
            allowed_roots=(
                *self.allowed_roots,
                *(root for root in roots if root is not None),
            ),
        )

    def with_roots(
        self, roots: Sequence[str | os.PathLike[str]]
    ) -> "KernelReadIsolation":
        return KernelReadIsolation(
            roots=(*self.roots, *roots),
            allowed_roots=self.allowed_roots,
        )


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


def _workspace_isolation_paths(
    workspace: str | os.PathLike[str],
    read_isolation: KernelReadIsolation | None,
) -> tuple[Path, tuple[Path, ...], tuple[Path, ...]] | None:
    """Validate and canonicalize one team Cell read boundary.

    At least one protected root must strictly contain the writable workspace.
    Other roots may live elsewhere (for example ``<data-root>/users``). Exact
    trusted exceptions must sit strictly below a protected root and may not
    contain the workspace or a protected root. Resolve every path first so a
    symlink alias cannot select a different boundary on macOS and Linux.
    """

    if read_isolation is None:
        return None
    workspace_path = Path(workspace).expanduser().resolve(strict=False)
    raw_roots = tuple(read_isolation.roots)
    if not raw_roots:
        raise SandboxConfigurationError(
            "kernel read isolation requires at least one protected root"
        )
    roots: list[Path] = []
    for raw_root in raw_roots:
        root_path = Path(raw_root).expanduser().resolve(strict=False)
        if "\x00" in str(root_path):
            raise SandboxConfigurationError(
                "kernel read-isolation roots cannot contain NUL bytes"
            )
        if not root_path.is_dir():
            raise SandboxConfigurationError(
                f"kernel read-isolation root does not exist: {root_path}"
            )
        # A parent deny already covers a nested protected root. Keeping only
        # minimal roots also prevents mount-order differences between Seatbelt
        # and bubblewrap when configured roots overlap.
        if root_path in roots or any(parent in root_path.parents for parent in roots):
            continue
        roots = [root for root in roots if root_path not in root.parents]
        roots.append(root_path)
    if not any(root in workspace_path.parents for root in roots):
        raise SandboxConfigurationError(
            "kernel workspace must be a strict child of a read-isolation root"
        )

    allowed: list[Path] = []
    for raw_allowed in read_isolation.allowed_roots:
        allowed_path = Path(raw_allowed).expanduser().resolve(strict=False)
        if "\x00" in str(allowed_path):
            raise SandboxConfigurationError(
                "kernel read-isolation exceptions cannot contain NUL bytes"
            )
        containing = [root for root in roots if root in allowed_path.parents]
        if not containing:
            if any(
                allowed_path == root or allowed_path in root.parents for root in roots
            ):
                raise SandboxConfigurationError(
                    "a read-isolation exception cannot contain a protected root"
                )
            # An exception outside every protected root changes no policy and
            # needs no bind. This is common for system Conda environments.
            continue
        if not allowed_path.is_dir():
            raise SandboxConfigurationError(
                f"kernel read-isolation exception does not exist: {allowed_path}"
            )
        if allowed_path == workspace_path:
            continue
        if allowed_path in workspace_path.parents:
            raise SandboxConfigurationError(
                "a read-isolation exception cannot contain the kernel workspace"
            )
        if workspace_path in allowed_path.parents:
            continue
        if allowed_path in allowed or any(
            parent in allowed_path.parents for parent in allowed
        ):
            continue
        allowed = [parent for parent in allowed if allowed_path not in parent.parents]
        allowed.append(allowed_path)
    return workspace_path, tuple(roots), tuple(allowed)


def _seatbelt_workspace_read_rules(
    workspace: str | os.PathLike[str],
    read_isolation: KernelReadIsolation | None,
) -> list[str]:
    """Deny shared roots and retain only exact trusted paths on Seatbelt."""

    paths = _workspace_isolation_paths(workspace, read_isolation)
    if paths is None:
        return []
    workspace_path, roots, allowed_roots = paths
    exceptions = (workspace_path, *allowed_roots)
    rules = [
        "(deny file-read* "
        + " ".join(
            f"(literal {_seatbelt_string(root)}) " f"(subpath {_seatbelt_string(root)})"
            for root in roots
        )
        + ")"
    ]
    for exception in exceptions:
        containing = next(root for root in roots if root in exception.parents)
        # Opening a known descendant requires metadata traversal on every
        # parent. Directory enumeration needs file-read-data, which remains
        # denied, so literal metadata grants reveal no sibling contents.
        traversal: list[Path] = []
        parent = exception.parent
        while True:
            traversal.append(parent)
            if parent == containing:
                break
            parent = parent.parent
        metadata_filters = " ".join(
            f"(literal {_seatbelt_string(path)})" for path in reversed(traversal)
        )
        exception_q = _seatbelt_string(exception)
        rules.extend(
            [
                f"(allow file-read-metadata {metadata_filters})",
                (
                    f"(allow file-read* (literal {exception_q}) "
                    f"(subpath {exception_q}))"
                ),
            ]
        )
    return rules


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
    read_isolation: KernelReadIsolation | None = None,
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
    # Seatbelt is last-match-wins. Deny the shared root, then re-allow only
    # this Kernel's exact workspace. Targeted credential denies are appended
    # afterwards so they still win if a trusted caller places one below it.
    lines.extend(_seatbelt_workspace_read_rules(workspace, read_isolation))
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
    read_isolation: KernelReadIsolation | None = None,
) -> list[str]:
    profile = build_seatbelt_profile(
        workspace,
        temp_dir,
        allow_raw_network=allow_raw_network,
        deny_read=deny_read,
        read_isolation=read_isolation,
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
    """Hide daemon environ for the single-user host-PID-namespace policy.

    A single-user sandbox deliberately keeps the host PID namespace.
    bubblewrap remains as the process returned by ``Popen`` and starts the
    worker as its direct child; ``KernelSandbox.send_interrupt()`` pins and
    validates that child before delivering SIGINT. The cost is that
    `/proc` still shows every host process, so a Linux cell can read
    `/proc/<daemon>/environ` — and that is where the daemon's API keys live,
    since the child's own environment is allowlisted clean.

    Team read isolation instead uses ``--unshare-pid``. Bubblewrap's
    ``--info-fd`` report identifies its namespace init; the Host pins that
    anchor, validates its unique command child through procfs, then retains a
    pidfd for the command. It does not need this one-file mask because its
    `/proc` contains only the private namespace.
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
    read_isolation: KernelReadIsolation | None = None,
    info_fd: int | None = None,
    new_session: bool = True,
) -> list[str]:
    """Wrap ``command`` in a read-only-root bubblewrap mount namespace."""

    workspace_s = str(workspace)
    temp_s = str(temp_dir)
    isolation = _workspace_isolation_paths(workspace, read_isolation)
    isolation_roots = tuple(str(root) for root in isolation[1]) if isolation else ()
    allowed_roots = tuple(str(root) for root in isolation[2]) if isolation else ()
    wrapped = [
        str(executable),
        "--die-with-parent",
        # Single-user mode keeps the host PID namespace: KernelSandbox's pidfd
        # path can then deliver SIGINT to bubblewrap's direct worker child.
        # Team read isolation instead gets a private PID namespace below; that
        # closes /proc/<sibling>/root aliases around the mount boundary.
        "--unshare-ipc",
        "--unshare-uts",
    ]
    if new_session:
        wrapped.insert(2, "--new-session")
    if info_fd is not None:
        if int(info_fd) < 0:
            raise SandboxConfigurationError("bubblewrap info fd must be non-negative")
        wrapped.extend(["--info-fd", str(int(info_fd))])
    if isolation_roots:
        # A private PID namespace closes /proc/<sibling>/root, which would
        # otherwise provide an alias around the mount hiding the shared root.
        wrapped.append("--unshare-pid")
    if not allow_raw_network:
        wrapped.append("--unshare-net")
    wrapped.extend(["--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc"])
    if isolation_roots:
        # Hide every existing and future private path behind fresh mounts. The
        # bubblewrap source of every later bind is resolved through oldroot,
        # so it remains available even after its parent was covered by tmpfs.
        for root in isolation_roots:
            wrapped.extend(["--tmpfs", root])
    wrapped.extend(["--bind", workspace_s, workspace_s])
    for root in allowed_roots:
        if root == str(Path(temp_s).resolve(strict=False)):
            continue
        wrapped.extend(["--ro-bind", root, root])
    wrapped.extend(["--bind", temp_s, temp_s])
    if isolation_roots:
        # remount-ro is deliberately non-recursive in bubblewrap: the parent
        # tmpfs becomes immutable while the nested workspace bind stays rw and
        # the exact trusted exception binds stay read-only.
        for root in isolation_roots:
            wrapped.extend(["--remount-ro", root])
    # Mask secret paths after the workspace/temp binds (so a denial still wins
    # when the workspace nests a secret) and before --chdir/--.
    wrapped.extend(_bwrap_read_masks(deny_read))
    # ...and after `--proc /proc` above, which is what makes this reachable:
    # the mount would otherwise replace whatever was bound over it.
    if not isolation_roots:
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
import os
import socket
import sys
from pathlib import Path

(
    workspace_file,
    temp_file,
    outside_file,
    expect_network_blocked,
    sibling_files_json,
    allowed_files_json,
) = sys.argv[1:7]
sibling_files = json.loads(sibling_files_json)
allowed_files = json.loads(allowed_files_json)

def can_write(name):
    try:
        path = Path(name)
        path.write_text("openai4s-sandbox-self-test", encoding="utf-8")
        path.unlink()
        return True
    except OSError:
        return False

def can_read(name):
    try:
        Path(name).read_bytes()
        return True
    except OSError:
        return False

def link_reads_blocked(sources, destination, *, symbolic):
    if not sources:
        return None
    for index, source in enumerate(sources):
        target = Path(destination + "-" + str(index))
        try:
            if symbolic:
                target.symlink_to(source)
            else:
                os.link(source, target)
            if can_read(target):
                return False
        except OSError:
            continue
        finally:
            try:
                target.unlink()
            except OSError:
                pass
    return True

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
    "sibling_read_blocked": (
        all(not can_read(path) for path in sibling_files)
        if sibling_files else None
    ),
    "sibling_symlink_read_blocked": link_reads_blocked(
        sibling_files, workspace_file + "-symlink", symbolic=True
    ),
    "sibling_hardlink_read_blocked": link_reads_blocked(
        sibling_files, workspace_file + "-hardlink", symbolic=False
    ),
    "allowed_roots_readable": (
        all(can_read(path) for path in allowed_files)
        if allowed_files else None
    ),
}
required = ["workspace_write", "temp_write", "outside_write_blocked"]
if expect_network_blocked == "1":
    required.append("network_blocked")
if sibling_files:
    required.extend(
        [
            "sibling_read_blocked",
            "sibling_symlink_read_blocked",
            "sibling_hardlink_read_blocked",
        ]
    )
if allowed_files:
    required.append("allowed_roots_readable")
ok = all(checks[key] is True for key in required)
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


def _allow_read_probe(root: Path, token: str) -> tuple[Path, bool]:
    """Choose real bytes below an exact allow root, creating a probe if empty."""

    pending = [root]
    visited = 0
    while pending and visited < 4096:
        directory = pending.pop()
        try:
            entries = os.scandir(directory)
        except OSError:
            break
        try:
            for entry in entries:
                visited += 1
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISREG(info.st_mode):
                    return Path(entry.path), False
                if stat.S_ISDIR(info.st_mode):
                    pending.append(Path(entry.path))
                if visited >= 4096:
                    break
        except OSError:
            break
        finally:
            entries.close()
    probe = root / f".openai4s-sandbox-read-allow-{token}"
    descriptor = os.open(
        probe,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.write(descriptor, b"openai4s-allowed-input")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return probe, True


def _assert_no_external_workspace_hardlinks(workspace: Path) -> None:
    """Refuse a workspace inode that also has a name outside the workspace.

    Path sandboxes cannot distinguish two hardlink names for one inode. A
    secret linked into the allowed workspace before the worker starts would
    therefore bypass both Seatbelt and a bubblewrap bind. Count every regular
    inode name without following symlinks and require the inode's link count to
    be fully accounted for inside the workspace. The scan is bounded and any
    ambiguity fails closed; a team Cell is never worth an unbounded daemon
    walk or a guessed authorization answer.
    """

    deadline = time.monotonic() + _WORKSPACE_LINK_SCAN_TIMEOUT_S
    pending = [workspace]
    seen_entries = 0
    inodes: dict[tuple[int, int], tuple[int, int, str]] = {}
    while pending:
        if time.monotonic() > deadline:
            raise SandboxUnavailableError(
                "team kernel workspace hardlink scan exceeded its time limit"
            )
        directory = pending.pop()
        try:
            entries = os.scandir(directory)
        except OSError as exc:
            raise SandboxUnavailableError(
                f"team kernel workspace cannot be safely scanned: {exc}"
            ) from exc
        try:
            for entry in entries:
                seen_entries += 1
                if seen_entries > _WORKSPACE_LINK_SCAN_MAX_ENTRIES:
                    raise SandboxUnavailableError(
                        "team kernel workspace hardlink scan exceeded its entry limit"
                    )
                if time.monotonic() > deadline:
                    raise SandboxUnavailableError(
                        "team kernel workspace hardlink scan exceeded its time limit"
                    )
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    raise SandboxUnavailableError(
                        "team kernel workspace changed during hardlink scan: " f"{exc}"
                    ) from exc
                if stat.S_ISDIR(info.st_mode):
                    pending.append(Path(entry.path))
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_nlink <= 1:
                    continue
                key = (int(info.st_dev), int(info.st_ino))
                expected, count, first = inodes.get(
                    key, (int(info.st_nlink), 0, entry.path)
                )
                if expected != int(info.st_nlink):
                    raise SandboxUnavailableError(
                        "team kernel workspace changed during hardlink scan"
                    )
                inodes[key] = (expected, count + 1, first)
        except OSError as exc:
            raise SandboxUnavailableError(
                f"team kernel workspace cannot be safely scanned: {exc}"
            ) from exc
        finally:
            entries.close()
    for expected, count, first in inodes.values():
        if count != expected:
            raise SandboxUnavailableError(
                "team kernel workspace contains a regular file hardlinked "
                f"outside the workspace: {first}"
            )


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
        read_isolation: KernelReadIsolation | None = None,
    ) -> None:
        self.status = status
        self._executable = executable
        self._temp_dir = temp_dir
        self._allow_raw_network = allow_raw_network
        self._owns_temp_dir = owns_temp_dir
        self._deny_read = tuple(deny_read)
        isolation = _workspace_isolation_paths(status.workspace, read_isolation)
        self._read_isolation = (
            KernelReadIsolation(
                roots=tuple(str(root) for root in isolation[1]),
                allowed_roots=tuple(str(root) for root in isolation[2]),
            )
            if isolation
            else None
        )
        self._workspace = Path(status.workspace)
        self._closed = False
        self._interrupt_gap_reported = False
        # The reason the MOST RECENT send_interrupt did not reach a worker, or
        # None when it did. Separate from `_interrupt_gap_reported`, which
        # rate-limits the operator-facing print to once per kernel: the print
        # is for a human reading a terminal, this is for the caller that has to
        # decide whether the cell it just asked to stop is still running.
        self._interrupt_gap: str | None = None
        # Team bubblewrap uses a private PID namespace. Its command is not the
        # launcher's direct child, so the single-user procfs resolver cannot
        # identify the SIGINT target. ``--info-fd`` supplies bubblewrap's
        # namespace-init PID in the launch namespace; adoption pins that
        # authenticated anchor, then pins its one direct command child.
        self._bwrap_info_read_fd: int | None = None
        self._bwrap_info_write_fd: int | None = None
        self._bwrap_launcher_pid: int | None = None
        self._bwrap_worker_pidfd: int | None = None

    def wrap_command(self, command: Sequence[str]) -> list[str]:
        argv = [str(part) for part in command]
        if not self.status.enforced:
            return argv
        if self._read_isolation is not None:
            _assert_no_external_workspace_hardlinks(self._workspace)
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
                read_isolation=self._read_isolation,
            )
        if self.status.backend == "bubblewrap":
            info_fd = None
            if self._read_isolation is not None:
                self._reset_bwrap_process_identity()
                try:
                    read_fd, write_fd = os.pipe()
                except OSError as exc:
                    raise SandboxUnavailableError(
                        "team bubblewrap could not create its worker PID channel"
                    ) from exc
                self._bwrap_info_read_fd = read_fd
                self._bwrap_info_write_fd = write_fd
                info_fd = write_fd
            return wrap_bwrap_command(
                argv,
                executable=self._executable,
                workspace=self.status.workspace,
                temp_dir=self._temp_dir,
                allow_raw_network=self._allow_raw_network,
                deny_read=self._deny_read,
                read_isolation=self._read_isolation,
                info_fd=info_fd,
                # PipeTransport starts the bwrap launcher in a new session.
                # Asking bwrap to create another session for its command splits
                # the wrapper and Cell subprocesses into different process
                # groups, so a watchdog kill reaches only the wrapper and
                # leaves the actual work alive. Inheriting the Popen-created
                # session preserves terminal isolation and gives the transport
                # one group that contains the complete worker tree.
                new_session=False,
            )
        raise SandboxUnavailableError(
            f"unknown enabled sandbox backend: {self.status.backend!r}"
        )

    def popen_pass_fds(self) -> tuple[int, ...]:
        """File descriptors the next local Popen must inherit exactly once."""

        if self._bwrap_info_write_fd is None:
            return ()
        return (self._bwrap_info_write_fd,)

    def _read_bwrap_info(self, descriptor: int) -> Mapping[str, Any]:
        deadline = time.monotonic() + _BWRAP_INFO_TIMEOUT_S
        payload = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SandboxUnavailableError(
                    "team bubblewrap did not report its namespace init before timeout"
                )
            try:
                readable, _, _ = select.select([descriptor], [], [], remaining)
            except (OSError, ValueError) as exc:
                raise SandboxUnavailableError(
                    "team bubblewrap namespace-init channel failed"
                ) from exc
            if not readable:
                raise SandboxUnavailableError(
                    "team bubblewrap did not report its namespace init before timeout"
                )
            try:
                chunk = os.read(descriptor, _BWRAP_INFO_MAX_BYTES - len(payload) + 1)
            except OSError as exc:
                raise SandboxUnavailableError(
                    "team bubblewrap namespace-init channel could not be read"
                ) from exc
            if chunk:
                payload.extend(chunk)
            if len(payload) > _BWRAP_INFO_MAX_BYTES:
                raise SandboxUnavailableError(
                    "team bubblewrap namespace-init report exceeded its byte limit"
                )
            try:
                decoded = json.loads(payload.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                if not chunk:
                    raise SandboxUnavailableError(
                        "team bubblewrap namespace-init report was invalid JSON"
                    )
                continue
            if not isinstance(decoded, Mapping):
                raise SandboxUnavailableError(
                    "team bubblewrap namespace-init report was not an object"
                )
            return decoded

    @staticmethod
    def _read_bounded_proc_file(path: Path) -> str:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        payload = bytearray()
        try:
            while True:
                chunk = os.read(
                    descriptor,
                    _BWRAP_PROC_MAX_BYTES - len(payload) + 1,
                )
                if not chunk:
                    break
                payload.extend(chunk)
                if len(payload) > _BWRAP_PROC_MAX_BYTES:
                    raise SandboxUnavailableError(
                        "team bubblewrap procfs identity exceeded its byte limit"
                    )
        finally:
            os.close(descriptor)
        try:
            return payload.decode("ascii")
        except UnicodeError as exc:
            raise SandboxUnavailableError(
                "team bubblewrap procfs identity was not ASCII"
            ) from exc

    @classmethod
    def _proc_parent_pid(
        cls,
        process_pid: int,
        *,
        proc_root: str | os.PathLike[str],
    ) -> int:
        status = cls._read_bounded_proc_file(
            Path(proc_root) / str(process_pid) / "status"
        )
        parents = [
            line.partition(":")[2].strip()
            for line in status.splitlines()
            if line.startswith("PPid:")
        ]
        if len(parents) != 1:
            raise SandboxUnavailableError(
                "team bubblewrap procfs identity omitted PPid"
            )
        try:
            parent = int(parents[0], 10)
        except ValueError as exc:
            raise SandboxUnavailableError(
                "team bubblewrap procfs identity contained an invalid PPid"
            ) from exc
        if parent < 0:
            raise SandboxUnavailableError(
                "team bubblewrap procfs identity contained an invalid PPid"
            )
        return parent

    @classmethod
    def _proc_direct_children(
        cls,
        process_pid: int,
        *,
        proc_root: str | os.PathLike[str],
    ) -> tuple[int, ...]:
        children_text = cls._read_bounded_proc_file(
            Path(proc_root) / str(process_pid) / "task" / str(process_pid) / "children"
        )
        try:
            children = tuple(int(token, 10) for token in children_text.split())
        except ValueError as exc:
            raise SandboxUnavailableError(
                "team bubblewrap namespace init reported an invalid child list"
            ) from exc
        if any(child <= 0 or child == process_pid for child in children):
            raise SandboxUnavailableError(
                "team bubblewrap namespace init reported an invalid child list"
            )
        if len(set(children)) != len(children):
            raise SandboxUnavailableError(
                "team bubblewrap namespace init reported an invalid child list"
            )
        return children

    @staticmethod
    def _assert_pidfd_alive(
        pidfd: int,
        *,
        pidfd_send_signal: Callable[..., Any],
        identity: str,
    ) -> None:
        try:
            pidfd_send_signal(pidfd, 0, None, 0)
        except (OSError, ValueError) as exc:
            raise SandboxUnavailableError(
                f"team bubblewrap {identity} exited during identity adoption"
            ) from exc

    def adopt_process(
        self,
        launcher_pid: int,
        *,
        proc_root: str | os.PathLike[str] = "/proc",
    ) -> None:
        """Pin the command below bubblewrap's reported namespace init.

        Called immediately after Popen. Failure is a launch failure: running a
        team worker whose interrupts cannot target the exact command would make
        cancellation appear successful while arbitrary Cell code continues.
        """

        if self._read_isolation is None or self.status.backend != "bubblewrap":
            return
        read_fd = self._bwrap_info_read_fd
        write_fd = self._bwrap_info_write_fd
        self._bwrap_info_read_fd = None
        self._bwrap_info_write_fd = None
        if write_fd is not None:
            try:
                os.close(write_fd)
            except OSError:
                pass
        if read_fd is None:
            raise SandboxUnavailableError(
                "team bubblewrap namespace-init channel was not initialized"
            )
        try:
            report = self._read_bwrap_info(read_fd)
        finally:
            try:
                os.close(read_fd)
            except OSError:
                pass
        init_pid = report.get("child-pid")
        if isinstance(init_pid, bool) or not isinstance(init_pid, int):
            raise SandboxUnavailableError(
                "team bubblewrap namespace-init report omitted child-pid"
            )
        launcher = int(launcher_pid)
        if init_pid <= 0 or launcher <= 0 or init_pid == launcher:
            raise SandboxUnavailableError(
                "team bubblewrap namespace-init report was invalid"
            )
        pidfd_open = getattr(os, "pidfd_open", None)
        pidfd_send_signal = getattr(signal, "pidfd_send_signal", None)
        if not callable(pidfd_open) or not callable(pidfd_send_signal):
            raise SandboxUnavailableError(
                "team bubblewrap requires pidfd support for reliable interrupts"
            )
        init_pidfd: int | None = None
        worker_pidfd: int | None = None
        adopted = False
        try:
            try:
                init_pidfd = int(pidfd_open(init_pid, 0))
            except (OSError, TypeError, ValueError) as exc:
                raise SandboxUnavailableError(
                    "team bubblewrap could not pin its reported namespace init"
                ) from exc
            if init_pidfd < 0:
                raise SandboxUnavailableError(
                    "team bubblewrap returned an invalid namespace-init pidfd"
                )
            self._assert_pidfd_alive(
                init_pidfd,
                pidfd_send_signal=pidfd_send_signal,
                identity="namespace init",
            )
            if self._proc_parent_pid(init_pid, proc_root=proc_root) != launcher:
                raise SandboxUnavailableError(
                    "team bubblewrap namespace init was not a child of its launcher"
                )
            if self._proc_direct_children(launcher, proc_root=proc_root) != (init_pid,):
                raise SandboxUnavailableError(
                    "team bubblewrap launcher did not have exactly its reported init"
                )

            deadline = time.monotonic() + _BWRAP_CHILD_TIMEOUT_S
            command_pid: int | None = None
            while command_pid is None:
                self._assert_pidfd_alive(
                    init_pidfd,
                    pidfd_send_signal=pidfd_send_signal,
                    identity="namespace init",
                )
                if self._proc_parent_pid(init_pid, proc_root=proc_root) != launcher:
                    raise SandboxUnavailableError(
                        "team bubblewrap namespace init changed launcher"
                    )
                children = self._proc_direct_children(
                    init_pid,
                    proc_root=proc_root,
                )
                if len(children) > 1:
                    raise SandboxUnavailableError(
                        "team bubblewrap namespace init had multiple direct children"
                    )
                if children:
                    command_pid = children[0]
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SandboxUnavailableError(
                        "team bubblewrap did not start one command before timeout"
                    )
                time.sleep(min(_BWRAP_CHILD_POLL_S, remaining))

            if self._proc_parent_pid(command_pid, proc_root=proc_root) != init_pid:
                raise SandboxUnavailableError(
                    "team bubblewrap command was not a child of namespace init"
                )
            try:
                worker_pidfd = int(pidfd_open(command_pid, 0))
            except (OSError, TypeError, ValueError) as exc:
                raise SandboxUnavailableError(
                    "team bubblewrap could not pin its command PID"
                ) from exc
            if worker_pidfd < 0:
                raise SandboxUnavailableError(
                    "team bubblewrap returned an invalid command pidfd"
                )

            # Pinning both identities closes numeric-PID reuse. Revalidate the
            # full launcher -> init -> sole command chain after opening the
            # command pidfd; only then may the Kernel retain it for SIGINT.
            self._assert_pidfd_alive(
                init_pidfd,
                pidfd_send_signal=pidfd_send_signal,
                identity="namespace init",
            )
            if self._proc_parent_pid(init_pid, proc_root=proc_root) != launcher:
                raise SandboxUnavailableError(
                    "team bubblewrap namespace init changed launcher"
                )
            if self._proc_direct_children(launcher, proc_root=proc_root) != (init_pid,):
                raise SandboxUnavailableError(
                    "team bubblewrap launcher changed its namespace-init child"
                )
            if self._proc_direct_children(init_pid, proc_root=proc_root) != (
                command_pid,
            ):
                raise SandboxUnavailableError(
                    "team bubblewrap namespace init changed its command child"
                )
            if self._proc_parent_pid(command_pid, proc_root=proc_root) != init_pid:
                raise SandboxUnavailableError("team bubblewrap command changed parent")
            self._assert_pidfd_alive(
                worker_pidfd,
                pidfd_send_signal=pidfd_send_signal,
                identity="command",
            )
            self._assert_pidfd_alive(
                init_pidfd,
                pidfd_send_signal=pidfd_send_signal,
                identity="namespace init",
            )
            adopted = True
        except (OSError, UnicodeError) as exc:
            raise SandboxUnavailableError(
                "team bubblewrap could not validate its procfs process tree"
            ) from exc
        finally:
            if worker_pidfd is not None and not adopted:
                try:
                    os.close(worker_pidfd)
                except OSError:
                    pass
            if init_pidfd is not None:
                try:
                    os.close(init_pidfd)
                except OSError:
                    pass
        self._bwrap_launcher_pid = launcher
        self._bwrap_worker_pidfd = worker_pidfd

    def _reset_bwrap_process_identity(self) -> None:
        for field in ("_bwrap_info_read_fd", "_bwrap_info_write_fd"):
            descriptor = getattr(self, field)
            setattr(self, field, None)
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        worker_pidfd = self._bwrap_worker_pidfd
        self._bwrap_worker_pidfd = None
        self._bwrap_launcher_pid = None
        if worker_pidfd is not None:
            try:
                os.close(worker_pidfd)
            except OSError:
                pass

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

        Six of the branches below return True having delivered nothing, which
        is correct for what the return value *means* and useless to a caller
        that has to tell a user whether their stop worked.  Each one now also
        records why, and `take_interrupt_gap()` is how `Kernel.interrupt()`
        reads it back.  Nothing here changes what this returns: its bool is a
        question about ownership and always was.
        """

        launcher = int(launcher_pid)
        self._interrupt_gap = None
        if not (
            launcher > 0
            and self.status.enforced
            and self.status.backend == "bubblewrap"
        ):
            return False

        pidfd_send_signal = getattr(signal, "pidfd_send_signal", None)
        if self._read_isolation is not None:
            # In a private PID namespace the command is a grandchild of the
            # outer launcher. Use only the persistent pidfd established below
            # bubblewrap's authenticated-by-inheritance namespace-init report;
            # the single-user procfs resolver would target that init instead.
            if (
                self._bwrap_launcher_pid != launcher
                or self._bwrap_worker_pidfd is None
                or not callable(pidfd_send_signal)
            ):
                self._report_interrupt_gap(
                    "bubblewrap did not provide a pinned command identity"
                )
                return True
            try:
                pidfd_send_signal(self._bwrap_worker_pidfd, int(signum), None, 0)
            except (OSError, ValueError) as error:
                self._report_interrupt_gap(
                    f"the pinned worker did not accept the signal ({error})"
                )
            return True

        pidfd_open = getattr(os, "pidfd_open", None)
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
                self._report_interrupt_gap(
                    "the worker exited between being pinned and being signalled"
                )
                return True
            pidfd_send_signal(pidfd, int(signum), None, 0)
        except (OSError, ValueError) as error:
            self._report_interrupt_gap(
                f"the pinned worker did not accept the signal ({error})"
            )
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
        # Recorded on every occurrence; printed once. A caller asking "did my
        # stop land?" needs this call's answer, not whether some earlier call
        # already used up the one print this kernel is allowed.
        self._interrupt_gap = reason
        if self._interrupt_gap_reported:
            return
        self._interrupt_gap_reported = True
        print(
            "[openai4s] cell interrupt cannot reach the sandboxed worker "
            f"({reason}); the stop request is dropped and a stuck cell is "
            "recovered by the watchdog instead",
            file=sys.stderr,
        )

    def take_interrupt_gap(self) -> str | None:
        """Why the last `send_interrupt` reached nobody, consumed once.

        Consumed rather than read, so a stale reason cannot be reported against
        a later stop that actually worked.
        """
        reason = self._interrupt_gap
        self._interrupt_gap = None
        return reason

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._reset_bwrap_process_identity()
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
    read_isolation: KernelReadIsolation | None = None,
) -> tuple[bool, str]:
    token = f"{os.getpid()}-{threading.get_ident()}"
    workspace_file = workspace / f".openai4s-sandbox-test-{token}"
    temp_file = temp_dir / f"self-test-{token}"
    isolation = _workspace_isolation_paths(workspace, read_isolation)
    sibling_roots: list[Path] = []
    sibling_files: list[Path] = []
    allowed_roots = tuple(str(root) for root in isolation[2]) if isolation else ()
    allowed_files: list[Path] = []
    created_allowed_files: list[Path] = []
    try:
        outside_root = _allocate_outside_probe(workspace, temp_dir)
    except OSError as exc:
        return False, f"self-test could not allocate deny probe: {exc}"
    if isolation is not None:
        for root in isolation[1]:
            try:
                sibling_root = Path(
                    tempfile.mkdtemp(
                        prefix=".openai4s-sandbox-read-deny-",
                        dir=str(root),
                    )
                )
                sibling_file = sibling_root / "must-not-read"
                sibling_file.write_text("openai4s-sibling-secret", encoding="utf-8")
                sibling_file.chmod(0o600)
                sibling_roots.append(sibling_root)
                sibling_files.append(sibling_file)
            except OSError as exc:
                shutil.rmtree(outside_root, ignore_errors=True)
                for candidate in sibling_roots:
                    shutil.rmtree(candidate, ignore_errors=True)
                return False, (
                    f"self-test could not allocate read-deny probe under {root}: {exc}"
                )
        for root_text in allowed_roots:
            try:
                probe, created = _allow_read_probe(Path(root_text), token)
                allowed_files.append(probe)
                if created:
                    created_allowed_files.append(probe)
            except OSError as exc:
                shutil.rmtree(outside_root, ignore_errors=True)
                for candidate in sibling_roots:
                    shutil.rmtree(candidate, ignore_errors=True)
                for candidate in created_allowed_files:
                    candidate.unlink(missing_ok=True)
                return False, (
                    f"self-test could not allocate read-allow probe under "
                    f"{root_text}: {exc}"
                )
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
        json.dumps([str(path) for path in sibling_files]),
        json.dumps([str(path) for path in allowed_files]),
    ]
    if backend == "seatbelt":
        command = wrap_seatbelt_command(
            probe,
            executable=executable,
            workspace=workspace,
            temp_dir=temp_dir,
            allow_raw_network=allow_raw_network,
            deny_read=deny_read,
            read_isolation=read_isolation,
        )
    else:
        command = wrap_bwrap_command(
            probe,
            executable=executable,
            workspace=workspace,
            temp_dir=temp_dir,
            allow_raw_network=allow_raw_network,
            deny_read=deny_read,
            read_isolation=read_isolation,
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
        for sibling_root in sibling_roots:
            shutil.rmtree(sibling_root, ignore_errors=True)
        for candidate in created_allowed_files:
            candidate.unlink(missing_ok=True)

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
    checks = payload.get("checks") if payload else None
    required_checks = ["workspace_write", "temp_write", "outside_write_blocked"]
    if not allow_raw_network:
        required_checks.append("network_blocked")
    if isolation is not None:
        required_checks.extend(
            [
                "sibling_read_blocked",
                "sibling_symlink_read_blocked",
                "sibling_hardlink_read_blocked",
            ]
        )
        if allowed_roots:
            required_checks.append("allowed_roots_readable")
    if (
        returncode == 0
        and payload
        and payload.get("ok") is True
        and isinstance(checks, Mapping)
        and all(checks.get(key) is True for key in required_checks)
    ):
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
    read_isolation: KernelReadIsolation | None = None,
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
    raw_allowed_roots = (
        tuple(read_isolation.allowed_roots) if read_isolation is not None else ()
    )
    isolation = _workspace_isolation_paths(workspace_path, read_isolation)
    normalized_isolation = (
        KernelReadIsolation(
            roots=tuple(str(root) for root in isolation[1]),
            allowed_roots=tuple(str(root) for root in isolation[2]),
        )
        if isolation
        else None
    )
    # A requested multi-tenant read boundary is an authorization control, not
    # a best-effort hardening switch. `auto` may degrade for a single-user
    # scientific kernel, but a team kernel must never run when this boundary
    # is absent or its read-bypass self-test fails.
    must_enforce = requested_mode == "enforce" or normalized_isolation is not None
    if allow_raw_network is None:
        allow_network = _parse_bool(
            os.environ.get(_RAW_NETWORK_ENV), name=_RAW_NETWORK_ENV, default=False
        )
    else:
        allow_network = bool(allow_raw_network)

    if requested_mode == "off":
        if normalized_isolation is not None:
            raise SandboxUnavailableError(
                "team read isolation was requested, but "
                f"{_SANDBOX_ENV}=off disables the required OS boundary"
            )
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
        if must_enforce:
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
            if must_enforce:
                raise SandboxUnavailableError(status.warning)
            _warn_once(status.warning or status.detail)
            return KernelSandbox(status=status)

    if normalized_isolation is not None:
        _assert_no_external_workspace_hardlinks(workspace_path)
    try:
        temp_parent: Path | None = None
        if normalized_isolation is not None:
            protected_workspace_root = next(
                Path(root)
                for root in normalized_isolation.roots
                if Path(root) in workspace_path.parents
            )
            temp_parent = protected_workspace_root / "kernel-temp"
            if temp_parent.is_symlink():
                raise SandboxConfigurationError(
                    f"team kernel temp root cannot be a symlink: {temp_parent}"
                )
            temp_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if temp_parent.resolve().parent != protected_workspace_root:
                raise SandboxConfigurationError(
                    f"team kernel temp root escapes protected data: {temp_parent}"
                )
        temp_path = Path(
            tempfile.mkdtemp(
                prefix="openai4s-kernel-",
                dir=str(temp_parent) if temp_parent is not None else None,
            )
        ).resolve()
    except OSError as exc:
        detail = f"{detection_detail}; private temp allocation failed: {exc}"
        status = _degraded_status(
            mode=requested_mode,
            workspace=workspace_path,
            backend=backend,
            detail=detail,
        )
        if must_enforce:
            raise SandboxUnavailableError(status.warning) from exc
        _warn_once(status.warning or status.detail)
        return KernelSandbox(status=status)
    if normalized_isolation is not None:
        # The private temp is inside the protected data root so another Cell's
        # sandbox cannot enumerate it. Also hide the canonical system temp root:
        # stale pre-upgrade ``openai4s-kernel-*`` directories live there and
        # same-uid Cells could otherwise enumerate them. Rebind this one exact
        # directory rw.
        system_temp_root = Path(tempfile.gettempdir()).expanduser().resolve()
        if system_temp_root == Path(system_temp_root.anchor):
            shutil.rmtree(temp_path, ignore_errors=True)
            raise SandboxConfigurationError(
                "system temp resolves to a filesystem root; team isolation "
                "cannot safely mask it"
            )
        isolation = _workspace_isolation_paths(
            workspace_path,
            KernelReadIsolation(
                roots=(*normalized_isolation.roots, system_temp_root),
                # Preserve exact exceptions which were outside the original
                # protected roots.  An interpreter/Skill checkout can live
                # below the canonical system temp directory (CI commonly
                # does this); it only becomes policy-relevant after that root
                # is added to hide stale sibling kernel temps.
                allowed_roots=(*raw_allowed_roots, temp_path),
            ),
        )
        assert isolation is not None
        normalized_isolation = KernelReadIsolation(
            roots=tuple(str(root) for root in isolation[1]),
            allowed_roots=tuple(str(root) for root in isolation[2]),
        )
    deny_read = _default_secret_read_denials(workspace_path)
    passed, self_test_detail = _run_self_test(
        backend=backend,
        executable=executable,
        workspace=workspace_path,
        temp_dir=temp_path,
        allow_raw_network=allow_network,
        runner=runner,
        deny_read=deny_read,
        read_isolation=normalized_isolation,
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
        if must_enforce:
            raise SandboxUnavailableError(status.warning)
        _warn_once(status.warning or status.detail)
        return KernelSandbox(status=status)
    if normalized_isolation is not None:
        try:
            _assert_no_external_workspace_hardlinks(workspace_path)
        except SandboxUnavailableError:
            shutil.rmtree(temp_path, ignore_errors=True)
            raise

    status = SandboxStatus(
        mode=requested_mode,
        state="enabled",
        backend=backend,
        enforced=True,
        self_test_passed=True,
        network_policy="raw_allowed" if allow_network else "blocked",
        workspace=str(workspace_path),
        temp_dir=str(temp_path),
        detail=(
            f"{detection_detail}; {self_test_detail}"
            + (
                "; team reads isolated under "
                + ", ".join(str(root) for root in normalized_isolation.roots)
                if normalized_isolation is not None
                else ""
            )
        ),
    )
    return KernelSandbox(
        status=status,
        executable=executable,
        temp_dir=str(temp_path),
        allow_raw_network=allow_network,
        owns_temp_dir=True,
        deny_read=deny_read,
        read_isolation=normalized_isolation,
    )


__all__ = [
    "KernelReadIsolation",
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
