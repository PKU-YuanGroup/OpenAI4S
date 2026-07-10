"""Command-line entry point for deterministic harness scenarios."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from .runner import run_scenario
from .schema import ScenarioValidationError, load_scenario

_DEFAULT_SCENARIOS = Path(__file__).resolve().parent / "scenarios"
_DEFAULT_GOLDEN = (
    Path(__file__).resolve().parent / "golden_traces" / "v1" / "r5_prechange.json"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m harness.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run deterministic scenarios")
    run.add_argument("--tier", default="pr", help="scenario tier tag to select")
    run.add_argument(
        "--offline",
        action="store_true",
        help="select only scenarios explicitly eligible for offline execution",
    )
    run.add_argument(
        "--scenario-dir",
        type=Path,
        default=_DEFAULT_SCENARIOS,
        help="directory containing versioned JSON scenarios",
    )
    run.add_argument(
        "--scenario",
        action="append",
        default=[],
        help="run only the named scenario id (repeatable)",
    )
    characterize = subparsers.add_parser(
        "characterize",
        help="compare the r5 pre-change characterization against its golden",
    )
    characterize.add_argument(
        "--write",
        action="store_true",
        help="regenerate the golden instead of comparing (review the diff!)",
    )
    characterize.add_argument(
        "--golden",
        type=Path,
        default=_DEFAULT_GOLDEN,
        help="golden file to compare against or rewrite",
    )
    return parser


def _scenario_paths(root: Path) -> list[Path]:
    if not root.is_dir():
        raise ScenarioValidationError(f"scenario directory does not exist: {root}")
    # Files are ordered by stable relative path.  Event lists are never sorted.
    return sorted(
        root.rglob("*.json"), key=lambda path: path.relative_to(root).as_posix()
    )


def _run(args: argparse.Namespace) -> int:
    selected_ids = set(args.scenario)
    results = []
    load_errors: list[str] = []
    seen_ids: dict[str, Path] = {}
    excluded_offline: set[str] = set()
    excluded_tier: set[str] = set()
    for path in _scenario_paths(args.scenario_dir):
        try:
            scenario = load_scenario(path)
        except ScenarioValidationError as exc:
            load_errors.append(str(exc))
            continue
        if scenario.id in seen_ids:
            load_errors.append(
                f"duplicate scenario id {scenario.id!r} in {path} "
                f"(already defined in {seen_ids[scenario.id]})"
            )
            continue
        seen_ids[scenario.id] = path
        if not scenario.in_tier(args.tier):
            excluded_tier.add(scenario.id)
            continue
        if selected_ids and scenario.id not in selected_ids:
            continue
        if args.offline and not scenario.is_offline:
            excluded_offline.add(scenario.id)
            continue
        results.append(run_scenario(scenario, offline=args.offline))

    found_ids = {result.scenario_id for result in results}
    for scenario_id in sorted(selected_ids - found_ids):
        if scenario_id in excluded_offline:
            load_errors.append(
                f"requested scenario {scenario_id!r} is in tier {args.tier!r} "
                "but is not eligible for --offline"
            )
        elif scenario_id in excluded_tier:
            load_errors.append(
                f"requested scenario {scenario_id!r} exists but is not tagged "
                f"tier:{args.tier}"
            )
        else:
            load_errors.append(
                f"requested scenario {scenario_id!r} was not found in tier "
                f"{args.tier!r}"
            )

    for error in load_errors:
        print(f"ERROR {error}")
    for result in results:
        state = "PASS" if result.passed else "FAIL"
        print(
            f"{state} {result.scenario_id} events={len(result.events)} "
            f"sha256={result.trace_sha256}"
        )
        for error in result.errors:
            print(f"  {error}")
    failed = len(load_errors) + sum(not result.passed for result in results)
    summary = {
        "schema_version": 1,
        "tier": args.tier,
        "offline": bool(args.offline),
        "selected": len(results),
        "passed": sum(result.passed for result in results),
        "failed": failed,
        "load_errors": len(load_errors),
    }
    print("SUMMARY " + json.dumps(summary, sort_keys=True, separators=(",", ":")))
    if not results:
        return 2
    return 1 if failed else 0


def _characterize(args: argparse.Namespace) -> int:
    # Imported lazily: only this subcommand touches production modules.
    from .characterize import characterization_bytes

    with tempfile.TemporaryDirectory(prefix="openai4s-characterize-") as tmp:
        current = characterization_bytes(Path(tmp) / "data")
    golden: Path = args.golden
    if args.write:
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_bytes(current)
        print(f"WROTE {golden} ({len(current)} bytes) — review the diff before commit")
        return 0
    if not golden.exists():
        print(f"ERROR golden {golden} does not exist (use --write to create it)")
        return 2
    if current == golden.read_bytes():
        print(f"MATCH {golden}")
        return 0
    print(
        f"DRIFT {golden}: production behavior no longer matches the reviewed "
        "golden. If intentional, rerun with --write and review current_behavior/"
        "desired_contract/known_bug together."
    )
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "characterize":
            return _characterize(args)
        return _run(args)
    except ScenarioValidationError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
