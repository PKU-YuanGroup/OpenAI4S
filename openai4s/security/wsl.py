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


def is_wsl() -> bool:
    return platform.system() == "Linux" and "microsoft" in platform.release().lower()


def windows_mounts() -> tuple[str, ...]:
    """Find DrvFs mounts, including custom automount roots and bind aliases."""
    mounts = []
    for line in Path("/proc/self/mountinfo").read_text("utf-8").splitlines():
        fields = line.split()
        separator = fields.index("-")
        fs_type = fields[separator + 1]
        options = " ".join(fields[separator + 2 :])
        if fs_type == "drvfs" or (fs_type == "9p" and "aname=drvfs" in options):
            path = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), fields[4])
            mounts.append(path)
    return tuple(sorted(set(mounts), key=lambda path: (path.count("/"), path)))


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


def socket_filter() -> bytes:
    """Classic BPF for bubblewrap: deny Hyper-V/vsock outside network namespaces.

    Reject alternate ABIs so socketcall/x32 cannot bypass the socket rule.
    Local Unix sockets and ordinary Linux networking retain their own policy.
    """
    arch, socket_nr = {
        "x86_64": (0xC000003E, 41),
        "aarch64": (0xC00000B7, 198),
    }[platform.machine()]
    instructions = (
        (0x20, 0, 0, 4),  # seccomp_data.arch
        (0x15, 1, 0, arch),
        (0x06, 0, 0, 0x80000000),  # KILL_PROCESS: foreign ABI
        (0x20, 0, 0, 0),  # seccomp_data.nr
        (0x35, 0, 1, 0x40000000),
        (0x06, 0, 0, 0x00050026),  # ENOSYS: x32 ABI
        (0x15, 0, 3, socket_nr),
        (0x20, 0, 0, 16),  # args[0]: socket family
        (0x15, 0, 1, 40),  # AF_VSOCK
        (0x06, 0, 0, 0x00050001),  # EPERM
        (0x06, 0, 0, 0x7FFF0000),  # ALLOW
    )
    return b"".join(struct.pack("=HBBI", *item) for item in instructions)
