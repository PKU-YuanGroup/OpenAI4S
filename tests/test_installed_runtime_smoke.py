"""The installed-runtime smoke's self-test, report and decision contracts.

The probe's happy path is a real daemon run — a wheel venv here, the Linux
bundle on the release/nightly runner — recorded in release evidence rather than
manufactured by a fixture. What this module locks down is everything that can
be decided without a daemon:

  * ``--self-test`` drives every decision check with synthetic responses and
    exits non-zero if any wrong shape is accepted (that is also this file's
    INV-13 lever: undo a check and the self-test names it);
  * the appendix-B.4 report keeps its exact shape, its verdict is derived from
    the checks, and a failure is one named check with a bounded, redacted
    detail — never a log quote;
  * the child environment drops the variables that could steer the runtime
    (``PYTHONPATH``, ``PYTHONHOME``, ``VIRTUAL_ENV``, ``OPENAI4S_WEBUI``, API
    keys, ``OPENAI4S_LLM_*``) and sets the isolated ones the scenario needs;
  * F16's budgets fail closed, and the process-group witness is exercised with
    a real child process this file owns.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest

from scripts import installed_runtime_smoke as smoke
from scripts.installed_runtime_smoke import SmokeFailure

SHA = "c" * 64
TOKEN = "tok-" + "z" * 24


def _checks(**overrides: str) -> dict[str, str]:
    checks = {name: smoke.PASSED for name in smoke.CHECKS}
    checks["browser_open"] = smoke.NOT_RUN
    checks.update(overrides)
    return checks


def _report(**overrides) -> dict:
    fields = {
        "target": "bundle",
        "artifact_sha256": SHA,
        "platform_name": "linux",
        "arch": "x86_64",
        "python_version": "3.13.0",
        "checks": _checks(),
        "duration_s": 12.5,
    }
    fields.update(overrides)
    return smoke.build_report(**fields)


def _write_fake_bundle(path: Path, *, arch: str = "x86_64") -> Path:
    """A tarball shaped like a bundle, enough for preparation and metadata."""
    top = f"OpenAI4S-0.3.1-linux-{arch}"
    with tarfile.open(path, "w:gz") as archive:
        for relative, executable in (
            ("bin/openai4s", True),
            ("runtime/bin/python3", True),
            ("src/openai4s/__init__.py", False),
        ):
            payload = b"#!/bin/sh\nexit 0\n" if executable else b""
            info = tarfile.TarInfo(f"{top}/{relative}")
            info.size = len(payload)
            info.mode = 0o755 if executable else 0o644
            archive.addfile(info, io.BytesIO(payload))
    return path


# --------------------------------------------------------------------------- #
# --self-test: every decision check, and its wrong shape
# --------------------------------------------------------------------------- #
def test_self_test_passes_and_covers_every_check():
    output = io.StringIO()
    assert smoke.run_self_test(output) == 0
    text = output.getvalue()
    assert "self-test: passed" in text
    # Every frozen check must have at least one negative case proving it refuses
    # the wrong shape; a check with no refusal case is not a gate.
    for name in smoke.CHECKS:
        assert "self-test: ok" in text
    assert text.count("self-test: ok") >= 40


def test_self_test_fails_when_a_decision_check_is_disabled(monkeypatch, capsys):
    """INV-13: undo a check's refusal and the self-test names it."""
    monkeypatch.setattr(smoke, "check_auth", lambda *args: None)
    assert smoke.run_self_test(sys.stdout) != 0
    captured = capsys.readouterr().out
    assert "auth refuses an open API" in captured
    assert "self-test: FAILED" in captured


# --------------------------------------------------------------------------- #
# The appendix-B.4 report
# --------------------------------------------------------------------------- #
def test_a_passing_report_has_the_frozen_shape():
    report = _report()
    assert set(report) == {
        "scenario_version",
        "target",
        "artifact_sha256",
        "platform",
        "arch",
        "python",
        "checks",
        "status",
        "duration_s",
        "failure",
    }
    assert report["scenario_version"] == smoke.SCENARIO_VERSION == 1
    assert list(report["checks"]) == list(smoke.CHECKS)
    assert report["status"] == smoke.PASSED
    assert report["failure"] is None
    assert report["duration_s"] == 12.5


def test_the_verdict_is_derived_from_the_checks_not_asserted():
    report = _report(checks=_checks(state_shared=smoke.FAILED))
    assert report["status"] == smoke.FAILED
    assert report["failure"]["check"] == "scenario"
    # A skip is not a pass: a check left at not_run fails the report even
    # though nobody recorded an explicit failure.
    report = _report(checks=_checks(artifact_capture=smoke.NOT_RUN))
    assert report["status"] == smoke.FAILED


