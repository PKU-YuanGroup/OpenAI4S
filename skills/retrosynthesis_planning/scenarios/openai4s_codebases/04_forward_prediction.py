#!/usr/bin/env python3
"""Hidden-product forward reaction benchmark protocol for Scenario 4."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any


class BenchmarkProtocolError(ValueError):
    """Raised when a scenario input or frozen output violates its contract."""


def require_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkProtocolError(f"{field} must be a non-empty string")
    return value.strip()


def finite_number(value: Any, *, field: str, allow_none: bool = True) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        suffix = " or null" if allow_none else ""
        raise BenchmarkProtocolError(f"{field} must be a finite number{suffix}")
    number = float(value)
    if not math.isfinite(number):
        raise BenchmarkProtocolError(f"{field} must be finite")
    return number


def positive_int(value: Any, *, field: str, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise BenchmarkProtocolError(f"{field} must be a positive integer")
    if maximum is not None and value > maximum:
        raise BenchmarkProtocolError(f"{field} must be at most {maximum}")
    return value


def json_copy(value: Any, *, field: str) -> Any:
    try:
        return json.loads(json.dumps(value, sort_keys=True, ensure_ascii=True))
    except (TypeError, ValueError) as exc:
        raise BenchmarkProtocolError(f"{field} must be JSON serializable") from exc


def require_exact_fields(
    value: Mapping[str, Any],
    expected: set[str] | frozenset[str],
    *,
    field: str,
) -> None:
    if not isinstance(value, Mapping):
        raise BenchmarkProtocolError(f"{field} must be an object")
    actual = set(value)
    if actual != set(expected):
        raise BenchmarkProtocolError(
            f"{field} has unsupported fields {sorted(actual - set(expected))} "
            f"or missing fields {sorted(set(expected) - actual)}"
        )


def sha256_json(value: Any) -> str:
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def write_json_atomic(path: str | Path, payload: Mapping[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary_name, destination)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def build_intermediate_artifact(
    scenario_id: str,
    records: Any,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, auditable scenario trajectory artifact."""

    payload = {
        "schema_version": 1,
        "scenario_id": require_text(scenario_id, field="scenario_id"),
        "records": json_copy(records, field="records"),
        "metadata": json_copy(dict(metadata or {}), field="metadata"),
    }
    payload["trajectory_sha256"] = sha256_json(payload)
    return payload


Canonicalizer = Callable[[str], str]
INPUT_FIELDS = frozenset({"reaction_id", "reactants", "reagents"})
PAYLOAD_FIELDS = frozenset({"reaction_id", "predictions", "error"})
REFERENCE_FIELDS = frozenset({"reaction_id", "products"})


def identity_isomeric_canonicalize(smiles: str) -> str:
    return require_text(smiles, field="SMILES")


def connectivity_canonicalize(smiles: str) -> str:
    return identity_isomeric_canonicalize(smiles).replace("@", "")


def rdkit_canonicalize(smiles: str) -> str:
    """Canonicalize a SMILES string using RDKit if available, else identity."""
    try:
        from rdkit import Chem
    except ImportError:
        return smiles.strip()
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise BenchmarkProtocolError(f"cannot parse SMILES {smiles!r}")
    for atom in molecule.GetAtoms():
        atom.SetAtomMapNum(0)
    return str(Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True))


def rdkit_connectivity_canonicalize(smiles: str) -> str:
    try:
        from rdkit import Chem
    except ImportError as exc:
        raise RuntimeError("RDKit is required for connectivity evaluation") from exc
    molecule = Chem.MolFromSmiles(require_text(smiles, field="product SMILES"))
    if molecule is None:
        raise BenchmarkProtocolError(f"cannot parse product SMILES {smiles!r}")
    for atom in molecule.GetAtoms():
        atom.SetAtomMapNum(0)
        atom.SetChiralTag(Chem.ChiralType.CHI_UNSPECIFIED)
    for bond in molecule.GetBonds():
        bond.SetStereo(Chem.BondStereo.STEREONONE)
    return str(Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=False))


