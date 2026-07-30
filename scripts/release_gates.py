#!/usr/bin/env python3
"""The canonical release quality-gate manifest, and the receipt bound to it.

One definition, imported by both sides. `run_quality_gates.py` runs what this
module lists; `release_pipeline.verify_quality_receipt` requires the receipt to
describe exactly this list. Before this module existed the producer owned a
`GATES` tuple and the consumer read nothing but exit codes, so the two could not
be compared at all:

    {"format": "openai4s-quality-receipt", "source_sha": <right sha>,
     "gates": [{"name": "pytest", "command": ["pytest"], "returncode": 0}]}

...staged a release. One gate instead of eight, `pytest` instead of
`uv run pytest -q`, and nothing to notice either. The receipt recorded a *shape*
that reads as evidence and decides nothing — the same failure the receipt was
introduced to fix, one level up. The test fixture in
`tests/test_release_pipeline.py` was itself written that way, which is how the
gap stayed invisible: the suite demonstrated the hole rather than closing it.

So verification here is exact-match, and every way a receipt can differ from the
manifest is a hard failure: a missing gate, a duplicated gate, an unknown gate, a
substituted command, a different schema version, a different manifest digest.
"Set of names is a superset" and "all recorded exit codes were zero" are both
satisfiable by a forgery.

Two kinds of gate:

`local`
    Run by the release quality job at the release SHA, in-process. The receipt
    records the exit code.
`check_suite`
    Proven by GitHub's own check runs *at that exact SHA* rather than re-executed.
    The browser matrix and the Python support matrix cost more than the rest of
    the release combined and already ran on the push to `main` that the tag
    points at, so re-running them would buy nothing but latency. What makes this
    evidence rather than an assumption is that the attestation is pinned to
    `head_sha`: a check run for a different commit does not count, and the
    check-run and workflow-run IDs are recorded so a reader can go look.

Deliberately *not* a gate: the nightly macOS/Linux sandbox jobs. They only run on
`schedule`/`workflow_dispatch`, so no check run for them exists at a release SHA
and requiring one would make every release unreachable. The release workflow runs
those checks itself, at the frozen SHA — see `PLATFORM_CHECK_COMMANDS`.
"""
from __future__ import annotations

import hashlib
import json
import platform
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

#: Bumped whenever the receipt's shape or the gate list changes. The consumer
#: requires an exact match rather than `>=`: an older producer cannot know about
#: a gate added later, so accepting its receipt would silently drop that gate.
SCHEMA_VERSION = 2

RECEIPT_FORMAT = "openai4s-quality-receipt"

#: Name of the receipt the quality job writes and staging verifies.
RECEIPT_NAME = "quality-receipt.json"

LOCAL_KIND = "local"
CHECK_SUITE_KIND = "check_suite"


class GateManifestError(Exception):
    """A receipt does not describe the canonical manifest. Do not release."""


@dataclass(frozen=True)
class Gate:
    name: str
    kind: str
    #: The exact argv for a `local` gate; empty for a `check_suite` gate.
    command: tuple[str, ...] = ()
    #: The GitHub check-run name for a `check_suite` gate; empty for `local`.
    check_name: str = ""

    def canonical(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "command": list(self.command),
            "check_name": self.check_name,
        }


#: The offline gates. Same set and same argv as the independent CI jobs — a
#: release that runs "the gates" where that quietly means fewer of them is the
#: original defect wearing a receipt.
LOCAL_GATES: tuple[Gate, ...] = (
    Gate("pre-commit", LOCAL_KIND, ("uv", "run", "pre-commit", "run", "--all-files")),
    Gate("mypy", LOCAL_KIND, ("uv", "run", "mypy")),
    Gate(
        "directory-readmes",
        LOCAL_KIND,
        ("uv", "run", "python", "scripts/check_directory_readmes.py"),
    ),
    Gate("pytest", LOCAL_KIND, ("uv", "run", "pytest", "-q")),
    Gate(
        "harness-pr",
        LOCAL_KIND,
        (
            "uv",
            "run",
            "python",
            "-m",
            "harness.cli",
            "run",
            "--tier",
            "pr",
            "--offline",
        ),
    ),
    Gate(
        "response-schemas",
        LOCAL_KIND,
        ("uv", "run", "python", "scripts/capture_response_schemas.py", "--check"),
    ),
    Gate(
        "response-contract",
        LOCAL_KIND,
        ("uv", "run", "python", "scripts/capture_response_contract.py", "--check"),
    ),
    Gate(
        "source-secret-scan",
        LOCAL_KIND,
        ("uv", "run", "python", "scripts/source_secret_scan.py"),
    ),
)

