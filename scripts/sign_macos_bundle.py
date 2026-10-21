#!/usr/bin/env python3
"""Sign embedded Mach-O files before sealing the macOS application bundle.

The Python runtime lives under Resources, where codesign --deep does not
discover nested code. Only Python receives JIT and user-wheel entitlements.
Use --sign - only for a local hardened-runtime rehearsal, never distribution.
"""

from __future__ import annotations

import argparse
import plistlib
import subprocess
import tempfile
from pathlib import Path

MACH_O_MAGIC = {
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf",
    b"\xbf\xba\xfe\xca",
}

PYTHON_ENTITLEMENTS = {
    # Numba/LLVM and ctypes callbacks allocate executable code at runtime.
    "com.apple.security.cs.allow-jit": True,
    # UMAP's eager Numba compilation also uses executable memory without
    # MAP_JIT; allow-jit alone terminates the interpreter during import.
    "com.apple.security.cs.allow-unsigned-executable-memory": True,
    # User-installed scientific wheels are not signed by our Developer ID.
    # Application/kernel permissions remain independent of this exception.
    "com.apple.security.cs.disable-library-validation": True,
}


def native_code(app: Path) -> list[Path]:
    """Find code in data locations, without following external symlinks."""
    root = app.resolve(strict=True)
    files = []
    for path in app.rglob("*"):
        if path.is_symlink():
            if not path.resolve().is_relative_to(root):
                raise ValueError(f"bundle symlink escapes the application: {path}")
            continue
        if path.is_file():
            with path.open("rb") as stream:
                if stream.read(4) in MACH_O_MAGIC:
                    files.append(path)
    return sorted(files, key=lambda path: (-len(path.parts), str(path)))


def _codesign(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["codesign", *args], check=True, capture_output=True, text=True
    )


def sign(app: Path, identity: str) -> int:
    if app.is_symlink() or not app.is_dir() or app.suffix != ".app":
        raise ValueError("expected a real .app directory")
    app = app.resolve()
    files = native_code(app)
    python = (app / "Contents/Resources/runtime/bin/python3").resolve(strict=True)
    if python not in files:
        raise ValueError("embedded Python is not a Mach-O inside the application")
    if not identity.strip():
        raise ValueError("a signing identity is required")

    options = [
        "--force",
        "--sign",
        identity,
        "--options",
        "runtime",
        "--timestamp=none" if identity == "-" else "--timestamp",
    ]
    with tempfile.TemporaryDirectory(prefix="openai4s-sign-") as temporary:
        entitlements = Path(temporary) / "python.plist"
        entitlements.write_bytes(plistlib.dumps(PYTHON_ENTITLEMENTS))
        for path in files:
            extra = ["--entitlements", str(entitlements)] if path == python else []
            _codesign(*options, *extra, str(path))
            _codesign("--verify", "--strict", str(path))
            if identity != "-":
                details = _codesign("--display", "--verbose=4", str(path)).stderr
                if (
                    "Authority=Developer ID Application:" not in details
                    or "Timestamp=" not in details
                ):
                    raise ValueError(f"missing Developer ID or timestamp: {path}")
        # --deep is appropriate for verification, not signature discovery.
        # The outer resource seal must be written after every nested signature.
        _codesign(*options, str(app))
        _codesign("--verify", "--deep", "--strict", str(app))
    return len(files)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sign", required=True, help="Developer ID identity or -")
    parser.add_argument("app", type=Path)
    args = parser.parse_args()
    try:
        count = sign(args.app, args.sign)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else exc
        parser.exit(1, f"macOS signing failed: {detail}\n")
    kind = "ad-hoc rehearsal only" if args.sign == "-" else "Developer ID"
    print(f"Signed and verified {count} Mach-O files and the app ({kind}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
