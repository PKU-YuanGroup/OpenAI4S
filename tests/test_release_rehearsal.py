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
# the truth. The Linux receipt carries one appendix-B.4 runtime-smoke report;
# it is bound to one tarball digest and one platform, every frozen check must
# have passed, and a report from another build or an older scenario cannot be
# borrowed.
# --------------------------------------------------------------------------

_TARBALL_SHA = "c" * 64


def _probe_report(**mutations):
    from tests._runtime_smoke import mutated_report

    return mutated_report(**mutations)


def test_a_passing_runtime_smoke_binds_to_the_tarball():
    document = release_receipts.normalize_runtime_smoke(
        _probe_report(), artifact_sha256=_TARBALL_SHA
    )
    assert document["scenario_version"] == 1
    assert document["target"] == "bundle"
    assert document["platform"] == "linux"
    assert document["arch"] == "x86_64"
    assert set(document["checks"]) == set(release_receipts.RUNTIME_SMOKE_CHECKS)
    assert document["failure"] is None


def test_a_report_from_an_older_scenario_cannot_be_borrowed():
    report = _probe_report()
    report["scenario_version"] = 0
    with pytest.raises(ReceiptError, match="scenario_version"):
        release_receipts.normalize_runtime_smoke(report, artifact_sha256=_TARBALL_SHA)


def test_a_runtime_smoke_for_another_platform_cannot_be_borrowed():
    report = _probe_report(arch="aarch64")
    with pytest.raises(ReceiptError, match="linux/x86_64"):
        release_receipts.normalize_runtime_smoke(report, artifact_sha256=_TARBALL_SHA)


def test_a_runtime_smoke_for_another_tarball_cannot_be_borrowed():
    report = _probe_report()
    report["artifact_sha256"] = "d" * 64
    with pytest.raises(ReceiptError, match="not the tarball"):
        release_receipts.normalize_runtime_smoke(report, artifact_sha256=_TARBALL_SHA)


def test_a_wheel_or_not_run_report_cannot_stand_in_for_the_linux_run():
    wheel = _probe_report()
    wheel["target"] = "wheel"
    wheel["checks"]["figure"] = "not_applicable"
    with pytest.raises(ReceiptError, match="bundle run"):
        release_receipts.normalize_runtime_smoke(wheel, artifact_sha256=_TARBALL_SHA)
    not_run = _probe_report()
    not_run["status"] = "not_run"
    with pytest.raises(ReceiptError, match="not run"):
        release_receipts.normalize_runtime_smoke(not_run, artifact_sha256=_TARBALL_SHA)


@pytest.mark.parametrize(
    "check", ["serve", "auth", "kernel_origin", "state_shared", "artifact_capture"]
)
def test_a_failed_check_is_refused(check):
    report = _probe_report()
    report["checks"][check] = "failed"
    with pytest.raises(ReceiptError, match=check):
        release_receipts.normalize_runtime_smoke(report, artifact_sha256=_TARBALL_SHA)


@pytest.mark.parametrize(
    "check",
    ["figure", "exact_version", "restart_readback", "browser_open", "clean_exit"],
)
def test_a_skipped_check_is_not_a_pass(check):
    """`figure`/`browser_open` may be not_applicable/not_run for a wheel report;
    a Linux package receipt must have actually run every check."""
    report = _probe_report()
    report["checks"][check] = "not_run"
    with pytest.raises(ReceiptError, match=check):
        release_receipts.normalize_runtime_smoke(report, artifact_sha256=_TARBALL_SHA)


def test_a_report_that_says_it_failed_while_status_is_passed_is_refused():
    report = _probe_report()
    report["failure"] = {"check": "serve", "detail": "x"}
    with pytest.raises(ReceiptError, match="reports a failure"):
        release_receipts.normalize_runtime_smoke(report, artifact_sha256=_TARBALL_SHA)


