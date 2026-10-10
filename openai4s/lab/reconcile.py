"""Recover durable intent, never reconstruct or replay a lost simulation."""

from collections.abc import Callable

from openai4s.lab.ports import LabLedgerPort


def reconcile_on_startup(
    ledger: LabLedgerPort, *, instance_id: str, clock_ms: Callable[[], int]
) -> dict[str, int]:
    counts = {
        "runs_ended": 0,
        "runs_failed": 0,
        "outcome_unknown": 0,
        "not_dispatched": 0,
    }
    # The ledger owns write timestamps. The injected clock dates this report.
    counts["reconciled_at_ms"] = clock_ms()
    for run in ledger.nonterminal_runs():
        if run["daemon_instance"] == instance_id:
            continue
        for command in ledger.inflight_commands(run["run_id"]):
            if command["state"] == "admitted":
                counts["not_dispatched"] += ledger.mark_not_dispatched(
                    command["command_id"],
                    error_code="provider_unavailable",
                    error="Previous daemon did not dispatch this command",
                )
            elif command["state"] in {"dispatching", "running", "stop_requested"}:
                counts["outcome_unknown"] += ledger.mark_outcome_unknown(
                    command["command_id"],
                    error="Previous daemon lost the provider receipt",
                )
        creating = run["status"] == "creating"
        counts["runs_failed" if creating else "runs_ended"] += ledger.end_run(
            run["run_id"],
            end_reason="create_failed" if creating else "provider_lost",
            status="failed" if creating else "ended",
        )
    return counts
