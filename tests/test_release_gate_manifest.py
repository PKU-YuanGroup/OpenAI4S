"""The quality receipt must describe the canonical gate manifest, exactly.

Every test here drives the **production** consumer —
`release_pipeline.verify_quality_receipt`, or the `step_test` that staging
actually calls — rather than the manifest helpers in isolation. That distinction
is the reason this file exists: the old consumer read `format`, `source_sha` and
a list of exit codes, so a receipt claiming one gate named `pytest` with the argv
`["pytest"]` staged a release. `tests/test_release_pipeline.py::_write_receipt`
wrote exactly that document and every test passed, which is how a receipt that
proved nothing looked like a receipt that proved something.

Each case below is one mutation of a good receipt. A mutation the consumer
accepts is a forgery the pipeline accepts.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts import release_gates
from scripts.release_gates import GateManifestError
from scripts.release_pipeline import (
    QUALITY_RECEIPT_NAME,
    Pipeline,
    ReleaseError,
    verify_quality_receipt,
)

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40
OTHER_SHA = "b" * 40


def _good_receipt(*, source_sha: str = SHA) -> dict:
    """What a real quality job at `source_sha` would upload.

    Built from the manifest rather than hand-listed, so adding a gate does not
    quietly leave this fixture describing the old set — the failure mode the
    module docstring is about.
    """
    return release_gates.build_receipt(
        source_sha,
        [
            {"name": gate.name, "command": list(gate.command), "returncode": 0}
            for gate in release_gates.LOCAL_GATES
        ],
        [
            {
                "name": gate.name,
                "check_name": gate.check_name,
                "check_run_id": f"{index + 1000}",
                "run_id": "9001",
                "url": f"https://example.invalid/{gate.name}",
                "conclusion": "success",
                "head_sha": source_sha,
            }
            for index, gate in enumerate(release_gates.CHECK_SUITE_GATES)
        ],
    )


def _write(directory: Path, document: dict) -> Path:
    target = directory / QUALITY_RECEIPT_NAME
    target.write_text(json.dumps(document), "utf-8")
    return target


def _verify(tmp_path: Path, document: dict, *, expected_sha: str = SHA):
    """Through the production entry point, which reads a file from disk."""
    return verify_quality_receipt(_write(tmp_path, document), expected_sha=expected_sha)


def test_a_faithful_receipt_is_accepted(tmp_path):
    """The success path, so a consumer wired to the wrong field cannot pass by
    refusing everything."""
    document = _verify(tmp_path, _good_receipt())
    assert document["source_sha"] == SHA
    assert len(document["gates"]) == len(release_gates.LOCAL_GATES)
    assert len(document["checks"]) == len(release_gates.CHECK_SUITE_GATES)


def test_the_two_gate_receipt_the_old_consumer_accepted_is_refused(tmp_path):
    """The exact document `tests/test_release_pipeline.py` used as its fixture.

    Right SHA, right format, all exit codes zero, one eighth of the gates, and
    argv that never ran. This passed.
    """
    forged = {
        "format": release_gates.RECEIPT_FORMAT,
        "schema_version": 1,
        "source_sha": SHA,
        "gates": [
            {"name": "pytest", "command": ["pytest"], "returncode": 0},
            {"name": "mypy", "command": ["mypy"], "returncode": 0},
        ],
    }
    with pytest.raises(ReleaseError):
        _verify(tmp_path, forged)


def test_a_missing_gate_is_named(tmp_path):
    document = _good_receipt()
    dropped = document["gates"].pop(3)
    with pytest.raises(ReleaseError, match=dropped["name"]):
        _verify(tmp_path, document)


def test_a_duplicated_gate_cannot_pad_the_count_back_up(tmp_path):
    """Dropping one row and duplicating another keeps `len(gates)` right."""
    document = _good_receipt()
    document["gates"].pop()
    document["gates"].append(copy.deepcopy(document["gates"][0]))
    with pytest.raises(ReleaseError, match="more than once"):
        _verify(tmp_path, document)


def test_an_unknown_gate_is_refused(tmp_path):
    document = _good_receipt()
    document["gates"].append(
        {"name": "definitely-fine", "command": ["true"], "returncode": 0}
    )
    with pytest.raises(ReleaseError, match="unknown"):
        _verify(tmp_path, document)


def test_a_substituted_command_is_refused(tmp_path):
    """The forgery the old consumer could not even see: the gate is present, the
    name is right, the exit code is zero, and what ran was `true`."""
    document = _good_receipt()
    target = next(row for row in document["gates"] if row["name"] == "pytest")
    target["command"] = ["true"]
    with pytest.raises(ReleaseError, match="pytest"):
        _verify(tmp_path, document)


def test_a_command_that_merely_gained_an_argument_is_refused(tmp_path):
    """`pytest -q -k 'not slow'` is not the gate the manifest declares."""
    document = _good_receipt()
    target = next(row for row in document["gates"] if row["name"] == "pytest")
    target["command"] = list(target["command"]) + ["-k", "not slow"]
    with pytest.raises(ReleaseError):
        _verify(tmp_path, document)


def test_a_failing_exit_code_is_still_refused(tmp_path):
    document = _good_receipt()
    next(row for row in document["gates"] if row["name"] == "mypy")["returncode"] = 1
    with pytest.raises(ReleaseError, match="mypy"):
        _verify(tmp_path, document)


def test_an_older_schema_version_is_refused(tmp_path):
    """Not `>=`: an older producer cannot know about a gate added later, so
    accepting its receipt silently drops that gate."""
    document = _good_receipt()
    document["schema_version"] = release_gates.SCHEMA_VERSION - 1
    with pytest.raises(ReleaseError, match="schema_version"):
        _verify(tmp_path, document)


def test_a_receipt_from_a_different_manifest_is_refused(tmp_path):
    document = _good_receipt()
    document["manifest_digest"] = "0" * 64
    with pytest.raises(ReleaseError, match="different gate manifest"):
        _verify(tmp_path, document)


def test_a_receipt_with_no_manifest_digest_is_refused(tmp_path):
    document = _good_receipt()
    document.pop("manifest_digest")
    with pytest.raises(ReleaseError, match="manifest digest"):
        _verify(tmp_path, document)


def test_a_receipt_for_another_commit_is_refused(tmp_path):
    with pytest.raises(ReleaseError):
        _verify(tmp_path, _good_receipt(source_sha=OTHER_SHA))


# --- the check-suite half ---------------------------------------------------


def test_a_missing_required_check_is_refused(tmp_path):
    document = _good_receipt()
    dropped = document["checks"].pop(0)
    with pytest.raises(ReleaseError, match=dropped["name"]):
        _verify(tmp_path, document)


def test_a_check_that_ran_on_another_commit_is_refused(tmp_path):
    """The retag case, seen from the attestation side: the browser matrix was
    green, on different sources."""
    document = _good_receipt()
    document["checks"][0]["head_sha"] = OTHER_SHA
    with pytest.raises(ReleaseError, match="not on"):
        _verify(tmp_path, document)


def test_a_check_that_did_not_conclude_success_is_refused(tmp_path):
    document = _good_receipt()
    document["checks"][0]["conclusion"] = "skipped"
    with pytest.raises(ReleaseError, match="skipped"):
        _verify(tmp_path, document)


def test_a_check_with_no_run_id_is_refused(tmp_path):
    """An attestation nobody can go and look at is not evidence."""
    document = _good_receipt()
    document["checks"][0]["check_run_id"] = ""
    with pytest.raises(ReleaseError, match="check-run id"):
        _verify(tmp_path, document)


def test_python_browser_and_linux_private_pid_checks_are_required():
    """Item 4's binding, asserted on the manifest rather than on prose."""
    names = {gate.check_name for gate in release_gates.CHECK_SUITE_GATES}
    for version in ("3.10", "3.12", "3.13", "3.14"):
        assert f"Offline tests (py{version})" in names
    for engine in ("chromium", "firefox", "webkit"):
        assert f"Browser workbench E2E ({engine})" in names
    assert "Linux bubblewrap Python/R persistent interrupt" in names
    assert "Linux bubblewrap full filesystem/egress boundary" in names


