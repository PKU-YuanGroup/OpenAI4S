"""Offline-first evaluation of Skill suggestion (plan §7 / W2-C).

Scores lexical baselines (B0, B2) without a network or key. Optional live
systems (B1, J1, J2) require ``--live``. The test split is frozen by a SHA-256
lock and may only be scored when ``--frozen-thresholds`` matches the template
version in ``openai4s.judgment.templates.skills``; this module never tunes
anything on that split.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

CASES_PATH = Path(__file__).with_name("judgment_skills_cases.json")
LOCK_PATH = Path(__file__).with_name("judgment_skills_cases.lock")
TERMS_PATH = Path(__file__).with_name("judgment_skills_zh_en_terms.json")

SCHEMA_VERSION = 1
SPLIT_SEED = 20260920
BOOTSTRAP_SEED = 20260920
BOOTSTRAP_RESAMPLES = 1000
INPUT_TOKEN_USD_PER_MILLION = 0.042
TOP_K = 3

LANGS = ("zh", "en")
CATEGORIES = (
    "curated",
    "bioskills_member",
    "multi_skill",
    "no_skill",
    "ambiguous",
)
SPLITS = ("dev", "test")
SYSTEMS = ("B0", "B1", "B2", "J1", "J2")
LIVE_SYSTEMS = frozenset({"B1", "J1", "J2"})
CASE_FIELDS = ("id", "lang", "category", "query", "gold", "notes", "split")

_LOADER: Any = None
_GLOSSARY: tuple[dict[str, str], ...] | None = None


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


def canonical_test_payload(cases: Sequence[Mapping[str, Any]]) -> str:
    """Serialize the test split the way the lock file hashes it.

    Test cases sorted by ``id``, each object with a fixed key order, UTF-8 JSON
    with no extra whitespace. Changing a test query, gold label, or split
    assignment changes this string.
    """

    test = [dict(case) for case in cases if case.get("split") == "test"]
    test.sort(key=lambda row: str(row.get("id", "")))
    ordered = []
    for case in test:
        ordered.append({key: case[key] for key in CASE_FIELDS if key in case})
    return json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))


def test_set_sha256(cases: Sequence[Mapping[str, Any]]) -> str:
    payload = canonical_test_payload(cases)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
    category = raw["category"]
    if category not in CATEGORIES:
        raise ValueError(f"case {case_id!r} category must be one of {CATEGORIES}")
    query = raw["query"]
    if not isinstance(query, str) or not query.strip():
        raise ValueError(f"case {case_id!r} query must be a non-empty string")
    gold = raw["gold"]
    if not isinstance(gold, list) or len(gold) > 3:
        raise ValueError(f"case {case_id!r} gold must be a list of 0–3 names")
    names: list[str] = []
    seen: set[str] = set()
    for item in gold:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"case {case_id!r} gold entries must be non-empty strings")
        if item in seen:
            raise ValueError(f"case {case_id!r} gold has a duplicate name {item!r}")
        seen.add(item)
        names.append(item)
    notes = raw["notes"]
    if not isinstance(notes, str):
        raise ValueError(f"case {case_id!r} notes must be a string")
    split = raw["split"]
    if split not in SPLITS:
        raise ValueError(f"case {case_id!r} split must be one of {SPLITS}")
    return {
        "id": case_id,
        "lang": lang,
        "category": category,
        "query": query,
        "gold": names,
        "notes": notes,
        "split": split,
    }


def assign_splits(
    cases: Sequence[Mapping[str, Any]],
    *,
    seed: int = SPLIT_SEED,
) -> list[dict[str, Any]]:
    """Stratify by lang × category, 50/50, with a fixed RNG seed."""

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for case in cases:
        row = dict(case)
        key = (str(row["lang"]), str(row["category"]))
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
        key = f"{case['lang']}:{case['category']}:{case.get('split', '')}"
        counts[key] = counts.get(key, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def case_indicators(
    gold: Sequence[str],
    pred: Sequence[str],
) -> dict[str, int | None]:
    """Per-query 0/1 indicators. ``None`` means the row is not in the denominator."""

    gold_set = {name for name in gold if name}
    pred_list = [name for name in list(pred)[:TOP_K] if name]
    abstained = not pred_list
    gold_empty = not gold_set
    if gold_empty:
        top1: int | None = None
        top3: int | None = None
        error_rec: int | None = None
        unnecessary: int | None = 0 if abstained else 1
    else:
        top1 = 1 if pred_list and pred_list[0] in gold_set else 0
        top3 = 1 if gold_set.intersection(pred_list) else 0
        error_rec = 1 if any(name not in gold_set for name in pred_list) else 0
        unnecessary = None
    return {
        "top1": top1,
        "top3_recall": top3,
        "error_recommendation": error_rec,
        "unnecessary_recommendation": unnecessary,
        "abstention": 1 if abstained else 0,
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


def score_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate plan-§7 metrics for one slice of scored rows."""

    top1 = [
        int(row["indicators"]["top1"])
        for row in rows
        if row["indicators"]["top1"] is not None
    ]
    top3 = [
        int(row["indicators"]["top3_recall"])
        for row in rows
        if row["indicators"]["top3_recall"] is not None
    ]
    error_rec = [
        int(row["indicators"]["error_recommendation"])
        for row in rows
        if row["indicators"]["error_recommendation"] is not None
    ]
    unnecessary = [
        int(row["indicators"]["unnecessary_recommendation"])
        for row in rows
        if row["indicators"]["unnecessary_recommendation"] is not None
    ]
    abstention = [int(row["indicators"]["abstention"]) for row in rows]
    requests = [float(row.get("requests") or 0) for row in rows]
    latencies = [float(row.get("latency_ms") or 0) for row in rows]
    tokens = [int(row.get("input_tokens") or 0) for row in rows]
    total_tokens = sum(tokens)
    return {
        "n": len(rows),
        "top1_accuracy": _ratio_block(top1),
        "top3_recall": _ratio_block(top3),
        "error_recommendation_rate": _ratio_block(error_rec),
        "unnecessary_recommendation_rate": _ratio_block(unnecessary),
        "abstention_rate": _ratio_block(abstention),
        "requests_per_query": {
            "n": len(requests),
            "mean": _mean(requests),
            "p50": _percentile(requests, 0.50),
            "p95": _percentile(requests, 0.95),
        },
        "latency_ms": {
            "n": len(latencies),
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
        },
        "input_tokens": total_tokens,
        "cost_usd": total_tokens / 1_000_000.0 * INPUT_TOKEN_USD_PER_MILLION,
    }


