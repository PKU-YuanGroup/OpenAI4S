"""The benchmark, run — not merely present.

The proposal is explicit about what would make ten workflows and twenty cases
worthless: a directory of fixtures nobody executes, or cases that pass because
the thing they exercise is a mock. So this file runs every case against the
real subsystems and asserts the outcome each case declared.

The declared outcome is the point. A case that says `failure` and completes
cleanly has failed exactly as much as one that says `success` and raises — a
benchmark scoring "no exception" measures only the half of the system nobody
doubted. Five of the twenty cases exist to watch something refuse.
"""
from __future__ import annotations

import pytest

from openai4s.benchmark import load_workflows, run_case
from openai4s.benchmark.model import OUTCOMES
from openai4s.benchmark.steps import STEPS

WORKFLOWS = load_workflows()
CASES = [(w, c) for w in WORKFLOWS for c in w.cases]


# --------------------------------------------------------------------------
# the frozen shape of the suite
# --------------------------------------------------------------------------


def test_ten_workflows_are_frozen():
    """The number is the commitment. Dropping one to make a run green is the
    failure mode this asserts against."""
    assert len(WORKFLOWS) == 10, [w.id for w in WORKFLOWS]


def test_every_workflow_carries_at_least_two_cases():
    thin = [w.id for w in WORKFLOWS if len(w.cases) < 2]
    assert not thin, f"a single case cannot represent a workflow: {thin}"


def test_at_least_twenty_versioned_cases():
    assert len(CASES) >= 20


def test_every_case_id_is_unique():
    ids = [case.id for _workflow, case in CASES]
    assert len(ids) == len(set(ids))


def test_every_workflow_declares_what_would_make_it_fail():
    """A workflow with no stated failure condition is a demo, not a case."""
    for workflow in WORKFLOWS:
        assert workflow.failure_conditions, workflow.id
        assert workflow.version
        assert workflow.summary


def test_every_step_a_manifest_names_is_implemented():
    """A manifest may not describe work nothing performs."""
    for workflow in WORKFLOWS:
        names = set(workflow.steps) | {
            name for case in workflow.cases for name in case.steps
        }
        missing = sorted(names - set(STEPS))
        assert not missing, f"{workflow.id} names unimplemented step(s) {missing}"


def test_the_suite_measures_more_than_the_happy_path():
    """Success-only coverage is the shape a benchmark drifts into."""
    outcomes = {case.outcome for _workflow, case in CASES}
    assert outcomes <= OUTCOMES
    assert outcomes - {"success"}, "every case expects success"
    refusing = [
        c.id for _w, c in CASES if c.outcome in ("failure", "permission_denied")
    ]
    assert len(refusing) >= 3, f"only {len(refusing)} case(s) watch something refuse"


# --------------------------------------------------------------------------
# and then it runs
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "workflow,case", CASES, ids=[case.id for _workflow, case in CASES]
)
def test_case(workflow, case):
    result = run_case(workflow, case)
    if result.skipped:
        pytest.skip(result.detail)
    assert result.passed, f"{case.id} ({case.outcome}): {result.detail}"