def test_every_required_check_names_a_job_that_really_exists_in_ci():
    """A renamed CI job must not silently stop being required.

    Parses the workflow and renders `jobs.<id>.name` against its matrix, which
    is what GitHub reports as the check-run name. Comparing against the file
    rather than a hardcoded list is what makes this catch a rename.
    """
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    rendered: set[str] = set()
    for job in workflow["jobs"].values():
        name = str(job.get("name") or "")
        if not name:
            continue
        matrix = ((job.get("strategy") or {}).get("matrix")) or {}
        if not matrix:
            rendered.add(name)
            continue
        for key, values in matrix.items():
            if not isinstance(values, list):
                continue
            for value in values:
                rendered.add(name.replace("${{ matrix.%s }}" % key, str(value)))
    for gate in release_gates.CHECK_SUITE_GATES:
        assert gate.check_name in rendered, (
            f"required check {gate.check_name!r} matches no job in ci.yml; "
            f"a job was renamed and the release stopped requiring it"
        )


# --- attest_check_runs, the producer side ----------------------------------


def _listing(*, head_sha: str = SHA, conclusion: str = "success") -> dict:
    return {
        "check_runs": [
            {
                "id": 4200 + index,
                "name": gate.check_name,
                "head_sha": head_sha,
                "conclusion": conclusion,
                "started_at": "2026-07-29T00:00:00Z",
                "details_url": "https://example.invalid/run",
                "check_suite": {"id": 77},
            }
            for index, gate in enumerate(release_gates.CHECK_SUITE_GATES)
        ]
    }


def test_attestation_records_the_ids_a_reader_would_need():
    rows = release_gates.attest_check_runs(_listing(), expected_sha=SHA)
    assert len(rows) == len(release_gates.CHECK_SUITE_GATES)
    assert all(row["check_run_id"] for row in rows)
    assert all(row["run_id"] == "77" for row in rows)


def test_attestation_refuses_green_checks_from_another_commit():
    with pytest.raises(GateManifestError, match="not "):
        release_gates.attest_check_runs(_listing(head_sha=OTHER_SHA), expected_sha=SHA)


def test_attestation_refuses_a_check_that_was_skipped():
    with pytest.raises(GateManifestError, match="skipped"):
        release_gates.attest_check_runs(
            _listing(conclusion="skipped"), expected_sha=SHA
        )


def test_attestation_refuses_an_absent_check():
    listing = _listing()
    listing["check_runs"] = listing["check_runs"][:-1]
    with pytest.raises(GateManifestError, match="no check run"):
        release_gates.attest_check_runs(listing, expected_sha=SHA)


