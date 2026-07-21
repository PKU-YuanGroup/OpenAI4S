"""What a job actually produced, recorded so it can be checked.

Before this module the compute package contained no hashing at all — a grep
for sha256/hashlib/checksum across `openai4s/compute` and
`openai4s_compute_provider` returned nothing. Three consequences, all of the
same kind:

  * A job could declare `outputs` globs, produce none of them, and still be
    reported `succeeded`. The declared patterns were persisted and never read
    back.
  * `_harvest` returned `dest.rglob("*")` wholesale, so `featured_files` was
    every harvested file rather than the documented subset matching the
    declared globs.
  * A transfer truncated midway is indistinguishable from a complete one when
    nothing records what arrived. `scp` exiting 0 on a partial copy is a real
    failure mode, and a size-and-hash record is the only thing that can see it.

The manifest is deliberately plain: for each harvested file its path relative
to the harvest root, its size, and its SHA-256. Relative paths keep the record
portable and keep the data directory out of it.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

#: Read size for hashing. Job outputs are routinely gigabytes, so the file is
#: streamed rather than read into memory.
_CHUNK = 1024 * 1024


def hash_file(path: Path) -> str:
    """Streaming SHA-256 of one file."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(root: Path) -> list[dict[str, Any]]:
    """Record every file under ``root``, sorted, with size and hash.

    Sorted so the manifest — and therefore its digest — is reproducible for
    the same set of bytes regardless of filesystem enumeration order.
    """
    root = Path(root)
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            size = path.stat().st_size
            checksum = hash_file(path)
        except OSError:
            # A file we cannot read is not a file we can vouch for. Recording
            # it with a null hash keeps it visible instead of dropping it
            # silently from the record of what arrived.
            size, checksum = None, None
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size": size,
                "sha256": checksum,
            }
        )
    return entries


def manifest_digest(entries: Iterable[dict[str, Any]]) -> str:
    """One hash over the whole manifest.

    Changing any file's contents, size, or name changes this value, so it is
    what a later reader compares against to decide whether the harvest they
    are looking at is the harvest that was recorded.
    """
    canonical = json.dumps(
        sorted(entries, key=lambda item: str(item.get("path"))),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def reconcile(
    entries: list[dict[str, Any]], declared: Any
) -> tuple[list[str], list[str]]:
    """Match a manifest against the globs the job promised to produce.

    Returns ``(featured, unmatched)``: the manifest paths matching a declared
    pattern, and the declared patterns that matched nothing.

    An unmatched pattern is the load-bearing half. The job said it would
    produce that file; it did not; and until now the job was reported
    succeeded anyway.

    With nothing declared, every harvested file is featured — the documented
    behaviour of omitting ``outputs``, and there is nothing to fail against.
    """
    paths = [str(item.get("path") or "") for item in entries]
    patterns = _patterns(declared)
    if not patterns:
        return list(paths), []

    featured: list[str] = []
    unmatched: list[str] = []
    for pattern in patterns:
        matches = [
            path
            for path in paths
            # Match the basename too: a job declaring `*.csv` means any csv it
            # wrote, not only ones at the harvest root.
            if fnmatch.fnmatch(path, pattern)
            or fnmatch.fnmatch(Path(path).name, pattern)
        ]
        if matches:
            featured.extend(matches)
        else:
            unmatched.append(pattern)
    # Preserve manifest order and drop duplicates from overlapping patterns.
    seen = set()
    ordered = [
        p for p in paths if p in set(featured) and not (p in seen or seen.add(p))
    ]
    return ordered, unmatched


def _patterns(declared: Any) -> list[str]:
    """Normalise the several shapes `outputs` arrives in.

    Only *featured* patterns are reconciled. The documented list form mixes
    bare globs with ``{'glob': ..., 'visibility': ...}`` entries, and a
    `hidden` entry is explicitly something the caller does not want surfaced —
    failing a job over one would punish them for saying so.

    An unrecognised shape yields no patterns, which means "nothing declared".
    That is the lenient direction on purpose: this decides whether a job is
    marked failed, and inventing a pattern from a shape we do not understand
    would fail correct jobs.
    """
    if declared is None:
        return []
    if isinstance(declared, str):
        try:
            parsed = json.loads(declared)
        except (TypeError, ValueError):
            return [declared] if declared.strip() else []
        declared = parsed
    if isinstance(declared, str):
        return [declared] if declared.strip() else []
    if isinstance(declared, dict):
        declared = declared.get("featured") or declared.get("outputs") or []
    if not isinstance(declared, (list, tuple)):
        return []

    patterns: list[str] = []
    for item in declared:
        if isinstance(item, dict):
            if str(item.get("visibility", "featured")).strip().lower() == "hidden":
                continue
            glob = item.get("glob") or item.get("pattern") or item.get("path")
            item = glob
        text = str(item or "").strip()
        if text:
            patterns.append(text)
    return patterns


__all__ = [
    "build_manifest",
    "hash_file",
    "manifest_digest",
    "reconcile",
]
