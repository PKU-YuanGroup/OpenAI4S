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
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

BUILD_RECEIPT_FORMAT = "openai4s-build-receipt"
BUILD_RECEIPT_SCHEMA_VERSION = 2

STAGE_ATTESTATION_FORMAT = "openai4s-stage-attestation"
STAGE_ATTESTATION_SCHEMA_VERSION = 2
STAGE_ATTESTATION_NAME = "stage-attestation.json"

MACOS_ASSET_VALUES = frozenset({"omit", "notarized"})

#: Suffix for a per-artifact-group build receipt: `build-receipt-<kind>.json`.
BUILD_RECEIPT_PREFIX = "build-receipt-"


class ReceiptError(Exception):
    """A receipt or attestation is not proof. Do not release."""


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no", ""}:
            return False
    if value in (0, 1):
        return bool(value)
    raise ReceiptError(f"workflow input is not a boolean: {value!r}")


def normalize_workflow_inputs(raw: Any) -> dict[str, Any]:
    """Closed-set dispatch inputs. Unknown keys are ignored, missing ones default."""
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ReceiptError("workflow inputs must be an object")
    macos_asset = str(raw.get("macos_asset") or "omit").strip() or "omit"
    if macos_asset not in MACOS_ASSET_VALUES:
        raise ReceiptError(
            f"macos_asset must be omit or notarized, got {macos_asset!r}"
        )
    return {
        "tag": str(raw.get("tag") or ""),
        "publish": _as_bool(raw.get("publish", False)),
        "pypi_only": _as_bool(raw.get("pypi_only", False)),
        "macos_asset": macos_asset,
    }


def normalize_notary(raw: Any, *, required: bool) -> dict[str, Any] | None:
    if raw is None:
        if required:
            raise ReceiptError("macos build receipt records no notary result")
        return None
    if not isinstance(raw, Mapping):
        raise ReceiptError("notary result is not an object")
    stapler = raw.get("stapler_returncode")
    spctl = raw.get("spctl_returncode")
    try:
        stapler_code = None if stapler is None else int(stapler)
        spctl_code = None if spctl is None else int(spctl)
    except (TypeError, ValueError) as error:
        raise ReceiptError(f"notary return codes are not integers: {error}") from error
    return {
        "requested": bool(raw.get("requested")),
        "submitted": bool(raw.get("submitted")),
        "stapled": bool(raw.get("stapled")),
        "stapler_returncode": stapler_code,
        "spctl_returncode": spctl_code,
        "post_staple_sha256": str(raw.get("post_staple_sha256") or ""),
    }


def notary_succeeded(notary: Mapping[str, Any] | None) -> bool:
    if not isinstance(notary, Mapping):
        return False
    return bool(
        notary.get("requested")
        and notary.get("submitted")
        and notary.get("stapled")
        and notary.get("stapler_returncode") == 0
        and notary.get("spctl_returncode") == 0
        and str(notary.get("post_staple_sha256") or "")
    )


def normalize_check_runs(raw: Any) -> list[dict[str, str]]:
    if raw in (None, ()):
        return []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ReceiptError("check_runs must be a list")
    rows: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ReceiptError("a check_runs row is not an object")
        rows.append(
            {
                "name": str(item.get("name") or ""),
                "check_run_id": str(item.get("check_run_id") or ""),
                "head_sha": str(item.get("head_sha") or ""),
            }
        )
    return rows


def workflow_run_id_from(explicit: str = "") -> str:
    return str(explicit or os.environ.get("GITHUB_RUN_ID") or "").strip()


