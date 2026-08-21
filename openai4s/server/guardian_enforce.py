"""Stage 7 Guardian enforcement for unattended ``ask`` resolutions.

Only ``allow_once`` is permitted, and only when the exact-action shadow
assessment is ``shadow_allow``, the tool is not dangerous, and no hard deny
applies. Guardian still cannot create a standing allow.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from openai4s.host.files import is_credential_path
from openai4s.server.guardian_shadow import assess_shadow, exact_action_envelope

_TRUE = frozenset({"1", "true", "yes", "on", "auto_review"})

# Permission targets are not uniformly paths: glob/grep target their pattern,
# and web_download targets its domain. Only inspect the argument that the file
# tool will actually resolve or open.
_FILE_PATH_ARGUMENTS = {
    "read_file": "path",
    "write_file": "path",
    "edit_file": "path",
    "glob": "path",
    "grep": "path",
    "list_dir": "path",
    "web_download": "path",
    "save_artifact": "path",
    "materialise_artifact": "filename",
}
_DIRECT_PATH_TARGET_TOOLS = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "list_dir",
        "save_artifact",
    }
)
# These tools write to a path that is not their permission target. If their
# canonical arguments do not expose that path, an unattended reviewer cannot
# safely infer it from the domain/version target and must fail closed.
_PATH_REQUIRED_FOR_REVIEW = frozenset({"web_download", "materialise_artifact"})
# ``grep`` discovers and opens files only after approval. A base directory is
# not enough to apply the unattended basename tier to every eventual read, so
# its data-dependent file set needs a human review.
_DYNAMIC_FILE_READ_TOOLS = frozenset({"grep"})


def _file_path_argument(
    tool: str,
    canonical_arguments: Any,
    *,
    target: str,
) -> str | None:
    key = _FILE_PATH_ARGUMENTS.get(tool)
    if key is None:
        return None
    arguments = canonical_arguments
    if isinstance(arguments, (list, tuple)):
        arguments = arguments[0] if arguments else None
    if isinstance(arguments, Mapping):
        value = arguments.get(key)
        if value not in (None, ""):
            return str(value)
    # These tools use their path itself as the permission target, so it is a
    # safe fail-closed fallback when canonical arguments are missing/malformed.
    # Never do this for glob/grep (pattern targets) or web_download (domain).
    if tool in _DIRECT_PATH_TARGET_TOOLS and target:
        return target
    return None


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


def unattended_file_deny_reason(
    *,
    tool: str,
    target: str,
    canonical_arguments: Any = None,
    resolved_file_path: str | None = None,
    resolved_file_is_credential: bool = False,
) -> str | None:
    """Return the unattended file fence reason, independent of rule scope.

    Default workspace rules intentionally allow routine file tools. This fence
    must therefore run before an ``allow`` rule as well as while resolving an
    ``ask``; otherwise the default policy bypasses the Guardian entirely.
    """
    file_path = _file_path_argument(
        tool,
        canonical_arguments,
        target=target,
    )
    # A successful Host resolution is workspace-relative and authoritative.
    # Falling back to a raw absolute spelling would reapply credential-shaped
    # parent segments above an explicitly trusted workspace root.
    reviewed_path = resolved_file_path if resolved_file_path is not None else file_path
    if resolved_file_is_credential or (
        reviewed_path is not None and is_credential_path(reviewed_path)
    ):
        return "unattended credential policy denied access to a credential path"
    if tool in _PATH_REQUIRED_FOR_REVIEW and file_path is None:
        return "unattended file policy denied access without a reviewable path"
    if tool in _DYNAMIC_FILE_READ_TOOLS:
        return "unattended file policy denied data-dependent file search"
    return None


def decide_unattended(
    payload: Mapping[str, Any],
    *,
    canonical_arguments: Any = None,
    resolved_file_path: str | None = None,
    resolved_file_is_credential: bool = False,
    config: Any | None = None,
    expected_digest: str | None = None,
) -> tuple[bool, str] | None:
    """Return (allow, message) or None to keep the legacy unattended path."""

    if not feature_enabled(config) or not auto_review_requested(config):
        return None
    arguments = (
        canonical_arguments if canonical_arguments is not None else payload.get("input")
    )
    envelope = exact_action_envelope(
        tool=str(payload.get("tool") or ""),
        target=str(payload.get("target") or ""),
        canonical_arguments=arguments,
        side_effect_class=str(payload.get("side_effect_class") or ""),
        resource_keys=list(payload.get("resource_keys") or ()),
        dangerous=bool(payload.get("dangerous")),
    )
    file_deny_reason = unattended_file_deny_reason(
        tool=envelope["tool"],
        target=envelope["target"],
        canonical_arguments=arguments,
        resolved_file_path=resolved_file_path,
        resolved_file_is_credential=resolved_file_is_credential,
    )
    assessment = assess_shadow(
        envelope,
        expected_digest=expected_digest,
        requested_scope="once",
        hard_deny=file_deny_reason is not None,
        hard_deny_reason=file_deny_reason,
    )
    if file_deny_reason is not None and assessment.get("outcome") == "shadow_deny":
        return False, file_deny_reason
    if (
        assessment.get("outcome") == "shadow_allow"
        and assessment.get("executes") is False
        and assessment.get("standing_allow") is False
        and not envelope["dangerous"]
        and not assessment.get("fail_closed")
    ):
        return True, "guardian allow_once for exact action"
    return False, str(assessment.get("rationale") or "guardian denied")
