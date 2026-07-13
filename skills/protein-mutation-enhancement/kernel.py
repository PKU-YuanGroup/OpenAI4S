"""Deterministic helpers for the protein mutation enhancement workflow.

This sidecar intentionally stays stdlib-only. It builds mutation libraries,
joins model/assay score tables, computes lightweight property heuristics, ranks
candidates, and decides whether an iterative design loop should continue.
Heavy ESM and structure prediction jobs are orchestrated by separate skills.
"""
from __future__ import annotations

import csv
import itertools
import json
import math
import re
from pathlib import Path
from typing import Iterable, Mapping, Sequence

AA_ALPHABET = "ACDEFGHIKLMNPQRSTVWY"
_MUTATION_RE = re.compile(r"^([A-Z])([1-9][0-9]*)([A-Z])$")

_AA_CLASS = {
    "A": "small",
    "C": "polar",
    "D": "negative",
    "E": "negative",
    "F": "aromatic",
    "G": "small",
    "H": "positive",
    "I": "hydrophobic",
    "K": "positive",
    "L": "hydrophobic",
    "M": "hydrophobic",
    "N": "polar",
    "P": "special",
    "Q": "polar",
    "R": "positive",
    "S": "polar",
    "T": "polar",
    "V": "hydrophobic",
    "W": "aromatic",
    "Y": "aromatic",
}

_HYDROPATHY = {
    "A": 1.8,
    "C": 2.5,
    "D": -3.5,
    "E": -3.5,
    "F": 2.8,
    "G": -0.4,
    "H": -3.2,
    "I": 4.5,
    "K": -3.9,
    "L": 3.8,
    "M": 1.9,
    "N": -3.5,
    "P": -1.6,
    "Q": -3.5,
    "R": -4.5,
    "S": -0.8,
    "T": -0.7,
    "V": 4.2,
    "W": -0.9,
    "Y": -1.3,
}

_CHARGE = {
    "D": -1,
    "E": -1,
    "H": 0.5,
    "K": 1,
    "R": 1,
}

_VOLUME = {
    "A": 88.6,
    "C": 108.5,
    "D": 111.1,
    "E": 138.4,
    "F": 189.9,
    "G": 60.1,
    "H": 153.2,
    "I": 166.7,
    "K": 168.6,
    "L": 166.7,
    "M": 162.9,
    "N": 114.1,
    "P": 112.7,
    "Q": 143.8,
    "R": 173.4,
    "S": 89.0,
    "T": 116.1,
    "V": 140.0,
    "W": 227.8,
    "Y": 193.6,
}


def validate_sequence(sequence: str, alphabet: str = AA_ALPHABET) -> str:
    """Return an uppercase sequence, raising ValueError on invalid residues."""
    seq = "".join(str(sequence).split()).upper()
    if not seq:
        raise ValueError("sequence is empty")
    allowed = set(alphabet)
    bad = sorted(set(seq) - allowed)
    if bad:
        raise ValueError(f"sequence contains unsupported residue(s): {''.join(bad)}")
    return seq


def parse_mutation(mutation: str, sequence: str | None = None) -> dict:
    """Parse `A12V` into a JSON-friendly dict with 1-indexed position."""
    match = _MUTATION_RE.match(str(mutation).strip().upper())
    if not match:
        raise ValueError(f"invalid mutation: {mutation!r}")
    wt, pos_s, mutant = match.groups()
    pos = int(pos_s)
    bad = sorted({wt, mutant} - set(AA_ALPHABET))
    if bad:
        raise ValueError(f"mutation contains unsupported residue(s): {''.join(bad)}")
    if wt == mutant:
        raise ValueError(f"mutation does not change residue: {mutation!r}")
    if sequence is not None:
        seq = validate_sequence(sequence)
        if pos > len(seq):
            raise ValueError(f"mutation position {pos} exceeds sequence length")
        observed = seq[pos - 1]
        if observed != wt:
            raise ValueError(
                f"wild-type mismatch at {pos}: mutation says {wt}, "
                f"sequence has {observed}"
            )
    return {"from": wt, "position": pos, "to": mutant}


