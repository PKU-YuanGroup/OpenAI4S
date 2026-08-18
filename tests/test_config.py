"""Configuration defaults, validation, and placeholder API-key filtering."""

from dataclasses import asdict, fields
from pathlib import Path

import pytest

from openai4s.config import (
    AUTO_MODE_IMPORT_QUARANTINE_SELECTION,
    AUTO_MODE_LEGACY_CAN_ENABLE_PERMISSION_REVIEW,
    AUTO_MODE_LEGACY_RESULT_REVIEW_MODE,
    AUTO_MODE_SELECTION_PRECEDENCE,
    AutoModeBudgets,
    AutoModeConfig,
    Config,
    LLMConfig,
    RoadmapFeatureFlags,
    is_placeholder_api_key,
)


def test_is_placeholder_api_key_matches_template_stubs():
    assert is_placeholder_api_key("your-api-key-here")
    assert is_placeholder_api_key("  Your-API-Key-Here  ")  # case/space-insensitive
    assert is_placeholder_api_key("changeme")
    assert is_placeholder_api_key("")
    assert is_placeholder_api_key(None)
    assert not is_placeholder_api_key("sk-real-0123456789")
    # the offline suite's fake key (tests/conftest.py) must stay "configured"
    assert not is_placeholder_api_key("test-key")


def test_post_init_drops_placeholder_from_env(monkeypatch):
    monkeypatch.delenv("OPENAI4S_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI4S_LLM_API_KEY", "your-api-key-here")
    assert LLMConfig(provider="deepseek").api_key == ""


def test_post_init_drops_placeholder_passed_explicitly(monkeypatch):
    monkeypatch.delenv("OPENAI4S_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI4S_LLM_API_KEY", raising=False)
    assert LLMConfig(provider="deepseek", api_key="your_api_key_here").api_key == ""


def test_placeholder_specific_env_falls_through_to_generic(monkeypatch):
    monkeypatch.setenv("OPENAI4S_ARK_API_KEY", "your-api-key-here")
    monkeypatch.setenv("OPENAI4S_LLM_API_KEY", "sk-real-generic")
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.delenv("DOUBAO_API_KEY", raising=False)
    assert LLMConfig(provider="ark").api_key == "sk-real-generic"


def test_placeholder_explicit_key_falls_through_to_env(monkeypatch):
    monkeypatch.delenv("OPENAI4S_ARK_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI4S_LLM_API_KEY", "sk-real-generic")
    assert (
        LLMConfig(provider="ark", api_key="your-api-key-here").api_key
        == "sk-real-generic"
    )


def test_notebook_repl_flag_defaults_off_and_reads_env(monkeypatch):
    # the in-Notebook developer REPL is read-only (off) by default
    monkeypatch.delenv("OPENAI4S_NOTEBOOK_REPL", raising=False)
    assert Config().notebook_repl is False

    monkeypatch.setenv("OPENAI4S_NOTEBOOK_REPL", "1")
    assert Config().notebook_repl is True

    # the shared _env_flag falsey vocabulary keeps it off
    monkeypatch.setenv("OPENAI4S_NOTEBOOK_REPL", "0")
    assert Config().notebook_repl is False
    monkeypatch.setenv("OPENAI4S_NOTEBOOK_REPL", "off")
    assert Config().notebook_repl is False


def test_team_mode_defaults_off_and_reads_env(monkeypatch):
    # INV-1: team mode is opt-in; unset env == single-user behavior
    monkeypatch.delenv("OPENAI4S_TEAM_MODE", raising=False)
    assert Config().team_mode is False

    monkeypatch.setenv("OPENAI4S_TEAM_MODE", "1")
    assert Config().team_mode is True
    monkeypatch.setenv("OPENAI4S_TEAM_MODE", "off")
    assert Config().team_mode is False


