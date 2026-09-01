#!/usr/bin/env python3
"""CLI for categorical reaction-condition recommendation (Scenario 5)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from retrosynthesis_planning.benchmark_common import (
    BenchmarkProtocolError,
    build_intermediate_artifact,
    write_json_atomic,
)
from retrosynthesis_planning.condition_benchmark import (
    normalize_condition_outputs,
    validate_condition_inputs,
)

SCENARIO_ID = "reaction_condition_uspto_categorical_v1"


def identity_canonicalize(smiles: str) -> str:
    if not isinstance(smiles, str) or not smiles.strip():
        raise BenchmarkProtocolError("molecule string must be non-empty")
    return smiles.strip()


def load_json(path: Path, field: str) -> Any:
    """Load and parse a JSON file with error handling."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise BenchmarkProtocolError(f"{field} file not found: {path}")
    except json.JSONDecodeError as exc:
        raise BenchmarkProtocolError(f"{field} file is not valid JSON: {exc}")


def validate_installation(installation: dict[str, Any]) -> None:
    """Validate the installation manifest."""
    if not isinstance(installation, dict):
        raise BenchmarkProtocolError("installation must be an object")
    scenario_id = installation.get("scenario_id")
    if scenario_id != SCENARIO_ID:
        raise BenchmarkProtocolError(
            f"Unsupported scenario_id: {scenario_id!r}, expected "
            "'reaction_condition_uspto_categorical_v1'"
        )


def validate_config(config: dict[str, Any]) -> tuple[int, int]:
    """Validate and extract top_k and random_seed from config."""
    if not isinstance(config, dict):
        raise BenchmarkProtocolError("config must be an object")
    top_k = config["top_k"]
    random_seed = config["random_seed"]
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1 or top_k > 10:
        raise BenchmarkProtocolError("top_k must be a positive integer at most 10")
    if isinstance(random_seed, bool) or not isinstance(random_seed, int):
        raise BenchmarkProtocolError("random_seed must be an integer")
    return top_k, random_seed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Categorical reaction-condition recommendation benchmark CLI"
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        required=True,
        help="Path to the workspace directory containing installation.json and public/",
    )
    args = parser.parse_args()

    workspace = args.workspace
    if not workspace.is_dir():
        print(f"Error: workspace directory not found: {workspace}", file=sys.stderr)
        return 1

    try:
        # Load and validate installation
        installation = load_json(workspace / "installation.json", "installation")
        validate_installation(installation)

        # Load public inputs
        public_dir = workspace / "public"
        if not public_dir.is_dir():
            raise BenchmarkProtocolError(f"public directory not found: {public_dir}")

        inputs = load_json(public_dir / "inputs.json", "inputs")
        vocabulary = load_json(public_dir / "vocabulary.json", "vocabulary")
        model_outputs = load_json(public_dir / "model_outputs.json", "model_outputs")
        config = load_json(public_dir / "config.json", "config")

        # Validate config
        top_k, random_seed = validate_config(config)

        # Validate and normalize inputs
        if not isinstance(inputs, list):
            raise BenchmarkProtocolError("inputs must be an array")
        if not isinstance(vocabulary, dict):
            raise BenchmarkProtocolError("vocabulary must be an object")
        if not isinstance(model_outputs, list):
            raise BenchmarkProtocolError("model_outputs must be an array")
        normalized_inputs = validate_condition_inputs(
            inputs, canonicalizer=identity_canonicalize
        )

        # Normalize outputs
        normalized_outputs = normalize_condition_outputs(
            normalized_inputs,
            model_outputs,
            vocabulary=vocabulary,
            top_k=top_k,
        )

        # Build intermediate artifact
        artifact = build_intermediate_artifact(
            SCENARIO_ID,
            normalized_outputs,
            metadata={"top_k": top_k},
        )

        # Write atomically
        results_dir = workspace / "results"
        output_path = results_dir / "intermediate_results.json"
        write_json_atomic(output_path, artifact)

        return 0

    except BenchmarkProtocolError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