def normalize_mutations(
    mutations: str | Mapping | Sequence,
    sequence: str | None = None,
) -> list[dict]:
    """Normalize mutation strings, dicts, or tuples into sorted mutation dicts.

    Accepted forms: the string `"A12V+G47D"`; a single mapping
    `{"from":"A","position":12,"to":"V"}`; or a list whose items are each a
    string, mapping, or tuple such as `("A", 12, "V")` / `(12, "V")` (the
    2-tuple requires `sequence`). A bare top-level tuple is iterated as a
    sequence of items, so wrap a single tuple in a list, e.g. `[("A", 12, "V")]`.
    """
    if mutations is None:
        return []
    if isinstance(mutations, str):
        parts = [p for p in re.split(r"[+,;\s]+", mutations.strip()) if p]
        parsed = [parse_mutation(part, sequence) for part in parts]
        return _validate_mutation_set(parsed)
    if isinstance(mutations, Mapping):
        parsed = [_coerce_mutation(mutations, sequence)]
        return _validate_mutation_set(parsed)

    parsed = []
    for item in mutations:
        parsed.append(_coerce_mutation(item, sequence))
    return _validate_mutation_set(parsed)


def mutation_id(
    mutations: str | Mapping | Sequence, sequence: str | None = None
) -> str:
    """Return a stable variant ID such as `A12V+G47D` or `WT`."""
    muts = normalize_mutations(mutations, sequence)
    if not muts:
        return "WT"
    return "+".join(
        f"{m['from']}{m['position']}{m['to']}"
        for m in sorted(muts, key=lambda x: x["position"])
    )


def apply_mutations(sequence: str, mutations: str | Mapping | Sequence) -> str:
    """Apply validated mutations to a wild-type sequence."""
    seq = validate_sequence(sequence)
    chars = list(seq)
    for mut in normalize_mutations(mutations, seq):
        chars[mut["position"] - 1] = mut["to"]
    return "".join(chars)


def enumerate_mutants(
    sequence: str,
    positions: Sequence[int] | None = None,
    substitutions: Mapping[int | str, Iterable[str] | str] | None = None,
    max_order: int = 1,
    seeds: Sequence[str | Mapping | Sequence] | None = None,
    limit: int | None = None,
    include_wild_type: bool = False,
    alphabet: str = AA_ALPHABET,
) -> list[dict]:
    """Build a deterministic single/double/combination mutant library.

    `positions` are 1-indexed. If `seeds` are supplied, each seed is kept and
    expanded with additional mutations up to `max_order`.
    """
    seq = validate_sequence(sequence, alphabet)
    if max_order < 1:
        raise ValueError("max_order must be >= 1")
    if limit is not None and limit < 1:
        raise ValueError("limit must be >= 1 when provided")

    pos_list = _normalize_positions(positions, len(seq))
    sub_map = _normalize_substitutions(seq, pos_list, substitutions, alphabet)
    options = [
        {"from": seq[pos - 1], "position": pos, "to": aa}
        for pos in pos_list
        for aa in sub_map[pos]
    ]

    out: list[dict] = []
    seen: set[str] = set()

    def add_variant(muts: Sequence[Mapping]) -> bool:
        mid = mutation_id(muts)
        if mid in seen:
            return False
        mutant_seq = apply_mutations(seq, muts) if muts else seq
        seen.add(mid)
        out.append(
            {
                "id": mid,
                "sequence": mutant_seq,
                "mutations": [dict(m) for m in normalize_mutations(muts, seq)],
                "order": len(muts),
            }
        )
        return limit is not None and len(out) >= limit

    if include_wild_type and add_variant([]):
        return out

    seed_sets = [normalize_mutations(seed, seq) for seed in seeds or []]
    if not seed_sets:
        for order in range(1, max_order + 1):
            for combo in itertools.combinations(options, order):
                if len({m["position"] for m in combo}) != order:
                    continue
                if add_variant(combo):
                    return out
        return out

    for seed in seed_sets:
        if len(seed) > max_order:
            raise ValueError(f"seed {mutation_id(seed)} exceeds max_order")
        if add_variant(seed):
            return out
        occupied = {m["position"] for m in seed}
        remaining = [m for m in options if m["position"] not in occupied]
        max_extra = max_order - len(seed)
        for extra_order in range(1, max_extra + 1):
            for extra in itertools.combinations(remaining, extra_order):
                if len({m["position"] for m in extra}) != extra_order:
                    continue
                if add_variant([*seed, *extra]):
                    return out
    return out


