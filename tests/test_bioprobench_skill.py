"""Regression contracts for the bundled BioProBench Skill sidecar.

Two defects here were silent: they produced plausible-looking metrics under a
``"success"`` status instead of raising, so only a pinned expected number
catches a regression.

* **ERR label encoding** — ground-truth ``is_correct`` was compared with the
  identity operator (``g is False``), so labels encoded as ``0``/``1`` or as
  ``"true"``/``"false"`` matched nothing and precision/recall/F1 collapsed to
  exactly ``0`` while accuracy stayed plausible.
* **PQA confidence parsing** — without an ``&`` separator the last whitespace
  token was taken as the confidence unconditionally, so a bare correct answer
  such as ``0.3`` was consumed as its own confidence and scored *wrong* with a
  fabricated confidence of ``0``, uncounted in ``Failed_Rate``.

Fixtures are pure stdlib. The module scope of ``kernel.py`` must stay
importable without the science stack, which is itself asserted below; only the
PQA aggregation needs numpy/scikit-learn and is skipped when they are absent.
"""

from __future__ import annotations

import importlib
import inspect
import json
import sys

import pytest

from openai4s.config import get_config

pytestmark = pytest.mark.skills


@pytest.fixture(scope="module", autouse=True)
def _skills_on_path():
    path = str(get_config().skills_dir)
    sys.path.insert(0, path)
    yield
    sys.path.remove(path)


@pytest.fixture(scope="module")
def kernel():
    return importlib.import_module("bioprobench.kernel")


def _write(tmp_path, name, records):
    path = tmp_path / name
    path.write_text(json.dumps(records), encoding="utf-8")
    return str(path)


def _answer(value):
    return f"[ANSWER_START]{value}[ANSWER_END]"


# --- module scope stays importable without the science stack ----------------


def test_kernel_module_scope_needs_no_third_party_imports(kernel):
    """ORD/ERR/REA-ERR must work in a kernel with none of the heavy deps."""
    for task in ("ORD", "ERR", "REA-ERR"):
        kernel._require(task)  # must not raise


# --- B2: ERR ground-truth label encodings -----------------------------------

# Predictions F, F, T, F, T against labels F, T, T, F, F.
# The positive class is "this step is wrong" (label False), so
# TP=2, FP=1, FN=1 -> precision = recall = f1 = 2/3, accuracy = 3/5.
_ERR_PREDICTIONS = [False, False, True, False, True]
_ERR_LABELS = [False, True, True, False, False]
_ERR_EXPECTED = {
    "accuracy": 0.6,
    "precision": 2 / 3,
    "recall": 2 / 3,
    "f1": 2 / 3,
    "failed_rate": 0.0,
}


@pytest.mark.parametrize(
    "encode",
    [
        pytest.param(lambda b: b, id="json-bool"),
        pytest.param(lambda b: int(b), id="int-0-1"),
        pytest.param(lambda b: "true" if b else "false", id="lowercase-string"),
        pytest.param(lambda b: "True" if b else "False", id="capitalized-string"),
        pytest.param(lambda b: "yes" if b else "no", id="yes-no-string"),
    ],
)
def test_err_scores_identically_across_label_encodings(kernel, tmp_path, encode):
    """Every accepted encoding of `is_correct` must yield the same metrics."""
    records = [
        {"generated_response": _answer(pred), "is_correct": encode(label)}
        for pred, label in zip(_ERR_PREDICTIONS, _ERR_LABELS)
    ]
    path = _write(tmp_path, "err.json", records)

    metrics = kernel.evaluate_correction_task(path)

    assert metrics == pytest.approx(_ERR_EXPECTED)
    # The specific regression: these collapsed to exactly 0 for non-bool labels.
    assert metrics["precision"] > 0
    assert metrics["recall"] > 0
    assert metrics["f1"] > 0


def test_err_unrecognized_label_is_counted_as_failed_not_scored(kernel, tmp_path):
    """Bad data must reach `failed_rate`, not be scored as a confident miss."""
    records = [{"generated_response": _answer(False), "is_correct": "maybe"}]
    path = _write(tmp_path, "err_bad.json", records)

    metrics = kernel.evaluate_correction_task(path)

    assert metrics["failed_rate"] == 1.0
    assert metrics["accuracy"] == 0
    assert kernel._result_status(metrics) == "failed"


def test_err_missing_label_does_not_shift_later_predictions(kernel, tmp_path):
    """A half-appended item would mis-pair every later prediction."""
    records = [
        {"generated_response": _answer(True), "is_correct": True},
        {"generated_response": _answer(False)},  # no ground truth at all
        {"generated_response": _answer(False), "is_correct": False},
        {"generated_response": _answer(True), "is_correct": True},
    ]
    path = _write(tmp_path, "err_missing.json", records)

    metrics = kernel.evaluate_correction_task(path)

    # Every *labelled* item agrees with its prediction, so accuracy is 1.0.
    assert metrics["accuracy"] == 1.0
    assert metrics["failed_rate"] == 0.25
    assert kernel._result_status(metrics) == "partial"


# --- an input with nothing to score must not report "success" ---------------


@pytest.mark.parametrize("task", ["PQA", "ORD", "ERR", "REA-ERR", "GEN"])
def test_empty_record_list_is_an_error_not_a_successful_zero(kernel, tmp_path, task):
    """Every evaluator guards its divisions with `if total else 0`, so an empty
    list would otherwise score as all-zero metrics at Failed_Rate 0 — i.e.
    `"status": "success"` for a run that measured nothing."""
    path = _write(tmp_path, "empty.json", [])

    result = kernel.run_bioprobench_eval(task, path)

    assert "error" in result
    assert "status" not in result
    assert "no response records" in result["error"]


