"""The release pipeline, tested without cutting a release.

That is the whole reason it is a script. A step embedded in a workflow that
triggers on a release event can only be exercised by the event it is supposed
to protect, so the first time anyone learns it is wrong is on a real version.

What these pin, in order of how much they would cost to get wrong:

  * the GitHub flip is the *last* cross-channel step and refuses to run until
    PyPI actually has the version — a public release with no matching package
    is the half-published state this pipeline exists to prevent;
  * a staging run does not rebuild: GitHub and PyPI must receive the same bytes;
  * a disk image is signed on evidence, never on a configured variable;
  * the read-back compares content, not filenames;
  * SHA256SUMS covers everything and is itself uploaded;
  * the SBOM describes the wheel and the image, not the machine that staged it;
  * provenance names the repository this source actually lives in;
  * a failure stops the pipeline — the steps after it must not run.
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
    STAGING_SKIPPED,
    STEPS,
    Pipeline,
    ReleaseError,
    build_provenance,
    build_sbom,
    canonical_source_uri,
    read_signature,
    sha256_file,
    wheel_components,
)

ROOT = Path(__file__).resolve().parents[1]


def _completed(returncode=0, stdout=b"", stderr=b""):
    return subprocess.CompletedProcess(["fake"], returncode, stdout, stderr)


@pytest.fixture
def assets(tmp_path):
    directory = tmp_path / "dist"
    directory.mkdir()
    (directory / "openai4s-0.2.0-py3-none-any.whl").write_bytes(b"wheel-bytes")
    (directory / "openai4s-0.2.0.tar.gz").write_bytes(b"sdist-bytes")
    return directory


def _signed_dmg(assets: Path, name: str = "OpenAI4S-0.2.0-arm64.dmg") -> Path:
    """A disk image with the receipt a macOS build job would have written."""
    dmg = assets / name
    dmg.write_bytes(b"dmg-bytes")
    dmg.with_name(dmg.name + ".codesign.json").write_text(
        json.dumps(
            {
                "authorities": [
                    "Developer ID Application: Example Inc (ABCDE12345)",
                    "Developer ID Certification Authority",
                    "Apple Root CA",
                ],
                "adhoc": False,
                "developer_id": True,
                # A real receipt records the deep verification result, and the
                # gate requires it to have succeeded.
                "verify_returncode": 0,
                # ...and the digest of the exact image it describes.
                "image_sha256": sha256_file(dmg),
            }
        ),
        encoding="utf-8",
    )
    return dmg


def _matching_pypi(assets: Path):
    """PyPI digests that agree with the local wheel/sdist bytes.

    The default for the ordering tests: PyPI is the finalize anchor, so a
    success-path run needs a matching digest for every Python distribution.
    Only wheels and sdists live on PyPI.
    """

    def digests(_project, _version):
        return {
            path.name: sha256_file(path)
            for path in assets.glob("*")
            if path.name.endswith((".whl", ".tar.gz"))
        }

    return digests


def _pipeline(assets, **kw):
    kw.setdefault("runner", lambda argv, cwd=None: _completed())
    kw.setdefault("gh", lambda argv: _completed(0, b'{"assets": [], "isDraft": true}'))
    kw.setdefault("smoke", lambda wheel: "smoke injected")
    kw.setdefault("pypi_check", lambda project, version: True)
    kw.setdefault("pypi_digests", _matching_pypi(assets))
    # The `assets` fixture provides already-built distributions, which is the
    # staging job's input. Default to that mode so `step_build` does not clear
    # them and then find nothing to collect (the mock runner does not rebuild).
    # Tests that exercise the build/test steps themselves pass from_artifacts
    # explicitly.
    kw.setdefault("from_artifacts", True)
    return Pipeline("0.2.0", assets_dir=assets, **kw)


#: Local build evidence read from disk, never uploaded as release assets — so a
#: faithful release listing must not include them.
_LOCAL_ONLY_SIDECARS = (".codesign.json", ".components.json")


def _gh_for(assets: Path, *, is_draft=True, corrupt=None, drop=None, extra=None):
    """A gh stand-in that behaves like a release the assets were uploaded to.

    The listing reflects what `step_upload` actually uploads (``self.assets``):
    the distributions and the generated sbom/provenance/SHA256SUMS, but not the
    `.codesign.json`/`.components.json` sidecars, which are local evidence read
    from disk and never pushed to the release. `extra` injects an unexpected
    asset a prior staging attempt might have left behind.
    """

    def _uploaded_names():
        names = {
            path.name
            for path in assets.glob("*")
            if path.is_file()
            and path.name != drop
            and not path.name.endswith(_LOCAL_ONLY_SIDECARS)
        }
        if extra:
            names.add(extra)
        return names

    def gh(argv):
        verb = argv[1]
        if verb == "view" and "isDraft" in argv:
            return _completed(0, json.dumps({"isDraft": is_draft}).encode())
        if verb == "view":
            listing = [
                {
                    "name": name,
                    "size": (assets / name).stat().st_size
                    if (assets / name).is_file()
                    else 0,
                }
                for name in sorted(_uploaded_names())
            ]
            return _completed(0, json.dumps({"assets": listing}).encode())
        if verb == "download":
            pattern = argv[argv.index("--pattern") + 1]
            destination = Path(argv[argv.index("--dir") + 1])
            source = assets / pattern
            payload = source.read_bytes()
            if pattern == corrupt:
                payload = payload + b"-tampered"
            (destination / pattern).write_bytes(payload)
            return _completed()
        return _completed()

    return gh


# --------------------------------------------------------------------------
# the order is the safety property
# --------------------------------------------------------------------------


def test_publishing_is_the_last_step():
    """A package index does not forget a version, so everything that could
    stop a release has to run before the one step that cannot be undone."""
    assert STEPS[-1] == "publish"


def test_nothing_external_happens_before_everything_local_has_passed():
    first_external = min(STEPS.index(name) for name in EXTERNAL)
    for name in ("build", "test", "assets", "smoke", "sbom", "provenance", "verify"):
        assert STEPS.index(name) < first_external, f"{name} runs after going public"


def test_assets_are_verified_before_they_are_staged_and_again_after_upload():
    assert STEPS.index("verify") < STEPS.index("draft")
    assert STEPS.index("upload") < STEPS.index("reverify") < STEPS.index("publish")


def test_the_checksum_manifest_is_written_after_everything_it_covers():
    for produced in ("sbom", "provenance"):
        assert STEPS.index(produced) < STEPS.index("checksums")
    assert STEPS.index("checksums") < STEPS.index("upload")


def test_a_dry_run_touches_nothing_and_still_reports_every_step(assets):
    report = _pipeline(assets, dry_run=True).run()
    assert report["ok"] and report["published"] is False
    assert [step["step"] for step in report["steps"]] == list(STEPS)
    assert not (assets / "sbom.cdx.json").exists()


# --------------------------------------------------------------------------
# staging consumes; it does not produce
# --------------------------------------------------------------------------


def test_a_staging_run_does_not_rebuild_or_retest(assets):
    """Its inputs *are* an earlier verified build. Rebuilding here would put
    different bytes on GitHub than PyPI receives for the same version."""
    ran: list[str] = []

    def runner(argv, cwd=None):
        ran.append(" ".join(str(a) for a in argv))
        return _completed()

    report = _pipeline(assets, from_artifacts=True, runner=runner).run()

    assert report["ok"], report
    joined = " ".join(ran)
    assert "-m build" not in joined, "staging re-ran the build"
    assert "-m pytest" not in joined, "staging re-ran the suite"
    for name in STAGING_SKIPPED:
        step = next(s for s in report["steps"] if s["step"] == name)
        assert step["facts"].get("from_artifacts") is True


def test_a_distribution_that_changed_after_collection_stops_the_run(assets):
    pipeline = _pipeline(assets, from_artifacts=True)

    def rewrite(wheel):
        wheel.write_bytes(b"different-bytes")
        return "smoke"

    pipeline._smoke = rewrite
    report = pipeline.run()

    assert report["ok"] is False
    assert report["stopped_at"] == "verify"
    assert "different bytes" in report["steps"][-1]["detail"]


def test_stop_after_runs_a_prefix_and_only_runs_one_step(assets):
    staged = _pipeline(assets, stop_after="reverify").run()
    assert [s["step"] for s in staged["steps"]] == list(
        STEPS[: STEPS.index("reverify") + 1]
    )
    assert staged["published"] is False

    final = _pipeline(assets, mode="release", only="publish", gh=_gh_for(assets)).run()
    assert [s["step"] for s in final["steps"]] == ["publish"]


# --------------------------------------------------------------------------
# a failure stops it
# --------------------------------------------------------------------------


def test_a_failing_step_stops_the_pipeline_there(assets):
    def refuse(argv, cwd=None):
        if "build" in argv:
            return _completed(1, b"", b"no build backend")
        return _completed()

    # from_artifacts=False so the build step actually runs and can fail.
    pipeline = _pipeline(assets, from_artifacts=False, runner=refuse)
    report = pipeline.run()

    assert report["ok"] is False
    assert report["stopped_at"] == "build"
    assert [step["step"] for step in report["steps"]] == ["build"]
    assert pipeline.performed == [], "no step may be recorded as done after a stop"


def test_the_assets_dir_is_absolute_so_subprocesses_from_root_find_it(tmp_path):
    """The staging job passes `--assets-dir assets`, a sibling of the checkout,
    while `_run` executes from ROOT. A relative path made pip and gh look under
    ROOT/assets, where the wheel is not."""
    from scripts.release_pipeline import Pipeline

    pipe = Pipeline("0.2.0", assets_dir="assets")
    assert pipe.assets_dir.is_absolute(), (
        "a relative assets dir would resolve against each subprocess's cwd, "
        "not the directory the staging job actually populated"
    )


def test_the_build_uses_a_frontend_available_in_the_locked_environment(assets):
    """`python -m build` is not a locked dependency, so the documented
    `uv run python scripts/release_pipeline.py` failed to import it before any
    release check ran."""
    seen: list = []

    def runner(argv, cwd=None):
        seen.append([str(a) for a in argv])
        return _completed()

    _pipeline(assets, from_artifacts=False, runner=runner).run()
    build_cmds = [c for c in seen if "build" in c]
    assert build_cmds, "no build was invoked"
    assert build_cmds[0][0] == "uv", (
        "the build must use the uv frontend that exists in the locked env, "
        "not `python -m build`"
    )


def test_a_stale_distribution_from_a_previous_build_is_not_published(assets):
    """`dist` is reused; collecting every wheel it holds would smoke-test,
    hash and upload a previous version's artifacts."""
    (assets / "openai4s-0.1.9-py3-none-any.whl").write_bytes(b"old-wheel")
    report = _pipeline(assets, from_artifacts=True).run()
    assert report["ok"] is False
    assert report["stopped_at"] == "assets"
    assert "another version" in report["steps"][-1]["detail"]