#: Proven by check runs at the exact release SHA. The names are the rendered
#: `jobs.<id>.name` values from `.github/workflows/ci.yml`, which is what GitHub
#: reports as the check-run name. `tests/test_release_gate_manifest.py` asserts
#: these against the workflow file so a renamed job cannot silently stop being
#: required.
CHECK_SUITE_GATES: tuple[Gate, ...] = (
    Gate("ci-lint", CHECK_SUITE_KIND, check_name="Lint (pre-commit)"),
    Gate(
        "ci-type-check",
        CHECK_SUITE_KIND,
        check_name="Type safety (core orchestration boundary)",
    ),
    Gate("ci-source-security", CHECK_SUITE_KIND, check_name="Source credential scan"),
    Gate(
        "ci-release-artifacts",
        CHECK_SUITE_KIND,
        check_name="Build and install wheel/sdist",
    ),
    Gate("ci-tests-py3.10", CHECK_SUITE_KIND, check_name="Offline tests (py3.10)"),
    Gate("ci-tests-py3.12", CHECK_SUITE_KIND, check_name="Offline tests (py3.12)"),
    Gate("ci-tests-py3.13", CHECK_SUITE_KIND, check_name="Offline tests (py3.13)"),
    Gate(
        "ci-browser-chromium",
        CHECK_SUITE_KIND,
        check_name="Browser workbench E2E (chromium)",
    ),
    Gate(
        "ci-browser-firefox",
        CHECK_SUITE_KIND,
        check_name="Browser workbench E2E (firefox)",
    ),
    Gate(
        "ci-browser-webkit",
        CHECK_SUITE_KIND,
        check_name="Browser workbench E2E (webkit)",
    ),
)

GATES: tuple[Gate, ...] = LOCAL_GATES + CHECK_SUITE_GATES

#: The platform checks the release workflow executes itself at the frozen SHA,
#: because no check run for them exists there. Keyed by the receipt row name.
PLATFORM_CHECK_COMMANDS: dict[str, tuple[str, ...]] = {
    "macos-sandbox": ("uv", "run", "python", "-m", "harness.smoke.macos_sandbox"),
    "linux-sandbox": ("uv", "run", "python", "-m", "harness.smoke.linux_sandbox"),
}


def local_gates() -> tuple[Gate, ...]:
    return LOCAL_GATES


def check_suite_gates() -> tuple[Gate, ...]:
    return CHECK_SUITE_GATES


def manifest() -> dict[str, Any]:
    """The manifest itself, as the receipt records it."""
    return {
        "schema_version": SCHEMA_VERSION,
        "gates": [gate.canonical() for gate in GATES],
    }


