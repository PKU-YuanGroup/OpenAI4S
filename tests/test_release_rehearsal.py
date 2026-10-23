"""Non-publish rehearsal receipts, and the lies they must refuse.

A real `publish=false` run is a hosted-CI act this tree cannot perform. What
it can do is define the document that run must leave, and refuse every
mutation that would let a skipped check, a missing digest, a publish=true
dispatch, or a notarization omit read as a successful rehearsal.
"""

from __future__ import annotations

import json

import pytest

from scripts import release_gates, release_receipts
from scripts.release_gates import GateManifestError
from scripts.release_receipts import ReceiptError

SHA = "a" * 40
OTHER = "b" * 40


def _inputs(**overrides):
    payload = {
        "tag": "v0.2.0",
        "publish": False,
        "pypi_only": False,
        "macos_asset": "omit",
    }
    payload.update(overrides)
    return payload


def _notary(*, success=True, digest="c" * 64):
    if success:
        return {
            "requested": True,
            "submitted": True,
            "stapled": True,
            "stapler_returncode": 0,
            "spctl_returncode": 0,
            "post_staple_sha256": digest,
        }
    return {
        "requested": False,
        "submitted": False,
        "stapled": False,
        "stapler_returncode": None,
        "spctl_returncode": None,
        "post_staple_sha256": "",
    }


def test_a_faithful_rehearsal_is_accepted():
    inputs = release_receipts.verify_rehearsal(
        workflow_inputs=_inputs(),
        candidate_sha=SHA,
        expected_sha=SHA,
        workflow_run_id="9001",
        dmg_count=0,
        notary=None,
    )
    assert inputs["publish"] is False
    assert inputs["macos_asset"] == "omit"


def test_publish_true_cannot_masquerade_as_a_rehearsal():
    with pytest.raises(ReceiptError, match="publish=true"):
        release_receipts.verify_rehearsal(
            workflow_inputs=_inputs(publish=True),
            candidate_sha=SHA,
            expected_sha=SHA,
            workflow_run_id="9001",
            dmg_count=0,
            notary=None,
        )


def test_pypi_only_cannot_masquerade_as_a_rehearsal():
    with pytest.raises(ReceiptError, match="pypi_only"):
        release_receipts.verify_rehearsal(
            workflow_inputs=_inputs(pypi_only=True),
            candidate_sha=SHA,
            expected_sha=SHA,
            workflow_run_id="9001",
            dmg_count=0,
            notary=None,
        )


def test_a_rehearsal_for_another_commit_is_refused():
    with pytest.raises(ReceiptError, match="candidate"):
        release_receipts.verify_rehearsal(
            workflow_inputs=_inputs(),
            candidate_sha=OTHER,
            expected_sha=SHA,
            workflow_run_id="9001",
            dmg_count=0,
            notary=None,
        )


def test_a_rehearsal_with_no_workflow_run_id_is_refused():
    with pytest.raises(ReceiptError, match="workflow run id"):
        release_receipts.verify_rehearsal(
            workflow_inputs=_inputs(),
            candidate_sha=SHA,
            expected_sha=SHA,
            workflow_run_id="",
            dmg_count=0,
            notary=None,
        )


def test_macos_omit_cannot_carry_a_dmg():
    with pytest.raises(ReceiptError, match="cannot carry a DMG"):
        release_receipts.verify_rehearsal(
            workflow_inputs=_inputs(macos_asset="omit"),
            candidate_sha=SHA,
            expected_sha=SHA,
            workflow_run_id="9001",
            dmg_count=1,
            notary=_notary(success=True),
        )


