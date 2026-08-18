"""Stage 7 Guardian enforcement for unattended ``ask`` resolutions.

Only ``allow_once`` is permitted, and only when the exact-action shadow
assessment is ``shadow_allow``, the tool is not dangerous, and no hard deny
applies. Guardian still cannot create a standing allow.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from openai4s.server.guardian_shadow import assess_shadow, exact_action_envelope

_TRUE = frozenset({"1", "true", "yes", "on", "auto_review"})


def feature_enabled(config: Any | None = None) -> bool:
    if config is not None:
        flags = getattr(config, "roadmap_features", None)
        if flags is not None:
            return bool(getattr(flags, "stage7_guardian_enforcement", False))
    return os.environ.get(
        "OPENAI4S_STAGE7_GUARDIAN_ENFORCEMENT", ""
    ).strip().lower() in {"1", "true", "yes", "on"}


def auto_review_requested(config: Any | None = None) -> bool:
    env = os.environ.get("OPENAI4S_UNATTENDED_APPROVAL", "deny").strip().lower()
    if env == "auto_review":
        return True
    if config is not None:
        auto = getattr(config, "auto_mode", None)
        if getattr(auto, "approvals_reviewer", "") == "auto_review":
            return True
    return False


def decide_unattended(
    payload: Mapping[str, Any],
    *,
    config: Any | None = None,
    expected_digest: str | None = None,
) -> tuple[bool, str] | None:
    """Return (allow, message) or None to keep the legacy unattended path."""

    if not feature_enabled(config) or not auto_review_requested(config):
        return None
    envelope = exact_action_envelope(
        tool=str(payload.get("tool") or ""),
        target=str(payload.get("target") or ""),
        canonical_arguments=payload.get("input"),
        side_effect_class=str(payload.get("side_effect_class") or ""),
        resource_keys=list(payload.get("resource_keys") or ()),
        dangerous=bool(payload.get("dangerous")),
    )
    assessment = assess_shadow(
        envelope,
        expected_digest=expected_digest,
        requested_scope="once",
        hard_deny=False,
    )
    if (
        assessment.get("outcome") == "shadow_allow"
        and assessment.get("executes") is False
        and assessment.get("standing_allow") is False
        and not envelope["dangerous"]
        and not assessment.get("fail_closed")
    ):
        return True, "guardian allow_once for exact action"
    return False, str(assessment.get("rationale") or "guardian denied")
