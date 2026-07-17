"""Built-in runnable environments — the prebuilt (conda) envs the host ships with.

Instead of pip-installing a scientific stack into ONE kernel on every task, the
session kernel can run in any of several **prebuilt environments**, each already
stocked for a domain (general data-science, structural biology, phylogenetics,
R). The agent (or the user, from the Notebook env selector) simply *picks* the
environment that already has what the task needs — `host.env.use("struct")` —
instead of installing packages every time.

Discovery is cheap-ish and cached module-wide:
  - `bin/python` / `bin/Rscript` probe → language + interpreter path;
  - `pkgscan.collect_packages` → the env's installed package set (lazy, cached
    per Environment);
  - a curated description derived from a handful of notable packages.

Discovery roots, in priority order:
  1. ``OPENAI4S_ENV_ROOTS`` — ``:``-separated *envs* directories (override);
  2. Conda/Mamba's own environment-root variables and active prefix;
  3. the base interpreter behind the daemon's venv;
  4. the usual conda/mamba install locations under ``$HOME``.

A synthetic ``base`` environment (the daemon's own interpreter, ``sys.executable``,
carrying the preinstalled stack from :mod:`openai4s.kernel.preinstall`) is
always present so env selection never leaves the user without a Python kernel.

Pure stdlib (+ :mod:`openai4s.pkgscan`), so it imports under any of the
prebuilt interpreters as well as the control kernel.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

from openai4s import pkgscan

# Envs that exist on disk but must never be offered as a user runtime.
# Populate via OPENAI4S_ENV_HIDE (comma-separated names).
_ALWAYS_HIDE: set[str] = set()

# Notable packages, in the order we surface them, used to auto-describe an env.
_HIGHLIGHT = [
    "numpy",
    "pandas",
    "scipy",
    "matplotlib",
    "seaborn",
    "scikit-learn",
    "statsmodels",
    "sympy",
    "networkx",
    "biopython",
    "biotite",
    "scanpy",
    "anndata",
    "rdkit",
    "torch",
    "tensorflow",
    "requests",
    "mafft",
    "iqtree",
    "trimal",
    "fasttree",
    "raxml",
    "ete3",
    "dendropy",
]

# Curated one-liners for the well-known reference envs (fall back to a derived
# description for anything else).
_KNOWN_DESC = {
    "python": "通用数据科学：numpy / pandas / scipy / matplotlib / biopython 等",
    "struct": "结构生物学：biotite / biotraj，mmCIF/PDB 解析、坐标与接触分析",
    "phylo": "系统发育：真实的 MAFFT / IQ-TREE / trimAl / FastTree + biopython",
    "r": "R 统计与绘图：tidyverse / ggplot2（用 ```r 单元格在持久 R 内核中运行）",
    "base": "内置默认内核：启动即预装 numpy/pandas/scipy/matplotlib/联网栈",
}


@dataclass
class Environment:
    """One runnable environment (a conda env or the synthetic ``base``)."""

    name: str
    language: str  # "python" | "r"
    root: Path  # env prefix
    python: str | None = None  # interpreter that runs worker.py (None ⇒ R-only)
    rscript: str | None = None
    is_conda: bool = True  # base is synthetic; conda envs prepend bin to PATH
    builtin: bool = True
    _packages: set[str] | None = field(default=None, repr=False, compare=False)
    _pyversion: str | None = field(default=None, repr=False, compare=False)

    # -- interpreter / activation -----------------------------------------
    @property
    def interpreter(self) -> str | None:
        """Path to the Python that should run the notebook kernel, or None when
        the env has no Python (R-only) and so cannot host a Python kernel."""
        return self.python

    @property
    def bin_dir(self) -> str | None:
        """`<root>/bin` for conda envs (prepended to PATH so the env's CLI tools —
        mafft, iqtree, Rscript — resolve); None for the synthetic base env."""
        if not self.is_conda:
            return None
        b = self.root / "bin"
        return str(b) if b.is_dir() else None

    # -- packages ----------------------------------------------------------
    def package_set(self) -> set[str]:
        """Normalized installed-package set (cached). Scanned via pkgscan
        (conda-meta ∪ dist-info ∪ R DESCRIPTION)."""
        if self._packages is None:
            try:
                self._packages = pkgscan.collect_packages(
                    self.root, language=self.language
                )
            except Exception:  # noqa: BLE001 — a broken env must not crash discovery
                self._packages = set()
        return self._packages

    def has_package(self, name: str) -> bool:
        return pkgscan.normalize_pkg(name) in self.package_set()

    def notable(self, limit: int = 12) -> list[str]:
        pkgs = self.package_set()
        return [h for h in _HIGHLIGHT if pkgscan.normalize_pkg(h) in pkgs][:limit]

    def description(self) -> str:
        if self.name in _KNOWN_DESC:
            return _KNOWN_DESC[self.name]
        note = self.notable(6)
        if note:
            return f"{self.language} 环境：{', '.join(note)}"
        return f"{self.language} 环境（{len(self.package_set())} 个包）"

    def python_version(self) -> str | None:
        """Interpreter version string, probed once and cached (empty for R-only)."""
        if self.python is None:
            return None
        if self._pyversion is None:
            try:
                out = subprocess.run(
                    [
                        self.python,
                        "-c",
                        "import platform;print(platform.python_version())",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
                self._pyversion = (out.stdout or "").strip() or "?"
            except Exception:  # noqa: BLE001
                self._pyversion = "?"
        return self._pyversion

    def to_dict(self, with_packages: bool = False) -> dict:
        d = {
            "name": self.name,
            "language": self.language,
            "root": str(self.root),
            "runnable": self.python is not None,  # can host the notebook kernel
            "is_conda": self.is_conda,
            "builtin": self.builtin,
            "description": self.description(),
            "notable": self.notable(),
            "python_version": self.python_version(),
        }
        if with_packages:
            d["package_count"] = len(self.package_set())
        return d


# --------------------------------------------------------------------------- #
#  Discovery (cached)
# --------------------------------------------------------------------------- #
_LOCK = threading.Lock()
_CACHE: list[Environment] | None = None


def _hidden_names() -> set[str]:
    extra = os.environ.get("OPENAI4S_ENV_HIDE", "")
    return _ALWAYS_HIDE | {n.strip() for n in extra.split(",") if n.strip()}


def _envs_root_from_prefix(prefix: str | os.PathLike[str]) -> Path:
    """Return the standard sibling-environment directory for a Conda prefix.

    Conda exposes either its base prefix (``<base>``) or an activated named
    environment (normally ``<base>/envs/<name>``).  The former stores named
    environments under ``<base>/envs``; the latter already has that directory
    as its parent.  Treating ``<base>.parent`` as an env root scans unrelated
    directories and incorrectly offers the base installation itself as a
    named environment.
    """

    path = Path(prefix).expanduser()
    if path.parent.name == "envs":
        return path.parent
    return path / "envs"


def _env_roots() -> list[Path]:
    """Candidate *envs* directories to scan, de-duplicated, in priority order."""
    roots: list[Path] = []
    seen: set[Path] = set()

    def add(p: Path) -> None:
        try:
            p = p.expanduser()
        except Exception:  # noqa: BLE001
            return
        if p in seen:
            return
        seen.add(p)
        roots.append(p)

    override = os.environ.get("OPENAI4S_ENV_ROOTS", "")
    for chunk in override.split(os.pathsep):
        if chunk.strip():
            add(Path(chunk.strip()))

    # Respect Conda's configured environment directories before inferred
    # locations.  CONDA_ENVS_PATH may contain multiple roots.
    configured = os.environ.get("CONDA_ENVS_PATH", "")
    for chunk in configured.split(os.pathsep):
        if chunk.strip():
            add(Path(chunk.strip()))

    mamba_root = os.environ.get("MAMBA_ROOT_PREFIX", "").strip()
    if mamba_root:
        add(Path(mamba_root) / "envs")

    prefix = os.environ.get("CONDA_PREFIX", "").strip()
    if prefix:
        add(_envs_root_from_prefix(prefix))

    # ``start.sh`` executes the project venv directly.  Even when the caller
    # did not activate Conda (and CONDA_PREFIX is therefore absent), a venv
    # created from a Conda Python retains that installation as sys.base_prefix.
    base_prefix = str(getattr(sys, "base_prefix", "") or "").strip()
    if base_prefix:
        add(_envs_root_from_prefix(base_prefix))

    home = Path.home()
    for base in ("miniconda3", "miniforge3", "anaconda3", "mambaforge", "micromamba"):
        add(home / base / "envs")
    return roots


def _detect_env(env_dir: Path) -> Environment | None:
    """Build an Environment for one env directory, or None if it has no usable
    interpreter."""
    bindir = env_dir / "bin"
    py = bindir / "python"
    if not py.exists():
        py = bindir / "python3"
    rscript = bindir / "Rscript"
    has_py = py.exists()
    has_r = rscript.exists()
    if not has_py and not has_r:
        return None
    language = "r" if (has_r and not has_py) else "python"
    return Environment(
        name=env_dir.name,
        language=language,
        root=env_dir,
        python=str(py) if has_py else None,
        rscript=str(rscript) if has_r else None,
        is_conda=True,
        builtin=True,
    )


def _base_environment() -> Environment:
    """The daemon's own interpreter as a first-class env (the preinstalled
    stack lives here). Never prepends anything to PATH."""
    return Environment(
        name="base",
        language="python",
        root=Path(sys.prefix),
        python=sys.executable,
        rscript=None,
        is_conda=False,
        builtin=True,
    )


def discover_environments(force: bool = False) -> list[Environment]:
    """All offerable environments (cached). ``base`` first, then the discovered
    conda envs sorted by name. Set ``force`` to rescan the disk."""
    global _CACHE
    with _LOCK:
        if _CACHE is not None and not force:
            return _CACHE
        hidden = _hidden_names()
        found: dict[str, Environment] = {"base": _base_environment()}
        for root in _env_roots():
            if not root.is_dir():
                continue
            for child in sorted(root.iterdir()):
                if not child.is_dir():
                    continue
                if child.name in hidden or child.name.startswith("."):
                    continue
                if child.name in found:  # first root wins on name collision
                    continue
                env = _detect_env(child)
                if env is not None:
                    found[env.name] = env
        base = found.pop("base")
        ordered = [base] + [found[k] for k in sorted(found)]
        _CACHE = ordered
        return _CACHE


def get_environment(name: str | None) -> Environment | None:
    if not name:
        return None
    for env in discover_environments():
        if env.name == name:
            return env
    return None


def default_env_name() -> str:
    """Which env a brand-new session's kernel runs in.

    ``OPENAI4S_DEFAULT_ENV`` wins; otherwise prefer the general-purpose
    ``python`` conda env (stocked so common tasks need no install), falling back
    to ``base`` when no conda envs were discovered."""
    override = os.environ.get("OPENAI4S_DEFAULT_ENV", "").strip()
    envs = discover_environments()
    names = {e.name for e in envs}
    if override and override in names:
        return override
    if "python" in names:
        return "python"
    return "base"


def list_environments(with_packages: bool = True) -> list[dict]:
    return [e.to_dict(with_packages=with_packages) for e in discover_environments()]