def test_a_stapled_image_with_a_nonzero_spctl_assessment_is_still_notarized():
    """`describe_macos_image.py` records `spctl` as informational and derives
    `notarized` from the staple; this predicate must not answer differently
    for the same evidence, or one gate calls the image verified and the next
    refuses it."""
    notary = {
        "requested": True,
        "submitted": True,
        "stapled": True,
        "stapler_returncode": 0,
        "spctl_returncode": 3,
        "post_staple_sha256": "a" * 64,
    }
    assert release_receipts.notary_succeeded(notary) is True
    assert not release_receipts.notary_succeeded({**notary, "stapler_returncode": 65})
    assert not release_receipts.notary_succeeded({**notary, "post_staple_sha256": ""})


def test_no_notary_success_means_zero_dmgs():
    with pytest.raises(ReceiptError, match="notarization success"):
        release_receipts.verify_rehearsal(
            workflow_inputs=_inputs(macos_asset="notarized"),
            candidate_sha=SHA,
            expected_sha=SHA,
            workflow_run_id="9001",
            dmg_count=1,
            notary=_notary(success=False),
        )


def test_a_notarized_rehearsal_may_carry_a_dmg():
    release_receipts.verify_rehearsal(
        workflow_inputs=_inputs(macos_asset="notarized"),
        candidate_sha=SHA,
        expected_sha=SHA,
        workflow_run_id="9001",
        dmg_count=1,
        notary=_notary(success=True),
    )


def test_a_skipped_check_cannot_attest_the_candidate():
    listing = {
        "check_runs": [
            {
                "id": 1,
                "name": gate.check_name,
                "head_sha": SHA,
                "conclusion": (
                    "skipped" if gate.name == "ci-tests-py3.14" else "success"
                ),
                "started_at": "2026-09-01T00:00:00Z",
                "details_url": "https://example.invalid/run",
                "check_suite": {"id": 77},
            }
            for gate in release_gates.CHECK_SUITE_GATES
        ]
    }
    with pytest.raises(GateManifestError, match="skipped"):
        release_gates.attest_check_runs(listing, expected_sha=SHA)


def test_a_build_receipt_missing_its_digest_is_refused(tmp_path):
    wheel = tmp_path / "openai4s-0.2.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel-bytes")
    document = release_receipts.build_build_receipt(
        "dist",
        SHA,
        [wheel],
        workflow_run_id="9001",
        workflow_inputs=_inputs(),
    )
    document["artifacts"][0]["sha256"] = ""
    path = tmp_path / release_receipts.build_receipt_name("dist")
    path.write_text(json.dumps(document), "utf-8")
    with pytest.raises(ReceiptError, match="does not match its build"):
        release_receipts.verify_build_receipts(
            [path], expected_sha=SHA, assets_dir=tmp_path, required_kinds=("dist",)
        )


def test_old_schema_build_receipts_cannot_satisfy_the_new_gate(tmp_path):
    wheel = tmp_path / "openai4s-0.2.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel-bytes")
    document = release_receipts.build_build_receipt(
        "dist",
        SHA,
        [wheel],
        workflow_run_id="9001",
        workflow_inputs=_inputs(),
    )
    document["schema_version"] = 1
    path = tmp_path / release_receipts.build_receipt_name("dist")
    path.write_text(json.dumps(document), "utf-8")
    with pytest.raises(ReceiptError, match="schema_version"):
        release_receipts.verify_build_receipts(
            [path], expected_sha=SHA, assets_dir=tmp_path, required_kinds=("dist",)
        )


def test_linux_ci_passed_and_release_unproven_are_not_a_contradiction():
    checks = [
        {
            "name": "ci-linux-sandbox-full",
            "conclusion": "success",
            "check_run_id": "4242",
            "head_sha": SHA,
        }
    ]
    evidence = release_gates.build_linux_boundary(checks, [])
    assert evidence["ci_attestation"]["status"] == "passed"
    assert evidence["release_reexecution"]["status"] == "unproven"
    release_gates.verify_linux_boundary(evidence)


