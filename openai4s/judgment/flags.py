"""Effective experimental-judgment flags: env > Store setting > default off."""

from __future__ import annotations

import json
import logging
import os
from typing import Literal, NamedTuple, Protocol

from openai4s.config import (
    _JUDGMENT_PROVIDERS,
    _STRICT_FALSE_VALUES,
    _STRICT_TRUE_VALUES,
    Config,
    ExperimentalJudgmentFlags,
)
from openai4s.judgment.disclosure import (
    CAPABILITIES,
    disclosure_matches,
    is_acknowledged,
)

log = logging.getLogger("openai4s.judgment.flags")

FlagSource = Literal[
    "env_off",
    "env_on",
    "setting",
    "default",
    "master_off",
    "no_disclosure",
]

JUDGMENT_FLAG_PRECEDENCE: tuple[str, ...] = (
    "env_false_forces_off",
    "env_true_enables",
    "else_store_setting",
    "else_default_off",
    "master_off_disables_capabilities",
    "ui_path_requires_disclosure",
)

SETTING_MASTER = "experimental.judgment.enabled"
SETTING_SKILL_SUGGEST = "experimental.judgment.capabilities.skill_suggest"
SETTING_LITERATURE_CHECK = "experimental.judgment.capabilities.literature_check"
SETTING_TEXT_FEATURES = "experimental.judgment.capabilities.text_features"
SETTING_SAFETY_SHADOW = "experimental.judgment.capabilities.safety_shadow"
SETTING_TASK_MODE_SHADOW = "experimental.judgment.capabilities.task_mode_shadow"
SETTING_PROVIDER = "experimental.judgment.provider"
SETTING_MODEL = "experimental.judgment.model"
SETTING_DISCLOSURE_ACK = "experimental.judgment.disclosure_ack"

SETTING_BY_CAPABILITY: dict[str, str] = {
    "skill_suggest": SETTING_SKILL_SUGGEST,
    "literature_check": SETTING_LITERATURE_CHECK,
    "text_features": SETTING_TEXT_FEATURES,
    "safety_shadow": SETTING_SAFETY_SHADOW,
    "task_mode_shadow": SETTING_TASK_MODE_SHADOW,
}

_ENV_ON_WARNED = False


class JudgmentFlagStore(Protocol):
    """Read-only Store subset used to resolve experimental judgment flags."""

    def get_setting(self, key: str, default: str | None = None) -> str | None: ...


class ResolvedFlag(NamedTuple):
    enabled: bool
    source: FlagSource


class EffectiveJudgmentFlags(NamedTuple):
    master: ResolvedFlag
    skill_suggest: ResolvedFlag
    literature_check: ResolvedFlag
    text_features: ResolvedFlag
    safety_shadow: ResolvedFlag
    task_mode_shadow: ResolvedFlag
    provider: str
    model: str
    timeout_s: float


def _parse_store_bool(raw: str | None) -> bool | None:
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in _STRICT_TRUE_VALUES:
        return True
    if value in _STRICT_FALSE_VALUES:
        return False
    return None


def _store_get(store: JudgmentFlagStore | None, key: str) -> str | None:
    if store is None:
        return None
    return store.get_setting(key)


def _load_ack(store: JudgmentFlagStore | None) -> object:
    raw = _store_get(store, SETTING_DISCLOSURE_ACK)
    if raw is None or not str(raw).strip():
        return None
    try:
        parsed: object = json.loads(raw)
    except ValueError:
        return None
    return parsed


def _resolve_one(env_value: bool | None, setting_value: bool | None) -> ResolvedFlag:
    if env_value is False:
        return ResolvedFlag(False, "env_off")
    if env_value is True:
        return ResolvedFlag(True, "env_on")
    if setting_value is True:
        return ResolvedFlag(True, "setting")
    if setting_value is False:
        return ResolvedFlag(False, "setting")
    return ResolvedFlag(False, "default")


def _warn_env_operator() -> None:
    global _ENV_ON_WARNED
    if _ENV_ON_WARNED:
        return
    _ENV_ON_WARNED = True
    log.warning(
        "OPENAI4S_EXPERIMENTAL_JUDGMENT is enabled via the environment; "
        "the operator is treated as having acknowledged outbound judgment "
        "data. Do not enable this for sensitive data."
    )


def _capability_env(flags: ExperimentalJudgmentFlags, name: str) -> bool | None:
    return {
        "skill_suggest": flags.skill_suggest,
        "literature_check": flags.literature_check,
        "text_features": flags.text_features,
        "safety_shadow": flags.safety_shadow,
        "task_mode_shadow": flags.task_mode_shadow,
    }[name]


def _resolve_provider(cfg: Config, store: JudgmentFlagStore | None) -> str:
    if os.environ.get("OPENAI4S_JUDGMENT_PROVIDER") is not None:
        return cfg.experimental_judgment.provider
    raw = _store_get(store, SETTING_PROVIDER)
    if raw is None or not raw.strip():
        return cfg.experimental_judgment.provider
    value = raw.strip().lower()
    if value not in _JUDGMENT_PROVIDERS:
        allowed = ", ".join(sorted(_JUDGMENT_PROVIDERS))
        raise ValueError(f"invalid {SETTING_PROVIDER}: expected one of {allowed}")
    return value


def _resolve_model(cfg: Config, store: JudgmentFlagStore | None) -> str:
    if os.environ.get("OPENAI4S_JUDGMENT_MODEL") is not None:
        return cfg.experimental_judgment.model
    raw = _store_get(store, SETTING_MODEL)
    if raw is None or not raw.strip():
        return cfg.experimental_judgment.model
    return raw.strip()


def resolve(cfg: Config, store: JudgmentFlagStore | None) -> EffectiveJudgmentFlags:
    """Apply the six precedence rules in ``JUDGMENT_FLAG_PRECEDENCE``."""

    raw = cfg.experimental_judgment
    provider = _resolve_provider(cfg, store)
    ack = _load_ack(store)
    master = _resolve_one(
        raw.master, _parse_store_bool(_store_get(store, SETTING_MASTER))
    )
    if master.enabled and master.source == "setting":
        if not disclosure_matches(ack, provider):
            master = ResolvedFlag(False, "no_disclosure")
    if master.source == "env_on":
        _warn_env_operator()

    resolved: dict[str, ResolvedFlag] = {}
    for name in CAPABILITIES:
        if not master.enabled:
            resolved[name] = ResolvedFlag(False, "master_off")
            continue
        flag = _resolve_one(
            _capability_env(raw, name),
            _parse_store_bool(_store_get(store, SETTING_BY_CAPABILITY[name])),
        )
        if (
            flag.enabled
            and flag.source == "setting"
            and not is_acknowledged(ack, name, provider)
        ):
            flag = ResolvedFlag(False, "no_disclosure")
        resolved[name] = flag

    return EffectiveJudgmentFlags(
        master=master,
        skill_suggest=resolved["skill_suggest"],
        literature_check=resolved["literature_check"],
        text_features=resolved["text_features"],
        safety_shadow=resolved["safety_shadow"],
        task_mode_shadow=resolved["task_mode_shadow"],
        provider=provider,
        model=(cfg.llm.model if provider == "llm" else _resolve_model(cfg, store)),
        timeout_s=float(raw.timeout_s),
    )
