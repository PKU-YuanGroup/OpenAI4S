"""Synthetic executed-runtime evidence for the release-receipt tests.

The probe's happy path is a real daemon run (recorded in release evidence); its
own refusals are covered by `test_installed_runtime_smoke.py`. What the receipt
tests need is a *well-formed* probe report they can bind, mutate and re-bind,
plus the browser-acceptance summary the R6 harness writes, so every receipt
refusal is exercised against the real document shapes rather than a hand-typed
guess at them.

`passing_report` describes the *previous* round's report shape on purpose: it
is what the transitional schema-3 receipt in this tree still validates, and R3
replaces both with the appendix-B.4 document. Keeping the fixture literal means
R1's rewrite of the probe cannot leave the receipt tests validating a shape no
producer writes. `bundle_report` stays derived from the receipt module's
`BUNDLE_SMOKE_*` constants, so a schema bump fails the fixture.
"""

from __future__ import annotations

import hashlib

from scripts import release_receipts

#: Matches `tests/test_release_pipeline.py::FAKE_HEAD`, the SHA the fake
#: pipeline runner reports from `git rev-parse HEAD`.
FAKE_SOURCE_SHA = "a" * 40


CSV_BYTES = b"shared,value\nanswer,43\n"


def _previous_cell(
    label: str, stdout: str, *, generation: str = "generation-1"
) -> dict:
    return {
        "status": "ok",
        "label": label,
        "execution_id": f"repl-{label}",
        "cell_status": "ok",
        "error": "",
        "stdout": stdout,
        "generation_id": generation,
        "cell_index": 1 if label == "cell-one" else 2,
        "files_written": [] if label == "cell-one" else ["smoke.csv"],
    }


def passing_report(
    *, package_sha256: str, arch: str = "x86_64", source_sha: str = FAKE_SOURCE_SHA
) -> dict:
    """The previous round's probe report shape, still validated by the receipt."""
    csv_sha = hashlib.sha256(CSV_BYTES).hexdigest()
    return {
        "format": "openai4s-installed-runtime-smoke",
        "schema_version": 1,
        "scenario_version": 1,
        "status": "passed",
        "package": {
            "kind": "bundle",
            "name": f"OpenAI4S-0.2.0-linux-{arch}.tar.gz",
            "sha256": package_sha256,
            "source_sha": source_sha,
        },
        "platform": {
            "os": "linux",
            "arch": arch,
            "host_os": "linux",
            "host_arch": arch,
            "executable_here": True,
            "python": "3.13.0",
        },
        "runtime": {
            "interpreter": "/tmp/bundle/runtime/bin/python3",
            "prefix": "/tmp/bundle/runtime",
            "interpreter_version": "3.13.0",
            "version": "0.2.0",
            "module_file": "/tmp/bundle/src/openai4s/__init__.py",
            "inside_package": True,
            "isolation": "-P",
        },
        "steps": {
            "boot": {"status": "ok"},
            "execution": {
                "status": "ok",
                "cells": [
                    _previous_cell("cell-one", "cell-one shared_value=42\n"),
                    _previous_cell("cell-two", "cell-two shared_value=43\n"),
                ],
                "shared_state": True,
                "same_kernel_generation": True,
            },
            "capture": {
                "status": "ok",
                "artifact_id": "a-smoke",
                "version_id": "v-smoke",
                "filename": "smoke.csv",
                "sha256": csv_sha,
                "size_bytes": len(CSV_BYTES),
                "version_url": "/api/v1/artifacts/versions/v-smoke",
                "bytes_match_metadata": True,
            },
            "reopen": {
                "status": "ok",
                "version_id": "v-smoke",
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
                },
                "final": {
                    "attempted": True,
                    "daemon_exited": True,
                    "workers_exited": True,
                },
            },
        },
        "diagnostics": [],
        "timing": {"elapsed_s": 11.0, "budgets_s": {"total": 420.0}},
    }


def bundle_report(
    *, artifact_sha256: str, arch: str = "x86_64", source_sha: str = FAKE_SOURCE_SHA
) -> dict:
    """The Linux browser acceptance summary the R6 harness writes."""
    return {
        "format": release_receipts.BUNDLE_SMOKE_FORMAT,
        "schema_version": release_receipts.BUNDLE_SMOKE_SCHEMA_VERSION,
        "scenario_version": release_receipts.BUNDLE_SMOKE_SCENARIO_VERSION,
        "status": "passed",
        "artifact_sha256": artifact_sha256,
        "source_sha": source_sha,
        "platform": {"os": "linux", "arch": arch},
        "package": {"kind": "bundle", "name": f"OpenAI4S-0.2.0-linux-{arch}.tar.gz"},
        "figure_tool": "matplotlib",
        "checks": {name: True for name in release_receipts.BUNDLE_SMOKE_CHECKS},
        "artifact": {"version_id": "v-browser", "sha256": "c" * 64},
        "timings": {
            "total_s": 123.4,
            "budgets_s": dict(release_receipts.BUNDLE_SMOKE_BUDGETS),
        },
        "posture": {
            "notebook_repl": "enabled for this run only",
            "kernel_sandbox": "off (the OS boundary has its own release gate)",
            "model_calls": "none",
            "daemon_environment": sorted(release_receipts.BUNDLE_SMOKE_DAEMON_ENV),
        },
        "diagnostics": [],
    }
