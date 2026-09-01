"""Class-unknown single-step retrosynthesis protocol normalization CLI.

This entry point owns the *public boundary* of the single-step retrosynthesis
scenario: it reads the installed public inputs (installation manifest, target
inputs, frozen model outputs, config, and model provenance), admits them under
the class-unknown schema, normalizes the frozen beams, and writes the benchmark
intermediate artifact atomically.

It performs no evaluation.  Reaction class, reference precursors, and patent
context are deliberately absent from the public boundary, and this module
rejects any input that attempts to smuggle them in.  The independent evaluator
runs only after the intermediate artifact is frozen.

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
    write_json_atomic,
)
from retrosynthesis_planning.single_step_benchmark import (
    SingleStepProtocolError,
    build_intermediate_results,
    normalize_prediction_payloads,
    rdkit_canonicalize,
    validate_public_targets,
)

SCENARIO_ID = "single_step_retrosynthesis_class_unknown_v1"

Canonicalizer = Callable[[str], str]


def identity_canonicalize(smiles: str) -> str:
    """Identity canonicalizer for the CC0 synthetic protocol fixture.

    The bundled fixture stores already-canonical SMILES, so normalization is a
    strict strip-and-validate pass rather than a chemical canonicalization.
    This must never be used for production chemistry data.
    """

    if not isinstance(smiles, str) or not smiles.strip():
        raise SingleStepProtocolError("SMILES must be a non-empty string")
    return smiles.strip()


def select_canonicalizer(prefer_rdkit: bool = False) -> Canonicalizer:
    """Choose the canonicalizer for the run.

    The bundled fixture is a CC0 synthetic protocol smoke test and uses
    identity canonicalization.  Production data must use the benchmark's RDKit
    canonicalizer; ``prefer_rdkit=True`` selects it when RDKit is importable
    and otherwise raises rather than silently degrading to string identity.
    """

    if prefer_rdkit:
        # Force the RDKit path so a missing dependency is a loud failure, not a
        # silent fallback to string comparison on real chemistry.
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


def _require_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkProtocolError(f"{field} must be a non-empty string")
    return value.strip()


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


def load_config(workspace: Path) -> tuple[int, int]:
    """Load and validate the protocol config (top_k and random_seed)."""

    config = _require_object(
        _read_json(workspace / "public" / "config.json", field="config.json"),
        field="config.json",
    )
    top_k = _require_positive_int(config.get("top_k"), field="config.top_k", maximum=10)
    random_seed = _require_positive_int(
        config.get("random_seed"), field="config.random_seed"
    )
    return top_k, random_seed


def load_model_manifest(workspace: Path) -> Mapping[str, Any]:
    manifest = _require_object(
        _read_json(
            workspace / "public" / "model_manifest.json",
            field="model_manifest.json",
        ),
        field="model_manifest.json",
    )
    return dict(manifest)


def build_records(
    workspace: Path,
    *,
    top_k: int,
    canonicalizer: Canonicalizer,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Normalize the frozen model outputs into per-target records."""

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

    targets = validate_public_targets(inputs, canonicalizer=canonicalizer)
    predictions = normalize_prediction_payloads(
        targets, outputs, top_k=top_k, canonicalizer=canonicalizer
    )

    records = [item.to_dict() for item in predictions]
    input_hashes = {
        "inputs.json": _sha256_file(workspace / "public" / "inputs.json"),
        "model_outputs.json": _sha256_file(
            workspace / "public" / "model_outputs.json"
        ),
        "config.json": _sha256_file(workspace / "public" / "config.json"),
        "model_manifest.json": _sha256_file(
            workspace / "public" / "model_manifest.json"
        ),
    }
    return records, input_hashes


def run(workspace: Path, *, canonicalizer: Canonicalizer | None = None) -> dict[str, Any]:
    """Run the full normalization and return the intermediate artifact."""

    installation = load_installation(workspace)
    verify_installed_hashes(installation, workspace)
    top_k, random_seed = load_config(workspace)
    model_manifest = load_model_manifest(workspace)

    if canonicalizer is None:
        canonicalizer = select_canonicalizer()

    records, _input_hashes = build_records(
        workspace, top_k=top_k, canonicalizer=canonicalizer
    )
    targets = validate_public_targets(
        _require_array(
            _read_json(workspace / "public" / "inputs.json", field="inputs.json"),
            field="inputs.json",
        ),
        canonicalizer=canonicalizer,
    )
    predictions = normalize_prediction_payloads(
        targets,
        _require_array(
            _read_json(
                workspace / "public" / "model_outputs.json",
                field="model_outputs.json",
            ),
            field="model_outputs.json",
        ),
        top_k=top_k,
        canonicalizer=canonicalizer,
    )
    return build_intermediate_results(
        targets,
        predictions,
        top_k=top_k,
        model_manifest=model_manifest,
        random_seed=random_seed,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize the class-unknown single-step retrosynthesis public "
            "boundary into the benchmark intermediate artifact. Reads the "
            "installed public inputs under --workspace and atomically writes "
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
    except (BenchmarkProtocolError, SingleStepProtocolError) as exc:
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
    "load_model_manifest",
    "main",
    "run",
    "select_canonicalizer",
    "verify_installed_hashes",
]