def test_attestation_uses_the_latest_attempt_for_a_name():
    """A re-run turns a red check green; the stale red attempt must not decide."""
    listing = _listing(conclusion="failure")
    for run in list(listing["check_runs"]):
        listing["check_runs"].append(
            {
                **run,
                "id": run["id"] + 500,
                "conclusion": "success",
                "started_at": "2026-07-30T00:00:00Z",
            }
        )
    rows = release_gates.attest_check_runs(listing, expected_sha=SHA)
    assert all(row["conclusion"] == "success" for row in rows)


# --- and through the step staging really runs ------------------------------


def test_staging_refuses_a_forged_receipt_through_step_test(tmp_path):
    """`verify_quality_receipt` is only load-bearing if `step_test` calls it.

    Drives `Pipeline.step_test` in the `--from-artifacts` mode the attach job
    uses, so a consumer that is correct but unwired still fails here.
    """
    assets = tmp_path / "assets"
    assets.mkdir()
    forged = _good_receipt()
    next(row for row in forged["gates"] if row["name"] == "pytest")["command"] = [
        "true"
    ]
    _write(assets, forged)

    def runner(argv, cwd=None):
        import subprocess

        parts = [str(a) for a in argv]
        if parts[:3] == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(parts, 0, SHA.encode(), b"")
        return subprocess.CompletedProcess(parts, 0, b"", b"")

    pipeline = Pipeline(
        "0.2.0",
        mode="release",
        assets_dir=assets,
        from_artifacts=True,
        runner=runner,
    )
    with pytest.raises(ReleaseError, match="pytest"):
        pipeline.step_test()


def test_staging_accepts_a_faithful_receipt_through_step_test(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    _write(assets, _good_receipt())

    def runner(argv, cwd=None):
        import subprocess

        parts = [str(a) for a in argv]
        if parts[:3] == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(parts, 0, SHA.encode(), b"")
        return subprocess.CompletedProcess(parts, 0, b"", b"")

    pipeline = Pipeline(
        "0.2.0",
        mode="release",
        assets_dir=assets,
        from_artifacts=True,
        runner=runner,
    )
    result = pipeline.step_test()
    assert result.ok
    assert result.facts["source_sha"] == SHA
    # The attestation must survive into the report, or the evidence bundle
    # cannot carry the run IDs.
    assert result.facts["checks"]


# --- item 8: the faults the release must not survive ------------------------
#
# One test per way the pipeline can be handed a plausible-looking lie. Each was
# either accepted before this batch, or had nothing that could express it.


def _pipeline_assets(tmp_path, *, source_sha=SHA):
    """A staging directory as the attach job receives it."""
    from scripts import release_receipts

    assets = tmp_path / "assets"
    assets.mkdir()
    wheel = assets / "openai4s-0.2.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel-bytes")
    sdist = assets / "openai4s-0.2.0.tar.gz"
    sdist.write_bytes(b"sdist-bytes")
    _write(assets, _good_receipt(source_sha=source_sha))
    receipt = release_receipts.build_build_receipt("dist", source_sha, [wheel, sdist])
    (assets / release_receipts.build_receipt_name("dist")).write_text(
        json.dumps(receipt), "utf-8"
    )
    return assets


def _runner_at(head):
    import subprocess

    def runner(argv, cwd=None):
        parts = [str(a) for a in argv]
        if parts[:3] == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(parts, 0, head.encode(), b"")
        return subprocess.CompletedProcess(parts, 0, b"", b"")

    return runner


def test_a_tag_that_moved_between_jobs_is_refused(tmp_path):
    """Item 8, retag-between-jobs.

    The workflow froze `SHA` before any job ran; this job's checkout is
    `OTHER_SHA`, which is what a force-pushed tag produces. Every job used to
    resolve `inputs.tag` on its own, so the wheel, the DMG and the gates could
    each be a different commit with nothing comparing them.
    """
    assets = _pipeline_assets(tmp_path)
    pipeline = Pipeline(
        "0.2.0",
        mode="release",
        assets_dir=assets,
        from_artifacts=True,
        runner=_runner_at(OTHER_SHA),
        source_sha=SHA,
    )
    with pytest.raises(ReleaseError, match="the tag moved between jobs"):
        pipeline.step_test()


def test_the_frozen_sha_is_checked_not_believed(tmp_path):
    """The same guard from the other side: matching sources are accepted, so the
    check cannot be passing by refusing everything."""
    assets = _pipeline_assets(tmp_path)
    pipeline = Pipeline(
        "0.2.0",
        mode="release",
        assets_dir=assets,
        from_artifacts=True,
        runner=_runner_at(SHA),
        source_sha=SHA,
    )
    assert pipeline.step_test().ok


def test_an_artifact_built_from_a_different_commit_is_refused(tmp_path):
    """Item 2/8: the build receipt names another commit than the frozen SHA.

    This is the retag window that the receipt-versus-checkout comparison alone
    cannot see: the DMG job checked out a moved tag, built an image, and the
    staging job's own checkout is fine.
    """
    from scripts import release_receipts

    assets = _pipeline_assets(tmp_path)
    dmg = assets / "OpenAI4S-0.2.0-arm64.dmg"
    dmg.write_bytes(b"dmg-bytes")
    stale = release_receipts.build_build_receipt("macos", OTHER_SHA, [dmg])
    (assets / release_receipts.build_receipt_name("macos")).write_text(
        json.dumps(stale), "utf-8"
    )
    with pytest.raises(release_receipts.ReceiptError, match="not the same commit"):
        release_receipts.verify_build_receipts(
            sorted(assets.glob("build-receipt-*.json")),
            expected_sha=SHA,
            assets_dir=assets,
            required_kinds=("dist", "macos"),
        )


