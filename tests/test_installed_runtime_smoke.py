"""Refusals for the installed-runtime science probe.

The probe itself launches a real daemon, so its happy path is proven by an
actual run on a machine that can execute the runtime — that run is recorded in
the release evidence, not manufactured here. What this module locks down is
the half of the probe that must never pass by accident: every way a report can
look like evidence while missing the chain. A second Cell that lost the kernel
state, a CSV that was never captured, bytes that disagree with their digest,
a reopen that serves something else, a shutdown that left a process, an old
schema, or a runtime that imported the host source checkout are each refused.

The last group drives the pieces that touch real process state — the shutdown
escalation and the process-group witness — with real child processes this file
owns, so the cleanup rule is exercised rather than described.
"""

from __future__ import annotations

import copy
import hashlib
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts import installed_runtime_smoke as smoke
from scripts.installed_runtime_smoke import SmokeError

SHA = "a" * 40
PACKAGE_SHA = "b" * 64
CSV_SHA = hashlib.sha256(smoke.EXPECTED_CSV_BYTES).hexdigest()


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _cells() -> list[dict]:
    """The two Cell facts exactly as the production response would produce them."""
    return [
        smoke.cell_facts(
            {
                "status": "completed",
                "execution_id": "repl-one",
                "cell": {
                    "status": "ok",
                    "error": None,
                    "stdout": "cell-one shared_value=42\n",
                    "cell_index": 1,
                    "generation_id": "generation-1",
                    "files_written": [],
                },
            },
            label="cell-one",
        ),
        smoke.cell_facts(
            {
                "status": "completed",
                "execution_id": "repl-two",
                "cell": {
                    "status": "ok",
                    "error": None,
                    "stdout": "cell-two shared_value=43\n",
                    "cell_index": 2,
                    "generation_id": "generation-1",
                    "files_written": ["smoke.csv"],
                },
            },
            label="cell-two",
        ),
    ]


def _good_report() -> dict:
    steps = {
        "boot": {"status": "ok", "frame_id": "f-1", "notebook_repl": True},
        "execution": {
            "status": "ok",
            "cells": _cells(),
            "shared_state": True,
            "same_kernel_generation": True,
        },
        "capture": {
            "status": "ok",
            "artifact_id": "a-1",
            "version_id": "v-1",
            "filename": "smoke.csv",
            "sha256": CSV_SHA,
            "size_bytes": len(smoke.EXPECTED_CSV_BYTES),
            "version_url": "/api/v1/artifacts/versions/v-1",
            "bytes_match_metadata": True,
            "cell_ids": ["c-1", "c-2"],
        },
        "reopen": {
            "status": "ok",
            "version_id": "v-1",
            "bytes_identical": True,
            "digest_identical": True,
            "history_entries": 2,
            "version_still_latest": True,
        },
        "cleanup": {
            "status": "ok",
            "first": {
                "attempted": True,
                "daemon_exited": True,
                "workers_exited": True,
                "cli_returncode": 0,
                "note": "",
            },
            "final": {
                "attempted": True,
                "daemon_exited": True,
                "workers_exited": True,
                "cli_returncode": 0,
                "note": "",
            },
        },
    }
    return smoke.build_report(
        package={
            "kind": "bundle",
            "name": "OpenAI4S-0.3.1-linux-x86_64",
            "sha256": PACKAGE_SHA,
            "source_sha": SHA,
        },
        platform_facts={
            "os": "linux",
            "arch": "x86_64",
            "host_os": "linux",
            "host_arch": "x86_64",
            "executable_here": True,
            "python": "3.13.0",
        },
        runtime_facts={
            "interpreter": "/tmp/bundle/runtime/bin/python3",
            "prefix": "/tmp/bundle/runtime",
            "interpreter_version": "3.13.0",
            "version": "0.3.1",
            "module_file": "/tmp/bundle/src/openai4s/__init__.py",
            "inside_package": True,
            "isolation": "-P",
        },
        steps=steps,
        diagnostics=[],
        elapsed_s=12.5,
        budgets={"startup": 90.0, "cell": 60.0, "stop": 30.0, "total": 420.0},
    )


