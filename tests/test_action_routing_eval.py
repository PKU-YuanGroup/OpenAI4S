"""The mixed Tool/Code router has an explicit, scored offline evaluation."""

from harness.evals.action_routing import (
    RoutingCase,
    evaluate_routing_cases,
    routing_cases,
)


def test_routing_fixture_suite_scores_every_action_channel() -> None:
    report = evaluate_routing_cases()

    assert report["version"] == 1
    assert report["total"] >= 10
    assert report["failed"] == 0
    assert report["accuracy"] == 1.0
    expected = {case.expected for case in routing_cases()}
    assert {"native_tools", "python", "r", "finalize", "none"} <= expected


def test_routing_report_exposes_reviewable_failures() -> None:
    deliberately_wrong = RoutingCase(
        case_id="expected-mismatch",
        task_class="report contract",
        content="```python\nprint(1)\n```",
        expected="r",
    )

    report = evaluate_routing_cases([deliberately_wrong])

    assert report["failed"] == 1
    assert report["cases"] == [
        {
            "case_id": "expected-mismatch",
            "task_class": "report contract",
            "expected": "r",
            "actual": "python",
            "passed": False,
        }
    ]
