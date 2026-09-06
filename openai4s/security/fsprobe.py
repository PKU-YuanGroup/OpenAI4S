"""Filesystem probes that keep an unreadable path on the caller's error path.

CPython 3.14 reimplemented ``Path.is_symlink()``, ``is_dir()``, ``is_file()``
and ``exists()`` on top of ``os.path`` -- which swallows *every* ``OSError``
and answers ``False``. Through 3.13 only ENOENT/ENOTDIR/EBADF/ELOOP were
swallowed; EACCES, EIO and friends raised. Every guard written as
``try: p.is_symlink() except OSError: refuse`` therefore stops refusing on
3.14 (the container's interpreter): an unreadable path becomes "not a
symlink", and the refusal branch is dead code.

These helpers go through ``os.lstat`` so the distinction survives: absence is
a plain answer, everything else the kernel would not tell us propagates for
the caller to fail closed on.
"""

from __future__ import annotations

import os
import stat
from typing import Literal, Optional, Union

PathLike = Union[str, "os.PathLike[str]"]
Kind = Literal["symlink", "dir", "file", "other"]


def lstat_is_symlink(path: PathLike, *, missing_ok: bool = True) -> bool:
    """``S_ISLNK`` of ``os.lstat(path)``.

    A missing path is not a symlink (``missing_ok``) or raises
    ``FileNotFoundError``; any other ``OSError`` propagates unchanged.
    """
    try:
        return stat.S_ISLNK(os.lstat(path).st_mode)
    except (FileNotFoundError, NotADirectoryError):
        if missing_ok:
            return False
        raise


def lstat_kind(path: PathLike) -> Optional[Kind]:
    """What ``path`` is without following it: ``None`` when absent.

    ``"symlink"`` for any link (dangling or not), ``"dir"``/``"file"`` for a
    real directory or regular file, ``"other"`` for sockets, FIFOs and
    devices. Raises ``OSError`` for anything the caller must not guess about.
    """
    try:
        mode = os.lstat(path).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return None
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "dir"
    if stat.S_ISREG(mode):
        return "file"
    return "other"
