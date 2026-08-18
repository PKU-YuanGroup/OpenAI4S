#!/usr/bin/env python3
"""What every shipped desktop bundle must contain, in one place.

Three packages now ship the same payload — the macOS ``.app``/``.dmg``, the
Linux ``.tar.gz``, and the Windows ``.zip`` that carries the Linux bundle as its
WSL2 payload — and each one has a verifier that must refuse the same failures:
a science stack that silently did not install, a source tree missing the Web UI
or the R worker, bytecode the app will rewrite in place, a developer's ``.env``
swept into the image.

Written once here rather than copied into each verifier, for the same reason
the two sandbox smokes share one implementation
(``harness/smoke/sandbox_boundary.py``): two copies drift until one platform
quietly stops checking what the other still does, and the platform that stopped
is the one that ships the broken image.

Stdlib only, and importable from a plain ``sys.path`` insert of ``scripts/`` —
these run against an unpacked release artifact, where the project is not
installed.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_HERE = Path(__file__).resolve().parent

#: The pip/import manifest both builders install from and every verifier checks.
MANIFEST = _HERE / "bundled_packages.txt"

#: Resources whose absence only shows up at runtime — a missing app.js is a
#: blank browser tab, a missing r_worker.R is a dead R channel — long after
#: release.
REQUIRED_SOURCES = (
    "openai4s/__init__.py",
    "openai4s/cli/main.py",
    "openai4s/kernel/worker.py",
    "openai4s/kernel/r_worker.R",
    "openai4s/compute/templates/run.sh.tmpl",
    "openai4s/compute/templates/wrapper.sh.tmpl",
    "openai4s/server/webui/index.html",
    "openai4s/server/webui/app.js",
    "openai4s/server/webui/style.css",
    "openai4s/server/webui/vendor/3Dmol-min.js",
    "openai4s_compute_provider/__init__.py",
    "openai4s_worker_runtime/__init__.py",
    "envs/python.yml",
    "envs/r.yml",
)

#: Only ever checked against the top of our own source tree: `tests/` and
#: `.git/` are perfectly legitimate *inside* third-party site-packages.
FORBIDDEN_SOURCES = (".git", ".venv", ".build", "tests", ".claude")

ENV_TEMPLATES = frozenset({".env.example", ".env.sample", ".env.template"})

MIN_SKILLS = 20

#: Below this, the bundle was not precompiled — see :func:`check_bytecode`.
MIN_PYC = 500

#: A scan that silently enumerated nothing reports the same "clean" as one that
#: actually looked, so the sample size is part of the assertion.
MIN_SCANNED_FILES = 200

_CREDENTIAL_ASSIGNMENT = re.compile(r"(?i)(api[_-]?key|secret|token)\s*=\s*[\"']?\S")


class BundleCheckError(RuntimeError):
    """A shipped bundle does not satisfy the contract."""


# --------------------------------------------------------------------------
# the pre-baked science stack
# --------------------------------------------------------------------------


def manifest_packages() -> list[tuple[str, str]]:
    """``(pip-name, import-name)`` for every package the builders pre-bake.

    Read from the same manifest the build scripts install from, so the package
    set a verifier enforces is exactly the one that was bundled.
    """
    if not MANIFEST.is_file():
        raise BundleCheckError(f"missing package manifest {MANIFEST}")
    packages: list[tuple[str, str]] = []
    for raw in MANIFEST.read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            raise BundleCheckError(f"manifest line missing import name: {raw!r}")
        packages.append((parts[0], parts[1]))
    if not packages:
        raise BundleCheckError("package manifest lists no packages")
    return packages


def bundled_imports() -> list[str]:
    return [import_name for _, import_name in manifest_packages()]


# --------------------------------------------------------------------------
# checks shared by every platform's verifier
# --------------------------------------------------------------------------


def declared_version(src: Path) -> str:
    """``openai4s.__version__`` as the *bundled* source tree declares it."""
    tree = ast.parse((src / "openai4s" / "__init__.py").read_text("utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == "__version__":
                if isinstance(node.value, ast.Constant) and isinstance(
                    node.value.value, str
                ):
                    return node.value.value
    raise BundleCheckError("bundled openai4s.__version__ is not a literal string")


def check_sources(src: Path) -> int:
    """Every runtime-only resource is present, and the Skill catalog is real."""
    missing = [name for name in REQUIRED_SOURCES if not (src / name).is_file()]
    if missing:
        raise BundleCheckError("source tree is missing: " + ", ".join(missing))
    skills = sorted(src.glob("skills/*/SKILL.md"))
    skills.extend(sorted(src.glob("skills/bioskills/*/SKILL.md")))
    if len(skills) < MIN_SKILLS:
        raise BundleCheckError(
            f"bundle ships only {len(skills)} Skills; expected at least {MIN_SKILLS}"
        )
    return len(skills)


def check_bytecode(roots: list[Path]) -> int:
    """Every .py in the image must ship never-revalidated hash-based bytecode.

    If it does not, the app compiles on first import and writes ``__pycache__``
    into its own bundle — which on macOS invalidates the code signature the
    moment anyone uses the app, and on every platform silently recompiles the
    entire stdlib and science stack on *each* launch wherever the bundle is
    read-only (straight from the DMG, or a system-wide install directory a
    non-admin user cannot write to). Timestamp bytecode is no better: unpacking
    or copying the bundle rewrites the .py mtimes, so all of it reads stale.
    """
    compiled: list[Path] = []
    for root in roots:
        if root.exists():
            compiled.extend(root.rglob("__pycache__/*.pyc"))
    if len(compiled) < MIN_PYC:
        raise BundleCheckError(
            f"bundle ships only {len(compiled)} .pyc files — it was not precompiled, "
            "so it will write bytecode into its own bundle on first run"
        )
    for path in compiled[:40]:
        flags = int.from_bytes(path.read_bytes()[4:8], "little")
        # bit0 = hash-based, bit1 = check_source. We require hash-based with
        # revalidation OFF, i.e. exactly 0b01.
        if flags & 0b11 != 0b01:
            kind = "timestamp" if not flags & 0b01 else "checked-hash"
            raise BundleCheckError(
                f"{path.name} carries {kind} bytecode; the build must use "
                "--invalidation-mode unchecked-hash or the app will rewrite it in place"
            )
    return len(compiled)


def check_no_secrets(root: Path, src: Path, launchers: list[Path]) -> None:
    """No credential-shaped material anywhere in the shipped tree.

    ``root`` is the whole bundle (the dotenv walk covers third-party packages
    too), ``src`` is our own source tree, and ``launchers`` are the shell /
    batch entry points, which are the one file a build script writes by hand
    and could therefore bake a value into.
    """
    # Imported lazily: `scripts/` must be on sys.path, which is the caller's
    # job, and a module-level import would make this file unimportable on its
    # own.
    from source_secret_scan import candidate_files as secret_scan_candidates
    from source_secret_scan import scan as secret_scan

    shipped = [name for name in FORBIDDEN_SOURCES if (src / name).exists()]
    if shipped:
        raise BundleCheckError("source tree ships: " + ", ".join(shipped))
    # A dotenv anywhere in the image is the one way a maintainer's provider key
    # can actually reach a user, so this walk covers the whole bundle.
    dotenvs = [
        path.relative_to(root).as_posix()
        for path in root.rglob(".env*")
        if path.is_file() and path.name.casefold() not in ENV_TEMPLATES
    ]
    if dotenvs:
        raise BundleCheckError("bundle ships dotenv files: " + ", ".join(dotenvs[:5]))
    scanned = len(secret_scan_candidates(src))
    if scanned < MIN_SCANNED_FILES:
        raise BundleCheckError(
            f"the credential scan only enumerated {scanned} files in the source tree — "
            "it is not actually inspecting the bundle"
        )
    findings = secret_scan(src)
    if findings:
        located = ", ".join(
            f"{finding.path}:{finding.line}:{finding.detector}"
            for finding in findings[:5]
        )
        raise BundleCheckError(
            f"credential-shaped material inside the bundle ({len(findings)} finding(s)): {located}"
        )
    for launcher in launchers:
        if not launcher.is_file():
            continue
        text = launcher.read_text("utf-8", errors="replace")
        if _CREDENTIAL_ASSIGNMENT.search(text):
            raise BundleCheckError(f"{launcher.name} assigns a credential-shaped value")


__all__ = [
    "BundleCheckError",
    "ENV_TEMPLATES",
    "FORBIDDEN_SOURCES",
    "MANIFEST",
    "MIN_PYC",
    "MIN_SCANNED_FILES",
    "MIN_SKILLS",
    "REQUIRED_SOURCES",
    "bundled_imports",
    "check_bytecode",
    "check_no_secrets",
    "check_sources",
    "declared_version",
    "manifest_packages",
]
