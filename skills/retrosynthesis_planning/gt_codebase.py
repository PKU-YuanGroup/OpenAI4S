"""Separated pipeline/test-case runtime for the six retrosynthesis Scenarios."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
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
    require_exact_fields,
    require_text,
    sha256_json,
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
from .single_step_benchmark import (
    build_intermediate_results,
    evaluate_predictions,
    normalize_prediction_payloads,
    normalize_references,
    validate_public_targets,
)
from .yield_benchmark import (
    evaluate_yield_predictions,
    normalize_yield_outputs,
    validate_yield_inputs,
)

SCENARIO_IDS = {
    "single_step": "single_step_retrosynthesis_class_unknown_v1",
    "multistep": "multistep_paroutes_budgeted_v1",
    "atom_mapping": "reaction_atom_mapping_curated_v1",
    "forward": "forward_prediction_uspto_mit_separated_v1",
    "conditions": "reaction_condition_uspto_categorical_v1",
    "yield": "buchwald_hartwig_yield_ood_v1",
}

_SAFE_JSON_NAME = re.compile(r"^[a-z][a-z0-9_]*\.json$")
_CASE_FIELDS = frozenset(
    {
        "schema_version",
        "scenario",
        "scenario_id",
        "query_source",
        "dataset",
        "public",
        "private_evaluator",
    }
)


def _identity(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkProtocolError("fixture canonical value must be non-empty")
    return value.strip()


def _connectivity(value: str) -> str:
    return _identity(value).replace("@", "")


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _array(path: Path) -> list[Mapping[str, Any]]:
    value = _read_json(path)
    if not isinstance(value, list) or not all(
        isinstance(row, Mapping) for row in value
    ):
        raise BenchmarkProtocolError(f"{path.name} must contain an array of objects")
    return list(value)


def _mapping(path: Path) -> Mapping[str, Any]:
    value = _read_json(path)
    if not isinstance(value, Mapping):
        raise BenchmarkProtocolError(f"{path.name} must contain an object")
    return value


def _fixture_mode(workspace: Path) -> bool:
    installation = _mapping(workspace / "installation.json")
    return installation.get("dataset_profile") == "synthetic_protocol_smoke"


def install_test_case(case_path: str | Path, workspace: str | Path) -> dict[str, Any]:
    """Install one bundled case while preserving the public/private boundary."""

    source = Path(case_path).resolve()
    destination = Path(workspace).resolve()
    case = _mapping(source)
    require_exact_fields(case, _CASE_FIELDS, field="test case")
    scenario = require_text(case["scenario"], field="scenario")
    if scenario not in SCENARIO_IDS or case["scenario_id"] != SCENARIO_IDS[scenario]:
        raise BenchmarkProtocolError("test case scenario identity mismatch")
    if destination.exists() and any(destination.iterdir()):
        raise BenchmarkProtocolError("workspace must not already contain files")
    public = case["public"]
    private = case["private_evaluator"]
    if not isinstance(public, Mapping) or not isinstance(private, Mapping):
        raise BenchmarkProtocolError("public and private_evaluator must be objects")
    written: dict[str, str] = {}
    for boundary, files in (("public", public), ("private_evaluator", private)):
        for name, payload in sorted(files.items()):
            if not isinstance(name, str) or not _SAFE_JSON_NAME.fullmatch(name):
                raise BenchmarkProtocolError(f"unsafe test-case file name {name!r}")
            target = destination / boundary / name
            _write_json(target, payload)
            written[f"{boundary}/{name}"] = _sha256_file(target)
    (destination / "results").mkdir(parents=True, exist_ok=True)
    dataset = case["dataset"]
    if not isinstance(dataset, Mapping):
        raise BenchmarkProtocolError("dataset must be an object")
    manifest = {
        "schema_version": 1,
        "scenario": scenario,
        "scenario_id": SCENARIO_IDS[scenario],
        "query_source": case["query_source"],
        "dataset_profile": dataset.get("profile"),
        "dataset": dict(dataset),
        "file_sha256": written,
        "ground_truth_boundary": "private_evaluator",
    }
    _write_json(destination / "installation.json", manifest)
    return manifest


def _public_paths(workspace: Path) -> tuple[Path, Path, Path]:
    public = workspace / "public"
    return public / "inputs.json", public / "model_outputs.json", public / "config.json"


def _normalize_atom_fixture(
    inputs: Sequence[Mapping[str, Any]], outputs: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any], ...]:
    """Validate pre-analyzed synthetic mapping rows without pretending to run RDKit."""

    reactions = validate_public_reactions(inputs)
    expected = {row["reaction_id"] for row in reactions}
    records: list[dict[str, Any]] = []
    fields = {
        "reaction_id",
        "correspondence",
        "bond_changes",
        "valid",
        "issues",
    }
    for index, output in enumerate(outputs, start=1):
        require_exact_fields(output, fields, field=f"fixture mapping output {index}")
        reaction_id = require_text(output["reaction_id"], field="reaction_id")
        if reaction_id not in expected or any(
            row["reaction_id"] == reaction_id for row in records
        ):
            raise BenchmarkProtocolError("unknown or duplicate fixture mapping output")
        if not isinstance(output["correspondence"], list) or not isinstance(
            output["bond_changes"], list
        ):
            raise BenchmarkProtocolError("fixture mapping arrays are required")
        records.append(dict(output))
    if {row["reaction_id"] for row in records} != expected:
        raise BenchmarkProtocolError("fixture mapping output must cover every reaction")
    return tuple(records)


def _normalize(scenario: str, workspace: Path) -> tuple[Any, dict[str, Any]]:
    input_path, output_path, config_path = _public_paths(workspace)
    inputs = _array(input_path)
    outputs = _array(output_path)
    config = _mapping(config_path)
    fixture = _fixture_mode(workspace)
    canonicalizer = _identity if fixture else None
    if scenario == "single_step":
        kwargs = {"canonicalizer": canonicalizer} if canonicalizer else {}
        targets = validate_public_targets(inputs, **kwargs)
        predictions = normalize_prediction_payloads(
            targets, outputs, top_k=int(config["top_k"]), **kwargs
        )
        artifact = build_intermediate_results(
            targets,
            predictions,
            top_k=int(config["top_k"]),
            model_manifest=_mapping(workspace / "public" / "model_manifest.json"),
            random_seed=int(config["random_seed"]),
        )
        return artifact, {"targets": targets, "predictions": predictions}
    if scenario == "multistep":
        kwargs = {"canonicalizer": canonicalizer} if canonicalizer else {}
        targets = validate_targets(inputs, **kwargs)
        stock = normalize_stock(
            _read_json(workspace / "public" / "stock.json"), **kwargs
        )
        records = normalize_planner_outputs(
            targets,
            outputs,
            stock=stock,
            max_routes=int(config["max_routes"]),
            budget=config["budget"],
            **kwargs,
        )
        metadata = {"budget": config["budget"], "max_routes": config["max_routes"]}
    elif scenario == "atom_mapping":
        records = (
            _normalize_atom_fixture(inputs, outputs)
            if fixture
            else normalize_mapping_outputs(validate_public_reactions(inputs), outputs)
        )
        metadata = {"fixture_preanalyzed": fixture}
    elif scenario == "forward":
        kwargs = (
            {
                "isomeric_canonicalizer": _identity,
                "connectivity_canonicalizer": _connectivity,
            }
            if fixture
            else {}
        )
        admitted = validate_forward_inputs(
            inputs, **({"canonicalizer": _identity} if fixture else {})
        )
        records = normalize_forward_outputs(
            admitted, outputs, top_k=int(config["top_k"]), **kwargs
        )
        metadata = {"top_k": config["top_k"]}
    elif scenario == "conditions":
        admitted = validate_condition_inputs(
            inputs, **({"canonicalizer": _identity} if fixture else {})
        )
        records = normalize_condition_outputs(
            admitted,
            outputs,
            vocabulary=_mapping(workspace / "public" / "vocabulary.json"),
            top_k=int(config["top_k"]),
        )
        metadata = {"top_k": config["top_k"]}
    elif scenario == "yield":
        admitted = validate_yield_inputs(
            inputs, **({"canonicalizer": _identity} if fixture else {})
        )
        records = normalize_yield_outputs(admitted, outputs)
        metadata = {"splits": sorted({row["split"] for row in admitted})}
    else:
        raise BenchmarkProtocolError(f"unsupported scenario {scenario!r}")
    return build_intermediate_artifact(
        SCENARIO_IDS[scenario], records, metadata=metadata
    ), {"records": records}


def run_pipeline(scenario: str, workspace: str | Path) -> dict[str, Any]:
    """Run only the public pipeline and freeze an intermediate artifact."""

    if scenario not in SCENARIO_IDS:
        raise BenchmarkProtocolError(f"unsupported scenario {scenario!r}")
    root = Path(workspace).resolve()
    installation = _mapping(root / "installation.json")
    if installation.get("scenario_id") != SCENARIO_IDS[scenario]:
        raise BenchmarkProtocolError("workspace scenario identity mismatch")
    artifact, _context = _normalize(scenario, root)
    _write_json(root / "results" / "intermediate_results.json", artifact)
    return artifact


def _verify_artifact(artifact: Mapping[str, Any], scenario: str) -> None:
    if artifact.get("scenario_id") != SCENARIO_IDS[scenario]:
        raise BenchmarkProtocolError("intermediate artifact scenario identity mismatch")
    unhashed = dict(artifact)
    claimed = unhashed.pop("trajectory_sha256", None)
    if claimed != sha256_json(unhashed):
        raise BenchmarkProtocolError("intermediate artifact trajectory hash mismatch")


def evaluate_workspace(scenario: str, workspace: str | Path) -> dict[str, Any]:
    """Score a frozen public artifact from the evaluator side of the boundary."""

    root = Path(workspace).resolve()
    artifact = _mapping(root / "results" / "intermediate_results.json")
    _verify_artifact(artifact, scenario)
    recomputed, context = _normalize(scenario, root)
    if artifact != recomputed:
        raise BenchmarkProtocolError("frozen artifact does not match public inputs")
    references = _array(root / "private_evaluator" / "references.json")
    fixture = _fixture_mode(root)
    if scenario == "single_step":
        targets = context["targets"]
        normalized_references = normalize_references(
            targets,
            references,
            **({"canonicalizer": _identity} if fixture else {}),
        )
        metrics = evaluate_predictions(
            context["predictions"],
            normalized_references,
            top_k=int(_mapping(root / "public" / "config.json")["top_k"]),
        )
    elif scenario == "multistep":
        metrics = evaluate_routes(context["records"], references)
    elif scenario == "atom_mapping":
        metrics = evaluate_mappings(context["records"], references)
    elif scenario == "forward":
        kwargs = (
            {
                "isomeric_canonicalizer": _identity,
                "connectivity_canonicalizer": _connectivity,
            }
            if fixture
            else {}
        )
        metrics = evaluate_forward_predictions(
            context["records"],
            references,
            top_k=int(_mapping(root / "public" / "config.json")["top_k"]),
            **kwargs,
        )
    elif scenario == "conditions":
        metrics = evaluate_condition_predictions(
            context["records"],
            references,
            top_k=int(_mapping(root / "public" / "config.json")["top_k"]),
        )
    elif scenario == "yield":
        metrics = evaluate_yield_predictions(context["records"], references)
    else:
        raise BenchmarkProtocolError(f"unsupported scenario {scenario!r}")
    _write_json(root / "results" / "evaluation.json", metrics)
    return metrics


def install_cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install a separated Scenario case")
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(install_test_case(args.case, args.workspace), sort_keys=True))
    return 0


def pipeline_cli(scenario: str, argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Run the {scenario} public pipeline")
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args(argv)
    artifact = run_pipeline(scenario, args.workspace)
    print(artifact["trajectory_sha256"])
    return 0


def evaluator_cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate one frozen Scenario output")
    parser.add_argument("--scenario", choices=tuple(SCENARIO_IDS), required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args(argv)
    metrics = evaluate_workspace(args.scenario, args.workspace)
    print(json.dumps(metrics, sort_keys=True))
    return 0


__all__ = [
    "SCENARIO_IDS",
    "evaluate_workspace",
    "install_test_case",
    "run_pipeline",
]
