"""Budgeted multistep route-planning protocol normalization CLI.

This entry point owns the *public boundary* of the multistep PaRoutes-style
route-planning scenario: it reads the installed public inputs (installation
manifest, target inputs, frozen planner outputs, config, and stock), admits
them under the budgeted protocol schema, normalizes the frozen route trees,
and writes the benchmark intermediate artifact atomically.

It performs no evaluation.  Reference routes, hidden route trees, and the
private evaluator are deliberately absent from the public boundary, and this
module rejects any input that attempts to smuggle them in.  The independent
evaluator runs only after the intermediate artifact is frozen.

The bundled CC0 synthetic fixture uses identity string canonicalization because
it is a protocol smoke test, not a chemistry benchmark.  Production data must
use the benchmark's RDKit canonicalizer; the canonicalizer is selected by
``select_canonicalizer`` and can be overridden by callers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from retrosynthesis_planning.benchmark_common import (
    BenchmarkProtocolError,
    build_intermediate_artifact,
    json_copy,
    require_text,
    write_json_atomic,
)
from retrosynthesis_planning.multistep_benchmark import (
    normalize_planner_outputs,
    normalize_stock,
    validate_targets,
)

SCENARIO_ID = "multistep_paroutes_budgeted_v1"

Canonicalizer = Callable[[str], str]


def identity_canonicalize(smiles: str) -> str:
    """Identity canonicalizer for the CC0 synthetic protocol fixture.

    The bundled fixture stores already-canonical molecule strings, so
    normalization is a strict strip-and-validate pass rather than a chemical
    canonicalization.  This must never be used for production chemistry data.
    """

    if not isinstance(smiles, str) or not smiles.strip():
        raise BenchmarkProtocolError("SMILES must be a non-empty string")
    return smiles.strip()


def select_canonicalizer(prefer_rdkit: bool = False) -> Canonicalizer:
    """Choose the canonicalizer for the run.

    The bundled fixture is a CC0 synthetic protocol smoke test and uses
    identity canonicalization.  Production data must use the benchmark's RDKit
    canonicalizer; ``prefer_rdkit=True`` selects it when RDKit is importable
    and otherwise raises rather than silently degrading to string identity.
    """

    if prefer_rdkit:
        # Force the RDKit path so a missing dependency is a loud failure, not
        # a silent fallback to string comparison on real chemistry.
        from retrosynthesis_planning.single_step_benchmark import rdkit_canonicalize

        rdkit_canonicalize("CCO")
        return rdkit_canonicalize
    return identity_canonicalize


def _read_json(path: Path, *, field: str) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise BenchmarkProtocolError(f"{field} not found at {path}") from exc
    except json.JSONDecodeError as exc:
        raise BenchmarkProtocolError(f"{field} is not valid JSON: {exc}") from exc


def _require_object(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BenchmarkProtocolError(f"{field} must be a JSON object")
    return value


def _require_array(value: Any, *, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise BenchmarkProtocolError(f"{field} must be a JSON array")
    return value


def _require_positive_int(value: Any, *, field: str, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise BenchmarkProtocolError(f"{field} must be a positive integer")
    if maximum is not None and value > maximum:
        raise BenchmarkProtocolError(f"{field} must be at most {maximum}")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_installation(workspace: Path) -> Mapping[str, Any]:
    """Load and gate the installation manifest on the expected scenario."""

    installation = _require_object(
        _read_json(workspace / "installation.json", field="installation.json"),
        field="installation.json",
    )
    scenario_id = installation.get("scenario_id")
    if scenario_id != SCENARIO_ID:
        raise BenchmarkProtocolError(
            f"installation.json scenario_id {scenario_id!r} does not match "
            f"expected {SCENARIO_ID!r}"
        )
    return installation


def verify_installed_hashes(
    installation: Mapping[str, Any], workspace: Path
) -> dict[str, str]:
    """Verify declared installed-file hashes and return the observed ones.

    ``installation.json`` declares hashes of the installed public files.  We
    recompute each file's SHA-256 and refuse to proceed on any mismatch, which
    is the "mismatched input" failure the protocol requires.
    """

    declared = installation.get("hashes") or installation.get("files")
    observed: dict[str, str] = {}
    if isinstance(declared, Mapping):
        for name, expected in declared.items():
            if not isinstance(name, str) or not isinstance(expected, str):
                raise BenchmarkProtocolError(
                    "installation.json hashes must map file names to hex digests"
                )
            path = workspace / "public" / name
            actual = _sha256_file(path)
            observed[name] = actual
            if actual != expected:
                raise BenchmarkProtocolError(
                    f"hash mismatch for public/{name}: declared {expected!r} "
                    f"but observed {actual!r}"
                )
    return observed


def load_config(workspace: Path) -> tuple[int, Mapping[str, int | float]]:
    """Load and validate the protocol config (max_routes and budget)."""

    config = _require_object(
        _read_json(workspace / "public" / "config.json", field="config.json"),
        field="config.json",
    )
    max_routes = _require_positive_int(
        config.get("max_routes"), field="config.max_routes"
    )
    budget = config.get("budget")
    if not isinstance(budget, Mapping) or not budget:
        raise BenchmarkProtocolError("config.budget must be a non-empty object")
    budget_copy = json_copy(dict(budget), field="config.budget")
    return max_routes, budget_copy


def load_stock(workspace: Path, *, canonicalizer: Canonicalizer) -> frozenset[str]:
    """Load and normalize the purchasable stock as a canonical set."""

    stock_values = _require_array(
        _read_json(workspace / "public" / "stock.json", field="stock.json"),
        field="stock.json",
    )
    return normalize_stock(stock_values, canonicalizer=canonicalizer)


def build_records(
    workspace: Path,
    *,
    max_routes: int,
    budget: Mapping[str, int | float],
    canonicalizer: Canonicalizer,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Normalize the frozen planner outputs into per-target records."""

    inputs = _require_array(
        _read_json(workspace / "public" / "inputs.json", field="inputs.json"),
        field="inputs.json",
    )
    outputs = _require_array(
        _read_json(
            workspace / "public" / "model_outputs.json",
            field="model_outputs.json",
        ),
        field="model_outputs.json",
    )

    targets = validate_targets(inputs, canonicalizer=canonicalizer)
    stock = load_stock(workspace, canonicalizer=canonicalizer)
    normalized = normalize_planner_outputs(
        targets,
        outputs,
        stock=stock,
        max_routes=max_routes,
        budget=budget,
        canonicalizer=canonicalizer,
    )

    records = [dict(item) for item in normalized]
    input_hashes = {
        "inputs.json": _sha256_file(workspace / "public" / "inputs.json"),
        "model_outputs.json": _sha256_file(
            workspace / "public" / "model_outputs.json"
        ),
        "config.json": _sha256_file(workspace / "public" / "config.json"),
        "stock.json": _sha256_file(workspace / "public" / "stock.json"),
    }
    return records, input_hashes


