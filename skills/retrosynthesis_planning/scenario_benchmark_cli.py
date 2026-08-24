"""One offline JSON CLI for independent retrosynthesis Scenarios 2 through 6."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .atom_mapping_benchmark import (
    evaluate_mappings,
    normalize_mapping_outputs,
    validate_public_reactions,
)
from .benchmark_common import (
    BenchmarkProtocolError,
    build_intermediate_artifact,
    sha256_json,
    write_json_atomic,
)
from .condition_benchmark import (
    evaluate_condition_predictions,
    normalize_condition_outputs,
    validate_condition_inputs,
)
from .forward_benchmark import (
    evaluate_forward_predictions,
    normalize_forward_outputs,
    validate_forward_inputs,
)
from .multistep_benchmark import (
    evaluate_routes,
    normalize_planner_outputs,
    normalize_stock,
    validate_targets,
)
from .yield_benchmark import (
    evaluate_yield_predictions,
    normalize_yield_outputs,
    validate_yield_inputs,
)

SCENARIO_IDS = {
    "multistep": "multistep_paroutes_budgeted_v1",
    "atom_mapping": "reaction_atom_mapping_curated_v1",
    "forward": "forward_prediction_uspto_mit_separated_v1",
    "conditions": "reaction_condition_tuple_closed_vocab_v1",
    "yield": "reaction_yield_distribution_shift_v1",
}


def _load(path: str) -> Any:
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def _array(value: Any, *, field: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or not all(
        isinstance(row, Mapping) for row in value
    ):
        raise BenchmarkProtocolError(f"{field} must be an array of objects")
    return value


def _normalize(args: argparse.Namespace) -> dict[str, Any]:
    inputs = _array(_load(args.inputs), field="inputs")
    predictions = _array(_load(args.predictions), field="predictions")
    metadata: dict[str, Any] = {}
    if args.scenario == "multistep":
        if not args.stock or not args.config:
            raise BenchmarkProtocolError(
                "multistep normalization requires --stock and --config"
            )
        stock_values = _load(args.stock)
        config = _load(args.config)
        if not isinstance(stock_values, list) or not isinstance(config, Mapping):
            raise BenchmarkProtocolError("stock must be an array and config an object")
        targets = validate_targets(inputs)
        stock = normalize_stock(stock_values)
        budget = config.get("budget")
        if not isinstance(budget, Mapping):
            raise BenchmarkProtocolError("config.budget must be an object")
        records = normalize_planner_outputs(
            targets,
            predictions,
            stock=stock,
            max_routes=config.get("max_routes"),
            budget=budget,
        )
        metadata = {"budget": dict(budget), "max_routes": config.get("max_routes")}
    elif args.scenario == "atom_mapping":
        records = normalize_mapping_outputs(
            validate_public_reactions(inputs), predictions
        )
    elif args.scenario == "forward":
        records = normalize_forward_outputs(
            validate_forward_inputs(inputs), predictions, top_k=args.top_k
        )
        metadata = {"top_k": args.top_k}
    elif args.scenario == "conditions":
        if not args.vocabulary:
            raise BenchmarkProtocolError(
                "condition normalization requires --vocabulary"
            )
        vocabulary = _load(args.vocabulary)
        if not isinstance(vocabulary, Mapping):
            raise BenchmarkProtocolError("vocabulary must be an object")
        records = normalize_condition_outputs(
            validate_condition_inputs(inputs),
            predictions,
            vocabulary=vocabulary,
            top_k=args.top_k,
        )
        metadata = {"top_k": args.top_k}
    else:
        records = normalize_yield_outputs(validate_yield_inputs(inputs), predictions)
    return build_intermediate_artifact(
        SCENARIO_IDS[args.scenario], records, metadata=metadata
    )


def _evaluate(args: argparse.Namespace) -> dict[str, Any]:
    artifact = _load(args.predictions)
    if not isinstance(artifact, Mapping) or not isinstance(
        artifact.get("records"), list
    ):
        raise BenchmarkProtocolError(
            "predictions must be a normalized intermediate artifact"
        )
    unhashed = dict(artifact)
    claimed_hash = unhashed.pop("trajectory_sha256", None)
    if claimed_hash != sha256_json(unhashed):
        raise BenchmarkProtocolError("intermediate artifact trajectory hash mismatch")
    if artifact.get("scenario_id") != SCENARIO_IDS[args.scenario]:
        raise BenchmarkProtocolError("intermediate artifact scenario_id mismatch")
    records = artifact["records"]
    references = _array(_load(args.references), field="references")
    if args.scenario == "multistep":
        return evaluate_routes(records, references)
    if args.scenario == "atom_mapping":
        return evaluate_mappings(records, references)
    if args.scenario == "forward":
        return evaluate_forward_predictions(records, references, top_k=args.top_k)
    if args.scenario == "conditions":
        return evaluate_condition_predictions(records, references, top_k=args.top_k)
    return evaluate_yield_predictions(records, references)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("normalize", "evaluate"))
    parser.add_argument("scenario", choices=tuple(SCENARIO_IDS))
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--inputs")
    parser.add_argument("--references")
    parser.add_argument("--stock")
    parser.add_argument("--config")
    parser.add_argument("--vocabulary")
    parser.add_argument("--top-k", type=int, default=10)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.action == "normalize":
        if not args.inputs:
            raise BenchmarkProtocolError("normalize requires --inputs")
        result = _normalize(args)
    else:
        if not args.references:
            raise BenchmarkProtocolError("evaluate requires --references")
        result = _evaluate(args)
    write_json_atomic(args.output, result)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess
    raise SystemExit(main())
