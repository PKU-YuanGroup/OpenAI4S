"""Offline protocol and evaluator for class-unknown single-step retrosynthesis.

The model boundary lives in :mod:`external_backends`.  This module owns the
scientific benchmark boundary after inference: strict public-input admission,
unordered precursor-set normalization, full-beam diagnostics, and private
multi-reference scoring.  RDKit is optional at import time and required only
when the default chemistry canonicalizer is called.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import tempfile
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_TOP_K = 10
PUBLIC_TARGET_FIELDS = frozenset({"target_id", "product_smiles"})
PREDICTION_PAYLOAD_FIELDS = frozenset({"target_id", "predictions", "error"})
REFERENCE_FIELDS = frozenset({"target_id", "precursor_sets"})

Canonicalizer = Callable[[str], str]


class SingleStepProtocolError(ValueError):
    """Raised when public inputs or frozen outputs violate the protocol."""


class ChemistryDependencyError(RuntimeError):
    """Raised when chemistry canonicalization is requested without RDKit."""


def rdkit_canonicalize(smiles: str) -> str:
    """Return a map-free canonical isomeric SMILES for one molecule.

    This deliberately performs no tautomer, salt, neutralization, or
    stereochemistry normalization.  Those transformations would change the
    benchmark identity rather than merely canonicalize its representation.
    """

    try:
        from rdkit import Chem
    except ImportError as exc:  # pragma: no cover - depends on optional env
        raise ChemistryDependencyError(
            "RDKit is required for scientific SMILES canonicalization"
        ) from exc
    if not isinstance(smiles, str) or not smiles.strip():
        raise SingleStepProtocolError("SMILES must be a non-empty string")
    value = smiles.strip()
    if "." in value:
        raise SingleStepProtocolError(
            "canonicalize one molecule at a time; dot-separated sets are handled "
            "by normalize_precursor_set"
        )
    molecule = Chem.MolFromSmiles(value)
    if molecule is None:
        raise SingleStepProtocolError(f"cannot parse SMILES {value!r}")
    for atom in molecule.GetAtoms():
        atom.SetAtomMapNum(0)
    return str(Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True))


def _text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SingleStepProtocolError(f"{field} must be a non-empty string")
    return value.strip()


def _json_copy(value: Any, *, field: str) -> Any:
    try:
        return json.loads(json.dumps(value, sort_keys=True, ensure_ascii=True))
    except (TypeError, ValueError) as exc:
        raise SingleStepProtocolError(f"{field} must be JSON serializable") from exc


def _score(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SingleStepProtocolError("prediction score must be a number or null")
    result = float(value)
    if not math.isfinite(result):
        raise SingleStepProtocolError("prediction score must be finite")
    return result


def _top_k(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SingleStepProtocolError("top_k must be an integer")
    if not 1 <= value <= MAX_TOP_K:
        raise SingleStepProtocolError(f"top_k must be between 1 and {MAX_TOP_K}")
    return value


def _signature(components: Sequence[str]) -> str:
    return ".".join(components)


def normalize_precursor_set(
    value: Any, *, canonicalizer: Canonicalizer = rdkit_canonicalize
) -> tuple[str, ...]:
    """Normalize a dot-separated precursor set as an unordered multiset."""

    raw = _text(value, field="reactants_smiles")
    parts = raw.split(".")
    if any(not part.strip() for part in parts):
        raise SingleStepProtocolError("precursor set contains an empty component")
    canonical: list[str] = []
    for part in parts:
        normalized = canonicalizer(part.strip())
        canonical.append(_text(normalized, field="canonical precursor"))
    canonical.sort()
    return tuple(canonical)


@dataclass(frozen=True, slots=True)
class PublicTarget:
    """A public target after class-unknown schema admission."""

    target_id: str
    product_smiles: str

    def to_dict(self) -> dict[str, str]:
        return {"target_id": self.target_id, "product_smiles": self.product_smiles}


def validate_public_targets(
    rows: Iterable[Mapping[str, Any]],
    *,
    canonicalizer: Canonicalizer = rdkit_canonicalize,
) -> tuple[PublicTarget, ...]:
    """Admit only anonymous product inputs; reject class/reference leakage."""

    targets: list[PublicTarget] = []
    seen_ids: set[str] = set()
    seen_products: set[str] = set()
    for index, raw_row in enumerate(rows, start=1):
        if not isinstance(raw_row, Mapping):
            raise SingleStepProtocolError(f"target row {index} must be an object")
        fields = set(raw_row)
        if fields != PUBLIC_TARGET_FIELDS:
            unexpected = sorted(fields - PUBLIC_TARGET_FIELDS)
            missing = sorted(PUBLIC_TARGET_FIELDS - fields)
            details: list[str] = []
            if unexpected:
                details.append("unsupported/hidden fields: " + ", ".join(unexpected))
            if missing:
                details.append("missing fields: " + ", ".join(missing))
            raise SingleStepProtocolError(
                f"target row {index} violates class-unknown schema ("
                + "; ".join(details)
                + ")"
            )
        target_id = _text(raw_row["target_id"], field=f"target row {index}.target_id")
        if target_id in seen_ids:
            raise SingleStepProtocolError(f"duplicate target_id {target_id!r}")
        product_raw = _text(
            raw_row["product_smiles"], field=f"target row {index}.product_smiles"
        )
        if "." in product_raw:
            raise SingleStepProtocolError(
                f"target {target_id!r} must contain one principal product"
            )
        product = _text(
            canonicalizer(product_raw), field=f"target {target_id!r} canonical product"
        )
        if product in seen_products:
            raise SingleStepProtocolError(
                f"duplicate canonical product for target {target_id!r}"
            )
        seen_ids.add(target_id)
        seen_products.add(product)
        targets.append(PublicTarget(target_id=target_id, product_smiles=product))
    if not targets:
        raise SingleStepProtocolError("public target set must not be empty")
    return tuple(targets)


@dataclass(frozen=True, slots=True)
class NormalizedPrediction:
    """One frozen beam with raw output and deterministic diagnostics."""

    rank: int
    raw_reactants_smiles: Any
    score: float | None
    status: str
    components: tuple[str, ...]
    signature: str | None
    duplicate_of_rank: int | None
    raw_prediction: Mapping[str, Any]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "raw_reactants_smiles": self.raw_reactants_smiles,
            "score": self.score,
            "status": self.status,
            "components": list(self.components),
            "signature": self.signature,
            "duplicate_of_rank": self.duplicate_of_rank,
            "raw_prediction": dict(self.raw_prediction),
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class TargetPredictions:
    """All submitted beams for one target, including backend failure state."""

    target_id: str
    product_smiles: str
    predictions: tuple[NormalizedPrediction, ...]
    backend_error: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "product_smiles": self.product_smiles,
            "predictions": [item.to_dict() for item in self.predictions],
            "backend_error": self.backend_error,
        }


def normalize_target_predictions(
    target: PublicTarget,
    predictions: Iterable[Mapping[str, Any]],
    *,
    top_k: int,
    canonicalizer: Canonicalizer = rdkit_canonicalize,
    backend_error: Any = None,
) -> TargetPredictions:
    """Normalize one target's full beam without dropping invalid candidates."""

    budget = _top_k(top_k)
    raw_predictions = list(predictions)
    if len(raw_predictions) > budget:
        raise SingleStepProtocolError(
            f"target {target.target_id!r} submitted {len(raw_predictions)} beams "
            f"for a Top-{budget} budget"
        )
    ranked: list[tuple[int, Mapping[str, Any]]] = []
    seen_ranks: set[int] = set()
    for index, raw in enumerate(raw_predictions, start=1):
        if not isinstance(raw, Mapping):
            raise SingleStepProtocolError(
                f"target {target.target_id!r} prediction {index} must be an object"
            )
        rank = raw.get("rank")
        if isinstance(rank, bool) or not isinstance(rank, int):
            raise SingleStepProtocolError("prediction rank must be an integer")
        if not 1 <= rank <= budget:
            raise SingleStepProtocolError(
                f"prediction rank {rank} falls outside the Top-{budget} budget"
            )
        if rank in seen_ranks:
            raise SingleStepProtocolError(f"duplicate prediction rank {rank}")
        seen_ranks.add(rank)
        ranked.append((rank, raw))
    ranked.sort(key=lambda item: item[0])

    first_rank_by_signature: dict[str, int] = {}
    normalized: list[NormalizedPrediction] = []
    for rank, raw in ranked:
        raw_copy = _json_copy(dict(raw), field="raw prediction")
        reactants = raw.get("reactants_smiles")
        score: float | None = None
        score_error: str | None = None
        try:
            score = _score(raw.get("score"))
        except SingleStepProtocolError as exc:
            score_error = str(exc)

        status = "valid"
        components: tuple[str, ...] = ()
        signature: str | None = None
        error = score_error
        if not isinstance(reactants, str) or not reactants.strip():
            status = "empty" if isinstance(reactants, str) else "invalid"
            error = error or "reactants_smiles must be a non-empty string"
        else:
            try:
                components = normalize_precursor_set(
                    reactants, canonicalizer=canonicalizer
                )
                signature = _signature(components)
            except Exception as exc:
                # A model beam is untrusted data.  Third-party canonicalizers
                # do not share one exception hierarchy, so isolate any ordinary
                # candidate-level failure while still allowing process-control
                # exceptions such as KeyboardInterrupt to propagate.
                status = "invalid"
                error = error or f"{type(exc).__name__}: {exc}"
        if score_error is not None:
            status = "invalid"
        duplicate_of = None
        if signature is not None:
            duplicate_of = first_rank_by_signature.get(signature)
            first_rank_by_signature.setdefault(signature, rank)
        normalized.append(
            NormalizedPrediction(
                rank=rank,
                raw_reactants_smiles=reactants,
                score=score,
                status=status,
                components=components,
                signature=signature,
                duplicate_of_rank=duplicate_of,
                raw_prediction=raw_copy,
                error=error,
            )
        )
    return TargetPredictions(
        target_id=target.target_id,
        product_smiles=target.product_smiles,
        predictions=tuple(normalized),
        backend_error=_json_copy(backend_error, field="backend_error"),
    )