def run(workspace: Path, *, canonicalizer: Canonicalizer | None = None) -> dict[str, Any]:
    """Run the full normalization and return the intermediate artifact."""

    installation = load_installation(workspace)
    verify_installed_hashes(installation, workspace)
    max_routes, budget = load_config(workspace)

    if canonicalizer is None:
        canonicalizer = select_canonicalizer()

    records, _input_hashes = build_records(
        workspace,
        max_routes=max_routes,
        budget=budget,
        canonicalizer=canonicalizer,
    )

    metadata: dict[str, Any] = {"budget": budget, "max_routes": max_routes}
    return build_intermediate_artifact(SCENARIO_ID, records, metadata=metadata)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize the budgeted multistep route-planning public boundary "
            "into the benchmark intermediate artifact. Reads the installed "
            "public inputs under --workspace and atomically writes "
            "results/intermediate_results.json. No evaluation is performed."
        )
    )
    parser.add_argument(
        "--workspace",
        required=True,
        help="Path to the installed scenario workspace containing "
        "installation.json and public/.",
    )
    parser.add_argument(
        "--rdkit",
        action="store_true",
        help="Use the benchmark RDKit canonicalizer instead of the fixture's "
        "identity canonicalizer (required for production chemistry data).",
    )
    args = parser.parse_args(argv)

    workspace = Path(args.workspace)
    try:
        artifact = run(workspace, canonicalizer=select_canonicalizer(args.rdkit))
    except BenchmarkProtocolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    output_path = workspace / "results" / "intermediate_results.json"
    write_json_atomic(output_path, artifact)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI
    raise SystemExit(main())


__all__ = [
    "SCENARIO_ID",
    "build_records",
    "identity_canonicalize",
    "load_config",
    "load_installation",
    "load_stock",
    "main",
    "run",
    "select_canonicalizer",
    "verify_installed_hashes",
]