def _mutated(mutate) -> dict:
    document = copy.deepcopy(_good_report())
    mutate(document)
    return document


def _set(document: dict, *path: str, value):
    target = document
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value


# --------------------------------------------------------------------------- #
# The verdict, and its baseline
# --------------------------------------------------------------------------- #
def test_a_complete_runtime_smoke_report_is_accepted():
    document = _good_report()
    assert document["status"] == smoke.PASSED
    assert document["scenario_version"] == smoke.SMOKE_SCENARIO_VERSION
    smoke.validate_smoke_report(
        document,
        expected_package_sha256=PACKAGE_SHA,
        expected_source_sha=SHA,
        expected_kind="bundle",
        expected_platform=("linux", "x86_64"),
    )


def test_the_verdict_is_derived_from_the_steps_not_asserted():
    document = _mutated(lambda d: d["steps"].pop("reopen"))
    rebuilt = smoke.build_report(
        package=document["package"],
        platform_facts=document["platform"],
        runtime_facts=document["runtime"],
        steps=document["steps"],
        diagnostics=[],
        elapsed_s=1.0,
        budgets={},
    )
    assert rebuilt["status"] == smoke.FAILED


def test_a_not_run_report_names_the_missing_run_and_is_not_evidence():
    document = smoke.build_not_run_report(
        package={"kind": "bundle", "name": "OpenAI4S-0.3.1-linux-x86_64", "sha256": ""},
        platform_facts={
            "os": "linux",
            "arch": "x86_64",
            "host_os": "darwin",
            "host_arch": "aarch64",
        },
        reason="a linux-x86_64 bundle cannot be executed on darwin/aarch64",
    )
    assert document["status"] == smoke.NOT_RUN
    with pytest.raises(SmokeError, match="not_run.*linux-x86_64"):
        smoke.validate_smoke_report(document)


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: _set(d, "status", value=smoke.FAILED), id="failed"),
        pytest.param(lambda d: _set(d, "schema_version", value=0), id="old-schema"),
        pytest.param(lambda d: _set(d, "scenario_version", value=0), id="old-scenario"),
        pytest.param(lambda d: d["steps"].pop("cleanup"), id="missing-cleanup-step"),
        pytest.param(
            lambda d: _set(d, "steps", "capture", "status", value="failed"),
            id="failed-step",
        ),
        pytest.param(
            lambda d: _set(d, "runtime", "inside_package", value=False),
            id="loaded-host-source",
        ),
        pytest.param(
            lambda d: _set(d, "platform", "arch", value="aarch64"),
            id="other-architecture",
        ),
    ],
)
def test_a_mutated_record_is_refused(mutate):
    document = _mutated(mutate)
    with pytest.raises(SmokeError):
        smoke.validate_smoke_report(document, expected_platform=("linux", "x86_64"))


def test_a_report_bound_to_another_artifact_is_refused():
    with pytest.raises(SmokeError, match="not the artifact being released"):
        smoke.validate_smoke_report(_good_report(), expected_package_sha256="c" * 64)


def test_a_report_bound_to_another_source_is_refused():
    with pytest.raises(SmokeError, match="not the frozen"):
        smoke.validate_smoke_report(_good_report(), expected_source_sha="d" * 40)


def test_a_wheel_report_cannot_stand_in_for_a_bundle():
    with pytest.raises(SmokeError, match="not 'wheel'"):
        smoke.validate_smoke_report(_good_report(), expected_kind="wheel")


# --------------------------------------------------------------------------- #
# Execution: a Cell that failed is not a Cell that ran
# --------------------------------------------------------------------------- #
def test_a_second_cell_that_lost_the_kernel_state_is_refused():
    payload = {
        "status": "completed",
        "execution_id": "repl-two",
        "cell": {
            "status": "error",
            "error": "NameError: name 'shared_value' is not defined",
            "stdout": "",
        },
    }
    with pytest.raises(SmokeError, match="NameError.*shared_value"):
        smoke.cell_facts(payload, label="cell-two")


