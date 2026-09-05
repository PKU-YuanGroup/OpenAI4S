#!/usr/bin/env python3
"""CLI for OOD reaction-yield estimation normalization."""

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
from retrosynthesis_planning.yield_benchmark import (
    normalize_yield_outputs,
    validate_yield_inputs,
)

SCENARIO_ID = "buchwald_hartwig_yield_ood_v1"


def identity_canonicalize(smiles: str) -> str:
    if not isinstance(smiles, str) or not smiles.strip():
        raise BenchmarkProtocolError("molecule string must be non-empty")
    return smiles.strip()


def load_json(path: Path) -> Any:
    """Load JSON from path with error handling."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        raise BenchmarkProtocolError(f"Failed to load {path}: {exc}")


def validate_installation(installation: dict[str, Any]) -> None:
    """Validate installation.json."""
    if not isinstance(installation, dict):
        raise BenchmarkProtocolError("installation must be an object")
    if installation.get("scenario_id") != SCENARIO_ID:
        raise BenchmarkProtocolError(
            f"Unsupported scenario_id: {installation.get('scenario_id')}"
        )


def validate_config(config: dict[str, Any]) -> None:
    """Validate config.json."""
    if not isinstance(config, dict):
        raise BenchmarkProtocolError("config must be an object")
    seed = config.get("random_seed")
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise BenchmarkProtocolError("random_seed must be a non-negative integer")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="OOD reaction-yield estimation normalization CLI"
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        required=True,
        help="Path to workspace directory containing installation.json, public/, and results/",
    )
    args = parser.parse_args()

    workspace = args.workspace
    if not workspace.is_dir():
        print(f"Error: workspace {workspace} is not a directory", file=sys.stderr)
        return 1

    try:
        # Load and validate installation
        installation_path = workspace / "installation.json"
        if not installation_path.is_file():
            raise BenchmarkProtocolError(f"Missing {installation_path}")
        installation = load_json(installation_path)
        validate_installation(installation)

        # Load and validate config
        config_path = workspace / "public" / "config.json"
        if not config_path.is_file():
            raise BenchmarkProtocolError(f"Missing {config_path}")
        config = load_json(config_path)
        validate_config(config)

        # Load inputs
        inputs_path = workspace / "public" / "inputs.json"
        if not inputs_path.is_file():
            raise BenchmarkProtocolError(f"Missing {inputs_path}")
        inputs_raw = load_json(inputs_path)
        if not isinstance(inputs_raw, list):
            raise BenchmarkProtocolError("inputs.json must be a list")

        # Load model outputs
        outputs_path = workspace / "public" / "model_outputs.json"
        if not outputs_path.is_file():
            raise BenchmarkProtocolError(f"Missing {outputs_path}")
        outputs_raw = load_json(outputs_path)
        if not isinstance(outputs_raw, list):
            raise BenchmarkProtocolError("model_outputs.json must be a list")

        # Validate and normalize inputs
        validated_inputs = validate_yield_inputs(
            inputs_raw, canonicalizer=identity_canonicalize, require_all_splits=True
        )

        # Validate and normalize outputs
        normalized_outputs = normalize_yield_outputs(validated_inputs, outputs_raw)

        observed_splits = sorted({row["split"] for row in validated_inputs})

        # Build intermediate artifact
        artifact = build_intermediate_artifact(
            SCENARIO_ID,
            normalized_outputs,
            metadata={"splits": observed_splits},
        )

        # Write atomically
        results_dir = workspace / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
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