def test_an_artifact_whose_bytes_changed_after_the_receipt_is_refused(tmp_path):
    from scripts import release_receipts

    assets = _pipeline_assets(tmp_path)
    (assets / "openai4s-0.2.0-py3-none-any.whl").write_bytes(b"different-bytes")
    with pytest.raises(release_receipts.ReceiptError, match="does not match its build"):
        release_receipts.verify_build_receipts(
            sorted(assets.glob("build-receipt-*.json")),
            expected_sha=SHA,
            assets_dir=assets,
            required_kinds=("dist",),
        )


def test_an_artifact_with_no_build_receipt_is_refused(tmp_path):
    from scripts import release_receipts

    assets = _pipeline_assets(tmp_path)
    with pytest.raises(release_receipts.ReceiptError, match="no build receipt for"):
        release_receipts.verify_build_receipts(
            sorted(assets.glob("build-receipt-*.json")),
            expected_sha=SHA,
            assets_dir=assets,
            required_kinds=("dist", "macos"),
        )


def test_a_seal_failure_stops_the_release(tmp_path, monkeypatch):
    """Item 5/8: sealing used to be best-effort, after `upload`, and documented
    as unable to fail a good release.

    Three consequences: a bundle sealed after the upload describes a release that
    has already gone out; a seal that cannot fail can be silently absent; and it
    was called with no `files=` at all, so the archive held one JSON report and
    none of the artifacts it claimed to be evidence for. It is now a step, before
    `checksums` and before `upload`, and a failure is a refusal.
    """
    from scripts import release_pipeline as module

    assets = _pipeline_assets(tmp_path)
    pipeline = Pipeline(
        "0.2.0",
        mode="release",
        assets_dir=assets,
        from_artifacts=True,
        runner=_runner_at(SHA),
        source_sha=SHA,
    )
    pipeline.assets = [assets / "openai4s-0.2.0-py3-none-any.whl"]

    def explode(*a, **k):
        raise OSError("read-only file system")

    monkeypatch.setattr(module, "seal_evidence_bundle", explode)
    with pytest.raises(ReleaseError, match="refusing to publish"):
        pipeline.step_evidence()


def test_the_evidence_bundle_carries_the_facts_it_claims_to(tmp_path):
    """It used to contain exactly one file: `release-report.json`, with no source
    SHA, no builder, no artifact hashes, no sandbox posture and none of the
    receipts."""
    assets = _pipeline_assets(tmp_path)
    pipeline = Pipeline(
        "0.2.0",
        mode="release",
        assets_dir=assets,
        from_artifacts=True,
        runner=_runner_at(SHA),
        source_sha=SHA,
    )
    pipeline.assets = [
        assets / "openai4s-0.2.0-py3-none-any.whl",
        assets / "openai4s-0.2.0.tar.gz",
    ]
    result = pipeline.step_evidence()
    assert result.ok

    import zipfile

    bundle = assets / "openai4s-0.2.0-evidence.zip"
    with zipfile.ZipFile(bundle) as archive:
        names = set(archive.namelist())
        report = json.loads(archive.read("release-report.json"))
    # The receipts travel with it, not just the report.
    assert "artifacts/quality-receipt.json" in names
    assert "artifacts/build-receipt-dist.json" in names
    assert report["source_sha"] == SHA
    assert report["builder"]["os"]
    assert report["builder"]["interpreter_version"]
    assert report["sandbox"]["requested"]
    assert set(report["artifacts"]) == {
        "openai4s-0.2.0-py3-none-any.whl",
        "openai4s-0.2.0.tar.gz",
    }
    # And the product's own verifier accepts it.
    from openai4s.evidence import verify_package

    assert verify_package(bundle)["ok"]


def test_the_evidence_bundle_is_sealed_before_anything_is_uploaded():
    """Ordering, asserted on STEPS rather than on prose.

    `evidence` before `checksums` so SHA256SUMS covers the bundle, and both
    before `upload` so a failure can still prevent the release.
    """
    from scripts.release_pipeline import STEPS

    assert STEPS.index("verify") < STEPS.index("evidence")
    assert STEPS.index("evidence") < STEPS.index("checksums")
    assert STEPS.index("checksums") < STEPS.index("upload")
    assert STEPS.index("upload") < STEPS.index("publish")