def normalize_prediction_payloads(
    targets: Sequence[PublicTarget],
    payloads: Iterable[Mapping[str, Any]],
    *,
    top_k: int,
    canonicalizer: Canonicalizer = rdkit_canonicalize,
) -> tuple[TargetPredictions, ...]:
    """Normalize exactly one frozen prediction payload per public target."""

    by_target = {target.target_id: target for target in targets}
    payload_by_target: dict[str, Mapping[str, Any]] = {}
    for index, payload in enumerate(payloads, start=1):
        if not isinstance(payload, Mapping):
            raise SingleStepProtocolError(
                f"prediction payload {index} must be an object"
            )
        fields = set(payload)
        unsupported = fields - PREDICTION_PAYLOAD_FIELDS
        missing = {"target_id", "predictions"} - fields
        if unsupported or missing:
            raise SingleStepProtocolError(
                f"prediction payload {index} has unsupported fields "
                f"{sorted(unsupported)} or missing fields {sorted(missing)}"
            )
        target_id = _text(payload["target_id"], field="prediction target_id")
        if target_id not in by_target:
            raise SingleStepProtocolError(
                f"prediction for unknown target {target_id!r}"
            )
        if target_id in payload_by_target:
            raise SingleStepProtocolError(
                f"duplicate prediction payload for target {target_id!r}"
            )
        if not isinstance(payload["predictions"], list):
            raise SingleStepProtocolError("predictions must be an array")
        payload_by_target[target_id] = payload
    missing_targets = sorted(set(by_target) - set(payload_by_target))
    if missing_targets:
        raise SingleStepProtocolError(
            "missing prediction payload(s): " + ", ".join(missing_targets)
        )
    return tuple(
        normalize_target_predictions(
            target,
            payload_by_target[target.target_id]["predictions"],
            top_k=top_k,
            canonicalizer=canonicalizer,
            backend_error=payload_by_target[target.target_id].get("error"),
        )
        for target in targets
    )