def test_a_completed_http_response_without_a_cell_is_not_an_execution():
    with pytest.raises(SmokeError, match="no executed Cell"):
        smoke.cell_facts({"status": "accepted", "execution_id": "x"}, label="cell")


def test_a_refused_repl_call_is_refused():
    with pytest.raises(SmokeError, match="notebook REPL is disabled"):
        smoke.cell_facts(
            {"error": "notebook REPL is disabled; send a message to resume the agent"},
            label="cell",
        )


def test_cells_that_do_not_share_state_are_refused():
    document = _mutated(
        lambda d: _set(d, "steps", "execution", "shared_state", value=False)
    )
    with pytest.raises(SmokeError, match="did not share one kernel state"):
        smoke.validate_smoke_report(document)


def test_the_cell_chain_requires_the_second_cell_to_have_used_the_first():
    """Two HTTP 200s are not evidence; the printed arithmetic is."""
    cells = _cells()
    facts = smoke.cell_chain_facts(cells)
    assert facts["shared_state"] is True
    assert facts["same_kernel_generation"] is True
    with pytest.raises(SmokeError, match="did not carry the variable"):
        smoke.cell_chain_facts(
            [cells[0], {**cells[1], "stdout": "cell-two shared_value=42\n"}]
        )
    with pytest.raises(SmokeError, match="two-Cell chain"):
        smoke.cell_chain_facts(cells[:1])


def test_the_second_cell_error_is_refused_by_the_record_too():
    document = _mutated(
        lambda d: _set(
            d,
            "steps",
            "execution",
            "cells",
            value=[
                _cells()[0],
                {**_cells()[1], "status": "error", "error": "NameError"},
            ],
        )
    )
    with pytest.raises(SmokeError, match="Cell 2 did not execute successfully"):
        smoke.validate_smoke_report(document)


def test_a_generation_reported_for_one_cell_only_is_not_claimed_as_shared():
    assert (
        smoke.same_generation({"generation_id": "g"}, {"generation_id": None}) is None
    )
    assert (
        smoke.same_generation({"generation_id": None}, {"generation_id": None}) is None
    )
    assert smoke.same_generation({"generation_id": "g"}, {"generation_id": "g"}) is True
    assert (
        smoke.same_generation({"generation_id": "g"}, {"generation_id": "h"}) is False
    )


# --------------------------------------------------------------------------- #
# Capture: only the production path counts
# --------------------------------------------------------------------------- #
class _FakeApi:
    """A canned HTTP surface for the capture/reopen helpers."""

    def __init__(
        self,
        *,
        entries=None,
        versions=None,
        artifacts=None,
        body: bytes = smoke.EXPECTED_CSV_BYTES,
    ) -> None:
        self.entries = entries if entries is not None else []
        self.versions = versions or []
        self.artifacts = artifacts or []
        self.body = body
        self.calls: list[tuple[str, str]] = []

    def json(self, method, path, body=None, *, timeout=None):
        self.calls.append((method, path))
        if path.endswith("/execution-log"):
            return {"entries": self.entries}
        if path.endswith("/versions"):
            return {"versions": self.versions}
        if path.endswith("/artifacts"):
            return self.artifacts
        raise AssertionError(f"unexpected call {method} {path}")

    def download(self, path, *, timeout=None):
        self.calls.append(("GET", path))
        return self.body


def _entry(*, filename: str = "smoke.csv", checksum: str = CSV_SHA):
    binding = {
        "filename": filename,
        "artifact_id": "a-1",
        "version_id": "v-1",
        "url": "/api/v1/artifacts/versions/v-1",
    }
    return {
        "producing_cell_id": "c-2",
        "status": "ok",
        "error": "",
        "source": smoke.CELL_TWO_CODE,
        "output_artifacts": [binding],
        "files_written": [filename],
    }


