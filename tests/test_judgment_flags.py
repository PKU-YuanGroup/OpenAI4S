"""Effective-flag resolution for the experimental judgment layer."""

from __future__ import annotations

import json
import logging

import pytest

from openai4s.config import Config, ExperimentalJudgmentFlags
from openai4s.judgment.disclosure import (
    CAPABILITIES,
    DISCLOSURE_VERSION,
    is_acknowledged,
)
from openai4s.judgment.flags import (
    JUDGMENT_FLAG_PRECEDENCE,
    SETTING_MASTER,
    SETTING_SKILL_SUGGEST,
    ResolvedFlag,
    resolve,
)

_TRUE_SPELLINGS = ("1", "true", "yes", "on", "TRUE", "Yes")
_FALSE_SPELLINGS = ("0", "false", "no", "off", "FALSE")
_JUDGMENT_ENV = (
    "OPENAI4S_EXPERIMENTAL_JUDGMENT",
    "OPENAI4S_JUDGMENT_SKILL_SUGGEST",
    "OPENAI4S_JUDGMENT_LITERATURE",
    "OPENAI4S_JUDGMENT_TEXT_FEATURES",
    "OPENAI4S_JUDGMENT_SAFETY_SHADOW",
    "OPENAI4S_JUDGMENT_TASK_MODE_SHADOW",
    "OPENAI4S_JUDGMENT_PROVIDER",
    "OPENAI4S_JUDGMENT_MODEL",
    "OPENAI4S_JUDGMENT_TIMEOUT_S",
)


class MemoryStore:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self._values = dict(values or {})

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        return self._values.get(key, default)


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _JUDGMENT_ENV:
        monkeypatch.delenv(name, raising=False)


def _ack(*capabilities: str, version: str = DISCLOSURE_VERSION) -> str:
    return json.dumps(
        {
            "version": version,
            "capabilities": list(capabilities),
            "acked_at": "2026-09-20T00:00:00Z",
        }
    )


@pytest.fixture(autouse=True)
def _judgment_env_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)


