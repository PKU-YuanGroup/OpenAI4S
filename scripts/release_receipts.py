#!/usr/bin/env python3
"""Build receipts, and the attestation that survives a mutable draft.

Two documents, both answering a question the pipeline could not previously ask.

**Build receipt** — "which sources is this artifact from, and what built it?"
Every job in the release workflow checked out `inputs.tag` independently. A tag is
mutable, so the wheel, the sdist and the DMG could each come from a different
commit, and nothing recorded or compared where any of them came from. The quality
receipt binds the *gates* to a SHA; nothing bound the *bytes*. A receipt is
written beside each artifact by the job that built it, naming the source SHA, the
artifact digests, and the OS/arch/interpreter of the builder — and staging
verifies every one of them against the frozen SHA before it stages anything.

**Stage attestation** — "is the draft still the release that was verified?"
`step_publish` re-hashed the draft's assets against the draft's own `SHA256SUMS`.
`SHA256SUMS` is itself a draft asset, so anything able to replace an asset can
replace the manifest in the same motion and the check passes: it is a document
vouching for itself. The attestation is written by the staging job and travels
through the workflow's artifact store instead of through the draft, so the
finalize job compares the draft against a record the draft cannot reach.

Neither document is signed, and neither claims to establish *who* produced it.
They establish that a set of bytes, a commit and a builder go together.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

BUILD_RECEIPT_FORMAT = "openai4s-build-receipt"
BUILD_RECEIPT_SCHEMA_VERSION = 1

STAGE_ATTESTATION_FORMAT = "openai4s-stage-attestation"
STAGE_ATTESTATION_SCHEMA_VERSION = 1
STAGE_ATTESTATION_NAME = "stage-attestation.json"

#: Suffix for a per-artifact-group build receipt: `build-receipt-<kind>.json`.
BUILD_RECEIPT_PREFIX = "build-receipt-"


class ReceiptError(Exception):
    """A receipt or attestation is not proof. Do not release."""


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _artifact_rows(artifacts: Iterable[Path]) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(Path(p) for p in artifacts):
        if not path.is_file():
            raise ReceiptError(f"cannot receipt {path}: not a file")
        rows.append(
            {"name": path.name, "sha256": _digest(path), "size": path.stat().st_size}
        )
    return rows


def build_receipt_name(kind: str) -> str:
    return f"{BUILD_RECEIPT_PREFIX}{kind}.json"


def build_build_receipt(
    kind: str,
    source_sha: str,
    artifacts: Sequence[Path],
) -> dict[str, Any]:
    """What one build job produced, and from what.

    `kind` names the job (`dist`, `macos`) so a release carrying several
    receipts can say which is missing rather than "a receipt is missing".
    For a notarized DMG this hashes the *post-staple* bytes: the macOS job
    staples first, then calls this, so the digest staging verifies is the
    digest a user downloads.
    """
    from scripts import release_gates

    if not source_sha:
        raise ReceiptError(
            "a build receipt with no source SHA binds nothing; the workflow must "
            "pass the frozen SHA"
        )
    return {
        "format": BUILD_RECEIPT_FORMAT,
        "schema_version": BUILD_RECEIPT_SCHEMA_VERSION,
        "kind": str(kind),
        "source_sha": str(source_sha),
        "builder": release_gates.builder_facts(),
        "artifacts": _artifact_rows(artifacts),
    }


def verify_build_receipts(
    receipts: Sequence[Path],
    *,
    expected_sha: str,
    assets_dir: Path,
    required_kinds: Sequence[str] = (),
) -> dict[str, Any]:
    """Every receipt must name the frozen SHA, and describe bytes that are here.

    The two halves matter separately. Checking only the SHA would accept a
    receipt for the right commit listing digests nothing on disk has; checking
    only the digests would accept an artifact built from a different commit.
    """
    if not expected_sha:
        raise ReceiptError("cannot verify build receipts without the frozen source SHA")
    seen: dict[str, dict[str, Any]] = {}
    for path in sorted(Path(p) for p in receipts):
        try:
            document = json.loads(Path(path).read_text("utf-8"))
        except (OSError, ValueError) as error:
            raise ReceiptError(f"build receipt {path.name} is unreadable: {error}")
        if not isinstance(document, Mapping):
            raise ReceiptError(f"build receipt {path.name} is not an object")
        if document.get("format") != BUILD_RECEIPT_FORMAT:
            raise ReceiptError(f"build receipt {path.name} has an unrecognised format")
        if document.get("schema_version") != BUILD_RECEIPT_SCHEMA_VERSION:
            raise ReceiptError(
                f"build receipt {path.name} has schema_version "
                f"{document.get('schema_version')!r}, this pipeline requires "
                f"{BUILD_RECEIPT_SCHEMA_VERSION}"
            )
        kind = str(document.get("kind") or "")
        if not kind:
            raise ReceiptError(f"build receipt {path.name} names no kind")
        if kind in seen:
            raise ReceiptError(f"two build receipts claim kind {kind!r}")
        recorded = str(document.get("source_sha") or "")
        if recorded != expected_sha:
            # The retag case. The gates ran on one commit; this artifact was
            # built from another.
            raise ReceiptError(
                f"build receipt {path.name} is for {recorded[:12] or '<none>'} "
                f"but this release is {expected_sha[:12]}; the artifacts and the "
                f"sources are not the same commit"
            )
        builder = document.get("builder")
        if not isinstance(builder, Mapping) or not builder.get("os"):
            raise ReceiptError(f"build receipt {path.name} records no builder platform")
        rows = document.get("artifacts")
        if not isinstance(rows, list) or not rows:
            raise ReceiptError(f"build receipt {path.name} lists no artifacts")
        for row in rows:
            if not isinstance(row, Mapping):
                raise ReceiptError(f"build receipt {path.name} has a malformed row")
            name = str(row.get("name") or "")
            candidate = Path(assets_dir) / name
            if not candidate.is_file():
                raise ReceiptError(
                    f"build receipt {path.name} describes {name}, which is not "
                    f"among the assets being staged"
                )
            actual = _digest(candidate)
            if actual != str(row.get("sha256") or ""):
                raise ReceiptError(
                    f"{name} does not match its build receipt "
                    f"({actual[:12]} != {str(row.get('sha256'))[:12]}); the bytes "
                    f"changed after they were built"
                )
        seen[kind] = dict(document)

    missing = sorted(set(required_kinds) - set(seen))
    if missing:
        raise ReceiptError(
            f"no build receipt for: {', '.join(missing)}; an artifact with no "
            f"receipt cannot be bound to these sources"
        )
    return seen


def build_stage_attestation(
    *,
    version: str,
    source_sha: str,
    assets: Sequence[Path],
) -> dict[str, Any]:
    """The exact asset set the staging job verified, recorded outside the draft."""
    from scripts import release_gates

    if not source_sha:
        raise ReceiptError("a stage attestation with no source SHA binds nothing")
    rows = _artifact_rows(assets)
    if not rows:
        raise ReceiptError("a stage attestation with no assets binds nothing")
    return {
        "format": STAGE_ATTESTATION_FORMAT,
        "schema_version": STAGE_ATTESTATION_SCHEMA_VERSION,
        "version": str(version),
        "source_sha": str(source_sha),
        "attested_by": release_gates.builder_facts(),
        "assets": rows,
    }


def verify_stage_attestation(
    path: Path,
    *,
    version: str,
) -> dict[str, str]:
    """Read the attestation and return the asset set it vouches for.

    Returns `{name: sha256}` rather than a verdict; the caller compares it to
    what the draft currently holds. Keeping the comparison at the call site is
    deliberate — the finalize job needs to report *which* asset drifted.
    """
    try:
        document = json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError) as error:
        raise ReceiptError(f"stage attestation is unreadable: {error}")
    if not isinstance(document, Mapping):
        raise ReceiptError("stage attestation is not an object")
    if document.get("format") != STAGE_ATTESTATION_FORMAT:
        raise ReceiptError("stage attestation has an unrecognised format")
    if document.get("schema_version") != STAGE_ATTESTATION_SCHEMA_VERSION:
        raise ReceiptError(
            f"stage attestation has schema_version "
            f"{document.get('schema_version')!r}, this pipeline requires "
            f"{STAGE_ATTESTATION_SCHEMA_VERSION}"
        )
    if str(document.get("version") or "") != str(version):
        raise ReceiptError(
            f"stage attestation is for version {document.get('version')!r}, not "
            f"{version!r}"
        )
    rows = document.get("assets")
    if not isinstance(rows, list) or not rows:
        raise ReceiptError("stage attestation lists no assets")
    digests: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ReceiptError("stage attestation has a malformed asset row")
        name = str(row.get("name") or "")
        digest = str(row.get("sha256") or "")
        if not name or not digest:
            raise ReceiptError("stage attestation has an asset with no name or digest")
        if name in digests:
            raise ReceiptError(f"stage attestation lists {name} twice")
        digests[name] = digest
    return digests


def main(argv: Sequence[str] | None = None) -> int:
    """Write a build receipt for the artifacts a build job just produced.

        python scripts/release_receipts.py --kind dist --source-sha "$SHA" dist/*.whl

    Called by the wheel/sdist and macOS jobs. `--source-sha` is the workflow's
    frozen SHA rather than `git rev-parse HEAD`, because the point of the receipt
    is to let staging compare what a job *thought* it was building against the one
    commit the release was frozen at.
    """
    import argparse
    import sys

    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", required=True, help="dist | macos | linux | windows")
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("artifacts", nargs="+", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        document = build_build_receipt(args.kind, args.source_sha, args.artifacts)
    except ReceiptError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 2
    out_dir = args.output_dir or Path(args.artifacts[0]).resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / build_receipt_name(args.kind)
    target.write_text(json.dumps(document, indent=2, sort_keys=True), "utf-8")
    print(
        f"{target.name}: {len(document['artifacts'])} artifact(s) bound to "
        f"{document['source_sha'][:12]} built on "
        f"{document['builder']['os']}/{document['builder']['arch']} "
        f"python {document['builder']['interpreter_version']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
