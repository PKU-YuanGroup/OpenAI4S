"""Numerical comparison and refusal contracts shared by empirical replays."""

from types import SimpleNamespace

import pytest

from harness.evals.sidecar_replay import matches, run_case


def test_nested_float_roundoff_does_not_hide_real_drift():
    expected = {"effects": [{"estimate": 0.3}]}
    assert matches({"effects": [{"estimate": 0.1 + 0.2}], "audit": {}}, expected)
    assert not matches({"effects": [{"estimate": 0.3001}]}, expected)
    assert not matches({"effects": []}, expected)
    assert not matches({}, expected)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_results_cannot_match_a_declared_estimate(value):
    assert not matches({"estimate": value}, {"estimate": 0.0})


@pytest.mark.stubbed_backend
def test_declared_refusal_needs_the_expected_error_not_success_or_a_crash():
    case = {
        "id": "refusal",
        "operation": "fit",
        "kwargs": {},
        "expected_error": "bad design",
    }
    success = SimpleNamespace(fit=lambda rows: {"estimate": 0})
    assert not run_case(success, case, [], mismatch_detail="unexpected success")[
        "passed"
    ]
    for error, passes in [
        (ValueError("bad design"), True),
        (TypeError("bad design"), False),
        (ValueError("different reason"), False),
    ]:

        def fail(rows):
            raise error

        result = run_case(
            SimpleNamespace(fit=fail), case, [], mismatch_detail="unexpected success"
        )
        assert result["passed"] is passes
