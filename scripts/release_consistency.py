"""Cross-check release documents and desktop payloads against the actual bytes.

A checksum manifest can be refreshed after replacing one draft asset. That
does not make its old build receipt, provenance, or sealed evidence describe
the replacement. Both staging and finalization use this read-only boundary;
it never repairs or regenerates evidence for the caller.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any, BinaryIO, Mapping, Sequence

from openai4s.evidence import verify_package
from scripts import release_gates
from scripts.release_gates import (
    RECEIPT_NAME,
    GateManifestError,
    verify_receipt_document,
)
from scripts.release_receipts import (
    BUILD_RECEIPT_PREFIX,
    ReceiptError,
    verify_build_receipts,
)

#: The names the pipeline writes and this module reads back. One definition,
#: imported by `release_pipeline`: the writer and the collector disagreeing on
#: the SBOM's name is how it was once built on every release and carried on none.
SBOM_NAME = "sbom.cdx.json"
PROVENANCE_NAME = "provenance.intoto.json"


def evidence_bundle_name(version: str) -> str:
    return f"openai4s-{version}-evidence.zip"


def stopped_evidence_name(version: str) -> str:
    """The best-effort record of a run that stopped. Never a release asset."""
    return f"openai4s-{version}-evidence-stopped.zip"


def _digest(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _document(payload: bytes, label: str) -> dict[str, Any]:
    try:
        document = json.loads(payload)
    except (ValueError, UnicodeError) as error:
        raise ReceiptError(f"{label} is not valid JSON: {error}") from error
    if not isinstance(document, dict):
        raise ReceiptError(f"{label} must be a JSON object")
    return document


def _match(actual: Any, expected: Mapping[str, str], label: str) -> None:
    if not isinstance(actual, dict):
        raise ReceiptError(f"{label} has no artifact digest map")
    missing = sorted(set(expected) - actual.keys())
    extra = sorted(actual.keys() - set(expected))
    changed = sorted(
        name
        for name in expected.keys() & actual.keys()
        if actual[name] != expected[name]
    )
    if missing or extra or changed:
        raise ReceiptError(
            f"{label} disagrees with the release assets: missing {missing}, "
            f"unexpected {extra}, changed {changed}; restore the verified assets "
            "or supply evidence from their actual trusted build"
        )


def _rows(rows: Any, *, label: str, sbom: bool = False) -> dict[str, str]:
    if not isinstance(rows, list):
        raise ReceiptError(f"{label} has no distribution list")
    digests: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ReceiptError(f"{label} has a malformed distribution row")
        if sbom and row.get("type") != "distribution":
            continue
        name = row.get("url" if sbom else "name")
        if not isinstance(name, str) or not name or name in digests:
            raise ReceiptError(f"{label} has a missing or duplicate distribution name")
        if sbom:
            hashes = row.get("hashes")
            if not isinstance(hashes, list):
                raise ReceiptError(f"{label} has no hashes for {name}")
            values = [
                item.get("content")
                for item in hashes
                if isinstance(item, dict) and item.get("alg") == "SHA-256"
            ]
            digest = values[0] if len(values) == 1 else None
        else:
            hashes = row.get("digest")
            digest = hashes.get("sha256") if isinstance(hashes, dict) else None
        if not isinstance(digest, str):
            raise ReceiptError(f"{label} has no unique SHA-256 for {name}")
        digests[name] = digest
    return digests


def _windows_name(member: str) -> str:
    """The path Windows gives a zip member once the package is extracted.

    Python keeps a member's name as written. Windows does not: `\\` separates
    like `/`, `.` and `..` components collapse, a trailing dot or space is
    stripped from each component, and names compare without case. Two members
    that differ only in those ways are one file to the launcher.
    """
    parts: list[str] = []
    for raw in member.replace("\\", "/").split("/"):
        if raw == "..":
            if parts:
                parts.pop()
            continue
        part = raw.rstrip(". ")
        if part:
            parts.append(part.casefold())
    return "/".join(parts)


def _windows_payloads(
    files: Mapping[str, Path], digests: Mapping[str, str], version: str
) -> None:
    for name, path in files.items():
        match = re.fullmatch(
            rf"OpenAI4S-{re.escape(version)}-windows-(x86_64|arm64)\.zip", name
        )
        if match is None:
            continue
        arch = "aarch64" if match[1] == "arm64" else "x86_64"
        linux = f"OpenAI4S-{version}-linux-{arch}.tar.gz"
        if linux not in files:
            raise ReceiptError(f"{name} payload has no matching release asset {linux}")
        payload_dir = f"{path.stem}/payload/"
        expected_member = payload_dir + linux
        sidecar = expected_member + ".sha256"
        with zipfile.ZipFile(path) as archive:
            # The launcher installs the first `payload\*.tar.gz` Windows hands it
            # and trusts the sidecar beside *that* file, so "exactly one payload"
            # has to be read the way Windows reads the extracted tree, not the
            # way `endswith(".tar.gz")` reads the zip: an `OpenAI4S-0.0.TAR.GZ`,
            # a `payload\\0.tar.gz` or a `./payload/0.tar.gz` riding along is the
            # one that sorts first and gets installed. Two rules: nothing under
            # the payload directory but the payload and its sidecar, spelled
            # exactly; and no other tarball anywhere in the package.
            payload_root = _windows_name(payload_dir) + "/"
            spelled = {
                _windows_name(expected_member): expected_member,
                _windows_name(sidecar): sidecar,
            }
            carried = [info for info in archive.infolist() if not info.is_dir()]
            stray = []
            for info in carried:
                where = _windows_name(info.filename)
                if where in spelled:
                    # The payload or its sidecar under another spelling lands
                    # on the same file and replaces the verified one.
                    if info.filename != spelled[where]:
                        stray.append(info.filename)
                elif where.startswith(payload_root) or where.endswith(".tar.gz"):
                    stray.append(info.filename)
            members = [info for info in carried if info.filename == expected_member]
            if stray or len(members) != 1:
                raise ReceiptError(
                    f"{name} must contain exactly the payload {linux}"
                    + (f"; it also carries {sorted(stray)}" if stray else "")
                )
            member = members[0]
            # Check before streaming: a forged zip cannot make verification
            # decompress more payload bytes than the independently held asset.
            if member.file_size != files[linux].stat().st_size:
                raise ReceiptError(f"{name} payload size differs from {linux}")
            with archive.open(member) as stream:
                actual = _digest(stream)
            if actual != digests[linux]:
                raise ReceiptError(
                    f"{name} payload does not match release asset {linux}"
                )
            recorded = [info for info in carried if info.filename == sidecar]
            if recorded:
                # The digest the launcher will actually check the install against.
                if len(recorded) != 1 or recorded[0].file_size > 4096:
                    raise ReceiptError(f"{name} carries a malformed payload checksum")
                # Compared as written: bootstrap.sh takes this token verbatim
                # and accepts lowercase hex only, so folding case here would
                # pass a package that then refuses every install.
                tokens = archive.read(recorded[0]).decode("utf-8", "replace").split()
                if not tokens or tokens[0] != digests[linux]:
                    raise ReceiptError(
                        f"{name} payload checksum does not name release asset {linux}"
                    )


def verify_release_consistency(
    assets: Sequence[Path],
    *,
    version: str,
    required_kinds: Sequence[str],
    expected_sha: str = "",
    workflow_run_id: str = "",
    digests: Mapping[str, str] | None = None,
) -> None:
    """Require the complete staged evidence chain, including manual recovery.

    The evidence is a pre-upload snapshot: its report covers distributions,
    SBOM and provenance, but cannot contain its own hash or SHA256SUMS. Build
    receipts are carried inside that archive, not as public sidecars.

    ``expected_sha`` and ``workflow_run_id`` are the two anchors staging holds
    its receipts to. The source SHA this chain is checked against is otherwise
    read out of the archive being checked, so a caller that was told either one
    passes it here rather than having the flag accepted and then ignored.
    ``digests`` are SHA-256s the caller has *just* taken of these same files.
    """
    try:
        _verify(
            assets,
            version=version,
            required_kinds=required_kinds,
            expected_sha=expected_sha,
            workflow_run_id=workflow_run_id,
            known=digests or {},
        )
    except ReceiptError:
        raise
    except Exception as error:  # noqa: BLE001
        # Every refusal has to reach the caller as one. A tuple of expected
        # types let a corrupt deflate stream (`zlib.error`) through as a raw
        # traceback: no report row, no sealed record of the stopped run.
        raise ReceiptError(
            f"release consistency verification failed: "
            f"{type(error).__name__}: {error}"
        ) from error


def _verify(
    assets: Sequence[Path],
    *,
    version: str,
    required_kinds: Sequence[str],
    expected_sha: str,
    workflow_run_id: str,
    known: Mapping[str, str],
) -> None:
    files = {path.name: path for path in assets if path.name != "SHA256SUMS"}
    evidence_name = evidence_bundle_name(version)
    required = {SBOM_NAME, PROVENANCE_NAME, evidence_name}
    if not required <= files.keys():
        raise ReceiptError(
            f"release is missing evidence assets: {sorted(required - files.keys())}"
        )
    digests = {}
    for name, path in files.items():
        if name in known:
            digests[name] = known[name]
            continue
        with path.open("rb") as stream:
            digests[name] = _digest(stream)
    distributions = {
        name: digest for name, digest in digests.items() if name not in required
    }
    if not distributions:
        raise ReceiptError("release evidence names no distributions")

    provenance = _document(files[PROVENANCE_NAME].read_bytes(), PROVENANCE_NAME)
    definition = provenance["predicate"]["buildDefinition"]
    if definition["externalParameters"]["version"] != version:
        raise ReceiptError("provenance names another release version")
    _match(
        _rows(provenance.get("subject"), label=PROVENANCE_NAME),
        distributions,
        PROVENANCE_NAME,
    )
    sbom = _document(files[SBOM_NAME].read_bytes(), SBOM_NAME)
    if sbom["metadata"]["component"]["version"] != version:
        raise ReceiptError("SBOM names another release version")
    _match(
        _rows(sbom.get("externalReferences"), label=SBOM_NAME, sbom=True),
        distributions,
        SBOM_NAME,
    )

    verdict = verify_package(files[evidence_name])
    if not verdict.get("ok") or verdict.get("format") != "openai4s-release-evidence":
        raise ReceiptError(
            f"{evidence_name} failed verification: {verdict.get('problems')}"
        )
    with zipfile.ZipFile(files[evidence_name]) as archive:
        report = _document(archive.read("release-report.json"), "release-report.json")
        if report.get("version") != version:
            raise ReceiptError("sealed release report names another version")
        source_sha = report.get("source_sha")
        if not isinstance(source_sha, str) or not re.fullmatch(
            r"[0-9a-f]{40}", source_sha
        ):
            raise ReceiptError("sealed release report has no frozen source SHA")
        if expected_sha and source_sha != expected_sha:
            raise ReceiptError(
                f"sealed release report is for {source_sha[:12]}, but this release "
                f"was frozen at {expected_sha[:12]}"
            )
        sources = definition["resolvedDependencies"]
        if (
            not isinstance(sources, list)
            or len(sources) != 1
            or sources[0]["digest"].get("sha1") != source_sha
        ):
            raise ReceiptError(
                "provenance source SHA differs from the sealed release report"
            )
        quality = _document(
            archive.read(f"artifacts/{RECEIPT_NAME}"), "sealed quality receipt"
        )
        try:
            verify_receipt_document(quality, expected_sha=source_sha)
        except GateManifestError as error:
            # The gate list the receipt is held to is the *running checkout's*.
            # Staging runs at the frozen SHA, so there they are the same list; a
            # hand-run `--only publish` from a later `main` is not, and it
            # arrives here after PyPI has already taken the version -- so say
            # what to do, not only what differed. Only for that case: a failed
            # gate or a wrong SHA fails identically from every checkout.
            elsewhere = (
                quality.get("schema_version") != release_gates.SCHEMA_VERSION
                or str(quality.get("manifest_digest") or "")
                != release_gates.manifest_digest()
            )
            raise ReceiptError(
                f"the sealed quality receipt did not verify: {error}"
                + (
                    f". The gate manifest it is held to is read from the checkout "
                    f"running this script; a hand-run finalize has to run from a "
                    f"checkout of the release commit {source_sha[:12]} (the "
                    f"v{version} tag), because a later revision's gate list will "
                    f"not match a receipt sealed at the tag"
                    if elsewhere
                    else ""
                )
            ) from error
        _match(
            report.get("artifacts"),
            {name: value for name, value in digests.items() if name != evidence_name},
            "sealed release report",
        )
        for name in (SBOM_NAME, PROVENANCE_NAME):
            if (
                hashlib.sha256(archive.read(f"artifacts/{name}")).hexdigest()
                != digests[name]
            ):
                raise ReceiptError(f"sealed {name} differs from the published asset")
        with tempfile.TemporaryDirectory(
            prefix="openai4s-evidence-receipts-"
        ) as scratch:
            receipts = []
            coverage: dict[str, str] = {}
            # The same shape `step_evidence` globs when it seals them. A narrower
            # pattern here would skip a receipt the sealer carried and then
            # report its distributions as unreceipted.
            sealed_receipt = re.compile(
                rf"artifacts/{re.escape(BUILD_RECEIPT_PREFIX)}[^/]+\.json"
            )
            for member in archive.namelist():
                if not sealed_receipt.fullmatch(member):
                    continue
                payload = archive.read(member)
                document = _document(payload, member)
                rows = document.get("artifacts")
                if not isinstance(rows, list):
                    raise ReceiptError(f"{member} lists no artifacts")
                for row in rows:
                    if not isinstance(row, dict):
                        raise ReceiptError(f"{member} has a malformed artifact row")
                    name = row.get("name")
                    if (
                        not isinstance(name, str)
                        or name not in distributions
                        or name in coverage
                    ):
                        raise ReceiptError(
                            f"{member} has an unexpected or duplicate artifact {name!r}"
                        )
                    coverage[name] = row.get("sha256")
                receipt = Path(scratch) / Path(member).name
                receipt.write_bytes(payload)
                receipts.append(receipt)
            _match(coverage, distributions, "sealed build receipts")
            # All supplied paths are in the staging/download directory. No
            # archive member is extracted and every receipt row was allowlisted
            # against real distribution names before the existing verifier reads.
            # `digests`: the rows were just matched against these exact hashes,
            # so re-reading every distribution to compare them again buys
            # nothing -- and a desktop bundle is hundreds of megabytes.
            sealed = verify_build_receipts(
                receipts,
                expected_sha=source_sha,
                assets_dir=files[SBOM_NAME].parent,
                required_kinds=required_kinds,
                digests=distributions,
            )
        for kind, document in sorted(sealed.items()):
            recorded_run = str(document.get("workflow_run_id") or "")
            if workflow_run_id and recorded_run != workflow_run_id:
                raise ReceiptError(
                    f"sealed build receipt {kind} is from workflow run "
                    f"{recorded_run or '<none>'}; this is run {workflow_run_id}"
                )
    _windows_payloads(files, digests, version)