def test_the_build_clears_stale_artifacts_before_building(tmp_path):
    """The non-staging path clears the directory, so a leftover cannot survive
    into the collected asset set."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "openai4s-0.1.9-py3-none-any.whl").write_bytes(b"old")

    def runner(argv, cwd=None):
        # Simulate the build writing this version's artifacts.
        (dist / "openai4s-0.2.0-py3-none-any.whl").write_bytes(b"new-wheel")
        (dist / "openai4s-0.2.0.tar.gz").write_bytes(b"new-sdist")
        return _completed()

    report = _pipeline(dist, from_artifacts=False, runner=runner).run()
    names = {
        Path(a).name
        for step in report["steps"]
        if step["step"] == "assets"
        for a in step["facts"].get("assets", [])
    }
    assert "openai4s-0.1.9-py3-none-any.whl" not in names, "the stale wheel survived"
    assert "openai4s-0.2.0-py3-none-any.whl" in names


def test_a_wheel_that_does_not_survive_a_clean_install_stops_the_run(assets):
    def broken(wheel):
        raise ReleaseError("the wheel does not install in a clean environment")

    report = _pipeline(assets, smoke=broken).run()
    assert report["ok"] is False
    assert report["stopped_at"] == "smoke"


def test_a_release_is_never_published_when_a_check_failed(assets):
    def refuse(argv, cwd=None):
        if "pytest" in " ".join(str(a) for a in argv):
            return _completed(1)
        return _completed()

    report = _pipeline(
        assets, mode="release", from_artifacts=False, runner=refuse
    ).run()
    assert report["published"] is False
    assert "publish" not in [step["step"] for step in report["steps"]]


def test_missing_assets_stop_the_run_before_anything_is_staged(tmp_path):
    empty = tmp_path / "dist"
    empty.mkdir()
    report = _pipeline(empty).run()
    assert report["stopped_at"] == "assets"


# --------------------------------------------------------------------------
# signing: evidence, not configuration
# --------------------------------------------------------------------------


def test_release_mode_refuses_an_image_with_no_developer_id_signature(
    assets, monkeypatch
):
    """Setting the secret used to be enough. The build script only ad-hoc
    signs, so a configured identity made an ad-hoc image pass this gate as
    Developer-ID-signed — the exact outcome signing exists to prevent."""
    dmg = assets / "OpenAI4S-0.2.0-arm64.dmg"
    dmg.write_bytes(b"dmg-bytes")
    dmg.with_name(dmg.name + ".codesign.json").write_text(
        json.dumps({"authorities": [], "adhoc": True, "developer_id": False}),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI4S_MACOS_SIGNING_IDENTITY", "Developer ID: Example")

    report = _pipeline(assets, mode="release").run()

    assert report["ok"] is False
    assert report["stopped_at"] == "verify"
    assert "Developer ID Application" in report["steps"][-1]["detail"]


def test_a_real_developer_id_receipt_passes_the_gate(assets, monkeypatch):
    _signed_dmg(assets)
    monkeypatch.delenv("OPENAI4S_MACOS_SIGNING_IDENTITY", raising=False)

    report = _pipeline(assets, mode="release", gh=_gh_for(assets)).run()

    assert report["ok"], report
    verify = next(s for s in report["steps"] if s["step"] == "verify")
    signature = verify["facts"]["signatures"]["OpenAI4S-0.2.0-arm64.dmg"]
    assert signature["developer_id"] is True
    assert signature["source"] == "receipt"


def test_local_mode_builds_an_unsigned_image_without_pretending(assets, monkeypatch):
    """A laptop has no Developer ID, and the pipeline still has to be
    exercisable there — it just may not claim what it did not do."""
    dmg = assets / "OpenAI4S-0.2.0-arm64.dmg"
    dmg.write_bytes(b"dmg-bytes")
    dmg.with_name(dmg.name + ".codesign.json").write_text(
        json.dumps({"authorities": [], "adhoc": True, "developer_id": False}),
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI4S_MACOS_SIGNING_IDENTITY", raising=False)

    report = _pipeline(assets, mode="local").run()
    verify = next(s for s in report["steps"] if s["step"] == "verify")
    assert report["ok"] is True
    assert verify["facts"]["signatures"][dmg.name]["developer_id"] is False


def test_a_missing_receipt_is_not_read_as_a_signature(tmp_path):
    dmg = tmp_path / "x.dmg"
    dmg.write_bytes(b"not really an image")
    info = read_signature(dmg, lambda argv: _completed(1, b"", b""))
    assert info.get("developer_id") is not True


def test_notarization_is_never_reported_as_verified(assets, monkeypatch):
    _signed_dmg(assets)
    report = _pipeline(assets, mode="release", gh=_gh_for(assets)).run()
    verify = next(s for s in report["steps"] if s["step"] == "verify")
    assert verify["facts"]["notarized"] is None
    assert (
        "requires an Apple Developer identity" in verify["facts"]["notarization_note"]
    )


# --------------------------------------------------------------------------
# the documents
# --------------------------------------------------------------------------


def test_the_sbom_names_the_assets_and_their_digests(assets):
    _pipeline(assets).run()
    document = json.loads((assets / "sbom.cdx.json").read_text())

    assert document["bomFormat"] == "CycloneDX"
    referenced = {
        ref["url"]: ref["hashes"][0]["content"]
        for ref in document["externalReferences"]
    }
    wheel = assets / "openai4s-0.2.0-py3-none-any.whl"
    assert referenced[wheel.name] == sha256_file(wheel)


def test_the_sbom_components_come_from_the_wheel_and_the_image(assets):
    """Not from the machine assembling the release, which on the staging job is
    an Ubuntu runner with none of this installed."""
    import zipfile

    wheel = assets / "openai4s-0.2.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "openai4s-0.2.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: openai4s\nVersion: 0.2.0\n"
            "Requires-Dist: numpy>=1.26 ; extra == 'science'\n",
        )
    dmg = _signed_dmg(assets)
    dmg.with_name(dmg.name + ".components.json").write_text(
        json.dumps(
            {
                "packages": [{"name": "scipy", "version": "1.14.0"}],
                # Bound to the exact image, as describe_macos_image writes it.
                "image_sha256": sha256_file(dmg),
            }
        ),
        encoding="utf-8",
    )

    _pipeline(assets).run()
    document = json.loads((assets / "sbom.cdx.json").read_text())
    names = {component["name"] for component in document["components"]}

    assert "openai4s" in names, "the shipped component is missing from its own SBOM"
    assert "scipy" in names, "the image's embedded runtime is not described"
    assert "numpy" in names


def test_an_image_with_no_component_inventory_is_named_not_omitted(assets):
    _signed_dmg(assets)
    _pipeline(assets).run()
    document = json.loads((assets / "sbom.cdx.json").read_text())
    properties = document["metadata"].get("properties") or []
    assert any("components-unread" in p["name"] for p in properties)


def test_a_components_sidecar_from_a_different_image_is_not_trusted(assets):
    """Codex P2: a `.components.json` left by an earlier rebuild with the same
    filename carries no binding to the bytes it describes. Its package list must
    not enter the SBOM for a different image; a stale/mismatched digest is read
    as unread, not trusted."""
    import zipfile

    wheel = assets / "openai4s-0.2.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "openai4s-0.2.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: openai4s\nVersion: 0.2.0\n",
        )
    dmg = _signed_dmg(assets)
    dmg.with_name(dmg.name + ".components.json").write_text(
        json.dumps(
            {
                "packages": [{"name": "ghost-pkg", "version": "9.9.9"}],
                # A digest for some *other* image — the binding does not match.
                "image_sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )

    _pipeline(assets).run()
    document = json.loads((assets / "sbom.cdx.json").read_text())
    names = {component["name"] for component in document["components"]}
    assert "ghost-pkg" not in names, "a mismatched-image inventory was trusted"
    properties = document["metadata"].get("properties") or []
    assert any(
        "components-unread" in p["name"] for p in properties
    ), "the mismatched sidecar should be reported unread, not silently dropped"


def test_the_provenance_points_at_the_repository_this_source_lives_in(
    assets, monkeypatch
):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("GITHUB_SERVER_URL", raising=False)
    _pipeline(
        assets,
        runner=lambda argv, cwd=None: (
            _completed(0, b"git@github.com:PKU-YuanGroup/OpenAI4S.git\n")
            if "remote.origin.url" in " ".join(str(a) for a in argv)
            else _completed(0, b"abc123\n")
        ),
    ).run()
    document = json.loads((assets / "provenance.intoto.json").read_text())
    uri = document["predicate"]["buildDefinition"]["resolvedDependencies"][0]["uri"]

    assert (
        "openai4s/openai4s" not in uri
    ), "the attestation pointed consumers at a repository this project is not"
    assert "PKU-YuanGroup/OpenAI4S" in uri


def test_the_canonical_uri_prefers_the_running_repository(monkeypatch):
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "PKU-YuanGroup/OpenAI4S")
    assert canonical_source_uri() == "git+https://github.com/PKU-YuanGroup/OpenAI4S"


def test_the_provenance_binds_the_digests_and_claims_no_author(assets):
    _pipeline(assets).run()
    document = json.loads((assets / "provenance.intoto.json").read_text())

    subjects = {item["name"]: item["digest"]["sha256"] for item in document["subject"]}
    wheel = assets / "openai4s-0.2.0-py3-none-any.whl"
    assert subjects[wheel.name] == sha256_file(wheel)
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


def test_the_wheel_metadata_is_where_the_component_list_comes_from(tmp_path):
    import zipfile

    wheel = tmp_path / "openai4s-0.2.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "openai4s-0.2.0.dist-info/METADATA",
            "Name: openai4s\nVersion: 0.2.0\nRequires-Dist: rich>=13\n",
        )
    components = wheel_components(wheel)
    assert components[0] == {"name": "openai4s", "version": "0.2.0", "scope": "shipped"}
    assert {"name": "rich", "version": "", "scope": "declared-dependency"} in components


# --------------------------------------------------------------------------
# checksums cover everything, and ship
# --------------------------------------------------------------------------


def test_the_checksum_manifest_covers_every_asset_and_is_itself_uploaded(assets):
    uploaded: list[str] = []

    def gh(argv):
        if argv[1] == "upload":
            uploaded.extend(Path(a).name for a in argv[3:] if not a.startswith("--"))
        return _gh_for(assets)(argv)

    _signed_dmg(assets)
    pipeline = _pipeline(assets, mode="release", gh=gh)
    report = pipeline.run()
    assert report["ok"], report

    manifest = (assets / "SHA256SUMS").read_text("utf-8")
    for name in ("sbom.cdx.json", "provenance.intoto.json", "openai4s-0.2.0.tar.gz"):
        assert name in manifest, f"{name} shipped unhashed"
    assert "SHA256SUMS" in uploaded, "the manifest itself was never uploaded"


# --------------------------------------------------------------------------
# staging and the read-back
# --------------------------------------------------------------------------


def test_the_draft_must_already_exist_and_still_be_a_draft(assets):
    def gh(argv):
        if argv[1] == "view" and "isDraft" in argv:
            return _completed(0, json.dumps({"isDraft": False}).encode())
        return _completed()

    report = _pipeline(assets, mode="release", gh=gh).run()
    assert report["ok"] is False
    assert report["stopped_at"] == "draft"
    assert "already public" in report["steps"][-1]["detail"]


def test_an_upload_that_lost_an_asset_stops_before_publish(assets):
    _signed_dmg(assets)
    report = _pipeline(
        assets, mode="release", gh=_gh_for(assets, drop="openai4s-0.2.0.tar.gz")
    ).run()
    assert report["ok"] is False
    assert report["stopped_at"] == "reverify"
    assert report["published"] is False


def test_an_asset_whose_name_survived_but_whose_bytes_did_not_is_caught(assets):
    """The check compared filenames, so a truncated or replaced asset passed."""
    _signed_dmg(assets)
    report = _pipeline(
        assets,
        mode="release",
        gh=_gh_for(assets, corrupt="openai4s-0.2.0-py3-none-any.whl"),
    ).run()

    assert report["ok"] is False
    assert report["stopped_at"] == "reverify"
    assert "do not match" in report["steps"][-1]["detail"]


def test_an_unexpected_asset_left_in_the_draft_stops_before_publish(assets):
    """Codex P1: `gh release upload --clobber` overwrites matching names but
    leaves anything extra in place, and the old one-way name check still passed.
    A leftover asset from an earlier staging attempt would then be published
    without appearing in checksums, provenance, or the read-back."""
    _signed_dmg(assets)
    report = _pipeline(
        assets,
        mode="release",
        gh=_gh_for(assets, extra="openai4s-0.1.9-py3-none-any.whl"),
    ).run()

    assert report["ok"] is False
    assert report["stopped_at"] == "reverify"
    assert report["published"] is False
    assert "did not produce" in report["steps"][-1]["detail"]


# --------------------------------------------------------------------------
# the cross-channel order
# --------------------------------------------------------------------------


def test_the_github_flip_waits_for_the_package_to_be_on_pypi(assets):
    """Flipping first left a public release with no matching package version
    whenever OIDC, the environment approval or the upload failed."""
    _signed_dmg(assets)
    report = _pipeline(
        assets,
        mode="release",
        gh=_gh_for(assets),
        pypi_check=lambda project, version: False,
    ).run()

    assert report["ok"] is False
    assert report["stopped_at"] == "publish"
    assert "not on PyPI" in report["steps"][-1]["detail"]
    assert "draft is untouched" in report["steps"][-1]["detail"]


def test_a_complete_release_publishes_last(assets):
    _signed_dmg(assets)
    calls: list[list[str]] = []

    inner = _gh_for(assets)

    def gh(argv):
        calls.append(list(argv))
        return inner(argv)

    report = _pipeline(assets, mode="release", gh=gh).run()

    assert report["ok"] and report["published"] is True
    verbs = [call[1] for call in calls]
    assert verbs.index("upload") < verbs.index("edit")
    assert calls[-1][-1] == "--draft=false", "publishing is the final act"


def _write_checksums(assets: Path) -> None:
    """A SHA256SUMS covering the uploaded assets, as step_checksums writes it.

    Excludes the local-only sidecars (they are never uploaded), so the manifest
    matches the release listing `_gh_for` serves.
    """
    lines = []
    for path in sorted(assets.glob("*")):
        if path.name == "SHA256SUMS" or path.name.endswith(_LOCAL_ONLY_SIDECARS):
            continue
        lines.append(f"{sha256_file(path)}  {path.name}\n")
    (assets / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")


def test_the_finalize_step_revalidates_the_draft_before_the_flip(assets):
    """The documented compensation — PyPI has it, the draft is still a draft —
    but it must not flip blind. `--only publish` runs standalone after an
    approval delay; a draft asset deleted or replaced since staging would be made
    public unverified. It re-hashes the draft against its own SHA256SUMS first,
    then flips."""
    _signed_dmg(assets)
    _write_checksums(assets)
    calls: list[list[str]] = []
    inner = _gh_for(assets)

    def gh(argv):
        calls.append(list(argv))
        return inner(argv)

    report = _pipeline(assets, mode="release", only="publish", gh=gh).run()

    assert report["ok"] is True and report["published"] is True
    verbs = [call[1] for call in calls]
    assert "download" in verbs, "finalize published without re-hashing the draft"
    assert verbs[-1] == "edit" and calls[-1][-1] == "--draft=false"


def test_finalize_refuses_to_publish_a_draft_asset_replaced_since_staging(assets):
    """Codex P1: the exact risk finalize re-validation exists for. A draft asset
    whose bytes were replaced between attach and the flip must stop the publish,
    not go public unverified."""
    _signed_dmg(assets)
    _write_checksums(assets)
    # The wheel in the draft no longer matches its verified digest.
    gh = _gh_for(assets, corrupt="openai4s-0.2.0-py3-none-any.whl")

    report = _pipeline(assets, mode="release", only="publish", gh=gh).run()

    assert report["ok"] is False
    assert report["stopped_at"] == "publish"
    assert report["published"] is False
    assert "no longer matches" in report["steps"][-1]["detail"]


def test_finalize_refuses_when_the_draft_diverges_from_what_pypi_published(assets):
    """Codex P1: the SHA256SUMS the finalizer re-hashes against comes from the
    same *mutable* draft, so a second staging run for this tag that clobbered
    both the assets and the manifest would self-validate — while PyPI already
    holds the first run's bytes. PyPI is immutable per version, so it decides."""
    _signed_dmg(assets)
    _write_checksums(assets)
    sdist = assets / "openai4s-0.2.0.tar.gz"

    # The sdist matches, but PyPI holds *different* wheel bytes — another run
    # staged this tag.
    def diverging(project, version):
        return {
            "openai4s-0.2.0-py3-none-any.whl": "f" * 64,
            sdist.name: sha256_file(sdist),
        }

    report = _pipeline(
        assets,
        mode="release",
        only="publish",
        gh=_gh_for(assets),
        pypi_digests=diverging,
    ).run()

    assert report["ok"] is False
    assert report["stopped_at"] == "publish"
    assert report["published"] is False
    assert "disagree with what PyPI" in report["steps"][-1]["detail"]


