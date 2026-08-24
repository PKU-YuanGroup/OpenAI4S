"""Curated atom-mapping and changed-bond protocol for Scenario 3."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .benchmark_common import (
    BenchmarkProtocolError,
    finite_number,
    json_copy,
    require_exact_fields,
    require_text,
    sha256_json,
)

REACTION_FIELDS = frozenset({"reaction_id", "reaction_smiles"})
PREDICTION_FIELDS = frozenset(
    {"reaction_id", "mapped_reaction", "confidence", "atom_correspondence"}
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


__all__ = [
    "BenchmarkProtocolError",
    "analyze_mapping_prediction",
    "evaluate_mappings",
    "normalize_mapping_outputs",
    "validate_public_reactions",
]
