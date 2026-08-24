"""Canonical server-owned URLs for durable resources.

Resource links are part of the HTTP contract.  Callers must not duplicate the
API prefix or interpolate an identifier as a path fragment: either mistake can
produce a message that looks delivered while its link is unroutable (or points
at more than one segment).
"""

from __future__ import annotations

from urllib.parse import quote

from openai4s.server.contract import API_ROOT

_LEGACY_API_ROOT = "/api"


def artifact_version_url(version_id: str) -> str:
    """Return the canonical URL for one immutable Artifact version.

    ``safe=""`` is deliberate.  A version identifier is exactly one path
    segment even if a future/imported identifier contains ``/``, Unicode, or a
    URL metacharacter.  Empty identifiers are rejected because falling back to
    an Artifact id or filename would turn an immutable completion link into a
    mutable or ambiguous one.
    """
    if not isinstance(version_id, str) or not version_id:
        raise ValueError("artifact version_id must be a non-empty string")
    # WHATWG URL clients normalise both literal and percent-encoded dot-only
    # path segments before sending the request.  Such an imported identifier
    # therefore cannot be represented by this path contract without changing
    # its identity; refuse it instead of publishing a URL for another route.
    if version_id in {".", ".."}:
        raise ValueError("artifact version_id cannot be a dot path segment")
    segment = quote(version_id, safe="")
    # Keep exact-version addressing in a reserved sub-path.  The compatible
    # ``/artifacts/{ident}`` reader also accepts Artifact ids and (uniquely)
    # filenames; using that ambiguous namespace for a completion link meant a
    # missing version could silently fall through to unrelated bytes whose
    # filename happened to equal the version id.  ``versions/`` cannot be a
    # legacy one-segment identifier, so the gateway can fail closed.
    return f"{API_ROOT}/artifacts/versions/{segment}"


def completion_artifact_url(
    *,
    artifact_id: object = None,
    filename: object = None,
    version_id: object = None,
    trusted_delivery: bool = False,
) -> str | None:
    """Build a completion link under the selected rollout contract.

    The flag-off branch exactly preserves the pre-Stage-1 route and fallback
    to a filename.  Once trusted delivery is enabled, only an exact immutable
    version is linkable; a missing version returns no URL instead of silently
    weakening the guarantee to a mutable Artifact head.
    """
    if trusted_delivery:
        if not isinstance(version_id, str) or not version_id:
            return None
        return artifact_version_url(version_id)
    legacy_ident = artifact_id or filename
    if legacy_ident is None:
        legacy_ident = ""
    return f"{_LEGACY_API_ROOT}/artifacts/{quote(str(legacy_ident), safe='')}"


__all__ = ["artifact_version_url", "completion_artifact_url"]
