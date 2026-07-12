"""Scored offline fixtures for the Tool / Code / Finalize routing boundary.

This evaluates the deterministic router, not a remote model's scientific
judgment.  Each fixture is a recorded normalized model reply representing one
task class.  The suite catches protocol regressions such as fenced code racing
a native tool batch, ordinary prose becoming completion, or R being routed to
Python.  It requires no key, network, kernel, or third-party package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from openai4s.agent.actions import (
    CodeCell,
    FinalizeAction,
    NativeToolBatch,
    NativeToolCall,
    route_action,
)


@dataclass(frozen=True, slots=True)
class RoutingCase:
    case_id: str
    task_class: str
    content: str
    expected: str
    tool_calls: tuple[dict[str, Any], ...] = ()


def _call(tool_name: str, ordinal: int = 0, **arguments: Any) -> dict[str, Any]:
    return {
        "id": f"call-{tool_name}-{ordinal}",
        "wire_id": f"wire-{ordinal}",
        "name": tool_name,
        "ordinal": ordinal,
        "raw_arguments": "{}",
        "arguments": arguments,
        "provider_meta": {},
    }


def routing_cases() -> tuple[RoutingCase, ...]:
    """Return the versioned, reviewable routing fixture set."""

    return (
        RoutingCase(
            "metadata-native",
            "known session metadata operation",
            "I will inspect the current session.",
            "native_tools",
            (_call("session_status"),),
        ),
        RoutingCase(
            "native-wins-over-code",
            "control operation must not race computation",
            "```python\nprint('must not run in this step')\n```",
            "native_tools",
            (_call("list_artifacts"),),
        ),
        RoutingCase(
            "parallel-native-batch",
            "independent structured reads",
            "",
            "native_tools",
            (
                _call("session_status", 0),
                _call("list_artifacts", 1),
            ),
        ),
        RoutingCase(
            "python-science",
            "open-ended scientific computation",
            "```python\nscores = [x * x for x in range(5)]\nprint(scores)\n```",
            "python",
        ),
        RoutingCase(
            "r-science",
            "native R statistical analysis",
            "```r\nfit <- lm(y ~ x, data=df)\nsummary(fit)\n```",
            "r",
        ),
        RoutingCase(
            "remote-job-control",
            "long-running remote orchestration",
            "Submit the validated workload.",
            "native_tools",
            (_call("compute_submit", experiment="folding"),),
        ),
        RoutingCase(
            "dynamic-tool-control",
            "promote a repeated verified method",
            "Define it for this session.",
            "native_tools",
            (_call("define_dynamic_tool", name="score_candidates"),),
        ),
        RoutingCase(
            "structured-finalize",
            "tool-only or conversational completion",
            "The requested status is ready.",
            "finalize",
            (_call("finalize_response", output={"status": "ready"}),),
        ),
        RoutingCase(
            "prose-is-not-completion",
            "ordinary explanation without terminal declaration",
            "Everything is complete.",
            "none",
        ),
        RoutingCase(
            "unsupported-fence",
            "non-scientific executable channel is rejected",
            "```javascript\nconsole.log('no')\n```",
            "none",
        ),
        RoutingCase(
            "first-cell-only",
            "one scientific action per decision step",
            "```r\nx <- 1\n```\n```python\nx = 2\n```",
            "r",
        ),
    )


def _label(action: object) -> str:
    if isinstance(action, NativeToolBatch):
        return "native_tools"
    if isinstance(action, FinalizeAction):
        return "finalize"
    if isinstance(action, CodeCell):
        return action.language
    return "none"


def evaluate_routing_cases(
    cases: Iterable[RoutingCase] | None = None,
) -> dict[str, Any]:
    """Run fixtures and return a stable scored report with failure details."""

    selected = tuple(cases if cases is not None else routing_cases())
    outcomes: list[dict[str, Any]] = []
    confusion: dict[str, dict[str, int]] = {}
    passed = 0
    for case in selected:
        calls = tuple(NativeToolCall(**dict(item)) for item in case.tool_calls)
        actual = _label(route_action(case.content, calls))
        ok = actual == case.expected
        passed += int(ok)
        confusion.setdefault(case.expected, {})[actual] = (
            confusion.setdefault(case.expected, {}).get(actual, 0) + 1
        )
        outcomes.append(
            {
                "case_id": case.case_id,
                "task_class": case.task_class,
                "expected": case.expected,
                "actual": actual,
                "passed": ok,
            }
        )
    total = len(selected)
    return {
        "version": 1,
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "accuracy": (passed / total) if total else 1.0,
        "confusion": confusion,
        "cases": outcomes,
    }


__all__ = ["RoutingCase", "evaluate_routing_cases", "routing_cases"]