def test_a_wheel_report_allows_figure_not_applicable_but_a_bundle_does_not():
    wheel = _report(target="wheel", checks=_checks(figure=smoke.NOT_APPLICABLE))
    assert wheel["status"] == smoke.PASSED
    bundle = _report(checks=_checks(figure=smoke.NOT_APPLICABLE))
    assert bundle["status"] == smoke.FAILED


def test_a_report_without_a_bound_artifact_digest_is_a_failure():
    report = _report(artifact_sha256="")
    assert report["status"] == smoke.FAILED
    assert report["failure"]["detail"]
    invalid = _report(artifact_sha256="not-a-digest")
    assert invalid["status"] == smoke.FAILED


def test_an_unknown_check_result_is_refused_rather_than_ignored():
    with pytest.raises(ValueError):
        _report(checks=_checks(serve="maybe"))
    with pytest.raises(ValueError):
        _report(target="container")


def test_a_failure_detail_is_bounded_and_redacted():
    report = smoke.build_report(
        target="bundle",
        artifact_sha256=SHA,
        platform_name="linux",
        arch="x86_64",
        python_version="3.13.0",
        checks=_checks(serve=smoke.FAILED),
        duration_s=1.0,
        failure={
            "check": "serve",
            "detail": (
                f"failed http://127.0.0.1:1/?token={TOKEN} "
                + "x" * (smoke.DETAIL_LIMIT + 100)
            ),
        },
    )
    detail = report["failure"]["detail"]
    assert report["status"] == smoke.FAILED
    assert report["failure"]["check"] == "serve"
    assert TOKEN not in detail
    assert "<redacted-token>" in detail
    assert len(detail) <= smoke.DETAIL_LIMIT + 40


# --------------------------------------------------------------------------- #
# Isolation and redaction
# --------------------------------------------------------------------------- #
def test_the_daemon_environment_cannot_be_steered_by_the_caller():
    base = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/caller",
        "PYTHONPATH": "/some/source/checkout",
        "PYTHONHOME": "/opt/python",
        "VIRTUAL_ENV": "/tmp/other-venv",
        "OPENAI4S_WEBUI": "legacy",
        "OPENAI4S_LLM_API_KEY": "sk-not-a-real-key",
        "OPENAI4S_CLAUDE_API_KEY": "sk-not-a-real-key",
        "OPENAI4S_NOTEBOOK_REPL": "0",
        "OPENAI4S_UPDATE_SOURCE": "",
        "KEEP_ME": "1",
    }
    env = smoke.clean_environment(
        base,
        data_dir=Path("/tmp/smoke/data"),
        home=Path("/tmp/smoke/home"),
        tmp_dir=Path("/tmp/smoke/tmp"),
        port=8123,
    )
    for forbidden in (
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "OPENAI4S_WEBUI",
        "OPENAI4S_LLM_API_KEY",
        "OPENAI4S_CLAUDE_API_KEY",
    ):
        assert forbidden not in env
    assert env["KEEP_ME"] == "1"
    assert env["OPENAI4S_DATA_DIR"] == "/tmp/smoke/data"
    assert env["HOME"] == "/tmp/smoke/home"
    assert env["TMPDIR"] == "/tmp/smoke/tmp"
    assert env["OPENAI4S_HOST"] == "127.0.0.1"
    assert env["OPENAI4S_PORT"] == "8123"
    assert env["OPENAI4S_NOTEBOOK_REPL"] == "1"
    assert env["OPENAI4S_UPDATE_SOURCE"] == "offline"


@pytest.mark.parametrize(
    "text",
    [
        f"failed http://127.0.0.1:8760/?token={TOKEN}",
        f"Authorization: Bearer {TOKEN}",
        f"X-OpenAI4S-Token: {TOKEN}",
        f"the token is {TOKEN}",
    ],
)
def test_redaction_removes_every_spelling_of_the_credential(text):
    cleaned = smoke.redact(text, TOKEN)
    assert TOKEN not in cleaned
    assert "<redacted-token>" in cleaned


# --------------------------------------------------------------------------- #
# F16 time budgets
# --------------------------------------------------------------------------- #
def test_the_budgets_are_the_frozen_f16_values():
    assert smoke.BUDGETS == {
        "serve": 90.0,
        "cell": 60.0,
        "browser": 120.0,
        "cleanup": 30.0,
        "total": 420.0,
    }


