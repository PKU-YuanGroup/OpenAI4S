#!/usr/bin/env python3
"""Curated atom-mapping and changed-bond protocol for Scenario 3."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
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


REACTION_FIELDS = frozenset({"reaction_id", "reaction_smiles"})
PREDICTION_FIELDS = frozenset(
    {"reaction_id", "mapped_reaction", "confidence", "atom_correspondence", "error"}
)
REFERENCE_FIELDS = frozenset(
    {"reaction_id", "equivalent_correspondences", "bond_changes", "ambiguous"}
)
_ATOM_MAP = re.compile(r":\d+\]")


def _chem():
    try:
        from rdkit import Chem
    except ImportError as exc:  # pragma: no cover - optional environment
        raise RuntimeError("RDKit is required for atom-mapping evaluation") from exc
    return Chem


def _split_reaction(value: Any) -> tuple[str, str]:
    reaction = require_text(value, field="reaction_smiles")
    if reaction.count(">") == 2:
        reactants, _reagents, products = reaction.split(">")
    elif reaction.count(">>") == 1:
        reactants, products = reaction.split(">>")
    else:
        raise BenchmarkProtocolError("reaction must contain reactant and product sides")
    if not reactants or not products:
        raise BenchmarkProtocolError("reaction sides must not be empty")
    return reactants, products


def validate_public_reactions(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, str], ...]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows, start=1):
        require_exact_fields(row, REACTION_FIELDS, field=f"reaction row {index}")
        reaction_id = require_text(row["reaction_id"], field="reaction_id")
        reaction = require_text(row["reaction_smiles"], field="reaction_smiles")
        _split_reaction(reaction)
        if _ATOM_MAP.search(reaction):
            raise BenchmarkProtocolError("public reactions must not contain atom maps")
        if reaction_id in seen:
            raise BenchmarkProtocolError(f"duplicate reaction_id {reaction_id!r}")
        seen.add(reaction_id)
        result.append({"reaction_id": reaction_id, "reaction_smiles": reaction})
    if not result:
        raise BenchmarkProtocolError("reaction set must not be empty")
    return tuple(result)


def _mapped_side(side: str, *, prefix: str) -> dict[str, Any]:
    chem = _chem()
    atoms: dict[int, dict[str, Any]] = {}
    bonds: dict[tuple[int, int], float] = {}
    unmapped: list[str] = []
    duplicates: list[int] = []
    for component_index, component in enumerate(side.split(".")):
        molecule = chem.MolFromSmiles(component)
        if molecule is None:
            raise BenchmarkProtocolError(f"cannot parse mapped component {component!r}")
        for atom in molecule.GetAtoms():
            stable_id = f"{prefix}{component_index}:a{atom.GetIdx()}"
            map_number = int(atom.GetAtomMapNum())
            if map_number <= 0:
                unmapped.append(stable_id)
                continue
            if map_number in atoms:
                duplicates.append(map_number)
            atoms[map_number] = {
                "stable_id": stable_id,
                "element": atom.GetSymbol(),
            }
        for bond in molecule.GetBonds():
            left = int(bond.GetBeginAtom().GetAtomMapNum())
            right = int(bond.GetEndAtom().GetAtomMapNum())
            if left > 0 and right > 0:
                bonds[tuple(sorted((left, right)))] = float(bond.GetBondTypeAsDouble())
    return {
        "atoms": atoms,
        "bonds": bonds,
        "unmapped": unmapped,
        "duplicates": sorted(set(duplicates)),
    }


def _canonical_side_without_maps(side: str) -> tuple[str, ...]:
    chem = _chem()
    result: list[str] = []
    for component in side.split("."):
        molecule = chem.MolFromSmiles(component)
        if molecule is None:
            raise BenchmarkProtocolError(
                f"cannot parse reaction component {component!r}"
            )
        for atom in molecule.GetAtoms():
            atom.SetAtomMapNum(0)
        result.append(
            str(chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True))
        )
    return tuple(sorted(result))


def _correspondence_entries(value: Any) -> dict[int, tuple[str, str]]:
    if not isinstance(value, list):
        raise BenchmarkProtocolError("atom_correspondence must be an array")
    result: dict[int, tuple[str, str]] = {}
    pairs: set[tuple[str, str]] = set()
    for index, entry in enumerate(value, start=1):
        if not isinstance(entry, Mapping):
            raise BenchmarkProtocolError(f"correspondence {index} must be an object")
        require_exact_fields(
            entry,
            {"map_num", "reactant_atom", "product_atom"},
            field=f"correspondence {index}",
        )
        map_num = entry["map_num"]
        if isinstance(map_num, bool) or not isinstance(map_num, int) or map_num < 1:
            raise BenchmarkProtocolError("map_num must be a positive integer")
        pair = (
            require_text(entry["reactant_atom"], field="reactant_atom"),
            require_text(entry["product_atom"], field="product_atom"),
        )
        if map_num in result or pair in pairs:
            raise BenchmarkProtocolError("atom correspondence must be one-to-one")
        result[map_num] = pair
        pairs.add(pair)
    return result


def _atom_identity(pair: tuple[str, str]) -> str:
    return f"{pair[0]}=>{pair[1]}"


def analyze_mapping_prediction(
    original_reaction: str,
    prediction: Mapping[str, Any],
) -> dict[str, Any]:
    require_exact_fields(prediction, PREDICTION_FIELDS, field="mapping prediction")
    mapped = require_text(prediction["mapped_reaction"], field="mapped_reaction")
    mapped_reactants, mapped_products = _split_reaction(mapped)
    original_reactants, original_products = _split_reaction(original_reaction)
    reactant_side = _mapped_side(mapped_reactants, prefix="r")
    product_side = _mapped_side(mapped_products, prefix="p")
    supplied = _correspondence_entries(prediction["atom_correspondence"])
    common_maps = set(reactant_side["atoms"]) & set(product_side["atoms"])
    issues: list[str] = []
    if _canonical_side_without_maps(mapped_reactants) != _canonical_side_without_maps(
        original_reactants
    ) or _canonical_side_without_maps(mapped_products) != _canonical_side_without_maps(
        original_products
    ):
        issues.append("mapped_reaction_structure_mismatch")
    if set(supplied) != common_maps:
        issues.append("correspondence_map_set_mismatch")
    for map_num in common_maps:
        if (
            reactant_side["atoms"][map_num]["element"]
            != product_side["atoms"][map_num]["element"]
        ):
            issues.append("element_mismatch")
        expected_pair = (
            reactant_side["atoms"][map_num]["stable_id"],
            product_side["atoms"][map_num]["stable_id"],
        )
        if supplied.get(map_num) != expected_pair:
            issues.append("correspondence_atom_id_mismatch")
    if reactant_side["duplicates"] or product_side["duplicates"]:
        issues.append("duplicate_map_number")
    if reactant_side["unmapped"] or product_side["unmapped"]:
        issues.append("unmapped_atoms")

    formed: list[str] = []
    broken: list[str] = []
    order_changed: list[str] = []
    reactant_bonds = reactant_side["bonds"]
    product_bonds = product_side["bonds"]
    all_bonds = set(reactant_bonds) | set(product_bonds)

    def atom_identity(map_num: int) -> str:
        if map_num in supplied:
            return _atom_identity(supplied[map_num])
        if map_num in reactant_side["atoms"]:
            return f"{reactant_side['atoms'][map_num]['stable_id']}=>absent"
        if map_num in product_side["atoms"]:
            return f"absent=>{product_side['atoms'][map_num]['stable_id']}"
        raise BenchmarkProtocolError(f"bond references unknown map number {map_num}")

    for endpoints in sorted(all_bonds):
        atom_ids = sorted(atom_identity(endpoint) for endpoint in endpoints)
        prefix = "|".join(atom_ids)
        old_order = reactant_bonds.get(endpoints)
        new_order = product_bonds.get(endpoints)
        if old_order is None:
            formed.append(f"formed:{prefix}:{new_order:g}")
        elif new_order is None:
            broken.append(f"broken:{prefix}:{old_order:g}")
        elif old_order != new_order:
            order_changed.append(f"order:{prefix}:{old_order:g}>{new_order:g}")
    correspondence_pairs = sorted(_atom_identity(pair) for pair in supplied.values())
    return {
        "reaction_id": require_text(prediction["reaction_id"], field="reaction_id"),
        "original_reaction": original_reaction,
        "mapped_reaction": mapped,
        "confidence": finite_number(prediction["confidence"], field="confidence"),
        "correspondence": correspondence_pairs,
        "formed_bonds": formed,
        "broken_bonds": broken,
        "order_changed_bonds": order_changed,
        "bond_changes": sorted(formed + broken + order_changed),
        "unmapped_reactant_atoms": reactant_side["unmapped"],
        "unmapped_product_atoms": product_side["unmapped"],
        "issues": sorted(set(issues)),
        "valid": not issues,
        "raw_prediction": json_copy(dict(prediction), field="mapping prediction"),
    }


def normalize_mapping_outputs(
    reactions: Sequence[Mapping[str, str]],
    predictions: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    reaction_by_id = {row["reaction_id"]: row for row in reactions}
    prediction_by_id: dict[str, Mapping[str, Any]] = {}
    for prediction in predictions:
        reaction_id = require_text(prediction.get("reaction_id"), field="reaction_id")
        if reaction_id not in reaction_by_id or reaction_id in prediction_by_id:
            raise BenchmarkProtocolError(
                f"unknown or duplicate reaction {reaction_id!r}"
            )
        prediction_by_id[reaction_id] = prediction
    if set(prediction_by_id) != set(reaction_by_id):
        raise BenchmarkProtocolError("mapping output must cover every reaction")
    return tuple(
        analyze_mapping_prediction(
            reaction_by_id[reaction_id]["reaction_smiles"],
            prediction_by_id[reaction_id],
        )
        for reaction_id in reaction_by_id
    )


def _f1(predicted: set[str], reference: set[str]) -> tuple[float, float, float]:
    true_positive = len(predicted & reference)
    precision = (
        true_positive / len(predicted) if predicted else (1.0 if not reference else 0.0)
    )
    recall = (
        true_positive / len(reference) if reference else (1.0 if not predicted else 0.0)
    )
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def evaluate_mappings(
    predictions: Sequence[Mapping[str, Any]],
    reference_rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    # The non-empty invariant is enforced on the normalize side by every
    # validate_* helper; without it here the metric divisions below reduce
    # to a bare ZeroDivisionError instead of a protocol refusal.
    if not predictions:
        raise BenchmarkProtocolError("mapping predictions must not be empty")
    refs: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(reference_rows, start=1):
        require_exact_fields(row, REFERENCE_FIELDS, field=f"reference row {index}")
        reaction_id = require_text(row["reaction_id"], field="reaction_id")
        if reaction_id in refs:
            raise BenchmarkProtocolError(f"duplicate reference {reaction_id!r}")
        refs[reaction_id] = row
    if {row["reaction_id"] for row in predictions} != set(refs):
        raise BenchmarkProtocolError("prediction and reference reactions must match")

    rows: list[dict[str, Any]] = []
    for prediction in predictions:
        reference = refs[prediction["reaction_id"]]
        alternatives = reference["equivalent_correspondences"]
        if not isinstance(alternatives, list) or not alternatives:
            raise BenchmarkProtocolError("equivalent_correspondences must not be empty")
        normalized_alternatives: list[set[str]] = []
        for alternative in alternatives:
            if not isinstance(alternative, list):
                raise BenchmarkProtocolError(
                    "each equivalent correspondence must be an array"
                )
            pairs: set[str] = set()
            for pair in alternative:
                if not isinstance(pair, Mapping):
                    raise BenchmarkProtocolError(
                        "equivalent correspondence entries must be objects"
                    )
                require_exact_fields(
                    pair,
                    {"reactant_atom", "product_atom"},
                    field="equivalent correspondence",
                )
                pairs.add(
                    _atom_identity(
                        (
                            require_text(pair["reactant_atom"], field="reactant_atom"),
                            require_text(pair["product_atom"], field="product_atom"),
                        )
                    )
                )
            normalized_alternatives.append(pairs)
        predicted_pairs = set(prediction["correspondence"])
        mapping_exact = any(
            predicted_pairs == alternative for alternative in normalized_alternatives
        )
        reference_changes = set(reference["bond_changes"])
        precision, recall, f1 = _f1(set(prediction["bond_changes"]), reference_changes)
        rows.append(
            {
                "reaction_id": prediction["reaction_id"],
                "ambiguous": bool(reference["ambiguous"]),
                "mapping_exact": mapping_exact,
                "bond_change_precision": precision,
                "bond_change_recall": recall,
                "bond_change_f1": f1,
                "valid": prediction["valid"],
            }
        )
    unambiguous = [row for row in rows if not row["ambiguous"]]
    result = {
        "schema_version": 1,
        "scenario_id": "reaction_atom_mapping_curated_v1",
        "reaction_count": len(rows),
        "unambiguous_count": len(unambiguous),
        "whole_reaction_exact_mapping_rate": (
            sum(row["mapping_exact"] for row in unambiguous) / len(unambiguous)
            if unambiguous
            else None
        ),
        "mean_bond_change_f1": sum(row["bond_change_f1"] for row in rows) / len(rows),
        "valid_mapping_rate": sum(row["valid"] for row in rows) / len(rows),
        "reactions": rows,
        "caveat": "Mapping confidence and bond-change accuracy are not reaction-success probabilities.",
    }
    result["result_sha256"] = sha256_json(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Curated atom-mapping and changed-bond protocol for Scenario 3."
    )
    parser.add_argument("--workspace", required=True, type=Path, help="Workspace path")
    args = parser.parse_args()

    workspace = args.workspace
    try:
        installation_path = workspace / "installation.json"
        with open(installation_path, "r", encoding="utf-8") as f:
            installation = json.load(f)
        if installation.get("scenario_id") != "reaction_atom_mapping_curated_v1":
            raise BenchmarkProtocolError(
                "installation scenario_id must be reaction_atom_mapping_curated_v1"
            )
        public_inputs_path = workspace / "public" / "inputs.json"
        with open(public_inputs_path, "r", encoding="utf-8") as f:
            public_inputs = json.load(f)
        if not isinstance(public_inputs, list):
            raise BenchmarkProtocolError("public/inputs.json must be an array")
        reactions = validate_public_reactions(public_inputs)

        model_outputs_path = workspace / "public" / "model_outputs.json"
        with open(model_outputs_path, "r", encoding="utf-8") as f:
            model_outputs = json.load(f)
        if not isinstance(model_outputs, list):
            raise BenchmarkProtocolError("public/model_outputs.json must be an array")

        config_path = workspace / "public" / "config.json"
        with open(config_path, "r", encoding="utf-8") as f:
            json.load(f)
        # Validate exact fixture schema for model_outputs
        for index, row in enumerate(model_outputs, start=1):
            require_exact_fields(
                row,
                {"reaction_id", "correspondence", "bond_changes", "valid", "issues"},
                field=f"model_outputs row {index}",
            )
            if not isinstance(row["correspondence"], list):
                raise BenchmarkProtocolError(
                    f"model_outputs row {index} correspondence must be an array"
                )
            if not isinstance(row["bond_changes"], list):
                raise BenchmarkProtocolError(
                    f"model_outputs row {index} bond_changes must be an array"
                )
            if not isinstance(row["valid"], bool):
                raise BenchmarkProtocolError(
                    f"model_outputs row {index} valid must be a boolean"
                )
            if not isinstance(row["issues"], list):
                raise BenchmarkProtocolError(
                    f"model_outputs row {index} issues must be an array"
                )

        # Validate coverage
        reaction_ids = {r["reaction_id"] for r in reactions}
        prediction_ids = {p["reaction_id"] for p in model_outputs}
        if reaction_ids != prediction_ids:
            raise BenchmarkProtocolError(
                "model_outputs must cover exactly the public reactions"
            )

        # Build intermediate artifact
        artifact = build_intermediate_artifact(
            "reaction_atom_mapping_curated_v1",
            model_outputs,
            metadata={"fixture_preanalyzed": True},
        )

        output_path = workspace / "results" / "intermediate_results.json"
        write_json_atomic(output_path, artifact)
        return 0
    except (BenchmarkProtocolError, OSError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