def property_score(mutations: str | Mapping | Sequence) -> float:
    """Score conservative physicochemical change on a 0..1 scale."""
    muts = normalize_mutations(mutations)
    if not muts:
        return 1.0
    scores = []
    for mut in muts:
        wt = mut["from"]
        aa = mut["to"]
        class_bonus = 0.15 if _AA_CLASS.get(wt) == _AA_CLASS.get(aa) else 0.0
        hydropathy_delta = abs(_HYDROPATHY[aa] - _HYDROPATHY[wt]) / 9.0
        charge_delta = abs(_CHARGE.get(aa, 0.0) - _CHARGE.get(wt, 0.0)) / 2.0
        volume_delta = abs(_VOLUME[aa] - _VOLUME[wt]) / 170.0
        penalty = 0.45 * hydropathy_delta + 0.25 * charge_delta
        penalty += 0.15 * volume_delta
        scores.append(_clamp(0.85 + class_bonus - penalty))
    return sum(scores) / len(scores)


def read_score_table(path: str | Path, id_column: str = "id") -> dict[str, dict]:
    """Read a CSV or JSON score table keyed by variant ID."""
    p = Path(path)
    if p.suffix.lower() == ".json":
        payload = json.loads(p.read_text("utf-8"))
        if isinstance(payload, Mapping):
            return {str(k): _coerce_score_row(v) for k, v in payload.items()}
        if isinstance(payload, list):
            return _records_to_table(payload, id_column)
        raise ValueError("JSON score table must be an object or list of records")

    with p.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return _records_to_table(list(reader), id_column)


def write_fasta(candidates: Sequence[Mapping], path: str | Path) -> None:
    """Write candidate sequences to FASTA using candidate `id` as header."""
    with Path(path).open("w", encoding="utf-8") as handle:
        for cand in candidates:
            cid = str(cand["id"])
            seq = validate_sequence(str(cand["sequence"]))
            handle.write(f">{cid}\n")
            for i in range(0, len(seq), 80):
                handle.write(seq[i : i + 80] + "\n")


def rank_mutants(
    candidates: Sequence[Mapping],
    score_tables: Sequence[Mapping | Sequence[Mapping]] | None = None,
    weights: Mapping[str, float] | None = None,
    directions: Mapping[str, str] | None = None,
    acceptance_thresholds: Mapping[str, float] | None = None,
) -> list[dict]:
    """Merge score tables, normalize metrics, and rank candidates."""
    if weights is not None and not weights:
        raise ValueError("weights must not be empty")
    weights = dict(weights or {"esm_delta": 0.5, "plddt": 0.3, "property_score": 0.2})
    directions = {k: v.lower() for k, v in (directions or {}).items()}
    thresholds = dict(acceptance_thresholds or {})
    if any(v < 0 for v in weights.values()):
        raise ValueError("weights must be non-negative; use directions for low metrics")
    total_weight = sum(weights.values())
    if total_weight <= 0:
        raise ValueError("at least one metric weight must be positive")

    rows = _merge_candidates(candidates, score_tables or [])
    for row in rows:
        if "property_score" in weights and row.get("property_score") is None:
            row["property_score"] = property_score(row.get("mutations", []))

    normalized_by_metric = {
        metric: _normalize_metric(rows, metric, directions.get(metric, "high"))
        for metric in weights
    }

    ranked = []
    for row in rows:
        norm = {
            metric: normalized_by_metric[metric].get(row["id"], 0.0)
            for metric in weights
        }
        missing = [
            metric
            for metric in weights
            if row.get(metric) is None or not _is_number(row.get(metric))
        ]
        composite = sum(norm[m] * weights[m] for m in weights) / total_weight
        enriched = dict(row)
        enriched["normalized_scores"] = norm
        enriched["missing_metrics"] = missing
        enriched["composite_score"] = round(composite, 6)
        enriched["passes_thresholds"] = _passes_thresholds(
            enriched, thresholds, directions
        )
        ranked.append(enriched)

    ranked.sort(key=lambda r: (-r["composite_score"], r["id"]))
    return ranked


