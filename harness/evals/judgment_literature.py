"""Offline-first evaluation of literature claim checking (plan §7 / W3-B).

The development split can be scored without a network or key: dataset
statistics plus the code-path locator/numeric detector (``CODE``). Optional
live systems ``LLM`` (configured main model, three-way relation) and ``J``
(``check_claims``) require ``--live``. The test split is frozen by a SHA-256
lock and may only be scored when ``--frozen-thresholds`` matches
``openai4s.judgment.templates.literature.TEMPLATE_VERSION``. This module never
tunes anything on that split.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib.util
import json
import math
import os
import random
import re
import subprocess
import sys
import time
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

CASES_PATH = Path(__file__).with_name("judgment_literature_cases.json")
LOCK_PATH = Path(__file__).with_name("judgment_literature_cases.lock")
KERNEL_PATH = (
    Path(__file__).resolve().parents[2] / "skills" / "literature-review" / "kernel.py"
)

SCHEMA_VERSION = 1
SPLIT_SEED = 20260920
BOOTSTRAP_SEED = 20260920
BOOTSTRAP_RESAMPLES = 1000
INPUT_TOKEN_USD_PER_MILLION = 0.042
DEFAULT_CLAIM_CONF = 0.8
FUZZY_MIN_RATIO = 0.9
MAX_SOURCE_CHARS = 1500

LANGS = ("zh", "en")
PERTURBATIONS = (
    "faithful",
    "negation",
    "numeric_change",
    "unit_change",
    "scope_widen",
    "scope_narrow",
    "fabricated_quote",
    "unrelated",
)
SPLITS = ("dev", "test")
SYSTEMS = ("CODE", "LLM", "J")
LIVE_SYSTEMS = frozenset({"LLM", "J"})
CASE_FIELDS = (
    "id",
    "lang",
    "perturbation",
    "claim",
    "quote",
    "source",
    "gold_status",
    "gold_relation",
    "split",
)
SOURCE_FIELDS = ("doi", "license", "text")
GOLD_RELATIONS = ("supports", "contradicts", "insufficient")
GOLD_STATUSES = (
    "verified",
    "contradicted",
    "unsupported",
    "not_found_needs_review",
    "numeric_mismatch",
)
REVIEW_STATUSES = frozenset({"uncertain", "not_found", "not_found_needs_review"})
NUMERIC_PERTURBATIONS = frozenset({"numeric_change", "unit_change"})

STATUS_TO_RELATION = {
    "verified": "supports",
    "contradicted": "contradicts",
    "unsupported": "insufficient",
    "numeric_mismatch": "contradicts",
}

LLM_PROMPT_VERSION = "2026-09-20.1"
LLM_PROMPT = """You classify how a SOURCE SECTION relates to a CLAIM.

Choose exactly one of:
- supports: the section states the claim, or directly entails that the claim holds
- contradicts: the section states the opposite, or entails that the claim does not hold
- insufficient: the section does not address what the claim asserts

If a number or unit in the claim disagrees with the section, choose contradicts.
Do not invent evidence. Do not treat "the paper exists" as support.

CLAIM:
{claim}

SOURCE SECTION:
{section}

