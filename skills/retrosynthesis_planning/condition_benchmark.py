"""Closed-vocabulary condition-tuple recommendation protocol for Scenario 5."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from .benchmark_common import (
    BenchmarkProtocolError,
    finite_number,
    json_copy,
    positive_int,
    require_exact_fields,
    require_text,
    sha256_json,
)
from .single_step_benchmark import normalize_precursor_set, rdkit_canonicalize

Canonicalizer = Callable[[str], str]
SLOTS = ("catalyst1", "solvent1", "solvent2", "reagent1", "reagent2")
INPUT_FIELDS = frozenset({"reaction_id", "reactants", "product"})
PAYLOAD_FIELDS = frozenset({"reaction_id", "predictions", "raw_output", "error"})
REFERENCE_FIELDS = frozenset({"reaction_id", "condition_sets"})


def _reaction_signature(
    reactants: Any, product: Any, canonicalizer: Canonicalizer
) -> tuple[str, str]:
    left = ".".join(
        normalize_precursor_set(
            require_text(reactants, field="reactants"), canonicalizer=canonicalizer
        )
    )
    right = require_text(
        canonicalizer(require_text(product, field="product")), field="canonical product"
    )
    return left, right


def validate_condition_inputs(
    rows: Iterable[Mapping[str, Any]],
    *,
    canonicalizer: Canonicalizer = rdkit_canonicalize,
) -> tuple[dict[str, str], ...]:
    result: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    seen_reactions: set[tuple[str, str]] = set()
    for index, row in enumerate(rows, start=1):
        require_exact_fields(row, INPUT_FIELDS, field=f"condition input {index}")
        reaction_id = require_text(row["reaction_id"], field="reaction_id")
        signature = _reaction_signature(row["reactants"], row["product"], canonicalizer)
        if reaction_id in seen_ids or signature in seen_reactions:
            raise BenchmarkProtocolError(
                "reaction IDs and reaction structures must be unique"
            )
        seen_ids.add(reaction_id)
        seen_reactions.add(signature)
        result.append(
            {
                "reaction_id": reaction_id,
                "reactants": signature[0],
                "product": signature[1],
            }
        )
    if not result:
        raise BenchmarkProtocolError("condition inputs must not be empty")
    return tuple(result)


def normalize_vocabulary(
    vocabulary: Mapping[str, Iterable[str]],
) -> dict[str, frozenset[str]]:
    require_exact_fields(vocabulary, set(SLOTS), field="condition vocabulary")
    result: dict[str, frozenset[str]] = {}
    for slot in SLOTS:
        labels = frozenset(
            require_text(value, field=f"vocabulary.{slot}")
            for value in vocabulary[slot]
        )
        if not labels:
            raise BenchmarkProtocolError(f"vocabulary.{slot} must not be empty")
        result[slot] = labels
    return result


def _condition_tuple(value: Any, *, field: str) -> tuple[str | None, ...]:
    if not isinstance(value, Mapping):
        raise BenchmarkProtocolError(f"{field} must be an object")
    require_exact_fields(value, set(SLOTS), field=field)
    normalized: list[str | None] = []
    for slot in SLOTS:
        raw = value[slot]
        normalized.append(
            None if raw in (None, "") else require_text(raw, field=f"{field}.{slot}")
        )
    return tuple(normalized)


def normalize_condition_outputs(
    inputs: Sequence[Mapping[str, str]],
    payloads: Iterable[Mapping[str, Any]],
    *,
    vocabulary: Mapping[str, Iterable[str]],
    top_k: int,
) -> tuple[dict[str, Any], ...]:
    budget = positive_int(top_k, field="top_k", maximum=10)
    vocab = normalize_vocabulary(vocabulary)
    input_by_id = {row["reaction_id"]: row for row in inputs}
    payload_by_id: dict[str, Mapping[str, Any]] = {}
    for index, payload in enumerate(payloads, start=1):
        fields = set(payload)
        if (
            not {"reaction_id", "predictions"}.issubset(fields)
            or fields - PAYLOAD_FIELDS
        ):
            raise BenchmarkProtocolError(f"condition payload {index} violates schema")
        reaction_id = require_text(payload["reaction_id"], field="reaction_id")
        if reaction_id not in input_by_id or reaction_id in payload_by_id:
            raise BenchmarkProtocolError(
                f"unknown or duplicate reaction {reaction_id!r}"
            )
        if (
            not isinstance(payload["predictions"], list)
            or len(payload["predictions"]) > budget
        ):
            raise BenchmarkProtocolError(
                "predictions must be an array within Top-K budget"
            )
        payload_by_id[reaction_id] = payload
    if set(payload_by_id) != set(input_by_id):
        raise BenchmarkProtocolError("condition output must cover every input")

    result: list[dict[str, Any]] = []
    for reaction_id, input_row in input_by_id.items():
        payload = payload_by_id[reaction_id]
        seen_ranks: set[int] = set()
        seen_tuples: dict[tuple[str | None, ...], int] = {}
        predictions: list[dict[str, Any]] = []
        for ordinal, raw in enumerate(payload["predictions"], start=1):
            if not isinstance(raw, Mapping):
                raise BenchmarkProtocolError("condition prediction must be an object")
            require_exact_fields(
                raw,
                {"rank", "score", "conditions"},
                field=f"condition prediction {ordinal}",
            )
            rank = raw["rank"]
            if (
                isinstance(rank, bool)
                or not isinstance(rank, int)
                or not 1 <= rank <= budget
                or rank in seen_ranks
            ):
                raise BenchmarkProtocolError(
                    "condition ranks must be unique and within budget"
                )
            seen_ranks.add(rank)
            values = _condition_tuple(raw["conditions"], field="conditions")
            oov = [
                slot
                for slot, value in zip(SLOTS, values)
                if value is not None and value not in vocab[slot]
            ]
            duplicate_of = seen_tuples.get(values)
            seen_tuples.setdefault(values, rank)
            predictions.append(
                {
                    "rank": rank,
                    "score": finite_number(raw["score"], field="score"),
                    "conditions": dict(zip(SLOTS, values)),
                    "condition_signature": list(values),
                    "oov_slots": oov,
                    "duplicate_of_rank": duplicate_of,
                    "valid": not oov,
                    "raw_prediction": json_copy(
                        dict(raw), field="condition prediction"
                    ),
                }
            )
        predictions.sort(key=lambda row: row["rank"])
        result.append(
            {
                **input_row,
                "predictions": predictions,
                "backend_error": json_copy(payload.get("error"), field="backend error"),
            }
        )
    return tuple(result)


def evaluate_condition_predictions(
    predictions: Sequence[Mapping[str, Any]],
    reference_rows: Iterable[Mapping[str, Any]],
    *,
    top_k: int,
) -> dict[str, Any]:
    # The non-empty invariant is enforced on the normalize side by every
    # validate_* helper; without it here the metric divisions below reduce
    # to a bare ZeroDivisionError instead of a protocol refusal.
    if not predictions:
        raise BenchmarkProtocolError("condition predictions must not be empty")
    budget = positive_int(top_k, field="top_k", maximum=10)
    references: dict[str, set[tuple[str | None, ...]]] = {}
    for index, row in enumerate(reference_rows, start=1):
        require_exact_fields(row, REFERENCE_FIELDS, field=f"reference row {index}")
        reaction_id = require_text(row["reaction_id"], field="reaction_id")
        if (
            reaction_id in references
            or not isinstance(row["condition_sets"], list)
            or not row["condition_sets"]
        ):
            raise BenchmarkProtocolError(
                "references must have unique IDs and non-empty condition_sets"
            )
        references[reaction_id] = {
            _condition_tuple(value, field="reference condition")
            for value in row["condition_sets"]
        }
    if {row["reaction_id"] for row in predictions} != set(references):
        raise BenchmarkProtocolError("prediction and reference reactions must match")

    cutoffs = sorted({value for value in (1, 3, 5, budget) if value <= budget})
    rows: list[dict[str, Any]] = []
    submitted = oov = duplicates = 0
    slot_hits = {slot: 0 for slot in SLOTS}
    for prediction in predictions:
        refs = references[prediction["reaction_id"]]
        ranks: list[int] = []
        for candidate in prediction["predictions"]:
            submitted += 1
            oov += bool(candidate["oov_slots"])
            duplicates += candidate["duplicate_of_rank"] is not None
            values = tuple(candidate["condition_signature"])
            if candidate["valid"] and values in refs:
                ranks.append(candidate["rank"])
        first = min(ranks) if ranks else None
        # Ranks are validated as unique and within budget, never as 1-based or
        # contiguous, so ``predictions[0]`` is whatever survived - not the
        # rank-1 beam this metric is named for.
        top_one = next(
            (item for item in prediction["predictions"] if item["rank"] == 1), None
        )
        for position, slot in enumerate(SLOTS):
            if top_one and any(
                # A reference slot left empty is absence of evidence: scoring
                # None == None would credit a model that predicts nothing.
                ref[position] is not None
                and top_one["condition_signature"][position] == ref[position]
                for ref in refs
            ):
                slot_hits[slot] += 1
        rows.append(
            {"reaction_id": prediction["reaction_id"], "first_exact_tuple_rank": first}
        )
    count = len(rows)
    result = {
        "schema_version": 1,
        "scenario_id": "reaction_condition_tuple_closed_vocab_v1",
        "reaction_count": count,
        "exact_tuple_top_k_accuracy": {
            str(cutoff): sum(
                row["first_exact_tuple_rank"] is not None
                and row["first_exact_tuple_rank"] <= cutoff
                for row in rows
            )
            / count
            for cutoff in cutoffs
        },
        "mean_reciprocal_rank": sum(
            (
                1.0 / row["first_exact_tuple_rank"]
                if row["first_exact_tuple_rank"]
                else 0.0
            )
            for row in rows
        )
        / count,
        "top1_slot_recall": {slot: slot_hits[slot] / count for slot in SLOTS},
        "oov_tuple_rate": oov / submitted if submitted else 0.0,
        "duplicate_tuple_rate": duplicates / submitted if submitted else 0.0,
        "unused_budget_slot_rate": (count * budget - submitted) / (count * budget),
        "reactions": rows,
        "caveat": "A recorded condition tuple is one literature choice, not the unique feasible optimum.",
    }
    result["result_sha256"] = sha256_json(result)
    return result


__all__ = [
    "BenchmarkProtocolError",
    "SLOTS",
    "evaluate_condition_predictions",
    "normalize_condition_outputs",
    "normalize_vocabulary",
    "validate_condition_inputs",
]
