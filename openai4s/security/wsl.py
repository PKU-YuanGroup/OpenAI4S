"""WSL host boundaries, kept separate from the scientific execution policy.

Windows interop is a privileged Host facility. A Cell gets a private PID
namespace and cannot see host mounts, session sockets or the interop loader.
No global WSL configuration is changed.
"""

from __future__ import annotations

import os
import platform
import re
import struct
from pathlib import Path

#: Surfaces WSL itself creates in a distribution. A Docker/Podman container on
#: a Windows host runs on the same `…-microsoft-standard-WSL2` kernel and so
#: inherits the release string, but none of these -- and it must not inherit
#: this module's policy either: masking /run and /tmp and requiring a private
#: PID namespace there would change a documented container deployment's
#: boundary on the strength of the host kernel's name.
_WSL_MARKERS = (
    "/run/WSL",
    "/mnt/wsl",
    "/proc/sys/fs/binfmt_misc/WSLInterop",
    "/proc/sys/fs/binfmt_misc/WSLInterop-late",
)


def is_wsl() -> bool:
    if platform.system() != "Linux" or "microsoft" not in platform.release().lower():
        return False
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    return any(os.path.exists(marker) for marker in _WSL_MARKERS)


def windows_mounts() -> tuple[str, ...]:
    """Find DrvFs mounts, including custom automount roots and bind aliases.

    An unreadable mount table degrades to "no Windows mounts" rather than
    raising: this runs on the kernel spawn path, inside a secret backend's
    ``available()`` probe, and before ``argparse`` in the CLI entrypoint, and
    none of those three can act on an ``OSError`` from procfs. A nested
    container, a ``hidepid`` mount or an already-replaced ``/proc`` would
    otherwise turn a missing optimisation into `openai4s --help` failing.
    """
    mounts = []
    try:
        table = Path("/proc/self/mountinfo").read_text("utf-8")
    except OSError:
        return ()
    for line in table.splitlines():
        fields = line.split()
        if "-" not in fields:
            continue
        separator = fields.index("-")
        fs_type = fields[separator + 1]
        options = " ".join(fields[separator + 2 :])
        if fs_type == "drvfs" or (fs_type == "9p" and "aname=drvfs" in options):
            path = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), fields[4])
            mounts.append(path)
    return tuple(sorted(set(mounts), key=lambda path: (path.count("/"), path)))


#: Masked roots that stay writable. `_bwrap_read_masks` covers a directory with
#: a *fresh, empty* tmpfs, which is already private to the sandbox's mount
#: namespace -- remounting those two read-only buys no isolation and only makes
#: a hardcoded `/tmp/...` write fail on WSL while succeeding on Linux and macOS.
WRITABLE_MASKS = frozenset({"/tmp", "/var/tmp"})


def read_denials() -> tuple[tuple[str, str], ...]:
    if not is_wsl():
        return ()
    return tuple(
        ("subpath", path)
        for path in (
            "/init",
            "/run",
            "/tmp",
            "/var/tmp",
            "/mnt/wsl",
            "/mnt/wslg",
            *windows_mounts(),
        )
    )


def resolver_rebinds(masked: tuple[str, ...]) -> list[str]:
    """`--ro-bind-try` args that keep DNS working under the WSL masks.

    WSL2 points ``/etc/resolv.conf`` at ``/mnt/wsl/resolv.conf``, or at
    ``/run/systemd/resolve/stub-resolv.conf`` on a systemd distribution --
    which is what ``wsl --install -d Ubuntu-24.04`` produces and what this
    launcher tells the user to install. Both live under a masked root, so the
    empty tmpfs leaves the symlink dangling and every hostname lookup in a
    network-enabled Cell fails while the same Cell resolves fine on plain
    Linux. `byoc_confinement._runtime_dns_rebinds` solves the same problem the
    same way; a ``-try`` bind is skipped when the path is absent.
    """
    try:
        resolved = os.path.realpath("/etc/resolv.conf")
    except OSError:  # pragma: no cover - unreadable /etc
        return []
    if not any(resolved.startswith(root.rstrip("/") + "/") for root in masked):
        return []
    return ["--ro-bind-try", resolved, resolved]


def linux_path(value: str) -> str:
    """Keep Linux tools in user order without traversing Windows PATH entries."""
    roots = windows_mounts()
    entries = []
    for entry in value.split(os.pathsep):
        if not entry or not os.path.isabs(entry):
            continue
        if any(entry == root or entry.startswith(root + "/") for root in roots):
            continue
        if entry not in entries:
            entries.append(entry)
    return os.pathsep.join(entries)


def powershell_path() -> str | None:
    if not is_wsl():
        return None
    for root in windows_mounts():
        candidate = (
            Path(root) / "Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        )
        if candidate.is_file():
            return str(candidate)
    return None


class UnsupportedArchitecture(RuntimeError):
    """No Hyper-V socket filter exists for this machine."""


#: `io_uring_setup(2)`. Identical on x86_64 and the generic (aarch64) table.
IO_URING_SETUP_NR = 425

#: AUDIT_ARCH_* plus the `socket(2)` syscall number, per ABI.
_SECCOMP_ARCH = {
    "x86_64": (0xC000003E, 41),
    "aarch64": (0xC00000B7, 198),
}


def socket_filter() -> bytes:
    """Classic BPF for bubblewrap: deny Hyper-V/vsock outside network namespaces.

    Reject alternate ABIs so socketcall/x32 cannot bypass the socket rule, and
    deny ``io_uring_setup``: seccomp classifies syscalls, not the operations
    submitted inside a ring, so ``IORING_OP_SOCKET`` (5.19+, and the WSL2
    kernel is newer) would open an AF_VSOCK fd without this filter ever being
    consulted -- the exact escape it exists to close. Docker and systemd block
    io_uring by default for the same reason. Local Unix sockets and ordinary
    Linux networking retain their own policy.
    """
    machine = platform.machine()
    try:
        arch, socket_nr = _SECCOMP_ARCH[machine]
    except KeyError:
        # Fail closed with a diagnosable message rather than a bare KeyError:
        # under `enforce` the caller must be able to say *why* the boundary
        # could not be built, and AF_VSOCK is not covered by --unshare-net.
        raise UnsupportedArchitecture(
            f"WSL isolation has no Hyper-V socket filter for {machine!r}"
        ) from None
    instructions = (
        (0x20, 0, 0, 4),  # seccomp_data.arch
        (0x15, 1, 0, arch),
        (0x06, 0, 0, 0x80000000),  # KILL_PROCESS: foreign ABI
        (0x20, 0, 0, 0),  # seccomp_data.nr
        (0x35, 0, 1, 0x40000000),
        (0x06, 0, 0, 0x00050026),  # ENOSYS: x32 ABI
        (0x15, 0, 1, IO_URING_SETUP_NR),
        (0x06, 0, 0, 0x00050001),  # EPERM: no ring to submit IORING_OP_SOCKET
        (0x15, 0, 3, socket_nr),
        (0x20, 0, 0, 16),  # args[0]: socket family
        (0x15, 0, 1, 40),  # AF_VSOCK
        (0x06, 0, 0, 0x00050001),  # EPERM
        (0x06, 0, 0, 0x7FFF0000),  # ALLOW
    )
    return b"".join(struct.pack("=HBBI", *item) for item in instructions)
