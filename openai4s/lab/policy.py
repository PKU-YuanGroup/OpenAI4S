"""Pure admission decisions; persistence and providers belong to the manager."""

from collections.abc import Mapping
from typing import Any

from openai4s.lab.models import Budgets, ErrorCode, LabError


def admission_error(
    run: Mapping[str, Any], budgets: Budgets, now_ms: int
) -> LabError | None:
    if run["status"] in {"ended", "failed"}:
        return LabError(ErrorCode.RUN_ENDED, "Lab run has ended")
    if run["status"] == "quarantined":
        return LabError(
            ErrorCode.RESOURCE_QUARANTINED, "Lab run requires reconciliation"
        )
    if run["status"] in {"busy", "creating"}:
        return LabError(ErrorCode.RESOURCE_BUSY, "Lab run is busy")
    exhausted = (
        ("max_steps", run["step_count"] >= budgets.max_steps),
        ("max_commands", run["command_count"] >= budgets.max_commands),
        ("max_wall_ms", now_ms - run["created_at"] >= budgets.max_wall_ms),
        (
            "max_consecutive_failures",
            run["consecutive_failures"] >= budgets.max_consecutive_failures,
        ),
    )
    for budget, reached in exhausted:
        if reached:
            return LabError(
                ErrorCode.BUDGET_EXHAUSTED,
                "Lab run budget exhausted",
                {"budget": budget},
            )
    return None
