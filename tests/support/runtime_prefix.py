"""Real interpreter prefixes for tests that observe env binding through sys.executable.

A bare ``prefix/bin/python -> sys.executable`` symlink is not a virtual
environment. Invoked that way, CPython 3.13 reports the symlink path in
``sys.executable`` while 3.14 reports the resolved base binary. Artifact
provenance records that self-report as ``interpreter``, so a fixture that is
only a symlink makes the same kernel look like two different interpreters.

A prefix with ``pyvenv.cfg`` is the actual selection mechanism: the cell
reports the selected env's launcher on both 3.13 and 3.14, and the
assertions that name that launcher stay strong rather than being relaxed
to accept a resolved path.

``symlinks=True`` is load-bearing on Linux 3.10: a copied
python-build-standalone launcher loses its runtime tree and cannot start.
A symlink plus ``pyvenv.cfg`` stays runnable and prefix-aware.
"""

from __future__ import annotations

import venv
from pathlib import Path


def install_runtime_prefix(prefix: Path) -> Path:
    """Create ``prefix`` as a real venv whose interpreter self-reports this prefix."""
    prefix = Path(prefix)
    venv.EnvBuilder(with_pip=False, symlinks=True).create(str(prefix))
    interpreter = prefix / "bin" / "python"
    if not interpreter.is_file():
        raise RuntimeError(f"venv at {prefix} has no bin/python")
    if not (prefix / "pyvenv.cfg").is_file():
        raise RuntimeError(f"venv at {prefix} has no pyvenv.cfg")
    return prefix


def install_named_runtime(root: Path, name: str) -> Path:
    """A named env directory whose ``bin/python`` is a real prefix-aware launcher."""
    return install_runtime_prefix(Path(root) / name)