def test_replacing_a_draft_asset_and_its_manifest_together_is_caught(tmp_path):
    """Item 7/8: the attack the self-referential check could not see.

    `step_publish` re-hashed the draft against the draft's own `SHA256SUMS`.
    Replace the asset and the manifest in one motion and the two agree with each
    other, so the check passes. The staging attestation does not live in the
    draft, so it disagrees.
    """
    from scripts import release_receipts

    attestation = tmp_path / release_receipts.STAGE_ATTESTATION_NAME
    wheel = tmp_path / "openai4s-0.2.0-py3-none-any.whl"
    wheel.write_bytes(b"the bytes that were verified")
    attestation.write_text(
        json.dumps(
            release_receipts.build_stage_attestation(
                version="0.2.0", source_sha=SHA, assets=[wheel]
            )
        ),
        "utf-8",
    )
    attested = release_receipts.verify_stage_attestation(attestation, version="0.2.0")

    # What the tampered draft now claims about itself: a different asset, and a
    # SHA256SUMS rewritten to match it.
    tampered_digest = "9" * 64
    drifted = [
        name
        for name, digest in {wheel.name: tampered_digest}.items()
        if name in attested and attested[name] != digest
    ]
    assert drifted == [wheel.name], (
        "the attestation must disagree with a draft whose asset and manifest "
        "were both replaced"
    )


def test_an_attestation_for_another_version_is_refused(tmp_path):
    from scripts import release_receipts

    wheel = tmp_path / "openai4s-0.2.0-py3-none-any.whl"
    wheel.write_bytes(b"bytes")
    target = tmp_path / release_receipts.STAGE_ATTESTATION_NAME
    target.write_text(
        json.dumps(
            release_receipts.build_stage_attestation(
                version="0.2.0", source_sha=SHA, assets=[wheel]
            )
        ),
        "utf-8",
    )
    with pytest.raises(release_receipts.ReceiptError, match="version"):
        release_receipts.verify_stage_attestation(target, version="0.3.0")


def test_the_finalize_job_refuses_when_its_attestation_is_absent(tmp_path):
    """A finalize job told to use an attestation that is not there must refuse,
    not fall back to trusting the draft."""
    assets = _pipeline_assets(tmp_path)
    pipeline = Pipeline(
        "0.2.0",
        mode="release",
        assets_dir=assets,
        only="publish",
        runner=_runner_at(SHA),
        source_sha=SHA,
        attestation=tmp_path / "nope.json",
        gh=lambda argv: __import__("subprocess").CompletedProcess(
            list(argv), 0, b'{"assets": [{"name": "SHA256SUMS", "size": 1}]}', b""
        ),
        pypi_check=lambda p, v: True,
    )
    with pytest.raises(ReleaseError, match="no stage attestation"):
        pipeline._revalidate_draft_from_checksums()


# --- item 1 & 4: the workflow graph itself ---------------------------------
#
# These parse the workflows and assert structural properties of the job graph.
# They are deliberately not substring searches: the existing
# `tests/test_release_gates.py` checks release.yml with `in` against its text,
# which passes on a commented-out line and cannot see a job that was added
# without a timeout.


def _workflow(name):
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text())


def _checkout_refs(job):
    """Every `ref:` a job's checkout steps resolve."""
    refs = []
    for step in job.get("steps") or []:
        uses = str(step.get("uses") or "")
        if "actions/checkout" in uses:
            refs.append(str((step.get("with") or {}).get("ref") or ""))
    return refs


def test_every_release_job_checks_out_the_immutable_workflow_sha():
    """Item 1. No checkout may resolve a dispatch input or step output.

    A tag is mutable, so the gates could run on one commit while the wheel came
    from a second and the DMG from a third. ``github.sha`` is fixed when the run
    is created; the freeze job separately proves it belongs to ``origin/main``.
    """
    workflow = _workflow("release.yml")
    jobs = workflow["jobs"]
    assert "freeze" in jobs, "no job validates the source SHA"

    offenders = {}
    for name, job in jobs.items():
        for ref in _checkout_refs(job):
            if ref != "${{ github.sha }}":
                offenders.setdefault(name, []).append(ref)
    assert (
        not offenders
    ), f"these jobs check out something other than github.sha: {offenders}"

    validation_step = next(
        step
        for step in jobs["freeze"].get("steps") or []
        if step.get("name") == "Validate the workflow SHA and optional release tag"
    )
    validation = str(validation_step.get("run") or "")
    assert 'git merge-base --is-ancestor "$SHA" origin/main' in validation
    assert '"$TAG_SHA" != "$SHA"' in validation
    assert "^v[0-9]+\\.[0-9]+\\.[0-9]+$" in validation
    assert validation_step["env"]["PYPI_ONLY"] == "${{ inputs.pypi_only }}"
    assert 'if [ "$PUBLISH" = "true" ] || [ "$PYPI_ONLY" = "true" ]; then' in validation
    assert 'git cat-file -t "$TAG"' in validation


def test_every_downstream_checkout_waits_for_source_validation():
    """A trusted ref is not enough unless validation happens before execution."""
    jobs = _workflow("release.yml")["jobs"]
    for name, job in jobs.items():
        if name == "freeze":
            continue
        if _checkout_refs(job):
            needs = job.get("needs") or []
            needs = [needs] if isinstance(needs, str) else needs
            assert (
                "freeze" in needs
            ), f"{name} executes a checkout without waiting for the freeze job"


