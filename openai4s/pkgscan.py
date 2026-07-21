"""Multi-environment package scanning.

A batched package-scan facility (dispatched Python scan scripts executed in a
one-shot VM). This is the foundation that lets Code-as-Action operate real
scientific environments: before the agent writes `import torch`, the system can
answer "which of these envs HAS torch, and which are MISSING it" in one batched
pass.

Key points:
  - env metadata file `.openai4s_metadata.json`, fields
    {kind, language, venv_path, source_path, op_log};
  - language probe: look for `bin/python` and `bin/Rscript`,
    `lang = "r" if has_r and not has_py else "python"`;
  - package-name normalization `re.sub(r"[-_.]+", "-", n).lower()` — THE key
    dependency-matching rule (so `Foo_Bar.baz` == `foo-bar-baz`);
  - three-source unified collection:
      conda: `conda-meta/*.json` -> "name"
      pip:   `*.dist-info/METADATA` -> "Name:"
      R:     `lib/R/library/*/DESCRIPTION` -> "Package:"
    the requested-only variant scans ONLY top-level deps carrying a `REQUESTED` marker;
  - batching: computing has/missing for many envs in ONE pass (spawning a
    one-shot VM per env across many envs would cost "minutes").

Pure stdlib, so it runs unchanged inside the `python -I -S` control kernel.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_META_FILE = ".openai4s_metadata.json"
_NORM_RE = re.compile(r"[-_.]+")


def normalize_pkg(name: str) -> str:
    """Canonical package key: collapse runs of -/_/. to a single '-', lower.

    Mirrors openai4s exactly: `re.sub(r"[-_.]+", "-", n).lower()`. This is the
    rule every source is funneled through so conda/pip/R names compare equal.
    """
    return _NORM_RE.sub("-", name.strip()).lower()


@dataclass
class EnvMetadata:
    """Parsed `.openai4s_metadata.json` for one environment."""

    kind: str | None = None
    language: str | None = None
    venv_path: str | None = None
    source_path: str | None = None
    op_log: str | None = None
    root: Path | None = None

    @classmethod
    def load(cls, env_dir: Path) -> "EnvMetadata":
        meta_path = env_dir / _META_FILE
        data: dict = {}
        if meta_path.exists():
            try:
                data = json.loads(meta_path.read_text("utf-8")) or {}
            except (json.JSONDecodeError, OSError):
                data = {}
        return cls(
            kind=data.get("kind"),
            language=data.get("language"),
            venv_path=data.get("venv_path"),
            source_path=data.get("source_path"),
            op_log=data.get("op_log"),
            root=env_dir,
        )


def detect_language(env_dir: Path) -> str:
    """`lang = "r" if has_r and not has_py else "python"`.

    Probes for interpreter binaries under bin/. R only wins when Rscript is
    present AND python is not — python is the default otherwise.
    """
    bindir = env_dir / "bin"
    has_py = (bindir / "python").exists() or (bindir / "python3").exists()
    has_r = (bindir / "Rscript").exists()
    return "r" if has_r and not has_py else "python"


# --- three-source package collection -------------------------------------


def _collect_conda(root: Path) -> set[str]:
    """conda-meta/*.json -> normalized "name" values."""
    out: set[str] = set()
    meta = root / "conda-meta"
    if not meta.is_dir():
        return out
    for j in meta.glob("*.json"):
        try:
            name = json.loads(j.read_text("utf-8")).get("name")
        except (json.JSONDecodeError, OSError):
            continue
        if name:
            out.add(normalize_pkg(str(name)))
    return out


def _read_field(path: Path, key: str) -> str | None:
    """Return the value of a `Key: value` line from an RFC822-ish metadata file."""
    prefix = key + ":"
    try:
        for line in path.read_text("utf-8", errors="replace").splitlines():
            if line.startswith(prefix):
                return line[len(prefix) :].strip()
    except OSError:
        return None
    return None


def _collect_pip(root: Path, requested_only: bool = False) -> set[str]:
    """*.dist-info/METADATA -> normalized "Name:".

    When `requested_only`, keep only distributions that
    carry the `REQUESTED` marker file — i.e. top-level deps the user asked for,
    not the full transitive closure.
    """
    out: set[str] = set()
    # Search only actual package directories. Recursing from ``lib/`` walks
    # large native toolchains, model caches and R libraries in scientific Conda
    # envs, turning a simple environment listing into a minutes-long scan.
    # The interpreter directory is matched with ``*`` rather than ``python*``
    # so PyPy (``lib/pypy3.10/site-packages``) resolves too, and both
    # ``site-packages`` and Debian/RHEL ``dist-packages`` are covered.
    candidates: list[Path] = []
    for lib in (root / "lib", root / "lib64"):
        for leaf in ("*/site-packages", "*/dist-packages"):
            candidates.extend(sorted(lib.glob(leaf)))
    candidates.append(root / "Lib" / "site-packages")  # Windows, harmless if absent
    seen_infos: set[Path] = set()
    for base in candidates:
        if not base.is_dir():
            continue
        for info in base.glob("*.dist-info"):
            if info in seen_infos:
                continue
            seen_infos.add(info)
            if requested_only and not (info / "REQUESTED").exists():
                continue
            name = _read_field(info / "METADATA", "Name")
            if name:
                out.add(normalize_pkg(name))
    return out


