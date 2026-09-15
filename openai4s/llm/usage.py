"""Private metering evidence carried beside the compatible public usage dict."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class UsageEvidence:
    raw: Mapping[str, Any]
    counters: Mapping[str, int]
    final: bool
    invalid: frozenset[str] = frozenset()


class RawUsage(dict):
    """Provider counters, with whether they describe the finished generation."""

    def __init__(self, value: Any = None, *, final: bool = True) -> None:
        super().__init__(value if isinstance(value, Mapping) else {})
        self.final = final


class UsageMetrics(dict):
    """JSON still sees only historical keys; accounting also sees their source."""

    def __init__(self, values: Mapping[str, Any], evidence: UsageEvidence) -> None:
        super().__init__(values)
        self.evidence = evidence


def copy_usage(value: Any, values: Mapping[str, Any] | None = None) -> dict:
    """Preserve private evidence when a typed boundary copies/project counters."""
    projected = values if values is not None else value
    projected = dict(projected) if isinstance(projected, Mapping) else {}
    evidence = getattr(value, "evidence", None)
    if evidence is None:
        raw = value if isinstance(value, Mapping) else {}
        evidence = usage_evidence(raw, measured_usage(raw))
    return UsageMetrics(projected, evidence)


def measured_usage(value: Any) -> dict[str, int]:
    """Only explicitly measured non-negative integers may enter a ledger."""
    evidence = getattr(value, "evidence", None)
    if evidence is not None:
        return dict(evidence.counters) if evidence.final else {}
    if not isinstance(value, Mapping) or getattr(value, "final", True) is False:
        return {}
    result = {
        key: item
        for key, item in value.items()
        if key
        in (
            "input_tokens",
            "output_tokens",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "cache_read",
            "cache_write",
            "reasoning_tokens",
        )
        and type(item) is int
        and item >= 0
    }
    for canonical, alias in (
        ("input_tokens", "prompt_tokens"),
        ("output_tokens", "completion_tokens"),
    ):
        if canonical not in result and alias in result:
            result[canonical] = result[alias]
    return result


def measured_total(value: Any) -> int | None:
    counters = measured_usage(value)
    if "input_tokens" in counters and "output_tokens" in counters:
        return max(
            counters["input_tokens"] + counters["output_tokens"],
            counters.get("total_tokens", 0),
        )
    return counters.get("total_tokens")


def usage_evidence(
    raw: Mapping[str, Any],
    counters: Mapping[str, int],
    invalid: frozenset[str] = frozenset(),
) -> UsageEvidence:
    return UsageEvidence(
        copy.deepcopy(dict(raw)), dict(counters), getattr(raw, "final", True), invalid
    )
