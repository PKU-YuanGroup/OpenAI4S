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

from scripts.release_pipeline import build_quality_receipt  # noqa: E402

#: The gates CI runs as independent jobs. Listed here so the release path runs
#: the same set rather than a subset that happens to be convenient -- a release
#: that runs "the gates" where that silently means fewer of them reproduces the
#: original defect one level up.
GATES: tuple[tuple[str, list[str]], ...] = (
    ("pre-commit", ["uv", "run", "pre-commit", "run", "--all-files"]),
    ("mypy", ["uv", "run", "mypy"]),
    (
        "directory-readmes",
        ["uv", "run", "python", "scripts/check_directory_readmes.py"],
    ),
    ("pytest", ["uv", "run", "pytest", "-q"]),
    (
        "harness-pr",
        [
            "uv",
            "run",
            "python",
            "-m",
            "harness.cli",
            "run",
            "--tier",
            "pr",
            "--offline",
        ],
    ),
    (
        "response-schemas",
        ["uv", "run", "python", "scripts/capture_response_schemas.py", "--check"],
    ),
    (
        "response-contract",
        ["uv", "run", "python", "scripts/capture_response_contract.py", "--check"],
    ),
    ("source-secret-scan", [sys.executable, "scripts/source_secret_scan.py"]),
)


def _head_sha() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True
    )
    if completed.returncode != 0:
        return ""
    return completed.stdout.decode().strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="quality-receipt.json")
    args = parser.parse_args()

    source_sha = _head_sha()
    if not source_sha:
        print("::error::cannot determine HEAD; a receipt would bind to nothing")
        return 2

    results: list[dict] = []
    for name, command in GATES:
        print(f"--- {name}: {' '.join(command)}", flush=True)
        completed = subprocess.run(command, cwd=ROOT)
        results.append(
            {"name": name, "command": command, "returncode": completed.returncode}
        )
        if completed.returncode != 0:
            print(f"::error::gate {name} failed ({completed.returncode})", flush=True)

    receipt = build_quality_receipt(source_sha, results)
    Path(args.output).write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    failed = [row["name"] for row in results if row["returncode"] != 0]
    print(f"receipt written for {source_sha[:12]} ({len(results)} gates)")
    if failed:
        print("failing gates: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
