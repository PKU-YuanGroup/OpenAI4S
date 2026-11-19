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
- `exec` keeps the shell child's pid == R's pid, so Kernel.interrupt()'s SIGINT
  lands in R directly (including when bubblewrap supervises that child) and is
  caught there as an interrupt condition with `interrupted=True`. R only
  installs that handler when the disposition it inherits is not SIG_IGN, and a
  backgrounded daemon (`./start.sh &`, nohup) passes SIG_IGN through every
  exec in this chain — kernel/transport.py's spawn boundary resets SIGINT to
  SIG_DFL in the child so the guarantee holds regardless of launch mode (the
  reset cannot live in _SH_WRAP: POSIX forbids a non-interactive shell from
  resetting a signal ignored on entry, so `trap - INT` would be a no-op).

The R kernel is an ANALYSIS kernel: it never emits host_call frames and has no
`host` object — completion (host.submit_output) stays on the python control
plane. Pure stdlib.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from openai4s.kernel.manager import Kernel
from openai4s.security.sandbox import KernelReadIsolation

_R_WORKER = Path(__file__).resolve().parent / "r_worker.R"

# Shell wrapper performing the fd swap; "$0" = Rscript, "$1" = r_worker.R.
_SH_WRAP = 'exec "$0" --vanilla "$1" 3>&1 4<&0 </dev/null 1>&2'


def resolve_r_interpreter(env: object | None = None) -> str | None:
    """Path to the Rscript that should run the R kernel, or None.

    Resolution order: the selected environment's own ``rscript`` → a discovered
    environment literally named ``r`` (the one ``openai4s setup`` builds) →
    ``Rscript`` on PATH → any other discovered env carrying Rscript. Never
    substitutes a python interpreter — an R cell either gets a real R or a soft
    error (no silent-fallback, unlike ``Kernel(python=None)``).

    PATH comes before "any other env": an R the user installed system-wide is
    the R they mean, while the first conda env that happens to ship Rscript is
    someone's personal environment (the review found every R cell running in a
    minimal conda R without survival/ggplot2 while PATH had both).

    PATH and "other env" candidates must be able to run the worker, which needs
    ``jsonlite``: a system R without it would turn every R cell into a startup
    error while a working env R sat next to it. When no candidate can, the
    first one is still returned so the spawn fails with the worker's own
    install hint instead of "no R interpreter".
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
    candidates: list[str] = []
    on_path = shutil.which("Rscript")
    if on_path:
        candidates.append(on_path)
    for e in envs:
        if e.rscript and str(e.rscript) not in candidates:
            candidates.append(str(e.rscript))
    for candidate in candidates:
        if _can_run_worker(candidate):
            return candidate
    return candidates[0] if candidates else None


#: Probe results keyed by interpreter identity. A "yes" holds until the binary
#: changes; a "no" is re-checked after a minute, so installing jsonlite takes
#: effect without restarting the daemon.
_READY: dict[tuple[str, int, int], tuple[bool, float]] = {}
_READY_LOCK = threading.Lock()
_NOT_READY_TTL_S = 60.0
_READY_PROBE = (
    'quit(status = if (requireNamespace("jsonlite", quietly = TRUE)) 0L else 3L)'
)


def _can_run_worker(rscript: str) -> bool:
    """Whether ``rscript`` has what r_worker.R needs to start (jsonlite)."""
    try:
        st = os.stat(rscript)
    except OSError:
        return False
    key = (rscript, st.st_mtime_ns, st.st_size)
    now = time.monotonic()
    with _READY_LOCK:
        cached = _READY.get(key)
    if cached is not None and (cached[0] or now - cached[1] < _NOT_READY_TTL_S):
        return cached[0]
    try:
        ok = (
            subprocess.run(
                [rscript, "--vanilla", "-e", _READY_PROBE],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        ok = False
    with _READY_LOCK:
        _READY[key] = (ok, now)
    return ok


def r_argv(rscript: str) -> list[str]:
    """The full spawn argv for an R kernel using ``rscript``."""
    return ["/bin/sh", "-c", _SH_WRAP, rscript, str(_R_WORKER)]


def spawn_r_kernel(
    cwd: str | None = None,
    rscript: str | None = None,
    env: object | None = None,
    read_isolation: KernelReadIsolation | None = None,
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
        read_isolation=read_isolation,
        # R cannot bound its own output inside a single top-level expression —
        # single threaded, no callback fires mid-expression — so the cell's two
        # streams are sunk to fifos the host drains and caps. See
        # kernel/sink_drain.py for what was measured before choosing this.
        capture_sinks=True,
    )