def normalize_precursor_set(raw: str, *, canonicalizer: Canonicalizer) -> list[str]:
    """Normalize a dot-separated set of precursor SMILES."""
    if not raw.strip():
        return []
    parts = [p.strip() for p in raw.split(".") if p.strip()]
    if not parts:
        return []
    return [canonicalizer(p) for p in parts]


def _normalize_components(
    value: Any,
    *,
    field: str,
    allow_empty: bool,
    canonicalizer: Canonicalizer,
) -> str:
    if allow_empty and value in (None, ""):
        return ""
    raw = require_text(value, field=field)
    return ".".join(normalize_precursor_set(raw, canonicalizer=canonicalizer))


def validate_forward_inputs(
    rows: Iterable[Mapping[str, Any]],
    *,
    canonicalizer: Canonicalizer = rdkit_canonicalize,
) -> tuple[dict[str, str], ...]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    signatures: set[tuple[str, str]] = set()
    for index, row in enumerate(rows, start=1):
        require_exact_fields(row, INPUT_FIELDS, field=f"forward input {index}")
        reaction_id = require_text(row["reaction_id"], field="reaction_id")
        reactants = _normalize_components(
            row["reactants"],
            field="reactants",
            allow_empty=False,
            canonicalizer=canonicalizer,
        )
        reagents = _normalize_components(
            row["reagents"],
            field="reagents",
            allow_empty=True,
            canonicalizer=canonicalizer,
        )
        signature = (reactants, reagents)
        if reaction_id in seen or signature in signatures:
            raise BenchmarkProtocolError(
                "reaction IDs and precursor inputs must be unique"
            )
        seen.add(reaction_id)
        signatures.add(signature)
        result.append(
            {"reaction_id": reaction_id, "reactants": reactants, "reagents": reagents}
        )
    if not result:
        raise BenchmarkProtocolError("forward inputs must not be empty")
    return tuple(result)


def normalize_forward_outputs(
    inputs: Sequence[Mapping[str, str]],
    payloads: Iterable[Mapping[str, Any]],
    *,
    top_k: int,
    isomeric_canonicalizer: Canonicalizer = rdkit_canonicalize,
    connectivity_canonicalizer: Canonicalizer = rdkit_connectivity_canonicalize,
) -> tuple[dict[str, Any], ...]:
    budget = positive_int(top_k, field="top_k", maximum=10)
    input_by_id = {row["reaction_id"]: row for row in inputs}
    payload_by_id: dict[str, Mapping[str, Any]] = {}
    for index, payload in enumerate(payloads, start=1):
        fields = set(payload)
        if (
            not {"reaction_id", "predictions"}.issubset(fields)
            or fields - PAYLOAD_FIELDS
        ):
            raise BenchmarkProtocolError(f"forward payload {index} violates schema")
        reaction_id = require_text(payload["reaction_id"], field="reaction_id")
        if reaction_id not in input_by_id or reaction_id in payload_by_id:
            raise BenchmarkProtocolError(
                f"unknown or duplicate reaction {reaction_id!r}"
            )
        if not isinstance(payload["predictions"], list):
            raise BenchmarkProtocolError("predictions must be an array")
        if len(payload["predictions"]) > budget:
            raise BenchmarkProtocolError("forward output exceeds Top-K budget")
        payload_by_id[reaction_id] = payload
    if set(payload_by_id) != set(input_by_id):
        raise BenchmarkProtocolError("forward output must cover every input")

    result: list[dict[str, Any]] = []
    for row in inputs:
        payload = payload_by_id[row["reaction_id"]]
        beams: list[dict[str, Any]] = []
        seen_ranks: set[int] = set()
        seen_products: dict[str, int] = {}
        for index, raw in enumerate(payload["predictions"], start=1):
            if not isinstance(raw, Mapping):
                raise BenchmarkProtocolError(
                    "each product prediction must be an object"
                )
            rank = raw.get("rank")
            if (
                isinstance(rank, bool)
                or not isinstance(rank, int)
                or not 1 <= rank <= budget
            ):
                raise BenchmarkProtocolError("product rank falls outside Top-K budget")
            if rank in seen_ranks:
                raise BenchmarkProtocolError("product ranks must be unique")
            seen_ranks.add(rank)
            raw_product = raw.get("product_smiles")
            status = "valid"
            isomeric = connectivity = error = None
            if not isinstance(raw_product, str) or not raw_product.strip():
                status = "empty" if isinstance(raw_product, str) else "invalid"
                error = "product_smiles must be a non-empty string"
            elif "." in raw_product:
                status = "invalid"
                error = "prediction must contain one principal product"
            else:
                try:
                    isomeric = require_text(
                        isomeric_canonicalizer(raw_product), field="canonical product"
                    )
                    connectivity = require_text(
                        connectivity_canonicalizer(raw_product),
                        field="connectivity product",
                    )
                except Exception as exc:
                    status = "invalid"
                    error = f"{type(exc).__name__}: {exc}"
            score = None
            try:
                score = finite_number(raw.get("score"), field="score")
            except BenchmarkProtocolError as exc:
                status = "invalid"
                error = error or str(exc)
            duplicate_of = seen_products.get(isomeric) if isomeric is not None else None
            if isomeric is not None:
                seen_products.setdefault(isomeric, rank)
            beams.append(
                {
                    "rank": rank,
                    "raw_product_smiles": raw_product,
                    "isomeric_product": isomeric,
                    "connectivity_product": connectivity,
                    "score": score,
                    "status": status,
                    "duplicate_of_rank": duplicate_of,
                    "error": error,
                    "raw_prediction": json_copy(dict(raw), field="product prediction"),
                }
            )
        beams.sort(key=lambda item: item["rank"])
        result.append(
            {
                **row,
                "predictions": beams,
                "backend_error": json_copy(payload.get("error"), field="backend error"),
            }
        )
    return tuple(result)


