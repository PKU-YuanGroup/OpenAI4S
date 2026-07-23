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
        error: str | None = None
        try:
            size = path.stat().st_size
            checksum = hash_file(path)
        except OSError as e:
            # A file we cannot read is not a file we can vouch for. Recording
            # it with a null hash keeps it visible instead of dropping it
            # silently from the record of what arrived — but the *reason* has
            # to ride along, because a path with no hash is the one entry a
            # reader must not mistake for a delivered output.
            size, checksum, error = None, None, f"{type(e).__name__}: {e}"
        entry: dict[str, Any] = {
            "path": path.relative_to(root).as_posix(),
            "size": size,
            "sha256": checksum,
        }
        if error:
            entry["error"] = error
        entries.append(entry)
    return entries


def unverified(entries: Iterable[dict[str, Any]]) -> list[str]:
    """Manifest paths recorded without a content hash.

    A harvested file that could not be read — permissions, a truncated
    transfer, an I/O error — was recorded with ``size`` and ``sha256`` set to
    null and then counted as a delivered output, because ``reconcile`` matched
    on path alone and nothing downstream looked at the hash. A job could
    therefore exit 0 and be reported ``succeeded`` with not one verifiable
    content hash behind it, which is precisely the false success the manifest
    exists to prevent.
    """
    return [
        str(entry.get("path") or "") for entry in entries if not entry.get("sha256")
    ]


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

    Only *verified* entries can satisfy a pattern. A file whose bytes could not
    be read has no hash, and a path is not evidence: matching on the path alone
    let an unreadable file discharge the promise it was named by. A pattern
    matched exclusively by such files is reported unmatched, which is the
    honest answer — the job said it would produce that output and nothing here
    can vouch that it did.
    """
    paths = [str(item.get("path") or "") for item in entries if item.get("sha256")]
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


def _declared_items(declared: Any) -> list[Any]:
    """Normalise the several shapes `outputs` arrives in into a flat list.

    An unrecognised shape yields nothing, which means "nothing declared". That
    is the lenient direction on purpose: this decides whether a job is marked
    failed, and inventing a pattern from a shape we do not understand would
    fail correct jobs.
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
    return list(declared)


def _glob_of(item: Any) -> str:
    if isinstance(item, dict):
        item = item.get("glob") or item.get("pattern") or item.get("path")
    return str(item or "").strip()


def _is_remote(item: Any) -> bool:
    return (
        isinstance(item, dict)
        and str(item.get("residency", "local")).strip().lower() == "remote"
    )


def _patterns(declared: Any) -> list[str]:
    """The globs a harvest is expected to satisfy.

    Only *featured, local* patterns are reconciled. The documented list form
    mixes bare globs with ``{'glob': ..., 'visibility': ...}`` entries, and a
    `hidden` entry is explicitly something the caller does not want surfaced —
    failing a job over one would punish them for saying so. The same applies to
    ``{'residency': 'remote'}``: the caller asked for that output to *stay* on
    the cluster, so its absence from the harvest is the requested outcome, not
    an unmet promise.
    """
    patterns: list[str] = []
    for item in _declared_items(declared):
        if isinstance(item, dict):
            if str(item.get("visibility", "featured")).strip().lower() == "hidden":
                continue
            if _is_remote(item):
                continue
        text = _glob_of(item)
        if text:
            patterns.append(text)
    return patterns


def remote_patterns(declared: Any) -> list[str]:
    """The globs the caller asked to keep on the cluster.

    ``_patterns`` already skips these so a stay-remote output is never counted
    missing — but that was only half the contract, and the quiet half. The
    transport still tarred the file, downloaded it and listed it in
    ``output_files``: the declaration said "do not move this", and the only
    thing it changed was whether the job got *blamed* for not moving it.

    These are the patterns the harvest itself must exclude.
    """
    patterns: list[str] = []
    for item in _declared_items(declared):
        if not _is_remote(item):
            continue
        text = _glob_of(item)
        if text:
            patterns.append(text)
    return patterns


def matches_any(path: str, patterns: list[str]) -> bool:
    """Does one harvested path match any of these globs?

    Path *or* basename, the same pair of tests ``reconcile`` uses, so an
    exclusion and a promise can never disagree about what a pattern covers.
    """
    name = Path(path).name
    return any(
        fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(name, pattern)
        for pattern in patterns
    )


__all__ = [
    "build_manifest",
    "hash_file",
    "manifest_digest",
    "matches_any",
    "reconcile",
    "remote_patterns",
    "unverified",
]
