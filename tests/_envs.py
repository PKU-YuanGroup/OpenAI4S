"""Real interpreter prefixes for the environment-binding tests.

A conda-shaped fixture used to be one bare symlink, ``<env>/bin/python ->
sys.executable``.  That stopped self-reporting the fixture's own prefix: a
copied python-build-standalone launcher loses its runtime tree on Linux 3.10,
and a bare symlink resolves to the base interpreter on newer builds, so the
kernel's ``sys.executable`` no longer named the env the test had selected.
``pyvenv.cfg`` plus a link is what keeps the launcher runnable *and*
prefix-aware on every interpreter the CI matrix runs.

One home for that incantation, so the next interpreter-specific tweak lands
once instead of in each test module that builds an env.
"""

from __future__ import annotations

import venv
from pathlib import Path


def real_python_prefix(prefix: Path) -> Path:
    """A pip-free venv at ``prefix`` whose ``bin/python`` reports ``prefix``."""
    venv.EnvBuilder(with_pip=False, symlinks=True).create(prefix)
    return prefix
