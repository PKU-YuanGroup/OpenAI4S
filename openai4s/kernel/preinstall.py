"""Kernel package pre-installation + on-demand install.

The persistent kernel is spawned with ``sys.executable`` (see kernel/manager.py),
so every package importable by the daemon's interpreter is available to agent
cells.  Historically the agent had to ``pip install`` a package mid-task and then
had no way to get a fresh kernel — this module fixes both halves:

  * ``core_plan()`` reports what the scientific stack is missing WITHOUT
    touching the environment. This is what daemon startup calls.

  * ``ensure_core()`` installs that plan. It runs only when a human or an
    explicit API asks — never as a side effect of ``openai4s serve``.

  * ``install(packages)`` performs an on-demand install (used by the
    ``POST /api/kernel/install`` endpoint and the ``host.pip_install`` tool). The
    caller then restarts the session kernel (kernel/manager.py ``Kernel.restart``)
    so the new package is picked up by a clean process.

Startup does not install. It used to: ``serve`` fired ``ensure_core`` on a
daemon thread, which resolved ~23 unpinned package names against PyPI and
installed them with ``--break-system-packages`` into whatever interpreter the
daemon happened to run under. That made three things true that should not be:
starting the daemon mutated the user's Python environment, what you got
depended on what PyPI served that day, and a cold start off the network failed
in a background thread nobody was watching. Diagnosing and installing are now
separate: startup reports, the user decides.

Homebrew / distro pythons are PEP-668 "externally managed"; installs pass
``--break-system-packages`` (a harmless no-op on unmanaged envs) so an
explicitly requested install works in the environments this daemon runs in.
That flag is why the implicit-at-startup behaviour was worth removing rather
than merely narrowing: it is exactly the flag that makes an unattended install
capable of stepping on a system interpreter.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from importlib import util as _importutil
from pathlib import Path
from typing import Any

# (pip name, import name) — the always-available baseline. Only packages that
# wheel reliably on modern CPythons (incl. 3.13/3.14) live here; heavy GPU /
# compiled stacks are opt-in via OPTIONAL below.
CORE_PACKAGES: list[tuple[str, str]] = [
    ("numpy", "numpy"),
    ("pandas", "pandas"),
    ("scipy", "scipy"),
    ("matplotlib", "matplotlib"),
    ("seaborn", "seaborn"),
    ("scikit-learn", "sklearn"),
    ("statsmodels", "statsmodels"),
    ("sympy", "sympy"),
    ("networkx", "networkx"),
    ("biopython", "Bio"),
    ("pillow", "PIL"),
    ("requests", "requests"),
    ("httpx", "httpx"),
    ("beautifulsoup4", "bs4"),
    ("lxml", "lxml"),
    ("openpyxl", "openpyxl"),
    ("tabulate", "tabulate"),
    ("tqdm", "tqdm"),
    ("pyyaml", "yaml"),
    ("plotly", "plotly"),
    ("h5py", "h5py"),
    ("pyarrow", "pyarrow"),
    ("python-dateutil", "dateutil"),
    ("regex", "regex"),
]

# Opt-in catalog surfaced in the UI (Customize → Compute → "Install package").
# These are large / slow / may lack wheels on the newest CPython; the user (or
# agent) installs them explicitly, then restarts the kernel.
OPTIONAL_PACKAGES: list[dict] = [
    {"name": "logomaker", "import": "logomaker", "note": "sequence logos"},
    {"name": "anndata", "import": "anndata", "note": "single-cell containers"},
    {"name": "scanpy", "import": "scanpy", "note": "single-cell RNA-seq"},
    {"name": "umap-learn", "import": "umap", "note": "UMAP embedding"},
    {"name": "rdkit", "import": "rdkit", "note": "cheminformatics"},
    {"name": "numba", "import": "numba", "note": "JIT acceleration"},
    {"name": "torch", "import": "torch", "note": "PyTorch (large)"},
    {"name": "transformers", "import": "transformers", "note": "HF models"},
    {"name": "gseapy", "import": "gseapy", "note": "gene-set enrichment"},
    {"name": "pysam", "import": "pysam", "note": "SAM/BAM/VCF"},
]

# Live progress, read by GET /api/environments/status.
STATUS: dict = {
    # idle | needs_provision | installing | ready | error
    #
    # needs_provision is the honest resting state for a cold install: packages
    # are missing and the daemon will not install them behind the user's back.
    "phase": "idle",
    "started_at": None,
    "finished_at": None,
    "installing": [],  # pip names currently being installed
    "installed": [],  # pip names installed this run
    "failed": [],  # [{name, error}]
    "missing": [],  # pip names a plan would install
    "message": "",
}
_LOCK = threading.Lock()


def _importable(import_name: str) -> bool:
    try:
        return _importutil.find_spec(import_name) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


def missing_core() -> list[tuple[str, str]]:
    """CORE packages that are not importable in the current interpreter."""
    return [(pip, imp) for pip, imp in CORE_PACKAGES if not _importable(imp)]


def _has_pip() -> bool:
    """Whether this interpreter can run ``python -m pip`` at all.

    uv-managed virtualenvs (including the daemon's own `.venv` built by
    `setup.sh`) ship without a `pip` module by default, so "run pip" is a
    choice to probe, not an assumption.
    """
    try:
        return _importutil.find_spec("pip") is not None
    except Exception:  # noqa: BLE001 - a broken finder reads as "no pip"
        return False


def _pip_install(
    pip_names: list[str], *, upgrade: bool = False, timeout: int = 1800
) -> tuple[bool, str]:
    if not pip_names:
        return True, ""
    if _has_pip():
        cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--break-system-packages",
            "--disable-pip-version-check",
            "--no-input",
        ]
    else:
        # No pip module: fall back to a `uv` binary on PATH, pointed at this
        # same interpreter. Only tool *selection* falls back — a failed install
        # under a present pip is reported as-is rather than retried with uv,
        # which would blur what actually broke.
        uv = shutil.which("uv")
        if uv is None:
            return False, (
                f"{sys.executable} has no pip module and no `uv` binary was "
                "found on PATH. Install pip into this environment "
                "(python -m ensurepip --upgrade), or install uv, then retry."
            )
        cmd = [uv, "pip", "install", "--python", sys.executable]
    if upgrade:
        cmd.append("--upgrade")
    cmd += pip_names
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"package install timed out after {timeout}s"
    except Exception as e:  # noqa: BLE001
        return False, str(e)
    log = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, log[-4000:]


def install(pip_names: list[str], *, upgrade: bool = False) -> dict:
    """On-demand install of one or more packages. Returns a structured result.

    Idempotent for already-present packages when ``upgrade`` is False.
    """
    pip_names = [p.strip() for p in pip_names if p and p.strip()]
    if not pip_names:
        return {"ok": True, "installed": [], "failed": [], "log": "nothing to do"}
    ok, log = _pip_install(pip_names, upgrade=upgrade)
    result = {
        "ok": ok,
        "installed": pip_names if ok else [],
        "failed": ([] if ok else [{"name": ", ".join(pip_names), "error": log[-600:]}]),
        "log": log,
    }
    return result


def core_plan() -> dict:
    """Report what ensure_core WOULD install. Never touches the environment.

    This is the `plan` half of plan/apply, and the only thing daemon startup is
    allowed to call.
    """
    missing = missing_core()
    pip_names = [pip for pip, _imp in missing]
    with _LOCK:
        if not missing:
            STATUS.update(
                phase="ready",
                message="scientific stack ready",
                installing=[],
                missing=[],
                finished_at=time.time(),
            )
        elif STATUS.get("phase") not in ("installing",):
            STATUS.update(
                phase="needs_provision",
                installing=[],
                missing=list(pip_names),
                message=(
                    f"{len(pip_names)} scientific package(s) not installed — "
                    f"run `openai4s setup` or install from Customize → Compute"
                ),
            )
    return {
        "ok": True,
        "missing": pip_names,
        "satisfied": not pip_names,
        "would_install": pip_names,
    }


def ensure_core(background: bool = True) -> dict:
    """Install any missing CORE packages.

    The `apply` half of plan/apply. Callers are explicit user actions only —
    startup calls core_plan() instead, because installing 23 unpinned packages
    into the user's interpreter is not something a daemon should do just
    because it booted.
    """
    missing = missing_core()
    if not missing:
        with _LOCK:
            STATUS.update(
                phase="ready",
                message="scientific stack ready",
                installing=[],
                installed=[],
                finished_at=time.time(),
            )
        return {"ok": True, "installed": [], "skipped": True}

    pip_names = [pip for pip, _imp in missing]

    def _run() -> dict:
        with _LOCK:
            STATUS.update(
                phase="installing",
                started_at=time.time(),
                finished_at=None,
                installing=list(pip_names),
                installed=[],
                failed=[],
                message=f"installing {len(pip_names)} package(s)…",
            )
        ok, log = _pip_install(pip_names)
        with _LOCK:
            if ok:
                STATUS.update(
                    phase="ready",
                    installing=[],
                    installed=list(pip_names),
                    finished_at=time.time(),
                    message="scientific stack ready",
                )
            else:
                # Best-effort: record which ones still fail to import.
                still = [pip for pip, imp in missing if not _importable(imp)]
                STATUS.update(
                    phase="ready" if not still else "error",
                    installing=[],
                    installed=[p for p in pip_names if p not in still],
                    failed=[{"name": p, "error": "install failed"} for p in still],
                    finished_at=time.time(),
                    message=(
                        "scientific stack ready"
                        if not still
                        else f"{len(still)} package(s) unavailable"
                    ),
                )
        return {"ok": ok, "installed": pip_names, "log": log}

    if background:
        threading.Thread(target=_run, name="openai4s-preinstall", daemon=True).start()
        return {"ok": True, "installed": pip_names, "background": True}
    return _run()


def installed_report() -> list[dict]:
    """Version report for the CORE + any importable OPTIONAL packages."""
    out: list[dict] = []
    seen = set()
    for pip, imp in CORE_PACKAGES:
        seen.add(imp)
        out.append(
            {
                "name": pip,
                "import": imp,
                "installed": _importable(imp),
                "version": _version(imp),
                "tier": "core",
            }
        )
    for spec in OPTIONAL_PACKAGES:
        imp = spec["import"]
        if imp in seen:
            continue
        out.append(
            {
                "name": spec["name"],
                "import": imp,
                "installed": _importable(imp),
                "version": _version(imp),
                "note": spec.get("note"),
                "tier": "optional",
            }
        )
    return out


def _version(import_name: str) -> str | None:
    try:
        from importlib.metadata import PackageNotFoundError, version

        # map a couple of import names whose dist name differs
        dist = {
            "sklearn": "scikit-learn",
            "Bio": "biopython",
            "PIL": "pillow",
            "yaml": "pyyaml",
            "bs4": "beautifulsoup4",
            "dateutil": "python-dateutil",
        }.get(import_name, import_name)
        try:
            return version(dist)
        except PackageNotFoundError:
            return version(import_name)
    except Exception:  # noqa: BLE001
        return None


def full_freeze() -> list[dict]:
    """Complete freeze of **this process**: every installed distribution as
    ``{"name", "version"}``, de-duplicated and sorted case-insensitively.

    Read the name literally. This describes the interpreter it runs in, which
    is the daemon's. It used to be documented as describing "the interpreter
    the session kernel runs in", on the reasoning that a worker is spawned with
    ``sys.executable`` and shares this process's site-packages — true when the
    daemon interpreter was the only kernel there was, and no longer true now
    that a cell may run in a selected conda environment or in R at all.
    Attributing this list to such a kernel is how an R artifact came to carry a
    Python package list.

    Use :func:`freeze_for` when the interpreter is not this one.
    """
    try:
        from importlib.metadata import distributions
    except Exception:  # noqa: BLE001
        return []
    seen: dict[str, dict] = {}
    for dist in distributions():
        try:
            meta = dist.metadata
            name = (meta["Name"] if meta else None) or None
        except Exception:  # noqa: BLE001
            name = None
        if not name:
            continue
        name = name.strip()
        key = name.lower()
        if key in seen:
            continue
        try:
            ver = dist.version
        except Exception:  # noqa: BLE001
            ver = None
        seen[key] = {"name": name, "version": ver}
    return sorted(seen.values(), key=lambda d: d["name"].lower())


def _interpreter_prefix(interpreter: str) -> str:
    """`<prefix>` for an interpreter at `<prefix>/bin/python`, resolved.

    A virtualenv's ``bin/python`` is a symlink to the base python, so comparing
    the *resolved executable* treats two different environments as one. The
    prefix — the directory holding ``bin/`` (and ``pyvenv.cfg``) — is what
    actually selects site-packages, and it is not resolved through the symlink.
    """
    path = Path(interpreter)
    parent = path.parent
    if parent.name in ("bin", "Scripts"):
        return str(parent.parent)
    return str(parent)


def _is_this_interpreter(interpreter: str) -> bool:
    """Whether `interpreter` is this process's *environment*, not just its
    resolved base executable. A venv shares the base python but not the prefix.
    """
    try:
        same_exe = os.path.realpath(str(interpreter)) == os.path.realpath(
            sys.executable
        )
    except OSError:
        return False
    if not same_exe:
        return False
    return os.path.realpath(_interpreter_prefix(str(interpreter))) == os.path.realpath(
        sys.prefix
    )


def _confined_probe(
    base_argv: list[str], workspace: str
) -> tuple[list[str], dict[str, str], Any]:
    """Wrap the freeze probe in the kernel's child env and OS boundary.

    Returns ``(argv, env, sandbox)``. The environment is always the scrubbed
    kernel child env — that alone removes the credential vector. The OS sandbox
    is applied when one can be built; when it cannot (``auto`` on a host with no
    backend) the scrubbed env still stands, which is a strict improvement over
    the daemon's full context. ``sandbox`` is returned so the caller can close
    it; it may be ``None``. ``workspace`` is the caller-owned temp cwd.
    """
    interpreter = base_argv[0]
    # `<prefix>/bin/python` → `<prefix>`. The freeze does not need the env's
    # tools on PATH, but binding it keeps the child consistent with a kernel.
    env_root: str | None = None
    try:
        parent = Path(interpreter).resolve().parent
        if parent.name in ("bin", "Scripts"):
            env_root = str(parent.parent)
    except OSError:
        env_root = None

    try:
        from openai4s.kernel.environment import build_kernel_environment

        env = build_kernel_environment(mode="probe", cwd=workspace, env_root=env_root)
    except Exception:  # noqa: BLE001 - a scrub failure must not run unconfined
        env = {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": workspace,
            "TMPDIR": workspace,
        }

    sandbox: Any = None
    mode = (os.environ.get("OPENAI4S_KERNEL_SANDBOX") or "auto").strip().lower()
    try:
        from openai4s.security.sandbox import (
            SandboxConfigurationError,
            create_kernel_sandbox,
        )

        sandbox = create_kernel_sandbox(workspace)
        argv = list(sandbox.wrap_command(base_argv))
        env = sandbox.apply_environment(env)
    except SandboxConfigurationError:
        # A *misconfiguration* (e.g. OPENAI4S_KERNEL_ALLOW_RAW_NETWORK=treu) is
        # never a reason to degrade to unconfined: the operator asked for a
        # boundary and typed it wrong. Propagate in every mode, including `auto`,
        # so the foreign interpreter does not run with host reach behind a typo.
        raise
    except Exception:  # noqa: BLE001
        # Fail closed under enforce. Silently launching the foreign interpreter
        # unconfined when the boundary could not be *built* (no backend on this
        # host) violates the mode's contract and lets it reach daemon-readable
        # files and the network despite the scrubbed env. `auto` still degrades
        # to the scrubbed env alone, a strict improvement over the daemon's full
        # context — but only for availability failures, not configuration ones.
        if mode == "enforce":
            raise
        # The launch that follows is deliberately unconfined, so it must not
        # inherit this sandbox's descriptors: `popen_pass_fds()` would hand a
        # foreign interpreter bubblewrap's info-pipe write end and the seccomp
        # program, which its own docstring says are inherited exactly once by
        # the wrapped command.
        if sandbox is not None:
            try:
                sandbox.close()
            except Exception:  # noqa: BLE001
                pass
            sandbox = None
        argv = list(base_argv)
    return argv, env, sandbox


class ProbeStopped(RuntimeError):
    """``run_confined_probe`` was asked to stop (its ``stop`` event was set)."""


#: How often a stoppable probe's wait checks its ``stop`` event.
_PROBE_STOP_POLL_S = 0.05
#: How long a stopped probe gets after SIGTERM to remove what it made.
_PROBE_TERM_GRACE_S = 2.0
#: Where Linux lists processes; elsewhere (macOS) `ps` is asked instead.
_PROC_ROOT = "/proc"


def _descendant_pids(root: int) -> list[int]:
    """Every process below ``root`` by parent link, best effort; [] if unknown.

    A probe's process group is not all of it: macOS ``system_profiler``, which
    matplotlib's font search runs, re-spawns itself as a ``-nospawn`` helper in
    a process group of its own, and that helper does the scan while holding
    the probe's stderr open. Read while the tree is still intact -- once a
    parent dies, its children belong to PID 1.
    """
    parents: dict[int, int] = {}
    try:
        if os.path.isfile(os.path.join(_PROC_ROOT, "self", "stat")):
            with os.scandir(_PROC_ROOT) as entries:
                for entry in entries:
                    if not entry.name.isdigit():
                        continue
                    try:
                        with open(os.path.join(entry.path, "stat"), "rb") as handle:
                            # `pid (comm) state ppid ...`; comm may hold spaces.
                            fields = handle.read().rsplit(b")", 1)[-1].split()
                        parents[int(entry.name)] = int(fields[1])
                    except (OSError, IndexError, ValueError):
                        continue
        else:
            listing = subprocess.run(
                [
                    "/bin/ps" if os.path.exists("/bin/ps") else "ps",
                    "-axo",
                    "pid=,ppid=",
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=2,
                check=False,
            )
            for line in listing.stdout.decode("ascii", "replace").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    parents[int(parts[0])] = int(parts[1])
    except (OSError, subprocess.SubprocessError):
        return []
    children: dict[int, list[int]] = {}
    for pid, ppid in parents.items():
        children.setdefault(ppid, []).append(pid)
    found: list[int] = []
    pending = [root]
    while pending:
        for child in children.get(pending.pop(), ()):
            if child not in found and child != root:
                found.append(child)
                pending.append(child)
    return found


def _signal_probe(proc: subprocess.Popen, signum: int) -> None:
    # Skipped once the leader has been reaped: its pid may name another process.
    if proc.returncode is not None:
        return
    try:
        if hasattr(os, "killpg"):
            # The group it leads (`start_new_session`).
            os.killpg(proc.pid, signum)
        else:  # pragma: no cover - no process groups on this platform
            proc.send_signal(signum)
    except OSError:
        pass


def _kill_pids(pids: list[int]) -> None:
    for pid in pids:
        try:
            os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        except OSError:
            pass


def _stop_probe_tree(proc: subprocess.Popen, *, graceful: bool) -> None:
    """End a probe and everything it started, never waiting on its pipes.

    ``subprocess.run`` killed only the leader on a timeout, leaving the work it
    had started running. The descendants are read first and killed outright
    (nothing of theirs is worth saving); a ``graceful`` stop gives the leader's
    group SIGTERM and a short grace to remove its own scratch files first.
    """
    below = _descendant_pids(proc.pid)
    if graceful:
        _signal_probe(proc, signal.SIGTERM)
        _kill_pids(below)
        try:
            proc.wait(timeout=_PROBE_TERM_GRACE_S)
            return
        except subprocess.TimeoutExpired:
            pass
    _signal_probe(proc, getattr(signal, "SIGKILL", signal.SIGTERM))
    _kill_pids(below)


def _communicate(
    proc: subprocess.Popen, timeout: float, stop: threading.Event | None
) -> tuple[bytes, bytes]:
    if stop is None:
        return proc.communicate(timeout=timeout)
    deadline = time.monotonic() + timeout
    while not stop.is_set():
        remaining = deadline - time.monotonic()
        try:
            return proc.communicate(
                timeout=max(0.0, min(remaining, _PROBE_STOP_POLL_S))
            )
        except subprocess.TimeoutExpired as expired:
            if time.monotonic() >= deadline:
                expired.timeout = timeout
                raise
    raise ProbeStopped("the probe was stopped")


def run_confined_probe(
    base_argv: list[str],
    *,
    timeout: float,
    stop: threading.Event | None = None,
) -> "subprocess.CompletedProcess":
    """Run a short probe against a foreign interpreter, confined.

    Shared by the freeze probe and the environment-verification probes: both
    execute a *foreign* interpreter (its executable, `.pth` files and
    sitecustomize run before the supplied code), so both need the scrubbed
    child environment and the OS boundary a kernel cell gets. Raises under
    ``OPENAI4S_KERNEL_SANDBOX=enforce`` when the boundary cannot be built,
    rather than degrading to an unconfined launch.

    The child runs in its own empty temp workspace, not the caller's working
    directory, which ``python -c`` would otherwise put first on ``sys.path``.

    A timeout, or any other way out of the wait, kills the probe and what it
    started, and the workspace is removed on every path. ``stop`` lets another
    thread end a probe early (the daemon's background font-list build, at
    shutdown): once it is set the probe is not started, or is sent SIGTERM and
    then killed with its descendants, and ``ProbeStopped`` is raised -- without
    waiting for a pipe some escaped descendant still holds open.
    """
    if stop is not None and stop.is_set():
        raise ProbeStopped("the probe was stopped before it started")
    workspace = tempfile.mkdtemp(prefix="openai4s-probe-")
    sandbox = None
    # `_confined_probe` is inside the try: under `enforce` with no working
    # sandbox backend it raises (correct fail-closed), and building it before
    # the try leaked the just-created workspace on every probe on such a host.
    try:
        argv, env, sandbox = _confined_probe(base_argv, workspace)
        # `start_new_session` for the same reason `PipeTransport` sets it:
        # `KernelSandbox.wrap_command` no longer emits bubblewrap's
        # `--new-session`, so the session boundary has to come from the spawn.
        # A foreign interpreter runs its own `.pth` files and `sitecustomize`
        # before the probe code, and `stdin` here is the daemon's, so without
        # this it would hold the operator's controlling terminal.
        #
        # `cwd` is the probe's own empty workspace, never the daemon's launch
        # directory. `python -c` without `-I` puts the working directory first
        # on `sys.path`, and the launch directory can be a CLI kernel's
        # writable workspace: a `json.py` or `platform.py` there answered for
        # the stdlib inside the probe. Bubblewrap already `--chdir`s into the
        # workspace; Seatbelt and the degraded `auto` launch did not.
        with subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=workspace,
            start_new_session=True,
            pass_fds=getattr(sandbox, "popen_pass_fds", lambda: ())(),
        ) as proc:
            try:
                stdout, stderr = _communicate(proc, timeout, stop)
            except ProbeStopped:
                _stop_probe_tree(proc, graceful=True)
                raise
            except BaseException:
                _stop_probe_tree(proc, graceful=False)
                raise
        return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
    finally:
        if sandbox is not None:
            try:
                sandbox.close()
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(workspace, ignore_errors=True)


def freeze_for(interpreter: str | None, *, timeout: float = 20.0) -> list[dict] | None:
    """Freeze an arbitrary interpreter, or None when it cannot be asked.

    Returns None rather than falling back to this process's own packages: a
    freeze attributed to the wrong interpreter is worse than an absent one,
    because it is believed. The caller records the absence and why.

    Runs the target interpreter once. Callers cache per kernel generation --
    an environment does not change within one -- so this is not on the
    per-artifact path.
    """
    if not interpreter:
        return None
    if _is_this_interpreter(interpreter):
        # Same interpreter *and* same environment: the in-process read is exact
        # and free. A realpath match alone is not enough — a virtualenv is a
        # symlink to the same base python but selects a different prefix and
        # site-packages, so freezing this process would record the wrong set.
        return full_freeze()
    probe = (
        "import json,sys\n"
        "try:\n"
        "    from importlib.metadata import distributions\n"
        "except Exception:\n"
        "    print('[]'); sys.exit(0)\n"
        "seen={}\n"
        "for d in distributions():\n"
        "    try:\n"
        "        m=d.metadata; n=(m['Name'] if m else None) or None\n"
        "    except Exception:\n"
        "        n=None\n"
        "    if not n: continue\n"
        "    n=n.strip(); k=n.lower()\n"
        "    if k in seen: continue\n"
        "    try: v=d.version\n"
        "    except Exception: v=None\n"
        "    seen[k]={'name':n,'version':v}\n"
        "print(json.dumps(sorted(seen.values(), key=lambda x: x['name'].lower())))\n"
    )
    # Confine the probe. This launches a *foreign* interpreter — for an
    # artifact from a selected environment, one a sandboxed kernel produced —
    # and `-I` only isolates Python's own path/env handling. It does nothing to
    # stop the executable, native startup, or a `.pth`/sitecustomize hook from
    # reading the daemon's credentials, touching daemon files, or reaching the
    # network. It runs through the shared confined path, which fails closed
    # under enforce.
    try:
        proc = run_confined_probe(
            [str(interpreter), "-I", "-c", probe], timeout=timeout
        )
    except (OSError, subprocess.SubprocessError):
        return None
    except Exception:  # noqa: BLE001 - enforce with no boundary → absent, not wrong
        return None
    if proc.returncode != 0:
        return None
    try:
        parsed = json.loads(proc.stdout.decode("utf-8", "replace") or "null")
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, list) else None


def status() -> dict:
    with _LOCK:
        return dict(STATUS)