# --------------------------------------------------------------------------
# upgrade readback: the previous stable bundle's data, opened by this release
#
# A release after 1.0.1 must show that data the pinned previous Linux bundle
# wrote is migrated and read back by this tarball, and that the pre-migration
# copy still opens in the previous bundle. Each way the evidence can be
# missing or wrong is refused, and the refusals keep their causes apart.
# --------------------------------------------------------------------------

_CANDIDATE = "OpenAI4S-1.1.0-linux-x86_64.tar.gz"
#: The previous stable bundle the lead verified against v1.0.1's SHA256SUMS.
_PINNED = {
    "name": "OpenAI4S-1.0.1-linux-x86_64.tar.gz",
    "version": "1.0.1",
    "sha256": "026c753daf705a186dfc694e7c79f428e0395e27d9607c56d739746b4df4503b",
}


def _upgrade(**changes):
    from tests._runtime_smoke import passing_upgrade_readback

    document = passing_upgrade_readback(
        candidate_name=_CANDIDATE, candidate_sha256=_TARBALL_SHA
    )
    for path, value in changes.items():
        target = document
        parts = path.split(".")
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = value
    return document


def _bind(upgrade, *, name=_CANDIDATE, pin=None):
    report = _probe_report()
    if upgrade is not None:
        report["upgrade_readback"] = upgrade
    return release_receipts.normalize_runtime_smoke(
        report,
        artifact_sha256=_TARBALL_SHA,
        artifact_name=name,
        upgrade_baseline=pin,
    )


def test_the_previous_bundle_is_pinned_once_in_the_release_workflow():
    assert release_receipts.pinned_upgrade_baseline() == _PINNED


def test_the_smoke_and_the_receipt_name_the_same_upgrade_checks():
    from scripts import installed_runtime_smoke as smoke

    assert smoke.UPGRADE_CHECKS == release_receipts.UPGRADE_READBACK_CHECKS
    assert smoke.UPGRADE_SCENARIO_VERSION == (
        release_receipts.UPGRADE_READBACK_SCENARIO_VERSION
    )


_PIN_NAME = '  OPENAI4S_UPGRADE_BASELINE_NAME: "OpenAI4S-1.0.1-linux-x86_64.tar.gz"\n'
_PIN_VERSION = '  OPENAI4S_UPGRADE_BASELINE_VERSION: "1.0.1"\n'
_PIN_SHA = "  OPENAI4S_UPGRADE_BASELINE_SHA256: " + "a" * 64 + "\n"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        pytest.param("jobs: {}\n", "0 time", id="no-pin"),
        pytest.param(
            "env:\n" + _PIN_NAME + _PIN_VERSION + "  # " + _PIN_SHA.lstrip(),
            "SHA256 0 time",
            id="commented-out",
        ),
        pytest.param(
            "env:\n" + _PIN_NAME + _PIN_VERSION + _PIN_SHA + _PIN_SHA,
            "2 time",
            id="pinned-twice",
        ),
        pytest.param(
            "env:\n" + _PIN_NAME.replace("1.0.1", "1.0.0") + _PIN_VERSION + _PIN_SHA,
            "version 1.0.1 is",
            id="name-disagrees-with-version",
        ),
        pytest.param(
            "env:\n" + _PIN_NAME + _PIN_VERSION + _PIN_SHA.replace("a" * 64, "nope"),
            "no SHA-256",
            id="no-digest",
        ),
    ],
)
def test_an_unusable_pin_is_refused(tmp_path, text, message):
    workflow = tmp_path / "release.yml"
    workflow.write_text(text, encoding="utf-8")
    with pytest.raises(ReceiptError, match=message):
        release_receipts.pinned_upgrade_baseline(workflow)