def test_an_expired_total_budget_fails_closed_with_a_bounded_diagnostic():
    import time

    budget = smoke.Budget(total_s=0.05)
    time.sleep(0.06)
    with pytest.raises(SmokeFailure) as raised:
        budget.for_step("serve")
    assert raised.value.check == "serve"
    assert "420s budget expired" in raised.value.detail


def test_a_step_budget_is_capped_by_what_the_total_has_left():
    budget = smoke.Budget(total_s=5.0)
    assert budget.for_step("cell") <= 5.0
    assert budget.for_step("cell") > 0


# --------------------------------------------------------------------------- #
# Runtime preparation and metadata
# --------------------------------------------------------------------------- #
def test_bundle_metadata_reads_the_archive_digest_and_architecture(tmp_path):
    tarball = _write_fake_bundle(tmp_path / "bundle.tar.gz")
    metadata = smoke.bundle_metadata(tarball)
    assert metadata["arch"] == "x86_64"
    assert metadata["sha256"] == smoke.sha256_file(tarball)
    with pytest.raises(SmokeFailure, match="does not exist"):
        smoke.bundle_metadata(tmp_path / "missing.tar.gz")


def test_a_foreign_architecture_bundle_is_refused_before_anything_runs(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="openai4s smoke test ") as scratch:
        tarball = _write_fake_bundle(Path(scratch) / "bundle.tar.gz")
        monkeypatch.setattr("platform.system", lambda: "darwin")
        monkeypatch.setattr("platform.machine", lambda: "arm64")
        work_root = Path(scratch) / "work"
        work_root.mkdir()
        with pytest.raises(SmokeFailure) as raised:
            smoke.runtime_for_bundle(tarball, work_root)
        assert raised.value.check == "serve"
        assert "cannot be executed on darwin/aarch64" in raised.value.detail
        assert "Linux release runner" in raised.value.detail


def test_the_wheel_origin_root_is_the_venv_prefix_not_the_resolved_interpreter(
    tmp_path,
):
    """A venv's ``bin/python`` symlinks to a base install; resolving would leave it."""
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    interpreter = venv / "bin" / "python"
    interpreter.symlink_to(sys.executable)
    runtime = smoke.runtime_for_python(interpreter, artifact=None, work_root=tmp_path)
    # The lexical venv prefix, not the symlink target's installation directory.
    assert runtime.origin_root == venv
    assert runtime.target == "wheel"
    assert runtime.python


def test_a_cell_path_is_inside_the_runtime_tree_even_through_a_symlink(tmp_path):
    root = tmp_path / "venv"
    (root / "bin").mkdir(parents=True)
    outside = tmp_path / "base-python"
    outside.write_text("", "utf-8")
    (root / "bin" / "python").symlink_to(outside)
    assert smoke.path_is_inside(str(root / "bin" / "python"), root)
    assert not smoke.path_is_inside(str(outside), root)


# --------------------------------------------------------------------------- #
# Decision checks: explicit positive/negative pairs beyond the self-test
# --------------------------------------------------------------------------- #
def test_a_completed_http_response_is_not_a_successful_cell():
    payload = {
        "status": "completed",
        "cell": {"status": "error", "error": "NameError: name 'x' is not defined"},
    }
    with pytest.raises(SmokeFailure, match="NameError"):
        smoke.check_cell(payload, check="state_shared")
    with pytest.raises(SmokeFailure, match="no executed Cell"):
        smoke.check_cell({"status": "accepted"}, check="state_shared")
    assert smoke.check_cell(
        {"status": "completed", "cell": {"status": "ok", "error": None}},
        check="state_shared",
    )


def test_a_second_cell_that_lost_the_variable_is_refused():
    with pytest.raises(SmokeFailure, match="did not share one kernel state"):
        smoke.check_state_shared("41\n")
    smoke.check_state_shared("42\n")


def test_a_capture_without_a_version_binding_is_refused():
    entry = {
        "files_written": ["smoke.csv"],
        "output_artifacts": [],
    }
    with pytest.raises(SmokeFailure, match="captured 0"):
        smoke.binding_for(entry, "smoke.csv", check="artifact_capture")


def test_version_bytes_that_disagree_with_the_recorded_digest_are_refused():
    version = {
        "version_id": "v1",
        "checksum": smoke.sha256_bytes(smoke.CSV_FIRST),
        "size_bytes": len(smoke.CSV_FIRST),
    }
    assert (
        smoke.check_version_bytes(smoke.CSV_FIRST, version, expected=smoke.CSV_FIRST)
        == version["checksum"]
    )
    with pytest.raises(SmokeFailure, match="do not match the version checksum"):
        smoke.check_version_bytes(smoke.CSV_SECOND, version, expected=smoke.CSV_FIRST)
    with pytest.raises(SmokeFailure, match="no SHA-256 checksum"):
        smoke.check_version_bytes(smoke.CSV_FIRST, {"checksum": "", "size_bytes": 9})