def normalize_references(
    targets: Sequence[PublicTarget],
    rows: Iterable[Mapping[str, Any]],
    *,
    canonicalizer: Canonicalizer = rdkit_canonicalize,
) -> dict[str, frozenset[str]]:
    """Normalize private multi-reference precursor sets after output freeze."""

    target_ids = {target.target_id for target in targets}
    result: dict[str, frozenset[str]] = {}
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping) or set(row) != REFERENCE_FIELDS:
            raise SingleStepProtocolError(
                f"reference row {index} must contain only target_id and precursor_sets"
            )
        target_id = _text(row["target_id"], field="reference target_id")
        if target_id not in target_ids:
            raise SingleStepProtocolError(f"reference for unknown target {target_id!r}")
        if target_id in result:
            raise SingleStepProtocolError(f"duplicate reference target {target_id!r}")
        values = row["precursor_sets"]
        if not isinstance(values, list) or not values:
            raise SingleStepProtocolError("precursor_sets must be a non-empty array")
        signatures = {
            _signature(normalize_precursor_set(value, canonicalizer=canonicalizer))
            for value in values
        }
        result[target_id] = frozenset(signatures)
    missing = sorted(target_ids - set(result))
    if missing:
        raise SingleStepProtocolError("missing reference(s): " + ", ".join(missing))
    return result


