"""Offline contracts for the literature claim-check evaluation (W3-B / plan §7)."""

from __future__ import annotations

import pytest

from harness.evals import judgment_literature as ev

CASES_PATH = ev.CASES_PATH
LOCK_PATH = ev.LOCK_PATH


@pytest.fixture(scope="module")
def cases() -> list[dict]:
    return ev.load_cases(CASES_PATH)


def test_dataset_schema_and_counts(cases: list[dict]) -> None:
    assert 280 <= len(cases) <= 320
    zh = [case for case in cases if case["lang"] == "zh"]
    en = [case for case in cases if case["lang"] == "en"]
    assert 0.25 <= len(zh) / len(cases) <= 0.40
    assert len(en) + len(zh) == len(cases)
    ids = [case["id"] for case in cases]
    assert len(ids) == len(set(ids))
    for case in cases:
        assert set(case) == set(ev.CASE_FIELDS)
        assert case["lang"] in ev.LANGS
        assert case["perturbation"] in ev.PERTURBATIONS
        assert case["split"] in ev.SPLITS
        assert case["gold_status"] in ev.GOLD_STATUSES
        assert case["gold_relation"] in ev.GOLD_RELATIONS
        assert isinstance(case["claim"], str) and case["claim"].strip()
        assert isinstance(case["quote"], str)
        source = case["source"]
        assert set(source) == set(ev.SOURCE_FIELDS)
        assert source["license"].strip()
        assert ev._LICENSE_RE.search(source["license"])
        assert 1 <= len(source["text"]) <= ev.MAX_SOURCE_CHARS
        assert ev._DOI_RE.match(source["doi"])


def test_every_lang_perturbation_split_stratum_is_nonempty(cases: list[dict]) -> None:
    counts = ev.stratum_counts(cases)
    for lang in ev.LANGS:
        for perturbation in ev.PERTURBATIONS:
            for split in ev.SPLITS:
                key = f"{lang}:{perturbation}:{split}"
                assert counts.get(key, 0) >= 1, key


def test_every_case_records_a_creative_commons_license(cases: list[dict]) -> None:
    licenses = {case["source"]["license"] for case in cases}
    assert licenses
    assert all(ev._LICENSE_RE.search(item) for item in licenses)


def test_lock_file_matches_the_frozen_test_split(cases: list[dict]) -> None:
    lock = ev.load_lock(LOCK_PATH)
    assert lock["algorithm"] == "sha256"
    assert lock["sha256"] == ev.test_set_sha256(cases)
    assert lock["n_test"] == sum(1 for case in cases if case["split"] == "test")
    mutated = []
    flipped = False
    for case in cases:
        row = dict(case)
        row["source"] = dict(case["source"])
        if row["split"] == "test" and not flipped:
            row["claim"] = row["claim"] + " [mutated]"
            flipped = True
        mutated.append(row)
    assert flipped
    assert ev.test_set_sha256(mutated) != lock["sha256"]


def test_locate_quote_exact_fuzzy_and_missing() -> None:
    source = 'The authors reported a "30% reduction" in 2019—see section 2.'
    exact = ev.locate_quote(
        "The authors reported a “30% reduction” in 2019—see section 2.", source
    )
    assert exact is not None
    assert exact["match"] == "exact"
    missing = ev.locate_quote(
        "A trial of 12487 adults cut 30-day mortality from 18.2% to 2.1%.",
        source,
    )
    assert missing is None
    fuzzy = ev.locate_quote(
        "The authors reported a 30% reduction in 2019 see section 2.",
        source,
    )
    assert fuzzy is not None
    assert str(fuzzy["match"]).startswith("fuzzy:")


def test_quantities_mismatch_number_and_unit() -> None:
    section = "Incidence fell from 12.5 mg/L to 4 mg/L in adults."
    assert ev.quantities_mismatch(
        "Incidence fell from 125 mg/L to 4 mg/L in adults.", section
    )
    assert ev.quantities_mismatch(
        "Incidence fell from 12.5 g/L to 4 mg/L in adults.", section
    )
    assert ev.quantities_mismatch(
        "Incidence fell from 12.5 mg to 4 mg/L in adults.", section
    )
    assert not ev.quantities_mismatch(
        "Incidence fell from 12.5 mg/L to 4 mg/L in adults.", section
    )
    assert not ev.quantities_mismatch("the finding was replicated", section)


