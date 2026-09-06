"""The lstat probes keep an unreadable path on the caller's error path.

Stated so it fails on the interpreter that motivated it: on CPython 3.14
``Path.is_symlink()`` answers ``False`` for a path under a mode-000 directory,
which turned every ``try: is_symlink() except OSError: refuse`` guard into
dead code. The probes must raise there, on every interpreter in the matrix,
while still answering absence and links plainly.
"""

from __future__ import annotations

import os
import stat

import pytest

from openai4s.security.fsprobe import lstat_is_symlink, lstat_kind


def test_absence_and_links_are_plain_answers(tmp_path):
    real_dir = tmp_path / "dir"
    real_dir.mkdir()
    real_file = tmp_path / "file"
    real_file.write_text("x", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(real_file)
    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "nowhere")

    assert lstat_is_symlink(link) is True
    assert lstat_is_symlink(dangling) is True
    assert lstat_is_symlink(real_file) is False
    assert lstat_is_symlink(tmp_path / "absent") is False
    assert lstat_is_symlink(real_file / "below-a-file") is False
    with pytest.raises(FileNotFoundError):
        lstat_is_symlink(tmp_path / "absent", missing_ok=False)

    assert lstat_kind(real_dir) == "dir"
    assert lstat_kind(real_file) == "file"
    assert lstat_kind(link) == "symlink"
    assert lstat_kind(dangling) == "symlink"
    assert lstat_kind(tmp_path / "absent") is None
    assert lstat_kind(real_file / "below-a-file") is None
    if hasattr(os, "mkfifo"):
        fifo = tmp_path / "fifo"
        os.mkfifo(fifo)
        assert lstat_kind(fifo) == "other"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads through mode 000")
def test_an_unreadable_parent_raises_instead_of_answering_false(tmp_path):
    sealed = tmp_path / "sealed"
    sealed.mkdir()
    inside = sealed / "inside"
    inside.symlink_to(tmp_path)
    sealed.chmod(0)
    try:
        with pytest.raises(PermissionError):
            lstat_is_symlink(inside)
        with pytest.raises(PermissionError):
            lstat_kind(inside)
    finally:
        sealed.chmod(stat.S_IRWXU)