def run_selection_round(
    candidates: Sequence[Mapping],
    score_tables: Sequence[Mapping | Sequence[Mapping]] | None = None,
    weights: Mapping[str, float] | None = None,
    directions: Mapping[str, str] | None = None,
    acceptance_thresholds: Mapping[str, float] | None = None,
    top_k: int = 20,
) -> dict:
    """Rank one design round and return loop-control fields."""
    if top_k < 1:
        raise ValueError("top_k must be >= 1")
    ranked = rank_mutants(
        candidates,
        score_tables=score_tables,
        weights=weights,
        directions=directions,
        acceptance_thresholds=acceptance_thresholds,
    )
    accepted = [row for row in ranked if row["passes_thresholds"]]
    return {
        "ranked": ranked,
        "accepted": accepted,
        "should_continue": not bool(accepted),
        "next_round_seeds": (
            [row["id"] for row in ranked[:top_k]] if not accepted else []
        ),
    }


def suggest_next_positions(
    ranked_candidates: Sequence[Mapping],
    max_positions: int = 10,
) -> list[int]:
    """Pick positions enriched among top-ranked candidates for the next round."""
    if max_positions < 1:
        raise ValueError("max_positions must be >= 1")
    weights: dict[int, float] = {}
    for rank, cand in enumerate(ranked_candidates):
        rank_weight = 1.0 / (rank + 1)
        for mut in normalize_mutations(cand.get("mutations", [])):
            pos = int(mut["position"])
            weights[pos] = weights.get(pos, 0.0) + rank_weight
    return [
        pos
        for pos, _ in sorted(weights.items(), key=lambda item: (-item[1], item[0]))[
            :max_positions
        ]
    ]


def write_ranked_json(result: Mapping, path: str | Path) -> None:
    """Persist a selection-round result for review or downstream loops."""
    Path(path).write_text(json.dumps(result, indent=2, sort_keys=True), "utf-8")


def _coerce_mutation(item, sequence: str | None) -> dict:
    if isinstance(item, str):
        return parse_mutation(item, sequence)
    if isinstance(item, Mapping):
        pos_raw = item.get("position", item.get("pos"))
        if pos_raw is None:
            raise ValueError(f"mutation mapping is missing a position: {item!r}")
        pos = int(pos_raw)
        wt = str(item.get("from", item.get("wt", ""))).upper()
        aa = str(item.get("to", item.get("mutant", item.get("aa", "")))).upper()
        if not wt and sequence is not None:
            wt = _residue_at(sequence, pos)
        if not wt or not aa:
            raise ValueError(f"mutation mapping is missing from/to: {item!r}")
        return parse_mutation(f"{wt}{pos}{aa}", sequence)
    if isinstance(item, Sequence):
        if len(item) == 2:
            if sequence is None:
                raise ValueError("(position, to) mutations require sequence")
            pos = int(item[0])
            wt = _residue_at(sequence, pos)
            return parse_mutation(f"{wt}{pos}{str(item[1]).upper()}", sequence)
        if len(item) == 3:
            wt, pos, aa = item
            return parse_mutation(
                f"{str(wt).upper()}{int(pos)}{str(aa).upper()}", sequence
            )
    raise ValueError(f"cannot parse mutation item: {item!r}")


def _validate_mutation_set(mutations: Sequence[Mapping]) -> list[dict]:
    seen: set[int] = set()
    out = []
    for mut in sorted((dict(m) for m in mutations), key=lambda x: x["position"]):
        pos = int(mut["position"])
        if pos in seen:
            raise ValueError(f"multiple mutations target position {pos}")
        seen.add(pos)
        out.append(
            {
                "from": str(mut["from"]).upper(),
                "position": pos,
                "to": str(mut["to"]).upper(),
            }
        )
    return out


