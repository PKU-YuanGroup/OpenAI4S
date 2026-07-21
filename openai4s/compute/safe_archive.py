"""Hostile-input-safe tar extraction for remote-compute harvests.

An ``out.tar.gz`` arrives from a remote machine we do not control, so it is
attacker-shaped input even on the happy path: a compromised or merely buggy job
can hand back absolute paths, ``..`` traversal, symlinks that redirect a later
member's write outside the harvest dir, device/FIFO nodes, or a gzip bomb.

``tarfile.extractall`` defends against none of that on Python 3.10-3.13, where
the default extraction filter is still ``fully_trusted``.

The contract here is enumerate-then-extract:

  1. read the member list and reject the whole archive if *any* member is
     unsafe — a partial extraction of a hostile archive is still a compromise;
  2. stream each member into a private temp dir under a hard byte cap, so a
     bomb trips the cap instead of the disk;
  3. only once every member has landed and verified, move the tree into place.

Rejection is all-or-nothing and raises ``UnsafeArchiveError``. Callers map that
to a failed harvest — never to a partial success.
"""
from __future__ import annotations

import shutil
import tarfile
from pathlib import Path

# Caps sized for a scientific harvest (model outputs, logs, structures) while
# still bounding a bomb. Callers may tighten but should not loosen silently.
DEFAULT_MAX_FILES = 20_000
DEFAULT_MAX_FILE_BYTES = 2 * 1024**3  # 2 GiB per member
DEFAULT_MAX_TOTAL_BYTES = 8 * 1024**3  # 8 GiB decompressed
DEFAULT_MAX_RATIO = 200  # decompressed:compressed


class UnsafeArchiveError(Exception):
    """The archive was rejected before anything was written to ``dest``."""


def _reject(name: str, reason: str) -> "UnsafeArchiveError":
    return UnsafeArchiveError(f"unsafe archive member {name!r}: {reason}")


def _validate_name(member: tarfile.TarInfo, dest: Path) -> Path:
    """Return the resolved destination path, or raise if the name escapes."""
    name = member.name
    if not name or name in (".", "./"):
        raise _reject(name, "empty member name")
    # Defense in depth: tar's name field is NUL-terminated, so tarfile already
    # truncates at the first NUL and this cannot fire for a real archive. It
    # guards the function's own contract for any non-tarfile caller.
    if "\x00" in name:
        raise _reject(name, "NUL byte in name")
    # PureWindowsPath-style drive/UNC prefixes are absolute on Windows even
    # though posixpath calls them relative.
    if name.startswith(("/", "\\")) or (len(name) > 1 and name[1] == ":"):
        raise _reject(name, "absolute path")
    parts = Path(name).parts
    if ".." in parts:
        raise _reject(name, "'..' traversal")
    # Resolve without following links (dest itself is a fresh dir we own).
    target = (dest / name).resolve()
    dest_resolved = dest.resolve()
    if target != dest_resolved and dest_resolved not in target.parents:
        raise _reject(name, f"resolves outside destination ({target})")
    return target


def _validate_type(member: tarfile.TarInfo) -> None:
    if member.issym() or member.islnk():
        raise _reject(member.name, "symlink/hardlink members are not allowed")
    if member.ischr() or member.isblk() or member.isfifo() or member.isdev():
        raise _reject(member.name, "device/FIFO members are not allowed")
    if not (member.isfile() or member.isdir()):
        raise _reject(member.name, f"unsupported member type {member.type!r}")


def safe_extract_tar(
    archive: Path,
    dest: Path,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    max_ratio: int = DEFAULT_MAX_RATIO,
) -> list[Path]:
    """Extract ``archive`` into ``dest``, rejecting hostile members outright.

    Returns the list of extracted file paths. Raises ``UnsafeArchiveError`` if
    the archive violates any path, type, or size constraint — in which case
    ``dest`` is left untouched.
    """
    dest = Path(dest)
    compressed_size = archive.stat().st_size

    with tarfile.open(archive, "r:gz") as tf:
        members = tf.getmembers()

        if len(members) > max_files:
            raise UnsafeArchiveError(
                f"archive has {len(members)} members, over the {max_files} cap"
            )

        # Pass 1: validate every member before writing a single byte.
        declared_total = 0
        for m in members:
            _validate_type(m)
            _validate_name(m, dest)
            if m.isfile():
                if m.size > max_file_bytes:
                    raise _reject(
                        m.name, f"declared size {m.size} over the {max_file_bytes} cap"
                    )
                declared_total += m.size
        if declared_total > max_total_bytes:
            raise UnsafeArchiveError(
                f"archive declares {declared_total} bytes, over the "
                f"{max_total_bytes} cap"
            )
        if compressed_size > 0 and declared_total / compressed_size > max_ratio:
            raise UnsafeArchiveError(
                f"compression ratio {declared_total / compressed_size:.0f}:1 over "
                f"the {max_ratio}:1 cap (possible decompression bomb)"
            )

        # Pass 2: stream into a private staging dir next to dest. The declared
        # sizes above are only a header claim, so the real cap is enforced here
        # against bytes actually written.
        dest.mkdir(parents=True, exist_ok=True)
        staging = Path(dest).parent / f".{dest.name}.unpack"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        try:
            written_total = 0
            extracted: list[Path] = []
            for m in members:
                target = _validate_name(m, staging)
                if m.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                src = tf.extractfile(m)
                if src is None:
                    continue
                written = 0
                with open(target, "wb") as out:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        written_total += len(chunk)
                        if written > max_file_bytes:
                            raise _reject(
                                m.name, f"actual size exceeds the {max_file_bytes} cap"
                            )
                        if written_total > max_total_bytes:
                            raise UnsafeArchiveError(
                                f"archive exceeds the {max_total_bytes} byte cap "
                                f"during extraction (possible decompression bomb)"
                            )
                        out.write(chunk)
                extracted.append(target)

            # Verified — move into place. Merge rather than replace: an ssh
            # harvest drops logs into the same dest.
            moved: list[Path] = []
            for path in extracted:
                rel = path.relative_to(staging)
                final = dest / rel
                final.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(final))
                moved.append(final)
            return moved
        finally:
            shutil.rmtree(staging, ignore_errors=True)