def test_every_publication_boundary_revalidates_the_remote_annotated_tag():
    """A tag can move after ``freeze`` while a long platform build is running.

    Each outward-facing boundary must fetch the current remote ref into a fixed
    local name immediately before it mutates GitHub or PyPI, then prove both the
    annotated object type and its peeled commit. The frozen checkout alone only
    proves which source built the artifacts; it says nothing about a later ref
    rewrite.
    """
    jobs = _workflow("release.yml")["jobs"]
    for job_name in ("attach", "pypi", "finalize"):
        candidates = [
            step
            for step in jobs[job_name].get("steps") or []
            if str(step.get("name") or "").startswith(
                "Revalidate the remote annotated tag"
            )
        ]
        assert len(candidates) == 1, f"{job_name} has no unique remote-tag check"
        step = candidates[0]
        assert step["env"] == {
            "TAG": "${{ inputs.tag }}",
            "SHA": "${{ github.sha }}",
        }
        # Each boundary *calls* the one implementation. Asserting substrings of
        # three inline copies made the gate mirror the copy-paste instead of
        # removing the need for it: three copies can drift apart and still
        # satisfy four substring checks each.
        assert (
            str(step.get("run") or "").strip()
            == 'bash scripts/revalidate_release_tag.sh "$TAG" "$SHA"'
        )

    assert jobs["pypi"]["permissions"] == {
        "contents": "read",
        "id-token": "write",
    }, "the PyPI tag revalidation needs read access without widening OIDC"


def test_the_tag_revalidation_is_one_implementation_with_teeth():
    """The checks that used to be inline, asserted once against the script."""
    script = (ROOT / "scripts" / "revalidate_release_tag.sh").read_text("utf-8")

    assert "set -euo pipefail" in script
    assert '"refs/tags/${TAG}:${LOCAL_REF}"' in script
    assert 'git cat-file -t "$LOCAL_REF"' in script
    assert '"${LOCAL_REF}^{commit}"' in script
    assert '"$TAG_SHA" != "$SHA"' in script
    assert script.count("exit 1") == 2, "both refusals must still be refusals"


def test_the_container_publication_boundary_is_held_to_the_same_rule():
    """`publish-image.yml` pushes to ghcr with `packages: write`.

    It is the other publication workflow, and the release-workflow assertions
    above cannot see it -- which is how it kept a mutable-tag checkout with the
    token left in git config long after release.yml stopped.
    """
    job = _workflow("publish-image.yml")["jobs"]["publish"]
    steps = job.get("steps") or []

    checkouts = [s for s in steps if "actions/checkout" in str(s.get("uses") or "")]
    assert checkouts, "no checkout to hold to the rule"
    for step in checkouts:
        with_ = step.get("with") or {}
        assert with_.get("ref") == "${{ github.sha }}"
        assert with_.get("persist-credentials") is False

    revalidations = [
        s for s in steps if "revalidate_release_tag.sh" in str(s.get("run") or "")
    ]
    assert len(revalidations) == 2, (
        "the tag must be proved against the frozen SHA before the build and "
        "again immediately before the outward-facing push"
    )
    names = [str(s.get("name") or "") for s in steps]
    assert names.index("Push the smoke-passing image") > names.index(
        "Revalidate the remote annotated tag before pushing"
    )

    resolve = next(s for s in steps if s.get("name") == "Resolve the release tag")
    # Anchored: the `v[0-9]*.[0-9]*.[0-9]*` glob it replaces also matched
    # `v1.2.3;anything`, and the value reaches later steps.
    assert "^v[0-9]+\\.[0-9]+\\.[0-9]+$" in str(resolve.get("run") or "")


@pytest.mark.parametrize("workflow", ["ci.yml", "release.yml"])
def test_every_job_has_an_explicit_timeout(workflow):
    """Item 4. ci.yml had none at all on any of its ten jobs, so a hung browser
    job or a wedged kernel test burned the runner's six-hour default."""
    jobs = _workflow(workflow)["jobs"]
    missing = sorted(name for name, job in jobs.items() if "timeout-minutes" not in job)
    assert not missing, f"{workflow} jobs with no timeout-minutes: {missing}"
    for name, job in jobs.items():
        budget = job["timeout-minutes"]
        assert (
            isinstance(budget, int) and 0 < budget <= 120
        ), f"{workflow}:{name} has an implausible timeout: {budget}"