def test_non_list_json_is_an_error(kernel, tmp_path):
    path = _write(tmp_path, "obj.json", {"records": []})

    result = kernel.run_bioprobench_eval("ORD", path)

    assert "error" in result
    assert "must be a JSON list" in result["error"]


def test_malformed_json_names_the_decode_failure(kernel, tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{bad", encoding="utf-8")

    result = kernel.run_bioprobench_eval("ORD", str(path))

    assert "error" in result
    assert "JSONDecodeError" in result["error"]


# --- B6: PQA answer/confidence split ----------------------------------------


@pytest.mark.parametrize(
    "content, expected",
    [
        ("0.3 & 95", ("0.3", 95)),
        ("sodium chloride & 80%", ("sodium chloride", 80)),
        ("0.3 95", ("0.3", 95)),
        ("0.3 95%", ("0.3", 95)),
        ("sodium chloride 100", ("sodium chloride", 100)),
    ],
)
def test_pqa_confidence_split_accepts_real_confidences(kernel, content, expected):
    assert kernel._split_answer_and_confidence(content) == expected


@pytest.mark.parametrize(
    "content",
    [
        "0.3",  # a bare correct answer, previously eaten as its own confidence
        "sodium chloride",  # trailing token is not a confidence
        "  ",  # nothing at all
        "0.3 & ",  # '&' form with no number after it
    ],
)
def test_pqa_confidence_split_rejects_unparseable_content(kernel, content):
    with pytest.raises(ValueError):
        kernel._split_answer_and_confidence(content)


def test_pqa_bare_answer_never_yields_a_fabricated_confidence(kernel):
    """`0.3` must not silently become answer='' with confidence 0."""
    with pytest.raises(ValueError):
        kernel._split_answer_and_confidence("0.3")


def test_pqa_end_to_end_counts_unparseable_answers_as_failed(kernel, tmp_path):
    pytest.importorskip("numpy")
    pytest.importorskip("sklearn")

    # The response is *correct* — it just carries no confidence.
    records = [{"generated_response": _answer("0.3"), "answer": "0.3"}]
    path = _write(tmp_path, "pqa_bare.json", records)

    result = kernel.run_bioprobench_eval("PQA", path)

    assert result["status"] == "failed"
    assert result["metrics"]["Failed_Rate"] == 1.0
    assert result["metrics"]["Brier_Score"] is None


def test_pqa_end_to_end_scores_a_well_formed_file(kernel, tmp_path):
    pytest.importorskip("numpy")
    pytest.importorskip("sklearn")

    records = [
        {"generated_response": _answer("0.3 & 95"), "answer": "0.3"},
        {"generated_response": _answer("A & 10"), "answer": "B"},
    ]
    path = _write(tmp_path, "pqa.json", records)

    result = kernel.run_bioprobench_eval("PQA", path)

    assert result["status"] == "success"
    assert result["metrics"]["Accuracy"] == pytest.approx(0.5)
    assert result["metrics"]["Failed_Rate"] == 0.0
    # Confidences 0.95 (right) and 0.10 (wrong) -> ((1-.95)^2 + (.10)^2) / 2.
    assert result["metrics"]["Brier_Score"] == pytest.approx(0.00625)


# `prompt_format` asks the model for `choice & confidence`, so the `&` branch is
# the prompted path and the whitespace branch is the fallback. An earlier fix
# hardened only the fallback, leaving the common path fabricating a confidence
# from the first digit run.
@pytest.mark.parametrize("token", ["95", "100", "0", "7", "95%"])
def test_pqa_both_separators_accept_the_same_confidences(kernel, token):
    assert kernel._split_answer_and_confidence(
        f"0.3 & {token}"
    ) == kernel._split_answer_and_confidence(f"0.3 {token}")


@pytest.mark.parametrize("token", ["0.95", "0.3", "1.00", "95.5", "abc", "101"])
def test_pqa_ampersand_branch_rejects_what_the_fallback_rejects(kernel, token):
    """A probability like 0.95 must not become confidence 0 via the first digit run."""
    with pytest.raises(ValueError):
        kernel._split_answer_and_confidence(f"0.3 & {token}")
    with pytest.raises(ValueError):
        kernel._split_answer_and_confidence(f"0.3 {token}")


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("Tris & EDTA & 90", ("Tris & EDTA", 90)),
        ("R&D buffer & 90", ("R&D buffer", 90)),
        ("wash & spin & 5", ("wash & spin", 5)),
    ],
)
def test_pqa_answer_may_itself_contain_an_ampersand(kernel, content, expected):
    """Only the last field is the confidence; the answer must match a choice exactly."""
    assert kernel._split_answer_and_confidence(content) == expected


def test_pqa_probability_confidence_reaches_failed_rate_not_a_worst_case_brier(
    kernel, tmp_path
):
    pytest.importorskip("numpy")
    pytest.importorskip("sklearn")

    # Every answer is correct; only the confidence format deviates. Scoring this
    # as success yielded Brier 1.0 — the worst attainable — with Failed_Rate 0.
    records = [{"generated_response": _answer("0.3 & 0.95"), "answer": "0.3"}]
    path = _write(tmp_path, "pqa.json", records)

    result = kernel.run_bioprobench_eval("PQA", path)

    assert result["status"] == "failed"
    assert result["metrics"]["Failed_Rate"] == 1.0


def test_trigger_batch_inference_exposes_the_endpoint(kernel):
    """A non-OpenAI key must not be transmittable only to api.openai.com."""
    assert "base_url" in inspect.signature(kernel.trigger_batch_inference).parameters
