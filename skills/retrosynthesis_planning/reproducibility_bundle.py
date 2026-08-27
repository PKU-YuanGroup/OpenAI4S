"""Build a deterministic, path-sanitized scientific reproducibility ZIP."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable

ALLOWED_SUFFIXES = {".json", ".md", ".py", ".txt", ".yaml", ".yml"}
MAX_FILE_BYTES = 64 * 1024 * 1024
_LOCAL_PATH = re.compile(rb"(?:/(?:aaa|home|Users|root)/|[A-Za-z]:[\\/]Users[\\/])")


class ReproducibilityBundleError(ValueError):
    """Raised when a proposed public bundle is unsafe or unsupported."""


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    return info


def build_reproducibility_bundle(
    source: str | Path,
    output: str | Path,
    *,
    forbidden_fragments: Iterable[str] = (),
) -> dict[str, object]:
    """Scan ``source`` and atomically create a deterministic ZIP at ``output``."""

    root = Path(source).resolve()
    destination = Path(output).resolve()
    if not root.is_dir():
        raise ReproducibilityBundleError("source must be an existing directory")
    if destination == root or root in destination.parents:
        raise ReproducibilityBundleError("output must be outside the source directory")
    forbidden = tuple(item.encode("utf-8") for item in forbidden_fragments if item)
    records: list[tuple[str, bytes, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ReproducibilityBundleError(f"symlink is not allowed: {path.name}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if path.suffix.lower() not in ALLOWED_SUFFIXES:
            raise ReproducibilityBundleError(
                f"unsupported file type in public bundle: {relative}"
            )
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ReproducibilityBundleError(
                f"file exceeds the 64 MiB public-bundle limit: {relative}"
            )
        payload = path.read_bytes()
        if _LOCAL_PATH.search(payload) or any(item in payload for item in forbidden):
            raise ReproducibilityBundleError(
                f"local or forbidden identifier found in: {relative}"
            )
        records.append((relative, payload, hashlib.sha256(payload).hexdigest()))
    if not records:
        raise ReproducibilityBundleError("source contains no supported files")
    checksum_payload = "".join(
        f"{digest}  {relative}\n" for relative, _payload, digest in records
    ).encode("utf-8")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=destination.name + ".", delete=False
        ) as handle:
            temporary = Path(handle.name)
        with zipfile.ZipFile(temporary, "w") as archive:
            for relative, payload, _digest in records:
                archive.writestr(_zip_info(relative), payload)
            archive.writestr(_zip_info("CHECKSUMS.sha256"), checksum_payload)
        os.replace(temporary, destination)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return {
        "output": destination.name,
        "files": len(records) + 1,
        "bytes": destination.stat().st_size,
        "sha256": digest,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--forbid", action="append", default=[])
    args = parser.parse_args()
    result = build_reproducibility_bundle(
        args.source, args.output, forbidden_fragments=args.forbid
    )
    print(
        f"created {result['output']} ({result['files']} files, "
        f"{result['bytes']} bytes, sha256={result['sha256']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