def test_linux_bwrap_interrupt_smoke_is_an_independent_real_runtime_job():
    """The private-PID SIGINT proof must not collapse back into fake procfs.

    Raw networking is a deliberate scope choice, so the job proves no egress
    claim regardless of what the runner could support.
    Ubuntu's user-namespace restriction is satisfied with its bwrap-specific
    capability-stripping profile, not by disabling AppArmor for the runner.
    Everything relevant to worker identity still runs through real bwrap plus
    real Python and R interpreters on every CI event.
    """

    jobs = _workflow("ci.yml")["jobs"]
    job = jobs["linux-bwrap-kernel-interrupt"]
    assert job["runs-on"] == "ubuntu-24.04"
    assert "if" not in job, "the security regression must run on pull requests"
    assert job.get("continue-on-error") is not True

    steps = job["steps"]
    install = "\n".join(str(step.get("run") or "") for step in steps)
    for package in (
        "apparmor",
        "apparmor-profiles",
        "bubblewrap",
        "r-base-core",
        "r-cran-jsonlite",
    ):
        assert package in install
    assert "--no-install-recommends" in install
    assert "apparmor_restrict_unprivileged_userns=0" not in install
    assert "sudo bwrap" not in install

    userns_steps = [
        step for step in steps if "bwrap-userns-restrict" in str(step.get("run") or "")
    ]
    assert len(userns_steps) == 1
    userns = userns_steps[0]
    assert "if" not in userns
    assert userns.get("continue-on-error") is not True
    assert str(userns.get("run") or "").strip() == (
        "sudo apparmor_parser --replace "
        "/usr/share/apparmor/extra-profiles/bwrap-userns-restrict"
    )

    preflight_steps = [
        step
        for step in steps
        if "raise SystemExit((os.getpid(), os.getppid()) != (2, 1))"
        in str(step.get("run") or "")
    ]
    assert len(preflight_steps) == 1
    preflight = preflight_steps[0]
    preflight_run = str(preflight.get("run") or "")
    assert "if" not in preflight
    assert preflight.get("continue-on-error") is not True
    assert preflight_run.strip() == (
        "/usr/bin/bwrap --die-with-parent --new-session --unshare-ipc "
        "--unshare-uts --unshare-pid --ro-bind / / --dev /dev --proc /proc -- "
        "/usr/bin/python3 -c 'import os; raise SystemExit((os.getpid(), "
        "os.getppid()) != (2, 1))'"
    )

    smoke_steps = [
        step
        for step in steps
        if "harness.smoke.linux_bwrap_interrupt" in str(step.get("run") or "")
    ]
    assert len(smoke_steps) == 1
    smoke = smoke_steps[0]
    assert "if" not in smoke
    assert (
        str(smoke.get("run") or "").strip()
        == "uv run python -m harness.smoke.linux_bwrap_interrupt"
    )
    assert smoke.get("env") == {
        "OPENAI4S_KERNEL_SANDBOX": "enforce",
        "OPENAI4S_KERNEL_ALLOW_RAW_NETWORK": "1",
    }
    assert smoke.get("continue-on-error") is not True
    install_steps = [
        step for step in steps if "r-base-core" in str(step.get("run") or "")
    ]
    assert len(install_steps) == 1
    assert "if" not in install_steps[0]
    assert install_steps[0].get("continue-on-error") is not True


def test_linux_sandbox_full_is_an_independent_enforce_job_without_raw_network():
    """The four-check filesystem/egress boundary must not collapse into the
    interrupt job, must not allow raw networking, and must not continue-on-error.

    Check-run name is attested at the frozen SHA as ci-linux-sandbox-full.
    The release workflow still does not re-execute this smoke as a
    platform-checks matrix leg (PLATFORM_CHECKS_UNAVAILABLE) until multiple
    scheduled greens pass.
    """
    jobs = _workflow("ci.yml")["jobs"]
    job = jobs["linux-sandbox-full"]
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["name"] == "Linux bubblewrap full filesystem/egress boundary"
    assert "if" not in job, "the full boundary must run on pull requests"
    assert job.get("continue-on-error") is not True

    steps = job["steps"]
    for step in steps:
        assert step.get("continue-on-error") is not True, step.get("name")
        assert "if" not in step, step.get("name")

    install = "\n".join(str(step.get("run") or "") for step in steps)
    for package in ("apparmor", "apparmor-profiles", "bubblewrap"):
        assert package in install
    assert "apparmor_restrict_unprivileged_userns=0" not in install
    assert "OPENAI4S_KERNEL_ALLOW_RAW_NETWORK" not in install

    userns_steps = [
        step for step in steps if "bwrap-userns-restrict" in str(step.get("run") or "")
    ]
    assert len(userns_steps) == 1

    preflight_steps = [
        step
        for step in steps
        if "--unshare-net" in str(step.get("run") or "")
        and "raise SystemExit((os.getpid(), os.getppid()) != (2, 1))"
        in str(step.get("run") or "")
    ]
    assert len(preflight_steps) == 1

    smoke_steps = [
        step
        for step in steps
        if "harness.smoke.linux_sandbox" in str(step.get("run") or "")
    ]
    assert len(smoke_steps) == 1
    smoke = smoke_steps[0]
    assert (
        str(smoke.get("run") or "").strip()
        == "uv run python -m harness.smoke.linux_sandbox"
    )
    assert smoke.get("env") == {"OPENAI4S_KERNEL_SANDBOX": "enforce"}
    assert "OPENAI4S_KERNEL_ALLOW_RAW_NETWORK" not in (smoke.get("env") or {})

    from harness.smoke.sandbox_boundary import EXPECTED
    from scripts import release_gates

    names = {gate.name: gate.check_name for gate in release_gates.CHECK_SUITE_GATES}
    assert names["ci-linux-sandbox-full"] == job["name"]
    assert "linux-sandbox" in release_gates.PLATFORM_CHECKS_UNAVAILABLE
    assert EXPECTED == {
        "network_blocked": True,
        "outside_write_blocked": True,
        "subprocess_secret_absent": True,
        "workspace_write": True,
    }


def test_full_boundary_smoke_refuses_a_raw_network_override():
    """Mutation: the interrupt job's env must not silently green this gate."""
    from harness.smoke.sandbox_boundary import refuse_raw_network_override

    with pytest.raises(RuntimeError, match="raw-network override"):
        refuse_raw_network_override(
            label="linux",
            env={"OPENAI4S_KERNEL_ALLOW_RAW_NETWORK": "1"},
        )
    with pytest.raises(RuntimeError, match="raw-network override"):
        refuse_raw_network_override(
            label="linux",
            env={"OPENAI4S_KERNEL_ALLOW_RAW_NETWORK": "true"},
        )
    refuse_raw_network_override(label="linux", env={})
    refuse_raw_network_override(
        label="linux", env={"OPENAI4S_KERNEL_ALLOW_RAW_NETWORK": "0"}
    )


