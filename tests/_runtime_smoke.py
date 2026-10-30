"""Synthetic appendix-B.4 runtime-smoke evidence for the release-receipt tests.

The probe's happy path is a real run (recorded in release evidence); its own
refusals are covered by `test_installed_runtime_smoke.py`. What the receipt tests
need is a *well-formed* report they can bind, mutate and re-bind, so every
receipt refusal is exercised against the real document shape rather than a
hand-typed guess at it.

The document is derived from the production constants (`RUNTIME_SMOKE_CHECKS`,
`RUNTIME_SMOKE_SCENARIO_VERSION`, `RUNTIME_SMOKE_PLATFORM`) and the artifact
digest under test, so a schema or scenario bump fails the fixture instead of
letting the receipt tests keep passing against a shape no producer writes.
"""

from __future__ import annotations

import copy

from scripts import release_receipts

#: Matches `tests/test_release_pipeline.py::FAKE_HEAD`, the SHA the fake
#: pipeline runner reports from `git rev-parse HEAD`.
FAKE_SOURCE_SHA = "a" * 40


def passing_report(*, artifact_sha256: str) -> dict:
    """A schema-3 runtime_smoke document in which every B.4 check passed."""
    return {
        "scenario_version": release_receipts.RUNTIME_SMOKE_SCENARIO_VERSION,
        "target": "bundle",
        "artifact_sha256": artifact_sha256,
        "platform": release_receipts.RUNTIME_SMOKE_PLATFORM[0],
        "arch": release_receipts.RUNTIME_SMOKE_PLATFORM[1],
        "python": "3.13.0",
        "checks": {name: "passed" for name in release_receipts.RUNTIME_SMOKE_CHECKS},
        "status": "passed",
        "duration_s": 11.0,
        "failure": None,
    }


def mutated_report(**mutations) -> dict:
    """A passing report with dotted paths replaced, for the refusal tests.

    ``mutated_report(**{"checks.figure": "not_applicable"})`` is the shape a
    wheel run produces; a Linux receipt must refuse it. Keys with a dot address
    one nested mapping; other keys are top-level fields.
    """
    document = passing_report(artifact_sha256="c" * 64)
    for path, value in mutations.items():
        target = document
        parts = path.split(".")
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = value
    return copy.deepcopy(document)
