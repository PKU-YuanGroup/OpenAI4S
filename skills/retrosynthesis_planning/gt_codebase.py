"""Separated pipeline/test-case runtime for the six retrosynthesis Scenarios."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
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
from .single_step_benchmark import (
    build_intermediate_results,
    evaluate_predictions,
    normalize_prediction_payloads,
    normalize_references,
    sha256_file,
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
#: The private digests live inside the private boundary, never in the manifest
#: the public pipeline reads: publishing the reference file's SHA-256 to the
#: public side turns the frozen answer into a brute-forceable oracle.
_PRIVATE_MANIFEST_NAME = "installation_private.json"
#: Bundled cases are the only authority that lives outside the workspace. A
#: manifest stored beside the files it describes cannot vouch for either of
#: them: rewriting a reference file and its own digest is two edits, not one.
_BUNDLED_CASES = Path(__file__).resolve().parent / "scenarios" / "test_cases"
_CASE_NAME = re.compile(r"^[0-9a-z][0-9a-z_]*$")
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


def _canonical_bytes(value: Any) -> bytes:
    """Reproduce exactly what ``write_json_atomic`` puts on disk."""

    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _frozen_case(installation: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Resolve the bundled case this workspace was installed from, if any."""

    name = installation.get("case_name")
    if not isinstance(name, str) or not _CASE_NAME.fullmatch(name):
        return None
    source = _BUNDLED_CASES / f"{name}.json"
    if not source.is_file():
        return None
    if installation.get("case_sha256") != sha256_file(source):
        raise BenchmarkProtocolError("installed case does not match the frozen case")
    return _mapping(source)


def _frozen_hashes(case: Mapping[str, Any], boundary: str) -> dict[str, str]:
    files = case.get(boundary)
    if not isinstance(files, Mapping):
        raise BenchmarkProtocolError("public and private_evaluator must be objects")
    return {
        f"{boundary}/{name}": hashlib.sha256(_canonical_bytes(payload)).hexdigest()
        for name, payload in files.items()
    }


def _fixture_mode(workspace: Path) -> bool:
    installation = _mapping(workspace / "installation.json")
    return installation.get("dataset_profile") == "synthetic_protocol_smoke"


def _verify_hashes(workspace: Path, hashes: Any, *, boundary: str) -> None:
    if not isinstance(hashes, Mapping):
        raise BenchmarkProtocolError("installation file_sha256 must be an object")
    for relative, expected in hashes.items():
        parts = relative.split("/") if isinstance(relative, str) else []
        if (
            len(parts) != 2
            or parts[0] != boundary
            or not _SAFE_JSON_NAME.fullmatch(parts[1])
        ):
            raise BenchmarkProtocolError("installation contains an unsafe file name")
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise BenchmarkProtocolError(f"invalid installed file hash: {relative}")
        target = workspace / relative
        if target.parent.is_symlink() or target.is_symlink():
            raise BenchmarkProtocolError(
                f"installed file must not be a symlink: {relative}"
            )
        try:
            actual = sha256_file(target)
        except OSError as exc:
            raise BenchmarkProtocolError(
                f"installed file is unavailable: {relative}"
            ) from exc
        if actual != expected:
            raise BenchmarkProtocolError(f"installed file hash mismatch: {relative}")


def _verify_installation(
    workspace: Path, scenario: str, *, include_private: bool
) -> None:
    """Check the installed dataset without opening the other process's boundary."""

    if scenario not in SCENARIO_IDS:
        raise BenchmarkProtocolError(f"unsupported scenario {scenario!r}")
    installation = _mapping(workspace / "installation.json")
    if (
        installation.get("scenario") != scenario
        or installation.get("scenario_id") != SCENARIO_IDS[scenario]
    ):
        raise BenchmarkProtocolError("workspace scenario identity mismatch")
    hashes = installation.get("file_sha256")
    if not isinstance(hashes, Mapping):
        raise BenchmarkProtocolError("installation file_sha256 must be an object")
    required = {"public/inputs.json", "public/model_outputs.json", "public/config.json"}
    extra_public = {
        "single_step": "model_manifest.json",
        "multistep": "stock.json",
        "conditions": "vocabulary.json",
    }.get(scenario)
    if extra_public:
        required.add(f"public/{extra_public}")
    if not required.issubset(hashes):
        raise BenchmarkProtocolError("installation is missing required file hashes")
    # Validates the anchor itself; the public digests stay the manifest's.
    # Substituting public inputs against an installed case is a supported
    # workflow, so the frozen case does not overrule them.
    case = _frozen_case(installation)
    _verify_hashes(workspace, hashes, boundary="public")
    if not include_private:
        return
    private = _mapping(workspace / "private_evaluator" / _PRIVATE_MANIFEST_NAME)
    if (
        private.get("scenario") != scenario
        or private.get("scenario_id") != SCENARIO_IDS[scenario]
    ):
        raise BenchmarkProtocolError("workspace scenario identity mismatch")
    private_hashes = private.get("file_sha256")
    if not isinstance(private_hashes, Mapping):
        raise BenchmarkProtocolError("installation file_sha256 must be an object")
    if "private_evaluator/references.json" not in private_hashes:
        raise BenchmarkProtocolError("installation is missing required file hashes")
    if case is not None:
        # The ground truth is the half a manifest stored beside it cannot
        # vouch for: rewriting references.json and its own digest is two edits,
        # not one, so the authority has to live outside the workspace.
        private_hashes = _frozen_hashes(case, "private_evaluator")
    # The public manifest is not covered by any digest, so the profile that
    # selects identity-vs-RDKit canonicalization is cross-checked against the
    # copy frozen behind the boundary the public side cannot write.
    if private.get("dataset_profile") != installation.get("dataset_profile"):
        raise BenchmarkProtocolError("workspace dataset profile mismatch")
    _verify_hashes(workspace, private_hashes, boundary="private_evaluator")