def _entries(*, filename: str = "smoke.csv") -> list[dict]:
    return [
        {
            "producing_cell_id": "c-1",
            "status": "ok",
            "error": "",
            "source": smoke.CELL_ONE_CODE,
            "output_artifacts": [],
            "files_written": [],
        },
        _entry(filename=filename),
    ]


def test_the_capture_helper_reads_the_version_through_its_exact_link():
    api = _FakeApi(
        entries=_entries(),
        versions=[
            {
                "version_id": "v-1",
                "checksum": CSV_SHA,
                "size_bytes": len(smoke.EXPECTED_CSV_BYTES),
            }
        ],
    )
    captured = smoke.read_capture(api, "f-1", _entries())
    assert captured["artifact_id"] == "a-1"
    assert captured["version_id"] == "v-1"
    assert captured["sha256"] == CSV_SHA
    assert captured["bytes_match_metadata"] is True
    assert ("GET", "/api/v1/artifacts/versions/v-1") in api.calls


def test_an_uncaptured_csv_is_refused():
    api = _FakeApi(entries=_entries(), versions=[])
    entry = _entry()
    entry["output_artifacts"] = []
    with pytest.raises(SmokeError, match="captured 0"):
        smoke.read_capture(api, "f-1", [_entries()[0], entry])


def test_captured_bytes_that_do_not_match_their_digest_are_refused():
    api = _FakeApi(
        entries=_entries(),
        versions=[
            {
                "version_id": "v-1",
                "checksum": CSV_SHA,
                "size_bytes": len(smoke.EXPECTED_CSV_BYTES),
            }
        ],
        body=b"shared,value\nanswer,44\n",
    )
    with pytest.raises(SmokeError, match="do not match the version checksum"):
        smoke.read_capture(api, "f-1", _entries())


def test_a_captured_artifact_with_no_digest_is_refused():
    api = _FakeApi(
        entries=_entries(),
        versions=[{"version_id": "v-1", "checksum": "", "size_bytes": 23}],
    )
    with pytest.raises(SmokeError, match="no SHA-256 checksum"):
        smoke.read_capture(api, "f-1", _entries())


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda d: _set(d, "steps", "capture", "artifact_id", value=""),
            id="no-artifact",
        ),
        pytest.param(
            lambda d: _set(d, "steps", "capture", "version_id", value=""),
            id="no-version",
        ),
        pytest.param(
            lambda d: _set(d, "steps", "capture", "sha256", value=""),
            id="no-digest",
        ),
        pytest.param(
            lambda d: _set(d, "steps", "capture", "bytes_match_metadata", value=False),
            id="digest-mismatch",
        ),
        pytest.param(
            lambda d: _set(d, "steps", "capture", "size_bytes", value=0),
            id="empty-artifact",
        ),
        pytest.param(
            lambda d: _set(d, "steps", "capture", "filename", value="other.csv"),
            id="wrong-filename",
        ),
    ],
)
def test_a_capture_that_did_not_happen_is_refused(mutate):
    with pytest.raises(SmokeError):
        smoke.validate_smoke_report(_mutated(mutate))


# --------------------------------------------------------------------------- #
# Reopen: the same version, from a fresh process
# --------------------------------------------------------------------------- #
def _reopen_api(*, entries=None, checksum: str = CSV_SHA, body=None) -> _FakeApi:
    return _FakeApi(
        entries=entries if entries is not None else _entries(),
        versions=[
            {
                "version_id": "v-1",
                "checksum": checksum,
                "size_bytes": len(smoke.EXPECTED_CSV_BYTES),
            }
        ],
        artifacts=[{"artifact_id": "a-1", "version_id": "v-1"}],
        body=body if body is not None else smoke.EXPECTED_CSV_BYTES,
    )


def _capture() -> dict:
    return {
        "artifact_id": "a-1",
        "version_id": "v-1",
        "sha256": CSV_SHA,
        "version_url": "/api/v1/artifacts/versions/v-1",
        "cell_ids": ["c-1", "c-2"],
    }


