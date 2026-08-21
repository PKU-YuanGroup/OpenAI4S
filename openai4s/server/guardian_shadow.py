"""Stage 6 Permission Guardian shadow adjudication.

Shadow records what Guardian would have decided on an exact action envelope.
It never executes the action, never creates a standing allow, and never
overrides sandbox/egress/secret/cost hard denies. Hash mismatch fails closed.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from typing import Any

POLICY_VERSION = "guardian-shadow-v1"
_TRUE = frozenset({"1", "true", "yes", "on"})


def feature_enabled(config: Any | None = None) -> bool:
    if config is not None:
        flags = getattr(config, "roadmap_features", None)
        if flags is not None:
            return bool(getattr(flags, "stage6_guardian_shadow", False))
    return (
        os.environ.get("OPENAI4S_STAGE6_GUARDIAN_SHADOW", "").strip().lower() in _TRUE
    )


def exact_action_envelope(
    *,
    tool: str,
    target: str | None = None,
    cwd: str | None = None,
    canonical_arguments: Any = None,
    side_effect_class: str | None = None,
    resource_keys: list[str] | None = None,
    dangerous: bool = False,
    http_method: str | None = None,
    domain: str | None = None,
) -> dict[str, Any]:
    """Normalize one permission-bound action for hashing."""

    return {
        "tool": str(tool or ""),
        "target": str(target or ""),
        "cwd": str(cwd or ""),
        "canonical_arguments": (
            canonical_arguments if canonical_arguments is not None else []
        ),
        "side_effect_class": str(side_effect_class or ""),
        "resource_keys": list(resource_keys or ()),
        "dangerous": bool(dangerous),
        "http_method": str(http_method or ""),
        "domain": str(domain or ""),
    }


def envelope_digest(envelope: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(envelope),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def assess_shadow(
    envelope: Mapping[str, Any],
    *,
    expected_digest: str | None = None,
    requested_scope: str = "once",
    hard_deny: bool = False,
    hard_deny_reason: str | None = None,
) -> dict[str, Any]:
    """Return a non-executing Guardian judgment."""

    digest = envelope_digest(envelope)
    if expected_digest and expected_digest != digest:
        return {
            "outcome": "failed",
            "risk": "unknown",
            "user_authorization": "none",
            "rationale": "action hash mismatch",
            "action_digest": digest,
            "expected_digest": expected_digest,
            "standing_allow": False,
            "executes": False,
            "fail_closed": True,
        }
    if requested_scope != "once":
        return {
            "outcome": "deny",
            "risk": "high",
            "user_authorization": "insufficient",
            "rationale": "Guardian cannot create a standing allow",
            "action_digest": digest,
            "standing_allow": False,
            "executes": False,
            "fail_closed": True,
        }
    if hard_deny or hard_deny_reason or envelope.get("dangerous"):
        outcome = "shadow_deny"
        risk = "critical" if hard_deny or hard_deny_reason else "high"
    else:
        outcome = "shadow_allow"
        risk = "low"
    result = {
        "outcome": outcome,
        "risk": risk,
        "user_authorization": "none",
        "rationale": (
            hard_deny_reason or "shadow adjudication does not execute the action"
        ),
        "action_digest": digest,
        "standing_allow": False,
        "executes": False,
        "fail_closed": False,
        "policy_version": POLICY_VERSION,
    }
    if hard_deny_reason:
        # The shadow records that Guardian cannot override a deterministic
        # policy boundary; the boundary remains the decision source.
        result["decision_source"] = "deterministic_policy"
    return result


def maybe_record_shadow(
    store: Any,
    request: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    config: Any | None = None,
    canonical_arguments: Any = None,
    hard_deny: bool = False,
    hard_deny_reason: str | None = None,
) -> dict[str, Any] | None:
    """Best-effort shadow record after a durable ask is created."""

    if not feature_enabled(config):
        return None
    envelope = exact_action_envelope(
        tool=str(payload.get("tool") or request.get("tool") or ""),
        target=str(payload.get("target") or request.get("target") or ""),
        canonical_arguments=(
            canonical_arguments
            if canonical_arguments is not None
            else request.get("canonical_arguments") or payload.get("input")
        ),
        side_effect_class=str(
            payload.get("side_effect_class") or request.get("side_effect_class") or ""
        ),
        resource_keys=list(
            payload.get("resource_keys") or request.get("resource_keys") or ()
        ),
        dangerous=bool(payload.get("dangerous") or request.get("dangerous")),
    )
    expected = request.get("expected_envelope_digest")
    assessment = assess_shadow(
        envelope,
        expected_digest=str(expected) if expected else None,
        hard_deny=hard_deny,
        hard_deny_reason=hard_deny_reason,
    )
    decision_id = str(request.get("decision_id") or "")
    if decision_id and hasattr(store, "set_setting"):
        store.set_setting(
            f"guardian-shadow:{decision_id}",
            json.dumps(assessment, ensure_ascii=False),
        )
    return assessment