def evaluate_forward_predictions(
    predictions: Sequence[Mapping[str, Any]],
    reference_rows: Iterable[Mapping[str, Any]],
    *,
    top_k: int,
    isomeric_canonicalizer: Canonicalizer = rdkit_canonicalize,
    connectivity_canonicalizer: Canonicalizer = rdkit_connectivity_canonicalize,
) -> dict[str, Any]:
    if not predictions:
        raise BenchmarkProtocolError("forward predictions must not be empty")
    budget = positive_int(top_k, field="top_k", maximum=10)
    references: dict[str, tuple[set[str], set[str]]] = {}
    for index, row in enumerate(reference_rows, start=1):
        require_exact_fields(row, REFERENCE_FIELDS, field=f"reference row {index}")
        reaction_id = require_text(row["reaction_id"], field="reaction_id")
        if reaction_id in references:
            raise BenchmarkProtocolError(f"duplicate reference {reaction_id!r}")
        products = row["products"]
        if not isinstance(products, list) or not products:
            raise BenchmarkProtocolError("reference products must be a non-empty array")
        isomeric = {isomeric_canonicalizer(product) for product in products}
        connectivity = {connectivity_canonicalizer(product) for product in products}
        references[reaction_id] = (isomeric, connectivity)
    if {row["reaction_id"] for row in predictions} != set(references):
        raise BenchmarkProtocolError("prediction and reference reactions must match")

    cutoffs = sorted({value for value in (1, 3, 5, budget) if value <= budget})
    rows: list[dict[str, Any]] = []
    submitted = invalid = empty = duplicates = 0
    for prediction in predictions:
        ref_isomeric, ref_connectivity = references[prediction["reaction_id"]]
        iso_ranks: list[int] = []
        connectivity_ranks: list[int] = []
        for beam in prediction["predictions"]:
            submitted += 1
            invalid += beam["status"] == "invalid"
            empty += beam["status"] == "empty"
            duplicates += beam["duplicate_of_rank"] is not None
            if beam["status"] == "valid":
                if beam["isomeric_product"] in ref_isomeric:
                    iso_ranks.append(beam["rank"])
                if beam["connectivity_product"] in ref_connectivity:
                    connectivity_ranks.append(beam["rank"])
        iso_rank = min(iso_ranks) if iso_ranks else None
        conn_rank = min(connectivity_ranks) if connectivity_ranks else None
        rows.append(
            {
                "reaction_id": prediction["reaction_id"],
                "isomeric_first_hit_rank": iso_rank,
                "connectivity_first_hit_rank": conn_rank,
                "stereochemistry_only_error": conn_rank is not None
                and iso_rank is None,
            }
        )
    count = len(rows)
    result = {
        "schema_version": 1,
        "scenario_id": "forward_prediction_uspto_mit_separated_v1",
        "reaction_count": count,
        "isomeric_top_k_accuracy": {
            str(cutoff): sum(
                row["isomeric_first_hit_rank"] is not None
                and row["isomeric_first_hit_rank"] <= cutoff
                for row in rows
            )
            / count
            for cutoff in cutoffs
        },
        "connectivity_top_k_accuracy": {
            str(cutoff): sum(
                row["connectivity_first_hit_rank"] is not None
                and row["connectivity_first_hit_rank"] <= cutoff
                for row in rows
            )
            / count
            for cutoff in cutoffs
        },
        "mean_reciprocal_rank": sum(
            (
                1.0 / row["isomeric_first_hit_rank"]
                if row["isomeric_first_hit_rank"]
                else 0.0
            )
            for row in rows
        )
        / count,
        "stereochemistry_only_error_rate": sum(
            row["stereochemistry_only_error"] for row in rows
        )
        / count,
        "invalid_prediction_rate": invalid / submitted if submitted else 0.0,
        "empty_prediction_rate": empty / submitted if submitted else 0.0,
        "duplicate_prediction_rate": duplicates / submitted if submitted else 0.0,
        "unused_budget_slot_rate": (count * budget - submitted) / (count * budget),
        "reactions": rows,
        "caveat": "Recorded-product recovery is not experimental feasibility or exhaustive byproduct truth.",
    }
    result["result_sha256"] = sha256_json(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Forward reaction-product prediction benchmark"
    )
    parser.add_argument("--workspace", required=True, help="Workspace path")
    args = parser.parse_args()

    workspace = Path(args.workspace)
    try:
        # Load installation.json
        installation_path = workspace / "installation.json"
        with open(installation_path, "r", encoding="utf-8") as f:
            installation = json.load(f)
        if installation.get("scenario_id") != "forward_prediction_uspto_mit_separated_v1":
            raise BenchmarkProtocolError("Invalid scenario_id in installation.json")

        # Load public inputs
        inputs_path = workspace / "public" / "inputs.json"
        with open(inputs_path, "r", encoding="utf-8") as f:
            inputs_data = json.load(f)
        if not isinstance(inputs_data, list):
            raise BenchmarkProtocolError("inputs.json must be an array")

        # Load model outputs
        outputs_path = workspace / "public" / "model_outputs.json"
        with open(outputs_path, "r", encoding="utf-8") as f:
            outputs_data = json.load(f)
        if not isinstance(outputs_data, list):
            raise BenchmarkProtocolError("model_outputs.json must be an array")

        # Load config
        config_path = workspace / "public" / "config.json"
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        top_k = config.get("top_k")
        random_seed = config.get("random_seed")
        if not isinstance(top_k, int) or top_k < 1 or top_k > 10:
            raise BenchmarkProtocolError("top_k must be an integer between 1 and 10")
        if not isinstance(random_seed, int):
            raise BenchmarkProtocolError("random_seed must be an integer")

        # Validate and normalize inputs
        normalized_inputs = validate_forward_inputs(
            inputs_data, canonicalizer=identity_isomeric_canonicalize
        )

        # Normalize outputs
        normalized_outputs = normalize_forward_outputs(
            normalized_inputs,
            outputs_data,
            top_k=top_k,
            isomeric_canonicalizer=identity_isomeric_canonicalize,
            connectivity_canonicalizer=connectivity_canonicalize,
        )

        # Build intermediate artifact
        artifact = build_intermediate_artifact(
            "forward_prediction_uspto_mit_separated_v1",
            normalized_outputs,
            metadata={"top_k": top_k},
        )

        # Write output
        output_path = workspace / "results" / "intermediate_results.json"
        write_json_atomic(output_path, artifact)

        return 0
    except (BenchmarkProtocolError, json.JSONDecodeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