def test_finalize_publishes_when_the_draft_matches_pypi(assets):
    """The same anchor must not block the normal path: matching digests publish."""
    _signed_dmg(assets)
    _write_checksums(assets)

    report = _pipeline(
        assets, mode="release", only="publish", gh=_gh_for(assets)
    ).run()  # default pypi_digests match the local dists

    assert report["ok"] is True and report["published"] is True


def test_finalize_refuses_when_pypi_returns_no_digests(assets):
    """Codex P1: the old `name in published` guard skipped every file when PyPI
    returned nothing, publishing unverified. An empty response is not a match —
    there is nothing to anchor against, so fail closed."""
    _signed_dmg(assets)
    _write_checksums(assets)

    report = _pipeline(
        assets,
        mode="release",
        only="publish",
        gh=_gh_for(assets),
        pypi_digests=lambda project, version: {},  # nothing to anchor against
    ).run()

    assert report["ok"] is False
    assert report["stopped_at"] == "publish"
    assert report["published"] is False
    assert "no file digests" in report["steps"][-1]["detail"]


def test_finalize_refuses_when_pypi_is_missing_a_distribution(assets):
    """A partial upload — the wheel landed but not the sdist — must not let the
    missing file ride onto GitHub unverified."""
    _signed_dmg(assets)
    _write_checksums(assets)
    wheel = assets / "openai4s-0.2.0-py3-none-any.whl"

    report = _pipeline(
        assets,
        mode="release",
        only="publish",
        gh=_gh_for(assets),
        # only the wheel is on PyPI; the sdist is missing
        pypi_digests=lambda project, version: {wheel.name: sha256_file(wheel)},
    ).run()

    assert report["ok"] is False
    assert report["stopped_at"] == "publish"
    assert report["published"] is False
    assert "missing distributions" in report["steps"][-1]["detail"]


