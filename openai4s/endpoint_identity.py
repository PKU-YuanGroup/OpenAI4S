"""One spelling for an LLM endpoint, and one place that strips its secrets.

Two questions were being answered by three private helpers, none of which the
profile writer could reach:

* *Is this the same endpoint?* `capabilities._normalize_endpoint` trims a
  trailing slash so a capability lookup is stable. Profiles compared the raw
  strings, so `https://h/v1` and `https://h/v1/` were two revisions of one
  configuration.
* *May this string be shown?* `doctor._sanitize_endpoint` drops userinfo and
  query -- its docstring says so in as many words -- and it had exactly one call
  site, in the diagnostics report. A profile's `base_url` was stored verbatim,
  published verbatim by `GET /model-profiles`, and frozen verbatim into an
  immutable revision. Measured: `https://user:s3cr3t@api.internal.corp/v1?key=abc`
  came back from the public projection with the password in it, and the sealed
  revision kept it forever.

That is plan section 7.2's "secrets do not enter the snapshot" being violated
through the endpoint field rather than the key field. The API key goes through
the secret broker; the credentials someone puts in a URL went nowhere near it.

So the stored form is the normalised, credential-free one. Nothing is redacted
at display time, because a value that was never stored cannot leak from a
surface nobody remembered to redact.
"""

from __future__ import annotations

import urllib.parse

__all__ = ["normalize_endpoint", "endpoint_credentials"]


def normalize_endpoint(url: str | None) -> str:
    """The canonical, credential-free spelling of an endpoint.

    Scheme, host, port and path are the routing detail; userinfo and query are
    not, and both are places a credential is put. A trailing slash is dropped so
    two spellings of one endpoint compare equal -- which is what makes an
    immutable revision mean "the same configuration" rather than "the same
    string".

    Returns `""` for an empty input and leaves an unparseable one alone rather
    than inventing a value: a caller storing it will fail its own validation,
    which is a better failure than a silently rewritten endpoint.
    """
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError:
        return text
    if not parts.scheme or not parts.hostname:
        # Not a URL this can reason about -- a bare host, say. Left as typed.
        return text
    host = parts.hostname
    if parts.port:
        host = f"{host}:{parts.port}"
    path = parts.path.rstrip("/")
    return urllib.parse.urlunsplit((parts.scheme, host, path, "", ""))


def endpoint_credentials(url: str | None) -> bool:
    """Whether this endpoint carries userinfo or a query string.

    Separate from the normalisation so a caller can *tell the user* that part of
    what they typed was dropped. Silently removing a query parameter someone
    believed was load-bearing is its own kind of wrong answer.
    """
    text = str(url or "").strip()
    if not text:
        return False
    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError:
        return False
    return bool(parts.username or parts.password or parts.query)
