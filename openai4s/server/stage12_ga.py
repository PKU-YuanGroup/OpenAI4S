"""Stage 12 GA kill switch and rollout declaration.

This flag does not turn earlier stages on. It records that the operator has
accepted the frozen rollout order and keeps a single kill switch that returns
the product to default-off Auto Mode.
"""

from __future__ import annotations

from typing import Any

from openai4s.server.auto_budget import inspect_budget_wiring

ROLLOUT_PHASES = (
    "shadow",
    "opt_in",
    "project_default",
    "default_on_candidate",
    "ga",
)

_OWN_FLAG = "stage12_auto_mode_ga"


def official_stage12_enabled(config: Any) -> bool:
    flags = getattr(config, "roadmap_features", None)
    return bool(flags is not None and getattr(flags, _OWN_FLAG, False))


def earlier_flags(config: Any) -> dict[str, bool]:
    flags = getattr(config, "roadmap_features", None)
    if flags is None:
        return {}
    names = getattr(flags, "__dataclass_fields__", {})
    return {
        name: bool(getattr(flags, name, False)) for name in names if name != _OWN_FLAG
    }


def rollout_status(config: Any) -> dict[str, Any]:
    enabled = official_stage12_enabled(config)
    previous = earlier_flags(config)
    inventory = inspect_budget_wiring()
    ga_ready = bool(inventory.get("ga_ready"))
    blocked_on: list[str] = []
    if inventory.get("missing_authorities"):
        blocked_on.append("budget_authority_missing")
    if inventory.get("duplicate_authorities"):
        blocked_on.append("budget_authority_duplicate")
    # missing_sinks includes UNWIRED_CONSUMERS keys: naming a hole is not
    # GA-ready. inspect_budget_wiring fail-closes that inventory.
    missing_sinks = inventory.get("missing_sinks") or []
    if missing_sinks or inventory.get("sink_bypass_count"):
        blocked_on.append("budget_sink_unwired")
    return {
        "ga_kill_switch_armed": enabled,
        "auto_mode_default": "off",
        "phases": list(ROLLOUT_PHASES),
        "active_phase": "ga" if enabled and ga_ready else "shadow",
        "earlier_flags_remain_opt_in": True,
        "earlier_flags": previous,
        "any_earlier_flag_on": any(previous.values()),
        "auto_budget": inventory,
        "ga_blocked_on": blocked_on,
        "ga_refused": bool(blocked_on),
    }
