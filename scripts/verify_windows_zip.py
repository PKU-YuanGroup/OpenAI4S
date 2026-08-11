#!/usr/bin/env python3
"""Validate the Windows .zip release package using only the standard library.

The Windows package is a Windows-side launcher wrapped around the Linux bundle,
which it installs into WSL2 on first run. Its failure modes are therefore not
the Linux bundle's — that artifact has its own verifier — but the ones a
Windows package specifically invites:

* a shell script that picked up CRLF endings on the way in and dies inside WSL
  with ``/bin/sh^M: bad interpreter``, on the user's machine rather than here;
* a payload whose checksum sidecar no longer matches it, which turns every
  install into a refusal for the wrong reason;
* a ``__MACOSX`` shadow tree from a macOS host, unzipping beside the package;
* and the one that matters most — a launcher that has quietly grown a native
  Windows execution path. ``openai4s/platform_support.py`` refuses to start a
  kernel on win32, so a package that reached Python directly would produce
  exactly the half-working install that refusal exists to prevent.

    python3 scripts/verify_windows_zip.py dist/OpenAI4S-0.1.0-windows-x86_64.zip
    python3 scripts/verify_windows_zip.py .build/windows/stage/OpenAI4S-0.1.0-windows-x86_64

Runs on any host: nothing here needs Windows, and nothing here is executed.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import re
import sys
import tarfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bundle_contract import BundleCheckError  # noqa: E402
from verify_linux_bundle import check_tar_members  # noqa: E402

_DIRNAME = re.compile(r"^OpenAI4S-(?P<version>[^-]+)-windows-(?P<arch>x86_64|arm64)$")

#: Everything the launcher needs. `payload/` is matched separately because its
#: file name carries the payload's own version and architecture.
_REQUIRED = (
    "OpenAI4S.cmd",
    "openai4s.ps1",
    "VERSION",
    "LICENSE",
    "READ ME FIRST.txt",
    "wsl/bootstrap.sh",
)

#: Must arrive with Windows line endings.
_CRLF_FILES = ("OpenAI4S.cmd", "openai4s.ps1", "VERSION", "READ ME FIRST.txt")

#: Must arrive with Unix line endings, because WSL runs it as a shell script.
_LF_FILES = ("wsl/bootstrap.sh",)

#: A native Windows binary has no business in this package: everything it runs,
#: it runs inside WSL2. `.exe` would be a shipped interpreter or helper, `.pyd`
#: a Windows CPython extension — either one means the package grew a second,
#: unsupported way to start.
_FORBIDDEN_SUFFIXES = (".exe", ".dll", ".pyd", ".msi", ".com", ".scr")

_CREDENTIAL_ASSIGNMENT = re.compile(r"(?i)(api[_-]?key|secret|token)\s*=\s*[\"']?\S")

#: The launcher must keep saying these. Each one is a refusal or a way out that
#: a user hits at exactly the moment they cannot ask anyone: no WSL at all, a
#: WSL 1 distribution (no user namespaces, so the sandbox cannot start), and the
#: localhost-forwarding setting that makes a healthy daemon look dead.
_REQUIRED_GUIDANCE = (
    "wsl --install",
    "wsl --set-version",
    "Ubuntu-24.04",
    "localhostForwarding",
)

#: These are executable contracts, not prose: the Windows side must ask the
#: installed CLI for the authenticated URL, and the WSL side must prove the
#: sandbox baseline before installing or starting anything.
_REQUIRED_LAUNCHER_RUNTIME = (
    "Get-AppUrl",
    "Test-OpenAI4SServing",
    "Test-LocalhostForwardingDisabled",
    "Get-WslIpv4",
    # An existing installation pins the distro choice; without this probe an
    # Ubuntu-24.04 install would silently strand the user's sessions in the
    # previously used distribution.
    "Test-DistroHasInstall",
    "'dev', 'eth0', 'scope', 'global'",
    "192.0.2.1",
    'proxyBypass = "127.0.0.1,localhost,$AppHost"',
    '"NO_PROXY=$proxyBypass"',
    '"no_proxy=$proxyBypass"',
    "if (-not (Test-SandboxIndependentCli $Arguments))",
    "OPENAI4S_WSL_PYPI_INDEX",
    "OPENAI4S_WSL_CONDA_MIRROR",
)

_REQUIRED_BOOTSTRAP_RUNTIME = (
    "preflight)",
    'MIN_BWRAP_VERSION="0.8.0"',
    "--die-with-parent",
    "--new-session",
    "--unshare-ipc",
    "--unshare-uts",
    "--unshare-net",
    "OPENAI4S_KERNEL_SANDBOX",
    "--no-browser",
    "--detached",
    "install_cli_link",
    "OPENAI4S_PYPI_INDEX_URL",
    # Mirror config files are rewritten only when they carry this marker; a
    # user-edited pip.conf/condarc must survive the next launch.
    "managed-by-openai4s-windows-launcher",
)


class _Package:
    """The package contents, from either a .zip or a staged directory."""

    def __init__(self, name: str, entries: dict[str, int]):
        self.name = name
        #: relative path -> uncompressed size
        self.entries = entries

    def read(self, relative: str) -> bytes:  # pragma: no cover - overridden
        raise NotImplementedError


class _ZipPackage(_Package):
    def __init__(self, archive: zipfile.ZipFile):
        names = [n for n in archive.namelist() if not n.endswith("/")]
        if not names:
            raise BundleCheckError("the zip is empty")
        # A macOS host that zipped with resource forks leaves this beside the
        # real tree; on Windows it unzips as a visible junk folder.
        litter = [n for n in names if n.startswith("__MACOSX/") or "/._" in n]
        if litter:
            raise BundleCheckError(
                "the zip carries macOS resource-fork entries: "
                + ", ".join(sorted(litter)[:3])
            )
        tops = {n.split("/", 1)[0] for n in names}
        if len(tops) != 1:
            raise BundleCheckError(
                "the zip must hold exactly one top-level directory, found: "
                + ", ".join(sorted(tops)[:5])
            )
        top = tops.pop()
        self._archive = archive
        self._top = top
        super().__init__(
            top,
            {
                n[len(top) + 1 :]: archive.getinfo(n).file_size
                for n in names
                if n.startswith(top + "/")
            },
        )

    def read(self, relative: str) -> bytes:
        return self._archive.read(f"{self._top}/{relative}")


class _DirPackage(_Package):
    def __init__(self, root: Path):
        self._root = root
        entries = {
            path.relative_to(root).as_posix(): path.stat().st_size
            for path in root.rglob("*")
            if path.is_file()
        }
        if not entries:
            raise BundleCheckError(f"{root} is empty")
        super().__init__(root.name, entries)

    def read(self, relative: str) -> bytes:
        return (self._root / relative).read_bytes()


def _open(target: Path) -> _Package:
    if target.is_dir():
        return _DirPackage(target)
    if not zipfile.is_zipfile(target):
        raise BundleCheckError(f"not a zip archive: {target}")
    return _ZipPackage(zipfile.ZipFile(target))


def _check_shape(package: _Package) -> tuple[str, str]:
    matched = _DIRNAME.match(package.name)
    if not matched:
        raise BundleCheckError(
            f"top-level directory {package.name!r} is not "
            "OpenAI4S-<version>-windows-<arch>"
        )
    missing = [name for name in _REQUIRED if name not in package.entries]
    if missing:
        raise BundleCheckError("the package is missing: " + ", ".join(missing))
    return matched.group("version"), matched.group("arch")


def _check_no_native_windows_path(package: _Package) -> None:
    """Nothing here may start OpenAI4S outside WSL2.

    Two halves, because either one alone is satisfiable by a broken package: no
    Windows executable is shipped, *and* the launcher's only execution path goes
    through wsl.exe.
    """
    shipped = sorted(
        name
        for name in package.entries
        if Path(name).suffix.lower() in _FORBIDDEN_SUFFIXES
    )
    if shipped:
        raise BundleCheckError(
            "the package ships native Windows binaries, which it has no "
            "supported way to use: " + ", ".join(shipped[:5])
        )
    launcher = package.read("openai4s.ps1").decode("utf-8", "replace")
    if "wsl.exe" not in launcher:
        raise BundleCheckError("openai4s.ps1 never invokes wsl.exe")
    # `python`/`pythonw` outside a WSL invocation would be a native start.
    for match in re.finditer(
        r"(?im)^[^#<]*?\b(Start-Process|&)\s+[\"']?(python\w*)", launcher
    ):
        raise BundleCheckError(
            f"openai4s.ps1 starts {match.group(2)!r} on the Windows side; "
            "native Windows kernels are refused by platform_support.py"
        )
    missing = [text for text in _REQUIRED_GUIDANCE if text not in launcher]
    if missing:
        raise BundleCheckError(
            "openai4s.ps1 no longer tells the user how to recover from: "
            + ", ".join(missing)
        )


def _check_wsl_runtime_contract(package: _Package) -> None:
    """The launcher opens an authenticated UI and fails closed on no sandbox."""

    launcher = package.read("openai4s.ps1").decode("ascii")
    bootstrap = package.read("wsl/bootstrap.sh").decode("ascii")
    missing_launcher = [
        text for text in _REQUIRED_LAUNCHER_RUNTIME if text not in launcher
    ]
    if missing_launcher:
        raise BundleCheckError(
            "openai4s.ps1 lost its secure WSL launch contract: "
            + ", ".join(missing_launcher)
        )
    # The bare root is intentionally unauthorized. Opening it yields a 401 and
    # a blank first launch; only `openai4s url` returns the query bootstrap that
    # sets the browser cookie and immediately removes the credential from the
    # address bar.
    if re.search(r"(?im)^\s*Start-Process\s+\$Url\s*$", launcher):
        raise BundleCheckError(
            "openai4s.ps1 opens the unauthenticated bare URL instead of the "
            "tokenized URL returned by openai4s url"
        )
    if "Start-Process $appUrl" not in launcher:
        raise BundleCheckError(
            "openai4s.ps1 never opens the authenticated URL returned by Get-AppUrl"
        )

    missing_bootstrap = [
        text for text in _REQUIRED_BOOTSTRAP_RUNTIME if text not in bootstrap
    ]
    if missing_bootstrap:
        raise BundleCheckError(
            "wsl/bootstrap.sh lost its WSL2/sandbox contract: "
            + ", ".join(missing_bootstrap)
        )
    mismatched_preflight = [
        flag for flag in ("--unshare-user", "--uid 0", "--gid 0") if flag in bootstrap
    ]
    if mismatched_preflight:
        raise BundleCheckError(
            "wsl/bootstrap.sh preflight uses flags absent from the runtime "
            "sandbox: " + ", ".join(mismatched_preflight)
        )


def _check_ascii(package: _Package) -> None:
    """The launcher must be pure ASCII, and this is not a style rule.

    `OpenAI4S.cmd` invokes `powershell.exe` -- Windows PowerShell 5.1, the one a
    user double-clicks -- and 5.1 reads a `.ps1` without a BOM as ANSI rather
    than UTF-8. A UTF-8 em dash decodes under cp1252 to three characters ending
    in 0x94 = U+201D, which PowerShell accepts as a closing double quote, so a
    dash inside a string literal ends the string and the parse collapses far
    below it. Caught on a real windows-latest runner: pwsh 7 parsed the same
    file happily and 5.1 failed 200 lines from the cause.
    """
    for name in _CRLF_FILES + _LF_FILES:
        body = package.read(name)
        try:
            body.decode("ascii")
        except UnicodeDecodeError as error:
            offending = body[error.start : error.start + 1]
            line = body[: error.start].count(b"\n") + 1
            raise BundleCheckError(
                f"{name} line {line} contains the non-ASCII byte {offending!r}; "
                "Windows PowerShell 5.1 decodes this file as ANSI and a stray "
                "0x94 becomes a closing quote that silently truncates a string"
            ) from None


def _check_line_endings(package: _Package) -> None:
    for name in _CRLF_FILES:
        body = package.read(name)
        # Every LF must be part of a CRLF. A file that is half-converted is the
        # one that produces the confusing failure.
        if b"\n" in body.replace(b"\r\n", b""):
            raise BundleCheckError(
                f"{name} has bare LF line endings; it must ship CRLF"
            )
        if b"\r\n" not in body:
            raise BundleCheckError(f"{name} has no CRLF line endings at all")
    for name in _LF_FILES:
        body = package.read(name)
        if b"\r" in body:
            raise BundleCheckError(
                f"{name} contains a carriage return; WSL would fail it with "
                "'bad interpreter'"
            )
        if not body.startswith(b"#!"):
            raise BundleCheckError(f"{name} has no shebang line")


def _payload_names(package: _Package) -> tuple[str, str]:
    tarballs = sorted(
        name
        for name in package.entries
        if name.startswith("payload/") and name.endswith(".tar.gz")
    )
    if len(tarballs) != 1:
        raise BundleCheckError(
            f"expected exactly one payload tarball, found {len(tarballs)}"
        )
    payload = tarballs[0]
    sidecar = f"{payload}.sha256"
    if sidecar not in package.entries:
        raise BundleCheckError(f"the payload has no checksum sidecar ({sidecar})")
    return payload, sidecar


def _check_payload(package: _Package, version: str, arch: str) -> tuple[str, str]:
    """The payload is the right build, intact, and shaped like a Linux bundle."""

    payload, sidecar = _payload_names(package)
    basename = payload.split("/", 1)[1]
    # Windows names the architecture arm64; the Linux bundle names it aarch64.
    linux_arch = "aarch64" if arch == "arm64" else arch
    expected = f"OpenAI4S-{version}-linux-{linux_arch}.tar.gz"
    if basename != expected:
        raise BundleCheckError(
            f"the payload is {basename!r} but this package declares "
            f"{version} / {arch}, which needs {expected!r}"
        )

    blob = package.read(payload)
    actual = hashlib.sha256(blob).hexdigest()
    recorded = package.read(sidecar).decode("utf-8").strip().split()[0]
    if actual != recorded:
        raise BundleCheckError(
            "the payload does not match its checksum sidecar — every install "
            f"would refuse it (sidecar {recorded}, actual {actual})"
        )

    # Stream the members out rather than unpacking 1 GB: this asserts the shape
    # the launcher depends on (one correctly named top-level directory, an
    # executable bin/openai4s for it to run). Everything inside is the Linux
    # bundle's own contract, and verify_linux_bundle.py is what enforces it.
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r|gz") as archive:
        members = {member.name: member for member in archive}
    top = check_tar_members(members)
    return top, actual


def _check_no_secrets(package: _Package) -> None:
    """No credential-shaped material in the package's own hand-written files."""
    dotenvs = [
        name
        for name in package.entries
        if Path(name).name.casefold().startswith(".env")
    ]
    if dotenvs:
        raise BundleCheckError("the package ships dotenv files: " + ", ".join(dotenvs))
    for name in _REQUIRED:
        if name == "LICENSE":
            continue
        text = package.read(name).decode("utf-8", "replace")
        if _CREDENTIAL_ASSIGNMENT.search(text):
            raise BundleCheckError(f"{name} assigns a credential-shaped value")


