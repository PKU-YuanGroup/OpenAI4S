"""The release pipeline, tested without cutting a release.

That is the whole reason it is a script. A step embedded in a workflow that
triggers `on: release` can only be exercised by the event it is supposed to
protect, so the first time anyone learns it is wrong is on a real version.

What these pin, in order of how much they would cost to get wrong:

  * publishing is last, and nothing irreversible runs before every check;
  * a failure stops the pipeline — the steps after it must not run;
  * release mode refuses to publish an unsigned disk image;
  * notarization is never reported as verified.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.release_pipeline import (  # noqa: E402
    EXTERNAL,
    STEPS,
    Pipeline,
    ReleaseError,
    build_provenance,
    build_sbom,
    sha256_file,
)


def _completed(returncode=0, stdout=b"", stderr=b""):
    return subprocess.CompletedProcess(["fake"], returncode, stdout, stderr)


@pytest.fixture
def assets(tmp_path):
    directory = tmp_path / "dist"
    directory.mkdir()
    (directory / "openai4s-0.2.0-py3-none-any.whl").write_bytes(b"wheel-bytes")
    (directory / "openai4s-0.2.0.tar.gz").write_bytes(b"sdist-bytes")
    return directory


def _pipeline(assets, **kw):
    kw.setdefault("runner", lambda argv, cwd=None: _completed())
    kw.setdefault("gh", lambda argv: _completed(0, b'{"assets": []}'))
    return Pipeline("0.2.0", assets_dir=assets, **kw)


# --------------------------------------------------------------------------
# the order is the safety property
# --------------------------------------------------------------------------


def test_publishing_is_the_last_step():
    """A package index does not forget a version, so everything that could
    stop a release has to run before the one step that cannot be undone."""
    assert STEPS[-1] == "publish"


def test_nothing_external_happens_before_everything_local_has_passed():
    first_external = min(STEPS.index(name) for name in EXTERNAL)
    for name in ("build", "test", "assets", "sbom", "provenance", "verify"):
        assert STEPS.index(name) < first_external, f"{name} runs after going public"


def test_assets_are_verified_before_they_are_staged_and_again_after_upload():
    assert STEPS.index("verify") < STEPS.index("draft")
    assert STEPS.index("upload") < STEPS.index("reverify") < STEPS.index("publish")


def test_a_dry_run_touches_nothing_and_still_reports_every_step(assets):
    report = _pipeline(assets, dry_run=True).run()
    assert report["ok"] and report["published"] is False
    assert [step["step"] for step in report["steps"]] == list(STEPS)
    assert not (assets / "sbom.cdx.json").exists()


# --------------------------------------------------------------------------
# a failure stops it
# --------------------------------------------------------------------------


def test_a_failing_step_stops_the_pipeline_there(assets):
    def refuse(argv, cwd=None):
        if "build" in argv:
            return _completed(1, b"", b"no build backend")
        return _completed()

    pipeline = _pipeline(assets, runner=refuse)
    report = pipeline.run()

    assert report["ok"] is False
    assert report["stopped_at"] == "build"
    assert [step["step"] for step in report["steps"]] == ["build"]
    assert pipeline.performed == [], "no step may be recorded as done after a stop"


def test_a_release_is_never_published_when_a_check_failed(assets):
    def refuse(argv, cwd=None):
        if "pytest" in " ".join(str(a) for a in argv):
            return _completed(1)
        return _completed()

    report = _pipeline(assets, mode="release", runner=refuse).run()
    assert report["published"] is False
    assert "publish" not in [step["step"] for step in report["steps"]]


def test_missing_assets_stop_the_run_before_anything_is_staged(tmp_path):
    empty = tmp_path / "dist"
    empty.mkdir()
    report = _pipeline(empty).run()
    assert report["stopped_at"] == "assets"


# --------------------------------------------------------------------------
# signing: fail closed, and never overclaim
# --------------------------------------------------------------------------


def test_release_mode_refuses_an_unsigned_disk_image(assets, monkeypatch):
    """ "The certificate was not configured" is not a reason to publish
    anyway — that is exactly the outcome signing exists to prevent."""
    (assets / "OpenAI4S-0.2.0-arm64.dmg").write_bytes(b"dmg-bytes")
    monkeypatch.delenv("OPENAI4S_MACOS_SIGNING_IDENTITY", raising=False)

    report = _pipeline(assets, mode="release").run()
    assert report["ok"] is False
    assert report["stopped_at"] == "verify"
    assert "refusing to publish unsigned" in report["steps"][-1]["detail"]


def test_local_mode_builds_an_unsigned_image_without_pretending(assets, monkeypatch):
    """A laptop has no Developer ID, and the pipeline still has to be
    exercisable there — it just may not claim what it did not do."""
    (assets / "OpenAI4S-0.2.0-arm64.dmg").write_bytes(b"dmg-bytes")
    monkeypatch.delenv("OPENAI4S_MACOS_SIGNING_IDENTITY", raising=False)

    report = _pipeline(assets, mode="local").run()
    verify = next(s for s in report["steps"] if s["step"] == "verify")
    assert report["ok"] is True
    assert verify["facts"]["signing_identity_configured"] is False


def test_notarization_is_never_reported_as_verified(assets, monkeypatch):
    monkeypatch.setenv("OPENAI4S_MACOS_SIGNING_IDENTITY", "Developer ID: Example")
    report = _pipeline(assets, mode="release").run()
    verify = next(s for s in report["steps"] if s["step"] == "verify")
    assert verify["facts"]["notarized"] is None
    assert (
        "requires an Apple Developer identity" in verify["facts"]["notarization_note"]
    )


# --------------------------------------------------------------------------
# the documents
# --------------------------------------------------------------------------


def test_the_sbom_names_the_assets_and_their_digests(assets):
    pipeline = _pipeline(assets)
    pipeline.run()
    document = json.loads((assets / "sbom.cdx.json").read_text())

    assert document["bomFormat"] == "CycloneDX"
    referenced = {
        ref["url"]: ref["hashes"][0]["content"]
        for ref in document["externalReferences"]
    }
    wheel = assets / "openai4s-0.2.0-py3-none-any.whl"
    assert referenced[wheel.name] == sha256_file(wheel)


def test_the_provenance_binds_the_digests_and_claims_no_author(assets):
    pipeline = _pipeline(assets)
    pipeline.run()
    document = json.loads((assets / "provenance.intoto.json").read_text())

    subjects = {item["name"]: item["digest"]["sha256"] for item in document["subject"]}
    wheel = assets / "openai4s-0.2.0-py3-none-any.whl"
    assert subjects[wheel.name] == sha256_file(wheel)
    # It is unsigned, so it must say so rather than reading as an attestation
    # of who built it.
    assert document["predicate"]["unsigned"] is True
    assert "does not establish who produced" in document["predicate"]["note"]


def test_an_sbom_with_nothing_to_list_is_still_honest():
    document = build_sbom([], version="0.2.0", packages=[])
    assert document["components"] == []
    assert document["metadata"]["component"]["version"] == "0.2.0"


def test_provenance_subjects_are_sorted_so_two_runs_agree(tmp_path):
    first = tmp_path / "b.whl"
    second = tmp_path / "a.whl"
    first.write_bytes(b"1")
    second.write_bytes(b"2")
    document = build_provenance(
        [first, second], version="0.2.0", source={"uri": "x", "digest": {}}
    )
    assert [item["name"] for item in document["subject"]] == ["a.whl", "b.whl"]


# --------------------------------------------------------------------------
# staging
# --------------------------------------------------------------------------


def test_an_upload_that_lost_an_asset_stops_before_publish(assets, monkeypatch):
    """A local checksum cannot see a transfer that dropped bytes, which is why
    the read-back exists at all."""
    monkeypatch.setenv("OPENAI4S_MACOS_SIGNING_IDENTITY", "Developer ID: Example")

    def gh(argv):
        if argv[1] == "view":
            return _completed(0, json.dumps({"assets": []}).encode())
        return _completed()

    report = _pipeline(assets, mode="release", gh=gh).run()
    assert report["ok"] is False
    assert report["stopped_at"] == "reverify"
    assert report["published"] is False


def test_a_complete_release_publishes_last(assets, monkeypatch):
    monkeypatch.setenv("OPENAI4S_MACOS_SIGNING_IDENTITY", "Developer ID: Example")
    calls: list[list[str]] = []

    def gh(argv):
        calls.append(list(argv))
        if argv[1] == "view":
            names = [{"name": path.name} for path in Path(assets).glob("*")]
            return _completed(0, json.dumps({"assets": names}).encode())
        return _completed()

    report = _pipeline(assets, mode="release", gh=gh).run()

    assert report["ok"] and report["published"] is True
    verbs = [call[1] for call in calls]
    assert verbs.index("create") < verbs.index("upload") < verbs.index("edit")
    assert calls[-1][-1] == "--draft=false", "publishing is the final act"
