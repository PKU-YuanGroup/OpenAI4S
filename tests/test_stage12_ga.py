"""Stage 12 GA kill switch remains default-off."""

from __future__ import annotations

from pathlib import Path

from openai4s.config import AutoModeBudgets, Config, RoadmapFeatureFlags
from openai4s.server.auto_budget import FIELD_AUTHORITIES, inspect_budget_wiring
from openai4s.server.stage12_ga import official_stage12_enabled, rollout_status


def test_stage12_flag_defaults_off_and_does_not_enable_earlier_stages():
    cfg = Config()
    assert official_stage12_enabled(cfg) is False
    status = rollout_status(cfg)
    assert status["auto_mode_default"] == "off"
    assert status["active_phase"] == "shadow"
    assert status["earlier_flags_remain_opt_in"] is True
    assert status["any_earlier_flag_on"] is False
    expected = {
        name
        for name in RoadmapFeatureFlags.__dataclass_fields__
        if name != "stage12_auto_mode_ga"
    }
    assert set(status["earlier_flags"]) == expected
    assert not any(status["earlier_flags"].values())


def test_stage12_kill_switch_declares_ga_without_changing_legacy_defaults():
    cfg = Config(roadmap_features=RoadmapFeatureFlags(stage12_auto_mode_ga=True))
    status = rollout_status(cfg)
    assert official_stage12_enabled(cfg) is True
    assert status["active_phase"] == "ga"
    assert status["any_earlier_flag_on"] is False
    assert cfg.notebook_repl is False
    assert cfg.roadmap_features.stage8_live_notebook_lineage is False
    assert cfg.roadmap_features.stage9_artifact_workbench is False
    assert cfg.roadmap_features.stage10_scientific_connectors is False
    assert cfg.roadmap_features.stage11_durable_remote_compute is False
    evidence = Path("docs/auto-mode-stage12-evidence.md").read_text(encoding="utf-8")
    for stage in range(13):
        assert f"| {stage} |" in evidence
    assert status["ga_refused"] is False
    assert status["ga_blocked_on"] == []
    assert status["auto_budget"]["ga_ready"] is True
    assert status["auto_budget"]["sink_bypass_count"] == 0
    assert set(status["auto_budget"]["field_authorities"]) == set(
        AutoModeBudgets.__dataclass_fields__
    )


def test_stage12_refuses_ga_when_a_budget_authority_or_sink_is_missing(monkeypatch):
    inventory = inspect_budget_wiring()
    assert inventory["ga_ready"] is True
    assert set(FIELD_AUTHORITIES) == set(AutoModeBudgets.__dataclass_fields__)

    def broken_inventory():
        return {
            **inventory,
            "ga_ready": False,
            "missing_sinks": ["review"],
            "sink_bypass_count": 1,
        }

    monkeypatch.setattr(
        "openai4s.server.stage12_ga.inspect_budget_wiring", broken_inventory
    )
    cfg = Config(roadmap_features=RoadmapFeatureFlags(stage12_auto_mode_ga=True))
    status = rollout_status(cfg)
    assert official_stage12_enabled(cfg) is True
    assert status["active_phase"] == "shadow"
    assert status["ga_refused"] is True
    assert "budget_sink_unwired" in status["ga_blocked_on"]