ROADMAP_FLAGS = {
    "stage1_trusted_delivery": "OPENAI4S_STAGE1_TRUSTED_DELIVERY",
    "stage2_auto_run_storage": "OPENAI4S_STAGE2_AUTO_RUN_STORAGE",
    "stage3_scientific_review_shadow": "OPENAI4S_STAGE3_SCIENTIFIC_REVIEW_SHADOW",
    "stage4_review_completion_gate": "OPENAI4S_STAGE4_REVIEW_COMPLETION_GATE",
    "stage5_auto_repair": "OPENAI4S_STAGE5_AUTO_REPAIR",
    "stage6_guardian_shadow": "OPENAI4S_STAGE6_GUARDIAN_SHADOW",
    "stage7_guardian_enforcement": "OPENAI4S_STAGE7_GUARDIAN_ENFORCEMENT",
    "stage8_live_notebook_lineage": "OPENAI4S_STAGE8_LIVE_NOTEBOOK_LINEAGE",
    "stage9_artifact_workbench": "OPENAI4S_STAGE9_ARTIFACT_WORKBENCH",
    "stage10_scientific_connectors": "OPENAI4S_STAGE10_SCIENTIFIC_CONNECTORS",
    "stage11_durable_remote_compute": "OPENAI4S_STAGE11_DURABLE_REMOTE_COMPUTE",
    "stage12_auto_mode_ga": "OPENAI4S_STAGE12_AUTO_MODE_GA",
}

BUDGET_ENV = {
    "max_review_rounds": "OPENAI4S_AUTO_MAX_REVIEW_ROUNDS",
    "max_repair_rounds": "OPENAI4S_AUTO_MAX_REPAIR_ROUNDS",
    "repair_turns_per_round": "OPENAI4S_AUTO_REPAIR_TURNS_PER_ROUND",
    "max_extra_cells": "OPENAI4S_AUTO_MAX_EXTRA_CELLS",
    "wall_time_s": "OPENAI4S_AUTO_WALL_TIME_S",
    "extra_token_multiplier": "OPENAI4S_AUTO_EXTRA_TOKEN_MULTIPLIER",
    "repeated_finding_limit": "OPENAI4S_AUTO_REPEATED_FINDING_LIMIT",
    "same_action_no_delta_limit": "OPENAI4S_AUTO_SAME_ACTION_NO_DELTA_LIMIT",
    "no_progress_turn_limit": "OPENAI4S_AUTO_NO_PROGRESS_TURN_LIMIT",
    "guardian_timeout_s": "OPENAI4S_AUTO_GUARDIAN_TIMEOUT_S",
    "guardian_consecutive_denial_limit": (
        "OPENAI4S_AUTO_GUARDIAN_CONSECUTIVE_DENIAL_LIMIT"
    ),
    "guardian_window_size": "OPENAI4S_AUTO_GUARDIAN_WINDOW_SIZE",
    "guardian_window_denial_limit": "OPENAI4S_AUTO_GUARDIAN_WINDOW_DENIAL_LIMIT",
}


def _clear_auto_mode_environment(monkeypatch):
    for env_name in (
        *ROADMAP_FLAGS.values(),
        *BUDGET_ENV.values(),
        "OPENAI4S_AUTO_MODE",
        "OPENAI4S_RESULT_REVIEW_MODE",
        "OPENAI4S_APPROVALS_REVIEWER",
    ):
        monkeypatch.delenv(env_name, raising=False)


def test_stage_roadmap_flags_default_off(monkeypatch):
    _clear_auto_mode_environment(monkeypatch)

    flags = Config().roadmap_features

    assert {item.name for item in fields(flags)} == set(ROADMAP_FLAGS)
    assert all(getattr(flags, name) is False for name in ROADMAP_FLAGS)


@pytest.mark.parametrize("field_name,env_name", ROADMAP_FLAGS.items())
def test_each_stage_roadmap_flag_has_an_exact_opt_in(monkeypatch, field_name, env_name):
    for candidate in ROADMAP_FLAGS.values():
        monkeypatch.delenv(candidate, raising=False)
    monkeypatch.setenv(env_name, "true")

    flags = RoadmapFeatureFlags()

    assert getattr(flags, field_name) is True
    assert all(
        getattr(flags, other) is False for other in ROADMAP_FLAGS if other != field_name
    )