Reply with one JSON object and nothing else:
{{"relation":"supports|contradicts|insufficient","confidence":0.0}}
confidence is your self-reported certainty between 0 and 1, not a calibrated probability.
"""

# Quantity detector used by CODE and by the dataset builder. Intentionally
# conservative: a leading number plus an optional unit. Bare 4-digit years are
# ignored when no unit is present so publication years do not fire.
_UNIT_TOKEN = (
    r"ng/mL|mg/mL|μg/L|µg/L|ug/L|g/L|mg/L|mL/min|μg/g|µg/g|ng/g|mg/kg|"
    r"°C|℃|mg|μg|µg|ug|kg|mL|ml|μL|µL|uL|"
    r"nM|μM|µM|uM|mM|mm|cm|nm|μm|µm|"
    r"kcal|mmHg|fold|min|bp|kb|IU|"
    r"%|％|倍|g|L"
)
_QUANTITY_RE = re.compile(
    r"(?P<num>[-+]?(?:\d{1,3}(?:[ ,]\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?)"
    r"\s*"
    r"(?P<unit>" + _UNIT_TOKEN + r")?"
    r"(?![A-Za-z/])",
    re.IGNORECASE,
)

_HYPHENS = "\u2010\u2011\u2012\u2013\u2014\u2212"
_QUOTE_MAP = {
    "\u201c": '"',
    "\u201d": '"',
    "\u2018": "'",
    "\u2019": "'",
    "\u300c": '"',
    "\u300d": '"',
}

_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
_LICENSE_RE = re.compile(r"^(cc0|cc\b)", re.IGNORECASE)

_KERNEL_MOD: Any = None


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


def canonical_test_payload(cases: Sequence[Mapping[str, Any]]) -> str:
    """Serialize the test split the way the lock file hashes it."""

    test = [dict(case) for case in cases if case.get("split") == "test"]
    test.sort(key=lambda row: str(row.get("id", "")))
    ordered = []
    for case in test:
        source = case.get("source") or {}
        ordered.append(
            {
                "id": case["id"],
                "lang": case["lang"],
                "perturbation": case["perturbation"],
                "claim": case["claim"],
                "quote": case.get("quote") or "",
                "source": {
                    "doi": source.get("doi", ""),
                    "license": source.get("license", ""),
                    "text": source.get("text", ""),
                },
                "gold_status": case["gold_status"],
                "gold_relation": case["gold_relation"],
                "split": case["split"],
            }
        )
    return json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))


def test_set_sha256(cases: Sequence[Mapping[str, Any]]) -> str:
    return hashlib.sha256(canonical_test_payload(cases).encode("utf-8")).hexdigest()


def lock_document(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "algorithm": "sha256",
        "encoding": "utf-8",
        "n_test": sum(1 for case in cases if case.get("split") == "test"),
        "serialization": (
            "json.dumps(test_cases_sorted_by_id, ensure_ascii=False, "
            "separators=(',', ':')) with keys " + ",".join(CASE_FIELDS)
        ),
        "sha256": test_set_sha256(cases),
        "split_seed": SPLIT_SEED,
    }


def load_lock(path: str | Path = LOCK_PATH) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, Mapping):
        raise ValueError("lock file must contain an object")
    digest = payload.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("lock file sha256 must be a 64-char hex digest")
    return dict(payload)


def load_cases(path: str | Path = CASES_PATH) -> list[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        raw_cases = payload
        schema_version = SCHEMA_VERSION
    elif isinstance(payload, Mapping):
        schema_version = payload.get("schema_version", SCHEMA_VERSION)
        raw_cases = payload.get("cases")
    else:
        raise ValueError("case file must be a list or an object with cases")
    if schema_version != SCHEMA_VERSION:
        raise ValueError(f"unsupported case schema_version {schema_version!r}")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("case file must contain a non-empty cases list")
    cases = [_validate_case(case, index) for index, case in enumerate(raw_cases, 1)]
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case id")
    return cases


def _validate_source(raw: object, case_id: str) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        raise ValueError(f"case {case_id!r} source must be an object")
    extra = set(raw) - set(SOURCE_FIELDS)
    if extra:
        raise ValueError(
            f"case {case_id!r} source has unsupported fields: {sorted(extra)}"
        )
    missing = [key for key in SOURCE_FIELDS if key not in raw]
    if missing:
        raise ValueError(f"case {case_id!r} source missing fields: {missing}")
    doi = raw["doi"]
    license_ = raw["license"]
    text = raw["text"]
    if not isinstance(doi, str) or not _DOI_RE.match(doi.strip()):
        raise ValueError(f"case {case_id!r} source.doi must look like a DOI")
    if not isinstance(license_, str) or not license_.strip():
        raise ValueError(f"case {case_id!r} source.license must be a non-empty string")
    if not _LICENSE_RE.search(license_.strip()):
        raise ValueError(
            f"case {case_id!r} source.license must be a Creative Commons token"
        )
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"case {case_id!r} source.text must be a non-empty string")
    if len(text) > MAX_SOURCE_CHARS:
        raise ValueError(
            f"case {case_id!r} source.text exceeds {MAX_SOURCE_CHARS} characters"
        )
    return {
        "doi": doi.strip(),
        "license": license_.strip(),
        "text": text,
    }


def _validate_case(raw: object, index: int) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError(f"case {index} must be an object")
    extra = set(raw) - set(CASE_FIELDS)
    if extra:
        raise ValueError(f"case {index} has unsupported fields: {sorted(extra)}")
    missing = [key for key in CASE_FIELDS if key not in raw]
    if missing:
        raise ValueError(f"case {index} missing fields: {missing}")
    case_id = raw["id"]
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError(f"case {index}.id must be a non-empty string")
    lang = raw["lang"]
    if lang not in LANGS:
        raise ValueError(f"case {case_id!r} lang must be one of {LANGS}")
    perturbation = raw["perturbation"]
    if perturbation not in PERTURBATIONS:
        raise ValueError(
            f"case {case_id!r} perturbation must be one of {PERTURBATIONS}"
        )
    claim = raw["claim"]
    if not isinstance(claim, str) or not claim.strip():
        raise ValueError(f"case {case_id!r} claim must be a non-empty string")
    quote = raw["quote"]
    if quote is None:
        quote = ""
    if not isinstance(quote, str):
        raise ValueError(f"case {case_id!r} quote must be a string")
    source = _validate_source(raw["source"], case_id)
    gold_status = raw["gold_status"]
    if gold_status not in GOLD_STATUSES:
        raise ValueError(f"case {case_id!r} gold_status must be one of {GOLD_STATUSES}")
    gold_relation = raw["gold_relation"]
    if gold_relation not in GOLD_RELATIONS:
        raise ValueError(
            f"case {case_id!r} gold_relation must be one of {GOLD_RELATIONS}"
        )
    split = raw["split"]
    if split not in SPLITS:
        raise ValueError(f"case {case_id!r} split must be one of {SPLITS}")
    return {
        "id": case_id,
        "lang": lang,
        "perturbation": perturbation,
        "claim": claim.strip(),
        "quote": quote,
        "source": source,
        "gold_status": gold_status,
        "gold_relation": gold_relation,
        "split": split,
    }


def assign_splits(
    cases: Sequence[Mapping[str, Any]],
    *,
    seed: int = SPLIT_SEED,
) -> list[dict[str, Any]]:
    """Stratify by lang × perturbation, 50/50, with a fixed RNG seed."""

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for case in cases:
        row = dict(case)
        key = (str(row["lang"]), str(row["perturbation"]))
        grouped.setdefault(key, []).append(row)
    rng = random.Random(seed)
    out: list[dict[str, Any]] = []
    for key in sorted(grouped):
        bucket = grouped[key]
        bucket.sort(key=lambda row: str(row["id"]))
        rng.shuffle(bucket)
        mid = len(bucket) // 2
        for index, row in enumerate(bucket):
            row["split"] = "dev" if index < mid else "test"
            out.append(row)
    out.sort(key=lambda row: str(row["id"]))
    return out


def stratum_counts(cases: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in cases:
        key = f"{case['lang']}:{case['perturbation']}:{case.get('split', '')}"
        counts[key] = counts.get(key, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Code path: normalize, locate, quantities
# ---------------------------------------------------------------------------


def normalize_text(text: str) -> str:
    """NFKC, fold whitespace, straighten quotes, unify hyphens (W3-A spec)."""

    collapsed = unicodedata.normalize("NFKC", text or "")
    for src, dst in _QUOTE_MAP.items():
        collapsed = collapsed.replace(src, dst)
    for hyphen in _HYPHENS:
        collapsed = collapsed.replace(hyphen, "-")
    collapsed = collapsed.replace("∶", ":").replace("：", ":")
    return re.sub(r"\s+", " ", collapsed).strip()


def locate_quote(quote: str, source: str) -> dict[str, Any] | None:
    """Exact then controlled fuzzy locate. None means not found."""

    needle = normalize_text(quote)
    haystack = normalize_text(source)
    if not needle:
        return None
    index = haystack.find(needle)
    if index >= 0:
        return {"match": "exact", "span": [index, index + len(needle)], "score": 1.0}
    window = max(16, len(needle))
    best: tuple[float, int, int] | None = None
    step = max(1, window // 8)
    limit = max(0, len(haystack) - window + 1)
    for start in range(0, limit, step):
        chunk = haystack[start : start + window]
        score = difflib.SequenceMatcher(None, needle, chunk).ratio()
        if best is None or score > best[0]:
            best = (score, start, start + len(chunk))
    if best is None or best[0] < FUZZY_MIN_RATIO:
        return None
    return {
        "match": f"fuzzy:{best[0]:.3f}",
        "span": [best[1], best[2]],
        "score": best[0],
    }


def _parse_number(raw: str) -> float | None:
    compact = raw.replace(",", "").replace(" ", "")
    try:
        return float(compact)
    except ValueError:
        return None


def extract_quantities(text: str) -> list[dict[str, Any]]:
    """Pull (number, unit) pairs. Years without a unit are dropped."""

    found: list[dict[str, Any]] = []
    seen: set[tuple[float, str]] = set()
    for match in _QUANTITY_RE.finditer(text or ""):
        number = _parse_number(match.group("num"))
        if number is None:
            continue
        unit = (match.group("unit") or "").strip()
        if not unit:
            raw = match.group("num")
            digits = re.sub(r"[^\d]", "", raw)
            if len(digits) == 4 and 1900 <= number <= 2100:
                continue
        key = (number, unit.lower())
        if key in seen:
            continue
        seen.add(key)
        found.append(
            {
                "number": number,
                "unit": unit,
                "raw": match.group(0).strip(),
            }
        )
    return found


def _norm_unit(unit: str) -> str:
    token = (unit or "").strip().lower().replace("％", "%")
    token = token.replace("µg", "μg").replace("µl", "μl").replace("µm", "μm")
    token = token.replace("ul", "μl").replace("um", "μm")
    return token


def quantities_mismatch(claim: str, section: str) -> bool:
    """True when a claim quantity is missing from the section or units conflict.

    This is the eval-side CODE detector described in plan §6 W3-A. When
    W3-A's helper is importable, live ``J`` uses that implementation instead.
    """

    claim_qs = extract_quantities(claim)
    if not claim_qs:
        return False
    section_qs = extract_quantities(section)
    for item in claim_qs:
        number = item["number"]
        unit = _norm_unit(item["unit"])
        matched_pair = False
        matched_number = False
        for other in section_qs:
            if abs(other["number"] - number) > 1e-9:
                continue
            matched_number = True
            if _norm_unit(other["unit"]) == unit:
                matched_pair = True
                break
        if unit:
            if not matched_pair:
                return True
        elif not matched_number:
            return True
    return False


def run_code_path(case: Mapping[str, Any]) -> dict[str, Any]:
    """Locate + numeric/unit compare only. Never calls a judge backend."""

    source_text = str((case.get("source") or {}).get("text") or "")
    quote = str(case.get("quote") or "")
    claim = str(case.get("claim") or "")
    located = locate_quote(quote, source_text) if quote.strip() else None
    if quote.strip() and located is None:
        return {
            "status": "not_found_needs_review",
            "relation": None,
            "confidence": 1.0,
            "probabilities": None,
            "match": None,
            "span": None,
            "numeric_mismatch": False,
        }
    section = source_text
    if located is not None:
        start, end = located["span"]
        section = normalize_text(source_text)[start:end] or source_text
    mismatched = quantities_mismatch(claim, section if quote.strip() else source_text)
    if mismatched:
        return {
            "status": "numeric_mismatch",
            "relation": "contradicts",
            "confidence": 1.0,
            "probabilities": None,
            "match": None if located is None else located["match"],
            "span": None if located is None else located["span"],
            "numeric_mismatch": True,
        }
    return {
        "status": "no_judgment",
        "relation": None,
        "confidence": None,
        "probabilities": None,
        "match": None if located is None else located["match"],
        "span": None if located is None else located["span"],
        "numeric_mismatch": False,
    }


def dataset_stats(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Offline counts plus code-path detection rates on constructed labels."""

    by_lang = {lang: 0 for lang in LANGS}
    by_pert = {name: 0 for name in PERTURBATIONS}
    by_status = {name: 0 for name in GOLD_STATUSES}
    by_relation = {name: 0 for name in GOLD_RELATIONS}
    licenses: dict[str, int] = {}
    for case in cases:
        by_lang[str(case["lang"])] = by_lang.get(str(case["lang"]), 0) + 1
        by_pert[str(case["perturbation"])] = (
            by_pert.get(str(case["perturbation"]), 0) + 1
        )
        by_status[str(case["gold_status"])] = (
            by_status.get(str(case["gold_status"]), 0) + 1
        )
        by_relation[str(case["gold_relation"])] = (
            by_relation.get(str(case["gold_relation"]), 0) + 1
        )
        license_ = str((case.get("source") or {}).get("license") or "")
        licenses[license_] = licenses.get(license_, 0) + 1

    numeric_cases = [
        case for case in cases if case.get("perturbation") in NUMERIC_PERTURBATIONS
    ]
    numeric_hits = 0
    for case in numeric_cases:
        outcome = run_code_path(case)
        if outcome["status"] == "numeric_mismatch":
            numeric_hits += 1
    fake_cases = [
        case for case in cases if case.get("perturbation") == "fabricated_quote"
    ]
    fake_hits = 0
    for case in fake_cases:
        outcome = run_code_path(case)
        if outcome["status"] in REVIEW_STATUSES:
            fake_hits += 1
    n_cases = len(cases)
    zh_n = by_lang.get("zh", 0)
    return {
        "n": n_cases,
        "by_lang": by_lang,
        "by_perturbation": by_pert,
        "by_gold_status": by_status,
        "by_gold_relation": by_relation,
        "licenses": licenses,
        "zh_fraction": None if n_cases == 0 else zh_n / n_cases,
        "code_path_numeric_detection": {
            "n": len(numeric_cases),
            "detected": numeric_hits,
            "rate": None if not numeric_cases else numeric_hits / len(numeric_cases),
        },
        "code_path_not_found_detection": {
            "n": len(fake_cases),
            "detected": fake_hits,
            "rate": None if not fake_cases else fake_hits / len(fake_cases),
        },
    }


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def normalize_status(status: str | None) -> str | None:
    if not status:
        return None
    if status == "not_found":
        return "not_found_needs_review"
    return status


