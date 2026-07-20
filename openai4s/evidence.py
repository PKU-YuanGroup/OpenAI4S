"""Standalone verification of an exported session/evidence package.

The export already hashes every entry and seals the list with a
``manifest_sha256``. What was missing is a way to *check* that without running
an import — and the two are not the same thing. Import verification tells the
person who already trusts this installation that the bytes survived the trip.
Evidence verification has to serve someone who received a zip from a colleague,
a reviewer checking a submission, or the same user six months later on a
different machine: they need to confirm the package is internally consistent
and unmodified **before** they let it near a daemon.

So this module deliberately depends on nothing but the standard library and
reads only the archive. No Store, no config, no network. It can be lifted out
and run anywhere a Python interpreter exists, which is the whole point of
calling a package "verifiable in a clean environment".

What verification establishes, stated precisely so it is not over-read:

  * every file listed in the manifest is present, and its bytes hash to the
    recorded digest;
  * the manifest's own body hashes to ``manifest_sha256``, so the list of
    hashes was not itself edited;
  * nothing is in the archive that the manifest does not account for — an
    extra file is a modification even when every listed hash still matches.

What it does NOT establish: authorship. Anyone can rewrite the payload and the
manifest together. Detecting *that* needs a signature over the manifest digest
by a key the verifier already trusts, which is a distribution decision (whose
key, distributed how) rather than a format one. Saying so here is better than
letting "verified" quietly imply more than it means.
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

MANIFEST_NAME = "manifest.json"


class EvidenceError(RuntimeError):
    """The package is missing, unreadable, or not a package at all."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(payload: Any) -> bytes:
    """Byte-for-byte what the exporter hashed.

    Key order and separators are part of the digest: a differently-formatted
    but semantically identical manifest hashes differently, so the verifier has
    to serialise exactly the way the exporter did.
    """
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def verify_package(path: Path | str) -> dict:
    """Check a package's internal consistency. Returns a structured report.

    Never raises for a *failed* verification — only for input that is not a
    package at all. A caller deciding whether to trust a file needs the list of
    problems, not one exception naming whichever happened to be found first.
    """
    path = Path(path)
    if not path.is_file():
        raise EvidenceError(f"not a file: {path}")

    problems: list[str] = []
    checked: list[str] = []

    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise EvidenceError(f"not a readable zip archive: {e}") from e

    with archive:
        names = set(archive.namelist())
        if MANIFEST_NAME not in names:
            raise EvidenceError(
                f"{path.name} has no {MANIFEST_NAME}; it is not an OpenAI4S package"
            )
        try:
            manifest = json.loads(archive.read(MANIFEST_NAME))
        except (ValueError, KeyError) as e:
            raise EvidenceError(f"{MANIFEST_NAME} is not valid JSON: {e}") from e
        if not isinstance(manifest, dict):
            raise EvidenceError(f"{MANIFEST_NAME} is not a JSON object")

        # 1. The manifest must vouch for itself before its contents mean
        #    anything: without this an editor could rewrite a payload and its
        #    recorded hash together and every per-file check would still pass.
        recorded = manifest.get("manifest_sha256")
        body = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
        actual = _sha256(_canonical_json(body))
        if not recorded:
            problems.append("manifest.json carries no manifest_sha256")
        elif recorded != actual:
            problems.append(
                f"manifest_sha256 mismatch: recorded {recorded[:16]}…, "
                f"computed {actual[:16]}… — the manifest itself was modified"
            )

        # 2. Every listed file present and unmodified.
        listed: set[str] = set()
        for entry in manifest.get("files") or []:
            name = entry.get("path")
            if not name:
                problems.append("a manifest entry has no path")
                continue
            listed.add(name)
            if name not in names:
                problems.append(f"{name}: listed in the manifest but absent")
                continue
            data = archive.read(name)
            digest = _sha256(data)
            if digest != entry.get("sha256"):
                problems.append(
                    f"{name}: content hash mismatch "
                    f"(recorded {str(entry.get('sha256'))[:16]}…, "
                    f"computed {digest[:16]}…)"
                )
            elif entry.get("size") is not None and len(data) != entry["size"]:
                problems.append(
                    f"{name}: size {len(data)} does not match the recorded "
                    f"{entry['size']}"
                )
            else:
                checked.append(name)

        # 3. Anything present but unlisted is a modification too. Checking only
        #    the listed files would pass a package with an added payload, which
        #    is exactly how a "verified" archive smuggles something.
        for name in sorted(names - listed - {MANIFEST_NAME}):
            problems.append(f"{name}: present in the archive but not in the manifest")

    return {
        "path": str(path),
        "ok": not problems,
        "format": manifest.get("format"),
        "schema_version": manifest.get("schema_version"),
        "files_verified": sorted(checked),
        "problems": problems,
        "archive_sha256": _sha256(path.read_bytes()),
        "verifies": (
            "internal consistency only — this does not establish who produced "
            "the package"
        ),
    }


__all__ = ["EvidenceError", "MANIFEST_NAME", "verify_package"]