def test_a_passing_upgrade_readback_is_kept_with_both_digests_and_the_records():
    document = _bind(_upgrade())["upgrade_readback"]
    assert document["status"] == "passed"
    assert document["baseline"] == {**_PINNED, "schema": 43}
    assert document["candidate"] == {
        "name": _CANDIDATE,
        "version": "1.1.0",
        "sha256": _TARBALL_SHA,
        "schema": 44,
    }
    assert set(document["checks"]) == set(release_receipts.UPGRADE_READBACK_CHECKS)
    records = document["records"]
    assert [row["version_id"] for row in records["csv_versions"]] == [
        "v-000000000001",
        "v-000000000002",
    ]
    assert all(len(row["sha256"]) == 64 for row in records["csv_versions"])
    assert records["history_entries"] == len(records["cell_ids"]) == 3
    assert document["failure"] is None


@pytest.mark.parametrize(
    "name",
    [
        _CANDIDATE,
        "OpenAI4S-1.0.2-linux-x86_64.tar.gz",
        # An unreadable version cannot prove it predates the gate.
        "OpenAI4S-1.1.0.dev0-linux-x86_64.tar.gz",
    ],
)
def test_a_release_after_the_first_baseline_must_carry_the_upgrade_readback(name):
    with pytest.raises(ReceiptError, match="carries no upgrade_readback"):
        _bind(None, name=name)


@pytest.mark.parametrize(
    "name",
    ["OpenAI4S-1.0.1-linux-x86_64.tar.gz", "OpenAI4S-0.2.0-linux-x86_64.tar.gz"],
)
def test_receipts_for_older_releases_stay_verifiable_without_it(name):
    assert "upgrade_readback" not in _bind(None, name=name)


@pytest.mark.parametrize(
    ("status", "message"),
    [
        pytest.param("baseline_unavailable", "was unavailable", id="baseline-missing"),
        pytest.param(
            "baseline_digest_mismatch", "is not the pinned one", id="wrong-digest"
        ),
        pytest.param("not_run", "was not run", id="not-run"),
    ],
)
def test_each_refusal_before_the_scenario_is_named_for_its_cause(status, message):
    from scripts import installed_runtime_smoke as smoke

    upgrade = _upgrade()
    declared = smoke.build_upgrade_report(
        baseline=upgrade["baseline"],
        candidate=upgrade["candidate"],
        checks={},
        records=None,
        duration_s=1.0,
        status=status,
        failure={"check": "baseline_digest", "detail": "detail from the run"},
    )
    with pytest.raises(ReceiptError, match=message) as raised:
        _bind(declared)
    assert "detail from the run" in str(raised.value)


def test_a_failed_upgrade_scenario_names_the_failed_check():
    upgrade = _upgrade(
        status="failed",
        failure={"check": "csv_versions", "detail": "version 2 was lost"},
    )
    upgrade["checks"]["csv_versions"] = "failed"
    with pytest.raises(ReceiptError, match="did not pass: csv_versions: version 2"):
        _bind(upgrade)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        pytest.param(
            {"candidate.sha256": "e" * 64}, "not the tarball", id="other-candidate"
        ),
        pytest.param(
            {
                "candidate.name": "OpenAI4S-1.1.1-linux-x86_64.tar.gz",
                "candidate.version": "1.1.1",
            },
            "not OpenAI4S-1.1.0",
            id="other-candidate-name",
        ),
        pytest.param(
            {"baseline.sha256": "f" * 64}, "sha256 'f", id="baseline-not-the-pin"
        ),
        pytest.param(
            {
                "baseline.name": "OpenAI4S-1.0.0-linux-x86_64.tar.gz",
                "baseline.version": "1.0.0",
            },
            "name 'OpenAI4S-1.0.0",
            id="older-unpinned-baseline",
        ),
        pytest.param(
            {"baseline.sha256": _TARBALL_SHA}, "candidate itself", id="self-upgrade"
        ),
        pytest.param(
            {"baseline.version": "1.0.2"}, "for that version", id="name-version-split"
        ),
        pytest.param({"baseline.schema": None}, "no previous schema", id="no-schema"),
        pytest.param(
            {"baseline.schema": 45}, "cannot go backwards", id="schema-backwards"
        ),
        pytest.param(
            {"checks.restore_backup": "not_run"},
            "restore_backup",
            id="restore-skipped",
        ),
        pytest.param(
            {"checks.baseline_readback": "failed"},
            "baseline_readback",
            id="old-bundle-cannot-read-the-copy",
        ),
        pytest.param(
            {"failure": {"check": "x", "detail": "y"}},
            "reports a failure",
            id="failed-while-passed",
        ),
        pytest.param(
            {"records.csv_versions": []}, "0 CSV version", id="no-history-versions"
        ),
        pytest.param({"scenario_version": 0}, "scenario_version", id="old-scenario"),
        pytest.param({"duration_s": None}, "no duration", id="no-duration"),
        pytest.param(
            {"records.backup_manifest_sha256": ""},
            "pre-migration copy",
            id="no-backup-digest",
        ),
        pytest.param(
            {"records.cell_ids": ["c-000000000001"]},
            "three Cells",
            id="lost-cells",
        ),
    ],
)
def test_upgrade_evidence_that_proves_nothing_is_refused(changes, message):
    with pytest.raises(ReceiptError, match=message):
        _bind(_upgrade(**changes))