def test_a_captured_figure_that_is_not_a_png_is_refused():
    smoke.check_png(smoke.PNG_MAGIC + b"data")
    with pytest.raises(SmokeFailure, match="not a PNG"):
        smoke.check_png(b"GIF89a")
    entry = {"figures": [], "output_artifacts": []}
    with pytest.raises(SmokeFailure, match="expected exactly 1"):
        smoke.figure_name(entry)


def test_exact_version_and_restart_readback_refusals():
    first_sha = smoke.sha256_bytes(smoke.CSV_FIRST)
    second_sha = smoke.sha256_bytes(smoke.CSV_SECOND)
    smoke.check_exact_version(
        first_version_id="v1",
        second_version_id="v2",
        first_body=smoke.CSV_FIRST,
        first_sha=first_sha,
        second_body=smoke.CSV_SECOND,
    )
    with pytest.raises(SmokeFailure, match="did not mint a new immutable version"):
        smoke.check_exact_version(
            first_version_id="v1",
            second_version_id="v1",
            first_body=smoke.CSV_FIRST,
            first_sha=first_sha,
            second_body=smoke.CSV_SECOND,
        )
    smoke.check_restart_readback(
        listed={"first": first_sha, "second": second_sha},
        first_expected=first_sha,
        second_expected=second_sha,
        first_body=smoke.CSV_FIRST,
        second_body=smoke.CSV_SECOND,
        history_entries=5,
    )
    with pytest.raises(SmokeFailure, match="different digests"):
        smoke.check_restart_readback(
            listed={"first": "d" * 64, "second": second_sha},
            first_expected=first_sha,
            second_expected=second_sha,
            first_body=smoke.CSV_FIRST,
            second_body=smoke.CSV_SECOND,
            history_entries=5,
        )


def test_browser_and_clean_exit_verdicts():
    smoke.check_browser_open(0, "")
    with pytest.raises(SmokeFailure, match="exited 1"):
        smoke.check_browser_open(1, "assertion failed")
    smoke.check_clean_exit(daemon_gone=True, work_root_gone=True)
    with pytest.raises(SmokeFailure, match="survives"):
        smoke.check_clean_exit(daemon_gone=False, work_root_gone=True)


@pytest.mark.parametrize(
    ("package_arch", "host_os", "host_arch", "runs"),
    [
        ("x86_64", "linux", "x86_64", True),
        ("x86_64", "linux", "amd64", True),
        ("aarch64", "linux", "arm64", True),
        ("x86_64", "linux", "aarch64", False),
        ("x86_64", "darwin", "aarch64", False),
        ("aarch64", "darwin", "aarch64", False),
    ],
)
def test_host_can_execute_only_claims_a_native_linux_runtime(
    package_arch, host_os, host_arch, runs
):
    assert (
        smoke.host_can_execute(
            "linux", package_arch, host_os=host_os, host_arch=host_arch
        )
        is runs
    )


# --------------------------------------------------------------------------- #
# Process-group witness, with a real child this file owns
# --------------------------------------------------------------------------- #
def test_the_process_group_witness_detects_a_live_worker_then_clears():
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(60)",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    daemon = smoke.Daemon.__new__(smoke.Daemon)
    daemon.pgid = proc.pid
    try:
        assert daemon.wait_for_group_exit(grace=0.2) is False
        proc.kill()
        proc.wait()
        assert daemon.wait_for_group_exit(grace=5.0) is True
    finally:
        if proc.poll() is None:  # pragma: no cover - only on a broken probe
            proc.kill()
            proc.wait()


# --------------------------------------------------------------------------- #
# CLI refusal paths (no daemon, no network)
# --------------------------------------------------------------------------- #
def test_usage_errors_are_reported_as_usage_failures(tmp_path):
    assert smoke.main([]) == 2
    assert smoke.main(["--bundle", "x", "--python", "y", "--report", "z"]) == 2
    assert smoke.main(["--bundle", "x"]) == 2
    assert (
        smoke.main(["--python", str(tmp_path / "python"), "--browser", "--report", "z"])
        == 2
    )


