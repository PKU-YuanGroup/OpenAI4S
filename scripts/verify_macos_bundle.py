#!/usr/bin/env python3
"""Validate the macOS .app/.dmg release image using only the standard library.

``verify_release_artifacts.py`` guards the wheel and sdist. The DMG is a
different contract: it ships an *embedded, relocatable* CPython plus the whole
science stack from ``scripts/bundled_packages.txt`` and the loose source tree,
so the failures worth catching are ones no wheel check can see — a runtime
that does not relocate, a science stack that silently did not install (rdkit,
scanpy, numba …), a source tree missing the Web UI or the R worker, a broken
ad-hoc signature, or a developer's ``.env`` swept into the image.

The half of that contract every platform shares lives in ``bundle_contract.py``;
what stays here is what is genuinely Mac-specific — the ``.app`` layout, the
``Info.plist``, the ``.icns`` ladder, and ``codesign``.

    python scripts/verify_macos_bundle.py dist/OpenAI4S-0.1.0-macos-arm64.dmg
    python scripts/verify_macos_bundle.py .build/dmg/stage/OpenAI4S.app

A ``.dmg`` argument is attached read-only, verified, and detached.
"""

from __future__ import annotations

import argparse
import contextlib
import plistlib
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bundle_contract import BundleCheckError  # noqa: E402
from bundle_contract import bundled_imports  # noqa: E402
from bundle_contract import check_bytecode  # noqa: E402
from bundle_contract import check_no_secrets  # noqa: E402
from bundle_contract import check_sources  # noqa: E402
from bundle_contract import declared_version  # noqa: E402


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


def _check_layout(app: Path) -> tuple[Path, Path, Path]:
    if app.is_symlink():
        raise BundleCheckError("application bundle root must not be a symlink")
    trusted_root = app.resolve()
    current = app
    for part in ("Contents", "Resources", "src"):
        current /= part
        if current.is_symlink():
            raise BundleCheckError(
                "application source path must not contain symlinks: "
                f"{current.relative_to(app).as_posix()}"
            )
        resolved = current.resolve()
        if resolved != trusted_root and trusted_root not in resolved.parents:
            raise BundleCheckError(
                f"application source path escapes the bundle: {current}"
            )
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
        version = declared_version(src)
        _check_plist(app, version)
        skills = check_sources(src)
        bundled = bundled_imports()
        python_version = _check_runtime(runtime, app, bundled)
        compiled = check_bytecode([src, runtime.parents[1] / "lib"])
        icon = _check_icon(app)
        _check_cli(runtime, src)
        check_no_secrets(app, src, [launcher])
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
