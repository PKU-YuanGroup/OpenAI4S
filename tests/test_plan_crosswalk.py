"""The plan's 56 original proposals must each have exactly one recorded outcome.

Section 14 of the integrated report tracks 56 proposals from seven source reports,
and its own definition of done says none may be handled by "omission means
implicitly rejected". Prose cannot carry that: a table in a document can gain a
duplicate, lose a row, or say `Completed` about something no call chain reaches,
and nothing notices.

So the crosswalk is data ([`docs/plan-crosswalk.json`](../docs/plan-crosswalk.json))
and this file is the check. It asserts the shape — 56 unique `(source,
original_id)` keys, each appearing once — and it asserts that the *vocabulary* is
used honestly: `closed` requires a named test file that exists, and
`implemented_unverified` requires the missing run to be named rather than left as
an absence.

What it deliberately does not do is verify that a `closed` row is really closed.
No test can: that is what the row's named test file is for. This file makes the
claim locatable and refuses the claims that are structurally empty.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CROSSWALK = ROOT / "docs" / "plan-crosswalk.json"


@pytest.fixture(scope="module")
def crosswalk() -> dict:
    return json.loads(CROSSWALK.read_text("utf-8"))


def test_the_crosswalk_has_exactly_fifty_six_rows(crosswalk):
    assert crosswalk["expected_rows"] == 56
    assert len(crosswalk["items"]) == 56, (
        f"the plan tracks 56 proposals, the crosswalk has " f"{len(crosswalk['items'])}"
    )


def test_every_key_appears_exactly_once(crosswalk):
    """A duplicate is how a row gets two different answers; a missing key is the
    "omission means rejected" the plan's exit criteria forbid."""
    keys = [(item["source"], item["original_id"]) for item in crosswalk["items"]]
    duplicates = sorted(k for k, n in Counter(keys).items() if n > 1)
    assert not duplicates, f"these keys appear more than once: {duplicates}"
    assert len(set(keys)) == 56


def test_all_seven_source_reports_are_represented(crosswalk):
    """Each of the seven reports contributed eight proposals. A source that
    vanished entirely is the failure mode a row count alone cannot see."""
    per_source = Counter(item["source"] for item in crosswalk["items"])
    assert sorted(per_source) == ["R1", "R2", "R3", "R4", "R5", "R6", "R7"]
    assert all(count == 8 for count in per_source.values()), dict(per_source)


def test_every_row_carries_the_required_fields(crosswalk):
    required = {
        "source",
        "original_id",
        "original_priority",
        "original_proposal",
        "integrated",
        "plan_conclusion",
        "status",
        "status_note",
        "tests",
        "browser_evidence",
        "external_evidence",
    }
    for item in crosswalk["items"]:
        missing = sorted(required - set(item))
        assert not missing, f"{item.get('original_id')} is missing {missing}"


def test_every_status_is_from_the_declared_vocabulary(crosswalk):
    allowed = set(crosswalk["statuses"])
    for item in crosswalk["items"]:
        assert item["status"] in allowed, (
            f"{item['source']}/{item['original_id']} has status "
            f"{item['status']!r}, which is not in {sorted(allowed)}"
        )


def test_a_closed_row_names_a_test_file_that_exists(crosswalk):
    """`closed` is the only status that claims something was proved, so it is the
    only one that has to point at the proof. A `closed` row with no test, or with
    a test file that does not exist, is the label this whole exercise is about."""
    for item in crosswalk["items"]:
        if item["status"] != "closed":
            continue
        named = [part.strip() for part in item["tests"].split(",") if part.strip()]
        assert (
            named
        ), f"{item['source']}/{item['original_id']} is closed but names no test"
        for path in named:
            assert (ROOT / path).is_file(), (
                f"{item['source']}/{item['original_id']} names {path}, which does "
                f"not exist"
            )


def test_an_unverified_row_names_the_run_it_is_missing(crosswalk):
    """ "Implemented but unverified" is only honest if it says what would settle
    it. Without that it is indistinguishable from "we did not check"."""
    for item in crosswalk["items"]:
        if item["status"] != "implemented_unverified":
            continue
        assert item["external_evidence"].strip(), (
            f"{item['source']}/{item['original_id']} is implemented_unverified but "
            f"does not name the missing run"
        )


def test_only_p2_rows_may_be_deferred(crosswalk):
    """A P0 or P1 row cannot be parked under the P2 decision."""
    for item in crosswalk["items"]:
        if item["status"] == "deferred_p2":
            assert item["integrated"].startswith("P2"), (
                f"{item['source']}/{item['original_id']} is deferred as P2 but its "
                f"destination is {item['integrated']!r}"
            )


def test_no_row_is_silently_absent_from_a_destination(crosswalk):
    """Every row names at least one integrated destination, so "where did this
    proposal go" always has an answer."""
    for item in crosswalk["items"]:
        assert item[
            "integrated"
        ].strip(), f"{item['source']}/{item['original_id']} names no destination"


def test_the_crosswalk_records_what_it_was_audited_against(crosswalk):
    """A status is a statement about a commit. Without the commit it is a
    statement about nothing."""
    assert len(crosswalk["baseline"]) == 40
    assert len(crosswalk["audited_at"]) == 40
