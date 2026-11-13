from __future__ import annotations

import pytest

from openai4s.rl import BudgetExceeded, BudgetLedger


def test_hard_budget_rejects_before_work_and_retry_is_idempotent():
    ledger = BudgetLedger({"gpu_seconds": 10, "api_usd": 1.0}, clock_ms=lambda: 100)
    first = ledger.reserve("allocation-1", {"gpu_seconds": 8, "api_usd": 0.25})
    assert ledger.reserve("allocation-1", {"gpu_seconds": 8, "api_usd": 0.25}) == first
    with pytest.raises(BudgetExceeded) as error:
        ledger.reserve("allocation-2", {"gpu_seconds": 3})
    assert error.value.fields["gpu_seconds"] == pytest.approx(1.0)
    assert ledger.state().remaining("gpu_seconds") == pytest.approx(2.0)


def test_soft_budget_allows_overage_but_records_actual_separately():
    ledger = BudgetLedger(
        {"gpu_seconds": 10}, modes={"gpu_seconds": "soft"}, clock_ms=lambda: 10
    )
    ledger.reserve("allocation-soft", {"gpu_seconds": 12})
    ledger.bind_receipt("allocation-soft", "receipt-soft")
    committed = ledger.commit("allocation-soft", {"gpu_seconds": 13})
    assert committed.state == "committed"
    assert committed.estimated["gpu_seconds"] == 12
    assert committed.actual["gpu_seconds"] == 13
    assert ledger.state().used["gpu_seconds"] == pytest.approx(13)
    assert ledger.state().remaining("gpu_seconds") == 0


def test_receipt_binding_prevents_optimistic_release_and_unknown_keeps_reservation():
    ledger = BudgetLedger({"gpu_seconds": 10}, clock_ms=lambda: 1)
    ledger.reserve("allocation-unknown", {"gpu_seconds": 4})
    ledger.bind_receipt("allocation-unknown", "receipt-unknown")
    with pytest.raises(ValueError, match="unstarted"):
        ledger.release("allocation-unknown")
    unknown = ledger.mark_unknown("allocation-unknown")
    assert unknown.state == "unknown"
    assert ledger.state().reserved["gpu_seconds"] == pytest.approx(4)
    committed = ledger.commit("allocation-unknown", {"gpu_seconds": 2})
    assert committed.state == "committed"
    assert ledger.state().reserved.get("gpu_seconds", 0) == 0
    assert ledger.state().used["gpu_seconds"] == pytest.approx(2)


def test_partial_actual_usage_stays_unknown_until_reconciliation():
    ledger = BudgetLedger({"gpu_seconds": 10, "api_usd": 1}, clock_ms=lambda: 1)
    ledger.reserve("allocation-partial", {"gpu_seconds": 4, "api_usd": 0.5})
    ledger.bind_receipt("allocation-partial", "receipt-partial")
    partial = ledger.commit("allocation-partial", {"gpu_seconds": 2})
    assert partial.state == "unknown"
    assert ledger.state().used == {}
    complete = ledger.reconcile("allocation-partial", {"api_usd": 0.25})
    assert complete.state == "committed"
    assert ledger.state().used == {"gpu_seconds": 2.0, "api_usd": 0.25}


def test_release_is_only_for_unstarted_unbound_reservation():
    ledger = BudgetLedger({"api_usd": 1}, clock_ms=lambda: 1)
    released = ledger.reserve("allocation-release", {"api_usd": 0.5})
    assert ledger.release("allocation-release").state == "released"
    assert ledger.release("allocation-release") == ledger.get("allocation-release")
    assert released.state == "reserved"