def verify_rehearsal(
    *,
    workflow_inputs: Mapping[str, Any],
    candidate_sha: str,
    expected_sha: str,
    workflow_run_id: str,
    dmg_count: int,
    notary: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """A non-publish run's receipt. publish=true cannot masquerade as this."""
    inputs = normalize_workflow_inputs(workflow_inputs)
    if inputs["publish"]:
        raise ReceiptError("a publish=true run is not a rehearsal")
    if inputs["pypi_only"]:
        raise ReceiptError("pypi_only is not a rehearsal")
    if not expected_sha or candidate_sha != expected_sha:
        raise ReceiptError(
            f"rehearsal candidate {candidate_sha[:12] or '<none>'} is not "
            f"{expected_sha[:12] or '<none>'}"
        )
    if not workflow_run_id:
        raise ReceiptError("rehearsal receipt records no workflow run id")
    if inputs["macos_asset"] == "omit" and dmg_count:
        raise ReceiptError("macos_asset=omit cannot carry a DMG")
    if dmg_count and not notary_succeeded(notary):
        raise ReceiptError("no notarization success receipt; DMG count must be zero")
    return inputs


def _notary_from_codesign_sidecars(artifacts: Sequence[Path]) -> dict[str, Any] | None:
    """Read stapler/spctl evidence written beside a DMG by the macOS job."""
    for artifact in artifacts:
        sidecar = Path(str(artifact) + ".codesign.json")
        if not sidecar.is_file():
            continue
        try:
            payload = json.loads(sidecar.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, Mapping):
            continue
        notarized = bool(payload.get("notarized"))
        return {
            "requested": True,
            "submitted": notarized,
            "stapled": notarized,
            "stapler_returncode": payload.get("stapler_returncode"),
            "spctl_returncode": payload.get("spctl_returncode"),
            "post_staple_sha256": str(payload.get("post_staple_sha256") or ""),
        }
    return None


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
    *,
    workflow_run_id: str = "",
    workflow_inputs: Mapping[str, Any] | None = None,
    check_runs: Sequence[Mapping[str, Any]] = (),
    notary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """What one build job produced, and from what.

    `kind` names the job (`dist`, `macos`) so a release carrying several
    receipts can say which is missing rather than "a receipt is missing".
    For a notarized DMG this hashes the *post-staple* bytes: the macOS job
    staples first, then calls this, so the digest staging verifies is the
    digest a user downloads.

    Schema 2 also binds the candidate commit, the workflow run, the dispatch
    inputs, optional check-run ids, and — for a macOS image — the notary
    and staple result. Old schema-1 receipts remain readable JSON but cannot
    satisfy this pipeline.
    """
    from scripts import release_gates

    if not source_sha:
        raise ReceiptError(
            "a build receipt with no source SHA binds nothing; the workflow must "
            "pass the frozen SHA"
        )
    document: dict[str, Any] = {
        "format": BUILD_RECEIPT_FORMAT,
        "schema_version": BUILD_RECEIPT_SCHEMA_VERSION,
        "kind": str(kind),
        "source_sha": str(source_sha),
        "candidate_sha": str(source_sha),
        "workflow_run_id": workflow_run_id_from(workflow_run_id),
        "workflow_inputs": normalize_workflow_inputs(workflow_inputs),
        "check_runs": normalize_check_runs(check_runs),
        "builder": release_gates.builder_facts(),
        "artifacts": _artifact_rows(artifacts),
    }
    if notary is None and str(kind) == "macos":
        notary = _notary_from_codesign_sidecars(artifacts)
    if notary is not None or str(kind) == "macos":
        recorded = normalize_notary(notary, required=False)
        if recorded is not None:
            document["notary"] = recorded
    return document


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
        candidate = str(document.get("candidate_sha") or recorded)
        if candidate != recorded:
            raise ReceiptError(
                f"build receipt {path.name} candidate_sha {candidate[:12]} "
                f"disagrees with source_sha {recorded[:12]}"
            )
        if not str(document.get("workflow_run_id") or ""):
            raise ReceiptError(f"build receipt {path.name} records no workflow run id")
        try:
            normalize_workflow_inputs(document.get("workflow_inputs"))
        except ReceiptError as error:
            raise ReceiptError(f"build receipt {path.name} {error}") from error
        try:
            normalize_check_runs(document.get("check_runs") or [])
        except ReceiptError as error:
            raise ReceiptError(f"build receipt {path.name} {error}") from error
        if document.get("notary") is not None:
            try:
                normalize_notary(document.get("notary"), required=False)
            except ReceiptError as error:
                raise ReceiptError(f"build receipt {path.name} {error}") from error
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
    workflow_run_id: str = "",
    workflow_inputs: Mapping[str, Any] | None = None,
    check_runs: Sequence[Mapping[str, Any]] = (),
    linux_boundary: Mapping[str, Any] | None = None,
    notary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The exact asset set the staging job verified, recorded outside the draft."""
    from scripts import release_gates

    if not source_sha:
        raise ReceiptError("a stage attestation with no source SHA binds nothing")
    rows = _artifact_rows(assets)
    if not rows:
        raise ReceiptError("a stage attestation with no assets binds nothing")
    document: dict[str, Any] = {
        "format": STAGE_ATTESTATION_FORMAT,
        "schema_version": STAGE_ATTESTATION_SCHEMA_VERSION,
        "version": str(version),
        "source_sha": str(source_sha),
        "candidate_sha": str(source_sha),
        "workflow_run_id": workflow_run_id_from(workflow_run_id),
        "workflow_inputs": normalize_workflow_inputs(workflow_inputs),
        "check_runs": normalize_check_runs(check_runs),
        "attested_by": release_gates.builder_facts(),
        "assets": rows,
    }
    if linux_boundary is not None:
        try:
            document["linux_boundary"] = release_gates.verify_linux_boundary(
                linux_boundary
            )
        except release_gates.GateManifestError as error:
            raise ReceiptError(str(error)) from error
    if notary is not None:
        document["notary"] = normalize_notary(notary, required=False)
    return document


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
    if not str(document.get("workflow_run_id") or document.get("source_sha") or ""):
        raise ReceiptError("stage attestation records no workflow run id or candidate")
    if document.get("workflow_inputs") is not None:
        try:
            normalize_workflow_inputs(document.get("workflow_inputs"))
        except ReceiptError as error:
            raise ReceiptError(f"stage attestation {error}") from error
    if document.get("linux_boundary") is not None:
        from scripts import release_gates

        try:
            release_gates.verify_linux_boundary(document.get("linux_boundary"))
        except release_gates.GateManifestError as error:
            raise ReceiptError(f"stage attestation {error}") from error
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
    parser.add_argument("--workflow-run-id", default="")
    parser.add_argument(
        "--workflow-inputs-json",
        default="",
        help="dispatch inputs as JSON: tag, publish, pypi_only, macos_asset",
    )
    parser.add_argument(
        "--notary-json",
        default="",
        help="notary/staple result as JSON (macos kind)",
    )
    parser.add_argument(
        "--check-runs-json",
        default="",
        help="check-run identifiers as a JSON list",
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("artifacts", nargs="+", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)

    inputs = None
    if args.workflow_inputs_json:
        try:
            inputs = json.loads(args.workflow_inputs_json)
        except ValueError as error:
            print(
                f"::error::workflow-inputs-json is not JSON: {error}", file=sys.stderr
            )
            return 2
    elif os.environ.get("OPENAI4S_RELEASE_INPUTS_JSON"):
        try:
            inputs = json.loads(os.environ["OPENAI4S_RELEASE_INPUTS_JSON"])
        except ValueError as error:
            print(
                f"::error::OPENAI4S_RELEASE_INPUTS_JSON is not JSON: {error}",
                file=sys.stderr,
            )
            return 2
    notary = None
    if args.notary_json:
        try:
            notary = json.loads(args.notary_json)
        except ValueError as error:
            print(f"::error::notary-json is not JSON: {error}", file=sys.stderr)
            return 2
    check_runs: Sequence[Mapping[str, Any]] = ()
    if args.check_runs_json:
        try:
            loaded = json.loads(args.check_runs_json)
        except ValueError as error:
            print(f"::error::check-runs-json is not JSON: {error}", file=sys.stderr)
            return 2
        if not isinstance(loaded, list):
            print("::error::check-runs-json must be a list", file=sys.stderr)
            return 2
        check_runs = loaded

    try:
        document = build_build_receipt(
            args.kind,
            args.source_sha,
            args.artifacts,
            workflow_run_id=args.workflow_run_id,
            workflow_inputs=inputs,
            check_runs=check_runs,
            notary=notary,
        )
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