def test_linux_flat_status_is_a_contradiction():
    evidence = release_gates.build_linux_boundary(
        [
            {
                "name": "ci-linux-sandbox-full",
                "conclusion": "success",
                "check_run_id": "4242",
                "head_sha": SHA,
            }
        ],
        [],
    )
    evidence["status"] = "passed"
    with pytest.raises(GateManifestError, match="flat status"):
        release_gates.verify_linux_boundary(evidence)


def test_linux_flat_unproven_next_to_ci_passed_is_a_contradiction():
    evidence = release_gates.build_linux_boundary(
        [
            {
                "name": "ci-linux-sandbox-full",
                "conclusion": "success",
                "check_run_id": "4242",
                "head_sha": SHA,
            }
        ],
        [],
    )
    evidence["unproven"] = {
        "linux-sandbox": "the release workflow did not re-execute this smoke"
    }
    with pytest.raises(GateManifestError, match="passed and unproven"):
        release_gates.verify_linux_boundary(evidence)


def test_ci_attestation_cannot_use_unproven():
    evidence = release_gates.build_linux_boundary([], [])
    evidence["ci_attestation"]["status"] = "unproven"
    with pytest.raises(GateManifestError, match="ci_attestation cannot be unproven"):
        release_gates.verify_linux_boundary(evidence)


def test_release_reexecution_cannot_use_passed():
    evidence = release_gates.build_linux_boundary([], [])
    evidence["release_reexecution"]["status"] = "passed"
    with pytest.raises(GateManifestError, match="release_reexecution cannot be passed"):
        release_gates.verify_linux_boundary(evidence)


def test_a_quality_receipt_carries_the_structured_linux_boundary():
    document = release_gates.build_receipt(
        SHA,
        [
            {"name": gate.name, "command": list(gate.command), "returncode": 0}
            for gate in release_gates.LOCAL_GATES
        ],
        [
            {
                "name": gate.name,
                "check_name": gate.check_name,
                "check_run_id": f"{index + 1}",
                "run_id": "9",
                "url": "https://example.invalid/check",
                "conclusion": "success",
                "head_sha": SHA,
            }
            for index, gate in enumerate(release_gates.CHECK_SUITE_GATES)
        ],
    )
    boundary = document["linux_boundary"]
    assert boundary["ci_attestation"]["status"] == "passed"
    assert boundary["release_reexecution"]["status"] == "unproven"
    assert "passed" not in json.dumps(boundary["release_reexecution"])
    verified = release_gates.verify_receipt_document(document, expected_sha=SHA)
    assert verified["linux_boundary"]["ci_attestation"]["check_run_id"]


def test_stage_attestation_binds_candidate_run_and_inputs(tmp_path):
    wheel = tmp_path / "openai4s-0.2.0-py3-none-any.whl"
    wheel.write_bytes(b"bytes")
    document = release_receipts.build_stage_attestation(
        version="0.2.0",
        source_sha=SHA,
        assets=[wheel],
        workflow_run_id="9001",
        workflow_inputs=_inputs(),
        check_runs=[{"name": "ci-tests-py3.14", "check_run_id": "88", "head_sha": SHA}],
    )
    assert document["candidate_sha"] == SHA
    assert document["workflow_run_id"] == "9001"
    assert document["workflow_inputs"]["publish"] is False
    path = tmp_path / release_receipts.STAGE_ATTESTATION_NAME
    path.write_text(json.dumps(document), "utf-8")
    attested = release_receipts.verify_stage_attestation(path, version="0.2.0")
    assert attested[wheel.name]


def test_the_container_series_is_a_required_check_suite_gate():
    names = {gate.check_name for gate in release_gates.CHECK_SUITE_GATES}
    assert "Offline tests (py3.14)" in names
    assert any(
        gate.name == "ci-tests-py3.14" for gate in release_gates.CHECK_SUITE_GATES
    )