def relation_of(pred: Mapping[str, Any]) -> str | None:
    relation = pred.get("relation")
    if isinstance(relation, str) and relation in GOLD_RELATIONS:
        return relation
    status = normalize_status(
        pred.get("status") if isinstance(pred.get("status"), str) else None
    )
    mapped = STATUS_TO_RELATION.get(status or "")
    return mapped


def case_indicators(
    gold_status: str,
    gold_relation: str,
    pred: Mapping[str, Any],
    *,
    claim_conf: float,
) -> dict[str, int | None]:
    """Per-row 0/1 indicators. ``None`` means the row is not in the denominator."""

    pred_status = normalize_status(
        pred.get("status") if isinstance(pred.get("status"), str) else None
    )
    pred_relation = relation_of(pred)
    confidence = pred.get("confidence")
    conf_value: float | None
    try:
        conf_value = None if confidence is None else float(confidence)
    except (TypeError, ValueError):
        conf_value = None
    review = 1 if pred_status in REVIEW_STATUSES else 0
    if pred_status in {None, "no_judgment", "disabled"} and pred_relation is None:
        status_acc: int | None = None
        relation_acc: int | None = None
        high_conf: int | None = None
    else:
        status_acc = (
            None
            if pred_status in {None, "no_judgment", "disabled", "uncertain"}
            else int(pred_status == gold_status)
        )
        relation_acc = (
            None if pred_relation is None else int(pred_relation == gold_relation)
        )
        if conf_value is None or conf_value < claim_conf:
            high_conf = None
        elif pred_relation is None:
            high_conf = None
        else:
            high_conf = int(pred_relation != gold_relation)
    support = None if gold_relation != "supports" else int(pred_relation == "supports")
    contradict = (
        None if gold_relation != "contradicts" else int(pred_relation == "contradicts")
    )
    return {
        "status_accuracy": status_acc,
        "relation_accuracy": relation_acc,
        "high_conf_error": high_conf,
        "support_recall": support,
        "contradiction_recall": contradict,
        "needs_review": review,
    }


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _percentile(values: Sequence[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    p = min(1.0, max(0.0, p))
    rank = (len(ordered) - 1) * p
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return float(ordered[int(rank)])
    weight = rank - low
    return float(ordered[low]) * (1.0 - weight) + float(ordered[high]) * weight


def bootstrap_mean_ci(
    values: Sequence[float],
    *,
    n_resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
    alpha: float = 0.05,
) -> tuple[float | None, float | None]:
    """Percentile bootstrap 95% CI of the mean. Empty input → (None, None)."""

    if not values:
        return None, None
    if len(values) == 1:
        point = float(values[0])
        return point, point
    rng = random.Random(seed)
    n_obs = len(values)
    samples = []
    for _ in range(n_resamples):
        total = 0.0
        for _again in range(n_obs):
            total += values[rng.randrange(n_obs)]
        samples.append(total / n_obs)
    samples.sort()
    lo = _percentile(samples, alpha / 2)
    hi = _percentile(samples, 1.0 - alpha / 2)
    return lo, hi


def _ratio_block(values: Sequence[float]) -> dict[str, Any]:
    point = _mean(values)
    lo, hi = bootstrap_mean_ci(values)
    return {
        "n": len(values),
        "value": point,
        "ci95": [lo, hi],
    }


def _confusion(
    rows: Sequence[Mapping[str, Any]],
    *,
    gold_key: str,
    pred_key: str,
    labels: Sequence[str],
) -> dict[str, dict[str, int]]:
    matrix = {gold: {pred: 0 for pred in labels} for gold in labels}
    for row in rows:
        gold = row.get(gold_key)
        pred = row.get(pred_key)
        if gold not in matrix:
            continue
        if pred not in matrix[gold]:
            continue
        matrix[gold][pred] += 1
    return matrix


def score_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    claim_conf: float,
) -> dict[str, Any]:
    def _col(name: str) -> list[int]:
        return [
            int(row["indicators"][name])
            for row in rows
            if row["indicators"][name] is not None
        ]

    latencies = [float(row.get("latency_ms") or 0) for row in rows]
    tokens = [int(row.get("input_tokens") or 0) for row in rows]
    total_tokens = sum(tokens)
    n_rows = len(rows)
    per_item_cost = (
        []
        if n_rows == 0
        else [tok / 1_000_000.0 * INPUT_TOKEN_USD_PER_MILLION for tok in tokens]
    )
    status_labels = list(GOLD_STATUSES)
    relation_labels = list(GOLD_RELATIONS)
    by_gold_status: dict[str, Any] = {}
    for status in GOLD_STATUSES:
        slice_rows = [row for row in rows if row.get("gold_status") == status]
        by_gold_status[status] = _ratio_block(
            [
                int(row["indicators"]["status_accuracy"])
                for row in slice_rows
                if row["indicators"]["status_accuracy"] is not None
            ]
        )
    by_gold_relation: dict[str, Any] = {}
    for relation in GOLD_RELATIONS:
        slice_rows = [row for row in rows if row.get("gold_relation") == relation]
        by_gold_relation[relation] = _ratio_block(
            [
                int(row["indicators"]["relation_accuracy"])
                for row in slice_rows
                if row["indicators"]["relation_accuracy"] is not None
            ]
        )
    return {
        "n": n_rows,
        "claim_conf": claim_conf,
        "status_accuracy": _ratio_block(_col("status_accuracy")),
        "relation_accuracy": _ratio_block(_col("relation_accuracy")),
        "high_conf_error_rate": _ratio_block(_col("high_conf_error")),
        "support_recall": _ratio_block(_col("support_recall")),
        "contradiction_recall": _ratio_block(_col("contradiction_recall")),
        "needs_review_rate": _ratio_block(_col("needs_review")),
        "by_gold_status": by_gold_status,
        "by_gold_relation": by_gold_relation,
        "status_confusion": _confusion(
            rows,
            gold_key="gold_status",
            pred_key="pred_status",
            labels=status_labels,
        ),
        "relation_confusion": _confusion(
            rows,
            gold_key="gold_relation",
            pred_key="pred_relation",
            labels=relation_labels,
        ),
        "latency_ms": {
            "n": len(latencies),
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
        },
        "input_tokens": total_tokens,
        "cost_usd": total_tokens / 1_000_000.0 * INPUT_TOKEN_USD_PER_MILLION,
        "cost_usd_per_item": {
            "n": len(per_item_cost),
            "mean": _mean(per_item_cost),
            "p50": _percentile(per_item_cost, 0.50),
            "p95": _percentile(per_item_cost, 0.95),
        },
    }


def score_predictions(
    cases: Sequence[Mapping[str, Any]],
    predictions: Mapping[str, Mapping[str, Any]],
    *,
    claim_conf: float,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        pred = dict(predictions.get(case["id"], {}))
        pred_status = normalize_status(
            pred.get("status") if isinstance(pred.get("status"), str) else None
        )
        pred_relation = relation_of(pred)
        indicators = case_indicators(
            str(case["gold_status"]),
            str(case["gold_relation"]),
            pred,
            claim_conf=claim_conf,
        )
        rows.append(
            {
                "id": case["id"],
                "lang": case["lang"],
                "perturbation": case["perturbation"],
                "gold_status": case["gold_status"],
                "gold_relation": case["gold_relation"],
                "pred_status": pred_status,
                "pred_relation": pred_relation,
                "confidence": pred.get("confidence"),
                "indicators": indicators,
                "requests": int(pred.get("requests") or 0),
                "latency_ms": float(pred.get("latency_ms") or 0.0),
                "input_tokens": int(pred.get("input_tokens") or 0),
                "error": pred.get("error"),
            }
        )
    by_lang = {
        lang: score_rows(
            [row for row in rows if row["lang"] == lang], claim_conf=claim_conf
        )
        for lang in LANGS
    }
    by_perturbation = {
        name: score_rows(
            [row for row in rows if row["perturbation"] == name],
            claim_conf=claim_conf,
        )
        for name in PERTURBATIONS
    }
    return {
        "overall": score_rows(rows, claim_conf=claim_conf),
        "by_lang": by_lang,
        "by_perturbation": by_perturbation,
        "cases": rows,
    }


# ---------------------------------------------------------------------------
# Systems
# ---------------------------------------------------------------------------


def claim_conf_threshold() -> float:
    try:
        from openai4s.judgment.templates.literature import CLAIM_CONF
    except ImportError:
        return DEFAULT_CLAIM_CONF
    try:
        return float(CLAIM_CONF)
    except (TypeError, ValueError):
        return DEFAULT_CLAIM_CONF


def literature_template_version() -> str | None:
    try:
        from openai4s.judgment.templates.literature import TEMPLATE_VERSION
    except ImportError:
        return None
    return str(TEMPLATE_VERSION)


def load_literature_kernel() -> Any:
    """Load ``skills/literature-review/kernel.py`` without requiring W3-A APIs."""

    global _KERNEL_MOD
    if _KERNEL_MOD is not None:
        return _KERNEL_MOD
    if not KERNEL_PATH.is_file():
        raise RuntimeError(f"literature-review kernel missing at {KERNEL_PATH}")
    spec = importlib.util.spec_from_file_location(
        "openai4s_eval_literature_kernel", KERNEL_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load literature-review kernel")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _KERNEL_MOD = module
    return module


def w3a_numeric_compare() -> Any | None:
    """Return W3-A's quantity helper if it has landed; else None."""

    try:
        module = load_literature_kernel()
    except Exception:
        return None
    for name in (
        "quantities_mismatch",
        "numeric_mismatch",
        "compare_quantities",
        "numbers_conflict",
    ):
        fn = getattr(module, name, None)
        if callable(fn):
            return fn
    return None


def _install_host_shim() -> None:
    """In-process ``host.judge`` for ``check_claims`` outside a kernel."""

    if "host" in sys.modules:
        return

    class _HostShim:
        def judge(self, template: str, state: object, **params: Any) -> Any:
            from openai4s.config import get_config
            from openai4s.host.judgment import JudgmentService

            service = JudgmentService(get_config(), None)
            result = service.run(
                purpose="literature_check",
                template_id=str(template),
                state=state,
                params=params or None,
            )
            return result.to_dict() if hasattr(result, "to_dict") else result

    sys.modules["host"] = _HostShim()


def _check_claims_fn() -> Any:
    module = load_literature_kernel()
    fn = getattr(module, "check_claims", None)
    if not callable(fn):
        raise RuntimeError(
            "check_claims is not available; W3-A has not landed in this tree"
        )
    _install_host_shim()
    return fn


def _input_tokens_from_usage(usage: object) -> int:
    if not isinstance(usage, Mapping):
        return 0
    for key in ("input_tokens", "prompt_tokens"):
        value = usage.get(key)
        if type(value) is int and value >= 0:
            return value
    return 0


def _parse_llm_relation(text: str) -> tuple[str | None, float | None]:
    blob = (text or "").strip()
    if not blob:
        return None, None
    try:
        start = blob.find("{")
        end = blob.rfind("}")
        payload = json.loads(
            blob[start : end + 1] if start >= 0 and end > start else blob
        )
    except (ValueError, json.JSONDecodeError):
        lowered = blob.lower()
        for name in GOLD_RELATIONS:
            if name in lowered:
                return name, None
        return None, None
    if not isinstance(payload, Mapping):
        return None, None
    relation = payload.get("relation")
    if not isinstance(relation, str):
        return None, None
    relation = relation.strip().lower()
    if relation not in GOLD_RELATIONS:
        return None, None
    confidence = payload.get("confidence")
    try:
        conf_value = None if confidence is None else float(confidence)
    except (TypeError, ValueError):
        conf_value = None
    return relation, conf_value


def _run_llm(case: Mapping[str, Any]) -> dict[str, Any]:
    from openai4s.config import get_config
    from openai4s.llm import chat

    section = str((case.get("source") or {}).get("text") or "")
    prompt = LLM_PROMPT.format(claim=case["claim"], section=section)
    cfg = get_config()
    reply = chat(
        [{"role": "user", "content": prompt}],
        cfg.llm,
        max_tokens=128,
        temperature=0,
    )
    text = ""
    tokens = 0
    if isinstance(reply, Mapping):
        content = reply.get("content")
        if isinstance(content, str):
            text = content
        text = text or str(reply.get("text") or "")
        tokens = _input_tokens_from_usage(reply.get("usage"))
    relation, confidence = _parse_llm_relation(text)
    status = None
    if relation == "supports":
        status = "verified"
    elif relation == "contradicts":
        status = "contradicted"
    elif relation == "insufficient":
        status = "unsupported"
    return {
        "status": status,
        "relation": relation,
        "confidence": confidence,
        "probabilities": None,
        "requests": 1,
        "input_tokens": tokens,
        "error": None if relation else "unparsed_llm_relation",
    }


def _first_mapping(payload: object) -> Mapping[str, Any] | None:
    if isinstance(payload, Mapping):
        return payload
    if isinstance(payload, Sequence) and payload and isinstance(payload[0], Mapping):
        return payload[0]
    return None


def _run_j(case: Mapping[str, Any]) -> dict[str, Any]:
    check_claims = _check_claims_fn()
    source = case["source"]
    claim_row = {
        "claim_id": case["id"],
        "claim": case["claim"],
        "source_id": "src",
    }
    quote = case.get("quote") or ""
    if quote.strip():
        claim_row["quote"] = quote
    payload = check_claims(
        [claim_row],
        {"src": {"text": source["text"], "version_id": source.get("doi")}},
    )
    row = _first_mapping(payload)
    if row is None:
        raise RuntimeError("check_claims returned no object")
    status = row.get("status")
    relation = row.get("relation")
    if not isinstance(relation, str):
        answers = row.get("answers")
        if isinstance(answers, Mapping):
            rel_ans = answers.get("relation")
            if isinstance(rel_ans, Mapping):
                relation = rel_ans.get("value")
    confidence = row.get("confidence")
    if confidence is None and isinstance(row.get("answers"), Mapping):
        rel_ans = row["answers"].get("relation")
        if isinstance(rel_ans, Mapping):
            confidence = rel_ans.get("confidence")
    usage = row.get("usage")
    latency = row.get("latency_ms")
    return {
        "status": status if isinstance(status, str) else None,
        "relation": relation if isinstance(relation, str) else None,
        "confidence": confidence,
        "probabilities": row.get("probabilities") or row.get("answers"),
        "requests": 1,
        "input_tokens": _input_tokens_from_usage(usage),
        "latency_ms": None if latency is None else float(latency),
        "error": None,
        "match": row.get("match"),
        "span": row.get("span"),
    }


def run_system(system: str, case: Mapping[str, Any]) -> dict[str, Any]:
    """Run one system on one case. Never raises into the scorer."""

    started = time.perf_counter()
    try:
        if system == "CODE":
            outcome = run_code_path(case)
            outcome["requests"] = 0
            outcome["input_tokens"] = 0
            outcome["error"] = None
        elif system == "LLM":
            outcome = _run_llm(case)
        elif system == "J":
            outcome = _run_j(case)
        else:
            raise ValueError(f"unknown system {system!r}")
    except Exception as exc:  # noqa: BLE001 - one case must not abort the run
        outcome = {
            "status": None,
            "relation": None,
            "confidence": None,
            "probabilities": None,
            "requests": 0,
            "input_tokens": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }
    if outcome.get("latency_ms") is None:
        outcome["latency_ms"] = (time.perf_counter() - started) * 1000.0
    return outcome


def evaluate_system(
    system: str,
    cases: Sequence[Mapping[str, Any]],
    *,
    claim_conf: float,
) -> dict[str, Any]:
    predictions: dict[str, dict[str, Any]] = {}
    for case in cases:
        predictions[case["id"]] = run_system(system, case)
    report = score_predictions(cases, predictions, claim_conf=claim_conf)
    report["system"] = system
    return report


def fake_endpoint_active() -> bool:
    return bool(os.environ.get("OPENAI4S_JUDGMENT_FAKE_ENDPOINT", "").strip())


def git_head() -> str | None:
    root = Path(__file__).resolve().parents[2]
    try:
        output = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return output.decode("ascii", errors="replace").strip() or None


def _fmt_ratio(block: Mapping[str, Any]) -> str:
    value = block.get("value")
    ci = block.get("ci95") or [None, None]
    n = block.get("n")
    if value is None:
        return f"n/a (n={n})"
    lo, hi = ci[0], ci[1]
    if lo is None or hi is None:
        return f"{value:.3f} (n={n})"
    return f"{value:.3f} [{lo:.3f}, {hi:.3f}] (n={n})"


def render_dataset_markdown(stats: Mapping[str, Any]) -> list[str]:
    numeric = stats.get("code_path_numeric_detection") or {}
    fake = stats.get("code_path_not_found_detection") or {}
    lines = [
        "## dataset",
        "",
        f"- n: {stats.get('n')}",
        f"- zh fraction: {stats.get('zh_fraction')}",
        f"- by lang: {stats.get('by_lang')}",
        f"- by perturbation: {stats.get('by_perturbation')}",
        f"- by gold_status: {stats.get('by_gold_status')}",
        f"- by gold_relation: {stats.get('by_gold_relation')}",
        f"- licenses: {stats.get('licenses')}",
        (
            f"- code-path numeric detection: {numeric.get('detected')}/"
            f"{numeric.get('n')} rate={numeric.get('rate')}"
        ),
        (
            f"- code-path not_found detection: {fake.get('detected')}/"
            f"{fake.get('n')} rate={fake.get('rate')}"
        ),
        "- claims are synthetic rewrites of OA excerpts; human review of at least 20% is required",
        "",
    ]
    return lines


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Literature claim-check evaluation",
        "",
        f"- git HEAD: `{report.get('git_head') or 'unknown'}`",
        f"- split: `{report.get('split')}`",
        f"- n: {report.get('n')}",
        f"- systems: {', '.join(report.get('systems_run') or [])}",
        f"- fake endpoint: {'yes — scores are not meaningful' if report.get('fake_endpoint') else 'no'}",
        f"- LLM prompt version: `{LLM_PROMPT_VERSION}`",
        f"- CLAIM_CONF: {report.get('claim_conf')}",
        f"- Jev input token price: ${INPUT_TOKEN_USD_PER_MILLION}/million",
        f"- bootstrap: {BOOTSTRAP_RESAMPLES} resamples, seed {BOOTSTRAP_SEED}, percentile 95% CI",
        f"- split seed: {SPLIT_SEED}",
    ]
    if report.get("frozen_thresholds"):
        lines.append(f"- frozen thresholds: `{report['frozen_thresholds']}`")
    if report.get("fake_endpoint"):
        lines.extend(
            [
                "",
                "> Scores produced against a loopback fake endpoint are not a",
                "> quality signal. They only prove the wiring.",
            ]
        )
    lines.append("")
    stats = report.get("dataset") or {}
    if stats:
        lines.extend(render_dataset_markdown(stats))
    for system in report.get("systems_run") or []:
        block = report["results"][system]
        overall = block["overall"]
        lines.extend(
            [
                f"## {system}",
                "",
                "### overall",
                "",
                f"- status accuracy: {_fmt_ratio(overall['status_accuracy'])}",
                f"- relation accuracy: {_fmt_ratio(overall['relation_accuracy'])}",
                f"- high-confidence error rate: {_fmt_ratio(overall['high_conf_error_rate'])}",
                f"- support recall: {_fmt_ratio(overall['support_recall'])}",
                f"- contradiction recall: {_fmt_ratio(overall['contradiction_recall'])}",
                f"- needs-review rate: {_fmt_ratio(overall['needs_review_rate'])}",
                (
                    f"- latency_ms p50 {overall['latency_ms']['p50']:.1f}"
                    f" p95 {overall['latency_ms']['p95']:.1f}"
                    if overall["latency_ms"]["p50"] is not None
                    else "- latency_ms: n/a"
                ),
                f"- input tokens: {overall['input_tokens']}",
                f"- cost USD: {overall['cost_usd']:.8f}",
                (
                    f"- cost USD/item mean {overall['cost_usd_per_item']['mean']:.8f}"
                    if overall["cost_usd_per_item"]["mean"] is not None
                    else "- cost USD/item: n/a"
                ),
                "",
                "### by language",
                "",
            ]
        )
        for lang in LANGS:
            slice_ = block["by_lang"][lang]
            lines.append(
                f"- **{lang}** (n={slice_['n']}): "
                f"status {_fmt_ratio(slice_['status_accuracy'])}; "
                f"relation {_fmt_ratio(slice_['relation_accuracy'])}; "
                f"high-conf-err {_fmt_ratio(slice_['high_conf_error_rate'])}; "
                f"support {_fmt_ratio(slice_['support_recall'])}; "
                f"contradict {_fmt_ratio(slice_['contradiction_recall'])}; "
                f"review {_fmt_ratio(slice_['needs_review_rate'])}"
            )
        lines.extend(["", "### by perturbation", ""])
        for name in PERTURBATIONS:
            slice_ = block["by_perturbation"][name]
            lines.append(
                f"- **{name}** (n={slice_['n']}): "
                f"status {_fmt_ratio(slice_['status_accuracy'])}; "
                f"relation {_fmt_ratio(slice_['relation_accuracy'])}"
            )
        lines.extend(["", "### status confusion (gold → pred)", ""])
        confusion = overall.get("status_confusion") or {}
        for gold in GOLD_STATUSES:
            row = confusion.get(gold) or {}
            cells = ", ".join(f"{pred}={row.get(pred, 0)}" for pred in GOLD_STATUSES)
            lines.append(f"- {gold}: {cells}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_systems(raw: str) -> list[str]:
    parts = [item.strip() for item in raw.split(",") if item.strip()]
    if not parts:
        raise ValueError("at least one system is required")
    unknown = [item for item in parts if item not in SYSTEMS]
    if unknown:
        raise ValueError(f"unknown systems {unknown}; expected one of {list(SYSTEMS)}")
    seen: set[str] = set()
    ordered: list[str] = []
    for item in parts:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def build_report(
    *,
    split: str,
    systems: Sequence[str],
    cases: Sequence[Mapping[str, Any]],
    live: bool,
    frozen_thresholds: str | None,
) -> dict[str, Any]:
    live_needed = [name for name in systems if name in LIVE_SYSTEMS]
    if live_needed and not live:
        raise SystemExit(
            f"{', '.join(live_needed)} require --live "
            "(LLM uses the configured main model; J calls check_claims)"
        )
    if split == "test":
        if not frozen_thresholds or not str(frozen_thresholds).strip():
            raise SystemExit(
                "--split test requires --frozen-thresholds <TEMPLATE_VERSION> "
                "matching openai4s.judgment.templates.literature"
            )
        version = literature_template_version()
        if version is None:
            raise SystemExit(
                "--split test: TEMPLATE_VERSION is not importable "
                "(openai4s.judgment.templates.literature is missing)"
            )
        if str(frozen_thresholds).strip() != version:
            raise SystemExit(
                f"--frozen-thresholds {frozen_thresholds!r} does not match "
                f"TEMPLATE_VERSION {version!r}"
            )
    selected = [case for case in cases if case["split"] == split]
    if not selected:
        raise SystemExit(f"no cases for split {split!r}")
    threshold = claim_conf_threshold()
    results = {
        name: evaluate_system(name, selected, claim_conf=threshold) for name in systems
    }
    fake = fake_endpoint_active() and "J" in systems
    return {
        "schema_version": SCHEMA_VERSION,
        "git_head": git_head(),
        "split": split,
        "n": len(selected),
        "systems_run": list(systems),
        "fake_endpoint": fake,
        "scores_are_not_meaningful": fake,
        "input_token_usd_per_million": INPUT_TOKEN_USD_PER_MILLION,
        "llm_prompt_version": LLM_PROMPT_VERSION,
        "claim_conf": threshold,
        "bootstrap": {
            "resamples": BOOTSTRAP_RESAMPLES,
            "seed": BOOTSTRAP_SEED,
            "method": "percentile",
        },
        "split_seed": SPLIT_SEED,
        "frozen_thresholds": frozen_thresholds if split == "test" else None,
        "dataset": dataset_stats(selected),
        "results": results,
    }


def _write_reports(report: Mapping[str, Any], out: str | None) -> tuple[Path, Path]:
    stem = f"judgment_literature-{report['split']}-{'-'.join(report['systems_run'])}"
    if out:
        target = Path(out)
        if target.suffix.lower() in {".json", ".md"}:
            directory = target.parent
            stem = target.stem
        else:
            directory = target
        directory.mkdir(parents=True, exist_ok=True)
    else:
        directory = Path.cwd()
    json_path = directory / f"{stem}.json"
    md_path = directory / f"{stem}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m harness.evals.judgment_literature",
        description="Score literature claim-check systems on the frozen eval set.",
    )
    parser.add_argument("--split", choices=SPLITS, required=True)
    parser.add_argument(
        "--systems",
        required=True,
        help="Comma-separated subset of CODE,LLM,J",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Allow LLM (live_llm three-way) and J (check_claims)",
    )
    parser.add_argument(
        "--out", help="Directory (or stem) for JSON and Markdown reports"
    )
    parser.add_argument(
        "--frozen-thresholds",
        dest="frozen_thresholds",
        help="Required for --split test; must equal TEMPLATE_VERSION",
    )
    parser.add_argument(
        "--cases", default=str(CASES_PATH), help="Override cases JSON path"
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        systems = parse_systems(args.systems)
        cases = load_cases(args.cases)
        report = build_report(
            split=args.split,
            systems=systems,
            cases=cases,
            live=args.live,
            frozen_thresholds=args.frozen_thresholds,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    json_path, md_path = _write_reports(report, args.out)
    sys.stdout.write(render_markdown(report))
    print(f"wrote {json_path}", file=sys.stderr)
    print(f"wrote {md_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