def _normalize_positions(positions: Sequence[int] | None, length: int) -> list[int]:
    raw = range(1, length + 1) if positions is None else positions
    out = sorted({int(pos) for pos in raw})
    if not out:
        raise ValueError("positions must not be empty")
    bad = [pos for pos in out if pos < 1 or pos > length]
    if bad:
        raise ValueError(f"positions out of range: {bad}")
    return out


def _normalize_substitutions(
    sequence: str,
    positions: Sequence[int],
    substitutions: Mapping[int | str, Iterable[str] | str] | None,
    alphabet: str,
) -> dict[int, list[str]]:
    allowed = set(alphabet)
    order = {aa: i for i, aa in enumerate(alphabet)}
    sub_map = {}
    substitutions = substitutions or {}
    for pos in positions:
        raw = substitutions.get(pos, substitutions.get(str(pos), alphabet))
        residues = [aa.upper() for aa in (raw if not isinstance(raw, str) else raw)]
        bad = sorted(set(residues) - allowed)
        if bad:
            raise ValueError(f"invalid substitutions at {pos}: {''.join(bad)}")
        wt = sequence[pos - 1]
        uniq = sorted({aa for aa in residues if aa != wt}, key=lambda aa: order[aa])
        sub_map[pos] = uniq
    return sub_map


def _merge_candidates(
    candidates: Sequence[Mapping],
    score_tables: Sequence[Mapping | Sequence[Mapping]],
) -> list[dict]:
    tables = [_as_score_table(table) for table in score_tables]
    rows = []
    for cand in candidates:
        row = dict(cand)
        row["id"] = str(row["id"])
        for table in tables:
            if row["id"] in table:
                row.update(table[row["id"]])
        rows.append(row)
    return rows


def _as_score_table(table: Mapping | Sequence[Mapping]) -> dict[str, dict]:
    if isinstance(table, Mapping):
        return {str(k): _coerce_score_row(v) for k, v in table.items()}
    return _records_to_table(table)


def _records_to_table(
    records: Sequence[Mapping],
    id_column: str = "id",
) -> dict[str, dict]:
    out = {}
    for record in records:
        if id_column not in record:
            raise ValueError(f"score record missing {id_column!r}: {record!r}")
        cid = str(record[id_column])
        row = {str(k): _coerce_number(v) for k, v in dict(record).items()}
        row.pop(id_column, None)
        out[cid] = row
    return out


def _coerce_score_row(value) -> dict:
    if isinstance(value, Mapping):
        return {str(k): _coerce_number(v) for k, v in value.items()}
    raise ValueError(f"score table value must be a mapping, got {value!r}")


def _coerce_number(value):
    if value in ("", None):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _normalize_metric(
    rows: Sequence[Mapping], metric: str, direction: str
) -> dict[str, float]:
    values = [
        (row["id"], float(row[metric])) for row in rows if _is_number(row.get(metric))
    ]
    if not values:
        return {}
    lo = min(v for _, v in values)
    hi = max(v for _, v in values)
    if math.isclose(lo, hi):
        return {cid: 1.0 for cid, _ in values}
    if direction not in ("high", "low"):
        raise ValueError(f"direction for {metric!r} must be 'high' or 'low'")
    if direction == "high":
        return {cid: (v - lo) / (hi - lo) for cid, v in values}
    return {cid: (hi - v) / (hi - lo) for cid, v in values}


def _passes_thresholds(
    row: Mapping,
    thresholds: Mapping[str, float],
    directions: Mapping[str, str],
) -> bool:
    for metric, threshold in thresholds.items():
        value = row.get(metric)
        if not _is_number(value):
            return False
        if directions.get(metric, "high") == "low":
            if float(value) > float(threshold):
                return False
        elif float(value) < float(threshold):
            return False
    return True


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def _residue_at(sequence: str, pos: int) -> str:
    """Return the 1-indexed wild-type residue, raising on out-of-range positions."""
    seq = validate_sequence(sequence)
    if pos < 1 or pos > len(seq):
        raise ValueError(f"mutation position {pos} exceeds sequence length")
    return seq[pos - 1]