def test_roadmap_flag_typo_is_rejected_instead_of_enabling(monkeypatch):
    monkeypatch.setenv("OPENAI4S_STAGE7_GUARDIAN_ENFORCEMENT", "flase")

    with pytest.raises(ValueError, match="OPENAI4S_STAGE7_GUARDIAN_ENFORCEMENT"):
        RoadmapFeatureFlags()


def test_direct_roadmap_flag_value_must_be_a_real_bool():
    with pytest.raises(ValueError, match="stage5_auto_repair must be a bool"):
        RoadmapFeatureFlags(stage5_auto_repair="off")  # type: ignore[arg-type]


def test_auto_mode_product_defaults_are_inert(monkeypatch):
    _clear_auto_mode_environment(monkeypatch)

    mode = Config().auto_mode

    assert mode.enabled is False
    assert mode.preset == "off"
    assert mode.result_review_mode == "off"
    assert mode.approvals_reviewer == "user"
    assert mode.deployment_explicit is False
    assert mode.deployment_explicit_fields == ()


def test_auto_mode_deployment_explicitness_distinguishes_unset_from_off(monkeypatch):
    _clear_auto_mode_environment(monkeypatch)
    unset = AutoModeConfig()

    monkeypatch.setenv("OPENAI4S_AUTO_MODE", "off")
    explicit_off = AutoModeConfig()

    assert unset.preset == explicit_off.preset == "off"
    assert unset.deployment_explicit is False
    assert explicit_off.deployment_explicit is True
    assert explicit_off.deployment_explicit_fields == ("preset",)


def test_auto_mode_deployment_explicitness_is_captured_once(monkeypatch):
    _clear_auto_mode_environment(monkeypatch)
    monkeypatch.setenv("OPENAI4S_RESULT_REVIEW_MODE", "review_only")
    mode = AutoModeConfig()
    monkeypatch.delenv("OPENAI4S_RESULT_REVIEW_MODE")

    assert mode.deployment_explicit is True
    assert mode.deployment_explicit_fields == ("result_review_mode",)
    assert mode.result_review_mode == "review_only"