def verify(target: Path) -> None:
    package = _open(target)
    version, arch = _check_shape(package)
    stamped = package.read("VERSION").decode("utf-8").strip()
    if stamped != version:
        raise BundleCheckError(
            f"VERSION says {stamped!r} but the package is named for {version!r}"
        )
    _check_ascii(package)
    _check_line_endings(package)
    _check_no_native_windows_path(package)
    _check_wsl_runtime_contract(package)
    _check_no_secrets(package)
    payload_dir, digest = _check_payload(package, version, arch)

    total = sum(package.entries.values())
    print(f"package   : {package.name}  (v{version}, windows-{arch})")
    print("launcher  : OpenAI4S.cmd + openai4s.ps1 (CRLF) + wsl/bootstrap.sh (LF)")
    print("platform  : every execution path goes through wsl.exe; no Windows binaries")
    print("WSL gate  : WSL2 + working bubblewrap >= 0.8.0; authenticated browser URL")
    print(f"payload   : {payload_dir}  sha256 {digest[:16]}…, sidecar agrees")
    print(
        f"size      : {total / 1024 / 1024:.0f} MB unpacked, {len(package.entries)} files"
    )
    print("secrets   : none in the launcher, notes or VERSION")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "target", type=Path, help="path to the .zip or the staged package directory"
    )
    args = parser.parse_args(argv)
    try:
        verify(args.target.resolve())
    except (
        BundleCheckError,
        OSError,
        ValueError,
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as error:
        print(f"Windows package verification failed: {error}", file=sys.stderr)
        return 1
    print("Windows package verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