def _component_multiset_distance(left: str, right: str) -> float:
    left_counts = Counter(left.split("."))
    right_counts = Counter(right.split("."))
    keys = set(left_counts) | set(right_counts)
    intersection = sum(min(left_counts[key], right_counts[key]) for key in keys)
    union = sum(max(left_counts[key], right_counts[key]) for key in keys)
    return 1.0 - (intersection / union if union else 1.0)


def _mean_pairwise_component_distance(signatures: Sequence[str]) -> float | None:
    if len(signatures) < 2:
        return None
    distances = [
        _component_multiset_distance(signatures[left], signatures[right])
        for left in range(len(signatures))
        for right in range(left + 1, len(signatures))
    ]
    return sum(distances) / len(distances)


def evaluate_predictions(
    predictions: Sequence[TargetPredictions],
    references: Mapping[str, frozenset[str]],
    *,
    top_k: int,
) -> dict[str, Any]:
    """Evaluate frozen beams against private references, target by target."""

    budget = _top_k(top_k)
    prediction_ids = [item.target_id for item in predictions]
    if len(prediction_ids) != len(set(prediction_ids)):
        raise SingleStepProtocolError("prediction targets must be unique")
    if set(prediction_ids) != set(references):
        raise SingleStepProtocolError(
            "prediction and reference target sets must match exactly"
        )
    cutoffs = sorted({cutoff for cutoff in (1, 3, 5, budget) if cutoff <= budget})
    target_results: list[dict[str, Any]] = []
    submitted = invalid = empty = duplicates = valid_unique = 0

    for target in predictions:
        target_refs = references[target.target_id]
        signatures_by_rank: list[tuple[int, str]] = []
        unique_signatures: list[str] = []
        seen_signatures: set[str] = set()
        for item in target.predictions:
            submitted += 1
            if item.status == "invalid":
                invalid += 1
            elif item.status == "empty":
                empty += 1
            if item.duplicate_of_rank is not None:
                duplicates += 1
            if item.status == "valid" and item.signature is not None:
                signatures_by_rank.append((item.rank, item.signature))
                if item.signature not in seen_signatures:
                    seen_signatures.add(item.signature)
                    unique_signatures.append(item.signature)
                    valid_unique += 1
        matching_ranks = [
            rank for rank, signature in signatures_by_rank if signature in target_refs
        ]
        first_hit_rank = min(matching_ranks) if matching_ranks else None
        hits_at_k = {
            str(cutoff): first_hit_rank is not None and first_hit_rank <= cutoff
            for cutoff in cutoffs
        }
        recall_at_k: dict[str, float] = {}
        for cutoff in cutoffs:
            recovered = {
                signature
                for rank, signature in signatures_by_rank
                if rank <= cutoff and signature in target_refs
            }
            recall_at_k[str(cutoff)] = len(recovered) / len(target_refs)
        target_results.append(
            {
                "target_id": target.target_id,
                "first_hit_rank": first_hit_rank,
                "reciprocal_rank": 1.0 / first_hit_rank if first_hit_rank else 0.0,
                "hits_at_k": hits_at_k,
                "multi_reference_recall_at_k": recall_at_k,
                "submitted": len(target.predictions),
                "valid_unique": len(unique_signatures),
                "component_set_diversity": _mean_pairwise_component_distance(
                    unique_signatures
                ),
                "backend_failed": target.backend_error is not None,
            }
        )

    target_count = len(target_results)
    budget_slots = target_count * budget
    aggregate_hits = {
        cutoff: sum(result["hits_at_k"][cutoff] for result in target_results)
        / target_count
        for cutoff in map(str, cutoffs)
    }
    aggregate_recall = {
        cutoff: sum(
            result["multi_reference_recall_at_k"][cutoff] for result in target_results
        )
        / target_count
        for cutoff in map(str, cutoffs)
    }
    diversity_values = [
        result["component_set_diversity"]
        for result in target_results
        if result["component_set_diversity"] is not None
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "scenario_id": "single_step_retrosynthesis_class_unknown_v1",
        "evaluation_unit": "target",
        "target_count": target_count,
        "top_k_budget": budget,
        "top_k_exact_match_accuracy": aggregate_hits,
        "multi_reference_recall_at_k": aggregate_recall,
        "mean_reciprocal_rank": sum(
            result["reciprocal_rank"] for result in target_results
        )
        / target_count,
        "invalid_prediction_rate": invalid / submitted if submitted else 0.0,
        "empty_prediction_rate": empty / submitted if submitted else 0.0,
        "duplicate_prediction_rate": duplicates / submitted if submitted else 0.0,
        "unique_valid_candidate_rate": valid_unique / submitted if submitted else 0.0,
        "unused_budget_slot_rate": (budget_slots - submitted) / budget_slots,
        "backend_failure_rate": sum(
            result["backend_failed"] for result in target_results
        )
        / target_count,
        "mean_component_set_diversity": (
            sum(diversity_values) / len(diversity_values) if diversity_values else None
        ),
        "targets": target_results,
        "caveat": (
            "Exact match measures recovery of recorded precursor sets, not the "
            "experimental feasibility of unmatched alternatives. Component-set "
            "diversity is an identity diagnostic, not molecular-fingerprint diversity."
        ),
    }