def test_auto_mode_deployment_explicit_metadata_is_closed():
    with pytest.raises(ValueError, match="unknown field"):
        AutoModeConfig(
            deployment_explicit=True,
            deployment_explicit_fields=([],),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("preset", ("1", "true", "yes", "on", "autonomous"))
def test_auto_mode_preset_is_one_noncontradictory_bundle(monkeypatch, preset):
    _clear_auto_mode_environment(monkeypatch)
    monkeypatch.setenv("OPENAI4S_AUTO_MODE", preset)
    monkeypatch.setenv("OPENAI4S_RESULT_REVIEW_MODE", "review_only")
    monkeypatch.setenv("OPENAI4S_APPROVALS_REVIEWER", "user")

    mode = AutoModeConfig()

    assert mode.enabled is True
    assert mode.preset == "autonomous"
    assert mode.result_review_mode == "auto_fix"
    assert mode.approvals_reviewer == "auto_review"
    assert mode.budgets == AutoModeBudgets()


def test_independent_submodes_remain_available_when_preset_is_off(monkeypatch):
    _clear_auto_mode_environment(monkeypatch)
    monkeypatch.setenv("OPENAI4S_AUTO_MODE", "off")
    monkeypatch.setenv("OPENAI4S_RESULT_REVIEW_MODE", "review_only")
    monkeypatch.setenv("OPENAI4S_APPROVALS_REVIEWER", "auto_review")

    mode = AutoModeConfig()

    assert mode.enabled is False
    assert mode.result_review_mode == "review_only"
    assert mode.approvals_reviewer == "auto_review"


@pytest.mark.parametrize(
    "name,value",
    (
        ("OPENAI4S_AUTO_MODE", "sometimes"),
        ("OPENAI4S_RESULT_REVIEW_MODE", "fix_everything"),
        ("OPENAI4S_APPROVALS_REVIEWER", "always_allow"),
    ),
)
def test_invalid_auto_mode_configuration_is_rejected(monkeypatch, name, value):
    _clear_auto_mode_environment(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        AutoModeConfig()


def test_auto_mode_choices_are_closed_and_normalized():
    mode = AutoModeConfig(
        enabled=True,
        result_review_mode=" AUTO_FIX ",
        approvals_reviewer=" AUTO_REVIEW ",
    )

    assert mode.enabled is True
    assert mode.result_review_mode == "auto_fix"
    assert mode.approvals_reviewer == "auto_review"


@pytest.mark.parametrize(
    "name",
    (
        "OPENAI4S_AUTO_MODE",
        "OPENAI4S_RESULT_REVIEW_MODE",
        "OPENAI4S_APPROVALS_REVIEWER",
        "OPENAI4S_STAGE7_GUARDIAN_ENFORCEMENT",
        "OPENAI4S_AUTO_MAX_REVIEW_ROUNDS",
    ),
)
def test_explicit_blank_strict_configuration_is_rejected(monkeypatch, name):
    _clear_auto_mode_environment(monkeypatch)
    monkeypatch.setenv(name, "   ")

    factory = RoadmapFeatureFlags if "STAGE7" in name else AutoModeConfig
    with pytest.raises(ValueError, match=name):
        factory()


def test_auto_mode_budget_defaults_freeze_the_master_plan(monkeypatch):
    _clear_auto_mode_environment(monkeypatch)

    assert asdict(AutoModeBudgets()) == {
        "max_review_rounds": 2,
        "max_repair_rounds": 2,
        "repair_turns_per_round": 12,
        "max_extra_cells": 30,
        "wall_time_s": 900,
        "extra_token_multiplier": 1.5,
        "repeated_finding_limit": 2,
        "same_action_no_delta_limit": 3,
        "no_progress_turn_limit": 5,
        "guardian_timeout_s": 90,
        "guardian_consecutive_denial_limit": 3,
        "guardian_window_size": 50,
        "guardian_window_denial_limit": 10,
    }


def test_auto_mode_budget_environment_can_only_tighten(monkeypatch):
    _clear_auto_mode_environment(monkeypatch)
    monkeypatch.setenv("OPENAI4S_AUTO_MAX_REPAIR_ROUNDS", "1")
    monkeypatch.setenv("OPENAI4S_AUTO_EXTRA_TOKEN_MULTIPLIER", "0.75")
    monkeypatch.setenv("OPENAI4S_AUTO_GUARDIAN_TIMEOUT_S", "45")

    budgets = AutoModeBudgets()

    assert budgets.max_repair_rounds == 1
    assert budgets.extra_token_multiplier == 0.75
    assert budgets.guardian_timeout_s == 45


@pytest.mark.parametrize(
    "name,value",
    (
        ("OPENAI4S_AUTO_MAX_REVIEW_ROUNDS", "3"),
        ("OPENAI4S_AUTO_MAX_REPAIR_ROUNDS", "-1"),
        ("OPENAI4S_AUTO_WALL_TIME_S", "900.0"),
        ("OPENAI4S_AUTO_EXTRA_TOKEN_MULTIPLIER", "nan"),
        ("OPENAI4S_AUTO_EXTRA_TOKEN_MULTIPLIER", "1.5001"),
        ("OPENAI4S_AUTO_GUARDIAN_TIMEOUT_S", "91"),
    ),
)
def test_auto_mode_budget_cannot_be_malformed_or_loosened(monkeypatch, name, value):
    _clear_auto_mode_environment(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        AutoModeBudgets()


def test_auto_mode_guardian_window_size_is_fixed_not_ambiguously_tightened():
    with pytest.raises(ValueError, match="guardian_window_size"):
        AutoModeBudgets(guardian_window_size=49)


def test_auto_mode_selection_precedence_and_migration_are_frozen():
    assert AUTO_MODE_SELECTION_PRECEDENCE == (
        "import_quarantine",
        "frame",
        "project",
        "deployment_explicit",
        "legacy_result_review",
        "built_in_defaults",
    )
    assert AUTO_MODE_IMPORT_QUARANTINE_SELECTION == (False, "off", "user")
    assert AUTO_MODE_LEGACY_RESULT_REVIEW_MODE == "review_only"
    assert AUTO_MODE_LEGACY_CAN_ENABLE_PERMISSION_REVIEW is False


def test_landed_stages_consume_only_their_roadmap_flags_without_changing_legacy_config(
    monkeypatch,
):
    _clear_auto_mode_environment(monkeypatch)
    baseline = Config()
    legacy_fields = tuple(
        item.name
        for item in fields(Config)
        if item.name not in {"roadmap_features", "auto_mode"}
    )

    for env_name in ROADMAP_FLAGS.values():
        monkeypatch.setenv(env_name, "1")
    monkeypatch.setenv("OPENAI4S_AUTO_MODE", "autonomous")
    enabled = Config()

    assert {name: getattr(enabled, name) for name in legacy_fields} == {
        name: getattr(baseline, name) for name in legacy_fields
    }

    package_root = Path(__file__).resolve().parents[1] / "openai4s"
    consumers = {name: [] for name in ROADMAP_FLAGS}
    auto_mode_consumers = []
    for path in package_root.rglob("*.py"):
        if path.name == "config.py":
            continue
        source = path.read_text(encoding="utf-8")
        relative = str(path.relative_to(package_root.parent))
        for flag_name in ROADMAP_FLAGS:
            if flag_name in source:
                consumers[flag_name].append(relative)
        if ".auto_mode" in source:
            auto_mode_consumers.append(relative)

    assert sorted(consumers.pop("stage1_trusted_delivery")) == [
        "openai4s/agent/loop.py",
        "openai4s/doctor.py",
        "openai4s/host/data.py",
        "openai4s/server/gateway.py",
    ]
    assert sorted(consumers.pop("stage2_auto_run_storage")) == [
        "openai4s/server/auto_mode.py",
        "openai4s/server/scientific_review.py",
    ]
    assert sorted(consumers.pop("stage3_scientific_review_shadow")) == [
        "openai4s/server/gateway.py",
        "openai4s/server/scientific_review.py",
    ]
    assert consumers == {
        name: []
        for name in ROADMAP_FLAGS
        if name
        not in {
            "stage1_trusted_delivery",
            "stage2_auto_run_storage",
            "stage3_scientific_review_shadow",
        }
    }
    assert "openai4s/server/auto_mode.py" in auto_mode_consumers
    assert all(
        relative.startswith("openai4s/server/")
        or relative.startswith("openai4s/storage/")
        or relative in {"openai4s/store.py", "openai4s/storage/__init__.py"}
        for relative in auto_mode_consumers
    )
    assert "openai4s/agent/engine.py" not in auto_mode_consumers
    assert "openai4s/host_dispatch.py" not in auto_mode_consumers


def test_data_roots_parse_colon_separated(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI4S_DATA_ROOTS", raising=False)
    assert Config().data_roots == []

    a, b = tmp_path / "datasets", tmp_path / "scratch"
    monkeypatch.setenv("OPENAI4S_DATA_ROOTS", f"{a}:{b}:")
    assert Config().data_roots == [a, b]

    monkeypatch.setenv("OPENAI4S_DATA_ROOTS", "   ")
    assert Config().data_roots == []


def test_placeholder_env_does_not_shadow_native_key(monkeypatch):
    # a .env copied verbatim from .env.example must not mask a real key the
    # user already exported for other tools — the placeholder is dropped
    # BEFORE the provider-native fallback (OPENAI_API_KEY & co) runs
    monkeypatch.delenv("OPENAI4S_CHATGPT_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI4S_LLM_API_KEY", "your-api-key-here")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-native-real")
    assert LLMConfig(provider="chatgpt").api_key == "sk-native-real"