def manifest_digest() -> str:
    """A digest over the gate list, so drift is one comparison rather than N.

    Covers names, kinds, argv and check names in declaration order. A receipt
    carrying a digest that does not match this one was produced against a
    different manifest, whatever its rows happen to say.
    """
    payload = json.dumps(manifest(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def builder_facts() -> dict[str, Any]:
    """Who and what produced a receipt or an artifact.

    Recorded because "the gates passed" and "the gates passed on the platform
    and interpreter this artifact was built for" are different claims, and the
    release used to make the first while implying the second.
    """
    return {
        "os": platform.system(),
        "os_release": platform.release(),
        "arch": platform.machine(),
        "interpreter": sys.implementation.name,
        "interpreter_version": platform.python_version(),
    }


def _require_rows(
    rows: Any,
    expected: Sequence[Gate],
    *,
    label: str,
) -> dict[str, Mapping[str, Any]]:
    """Exact-match a receipt's rows against the manifest's gates for one kind.

    Every failure mode is separate and named, because "the receipt was wrong" is
    not actionable and each of these has a different cause: a stale producer, a
    forged document, a gate removed from the manifest but not the runner.
    """
    if not isinstance(rows, list):
        raise GateManifestError(f"quality receipt records no {label} list")
    seen: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise GateManifestError(f"a {label} row is not an object")
        name = str(row.get("name") or "")
        if not name:
            raise GateManifestError(f"a {label} row names no gate")
        if name in seen:
            # A duplicate is how a forgery pads a count back up to the expected
            # length after dropping a gate.
            raise GateManifestError(f"{label} {name!r} appears more than once")
        seen[name] = row

    wanted = {gate.name: gate for gate in expected}
    missing = sorted(set(wanted) - set(seen))
    if missing:
        raise GateManifestError(
            f"quality receipt is missing {label}(s): {', '.join(missing)}"
        )
    unknown = sorted(set(seen) - set(wanted))
    if unknown:
        raise GateManifestError(
            f"quality receipt records unknown {label}(s): {', '.join(unknown)}"
        )
    if len(rows) != len(expected):
        raise GateManifestError(
            f"quality receipt records {len(rows)} {label}(s), manifest has "
            f"{len(expected)}"
        )
    return seen


def verify_receipt_document(
    document: Any,
    *,
    expected_sha: str,
) -> dict[str, Any]:
    """Refuse everything about a receipt that is not proof.

    Raises `GateManifestError` rather than returning a verdict: every caller
    treats "cannot prove it" as "do not release".
    """
    if not isinstance(document, dict):
        raise GateManifestError("quality receipt is not an object")
    if document.get("format") != RECEIPT_FORMAT:
        raise GateManifestError("quality receipt has an unrecognised format")

    version = document.get("schema_version")
    if version != SCHEMA_VERSION:
        raise GateManifestError(
            f"quality receipt schema_version is {version!r}, this pipeline "
            f"requires {SCHEMA_VERSION}"
        )

    recorded_digest = str(document.get("manifest_digest") or "")
    if not recorded_digest:
        raise GateManifestError("quality receipt carries no manifest digest")
    if recorded_digest != manifest_digest():
        raise GateManifestError(
            f"quality receipt was produced against a different gate manifest "
            f"({recorded_digest[:12]} != {manifest_digest()[:12]})"
        )

    recorded_sha = str(document.get("source_sha") or "")
    if not recorded_sha:
        raise GateManifestError("quality receipt names no source SHA")
    if not expected_sha:
        raise GateManifestError(
            "cannot determine the source SHA being released, so a receipt "
            "cannot be bound to it"
        )
    if recorded_sha != expected_sha:
        raise GateManifestError(
            f"quality receipt is for {recorded_sha[:12]} but this release is "
            f"{expected_sha[:12]}; the gates did not run on these sources"
        )

    gate_rows = _require_rows(document.get("gates"), LOCAL_GATES, label="gate")
    for gate in LOCAL_GATES:
        row = gate_rows[gate.name]
        command = [str(part) for part in (row.get("command") or [])]
        if tuple(command) != gate.command:
            # The substitution case. A row saying it ran `true` and returned 0
            # is indistinguishable from a passing gate unless the argv is
            # compared to the manifest's.
            raise GateManifestError(
                f"gate {gate.name!r} recorded command {command!r}, manifest "
                f"declares {list(gate.command)!r}"
            )
        try:
            returncode = int(row.get("returncode"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise GateManifestError(
                f"gate {gate.name!r} records no usable exit code"
            ) from None
        if returncode != 0:
            raise GateManifestError(
                f"gate {gate.name!r} failed with exit code {returncode}"
            )

    check_rows = _require_rows(document.get("checks"), CHECK_SUITE_GATES, label="check")
    for gate in CHECK_SUITE_GATES:
        row = check_rows[gate.name]
        if str(row.get("check_name") or "") != gate.check_name:
            raise GateManifestError(
                f"check {gate.name!r} names {row.get('check_name')!r}, manifest "
                f"declares {gate.check_name!r}"
            )
        head = str(row.get("head_sha") or "")
        if head != expected_sha:
            # The whole value of the attestation. A successful check run for
            # some other commit says nothing about these sources.
            raise GateManifestError(
                f"check {gate.name!r} ran on {head[:12] or '<none>'}, not on "
                f"{expected_sha[:12]}"
            )
        conclusion = str(row.get("conclusion") or "")
        if conclusion != "success":
            raise GateManifestError(
                f"check {gate.name!r} concluded {conclusion or '<none>'!r}, "
                f"not success"
            )
        if not str(row.get("check_run_id") or ""):
            raise GateManifestError(
                f"check {gate.name!r} records no check-run id, so nobody can "
                f"go and look at it"
            )
    return document


def build_receipt(
    source_sha: str,
    gates: Iterable[Mapping[str, Any]],
    checks: Iterable[Mapping[str, Any]],
    *,
    platform_checks: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """The document a quality run leaves behind.

    Records what ran and what it returned, and makes no judgement — a receipt
    that decides it passed is a receipt that can flatter itself. It also records
    the manifest it was produced against, which is the part that lets the
    consumer detect a receipt from a different gate list rather than a receipt
    with fewer rows.
    """
    return {
        "format": RECEIPT_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "manifest_digest": manifest_digest(),
        "source_sha": str(source_sha),
        "builder": builder_facts(),
        "gates": [
            {
                "name": str(row["name"]),
                "command": list(row.get("command") or []),
                "returncode": int(row["returncode"]),
            }
            for row in gates
        ],
        "checks": [
            {
                "name": str(row["name"]),
                "check_name": str(row.get("check_name") or ""),
                "check_run_id": str(row.get("check_run_id") or ""),
                "run_id": str(row.get("run_id") or ""),
                "url": str(row.get("url") or ""),
                "conclusion": str(row.get("conclusion") or ""),
                "head_sha": str(row.get("head_sha") or ""),
            }
            for row in checks
        ],
        "platform_checks": [
            {
                "name": str(row["name"]),
                "command": list(row.get("command") or []),
                "returncode": int(row["returncode"]),
                "runner": dict(row.get("runner") or {}),
            }
            for row in platform_checks
        ],
    }


def attest_check_runs(
    payload: Any,
    *,
    expected_sha: str,
    required: Sequence[Gate] = CHECK_SUITE_GATES,
) -> list[dict[str, Any]]:
    """Turn GitHub's check-run listing into receipt rows, or refuse.

    Pure so it can be tested without a network: the caller fetches
    `repos/{repo}/commits/{sha}/check-runs` and hands the parsed JSON here.

    A check run only counts when it is the *latest* attempt for its name, it
    concluded `success`, and its `head_sha` is the commit being released. The
    last condition is the one that matters: without it this degrades into
    "some run somewhere was green".
    """
    if not isinstance(payload, Mapping):
        raise GateManifestError("the check-run listing was not an object")
    runs = payload.get("check_runs")
    if not isinstance(runs, list):
        raise GateManifestError("the check-run listing carries no check_runs")

    latest: dict[str, Mapping[str, Any]] = {}
    for run in runs:
        if not isinstance(run, Mapping):
            continue
        name = str(run.get("name") or "")
        if not name:
            continue
        previous = latest.get(name)
        if previous is None or str(run.get("started_at") or "") >= str(
            previous.get("started_at") or ""
        ):
            latest[name] = run

    rows: list[dict[str, Any]] = []
    problems: list[str] = []
    for gate in required:
        run = latest.get(gate.check_name)
        if run is None:
            problems.append(f"{gate.check_name!r}: no check run at this commit")
            continue
        head = str(run.get("head_sha") or "")
        conclusion = str(run.get("conclusion") or "")
        if head != expected_sha:
            problems.append(
                f"{gate.check_name!r}: ran on {head[:12] or '<none>'}, "
                f"not {expected_sha[:12]}"
            )
            continue
        if conclusion != "success":
            problems.append(f"{gate.check_name!r}: concluded {conclusion or '<none>'}")
            continue
        details = str(run.get("details_url") or run.get("html_url") or "")
        rows.append(
            {
                "name": gate.name,
                "check_name": gate.check_name,
                "check_run_id": str(run.get("id") or ""),
                # The workflow run, so a reader can find the whole suite rather
                # than one job.
                "run_id": str(
                    (run.get("check_suite") or {}).get("id")
                    if isinstance(run.get("check_suite"), Mapping)
                    else ""
                ),
                "url": details,
                "conclusion": conclusion,
                "head_sha": head,
            }
        )
    if problems:
        raise GateManifestError(
            "required checks are not green at this commit: " + "; ".join(problems)
        )
    return rows