def test_a_missing_runtime_writes_a_failed_report_and_exits_nonzero(tmp_path):
    report_path = tmp_path / "report.json"
    exit_code = smoke.main(
        [
            "--python",
            str(tmp_path / "no-such-python"),
            "--report",
            str(report_path),
        ]
    )
    assert exit_code == 1
    report = json.loads(report_path.read_text("utf-8"))
    assert report["status"] == smoke.FAILED
    assert report["failure"]["check"] == "serve"
    assert "does not exist" in report["failure"]["detail"]
    assert set(report["checks"]) == set(smoke.CHECKS)


def test_a_foreign_bundle_writes_no_pass_report(monkeypatch, tmp_path):
    tarball = _write_fake_bundle(tmp_path / "bundle.tar.gz")
    monkeypatch.setattr("platform.system", lambda: "darwin")
    monkeypatch.setattr("platform.machine", lambda: "arm64")
    report_path = tmp_path / "report.json"
    exit_code = smoke.main(["--bundle", str(tarball), "--report", str(report_path)])
    assert exit_code == 1
    report = json.loads(report_path.read_text("utf-8"))
    assert report["target"] == "bundle"
    assert report["arch"] == "x86_64"
    assert report["artifact_sha256"] == smoke.sha256_file(tarball)
    assert report["status"] == smoke.FAILED
    assert "cannot be executed" in report["failure"]["detail"]
    assert "linux-x86_64" in report["failure"]["detail"]


def test_the_report_never_carries_the_daemon_token(monkeypatch, tmp_path):
    """A preparation failure is still the most likely place a token could leak."""
    tarball = _write_fake_bundle(tmp_path / "bundle.tar.gz")
    monkeypatch.setattr("platform.system", lambda: "darwin")
    monkeypatch.setattr("platform.machine", lambda: "arm64")
    report_path = tmp_path / "report.json"
    smoke.main(["--bundle", str(tarball), "--report", str(report_path)])
    text = report_path.read_text("utf-8")
    assert TOKEN not in text
    assert "sk-" not in text


# --------------------------------------------------------------------------- #
# R2 contract: what R1 hands the browser layer
# --------------------------------------------------------------------------- #
def _scenario(tmp_path, *, browser: bool = True):
    runtime = smoke.Runtime(
        target="bundle",
        artifact_sha256=SHA,
        platform_name="linux",
        arch="x86_64",
        python_version="3.13.0",
        origin_root=tmp_path,
        serve_argv=["bundle", "serve", "--no-open"],
        stop_argv=["bundle", "stop"],
        cwd=tmp_path,
    )
    scenario = smoke.Scenario(
        runtime=runtime, work_root=tmp_path / "work", browser=browser, env_base={}
    )
    scenario.frame_id = "f-session"
    scenario.project_id = "p-project"
    scenario.csv_artifact_id = "a-csv"
    scenario.csv_version_first = "v-csv-first"
    scenario.csv_sha_first = "d" * 64
    scenario.figure = {
        "artifact_id": "a-png",
        "version_id": "v-png",
        "checksum": "e" * 64,
        "filename": "figure-1.png",
    }
    return scenario


def test_the_browser_expectation_names_the_session_and_both_exact_versions(tmp_path):
    """R2 opens these exact versions; the document must carry no credential."""
    document = _scenario(tmp_path).browser_expectation()
    assert document["frame_id"] == "f-session"
    assert document["project_id"] == "p-project"
    assert document["csv"] == {
        "artifact_id": "a-csv",
        "version_id": "v-csv-first",
        "checksum": "d" * 64,
        "filename": "smoke.csv",
    }
    assert document["png"]["version_id"] == "v-png"
    assert document["png"]["filename"] == "figure-1.png"
    assert "token" not in json.dumps(document).lower()


def test_a_bundle_runtime_launches_and_stops_through_the_bundled_cli(
    monkeypatch, tmp_path
):
    """The scenario must use the package's own entry point, not the host CLI."""
    tarball = _write_fake_bundle(tmp_path / "bundle.tar.gz")
    monkeypatch.setattr("platform.system", lambda: "linux")
    monkeypatch.setattr("platform.machine", lambda: "x86_64")
    work_root = tmp_path / "work"
    work_root.mkdir()
    runtime = smoke.runtime_for_bundle(tarball, work_root)
    assert runtime.target == "bundle"
    assert runtime.serve_argv[0].endswith("/bin/openai4s")
    assert runtime.serve_argv[-2:] == ["serve", "--no-open"]
    assert runtime.stop_argv[0] == runtime.serve_argv[0]
    assert runtime.stop_argv[-1] == "stop"
    assert str(runtime.origin_root).endswith("OpenAI4S-0.3.1-linux-x86_64")