def score_predictions(
    cases: Sequence[Mapping[str, Any]],
    predictions: Mapping[str, Sequence[str]],
    *,
    extra: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Score an id → predicted-names map. ``extra`` carries cost/latency per id."""

    extra = extra or {}
    rows: list[dict[str, Any]] = []
    for case in cases:
        pred = list(predictions.get(case["id"], ()))
        info = dict(extra.get(case["id"], {}))
        indicators = case_indicators(case["gold"], pred)
        rows.append(
            {
                "id": case["id"],
                "lang": case["lang"],
                "category": case["category"],
                "gold": list(case["gold"]),
                "pred": pred[:TOP_K],
                "indicators": indicators,
                "requests": int(info.get("requests") or 0),
                "latency_ms": float(info.get("latency_ms") or 0.0),
                "input_tokens": int(info.get("input_tokens") or 0),
                "error": info.get("error"),
            }
        )
    by_lang = {
        lang: score_rows([row for row in rows if row["lang"] == lang]) for lang in LANGS
    }
    by_category = {
        category: score_rows([row for row in rows if row["category"] == category])
        for category in CATEGORIES
    }
    return {
        "overall": score_rows(rows),
        "by_lang": by_lang,
        "by_category": by_category,
        "cases": rows,
    }


# ---------------------------------------------------------------------------
# Systems
# ---------------------------------------------------------------------------


def _skill_loader():
    global _LOADER
    if _LOADER is None:
        from openai4s.config import Config
        from openai4s.skills_loader import SkillLoader

        loader = SkillLoader(cfg=Config())
        loader.discover()
        _LOADER = loader
    return _LOADER


def known_skill_names() -> set[str]:
    """Declared names and directory keys currently visible to the loader."""

    loader = _skill_loader()
    names: set[str] = set()
    for key, skill in loader.discover().items():
        names.add(str(key))
        names.add(str(skill.name))
    return names


def lexical_top3(query: str) -> list[str]:
    hits = _skill_loader().search(query, limit=TOP_K)
    names: list[str] = []
    for hit in hits:
        name = hit.get("name") if isinstance(hit, Mapping) else None
        if isinstance(name, str) and name:
            names.append(name)
    return names[:TOP_K]


def load_glossary(path: str | Path = TERMS_PATH) -> tuple[dict[str, str], ...]:
    global _GLOSSARY
    cached_path = getattr(load_glossary, "_path", None)
    if _GLOSSARY is not None and cached_path == str(path):
        return _GLOSSARY
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, Mapping):
        raise ValueError("glossary file must contain an object")
    raw_terms = payload.get("terms")
    if not isinstance(raw_terms, list):
        raise ValueError("glossary file must contain a terms list")
    if len(raw_terms) > 300:
        raise ValueError("glossary exceeds the 300-term cap")
    terms: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_terms, 1):
        if not isinstance(item, Mapping):
            raise ValueError(f"glossary term {index} must be an object")
        zh = item.get("zh")
        en = item.get("en")
        if not isinstance(zh, str) or not zh.strip():
            raise ValueError(f"glossary term {index}.zh must be a non-empty string")
        if not isinstance(en, str) or not en.strip():
            raise ValueError(f"glossary term {index}.en must be a non-empty string")
        key = zh.strip()
        if key in seen:
            raise ValueError(f"glossary has a duplicate zh key {key!r}")
        seen.add(key)
        terms.append({"zh": key, "en": en.strip()})
    terms.sort(key=lambda item: len(item["zh"]), reverse=True)
    _GLOSSARY = tuple(terms)
    load_glossary._path = str(path)  # type: ignore[attr-defined]
    return _GLOSSARY


def expand_query_b2(query: str, lang: str, terms: Sequence[Mapping[str, str]]) -> str:
    """Append English keywords for Chinese hits. English queries pass through."""

    if lang != "zh":
        return query
    extras: list[str] = []
    used: set[str] = set()
    for item in terms:
        zh = item["zh"]
        if zh and zh in query and zh not in used:
            used.add(zh)
            extras.append(item["en"])
    if not extras:
        return query
    return query + " " + " ".join(extras)


def _input_tokens_from_usage(usage: object) -> int:
    if not isinstance(usage, Mapping):
        return 0
    for key in ("input_tokens", "prompt_tokens"):
        value = usage.get(key)
        if type(value) is int and value >= 0:
            return value
    return 0


def _rewrite_query_b1(query: str) -> tuple[str, int]:
    from openai4s.config import get_config
    from openai4s.llm import chat

    prompt = (
        "Rewrite the scientific user query into a short English keyword string "
        "for lexical skill retrieval. Output only space-separated English "
        "keywords. No punctuation, no quotes, no explanation.\n\nQuery:\n"
        f"{query}"
    )
    cfg = get_config()
    reply = chat(
        [{"role": "user", "content": prompt}],
        cfg.llm,
        max_tokens=64,
        temperature=0,
    )
    text = ""
    if isinstance(reply, Mapping):
        content = reply.get("content")
        if isinstance(content, str):
            text = content
        text = text or str(reply.get("text") or "")
        tokens = _input_tokens_from_usage(reply.get("usage"))
    else:
        tokens = 0
    rewritten = " ".join(text.split())
    return (rewritten or query), tokens


def _suggest_skills(query: str, *, first_only: bool) -> dict[str, Any]:
    from openai4s.config import get_config
    from openai4s.host.skills import SkillService

    service = SkillService(get_config())
    suggest = getattr(service, "suggest", None)
    if not callable(suggest):
        raise RuntimeError(
            "SkillService.suggest is not available; W2-A has not landed in this tree"
        )
    spec: dict[str, Any] = {"query": query}
    if first_only:
        spec["first_request_only"] = True
        spec["max_requests"] = 1
        spec["stage"] = "fanout"
    return suggest(spec)


def _names_from_suggest(payload: object) -> list[str]:
    if not isinstance(payload, Mapping):
        return []
    suggestions = payload.get("semantic_suggestions")
    if not isinstance(suggestions, list):
        return []
    names: list[str] = []
    for item in suggestions:
        if isinstance(item, Mapping):
            name = item.get("name")
        elif isinstance(item, str):
            name = item
        else:
            name = None
        if isinstance(name, str) and name:
            names.append(name)
        if len(names) >= TOP_K:
            break
    return names


def run_system(
    system: str,
    case: Mapping[str, Any],
    *,
    terms: Sequence[Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    """Run one system on one case. Never raises into the scorer."""

    query = str(case["query"])
    lang = str(case["lang"])
    started = time.perf_counter()
    pred: list[str] = []
    requests = 0
    tokens = 0
    error: str | None = None
    try:
        if system == "B0":
            pred = lexical_top3(query)
        elif system == "B2":
            glossary = terms if terms is not None else load_glossary()
            expanded = expand_query_b2(query, lang, glossary)
            pred = lexical_top3(expanded)
        elif system == "B1":
            rewritten, tokens = _rewrite_query_b1(query)
            pred = lexical_top3(rewritten)
            requests = 1
        elif system in ("J1", "J2"):
            payload = _suggest_skills(query, first_only=(system == "J1"))
            pred = _names_from_suggest(payload)
            if isinstance(payload, Mapping):
                requests = int(payload.get("requests") or (1 if pred or payload else 0))
                tokens = _input_tokens_from_usage(payload.get("usage"))
                if payload.get("latency_ms") is not None:
                    try:
                        latency_override = float(payload["latency_ms"])
                    except (TypeError, ValueError):
                        latency_override = None
                else:
                    latency_override = None
            else:
                latency_override = None
            latency_ms = (
                latency_override
                if latency_override is not None
                else (time.perf_counter() - started) * 1000.0
            )
            return {
                "pred": pred[:TOP_K],
                "requests": requests,
                "latency_ms": latency_ms,
                "input_tokens": tokens,
                "error": None,
            }
        else:
            raise ValueError(f"unknown system {system!r}")
    except Exception as exc:  # noqa: BLE001 - one case must not abort the run
        error = f"{type(exc).__name__}: {exc}"
        pred = []
    latency_ms = (time.perf_counter() - started) * 1000.0
    return {
        "pred": pred[:TOP_K],
        "requests": requests,
        "latency_ms": latency_ms,
        "input_tokens": tokens,
        "error": error,
    }


def evaluate_system(
    system: str,
    cases: Sequence[Mapping[str, Any]],
    *,
    terms: Sequence[Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    predictions: dict[str, list[str]] = {}
    extra: dict[str, dict[str, Any]] = {}
    for case in cases:
        outcome = run_system(system, case, terms=terms)
        predictions[case["id"]] = list(outcome["pred"])
        extra[case["id"]] = {
            "requests": outcome["requests"],
            "latency_ms": outcome["latency_ms"],
            "input_tokens": outcome["input_tokens"],
            "error": outcome["error"],
        }
    report = score_predictions(cases, predictions, extra=extra)
    report["system"] = system
    return report


def fake_endpoint_active() -> bool:
    value = os.environ.get("OPENAI4S_JUDGMENT_FAKE_ENDPOINT", "").strip()
    return bool(value)


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


def skills_template_version() -> str | None:
    try:
        from openai4s.judgment.templates.skills import TEMPLATE_VERSION
    except ImportError:
        return None
    return str(TEMPLATE_VERSION)


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


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Skill suggestion evaluation",
        "",
        f"- git HEAD: `{report.get('git_head') or 'unknown'}`",
        f"- split: `{report.get('split')}`",
        f"- n: {report.get('n')}",
        f"- systems: {', '.join(report.get('systems_run') or [])}",
        f"- fake endpoint: {'yes — scores are not meaningful' if report.get('fake_endpoint') else 'no'}",
        f"- input token price: ${INPUT_TOKEN_USD_PER_MILLION}/million",
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
    for system in report.get("systems_run") or []:
        block = report["results"][system]
        overall = block["overall"]
        lines.extend(
            [
                f"## {system}",
                "",
                "### overall",
                "",
                f"- top-1 accuracy: {_fmt_ratio(overall['top1_accuracy'])}",
                f"- top-3 recall: {_fmt_ratio(overall['top3_recall'])}",
                f"- error recommendation rate: {_fmt_ratio(overall['error_recommendation_rate'])}",
                f"- unnecessary recommendation rate: {_fmt_ratio(overall['unnecessary_recommendation_rate'])}",
                f"- abstention rate: {_fmt_ratio(overall['abstention_rate'])}",
                (
                    f"- requests/query mean {overall['requests_per_query']['mean']:.3f}"
                    f" p50 {overall['requests_per_query']['p50']:.3f}"
                    f" p95 {overall['requests_per_query']['p95']:.3f}"
                    if overall["requests_per_query"]["mean"] is not None
                    else "- requests/query: n/a"
                ),
                (
                    f"- latency_ms p50 {overall['latency_ms']['p50']:.1f}"
                    f" p95 {overall['latency_ms']['p95']:.1f}"
                    if overall["latency_ms"]["p50"] is not None
                    else "- latency_ms: n/a"
                ),
                f"- input tokens: {overall['input_tokens']}",
                f"- cost USD: {overall['cost_usd']:.8f}",
                "",
                "### by language",
                "",
            ]
        )
        for lang in LANGS:
            slice_ = block["by_lang"][lang]
            lines.append(
                f"- **{lang}** (n={slice_['n']}): "
                f"top-1 {_fmt_ratio(slice_['top1_accuracy'])}; "
                f"top-3 {_fmt_ratio(slice_['top3_recall'])}; "
                f"error {_fmt_ratio(slice_['error_recommendation_rate'])}; "
                f"unnecessary {_fmt_ratio(slice_['unnecessary_recommendation_rate'])}; "
                f"abstain {_fmt_ratio(slice_['abstention_rate'])}"
            )
        lines.extend(["", "### by category", ""])
        for category in CATEGORIES:
            slice_ = block["by_category"][category]
            lines.append(
                f"- **{category}** (n={slice_['n']}): "
                f"top-1 {_fmt_ratio(slice_['top1_accuracy'])}; "
                f"top-3 {_fmt_ratio(slice_['top3_recall'])}; "
                f"error {_fmt_ratio(slice_['error_recommendation_rate'])}; "
                f"unnecessary {_fmt_ratio(slice_['unnecessary_recommendation_rate'])}; "
                f"abstain {_fmt_ratio(slice_['abstention_rate'])}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_systems(raw: str) -> list[str]:
    parts = [item.strip() for item in raw.split(",") if item.strip()]
    if not parts:
        raise ValueError("at least one system is required")
    unknown = [item for item in parts if item not in SYSTEMS]
    if unknown:
        raise ValueError(f"unknown systems {unknown}; expected one of {list(SYSTEMS)}")
    # Preserve order, drop duplicates.
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
            "(B1 uses the configured main model; J1/J2 call SkillService.suggest)"
        )
    if split == "test":
        if not frozen_thresholds or not str(frozen_thresholds).strip():
            raise SystemExit(
                "--split test requires --frozen-thresholds <TEMPLATE_VERSION> "
                "matching openai4s.judgment.templates.skills"
            )
        version = skills_template_version()
        if version is None:
            raise SystemExit(
                "--split test: TEMPLATE_VERSION is not importable "
                "(openai4s.judgment.templates.skills is missing)"
            )
        if str(frozen_thresholds).strip() != version:
            raise SystemExit(
                f"--frozen-thresholds {frozen_thresholds!r} does not match "
                f"TEMPLATE_VERSION {version!r}"
            )
    selected = [case for case in cases if case["split"] == split]
    if not selected:
        raise SystemExit(f"no cases for split {split!r}")
    terms = load_glossary() if "B2" in systems else None
    results = {name: evaluate_system(name, selected, terms=terms) for name in systems}
    fake = fake_endpoint_active() and any(name in ("J1", "J2") for name in systems)
    return {
        "schema_version": SCHEMA_VERSION,
        "git_head": git_head(),
        "split": split,
        "n": len(selected),
        "systems_run": list(systems),
        "fake_endpoint": fake,
        "scores_are_not_meaningful": fake,
        "input_token_usd_per_million": INPUT_TOKEN_USD_PER_MILLION,
        "bootstrap": {
            "resamples": BOOTSTRAP_RESAMPLES,
            "seed": BOOTSTRAP_SEED,
            "method": "percentile",
        },
        "split_seed": SPLIT_SEED,
        "frozen_thresholds": frozen_thresholds if split == "test" else None,
        "results": results,
    }


def _write_reports(report: Mapping[str, Any], out: str | None) -> tuple[Path, Path]:
    stem = f"judgment_skills-{report['split']}-{'-'.join(report['systems_run'])}"
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
        prog="python -m harness.evals.judgment_skills",
        description="Score Skill suggestion systems on the frozen eval set.",
    )
    parser.add_argument("--split", choices=SPLITS, required=True)
    parser.add_argument(
        "--systems",
        required=True,
        help="Comma-separated subset of B0,B1,B2,J1,J2",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Allow B1 (live_llm rewrite) and J1/J2 (SkillService.suggest)",
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
