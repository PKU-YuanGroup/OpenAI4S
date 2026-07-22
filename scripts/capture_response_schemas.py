#!/usr/bin/env python3
"""Regenerate (or verify) the frozen response shapes in docs/response-schemas.json.

    python scripts/capture_response_schemas.py            # rewrite the artifact
    python scripts/capture_response_schemas.py --check     # fail on drift

Runs the offline suite with the capture installed and records what every route
actually returned. The suite is the corpus: routes it exercises get a schema,
routes it does not are reported as uncovered. That number is the point -- it
says how much of the HTTP surface is pinned, and it is meant to go up.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openai4s.server import contract, response_capture  # noqa: E402


def _run_suite(destination: Path) -> int:
    env = dict(os.environ)
    env["OPENAI4S_CAPTURE_SCHEMAS"] = str(destination)
    # Deliberately no -x. Stopping at the first failure truncates the capture,
    # and every route the run never reached would then be reported as "frozen
    # but no longer observed" -- drift that is really just an aborted run.
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "tests"],
        cwd=ROOT,
        env=env,
    )
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare against the committed artifact instead of rewriting it",
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        captured = Path(tmp) / "captured.json"
        code = _run_suite(captured)
        if code != 0:
            # A failing suite exercises fewer routes, so comparing what it did
            # capture against the frozen file would blame this change for gaps
            # the failures caused. Fix the tests first.
            print(
                f"the suite failed (pytest exited {code}); the capture would be "
                "incomplete and any drift it reported would be an artefact of "
                "that, not a real change",
                file=sys.stderr,
            )
            return code
        if not captured.is_file():
            print(
                "no responses were captured; the suite did not reach the gateway",
                file=sys.stderr,
            )
            return 1
        observed = json.loads(captured.read_text("utf-8"))

    routes = observed.get("routes") or {}
    covered = {key.split(" ", 1)[1].rsplit(" [", 1)[0] for key in routes}
    total = len(contract.http_routes())
    print(f"captured {len(routes)} route/status shapes")
    print(f"coverage: {len(covered)}/{total} routes exercised by the offline suite")

    if args.check:
        problems = response_capture.check(observed, response_capture.load())
        breaking = [p for p in problems if "BREAKING" in p]
        other = [p for p in problems if "BREAKING" not in p]

        if other:
            # Reported, not enforced. The capture depends on which optional
            # extras are installed and on which tests a platform skips: a route
            # whose list is empty here and populated there differs in shape
            # without anything having changed. Failing on that would train
            # everyone to regenerate the file to make CI shut up, which is how
            # a contract gate stops meaning anything.
            print("\nshapes moved without breaking a client:")
            for problem in other:
                print(f"  {problem}")
            print("  (regenerate and commit when the change is yours)")

        if breaking:
            print("\na client written against the frozen shapes would break:")
            for problem in breaking:
                print(f"  {problem}")
            print(
                "\nIf this was intended, rerun without --check and commit the "
                "diff. If it was not, the diff is the bug report."
            )
            return 1
        print("no breaking change to the frozen response shapes")
        return 0

    written = response_capture.save(observed)
    print(f"wrote {written.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