def install_test_case(case_path: str | Path, workspace: str | Path) -> dict[str, Any]:
    """Install one bundled case while preserving the public/private boundary."""

    source = Path(case_path).resolve()
    requested = Path(workspace)
    if requested.is_symlink():
        raise BenchmarkProtocolError("workspace must not be a symlink")
    destination = requested.resolve()
    case = _mapping(source)
    require_exact_fields(case, _CASE_FIELDS, field="test case")
    scenario = require_text(case["scenario"], field="scenario")
    if scenario not in SCENARIO_IDS or case["scenario_id"] != SCENARIO_IDS[scenario]:
        raise BenchmarkProtocolError("test case scenario identity mismatch")
    if destination.exists():
        if not destination.is_dir():
            raise BenchmarkProtocolError("workspace must be a directory")
        if any(destination.iterdir()):
            raise BenchmarkProtocolError("workspace must not already contain files")
    public = case["public"]
    private = case["private_evaluator"]
    if not isinstance(public, Mapping) or not isinstance(private, Mapping):
        raise BenchmarkProtocolError("public and private_evaluator must be objects")
    # Names are validated up front: a rejection partway through the write loop
    # used to leave the ground truth on disk with no installation.json, and the
    # non-empty guard above then blocked every retry into that workspace.
    for files in (public, private):
        for name in sorted(files):
            if not isinstance(name, str) or not _SAFE_JSON_NAME.fullmatch(name):
                raise BenchmarkProtocolError(f"unsafe test-case file name {name!r}")
            if name == _PRIVATE_MANIFEST_NAME:
                raise BenchmarkProtocolError(f"reserved test-case file name {name!r}")
    dataset = case["dataset"]
    if not isinstance(dataset, Mapping):
        raise BenchmarkProtocolError("dataset must be an object")
    written: dict[str, str] = {}
    private_written: dict[str, str] = {}
    for boundary, files in (("public", public), ("private_evaluator", private)):
        for name, payload in sorted(files.items()):
            target = destination / boundary / name
            write_json_atomic(target, payload)
            digest = sha256_file(target)
            if boundary == "public":
                written[f"{boundary}/{name}"] = digest
            else:
                private_written[f"{boundary}/{name}"] = digest
    (destination / "results").mkdir(parents=True, exist_ok=True)
    case_name = source.stem
    manifest = {
        "schema_version": 3,
        "case_name": case_name if _CASE_NAME.fullmatch(case_name) else None,
        "case_sha256": sha256_file(source),
        "scenario": scenario,
        "scenario_id": SCENARIO_IDS[scenario],
        "query_source": case["query_source"],
        "dataset_profile": dataset.get("profile"),
        "dataset": dict(dataset),
        "file_sha256": written,
        "ground_truth_boundary": "private_evaluator",
    }
    write_json_atomic(
        destination / "private_evaluator" / _PRIVATE_MANIFEST_NAME,
        {
            "schema_version": 2,
            "case_name": manifest["case_name"],
            "case_sha256": manifest["case_sha256"],
            "scenario": scenario,
            "scenario_id": SCENARIO_IDS[scenario],
            "dataset_profile": dataset.get("profile"),
            "file_sha256": private_written,
        },
    )
    write_json_atomic(destination / "installation.json", manifest)
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
    seen: set[str] = set()
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
        if reaction_id not in expected or reaction_id in seen:
            raise BenchmarkProtocolError("unknown or duplicate fixture mapping output")
        seen.add(reaction_id)
        if not isinstance(output["valid"], bool):
            raise BenchmarkProtocolError("fixture mapping valid must be a boolean")
        for field in ("correspondence", "bond_changes", "issues"):
            if not isinstance(output[field], list):
                raise BenchmarkProtocolError(
                    f"fixture mapping {field} must be an array"
                )
            for value in output[field]:
                require_text(value, field=f"fixture mapping {field} entry")
        records.append(dict(output))
    if seen != expected:
        raise BenchmarkProtocolError("fixture mapping output must cover every reaction")
    return tuple(records)


