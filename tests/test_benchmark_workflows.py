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


def _tiny_workflow(steps, cases):
    from openai4s.benchmark.model import Case, Workflow

    return Workflow(
        id="probe",
        version="1",
        title="probe",
        summary="probe",
        steps=tuple(steps),
        permissions=(),
        artifacts=(),
        failure_conditions=("x",),
        cases=tuple(cases),
    )


def test_an_unimplemented_step_is_a_hard_error_not_a_scored_refusal():
    """A manifest naming a step nothing implements is a manifest bug. Raising it
    inside the outcome-evaluation try let an error-expecting case catch the
    KeyError and score it green — a workflow describing work nothing does passed.
    It must fail hard instead, regardless of the declared outcome."""
    from openai4s.benchmark.model import Case

    case = Case(
        id="c",
        workflow="probe",
        title="c",
        outcome="failure",
        steps=("does_not_exist",),
        expect={},
    )
    with pytest.raises(KeyError, match="not.*implemented|nothing does"):
        run_case(_tiny_workflow(("does_not_exist",), [case]), case)


def test_an_error_expecting_case_must_assert_something_about_the_error(monkeypatch):
    """An empty `expect` on a failure/permission_denied case would pass on any
    incidental exception — fabricated coverage in a suite that gates releases.
    An unexpected infrastructure error must not be scored as the declared
    refusal when the case asserts nothing about it."""
    from openai4s.benchmark import runner as runner_mod
    from openai4s.benchmark.model import Case

    def boom(context, inputs):
        raise RuntimeError("an incidental infrastructure failure, not a refusal")

    monkeypatch.setitem(runner_mod.STEPS, "_boom", boom)
    case = Case(
        id="c",
        workflow="probe",
        title="c",
        outcome="failure",
        steps=("_boom",),
        expect={},  # asserts nothing about the error
    )
    result = run_case(_tiny_workflow(("_boom",), [case]), case)
    assert result.passed is False, "an incidental error was scored as the refusal"
    assert not result.skipped
    assert "asserts nothing" in result.detail or "empty expect" in result.detail


# --------------------------------------------------------------------------
# the manifests must ship, and the root parameter must be honoured
# --------------------------------------------------------------------------


def test_the_workflows_are_shipped_as_package_data():
    """An installed wheel that did not ship the manifests would make
    `openai4s benchmark` report a green run over zero workflows."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    text = (root / "pyproject.toml").read_text("utf-8")
    try:
        import tomllib
    except ModuleNotFoundError:
        # `tomllib` is stdlib from 3.11, and this project supports 3.10 — where
        # an unconditional import made the whole test error out rather than
        # check anything. The core is zero-dependency, so there is no parser to
        # fall back to; the same claim is asserted as text, which is what the
        # two checks below already do.
        assert '"workflows*"' in text, (
            "the benchmark manifests are not packaged; an installed benchmark "
            "would find nothing and pass silently"
        )
    else:
        include = tomllib.loads(text)["tool"]["setuptools"]["packages"]["find"][
            "include"
        ]
        assert "workflows*" in include, (
            "the benchmark manifests are not packaged; an installed benchmark "
            "would find nothing and pass silently"
        )
    manifest = (root / "MANIFEST.in").read_text("utf-8")
    assert "recursive-include workflows" in manifest
    build = (root / "scripts" / "build_macos_dmg.sh").read_text("utf-8")
    assert "/workflows" in build, "the DMG does not copy the workflow manifests"


def test_run_all_honours_the_root_it_is_given(tmp_path):
    """The parameter was accepted and ignored, so a caller pointing at another
    suite silently got the repository default."""
    from openai4s.benchmark.runner import run_all

    empty = tmp_path / "empty-suite"
    empty.mkdir()
    report = run_all(empty)
    assert (
        report["workflows"] == 0
    ), "run_all ran the default suite instead of the empty root it was given"


def test_the_cli_treats_zero_workflows_as_a_failure(monkeypatch, capsys, tmp_path):
    """Zero workflows is not a pass. A packaging regression must not exit 0."""
    import importlib
    import types

    from openai4s.benchmark import model as bmodel

    cli = importlib.import_module("openai4s.cli.main")
    monkeypatch.setattr(bmodel, "WORKFLOW_ROOT", tmp_path / "no-workflows")
    rc = cli.cmd_benchmark(types.SimpleNamespace(list=False, json=False))
    assert rc == 1
    assert "no benchmark workflows" in capsys.readouterr().err
