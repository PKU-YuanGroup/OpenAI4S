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


def _pip_install(
    pip_names: list[str], *, upgrade: bool = False, timeout: int = 1800
) -> tuple[bool, str]:
    if not pip_names:
        return True, ""
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--break-system-packages",
        "--disable-pip-version-check",
        "--no-input",
    ]
    if upgrade:
        cmd.append("--upgrade")
    cmd += pip_names
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"pip install timed out after {timeout}s"
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
        from openai4s.security.sandbox import create_kernel_sandbox

        sandbox = create_kernel_sandbox(workspace)
        argv = list(sandbox.wrap_command(base_argv))
        env = sandbox.apply_environment(env)
    except Exception:  # noqa: BLE001
        # Fail closed under enforce. Silently launching the foreign interpreter
        # unconfined when the boundary could not be built violates the mode's
        # contract and lets it reach daemon-readable files and the network
        # despite the scrubbed env. `auto` still degrades to the scrubbed env
        # alone, which is a strict improvement over the daemon's full context.
        if mode == "enforce":
            raise
        argv = list(base_argv)
    return argv, env, sandbox


def run_confined_probe(
    base_argv: list[str], *, timeout: float
) -> "subprocess.CompletedProcess":
    """Run a short probe against a foreign interpreter, confined.

    Shared by the freeze probe and the environment-verification probes: both
    execute a *foreign* interpreter (its executable, `.pth` files and
    sitecustomize run before the supplied code), so both need the scrubbed
    child environment and the OS boundary a kernel cell gets. Raises under
    ``OPENAI4S_KERNEL_SANDBOX=enforce`` when the boundary cannot be built,
    rather than degrading to an unconfined launch.
    """
    workspace = tempfile.mkdtemp(prefix="openai4s-probe-")
    argv, env, sandbox = _confined_probe(base_argv, workspace)
    try:
        return subprocess.run(argv, capture_output=True, timeout=timeout, env=env)
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