def test_finalize_refuses_a_draft_missing_its_checksum_manifest(assets):
    """Without SHA256SUMS there is nothing to re-validate against; publishing
    then would be a blind flip."""
    _signed_dmg(assets)  # but no SHA256SUMS written to the draft

    report = _pipeline(assets, mode="release", only="publish", gh=_gh_for(assets)).run()

    assert report["ok"] is False
    assert report["stopped_at"] == "publish"
    assert "SHA256SUMS" in report["steps"][-1]["detail"]


# --------------------------------------------------------------------------
# a signature that does not verify is not a signature; a version is exact
# --------------------------------------------------------------------------


def test_a_receipt_whose_deep_verification_failed_is_not_developer_id(tmp_path):
    """describe_macos_image records both the authorities and whether
    `codesign --verify` succeeded. A Developer ID authority string with a
    failed deep verification is a broken signature, not a valid one."""
    from scripts.release_pipeline import SIGNATURE_RECEIPT_SUFFIX, read_signature

    dmg = tmp_path / "x.dmg"
    dmg.write_bytes(b"img")
    dmg.with_name(dmg.name + SIGNATURE_RECEIPT_SUFFIX).write_text(
        json.dumps(
            {
                "authorities": ["Developer ID Application: Example Inc"],
                "adhoc": False,
                "verify_returncode": 1,  # the deep verification FAILED
            }
        ),
        encoding="utf-8",
    )
    info = read_signature(dmg, lambda argv: _completed())
    assert info["developer_id"] is False, (
        "a Developer ID authority with a failed deep verification must not "
        "count as signed"
    )