def _collect_r(root: Path) -> set[str]:
    """lib/R/library/*/DESCRIPTION -> normalized "Package:"."""
    out: set[str] = set()
    libdir = root / "lib" / "R" / "library"
    if not libdir.is_dir():
        return out
    for desc in libdir.glob("*/DESCRIPTION"):
        name = _read_field(desc, "Package")
        if name:
            out.add(normalize_pkg(name))
    return out


def collect_packages(
    env_dir: Path, *, language: str | None = None, requested_only: bool = False
) -> set[str]:
    """Unified normalized package set for one env (conda ∪ pip ∪ R)."""
    root = env_dir
    lang = language or detect_language(env_dir)
    pkgs: set[str] = set()
    pkgs |= _collect_conda(root)
    if lang == "r":
        pkgs |= _collect_r(root)
    else:
        pkgs |= _collect_pip(root, requested_only=requested_only)
    return pkgs


@dataclass
class EnvScan:
    """Result of scanning one environment."""

    root: str
    language: str
    metadata: EnvMetadata
    packages: set[str] = field(default_factory=set)
    has: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "language": self.language,
            "kind": self.metadata.kind,
            "package_count": len(self.packages),
            "has": sorted(self.has),
            "missing": sorted(self.missing),
        }


def scan_envs(
    env_dirs: list[str | Path],
    *,
    require: list[str] | None = None,
    requested_only: bool = False,
) -> list[dict]:
    """Batch-scan many environments in ONE pass (the perf point of).

    For each env: load `.openai4s_metadata.json`, detect language, collect the
    normalized package set from all three sources, then — if `require` is given
    — split the requested names into `has` / `missing` (both normalized before
    comparison, so `scikit_learn` matches `scikit-learn`).
    """
    want = [normalize_pkg(p) for p in (require or [])]
    want_display = {normalize_pkg(p): p for p in (require or [])}
    results: list[dict] = []
    for d in env_dirs:
        env_dir = Path(d)
        meta = EnvMetadata.load(env_dir)
        lang = meta.language or detect_language(env_dir)
        pkgs = collect_packages(env_dir, language=lang, requested_only=requested_only)
        scan = EnvScan(root=str(env_dir), language=lang, metadata=meta, packages=pkgs)
        if want:
            scan.has = [want_display[w] for w in want if w in pkgs]
            scan.missing = [want_display[w] for w in want if w not in pkgs]
        results.append(scan.to_dict())
    return results
