"""The retrieval provenance a client is allowed to see.

`artifact_versions.source` records where retrieved data came from: the
database, the request URL, the query, when it happened, per-response hashes.
It has never been sent to a client, and it should be — "this figure is built on
data fetched from UniProt at 14:02, and here is the hash of what came back" is
the difference between a plot and a result.

But it cannot be sent as it stands, for three reasons that are each enough on
their own:

1. **A request URL carries credentials.** Plenty of scientific APIs take the
   key in the query string, and this envelope is written by whatever code did
   the retrieval — including a skill nobody has audited. Rendering it raw would
   publish an API key into the UI and into every stored frame that quotes it.
2. **The envelope is open-ended.** It is written as free-form JSON by the
   retrieval site, so "show the envelope" means showing whatever some future
   caller decided to put there. An allowlist is the only version of this that
   stays true as the callers change.
3. **It is unbounded.** A query can be a 200 KB POST body. A panel is not a
   place to discover that.

So: allowlist the keys, bound every value, and redact inside URLs and text
rather than trusting the whole-value check — a key in a query parameter sits in
the middle of a long string, which `redact` alone reads as "not opaque".
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from openai4s.observability import _looks_opaque, fingerprint, redact_text

#: Fields a client may see, with what each is for. Anything else in the
#: envelope is dropped rather than rendered: this is written by retrieval code
#: that is not required to know about this panel, so the safe default when a
#: new key appears is that nobody sees it until somebody adds it here.
ALLOWED_FIELDS: dict[str, str] = {
    "database": "which resource was queried",
    "source": "the label the retrieval gave itself",
    "retrieved_at": "when",
    "request_url": "the exact request, credentials removed",
    "query": "the query terms",
    "normalization_version": "how the response was normalised",
    "response_sha256": "hash of what came back",
    "record_count": "how many records the response held",
}

#: Per-value ceiling. A query can be a large POST body, and a provenance panel
#: is not where anyone should discover that.
MAX_VALUE_CHARS = 2000

#: Query parameters that hold credentials on real scientific APIs. Matched by
#: substring so `apikey`, `api_key` and `X-Api-Key` all land.
CREDENTIAL_PARAMS = ("key", "token", "secret", "password", "auth", "signature", "sig")


def _redact_path(path: str) -> str:
    """Fingerprint any path segment that looks like a credential.

    `redact_text` splits on spaces, and a URL has none — so a key embedded in
    the path (`/v1/sk-live-.../records`) arrives as a single "word" whose
    slashes and dots stop it reading as opaque, and it survives untouched. That
    is not hypothetical: path-style keys are ordinary in scientific APIs, and
    the test for it is what found this. Segments are the right unit because
    each one is exactly the kind of token the opacity check was written for.
    """
    return "/".join(
        f"<redacted:{fingerprint(segment)}>" if _looks_opaque(segment) else segment
        for segment in path.split("/")
    )


def _redact_netloc(netloc: str) -> str:
    """Userinfo is a credential by definition when it has a password."""
    if "@" not in netloc:
        return netloc
    userinfo, _, host = netloc.rpartition("@")
    user, sep, secret = userinfo.partition(":")
    if sep and secret:
        return f"{user}:<redacted:{fingerprint(secret)}>@{host}"
    if _looks_opaque(userinfo):
        return f"<redacted:{fingerprint(userinfo)}>@{host}"
    return netloc


def _redact_url(raw: str) -> str:
    """Return the URL with credential-bearing query parameters fingerprinted.

    The parameter is kept and its *value* replaced, because "which parameters
    were sent" is provenance and the value is the secret. Dropping the
    parameter entirely would quietly change what the URL claims to have been.
    """
    try:
        parts = urlsplit(raw)
    except ValueError:
        return "<unparseable url>"
    # No early return for a URL without a query. An earlier version had one,
    # and it meant the path and userinfo redaction below never ran for exactly
    # the URLs that carry a path-style key -- which is the shape that has no
    # query by construction. The test for it is what found that.
    cleaned = []
    for name, value in parse_qsl(parts.query, keep_blank_values=True):
        if value and any(bit in name.lower() for bit in CREDENTIAL_PARAMS):
            cleaned.append((name, f"<redacted:{fingerprint(value)}>"))
        else:
            cleaned.append((name, value))
    rebuilt = urlunsplit(
        (
            parts.scheme,
            _redact_netloc(parts.netloc),
            _redact_path(parts.path),
            urlencode(cleaned),
            parts.fragment,
        )
    )
    return rebuilt


def _clip(value: str) -> tuple[str, bool]:
    if len(value) <= MAX_VALUE_CHARS:
        return value, False
    return value[:MAX_VALUE_CHARS], True


def public_source(envelope: Any) -> dict[str, Any] | None:
    """The client-safe projection of one version's retrieval provenance.

    Returns None when there is nothing to show, which is the common case: most
    artifacts are computed rather than retrieved, and an empty panel that says
    "no provenance" is worse than no panel, because it reads as a finding.
    """
    if isinstance(envelope, str):
        try:
            envelope = json.loads(envelope)
        except (TypeError, ValueError):
            return None
    if not isinstance(envelope, dict) or not envelope:
        return None

    out: dict[str, Any] = {}
    truncated: list[str] = []
    for field in ALLOWED_FIELDS:
        if field not in envelope:
            continue
        value = envelope[field]
        if value is None or value == "":
            continue
        if isinstance(value, (int, float, bool)):
            out[field] = value
            continue
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        text = _redact_url(text) if field == "request_url" else redact_text(text)
        text, was_clipped = _clip(text)
        if was_clipped:
            truncated.append(field)
        out[field] = text

    if not out:
        return None
    # Named, not implied. A field silently cut at 2000 characters reads as the
    # whole value, and a provenance panel that shows a shortened URL as if it
    # were the request is worse than showing nothing.
    if truncated:
        out["truncated_fields"] = sorted(truncated)
    # What was dropped, counted rather than listed: the names themselves come
    # from unaudited retrieval code and are not safe to render.
    dropped = len([k for k in envelope if k not in ALLOWED_FIELDS])
    if dropped:
        out["undisclosed_field_count"] = dropped
    return out


__all__ = [
    "ALLOWED_FIELDS",
    "CREDENTIAL_PARAMS",
    "MAX_VALUE_CHARS",
    "public_source",
]
