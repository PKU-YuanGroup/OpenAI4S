"""Synthetic executed-runtime evidence for the release-receipt tests.

The probe's happy path is a real daemon run (recorded in release evidence); its
own refusals are covered by `test_installed_runtime_smoke.py`. What the receipt
tests need is a *well-formed* probe report they can bind, mutate and re-bind,
plus the browser-acceptance summary the R6 harness writes, so every receipt
refusal is exercised against the real document shapes rather than a hand-typed
guess at them.

Both builders are derived from the production constants (the probe's
`build_report`, the receipt module's `BUNDLE_SMOKE_*` names), so a schema bump
fails the fixtures instead of letting them keep passing against a shape nothing
produces any more.
"""

from __future__ import annotations

import hashlib

from scripts import installed_runtime_smoke as smoke
from scripts import release_receipts

#: Matches `tests/test_release_pipeline.py::FAKE_HEAD`, the SHA the fake
#: pipeline runner reports from `git rev-parse HEAD`.
FAKE_SOURCE_SHA = "a" * 40


def passing_report(
    *, package_sha256: str, arch: str = "x86_64", source_sha: str = FAKE_SOURCE_SHA
) -> dict:
    """A probe report that says the two-Cell science chain really ran."""
    cells = [
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
    csv_sha = hashlib.sha256(smoke.EXPECTED_CSV_BYTES).hexdigest()
    return smoke.build_report(
        package={
            "kind": "bundle",
            "name": f"OpenAI4S-0.2.0-linux-{arch}.tar.gz",
            "sha256": package_sha256,
            "source_sha": source_sha,
        },
        platform_facts={
            "os": "linux",
            "arch": arch,
            "host_os": "linux",
            "host_arch": arch,
            "executable_here": True,
            "python": "3.13.0",
        },
        runtime_facts={
            "interpreter": "/tmp/bundle/runtime/bin/python3",
            "prefix": "/tmp/bundle/runtime",
            "interpreter_version": "3.13.0",
            "version": "0.2.0",
            "module_file": "/tmp/bundle/src/openai4s/__init__.py",
            "inside_package": True,
            "isolation": "-P",
        },
        steps={
            "boot": {"status": "ok"},
            "execution": {
                "status": "ok",
                "cells": cells,
                "shared_state": True,
                "same_kernel_generation": True,
            },
            "capture": {
                "status": "ok",
                "artifact_id": "a-smoke",
                "version_id": "v-smoke",
                "filename": "smoke.csv",
                "sha256": csv_sha,
                "size_bytes": len(smoke.EXPECTED_CSV_BYTES),
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
        diagnostics=[],
        elapsed_s=11.0,
        budgets={"total": 420.0},
    )


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
        "checks": {name: True for name in release_receipts.BUNDLE_SMOKE_CHECKS},
        "artifact": {"version_id": "v-browser", "sha256": "c" * 64},
        "timings": {"total_s": 123.4},
        "diagnostics": [],
    }