def test_case_indicators_edges() -> None:
    gold_status = "verified"
    gold_relation = "supports"
    abstain = ev.case_indicators(
        gold_status,
        gold_relation,
        {"status": "no_judgment", "relation": None, "confidence": None},
        claim_conf=0.8,
    )
    assert abstain["status_accuracy"] is None
    assert abstain["relation_accuracy"] is None
    assert abstain["high_conf_error"] is None
    assert abstain["needs_review"] == 0

    hit = ev.case_indicators(
        "verified",
        "supports",
        {"status": "verified", "relation": "supports", "confidence": 0.9},
        claim_conf=0.8,
    )
    assert hit["status_accuracy"] == 1
    assert hit["relation_accuracy"] == 1
    assert hit["high_conf_error"] == 0
    assert hit["support_recall"] == 1

    miss = ev.case_indicators(
        "contradicted",
        "contradicts",
        {"status": "verified", "relation": "supports", "confidence": 0.95},
        claim_conf=0.8,
    )
    assert miss["status_accuracy"] == 0
    assert miss["relation_accuracy"] == 0
    assert miss["high_conf_error"] == 1
    assert miss["contradiction_recall"] == 0

    low_conf = ev.case_indicators(
        "contradicted",
        "contradicts",
        {"status": "verified", "relation": "supports", "confidence": 0.2},
        claim_conf=0.8,
    )
    assert low_conf["high_conf_error"] is None

    review = ev.case_indicators(
        "not_found_needs_review",
        "insufficient",
        {"status": "not_found", "relation": None, "confidence": 1.0},
        claim_conf=0.8,
    )
    assert review["needs_review"] == 1
    assert review["status_accuracy"] == 1


def test_score_predictions_and_bootstrap_bounds() -> None:
    cases = [
        {
            "id": "c1",
            "lang": "en",
            "perturbation": "faithful",
            "claim": "a",
            "quote": "a",
            "source": {"doi": "10.0000/aaa", "license": "CC BY", "text": "a"},
            "gold_status": "verified",
            "gold_relation": "supports",
            "split": "dev",
        },
        {
            "id": "c2",
            "lang": "zh",
            "perturbation": "negation",
            "claim": "b",
            "quote": "b",
            "source": {"doi": "10.0000/bbb", "license": "CC BY", "text": "b"},
            "gold_status": "contradicted",
            "gold_relation": "contradicts",
            "split": "dev",
        },
        {
            "id": "c3",
            "lang": "en",
            "perturbation": "fabricated_quote",
            "claim": "c",
            "quote": "c",
            "source": {"doi": "10.0000/ccc", "license": "CC0", "text": "c"},
            "gold_status": "not_found_needs_review",
            "gold_relation": "insufficient",
            "split": "dev",
        },
    ]
    report = ev.score_predictions(
        cases,
        {
            "c1": {
                "status": "verified",
                "relation": "supports",
                "confidence": 0.9,
                "latency_ms": 10,
                "input_tokens": 0,
            },
            "c2": {
                "status": "verified",
                "relation": "supports",
                "confidence": 0.9,
                "latency_ms": 20,
                "input_tokens": 100,
            },
            "c3": {
                "status": "not_found_needs_review",
                "relation": None,
                "confidence": 1.0,
                "latency_ms": 30,
                "input_tokens": 0,
            },
        },
        claim_conf=0.8,
    )
    overall = report["overall"]
    assert overall["n"] == 3
    assert overall["status_accuracy"]["n"] == 3
    assert overall["status_accuracy"]["value"] == pytest.approx(2 / 3)
    assert overall["relation_accuracy"]["value"] == pytest.approx(0.5)
    assert overall["high_conf_error_rate"]["value"] == pytest.approx(0.5)
    assert overall["support_recall"]["value"] == 1.0
    assert overall["contradiction_recall"]["value"] == 0.0
    assert overall["needs_review_rate"]["value"] == pytest.approx(1 / 3)
    assert overall["input_tokens"] == 100
    assert overall["cost_usd"] == pytest.approx(
        100 / 1_000_000 * ev.INPUT_TOKEN_USD_PER_MILLION
    )
    lo, hi = overall["status_accuracy"]["ci95"]
    assert lo is not None and hi is not None
    assert lo <= overall["status_accuracy"]["value"] <= hi
    assert report["by_lang"]["zh"]["n"] == 1
    assert (
        report["by_perturbation"]["fabricated_quote"]["needs_review_rate"]["value"]
        == 1.0
    )


def test_bootstrap_empty_and_degenerate() -> None:
    assert ev.bootstrap_mean_ci([]) == (None, None)
    assert ev.bootstrap_mean_ci([1.0]) == (1.0, 1.0)
    lo, hi = ev.bootstrap_mean_ci([0.0, 0.0, 0.0])
    assert lo == 0.0 and hi == 0.0
    lo, hi = ev.bootstrap_mean_ci([0.0, 1.0] * 10)
    assert lo is not None and hi is not None
    assert 0.0 <= lo <= 0.5 <= hi <= 1.0


