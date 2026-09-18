#!/usr/bin/env python3
"""Run every offline gate and write a receipt bound to this checkout's SHA.

    uv run python scripts/run_quality_gates.py --output quality-receipt.json

The release pipeline used to answer "not run: the suite gated the build that
produced these artifacts" and pass. The build job runs no suite at all: it
checks out the tag, scans for secrets, builds, and verifies the wheel's
metadata. That sentence was the only thing standing between a release and the
claim that tests gated it, and it was false.

So the gates run here, at the release SHA, and leave a document saying which
ones ran and what they returned. The document makes no judgement -- it records
exit codes -- because a receipt that decides it passed is a receipt that can
flatter itself. `verify_quality_receipt` in the pipeline decides, and it
re-derives the SHA it is releasing rather than trusting the one written down.

The gate list itself lives in `scripts/release_gates.py`, imported by both this
producer and that consumer. It used to live here, privately, while the consumer
compared nothing but exit codes -- so a receipt naming one gate instead of eight,
with argv that never ran, was indistinguishable from a real one.

Two things this cannot run locally and does not pretend to: the browser matrix
and the Python support matrix. They already ran on the push to `main` that the
release tag points at, so `--head-sha` plus `--check-runs` attests to them from
GitHub's own check runs *at that exact commit* rather than re-executing an hour
of work. Every attested check must be green, must be the latest attempt, and
must name this commit as its `head_sha`.

Every gate runs even after one fails, so a red run reports everything that is
wrong rather than the first thing.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import release_gates  # noqa: E402
from scripts.release_gates import GateManifestError  # noqa: E402


def _head_sha() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True
    )
    if completed.returncode != 0:
        return ""
    return completed.stdout.decode().strip()


def _merge_check_run_pages(text: str) -> dict:
    """One `{"check_runs": [...]}` listing from every page `gh api` wrote.

    `gh api --paginate` on an object endpoint writes each page as its own JSON
    document, back to back; `--slurp` wraps the pages in one array instead. A
    single `json.loads` accepted neither once a commit had more than 100 check
    runs -- it raised "Extra data" on the second page, after every local gate
    had run -- so a release SHA that had collected a few nightly schedules and
    Dependabot runs could not produce a receipt at all.

    Every page is kept, in order, and `attest_check_runs` still picks the latest
    attempt per name across all of them. Anything that is not a sequence of page
    objects each carrying a `check_runs` list is refused rather than guessed at.
    """
    decoder = json.JSONDecoder()
    documents: list[object] = []
    index = 0
    while True:
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        try:
            document, index = decoder.raw_decode(text, index)
        except json.JSONDecodeError as error:
            raise GateManifestError(
                f"the check-run listing is not JSON: {error}"
            ) from None
        documents.append(document)

    pages: list[object] = []
    for document in documents:
        # `--slurp` is one array of pages; `--paginate` alone is a page per
        # document. Both reduce to the same ordered list of page objects.
        pages.extend(document if isinstance(document, list) else [document])
    if not pages:
        raise GateManifestError("the check-run listing is empty")

    runs: list[object] = []
    for number, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            raise GateManifestError(
                f"the check-run listing's page {number} is not an object"
            )
        page_runs = page.get("check_runs")
        if not isinstance(page_runs, list):
            raise GateManifestError(
                f"the check-run listing's page {number} carries no check_runs"
            )
        runs.extend(page_runs)
    return {"check_runs": runs}


def _attest(path: Path | None, source_sha: str) -> list[dict]:
    """Turn a saved `check-runs` listing into receipt rows.

    The workflow writes the listing with `gh api --paginate`; the page merge is
    `_merge_check_run_pages`, and every refusal about the checks themselves is
    `release_gates.attest_check_runs`. Both are pure and therefore testable
    without a network.
    """
    if path is None:
        raise GateManifestError(
            "no check-run listing was supplied, so the browser and Python "
            "support matrices cannot be attested for this commit. Pass "
            "--check-runs with the output of "
            "`gh api --paginate --slurp repos/{owner}/{repo}/commits/<sha>/check-runs`."
        )
    payload = _merge_check_run_pages(path.read_text("utf-8"))
    return release_gates.attest_check_runs(payload, expected_sha=source_sha)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=release_gates.RECEIPT_NAME)
    parser.add_argument(
        "--check-runs",
        type=Path,
        default=None,
        help="JSON from the commit check-runs API, for the attested gates",
    )
    parser.add_argument(
        "--platform-checks",
        type=Path,
        default=None,
        help="JSON array of platform-check rows produced by the release workflow",
    )
    args = parser.parse_args()

    source_sha = _head_sha()
    if not source_sha:
        print("::error::cannot determine HEAD; a receipt would bind to nothing")
        return 2

    results: list[dict] = []
    for gate in release_gates.LOCAL_GATES:
        command = list(gate.command)
        print(f"--- {gate.name}: {' '.join(command)}", flush=True)
        completed = subprocess.run(command, cwd=ROOT)
        results.append(
            {
                "name": gate.name,
                # The manifest's argv verbatim, not the argv as this process
                # happened to assemble it: the consumer compares them, so a
                # difference here would be an unexplainable release failure.
                "command": command,
                "returncode": completed.returncode,
            }
        )
        if completed.returncode != 0:
            print(
                f"::error::gate {gate.name} failed ({completed.returncode})", flush=True
            )

    platform_rows: list[dict] = []
    if args.platform_checks is not None:
        platform_rows = json.loads(args.platform_checks.read_text("utf-8"))

    try:
        checks = _attest(args.check_runs, source_sha)
    except (GateManifestError, OSError, ValueError) as error:
        # Fail closed and write nothing. A receipt missing its attested half
        # would be refused by staging anyway, and an unreadable half-receipt on
        # disk is worse than none: the next reader has to work out which it is.
        print(f"::error::{error}")
        return 2

    receipt = release_gates.build_receipt(
        source_sha, results, checks, platform_checks=platform_rows
    )
    Path(args.output).write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    failed = [row["name"] for row in results if row["returncode"] != 0]
    print(
        f"receipt written for {source_sha[:12]} "
        f"({len(results)} local gate(s), {len(checks)} attested check(s), "
        f"{len(platform_rows)} platform check(s))"
    )
    if failed:
        print("failing gates: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