def test_stage_attestation_without_a_workflow_run_id_is_refused(tmp_path, monkeypatch):
    """The run-id requirement must not be satisfied by the candidate SHA.

    `verify_stage_attestation` once checked `workflow_run_id or source_sha`,
    and a schema-2 attestation always carries a source SHA, so an attestation
    binding no workflow run at all passed the gate that schema 2 exists to
    enforce -- while `verify_build_receipts` refused the same omission.
    """
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    wheel = tmp_path / "openai4s-0.2.0-py3-none-any.whl"
    wheel.write_bytes(b"bytes")
    document = release_receipts.build_stage_attestation(
        version="0.2.0", source_sha=SHA, assets=[wheel], workflow_run_id=""
    )
    assert document["workflow_run_id"] == ""
    target = tmp_path / release_receipts.STAGE_ATTESTATION_NAME
    target.write_text(json.dumps(document), "utf-8")
    with pytest.raises(release_receipts.ReceiptError, match="workflow run id"):
        release_receipts.verify_stage_attestation(target, version="0.2.0")


# --------------------------------------------------------------------------
# executed-runtime evidence is a receipt-level claim too
#
# A rehearsal may publish nothing, but the documents it leaves must still say
# the truth. These cases cover the two reports a Linux build receipt carries:
# the installed-runtime probe (`runtime_smoke`) and the browser acceptance
# (`bundle_smoke`). Each is bound to one package digest, one frozen source SHA
# and one platform; a report from another build cannot be borrowed.
# --------------------------------------------------------------------------

_TARBALL_SHA = "c" * 64
_OTHER_SHA = "d" * 40


def _probe_report(**overrides):
    from tests._runtime_smoke import passing_report

    return passing_report(
        package_sha256=_TARBALL_SHA,
        source_sha=SHA,
        **overrides,
    )


def test_a_passing_probe_report_binds_to_one_tarball_and_commit():
    document = release_receipts.normalize_runtime_smoke(
        _probe_report(),
        package_sha256=_TARBALL_SHA,
        source_sha=SHA,
        platform=("linux", "x86_64"),
    )
    assert document["status"] == "passed"
    assert document["steps"]["execution"]["shared_state"] is True


def test_a_probe_report_for_another_commit_cannot_be_borrowed():
    with pytest.raises(ReceiptError, match="not the frozen"):
        release_receipts.normalize_runtime_smoke(
            _probe_report(),
            package_sha256=_TARBALL_SHA,
            source_sha=_OTHER_SHA,
            platform=("linux", "x86_64"),
        )


def test_a_probe_report_from_an_older_scenario_cannot_be_borrowed():
    report = _probe_report()
    report["scenario_version"] = 0
    with pytest.raises(ReceiptError, match="scenario_version"):
        release_receipts.normalize_runtime_smoke(
            report,
            package_sha256=_TARBALL_SHA,
            source_sha=SHA,
            platform=("linux", "x86_64"),
        )


def test_a_probe_report_for_another_architecture_cannot_be_borrowed():
    report = _probe_report(arch="aarch64")
    with pytest.raises(ReceiptError, match="ran on linux/aarch64"):
        release_receipts.normalize_runtime_smoke(
            report,
            package_sha256=_TARBALL_SHA,
            source_sha=SHA,
            platform=("linux", "x86_64"),
        )


def test_the_browser_acceptance_must_record_every_scene_as_passed():
    from tests._runtime_smoke import bundle_report

    good = bundle_report(artifact_sha256=_TARBALL_SHA, source_sha=SHA)
    assert (
        release_receipts.normalize_bundle_smoke(
            good,
            artifact_sha256=_TARBALL_SHA,
            source_sha=SHA,
            platform=("linux", "x86_64"),
        )["status"]
        == "passed"
    )
    skipped = json.loads(json.dumps(good))
    skipped["checks"]["restart"] = False
    with pytest.raises(ReceiptError, match="restart"):
        release_receipts.normalize_bundle_smoke(
            skipped,
            artifact_sha256=_TARBALL_SHA,
            source_sha=SHA,
            platform=("linux", "x86_64"),
        )


