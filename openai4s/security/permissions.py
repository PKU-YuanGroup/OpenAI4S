"""Owner-only permissions for the data directory and its sensitive files.

The SQLite database can hold credentials in the clear (model-profile API keys,
connector env, managed-endpoint tokens), and it was created at the process
umask — typically 0644, i.e. readable by every local account. On a shared
workstation, a lab server, or any box with a second login, that is the whole
credential store one `cat` away.

The SecretBroker that was once "still the actual fix" has landed:
`secret_broker.py` keeps a credential behind an opaque reference in the system
keychain or the process environment, `secret_migration.py` moves existing rows
over, and `auto` fails closed rather than degrading to plaintext. That does not
retire this module. `OPENAI4S_SECRET_STORE=plaintext` remains a mode a user can
select by name; a value that has not been migrated yet, or whose migration
failed, deliberately stays readable where it is so the install keeps working;
and the file mode is what makes a backup or an rsync of ~/.openai4s not
silently world-readable at the far end regardless of which store is in use.

Stdlib only, and best-effort by contract: a filesystem that cannot represent
POSIX modes (Windows, FAT, some network mounts) must not stop the daemon from
starting. Callers get a boolean so they can report the posture rather than
assume it.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

DIR_MODE = 0o700
FILE_MODE = 0o600

_POSIX = os.name == "posix"


def harden_dir(path: Path) -> bool:
    """chmod a directory to owner-only. Returns True if the mode now matches."""
    return _chmod(path, DIR_MODE)


def harden_file(path: Path) -> bool:
    """chmod a file to owner-only. Returns True if the mode now matches."""
    return _chmod(path, FILE_MODE)


def harden_db(db_path: Path) -> bool:
    """Tighten a SQLite database and the sidecars it may create.

    -wal and -shm carry the same committed data as the database itself, so
    hardening only the main file would leave the contents readable through
    them whenever WAL mode is on.
    """
    ok = _chmod(db_path, FILE_MODE)
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists():
            ok = _chmod(sidecar, FILE_MODE) and ok
    return ok


def _chmod(path: Path, mode: int) -> bool:
    if not _POSIX:
        # Windows: the POSIX bits are not meaningful and os.chmod only moves
        # the read-only flag. Restricting to the current user needs an ACL
        # edit, which is out of scope here — report the truth instead of
        # pretending, so a caller can surface "not hardened" rather than
        # claim a boundary that is not there.
        return False
    try:
        os.chmod(path, mode)
    except OSError:
        return False
    return verify(path, mode)


def verify(path: Path, mode: int) -> bool:
    """True when the path grants nothing to group or other."""
    try:
        actual = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return False
    return actual == mode


def is_owner_only(path: Path) -> bool:
    """True when no group/other bit is set. Looser than an exact-mode check —
    use for assertions about exposure rather than about an exact value."""
    try:
        actual = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return False
    return not (actual & (stat.S_IRWXG | stat.S_IRWXO))


def posture(data_dir: Path, db_path: Path) -> dict:
    """Machine-readable report for the security/status surface."""
    return {
        "supported": _POSIX,
        "platform": sys.platform,
        "data_dir_owner_only": is_owner_only(data_dir),
        "db_owner_only": is_owner_only(db_path),
        "detail": (
            ""
            if _POSIX
            else "POSIX modes are not enforced on this platform; restrict the "
            "data directory with an ACL instead"
        ),
    }


def fsync_dir(directory: "Path") -> None:
    """Persist a directory entry, not just the bytes behind it.

    Publishing a file by `os.link`/`os.replace` is a change to the
    *directory*; fsyncing only the file leaves a crash able to keep the
    content and lose the name. For the two things this project publishes
    that way -- the daemon access token and the worker bootstrap signing
    secret -- losing the name means the next boot mints a different one and
    invalidates every credential already issued from the old.

    Lives here because both publishers need it and one of them was written
    without it: a private copy in `server/local_auth.py` is not reachable
    from `orchestration/bootstrap.py`, so the second implementation of the
    same protocol simply omitted the step.

    Best-effort by contract: not every filesystem lets a directory be
    opened for fsync, and refusing to start over a durability nicety is the
    worse trade.
    """
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


__all__ = [
    "DIR_MODE",
    "FILE_MODE",
    "fsync_dir",
    "harden_db",
    "harden_dir",
    "harden_file",
    "is_owner_only",
    "posture",
    "verify",
]