def test_release_mode_rejects_an_image_whose_signature_does_not_verify(assets):
    dmg = assets / "OpenAI4S-0.2.0-arm64.dmg"
    dmg.write_bytes(b"dmg")
    from scripts.release_pipeline import SIGNATURE_RECEIPT_SUFFIX

    dmg.with_name(dmg.name + SIGNATURE_RECEIPT_SUFFIX).write_text(
        json.dumps(
            {
                "authorities": ["Developer ID Application: Example Inc"],
                "adhoc": False,
                "verify_returncode": 1,
            }
        ),
        encoding="utf-8",
    )
    report = _pipeline(assets, mode="release").run()
    assert report["ok"] is False
    assert report["stopped_at"] == "verify"


@pytest.mark.parametrize(
    "filename,version,matches",
    [
        ("openai4s-0.2.0-py3-none-any.whl", "0.2.0", True),
        ("openai4s-0.2.0.tar.gz", "0.2.0", True),
        ("OpenAI4S-0.2.0-arm64.dmg", "0.2.0", True),
        ("openai4s-0.2.0rc1-py3-none-any.whl", "0.2.0", False),
        ("openai4s-10.2.0-py3-none-any.whl", "0.2.0", False),
        ("openai4s-0.2.0.post1.tar.gz", "0.2.0", False),
    ],
)
def test_the_version_is_matched_exactly_not_as_a_substring(filename, version, matches):
    from scripts.release_pipeline import _asset_version

    assert (_asset_version(filename) == version) is matches


