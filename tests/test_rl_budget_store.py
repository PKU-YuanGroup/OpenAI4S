from __future__ import annotations

import pytest

from openai4s.config import Config
from openai4s.store import get_store


def test_store_backed_budget_survives_reopen_and_does_not_double_commit(tmp_path):
    db_path = Config(data_dir=tmp_path).db_path
    store = get_store(db_path)
    ledger = store.rl.budget_ledger(
        "rollout-budget",
        {"gpu_seconds": 10, "api_usd": 1},
        modes={"gpu_seconds": "hard", "api_usd": "soft"},
    )
    ledger.reserve("allocation-1", {"gpu_seconds": 4, "api_usd": 0.2})
    ledger.bind_receipt("allocation-1", "provider-receipt-1")
    ledger.mark_unknown("allocation-1")
    store.close()

    reopened = get_store(db_path)
    try:
        restored = reopened.rl.budget_ledger(
            "rollout-budget",
            {"gpu_seconds": 10, "api_usd": 1},
            modes={"gpu_seconds": "hard", "api_usd": "soft"},
        )
        assert restored.get("allocation-1") is not None
        assert restored.get("allocation-1").state == "unknown"
        assert restored.get("allocation-1").provider_receipt == "provider-receipt-1"
        assert restored.state().reserved["gpu_seconds"] == pytest.approx(4)

        restored.reconcile("allocation-1", {"gpu_seconds": 3, "api_usd": 0.1})
        restored.reconcile("allocation-1", {"gpu_seconds": 3, "api_usd": 0.1})
        assert restored.state().used == {"gpu_seconds": 3.0, "api_usd": 0.1}
    finally:
        reopened.close()


def test_store_backed_budget_rejects_changed_limits(tmp_path):
    db_path = Config(data_dir=tmp_path).db_path
    store = get_store(db_path)
    store.rl.budget_ledger("budget", {"gpu_seconds": 10})
    store.close()
    reopened = get_store(db_path)
    try:
        with pytest.raises(ValueError, match="limits conflict"):
            reopened.rl.budget_ledger("budget", {"gpu_seconds": 11})
    finally:
        reopened.close()