def test_a_skipped_linux_full_boundary_check_is_refused():
    """Mutation: a skipped check run is not evidence at the frozen SHA."""
    listing = _listing()
    for run in listing["check_runs"]:
        if run["name"] == "Linux bubblewrap full filesystem/egress boundary":
            run["conclusion"] = "skipped"
            break
    else:
        raise AssertionError("fixture has no linux-sandbox-full check run")
    with pytest.raises(GateManifestError, match="skipped"):
        release_gates.attest_check_runs(listing, expected_sha=SHA)


def test_the_release_binds_the_platform_checks_to_the_frozen_sha():
    """Item 4's third leg. The sandbox jobs in ci.yml run only on
    `schedule`/`workflow_dispatch`, so no check run for them exists at a release
    SHA -- requiring one would make every release unreachable. They are executed
    in the release instead, which is why they are not in CHECK_SUITE_GATES.
    """
    jobs = _workflow("release.yml")["jobs"]
    assert "platform-checks" in jobs
    from scripts import release_gates

    modules = {
        entry["module"]
        for entry in jobs["platform-checks"]["strategy"]["matrix"]["include"]
    }
    # Against the manifest constant, not a hardcoded pair: a declaration nothing
    # reads is how `Tool.dangerous` came to be set on ten tools and consulted by
    # no gate. Comparing here makes the constant load-bearing, so the workflow and
    # the manifest cannot drift.
    declared = {
        command[-1] for command in release_gates.PLATFORM_CHECK_COMMANDS.values()
    }
    assert modules == declared, (
        f"release.yml runs {sorted(modules)} but the manifest declares "
        f"{sorted(declared)}"
    )

    attested = {gate.check_name for gate in release_gates.CHECK_SUITE_GATES}
    assert "macOS sandbox enforcement (nightly)" not in attested, (
        "a nightly-only check must not be an attested gate: no check run for it "
        "exists at a release SHA, so requiring one makes releases unreachable"
    )
    assert "Linux bubblewrap full filesystem/egress boundary" in attested
    # And the jobs that build artifacts must wait for them.
    for name in ("build", "macos-app"):
        assert "platform-checks" in jobs[name]["needs"]


def test_the_release_declares_every_platform_it_does_not_prove():
    """A platform leaves the matrix by being declared unprovable, or not at all.

    The former hosted `linux-sandbox` run failed during network-namespace setup.
    The targeted CI job now loads a restricted bwrap profile, so that historical
    result does not establish what a full smoke would do today; the raw-network
    job still cannot prove it. release.yml nevertheless required the unproven
    leg and `build` needs `platform-checks`, so every publication was
    unreachable rather than only a bad one being blocked.

    The fix must not be a silent deletion. An absent platform and a passing one
    look identical in an evidence bundle, and the plan's rollback clause is that
    missing evidence degrades a platform to preview rather than recording an
    un-run check as success. So the two dicts together must still name every
    platform, which is what makes dropping one a test failure rather than a
    quiet reduction in scope.
    """
    from scripts import release_gates

    executed = set(release_gates.PLATFORM_CHECK_COMMANDS)
    unprovable = set(release_gates.PLATFORM_CHECKS_UNAVAILABLE)

    assert executed & unprovable == set(), (
        "a platform cannot be both executed and declared unprovable: "
        f"{sorted(executed & unprovable)}"
    )
    assert executed | unprovable == {"macos-sandbox", "linux-sandbox"}, (
        "every platform must be either executed or declared unprovable; "
        "dropping one silently is how an un-run check comes to read as a pass"
    )
    assert all(
        release_gates.PLATFORM_CHECKS_UNAVAILABLE[name].strip() for name in unprovable
    ), "an unprovable platform must say why, or the declaration proves nothing"

    # And the workflow must not quietly re-add one. A matrix entry for a check
    # declared unprovable is the original defect coming back.
    jobs = _workflow("release.yml")["jobs"]
    modules = {
        entry["module"]
        for entry in jobs["platform-checks"]["strategy"]["matrix"]["include"]
    }
    for name in unprovable:
        module = f"harness.smoke.{name.replace('-', '_')}"
        assert module not in modules, (
            f"{module} is declared unprovable but release.yml still runs it; "
            "requiring an unproven check makes every release unreachable"
        )


def test_the_evidence_bundle_and_attestation_leave_the_attach_job():
    """Item 5 and 7. The bundle has to be an uploaded artifact rather than a file
    left in a runner's working directory, and the attestation must NOT travel
    through the draft it vouches for."""
    jobs = _workflow("release.yml")["jobs"]
    uploads = {
        (step.get("with") or {}).get("name"): (step.get("with") or {}).get("path")
        for step in jobs["attach"]["steps"]
        if "upload-artifact" in str(step.get("uses") or "")
    }
    assert "release-evidence" in uploads
    assert "stage-attestation" in uploads

    downloads = {
        (step.get("with") or {}).get("name")
        for step in jobs["finalize"]["steps"]
        if "download-artifact" in str(step.get("uses") or "")
    }
    assert "stage-attestation" in downloads, (
        "finalize must consume the attestation out of band; reading the draft's "
        "own SHA256SUMS is a document vouching for itself"
    )
    publish = " ".join(str(step.get("run") or "") for step in jobs["finalize"]["steps"])
    assert "--attestation" in publish
