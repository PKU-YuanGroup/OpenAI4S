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

import subprocess
import sys
import threading
import time
from importlib import util as _importutil

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
    """Complete environment freeze: every installed distribution as
    ``{"name", "version"}``, de-duplicated and sorted case-insensitively.

    This is the ``pip freeze`` / ``conda list`` equivalent of the interpreter the
    session kernel runs in — the worker is spawned with ``sys.executable`` and
    shares this process's site-packages (kernel/manager.py), so a daemon-side
    freeze reflects exactly what agent cells (and therefore a figure's code) could
    import. It backs the artifact-provenance "Environment" view, so a figure
    records the full package set that produced it — not just the curated
    ``installed_report()`` subset.
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


def status() -> dict:
    with _LOCK:
        return dict(STATUS)