def test_a_lost_history_version_yields_no_passing_receipt():
    """Report section 03: a lost CSV version must not reach a passing receipt,
    whether the record drops it or names the surviving one twice."""
    upgrade = _upgrade()
    survivor = upgrade["records"]["csv_versions"][1]
    upgrade["records"]["csv_versions"] = [survivor]
    with pytest.raises(ReceiptError, match="1 CSV version"):
        _bind(upgrade)
    upgrade["records"]["csv_versions"] = [survivor, dict(survivor)]
    with pytest.raises(ReceiptError, match="same CSV version twice"):
        _bind(upgrade)


def test_a_previous_bundle_newer_than_the_candidate_is_not_an_upgrade():
    pin = {**_PINNED, "name": "OpenAI4S-1.2.0-linux-x86_64.tar.gz", "version": "1.2.0"}
    upgrade = _upgrade(**{f"baseline.{key}": value for key, value in pin.items()})
    with pytest.raises(ReceiptError, match="not older than the candidate 1.1.0"):
        _bind(upgrade, pin=pin)


def test_a_same_version_rehearsal_is_kept_only_where_the_field_is_optional():
    """Rehearsing 1.0.1 against a rebuild of 1.0.1 proves the driver, not an
    upgrade: it may be recorded for 1.0.1 but cannot satisfy a later release."""
    rebuilt = "OpenAI4S-1.0.1-linux-x86_64.tar.gz"
    upgrade = _upgrade(**{"candidate.name": rebuilt, "candidate.version": "1.0.1"})
    assert _bind(upgrade, name=rebuilt)["upgrade_readback"]["status"] == "passed"
    later = "OpenAI4S-1.0.2-linux-x86_64.tar.gz"
    pin = {**_PINNED, "name": later, "version": "1.0.2"}
    same = _upgrade(
        **{f"baseline.{key}": value for key, value in pin.items()},
        **{"candidate.name": later, "candidate.version": "1.0.2"},
    )
    with pytest.raises(ReceiptError, match="not older than the candidate 1.0.2"):
        _bind(same, name=later, pin=pin)


def test_an_older_receipt_still_verifies_after_the_pin_moves_on():
    """The pin applies to the release it exists for. Once it names 1.1.0, the
    1.1.0 receipt (made against 1.0.1) still verifies; 1.1.1 must use 1.1.0."""
    moved = {
        "name": "OpenAI4S-1.1.0-linux-x86_64.tar.gz",
        "version": "1.1.0",
        "sha256": "9" * 64,
    }
    assert _bind(_upgrade(), pin=moved)["upgrade_readback"]["status"] == "passed"
    later = "OpenAI4S-1.1.1-linux-x86_64.tar.gz"
    upgrade = _upgrade(**{"candidate.name": later, "candidate.version": "1.1.1"})
    with pytest.raises(ReceiptError, match="not the pinned"):
        _bind(upgrade, name=later, pin=moved)