def _config_int(config: Mapping[str, Any], field: str) -> int:
    """Read one integer config value without silently coercing another type.

    ``int(...)`` accepted ``"3"``, ``3.9`` and ``True`` here and produced an
    artifact byte-identical to the canonical one, so the byte-equality gate
    could never see the divergence from the generated CLIs, which refuse them.
    """

    if field not in config:
        raise BenchmarkProtocolError(f"config is missing {field}")
    value = config[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise BenchmarkProtocolError(f"config {field} must be an integer")
    return value


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
            targets, outputs, top_k=_config_int(config, "top_k"), **kwargs
        )
        artifact = build_intermediate_results(
            targets,
            predictions,
            top_k=_config_int(config, "top_k"),
            model_manifest=_mapping(workspace / "public" / "model_manifest.json"),
            random_seed=_config_int(config, "random_seed"),
        )
        return artifact, {
            "config": config,
            "targets": targets,
            "predictions": predictions,
        }
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
            max_routes=_config_int(config, "max_routes"),
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
            admitted, outputs, top_k=_config_int(config, "top_k"), **kwargs
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
            top_k=_config_int(config, "top_k"),
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
    ), {"config": config, "records": records}


def run_pipeline(scenario: str, workspace: str | Path) -> dict[str, Any]:
    """Run only the public pipeline and freeze an intermediate artifact."""

    root = Path(workspace).resolve()
    _verify_installation(root, scenario, include_private=False)
    artifact, _context = _normalize(scenario, root)
    write_json_atomic(root / "results" / "intermediate_results.json", artifact)
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
    _verify_installation(root, scenario, include_private=True)
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
            top_k=_config_int(context["config"], "top_k"),
        )
    elif scenario == "multistep":
        # Multistep is normalized under the fixture canonicalizer, so it must
        # be scored under it too: evaluate_routes otherwise picks whatever
        # chemistry is importable and the frozen metrics become env-dependent.
        metrics = evaluate_routes(
            context["records"],
            references,
            **({"canonicalizer": _identity} if fixture else {}),
        )
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
            top_k=_config_int(context["config"], "top_k"),
            **kwargs,
        )
    elif scenario == "conditions":
        metrics = evaluate_condition_predictions(
            context["records"],
            references,
            top_k=_config_int(context["config"], "top_k"),
        )
    elif scenario == "yield":
        metrics = evaluate_yield_predictions(context["records"], references)
    else:
        raise BenchmarkProtocolError(f"unsupported scenario {scenario!r}")
    write_json_atomic(root / "results" / "evaluation.json", metrics)
    return metrics


def _refuse(error: Exception) -> int:
    """Report a protocol refusal the way every Scenario query requires.

    A traceback is not a refusal: it prints this module's internals and the
    operator's absolute paths, and the verifier that captures it cannot tell
    it apart from a crash.
    """

    print(f"Error: {error}", file=sys.stderr)
    return 1


def install_cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install a separated Scenario case")
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = install_test_case(args.case, args.workspace)
    except (BenchmarkProtocolError, OSError, json.JSONDecodeError) as error:
        return _refuse(error)
    print(json.dumps(manifest, sort_keys=True))
    return 0


def pipeline_cli(scenario: str, argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Run the {scenario} public pipeline")
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        artifact = run_pipeline(scenario, args.workspace)
    except (BenchmarkProtocolError, OSError, json.JSONDecodeError) as error:
        return _refuse(error)
    print(artifact["trajectory_sha256"])
    return 0


def evaluator_cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate one frozen Scenario output")
    parser.add_argument("--scenario", choices=tuple(SCENARIO_IDS), required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        metrics = evaluate_workspace(args.scenario, args.workspace)
    except (BenchmarkProtocolError, OSError, json.JSONDecodeError) as error:
        return _refuse(error)
    print(json.dumps(metrics, sort_keys=True))
    return 0


__all__ = [
    "SCENARIO_IDS",
    "evaluate_workspace",
    "install_test_case",
    "run_pipeline",
]