def build_intermediate_results(
    targets: Sequence[PublicTarget],
    predictions: Sequence[TargetPredictions],
    *,
    top_k: int,
    input_hashes: Mapping[str, str] | None = None,
    model_manifest: Mapping[str, Any] | None = None,
    random_seed: int = 2026,
) -> dict[str, Any]:
    """Build the public, pre-evaluation trajectory artifact."""

    budget = _top_k(top_k)
    if [target.target_id for target in targets] != [
        item.target_id for item in predictions
    ]:
        raise SingleStepProtocolError(
            "targets and prediction payloads must have identical order"
        )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "scenario_id": "single_step_retrosynthesis_class_unknown_v1",
        "task_condition": "reaction_class_unknown",
        "random_seed": random_seed,
        "input_hashes": dict(input_hashes or {}),
        "model": dict(model_manifest or {}),
        "budget": {"top_k": budget},
        "target_count": len(targets),
        "targets": [item.to_dict() for item in predictions],
        "warnings": [],
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    payload["trajectory_sha256"] = hashlib.sha256(canonical).hexdigest()
    return payload


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_targets_csv(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise SingleStepProtocolError("targets CSV must contain a header")
        return [dict(row) for row in reader]


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SingleStepProtocolError(
                    f"invalid JSONL at line {line_number}: {exc}"
                ) from exc
            if not isinstance(value, dict):
                raise SingleStepProtocolError(
                    f"JSONL line {line_number} must contain an object"
                )
            rows.append(value)
    return rows


def _write_json_atomic(path: str | Path, payload: Mapping[str, Any]) -> None:
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


def _load_manifest(path: str | None) -> dict[str, Any]:
    if path is None:
        return {}
    with Path(path).open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise SingleStepProtocolError("model manifest must contain an object")
    return value


def main(
    argv: Sequence[str] | None = None,
    *,
    canonicalizer: Canonicalizer = rdkit_canonicalize,
) -> int:
    """Run public normalization or private evaluation from frozen files."""

    parser = argparse.ArgumentParser(
        description="Class-unknown single-step retrosynthesis benchmark protocol"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    normalize_parser = subparsers.add_parser("normalize")
    evaluate_parser = subparsers.add_parser("evaluate")
    for subparser in (normalize_parser, evaluate_parser):
        subparser.add_argument("--targets", required=True)
        subparser.add_argument("--predictions", required=True)
        subparser.add_argument("--output", required=True)
        subparser.add_argument("--top-k", required=True, type=int)
    normalize_parser.add_argument("--model-manifest")
    evaluate_parser.add_argument("--references", required=True)
    args = parser.parse_args(argv)

    targets_path = Path(args.targets)
    predictions_path = Path(args.predictions)
    targets = validate_public_targets(
        load_targets_csv(targets_path), canonicalizer=canonicalizer
    )
    predictions = normalize_prediction_payloads(
        targets,
        load_jsonl(predictions_path),
        top_k=args.top_k,
        canonicalizer=canonicalizer,
    )
    if args.command == "normalize":
        output = build_intermediate_results(
            targets,
            predictions,
            top_k=args.top_k,
            input_hashes={
                "targets.csv": sha256_file(targets_path),
                "predictions.jsonl": sha256_file(predictions_path),
            },
            model_manifest=_load_manifest(args.model_manifest),
        )
    else:
        reference_path = Path(args.references)
        references = normalize_references(
            targets, load_jsonl(reference_path), canonicalizer=canonicalizer
        )
        output = evaluate_predictions(predictions, references, top_k=args.top_k)
        output["input_hashes"] = {
            "targets.csv": sha256_file(targets_path),
            "predictions.jsonl": sha256_file(predictions_path),
            "references.jsonl": sha256_file(reference_path),
        }
    _write_json_atomic(args.output, output)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI
    raise SystemExit(main())


__all__ = [
    "ChemistryDependencyError",
    "MAX_TOP_K",
    "NormalizedPrediction",
    "PublicTarget",
    "SCHEMA_VERSION",
    "SingleStepProtocolError",
    "TargetPredictions",
    "build_intermediate_results",
    "evaluate_predictions",
    "load_jsonl",
    "load_targets_csv",
    "main",
    "normalize_prediction_payloads",
    "normalize_precursor_set",
    "normalize_references",
    "normalize_target_predictions",
    "rdkit_canonicalize",
    "sha256_file",
    "validate_public_targets",
]