@pytest.mark.parametrize("value", _TRUE_SPELLINGS)
def test_tristate_true(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("OPENAI4S_EXPERIMENTAL_JUDGMENT", value)
    assert ExperimentalJudgmentFlags().master is True


@pytest.mark.parametrize("value", _FALSE_SPELLINGS)
def test_tristate_false(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("OPENAI4S_EXPERIMENTAL_JUDGMENT", value)
    assert ExperimentalJudgmentFlags().master is False


def test_tristate_unset_is_none() -> None:
    flags = ExperimentalJudgmentFlags()
    assert flags.master is None
    assert flags.skill_suggest is None
    assert flags.literature_check is None
    assert flags.text_features is None
    assert flags.safety_shadow is None
    assert flags.task_mode_shadow is None
    assert flags.provider == "typesafe"
    assert flags.model == "jev-1.13.0"
    assert flags.timeout_s == 3.0


def test_tristate_typo_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI4S_EXPERIMENTAL_JUDGMENT", "flase")
    with pytest.raises(ValueError, match="OPENAI4S_EXPERIMENTAL_JUDGMENT"):
        ExperimentalJudgmentFlags()


def test_invalid_provider_and_timeout_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI4S_JUDGMENT_PROVIDER", "other")
    with pytest.raises(ValueError, match="OPENAI4S_JUDGMENT_PROVIDER"):
        ExperimentalJudgmentFlags()
    monkeypatch.delenv("OPENAI4S_JUDGMENT_PROVIDER")
    monkeypatch.setenv("OPENAI4S_JUDGMENT_TIMEOUT_S", "0")
    with pytest.raises(ValueError, match="OPENAI4S_JUDGMENT_TIMEOUT_S"):
        ExperimentalJudgmentFlags()
    monkeypatch.setenv("OPENAI4S_JUDGMENT_TIMEOUT_S", "31")
    with pytest.raises(ValueError, match="OPENAI4S_JUDGMENT_TIMEOUT_S"):
        ExperimentalJudgmentFlags()


def test_rule1_env_false_is_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI4S_EXPERIMENTAL_JUDGMENT", "0")
    monkeypatch.setenv("OPENAI4S_JUDGMENT_SKILL_SUGGEST", "1")
    store = MemoryStore(
        {
            SETTING_MASTER: "true",
            SETTING_SKILL_SUGGEST: "true",
            "experimental.judgment.disclosure_ack": _ack("skill_suggest"),
        }
    )
    effective = resolve(Config(), store)
    assert effective.master == ResolvedFlag(False, "env_off")
    assert effective.skill_suggest == ResolvedFlag(False, "master_off")
    for name in CAPABILITIES:
        assert getattr(effective, name).enabled is False


def test_rule2_env_true_enables_without_disclosure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("OPENAI4S_EXPERIMENTAL_JUDGMENT", "1")
    monkeypatch.setenv("OPENAI4S_JUDGMENT_SKILL_SUGGEST", "1")
    caplog.set_level(logging.WARNING, logger="openai4s.judgment.flags")
    effective = resolve(Config(), MemoryStore())
    assert effective.master == ResolvedFlag(True, "env_on")
    assert effective.skill_suggest == ResolvedFlag(True, "env_on")
    assert effective.literature_check == ResolvedFlag(False, "default")


def test_rule3_store_setting_used_when_env_unset() -> None:
    store = MemoryStore(
        {
            SETTING_MASTER: "true",
            SETTING_SKILL_SUGGEST: "true",
            "experimental.judgment.disclosure_ack": _ack("skill_suggest"),
        }
    )
    effective = resolve(Config(), store)
    assert effective.master == ResolvedFlag(True, "setting")
    assert effective.skill_suggest == ResolvedFlag(True, "setting")


def test_rule4_neither_env_nor_setting_defaults_off() -> None:
    effective = resolve(Config(), MemoryStore())
    assert effective.master == ResolvedFlag(False, "default")
    assert effective.skill_suggest == ResolvedFlag(False, "master_off")


def test_rule5_master_off_disables_every_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI4S_JUDGMENT_SKILL_SUGGEST", "1")
    monkeypatch.setenv("OPENAI4S_JUDGMENT_LITERATURE", "1")
    store = MemoryStore(
        {
            SETTING_SKILL_SUGGEST: "true",
            "experimental.judgment.disclosure_ack": _ack(*CAPABILITIES),
        }
    )
    effective = resolve(Config(), store)
    assert effective.master.enabled is False
    for name in CAPABILITIES:
        flag = getattr(effective, name)
        assert flag.enabled is False
        assert flag.source == "master_off"


def test_rule6_ui_path_requires_current_disclosure() -> None:
    store = MemoryStore(
        {
            SETTING_MASTER: "true",
            SETTING_SKILL_SUGGEST: "true",
        }
    )
    effective = resolve(Config(), store)
    assert effective.master == ResolvedFlag(False, "no_disclosure")
    assert effective.skill_suggest == ResolvedFlag(False, "master_off")

    store_wrong = MemoryStore(
        {
            SETTING_MASTER: "true",
            SETTING_SKILL_SUGGEST: "true",
            "experimental.judgment.disclosure_ack": _ack(
                "skill_suggest", version="1999-01-01"
            ),
        }
    )
    effective_wrong = resolve(Config(), store_wrong)
    assert effective_wrong.master.source == "no_disclosure"
    assert effective_wrong.master.enabled is False


def test_ui_capability_without_capability_ack_is_no_disclosure() -> None:
    store = MemoryStore(
        {
            SETTING_MASTER: "true",
            SETTING_SKILL_SUGGEST: "true",
            "experimental.judgment.disclosure_ack": _ack("literature_check"),
        }
    )
    effective = resolve(Config(), store)
    assert effective.master == ResolvedFlag(True, "setting")
    assert effective.skill_suggest == ResolvedFlag(False, "no_disclosure")


def test_env_on_does_not_require_disclosure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI4S_EXPERIMENTAL_JUDGMENT", "true")
    monkeypatch.setenv("OPENAI4S_JUDGMENT_SKILL_SUGGEST", "yes")
    effective = resolve(Config(), None)
    assert effective.master.enabled is True
    assert effective.skill_suggest.enabled is True
    assert effective.master.source == "env_on"
    assert effective.skill_suggest.source == "env_on"


def test_precedence_tuple_lists_six_rules() -> None:
    assert len(JUDGMENT_FLAG_PRECEDENCE) == 6
    assert JUDGMENT_FLAG_PRECEDENCE[0] == "env_false_forces_off"
    assert JUDGMENT_FLAG_PRECEDENCE[-1] == "ui_path_requires_disclosure"


def test_is_acknowledged_rejects_wrong_shape_and_version() -> None:
    assert is_acknowledged(None, "skill_suggest") is False
    assert is_acknowledged({"version": DISCLOSURE_VERSION}, "skill_suggest") is False
    assert (
        is_acknowledged(
            {"version": "other", "capabilities": ["skill_suggest"]},
            "skill_suggest",
        )
        is False
    )
    assert (
        is_acknowledged(
            {"version": DISCLOSURE_VERSION, "capabilities": ["skill_suggest"]},
            "skill_suggest",
        )
        is True
    )


def test_config_exposes_experimental_judgment() -> None:
    cfg = Config()
    assert isinstance(cfg.experimental_judgment, ExperimentalJudgmentFlags)
    assert cfg.experimental_judgment.master is None
