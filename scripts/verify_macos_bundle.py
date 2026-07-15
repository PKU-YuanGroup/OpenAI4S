#!/usr/bin/env python3
"""Validate the macOS .app/.dmg release image using only the standard library.

``verify_release_artifacts.py`` guards the wheel and sdist. The DMG is a
different contract: it ships an *embedded, relocatable* CPython plus the whole
science stack from ``scripts/dmg_bundled_packages.txt`` and the loose source
tree, so the failures worth catching are ones no wheel check can see — a runtime
that does not relocate, a science stack that silently did not install (rdkit,
scanpy, numba …), a source tree missing the Web UI or the R worker, a broken
ad-hoc signature, or a developer's ``.env`` swept into the image.

    python scripts/verify_macos_bundle.py dist/OpenAI4S-0.1.0-macos-arm64.dmg
    python scripts/verify_macos_bundle.py .build/dmg/stage/OpenAI4S.app

A ``.dmg`` argument is attached read-only, verified, and detached.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import plistlib
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from source_secret_scan import candidate_files as secret_scan_candidates  # noqa: E402
from source_secret_scan import scan as secret_scan  # noqa: E402

# Resources whose absence only shows up at runtime — a missing app.js is a blank
# browser tab, a missing r_worker.R is a dead R channel — long after release.
_REQUIRED_SOURCES = (
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
# Only ever checked against the top of our own source tree: `tests/` and
# `.git/` are perfectly legitimate *inside* third-party site-packages.
_FORBIDDEN_SOURCES = (".git", ".venv", ".build", "tests", ".claude")
_ENV_TEMPLATES = frozenset({".env.example", ".env.sample", ".env.template"})
_MIN_SKILLS = 20


class BundleCheckError(RuntimeError):
    pass


@contextlib.contextmanager
def _bundle(target: Path) -> Iterator[Path]:
    """Yield the .app directory, attaching a .dmg read-only if needed."""

    if target.suffix != ".dmg":
        yield target
        return
    with tempfile.TemporaryDirectory(prefix="openai4s-dmg-verify-") as mount:
        subprocess.run(
            [
                "hdiutil",
                "attach",
                "-readonly",
                "-nobrowse",
                "-mountpoint",
                mount,
                str(target),
            ],
            check=True,
            capture_output=True,
        )
        try:
            apps = sorted(Path(mount).glob("*.app"))
            if len(apps) != 1:
                raise BundleCheckError(
                    f"expected exactly one .app in the image, found {len(apps)}"
                )
            yield apps[0]
        finally:
            subprocess.run(
                ["hdiutil", "detach", "-force", mount],
                check=False,
                capture_output=True,
            )


def _run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, **kwargs)  # type: ignore[arg-type]


def _declared_version(src: Path) -> str:
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


_MANIFEST = Path(__file__).resolve().parent / "dmg_bundled_packages.txt"


def _bundled_imports() -> list[str]:
    """The import names the build script pre-baked into the bundle.

    Read from the same manifest scripts/build_macos_dmg.sh installs from, so the
    package set the verifier enforces is exactly the one that was bundled — the
    two can never drift. Second column of each non-comment line is the import
    name.
    """
    if not _MANIFEST.is_file():
        raise BundleCheckError(f"missing package manifest {_MANIFEST}")
    imports: list[str] = []
    for raw in _MANIFEST.read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            raise BundleCheckError(f"manifest line missing import name: {raw!r}")
        imports.append(parts[1])
    if not imports:
        raise BundleCheckError("package manifest lists no packages")
    return imports


def _check_layout(app: Path) -> tuple[Path, Path, Path]:
    contents = app / "Contents"
    launcher = contents / "MacOS" / "OpenAI4S"
    runtime = contents / "Resources" / "runtime" / "bin" / "python3"
    src = contents / "Resources" / "src"
    for path, label in (
        (contents / "Info.plist", "Info.plist"),
        (launcher, "launcher"),
        (runtime, "embedded interpreter"),
        (src / "openai4s", "source tree"),
    ):
        if not path.exists():
            raise BundleCheckError(f"bundle is missing its {label}: {path}")
    for path in (launcher, runtime):
        mode = path.stat().st_mode
        if not mode & 0o111:
            raise BundleCheckError(f"not executable: {path}")
    return launcher, runtime, src


def _check_plist(app: Path, version: str) -> None:
    plist = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    expected = {
        "CFBundleExecutable": "OpenAI4S",
        "CFBundleIdentifier": "com.openai4s.app",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "CFBundlePackageType": "APPL",
    }
    mismatched = [
        f"{key}={plist.get(key)!r} (want {want!r})"
        for key, want in expected.items()
        if plist.get(key) != want
    ]
    if mismatched:
        raise BundleCheckError("Info.plist mismatch: " + ", ".join(mismatched))
    if not (
        app / "Contents" / "Resources" / plist.get("CFBundleIconFile", "")
    ).exists():
        raise BundleCheckError("Info.plist declares an icon the bundle does not ship")


def _check_runtime(runtime: Path, app: Path, imports: list[str]) -> str:
    """The interpreter must relocate with the bundle and already hold the stack."""

    probe = (
        "import json, sys\n"
        "mods = json.loads(sys.argv[1])\n"
        "missing = []\n"
        "located = {}\n"
        "for name in mods:\n"
        "    try:\n"
        "        located[name] = getattr(__import__(name), '__file__', None)\n"
        "    except Exception as error:\n"
        "        missing.append(f'{name}: {error}')\n"
        "print(json.dumps({'prefix': sys.prefix, 'version': sys.version.split()[0],\n"
        "                  'executable': sys.executable, 'missing': missing,\n"
        "                  'located': located}))\n"
    )
    import json

    # -I (isolated): no cwd on sys.path, no PYTHONPATH, no user site. Without it
    # the checker's own environment can satisfy an import the bundle is missing,
    # and this is the ONLY thing standing between the build script's hardcoded
    # install list and preinstall.py's CORE_PACKAGES drifting apart.
    result = _run(
        [str(runtime), "-I", "-c", probe, json.dumps(imports)],
        cwd=str(Path(tempfile.gettempdir())),
        env={"PATH": "/usr/bin:/bin"},
    )
    if result.returncode != 0:
        raise BundleCheckError(
            f"embedded interpreter failed to run: {result.stderr.strip()}"
        )
    report = json.loads(result.stdout.strip().splitlines()[-1])
    if report["missing"]:
        raise BundleCheckError(
            "the bundled science stack is incomplete — " + "; ".join(report["missing"])
        )
    root = app.resolve()
    if not imports or sorted(report["located"]) != sorted(imports):
        raise BundleCheckError("the import probe did not report every CORE package")
    # Importable is not the same as bundled: prove each one resolved out of the
    # app itself.
    for name, origin in report["located"].items():
        if not origin or root not in Path(origin).resolve().parents:
            raise BundleCheckError(
                f"{name} did not resolve from inside the bundle (origin={origin})"
            )
    prefix = Path(report["prefix"]).resolve()
    if root not in prefix.parents and prefix != root:
        raise BundleCheckError(
            f"embedded interpreter did not relocate into the bundle: sys.prefix={prefix}"
        )
    return str(report["version"])


def _check_cli(runtime: Path, src: Path) -> None:
    """The daemon entry point must work offline, from outside any checkout."""

    result = _run(
        [str(runtime), "-m", "openai4s", "--help"],
        cwd=str(Path(tempfile.gettempdir())),
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(src), "HOME": str(Path.home())},
    )
    if result.returncode != 0 or "serve" not in result.stdout:
        raise BundleCheckError(
            "`python -m openai4s --help` did not run inside the bundle: "
            + (result.stderr.strip() or result.stdout.strip())[:400]
        )


def _check_icon(app: Path) -> str:
    """The .icns must exist and carry the full Retina ladder.

    Info.plist declares the icon, so an absent or truncated one is a bundle that
    shows a blank page in the Dock. `icns` is a container of named entries; a
    build that only sliced small sizes still produces a valid file, so check for
    the 512@2x (1024px) entry by name rather than trusting the file's existence.
    """
    icon = app / "Contents" / "Resources" / "app.icns"
    if not icon.is_file():
        raise BundleCheckError("bundle ships no app.icns")
    payload = icon.read_bytes()
    if payload[:4] != b"icns":
        raise BundleCheckError("app.icns is not an icns container")
    # ic07/ic08/ic09/ic10 = 128/256/512/1024px; ic10 is the 512@2x Retina slot.
    required = {b"ic07", b"ic08", b"ic09", b"ic10"}
    missing = sorted(name.decode() for name in required if name not in payload)
    if missing:
        raise BundleCheckError("app.icns is missing icon sizes: " + ", ".join(missing))
    return f"{len(payload) / 1024:.0f} KB, Retina ladder complete"


def _check_bytecode(src: Path, runtime: Path) -> int:
    """Every .py in the image must ship never-revalidated hash-based bytecode.

    If it does not, the app compiles on first import and writes __pycache__ into
    its own bundle — which invalidates the code signature the moment anyone uses
    the app, and, wherever the bundle is read-only (straight from the DMG, or
    /Applications for a non-admin user), silently recompiles the entire stdlib
    and science stack on *every* launch. Timestamp bytecode is no better: copying
    the app out of the image rewrites the .py mtimes, so all of it reads stale.
    """
    lib = runtime.parents[1] / "lib"
    compiled = list(src.rglob("__pycache__/*.pyc")) + list(
        lib.rglob("__pycache__/*.pyc")
    )
    if len(compiled) < 500:
        raise BundleCheckError(
            f"bundle ships only {len(compiled)} .pyc files — it was not precompiled, "
            "so it will write bytecode into its own signed bundle on first run"
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


def _check_sources(src: Path) -> int:
    missing = [name for name in _REQUIRED_SOURCES if not (src / name).is_file()]
    if missing:
        raise BundleCheckError("source tree is missing: " + ", ".join(missing))
    skills = sorted(src.glob("skills/*/SKILL.md"))
    if len(skills) < _MIN_SKILLS:
        raise BundleCheckError(
            f"bundle ships only {len(skills)} Skills; expected at least {_MIN_SKILLS}"
        )
    return len(skills)


def _check_no_secrets(app: Path, src: Path) -> None:
    shipped = [name for name in _FORBIDDEN_SOURCES if (src / name).exists()]
    if shipped:
        raise BundleCheckError("source tree ships: " + ", ".join(shipped))
    # A dotenv anywhere in the image is the one way a maintainer's provider key
    # can actually reach a user, so this walk covers the whole bundle.
    dotenvs = [
        path.relative_to(app).as_posix()
        for path in app.rglob(".env*")
        if path.is_file() and path.name.casefold() not in _ENV_TEMPLATES
    ]
    if dotenvs:
        raise BundleCheckError("bundle ships dotenv files: " + ", ".join(dotenvs[:5]))
    # A scanner that silently enumerated nothing reports the same "clean" as one
    # that actually looked, so make the sample size part of the assertion.
    scanned = len(secret_scan_candidates(src))
    if scanned < 200:
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
    launcher_text = (app / "Contents" / "MacOS" / "OpenAI4S").read_text("utf-8")
    if re.search(r"(?i)(api[_-]?key|secret|token)\s*=\s*[\"']?\S", launcher_text):
        raise BundleCheckError("the launcher assigns a credential-shaped value")


def _check_signature(app: Path) -> str:
    result = _run(["codesign", "--verify", "--deep", "--strict", str(app)])
    if result.returncode != 0:
        raise BundleCheckError(
            "code signature does not verify (macOS will kill the app): "
            + result.stderr.strip()[:400]
        )
    display = _run(["codesign", "--display", "--verbose=2", str(app)])
    for line in (display.stderr or "").splitlines():
        if line.startswith("Signature="):
            return line.split("=", 1)[1]
    return "ad-hoc"


def verify(target: Path) -> None:
    with _bundle(target) as app:
        launcher, runtime, src = _check_layout(app)
        version = _declared_version(src)
        _check_plist(app, version)
        skills = _check_sources(src)
        bundled = _bundled_imports()
        python_version = _check_runtime(runtime, app, bundled)
        compiled = _check_bytecode(src, runtime)
        icon = _check_icon(app)
        _check_cli(runtime, src)
        _check_no_secrets(app, src)
        signature = _check_signature(app)
        print(f"bundle    : {app.name}  (v{version})")
        print(
            f"runtime   : embedded CPython {python_version}, {len(bundled)} science packages import from the bundle"
        )
        print(f"sources   : Web UI + R worker + compute templates + {skills} Skills")
        print(f"icon      : {icon}")
        print(
            f"bytecode  : {compiled} precompiled .pyc, hash-based (never rewritten in place)"
        )
        print(f"signature : {signature}; no credential material in the image")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="path to OpenAI4S.app or a .dmg")
    args = parser.parse_args(argv)
    try:
        verify(args.target.resolve())
    except (
        BundleCheckError,
        OSError,
        SyntaxError,
        ValueError,
        subprocess.CalledProcessError,
    ) as error:
        print(f"macOS bundle verification failed: {error}", file=sys.stderr)
        return 1
    print("macOS bundle verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
