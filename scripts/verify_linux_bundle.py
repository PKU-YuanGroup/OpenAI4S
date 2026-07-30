#!/usr/bin/env python3
"""Validate the Linux .tar.gz release bundle using only the standard library.

``verify_release_artifacts.py`` guards the wheel and sdist and
``verify_macos_bundle.py`` guards the DMG. The Linux tarball is a third
contract: it ships an *embedded, relocatable* CPython plus the whole science
stack from ``scripts/bundled_packages.txt`` and the loose source tree, so the
failures worth catching are ones no wheel check can see — a runtime that did not
relocate, a science stack that silently did not install, a source tree missing
the Web UI or the R worker, a desktop entry that launches nothing, or a
developer's ``.env`` swept into the archive.

    python3 scripts/verify_linux_bundle.py dist/OpenAI4S-0.1.0-linux-x86_64.tar.gz
    python3 scripts/verify_linux_bundle.py .build/linux/stage/OpenAI4S-0.1.0-linux-x86_64

Everything here runs on any host, because the archive can be inspected without
being executed. On a Linux host of the matching architecture it does strictly
more: it *runs* the embedded interpreter, which is the only way to prove the
science stack imports rather than merely being present on disk. A cross-built
image therefore gets a real verification, just a weaker one, and the report says
which of the two it was rather than letting a skipped check read as a pass.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import platform
import re
import struct
import subprocess
import sys
import tarfile
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

#: Relative path -> whether it must carry an executable bit.
_REQUIRED_ENTRIES = {
    "OpenAI4S": True,
    "bin/openai4s": True,
    "install.sh": True,
    "uninstall.sh": True,
    "runtime/bin/python3": True,
    "VERSION": False,
    "LICENSE": False,
    "runtime/pip.conf": False,
    "share/applications/openai4s.desktop.in": False,
    "src/openai4s/__init__.py": False,
}

_ICON_SIZES = (16, 32, 48, 64, 128, 256, 512)

_DIRNAME = re.compile(r"^OpenAI4S-(?P<version>[^-]+)-linux-(?P<arch>x86_64|aarch64)$")

_ARCH_ALIASES = {"amd64": "x86_64", "arm64": "aarch64"}


@contextlib.contextmanager
def _unpacked(target: Path) -> Iterator[tuple[Path, tarfile.TarFile | None]]:
    """Yield the app directory, unpacking a ``.tar.gz`` into a temp dir first."""

    if target.is_dir():
        yield target, None
        return
    if not tarfile.is_tarfile(target):
        raise BundleCheckError(f"not a tar archive: {target}")
    with tarfile.open(target, "r:gz") as archive:
        check_tar_members({member.name: member for member in archive.getmembers()})
        with tempfile.TemporaryDirectory(prefix="openai4s-linux-verify-") as scratch:
            # `data` refuses absolute paths, parent-directory escapes and links
            # that point outside the archive. Verification must not be the step
            # that lets a malformed artifact write outside its own temp dir.
            archive.extractall(scratch, filter="data")
            roots = [path for path in Path(scratch).iterdir() if path.is_dir()]
            if len(roots) != 1:
                raise BundleCheckError(
                    f"expected exactly one top-level directory, found {len(roots)}"
                )
            yield roots[0], archive


def check_tar_members(members: dict[str, tarfile.TarInfo]) -> str:
    """One top-level directory, correctly named, with the exec bits intact.

    Takes an already-collected member map rather than a ``TarFile`` so the same
    check can run over a tarball that is being streamed out of the Windows
    package's zip, where nothing is seekable. Returns the top-level directory
    name.

    Mode is read from the archive rather than from an unpacked copy: the
    executable bit is a property of what ships, and an extraction can add or
    lose one. A launcher that arrives non-executable is a bundle nobody can
    start.
    """
    if not members:
        raise BundleCheckError("the archive is empty")
    tops = {name.split("/", 1)[0] for name in members}
    if len(tops) != 1:
        raise BundleCheckError(
            "the archive must hold exactly one top-level directory, found: "
            + ", ".join(sorted(tops)[:5])
        )
    top = tops.pop()
    if not _DIRNAME.match(top):
        raise BundleCheckError(
            f"top-level directory {top!r} is not OpenAI4S-<version>-linux-<arch>"
        )
    for relative, executable in _REQUIRED_ENTRIES.items():
        member = members.get(f"{top}/{relative}")
        if member is None:
            raise BundleCheckError(f"the archive is missing {relative}")
        if executable and not member.mode & 0o111:
            raise BundleCheckError(
                f"{relative} is not executable inside the archive (mode "
                f"{member.mode:o}); unpacking it would produce a bundle that "
                "cannot be started"
            )
    return top


def _archive_arch(app: Path) -> tuple[str, str]:
    matched = _DIRNAME.match(app.name)
    if not matched:
        raise BundleCheckError(
            f"directory {app.name!r} is not OpenAI4S-<version>-linux-<arch>"
        )
    return matched.group("version"), matched.group("arch")


def _check_layout(app: Path) -> tuple[Path, Path, Path]:
    launcher = app / "OpenAI4S"
    runtime = app / "runtime" / "bin" / "python3"
    src = app / "src"
    for path, label in (
        (launcher, "launcher"),
        (runtime, "embedded interpreter"),
        (app / "bin" / "openai4s", "bundled CLI"),
        (app / "install.sh", "install.sh"),
        (src / "openai4s", "source tree"),
    ):
        if not path.exists():
            raise BundleCheckError(f"bundle is missing its {label}: {path}")
    for path in (launcher, runtime, app / "bin" / "openai4s", app / "install.sh"):
        if not path.stat().st_mode & 0o111:
            raise BundleCheckError(f"not executable: {path}")
    return launcher, runtime, src


def _check_version(app: Path, src: Path) -> str:
    """The three places a version is written must agree.

    They are produced by three different mechanisms — a `sed` out of the source
    at build time, the directory name, and the source itself — so a mismatch
    means the build ran against a tree it did not ship.
    """
    declared = declared_version(src)
    stamped = (app / "VERSION").read_text("utf-8").strip()
    from_name, _arch = _archive_arch(app)
    if not declared == stamped == from_name:
        raise BundleCheckError(
            "version mismatch: source declares "
            f"{declared!r}, VERSION says {stamped!r}, directory name says {from_name!r}"
        )
    return declared


def _site_packages(app: Path) -> Path:
    candidates = sorted((app / "runtime" / "lib").glob("python3.*/site-packages"))
    if not candidates:
        raise BundleCheckError("the embedded runtime has no site-packages directory")
    return candidates[0]


def _check_stack_present(app: Path, imports: list[str]) -> int:
    """Every manifest package is physically inside the bundle's site-packages.

    This is the check that works everywhere, including from a machine that
    cannot execute the target. It is deliberately weaker than the import probe
    and does not replace it: a package can be present and still fail to import.
    """
    site = _site_packages(app)
    missing = []
    for name in imports:
        found = (
            (site / name / "__init__.py").is_file()
            or (site / f"{name}.py").is_file()
            or any(site.glob(f"{name}.*.so"))
            or any(site.glob(f"{name}.so"))
            or (site / name).is_dir()
        )
        if not found:
            missing.append(name)
    if missing:
        raise BundleCheckError(
            "the bundled science stack is incomplete — missing from "
            f"{site.name}: {', '.join(missing)}"
        )
    return len(imports)


def _run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, **kwargs)  # type: ignore[arg-type]


def _can_execute(arch: str) -> bool:
    host = platform.machine().lower()
    return sys.platform.startswith("linux") and _ARCH_ALIASES.get(host, host) == arch


def _check_runtime_executes(runtime: Path, app: Path, imports: list[str]) -> str:
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
        "                  'missing': missing, 'located': located}))\n"
    )
    # -I (isolated): no cwd on sys.path, no PYTHONPATH, no user site. Without it
    # the checker's own environment can satisfy an import the bundle is missing,
    # and this is the ONLY thing standing between the build script's install
    # list and the manifest drifting apart.
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
    if sorted(report["located"]) != sorted(imports):
        raise BundleCheckError("the import probe did not report every CORE package")
    # Importable is not the same as bundled: prove each one resolved out of the
    # bundle itself.
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


def _check_cli_executes(runtime: Path, src: Path) -> None:
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


def _png_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    if header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise BundleCheckError(f"{path.name} is not a PNG with a leading IHDR chunk")
    width, height = struct.unpack(">II", header[16:24])
    return width, height


def _check_icons(app: Path) -> str:
    """The hicolor ladder must exist *and* actually be at the declared sizes.

    A resize step that failed open — writing the 1024px master into every slot —
    leaves a directory tree that looks complete and a desktop that renders a
    blurry, oversized tile, so the pixel dimensions are part of the check.
    """
    root = app / "share" / "icons" / "hicolor"
    for size in _ICON_SIZES:
        icon = root / f"{size}x{size}" / "apps" / "openai4s.png"
        if not icon.is_file():
            raise BundleCheckError(f"bundle ships no {size}x{size} icon")
        actual = _png_size(icon)
        if actual != (size, size):
            raise BundleCheckError(
                f"{icon.relative_to(app)} is {actual[0]}x{actual[1]}, not {size}x{size}"
            )
    return f"hicolor ladder complete ({len(_ICON_SIZES)} sizes, 16-512px)"


def _check_desktop_entry(app: Path) -> None:
    """The menu entry must be a template, and install.sh must fill it in.

    A relocatable tarball does not know where it will be unpacked, so a
    pre-baked absolute ``Exec=`` would produce a menu entry that launches
    nothing. Shipping the template is only correct if something substitutes it,
    which is why both halves are checked together.
    """
    template = app / "share" / "applications" / "openai4s.desktop.in"
    text = template.read_text("utf-8")
    required = (
        "[Desktop Entry]",
        "Type=Application",
        "Name=OpenAI4S",
        "Exec=@APPDIR@/OpenAI4S",
        "Icon=@ICON@",
    )
    missing = [line for line in required if line not in text]
    if missing:
        raise BundleCheckError(
            "the desktop entry template is missing: " + ", ".join(missing)
        )
    installer = (app / "install.sh").read_text("utf-8")
    for placeholder in ("@APPDIR@", "@ICON@"):
        if placeholder not in installer:
            raise BundleCheckError(
                f"install.sh never substitutes {placeholder}; the installed menu "
                "entry would carry the literal placeholder and launch nothing"
            )


def _check_pip_redirect(app: Path) -> None:
    """pip must be redirected out of the bundle before anything installs.

    The kernel strips every ``PIP_*`` variable from a cell's environment, so an
    env-based redirect covers the daemon and misses ``host.bash("pip install
    …")``. Without the site config, the first in-cell install writes into the
    unpacked bundle — which fails outright on a system-wide or read-only unpack.
    """
    text = (app / "runtime" / "pip.conf").read_text("utf-8")
    if "user = true" not in text.replace("user=true", "user = true"):
        raise BundleCheckError(
            "runtime/pip.conf does not redirect installs to the user site"
        )


def _check_relocatable(app: Path, launcher: Path) -> None:
    """Nothing in the entry points may point back at the build machine."""
    for script in (launcher, app / "bin" / "openai4s", app / "install.sh"):
        text = script.read_text("utf-8")
        if "BASH_SOURCE" not in text:
            raise BundleCheckError(
                f"{script.name} does not self-locate; it cannot be relocatable"
            )
        for stale in re.findall(
            r"(?m)^[^#\n]*?(/(?:Users|home)/[\w.-]+/[\w./-]+)", text
        ):
            raise BundleCheckError(
                f"{script.name} hardcodes a build-machine path: {stale}"
            )


def verify(target: Path) -> None:
    with _unpacked(target) as (app, _archive):
        launcher, runtime, src = _check_layout(app)
        version = _check_version(app, src)
        _, arch = _archive_arch(app)
        skills = check_sources(src)
        imports = bundled_imports()
        present = _check_stack_present(app, imports)
        compiled = check_bytecode([src, app / "runtime" / "lib"])
        icons = _check_icons(app)
        _check_desktop_entry(app)
        _check_pip_redirect(app)
        _check_relocatable(app, launcher)
        check_no_secrets(
            app,
            src,
            [
                launcher,
                app / "bin" / "openai4s",
                app / "install.sh",
                app / "uninstall.sh",
            ],
        )
        if _can_execute(arch):
            python_version = _check_runtime_executes(runtime, app, imports)
            _check_cli_executes(runtime, src)
            runtime_note = (
                f"embedded CPython {python_version}, {present} science packages "
                "imported from inside the bundle; `python -m openai4s --help` runs"
            )
            depth = "executed"
        else:
            runtime_note = (
                f"{present} science packages present in the bundle's site-packages "
                f"(not imported: this host cannot run linux-{arch} binaries)"
            )
            depth = f"static only — run this on a linux-{arch} host before releasing"

        print(f"bundle    : {app.name}  (v{version})")
        print(f"runtime   : {runtime_note}")
        print(f"sources   : Web UI + R worker + compute templates + {skills} Skills")
        print(f"icons     : {icons}")
        print(
            f"bytecode  : {compiled} precompiled .pyc, hash-based (never rewritten in place)"
        )
        print("desktop   : relocatable entry template + install.sh substitution")
        print("secrets   : none; no build-machine paths in the entry points")
        print(f"depth     : {depth}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "target", type=Path, help="path to the .tar.gz or the unpacked app directory"
    )
    args = parser.parse_args(argv)
    try:
        verify(args.target.resolve())
    except (
        BundleCheckError,
        OSError,
        SyntaxError,
        ValueError,
        tarfile.TarError,
        subprocess.CalledProcessError,
    ) as error:
        print(f"Linux bundle verification failed: {error}", file=sys.stderr)
        return 1
    print("Linux bundle verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