def test_the_reopen_helper_reads_the_same_bytes_and_history_back():
    reopened = smoke.read_back(_reopen_api(), "f-1", _capture())
    assert reopened["bytes_identical"] is True
    assert reopened["digest_identical"] is True
    assert reopened["history_entries"] == 2
    assert reopened["version_still_latest"] is True


def test_a_reopen_with_a_lost_history_entry_is_refused():
    api = _reopen_api(entries=[_entries()[0]])
    with pytest.raises(SmokeError, match="expected 2"):
        smoke.read_back(api, "f-1", _capture())


def test_a_reopen_serving_different_bytes_is_refused():
    api = _reopen_api(body=b"shared,value\nanswer,44\n")
    with pytest.raises(SmokeError, match="different bytes"):
        smoke.read_back(api, "f-1", _capture())


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda d: _set(d, "steps", "reopen", "version_id", value="v-2"),
            id="other-version",
        ),
        pytest.param(
            lambda d: _set(d, "steps", "reopen", "bytes_identical", value=False),
            id="other-bytes",
        ),
        pytest.param(
            lambda d: _set(d, "steps", "reopen", "digest_identical", value=False),
            id="other-digest",
        ),
        pytest.param(
            lambda d: _set(d, "steps", "reopen", "history_entries", value=1),
            id="partial-history",
        ),
        pytest.param(
            lambda d: _set(d, "steps", "reopen", "version_still_latest", value=False),
            id="not-latest",
        ),
    ],
)
def test_a_reopen_that_proved_nothing_is_refused(mutate):
    with pytest.raises(SmokeError):
        smoke.validate_smoke_report(_mutated(mutate))


# --------------------------------------------------------------------------- #
# Cleanup: a leaked process is a failed probe
# --------------------------------------------------------------------------- #
def test_cleanup_requires_both_shutdowns_to_have_really_happened():
    step = smoke.cleanup_step(
        {
            "first": {"attempted": True, "daemon_exited": True, "workers_exited": True},
            "final": {"attempted": True, "daemon_exited": True, "workers_exited": True},
        },
        daemons_started=2,
    )
    assert step["status"] == "ok"
    step = smoke.cleanup_step(
        {
            "first": {"attempted": True, "daemon_exited": True, "workers_exited": True},
            "final": {
                "attempted": True,
                "daemon_exited": False,
                "workers_exited": False,
            },
        },
        daemons_started=2,
    )
    assert step["status"] == "failed"


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda d: _set(
                d, "steps", "cleanup", "final", "daemon_exited", value=False
            ),
            id="daemon-left-running",
        ),
        pytest.param(
            lambda d: _set(
                d, "steps", "cleanup", "first", "workers_exited", value=False
            ),
            id="worker-left-running",
        ),
        pytest.param(
            lambda d: d["steps"]["cleanup"].pop("final"), id="no-final-shutdown"
        ),
    ],
)
def test_a_shutdown_that_leaked_a_process_is_refused(mutate):
    with pytest.raises(SmokeError):
        smoke.validate_smoke_report(_mutated(mutate))


# --------------------------------------------------------------------------- #
# Real process-state helpers
# --------------------------------------------------------------------------- #
def test_stop_escalates_for_a_daemon_that_ignores_sigterm(tmp_path):
    """A daemon that never exits must be killed, and reported, not waited on."""

    class _NoopCli:
        """Stands in for the product's own `openai4s stop`."""

        def cli(self, *arguments):
            if arguments == ("stop",):
                return [sys.executable, "-c", "pass"]
            return [sys.executable, "-c", "raise SystemExit(0)"]

        def child_environment(self, base):
            return dict(base)

    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal, time\n"
            "signal.signal(signal.SIGTERM, lambda *a: None)\n"
            "time.sleep(60)\n",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        daemon = smoke.Daemon(
            runtime=_NoopCli(),
            env={},
            cwd=tmp_path,
            log_path=tmp_path / "daemon.log",
            port=1,
        )
        daemon.proc = proc
        daemon.pgid = proc.pid
        result = daemon.stop(0.3)
        assert result["attempted"] is True
        assert result["cli_returncode"] == 0
        assert result["daemon_exited"] is True, "the ignored SIGTERM daemon survived"
        assert result["workers_exited"] is True
        assert "kept running" in result["note"]
    finally:
        if proc.poll() is None:  # pragma: no cover - only on a broken probe
            proc.kill()
            proc.wait()


