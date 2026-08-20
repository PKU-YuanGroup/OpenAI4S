#!/usr/bin/env python3
"""Regenerate (or verify) the frozen response shapes in docs/response-schemas.json.

    uv run python scripts/capture_response_schemas.py            # rewrite the artifact
    uv run python scripts/capture_response_schemas.py --check     # fail on drift

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

_MAX_WORKERS = 4


def _run_suite(destination: Path) -> int:
    env = dict(os.environ)
    env["OPENAI4S_CAPTURE_SCHEMAS"] = str(destination)
    # Deliberately no -x. Stopping at the first failure truncates the capture,
    # and every route the run never reached would then be reported as "frozen
    # but no longer observed" -- drift that is really just an aborted run.
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--no-header",
            # The same width and scheduler CI runs the suite under. This used
            # to be the one gate that could not take them: the capture is
            # written once per session, so four workers each writing the same
            # destination would retain only the last one's fraction of the
            # evidence. They now leave shares that `response_capture.assemble`
            # merges below through the same `merge` call `Recorder.observe`
            # makes, so a route observed in two workers reaches the schema it
            # would have reached in one process.
            "-n",
            "auto",
            "--maxprocesses",
            str(_MAX_WORKERS),
            "--dist",
            "loadfile",
            "tests",
        ],
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
        if code != 0 and args.check:
            # Do not assemble a known-aborted run.  Besides being incomplete,
            # a worker failure may be the reason a share is absent; the pytest
            # failure is the useful result and must not be hidden by a second
            # assembly error.
            print(
                f"the suite failed (pytest exited {code}); the capture would be "
                "incomplete and any drift it reported would be an artefact of "
                "that, not a real change",
                file=sys.stderr,
            )
            return code
        # Merge the workers' shares, if the run was split. Here rather than in
        # a pytest hook because every one of those processes has now exited:
        # there is no writer left to race.
        try:
            response_capture.assemble(captured, require_complete=True)
        except (OSError, ValueError) as error:
            print(
                f"the xdist response capture was incomplete: {error}",
                file=sys.stderr,
            )
            return code or 1
        if code != 0:
            # Regeneration must still work here. The suite contains tests that
            # validate this very artifact, so a stale file makes them fail --
            # and refusing to write on a failing suite would mean the only way
            # to fix the file is blocked by the file being unfixed. Say the
            # capture may be short and let the coverage line be the check.
            print(
                f"\nwarning: the suite failed (pytest exited {code}), so this "
                "capture may have missed routes. Compare the coverage line "
                "below against the previous run before committing.",
                file=sys.stderr,
            )
        if not captured.is_file():
            print(
                "no responses were captured; the suite did not reach the gateway",
                file=sys.stderr,
            )
            return 1
        observed = json.loads(captured.read_text("utf-8"))

    routes = observed.get("routes") or {}
    covered = {key.split(" ", 1)[1].rsplit(" [", 1)[0] for key in routes}
    known = contract.http_routes()
    uncovered = sorted(known - covered)
    print(f"captured {len(routes)} route/status shapes")
    print(
        f"coverage: {len(covered)}/{len(known)} routes exercised by the offline suite"
    )

    if uncovered:
        # The count on its own is not actionable. These are the routes whose
        # responses nothing checks, so they are also the list of tests worth
        # writing next -- and leaving it as a number is how a known gap turns
        # into a forgotten one.
        print(f"\n{len(uncovered)} routes no offline test reaches:")
        for route in uncovered:
            print(f"  {route}")

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

        if uncovered:
            # A gate now, not a metric. Plan line 311 says a new or changed
            # route must update the response schema and the response contract,
            # and this was the only thing positioned to enforce the first half
            # -- it printed the list and returned 0, so a route could sit in the
            # contract with no shape indefinitely. `/frames/<id>/admissions/<id>`
            # did, for forty-three commits.
            #
            # Safe to turn on today precisely because the number is 0: this
            # cannot fail on work already done, only on work that skips the
            # test. A route genuinely unreachable offline belongs in the
            # contract's own exclusions with a reason, not in a list nobody
            # reads.
            print(
                f"\n{len(uncovered)} routes are in the contract with no frozen "
                "shape. Drive them from the offline suite, or say in the "
                "contract why they cannot be."
            )
            return 1

        print("no breaking change to the frozen response shapes")
        return 0

    written = response_capture.save(observed)
    print(f"wrote {written.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
