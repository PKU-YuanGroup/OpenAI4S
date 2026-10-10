"""Execute the survey recipe's actual scoring example on synthetic local inputs."""

from __future__ import annotations

import ast
import csv
import hashlib
import json
import re
from pathlib import Path

import pytest

RECIPE_PATH = (
    Path(__file__).resolve().parents[1] / "skills/is-survey-measurement/SKILL.md"
)
HEADER = "q1,q2,q3,q4,q5,q6\n"
# Deliberately tiny synthetic scoring fixtures, never a sample for CFA inference.
VALID_ROWS = "1,2,3,4,5,1\n5,4,2,1,3,5\n"


@pytest.fixture(scope="module")
def scoring_recipe():
    blocks = re.findall(r"```python\n(.*?)\n```", RECIPE_PATH.read_text(), re.S)
    scoring_blocks = [
        block
        for block in blocks
        if "csv.DictReader" in block and "scored-items.csv" in block
    ]
    assert len(scoring_blocks) == 1, "Expected one runnable survey scoring example"
    return scoring_blocks[0]


def _execute(scoring_recipe, *, missing_policy=None):
    tree = ast.parse(scoring_recipe, filename=str(RECIPE_PATH))
    if missing_policy is not None:
        # Make the one documented researcher choice, leaving all recipe logic intact.
        choices = [
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "missing_policy"
                for target in node.targets
            )
        ]
        assert len(choices) == 1
        choices[0].value = ast.Constant(value=missing_policy)
        ast.fix_missing_locations(tree)
    exec(compile(tree, str(RECIPE_PATH), "exec"), {})


def _write_source(tmp_path, monkeypatch, content):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "survey.csv"
    source.write_bytes(content.encode("utf-8"))
    return source


def _read_output(tmp_path):
    output = tmp_path / "measurement-analysis"
    audit = json.loads((output / "scoring-audit.json").read_text())
    with (output / "scored-items.csv").open(newline="") as stream:
        scored = list(csv.DictReader(stream))
    return audit, scored


def test_scoring_reverses_only_declared_items_and_preserves_source(
    scoring_recipe, tmp_path, monkeypatch
):
    original = HEADER + VALID_ROWS
    source = _write_source(tmp_path, monkeypatch, original)
    _execute(scoring_recipe)

    assert source.read_bytes() == original.encode("utf-8")
    audit, scored = _read_output(tmp_path)
    assert scored == [
        {
            "source_row": "1",
            "q1": "1",
            "q2": "2",
            "q3": "3",
            "q4": "4",
            "q5": "5",
            "q6": "5",
        },
        {
            "source_row": "2",
            "q1": "5",
            "q2": "4",
            "q3": "4",
            "q4": "1",
            "q5": "3",
            "q6": "1",
        },
    ]
    assert audit["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert audit["source_rows"] == audit["complete_rows"] == 2
    assert audit["status"] == "ready_for_review"
    assert audit["incomplete_source_rows"] == []
    assert not any(audit["item_missing_counts"].values())
    assert audit["raw_category_counts"]["q3"] == {"3": 1, "2": 1}
    assert audit["item_specification"]["q3"]["reverse"] is True
    assert audit["item_specification"]["q2"]["reverse"] is False


def test_missing_items_block_by_default_but_leave_an_audit(
    scoring_recipe, tmp_path, monkeypatch
):
    original = HEADER + VALID_ROWS + "1,,3,4,5,1\n"
    source = _write_source(tmp_path, monkeypatch, original)
    with pytest.raises(ValueError, match="Missing items: audit saved"):
        _execute(scoring_recipe)

    assert source.read_bytes() == original.encode("utf-8")
    output = tmp_path / "measurement-analysis"
    assert not (output / "scored-items.csv").exists()
    audit = json.loads((output / "scoring-audit.json").read_text())
    assert audit["status"] == "blocked_missing_items"
    assert audit["missing_policy"] == "error"
    assert audit["incomplete_source_rows"] == [3]
    assert audit["item_missing_counts"]["q2"] == 1
    assert audit["source_rows"] == 3
    assert audit["complete_rows"] == 2


def test_explicit_complete_case_records_loss_without_filling_items(
    scoring_recipe, tmp_path, monkeypatch
):
    original = HEADER + VALID_ROWS + "1,NA,3,4,5,1\n"
    source = _write_source(tmp_path, monkeypatch, original)
    _execute(scoring_recipe, missing_policy="complete_case")

    assert source.read_bytes() == original.encode("utf-8")
    audit, scored = _read_output(tmp_path)
    assert audit["missing_policy"] == "complete_case"
    assert audit["status"] == "ready_for_review"
    assert audit["incomplete_source_rows"] == [3]
    assert audit["item_missing_counts"]["q2"] == 1
    assert audit["source_rows"] == 3
    assert audit["complete_rows"] == 2
    assert [row["source_row"] for row in scored] == ["1", "2"]


@pytest.mark.parametrize(
    "invalid_code,expected_error",
    [
        ("6", "Out-of-range/noninteger code"),
        ("NaN", "Out-of-range/noninteger code"),
        ("1.5", "Out-of-range/noninteger code"),
        ("unrecognized", "Unrecognized code"),
    ],
)
def test_invalid_item_codes_are_not_coerced_or_scored(
    scoring_recipe, tmp_path, monkeypatch, invalid_code, expected_error
):
    original = HEADER + f"{invalid_code},2,3,4,5,1\n"
    source = _write_source(tmp_path, monkeypatch, original)
    with pytest.raises(ValueError, match=expected_error):
        _execute(scoring_recipe)

    assert source.read_bytes() == original.encode("utf-8")
    assert not (tmp_path / "measurement-analysis").exists()


def test_duplicate_item_headers_are_rejected_before_overwriting_values(
    scoring_recipe, tmp_path, monkeypatch
):
    original = "q1,q1,q3,q4,q5,q6\n" + VALID_ROWS
    source = _write_source(tmp_path, monkeypatch, original)
    with pytest.raises(ValueError, match="Missing or duplicate column names"):
        _execute(scoring_recipe)

    assert source.read_bytes() == original.encode("utf-8")
    assert not (tmp_path / "measurement-analysis").exists()