def test_a_process_group_holding_a_worker_is_detected_then_clears(tmp_path):
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal, time\n"
            "signal.signal(signal.SIGTERM, lambda *a: None)\n"
            "time.sleep(60)\n",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    daemon = smoke.Daemon(
        runtime=object(),
        env={},
        cwd=tmp_path,
        log_path=tmp_path / "daemon.log",
        port=1,
    )
    daemon.pgid = proc.pid
    try:
        assert daemon._wait_for_process_group_exit(grace_s=0.2) is False
        proc.kill()
        proc.wait()
        assert daemon._wait_for_process_group_exit(grace_s=5.0) is True
    finally:
        if proc.poll() is None:  # pragma: no cover - only on a broken probe
            proc.kill()
            proc.wait()


def test_free_loopback_port_is_bindable_on_the_loopback_interface():
    port = smoke.free_loopback_port()
    assert 1024 <= port <= 65535
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", port))


# --------------------------------------------------------------------------- #
# Environment, redaction and report hygiene
# --------------------------------------------------------------------------- #
def test_the_daemon_environment_inherits_no_credentials_or_import_paths(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("OPENAI4S_LLM_API_KEY", "sk-should-not-cross")
    monkeypatch.setenv("OPENAI4S_CLAUDE_API_KEY", "sk-also-not")
    monkeypatch.setenv("PYTHONPATH", "/some/source/checkout")
    env = smoke.clean_environment(
        data_dir=tmp_path / "data",
        host="127.0.0.1",
        port=7777,
        home=tmp_path / "home",
        temp_dir=tmp_path / "tmp",
    )
    assert "OPENAI4S_LLM_API_KEY" not in env
    assert "OPENAI4S_CLAUDE_API_KEY" not in env
    assert "PYTHONPATH" not in env
    assert env["OPENAI4S_DATA_DIR"] == str(tmp_path / "data")
    assert env["OPENAI4S_NOTEBOOK_REPL"] == "1"
    assert env["OPENAI4S_SECRET_STORE"] == "plaintext"

    with_pythonpath = smoke.clean_environment(
        data_dir=tmp_path / "data",
        host="127.0.0.1",
        port=7777,
        home=tmp_path / "home",
        temp_dir=tmp_path / "tmp",
        pythonpath=tmp_path / "bundle" / "src",
    )
    assert with_pythonpath["PYTHONPATH"] == str(tmp_path / "bundle" / "src")


def test_the_probe_log_never_carries_the_access_token(tmp_path):
    log = tmp_path / "daemon.log"
    log.write_text(
        "sign in: http://127.0.0.1:8760/?token=SECRET-VALUE\n"
        "header X-OpenAI4S-Token: SECRET-VALUE\n",
        "utf-8",
    )
    daemon = smoke.Daemon(
        runtime=object(),
        env={},
        cwd=tmp_path,
        log_path=log,
        port=1,
    )
    daemon.token = "SECRET-VALUE"
    tail = daemon.log_tail()
    assert "SECRET-VALUE" not in tail
    assert "<redacted-token>" in tail


def test_redact_token_removes_the_value_and_the_query_form():
    text = "failed at http://127.0.0.1:1/?token=abc123&x=1 (X-OpenAI4S-Token: abc123)"
    redacted = smoke.redact_token(text, "abc123")
    assert "abc123" not in redacted
    assert redacted.count("<redacted-token>") == 2


def test_diagnostics_are_bounded():
    huge = "x" * (smoke.DIAGNOSTIC_LIMIT + 10)
    clipped = smoke.bounded(huge)
    assert len(clipped) > smoke.DIAGNOSTIC_LIMIT
    assert "more characters" in clipped
    assert smoke.bounded("short") == "short"


# --------------------------------------------------------------------------- #
# Platform rules
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("package_os", "arch", "host_os", "host_arch", "runs"),
    [
        ("linux", "x86_64", "linux", "x86_64", True),
        ("linux", "aarch64", "linux", "aarch64", True),
        ("linux", "x86_64", "linux", "amd64", True),
        ("linux", "aarch64", "linux", "arm64", True),
        ("linux", "x86_64", "darwin", "aarch64", False),
        ("linux", "x86_64", "linux", "aarch64", False),
        ("linux", "aarch64", "linux", "x86_64", False),
        ("linux", "x86_64", "win32", "amd64", False),
    ],
)
def test_host_can_execute_only_claims_a_native_runtime(
    package_os, arch, host_os, host_arch, runs
):
    assert (
        smoke.host_can_execute(package_os, arch, host_os=host_os, host_arch=host_arch)
        is runs
    )