def test_split_test_refuses_without_matching_frozen_thresholds(
    cases: list[dict],
) -> None:
    with pytest.raises(SystemExit, match="frozen-thresholds"):
        ev.build_report(
            split="test",
            systems=["CODE"],
            cases=cases,
            live=False,
            frozen_thresholds=None,
        )
    with pytest.raises(SystemExit, match="TEMPLATE_VERSION|frozen-thresholds"):
        ev.build_report(
            split="test",
            systems=["CODE"],
            cases=cases,
            live=False,
            frozen_thresholds="not-a-real-template-version",
        )


def test_live_systems_require_the_live_flag(cases: list[dict]) -> None:
    with pytest.raises(SystemExit, match="--live"):
        ev.build_report(
            split="dev",
            systems=["LLM"],
            cases=cases[:1],
            live=False,
            frozen_thresholds=None,
        )
    with pytest.raises(SystemExit, match="--live"):
        ev.build_report(
            split="dev",
            systems=["J"],
            cases=cases[:1],
            live=False,
            frozen_thresholds=None,
        )


def test_parse_systems_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown"):
        ev.parse_systems("CODE,X")
    assert ev.parse_systems("CODE,LLM,CODE") == ["CODE", "LLM"]


def test_assign_splits_is_deterministic() -> None:
    raw = [
        {
            "id": f"{lang}-{pert}-{i:02d}",
            "lang": lang,
            "perturbation": pert,
            "claim": "q",
            "quote": "q",
            "source": {"doi": "10.0000/x", "license": "CC BY", "text": "q"},
            "gold_status": "verified",
            "gold_relation": "supports",
        }
        for lang in ev.LANGS
        for pert in ev.PERTURBATIONS
        for i in range(4)
    ]
    first = ev.assign_splits(raw, seed=ev.SPLIT_SEED)
    second = ev.assign_splits(raw, seed=ev.SPLIT_SEED)
    assert [case["split"] for case in first] == [case["split"] for case in second]
    counts = ev.stratum_counts(first)
    for lang in ev.LANGS:
        for pert in ev.PERTURBATIONS:
            assert counts[f"{lang}:{pert}:dev"] == 2
            assert counts[f"{lang}:{pert}:test"] == 2


def test_eval_code_path_detects_constructed_numeric_and_fake_cases(
    cases: list[dict],
) -> None:
    numeric = [
        case for case in cases if case["perturbation"] in ev.NUMERIC_PERTURBATIONS
    ]
    assert numeric
    misses = [
        case["id"]
        for case in numeric
        if ev.run_code_path(case)["status"] != "numeric_mismatch"
    ]
    assert misses == []
    fakes = [case for case in cases if case["perturbation"] == "fabricated_quote"]
    assert fakes
    fake_misses = [
        case["id"]
        for case in fakes
        if ev.run_code_path(case)["status"] not in ev.REVIEW_STATUSES
    ]
    assert fake_misses == []


def test_w3a_numeric_compare_detects_numeric_perturbations(cases: list[dict]) -> None:
    compare = ev.w3a_numeric_compare()
    if compare is None:
        pytest.skip(
            "W3-A literature helper is not merged in this tree; "
            "skills/literature-review/kernel.py has no quantity-compare function yet"
        )
    numeric = [
        case for case in cases if case["perturbation"] in ev.NUMERIC_PERTURBATIONS
    ]
    misses = []
    for case in numeric:
        section = case["source"]["text"]
        quote = case.get("quote") or ""
        located = ev.locate_quote(quote, section) if quote else None
        if located is not None:
            start, end = located["span"]
            section = ev.normalize_text(section)[start:end] or section
        try:
            flagged = bool(compare(case["claim"], section))
        except TypeError:
            flagged = bool(compare(case["claim"], section, window=400))
        if not flagged:
            misses.append(case["id"])
    assert misses == []


def test_code_runs_offline_on_the_dev_split(cases: list[dict]) -> None:
    dev = [case for case in cases if case["split"] == "dev"]
    assert dev
    report = ev.build_report(
        split="dev",
        systems=["CODE"],
        cases=cases,
        live=False,
        frozen_thresholds=None,
    )
    assert report["n"] == len(dev)
    assert report["systems_run"] == ["CODE"]
    numeric = report["dataset"]["code_path_numeric_detection"]
    assert numeric["n"] > 0
    assert numeric["detected"] == numeric["n"]
    errors = [
        row["error"] for row in report["results"]["CODE"]["cases"] if row.get("error")
    ]
    assert errors == []
    assert report["results"]["CODE"]["overall"]["input_tokens"] == 0
    assert report["results"]["CODE"]["overall"]["cost_usd"] == 0.0
    zh = report["results"]["CODE"]["by_lang"]["zh"]
    en = report["results"]["CODE"]["by_lang"]["en"]
    assert zh["n"] + en["n"] == len(dev)