def test_a_browser_acceptance_that_did_not_run_is_refused():
    from tests._runtime_smoke import bundle_report

    not_run = bundle_report(artifact_sha256=_TARBALL_SHA, source_sha=SHA)
    not_run["status"] = "not_run"
    not_run["not_run_reason"] = "no browser on this runner"
    with pytest.raises(ReceiptError, match="not_run"):
        release_receipts.normalize_bundle_smoke(
            not_run,
            artifact_sha256=_TARBALL_SHA,
            source_sha=SHA,
            platform=("linux", "x86_64"),
        )


def test_a_browser_acceptance_for_another_tarball_is_refused():
    from tests._runtime_smoke import bundle_report

    other = bundle_report(artifact_sha256="e" * 64, source_sha=SHA)
    with pytest.raises(ReceiptError, match="not the tarball being released"):
        release_receipts.normalize_bundle_smoke(
            other,
            artifact_sha256=_TARBALL_SHA,
            source_sha=SHA,
            platform=("linux", "x86_64"),
        )


def test_a_browser_acceptance_without_the_packaged_matplotlib_is_refused():
    from tests._runtime_smoke import bundle_report

    report = bundle_report(artifact_sha256=_TARBALL_SHA, source_sha=SHA)
    report["figure_tool"] = "stdlib-png"
    with pytest.raises(ReceiptError, match="packaged matplotlib"):
        release_receipts.normalize_bundle_smoke(
            report,
            artifact_sha256=_TARBALL_SHA,
            source_sha=SHA,
            platform=("linux", "x86_64"),
        )


def test_a_browser_acceptance_must_echo_the_frozen_time_budgets():
    from tests._runtime_smoke import bundle_report

    report = bundle_report(artifact_sha256=_TARBALL_SHA, source_sha=SHA)
    report["timings"]["budgets_s"]["cell"] = 120
    with pytest.raises(ReceiptError, match="cell budget"):
        release_receipts.normalize_bundle_smoke(
            report,
            artifact_sha256=_TARBALL_SHA,
            source_sha=SHA,
            platform=("linux", "x86_64"),
        )


def test_a_browser_acceptance_that_blew_the_total_budget_is_refused():
    from tests._runtime_smoke import bundle_report

    report = bundle_report(artifact_sha256=_TARBALL_SHA, source_sha=SHA)
    report["timings"]["total_s"] = 421
    with pytest.raises(ReceiptError, match="total budget"):
        release_receipts.normalize_bundle_smoke(
            report,
            artifact_sha256=_TARBALL_SHA,
            source_sha=SHA,
            platform=("linux", "x86_64"),
        )


def test_a_browser_acceptance_must_record_the_daemon_environment():
    from tests._runtime_smoke import bundle_report

    report = bundle_report(artifact_sha256=_TARBALL_SHA, source_sha=SHA)
    report["posture"]["daemon_environment"].remove("OPENAI4S_NOTEBOOK_REPL")
    with pytest.raises(ReceiptError, match="OPENAI4S_NOTEBOOK_REPL"):
        release_receipts.normalize_bundle_smoke(
            report,
            artifact_sha256=_TARBALL_SHA,
            source_sha=SHA,
            platform=("linux", "x86_64"),
        )


def test_a_browser_acceptance_whose_diagnostics_leaked_a_token_is_refused():
    from tests._runtime_smoke import bundle_report

    report = bundle_report(artifact_sha256=_TARBALL_SHA, source_sha=SHA)
    report["diagnostics"] = ["sign in: http://127.0.0.1:8760/?token=live-secret-value"]
    with pytest.raises(ReceiptError, match="credential-shaped"):
        release_receipts.normalize_bundle_smoke(
            report,
            artifact_sha256=_TARBALL_SHA,
            source_sha=SHA,
            platform=("linux", "x86_64"),
        )