def test_a_prerelease_leftover_does_not_stage_for_the_final_tag(assets):
    """The substring guard let `0.2.0rc1` satisfy a `0.2.0` release on the
    staging path, where it is the only version check."""
    (assets / "openai4s-0.2.0rc1-py3-none-any.whl").write_bytes(b"prerelease")
    report = _pipeline(assets, from_artifacts=True).run()
    assert report["ok"] is False
    assert report["stopped_at"] == "assets"
    assert "another version" in report["steps"][-1]["detail"]


def test_a_receipt_from_a_different_image_does_not_sign_this_one(tmp_path):
    """A stale or copied receipt from any signed image must not vouch for a
    different DMG: the receipt records the image digest, and the gate re-hashes
    and requires a match."""
    from scripts.release_pipeline import SIGNATURE_RECEIPT_SUFFIX, read_signature

    dmg = tmp_path / "unsigned.dmg"
    dmg.write_bytes(b"a-different-unsigned-image")
    dmg.with_name(dmg.name + SIGNATURE_RECEIPT_SUFFIX).write_text(
        json.dumps(
            {
                "authorities": ["Developer ID Application: Example Inc"],
                "adhoc": False,
                "verify_returncode": 0,
                # The digest of some *other* image, not this one.
                "image_sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )
    info = read_signature(dmg, lambda argv: _completed())
    assert info["developer_id"] is False, (
        "a receipt whose recorded digest does not match this image must not "
        "vouch for it"
    )
    assert info["image_digest_matches"] is False