def test_a_bundle_directory_name_carries_its_architecture():
    assert smoke.package_arch_from_name("OpenAI4S-0.3.1-linux-x86_64") == "x86_64"
    assert smoke.package_arch_from_name("OpenAI4S-1.2.3-linux-aarch64") == "aarch64"
    for name in ("OpenAI4S-0.3.1-linux-riscv", "openai4s-linux-x86_64", "random"):
        with pytest.raises(SmokeError, match="OpenAI4S-<version>-linux-<arch>"):
            smoke.package_arch_from_name(name)


def test_a_foreign_architecture_bundle_is_recorded_not_run(monkeypatch, tmp_path):
    app = tmp_path / "OpenAI4S-0.3.1-linux-x86_64"
    app.mkdir()
    monkeypatch.setattr("platform.system", lambda: "darwin")
    monkeypatch.setattr("platform.machine", lambda: "arm64")
    output = tmp_path / "smoke.json"
    exit_code = smoke.main(["--bundle", str(app), "--output", str(output)])
    assert exit_code == smoke.EXIT_NOT_RUN
    document = json.loads(output.read_text("utf-8"))
    assert document["status"] == smoke.NOT_RUN
    assert "linux-x86_64" in document["not_run_reason"]
    assert document["runtime"] == {}
    with pytest.raises(SmokeError, match="not_run"):
        smoke.validate_smoke_report(document)


# --------------------------------------------------------------------------- #
# CLI failure paths
# --------------------------------------------------------------------------- #
def test_a_runtime_interpreter_that_does_not_exist_fails_the_probe(tmp_path):
    output = tmp_path / "smoke.json"
    exit_code = smoke.main(
        [
            "--python",
            str(tmp_path / "no-such-python"),
            "--output",
            str(output),
        ]
    )
    assert exit_code == smoke.EXIT_FAILED
    document = json.loads(output.read_text("utf-8"))
    assert document["status"] == smoke.FAILED
    assert document["diagnostics"], "a failed probe must leave a diagnostic"
    with pytest.raises(SmokeError, match="status is 'failed'"):
        smoke.validate_smoke_report(document)


def test_cli_usage_errors_are_reported_as_usage_failures(tmp_path):
    assert (
        smoke.main(["--bundle", str(tmp_path)]) == smoke.EXIT_USAGE
    ), "a missing --output must not be mistaken for a probe result"


def test_the_probe_work_directory_contains_a_space(tmp_path):
    """The packaged runtime has to survive a path with spaces, like a user's."""
    options = smoke.build_parser().parse_args(
        ["--python", sys.executable, "--output", str(tmp_path / "out.json")]
    )
    probe = smoke.Probe(options)
    try:
        assert " " in probe.work_root.name
    finally:
        import shutil

        shutil.rmtree(probe.work_root, ignore_errors=True)
