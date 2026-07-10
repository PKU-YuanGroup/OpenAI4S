"""First-class persistent R kernel — the R sibling of the python worker.

The host executes exactly two kinds of instructions: python cells on the
Jupyter-style worker (kernel/worker.py) and R cells on kernel/r_worker.R. Both
speak the same JSON-per-line frame protocol and are driven by the SAME manager
(kernel/manager.Kernel) — this module only resolves an R interpreter and builds
the argv that gives r_worker.R the fd discipline worker.py gets from its dup2
swap:

    sh -c 'exec "$0" --vanilla "$1" 3>&1 4<&0 </dev/null 1>&2' <Rscript> <r_worker.R>

- protocol OUT rides fd 3 (aliased from the stdout pipe the manager reads),
- protocol IN rides fd 4 (aliased from the stdin pipe the manager writes),
- fd 0 becomes /dev/null so R code reading stdin cannot eat protocol frames,
- fd 1 is aliased to stderr so stray C-level prints never corrupt the wire,
- `exec` keeps the spawned pid == R's pid so Kernel.interrupt()'s SIGINT lands
  in R (caught there as an interrupt condition → interrupted=True).

The R kernel is an ANALYSIS kernel: it never emits host_call frames and has no
`host` object — completion (host.submit_output) stays on the python control
plane. Pure stdlib.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from openai4s.kernel.manager import Kernel

_R_WORKER = Path(__file__).resolve().parent / "r_worker.R"

# Shell wrapper performing the fd swap; "$0" = Rscript, "$1" = r_worker.R.
_SH_WRAP = 'exec "$0" --vanilla "$1" 3>&1 4<&0 </dev/null 1>&2'


def resolve_r_interpreter(env: object | None = None) -> str | None:
    """Path to the Rscript that should run the R kernel, or None.

    Resolution order: the selected environment's own ``rscript`` → a discovered
    environment literally named ``r`` → any discovered env carrying Rscript →
    ``Rscript`` on PATH. Never substitutes a python interpreter — an R cell
    either gets a real R or a soft error (no silent-fallback, unlike
    ``Kernel(python=None)``).
    """
    rs = getattr(env, "rscript", None) if env is not None else None
    if rs:
        return str(rs)
    try:
        from openai4s.kernel.environments import discover_environments

        envs = discover_environments()
    except Exception:  # noqa: BLE001 — discovery must never break resolution
        envs = []
    for e in envs:
        if e.name == "r" and e.rscript:
            return str(e.rscript)
    for e in envs:
        if e.rscript:
            return str(e.rscript)
    return shutil.which("Rscript")


def r_argv(rscript: str) -> list[str]:
    """The full spawn argv for an R kernel using ``rscript``."""
    return ["/bin/sh", "-c", _SH_WRAP, rscript, str(_R_WORKER)]


def spawn_r_kernel(
    cwd: str | None = None,
    rscript: str | None = None,
    env: object | None = None,
) -> Kernel:
    """Spawn a persistent R kernel, reusing the language-neutral manager.

    ``env`` (an ``environments.Environment``) narrows interpreter resolution to
    that env's Rscript first. Raises RuntimeError when no R is available — the
    caller turns that into a soft observation for the model.
    """
    rs = rscript or resolve_r_interpreter(env)
    if not rs:
        raise RuntimeError(
            "no R interpreter available: build the prebuilt 'r' environment "
            "(openai4s setup) or install R so Rscript is on PATH"
        )
    env_root = None
    env_name = None
    if env is not None and getattr(env, "rscript", None):
        root = getattr(env, "root", None)
        if root is not None and getattr(env, "is_conda", False):
            env_root = str(root)
            env_name = getattr(env, "name", None)
    return Kernel(
        dispatcher=None,  # analysis kernel: r_worker.R never emits host_call
        cwd=cwd,
        mode="r",
        env_root=env_root,
        env_name=env_name,
        argv=r_argv(rs),
    )
