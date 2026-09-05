"""Capability grants for artifact bytes served on the sandbox origin.

The Workbench previews model-authored HTML. Executing it on the app's own
origin is the one thing the Artifact policy exists to prevent: a script there
reaches `parent.document`, the session cookie and the whole REST API. So the
preview is served from a *different* origin, where a script can run because
there is nothing on that origin worth reaching.

A different origin has no session cookie, which is the point and also the
problem: the preview request arrives unauthenticated. This module is the
answer. The app origin, where the caller *is* authenticated, mints a grant; the
sandbox origin accepts nothing else.

Two properties do the work:

* **The token is in the path, not a query or a cookie.** A grant URL is
  ``/sandbox/<token>/preview/<ident>``, so a relative ``<img src="figure.png">``
  inside the document resolves to ``/sandbox/<token>/preview/figure.png`` and
  carries the grant with it. No cookie is set on the sandbox origin at all,
  which is what keeps that origin credential-free.
* **A grant names one frame and one deadline.** Sibling files resolve because
  they share the frame; nothing else resolves, so a preview of one session's
  report cannot read another session's artifacts even though both are one
  filename lookup apart.

The key is the daemon's own access token, so there is no new secret to store or
rotate: restarting the daemon keeps grants valid exactly as long as the token
that signs them lives.
"""

from __future__ import annotations

import base64
import hmac
import time
from hashlib import sha256

#: Path prefix that marks a request as arriving under a grant. Everything below
#: it is artifact bytes and nothing else -- no API, no app shell.
SANDBOX_PREFIX = "/sandbox/"

#: Long enough to open a report and click around it, short enough that a URL
#: copied out of devtools is not a lasting credential. Refreshed per preview.
DEFAULT_TTL_SECONDS = 3600

_SEPARATOR = "."


class GrantError(ValueError):
    """A grant that does not verify. Never says which half failed."""


def _sign(secret: str, payload: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), sha256)
    return base64.urlsafe_b64encode(digest.digest()).decode("ascii").rstrip("=")


def _b64(value: str) -> str:
    raw = base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii")
    return raw.rstrip("=")


def _unb64(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding).decode("utf-8")


def mint(
    secret: str,
    frame_id: str,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: float | None = None,
) -> str:
    """Return a token granting read access to one frame's artifacts."""
    if not secret:
        raise GrantError("no signing secret")
    expiry = int((time.time() if now is None else now) + max(1, int(ttl_seconds)))
    payload = f"{_b64(str(frame_id or ''))}{_SEPARATOR}{expiry}"
    return f"{payload}{_SEPARATOR}{_sign(secret, payload)}"


def verify(secret: str, token: str, *, now: float | None = None) -> str:
    """Return the granted frame id, or raise :class:`GrantError`.

    Constant-time on the signature, and the signature is checked *before* the
    expiry so a forged token cannot be distinguished from an expired one by
    timing or by message.
    """
    if not secret:
        raise GrantError("no signing secret")
    parts = str(token or "").split(_SEPARATOR)
    if len(parts) != 3:
        raise GrantError("malformed grant")
    encoded_frame, encoded_expiry, signature = parts
    payload = f"{encoded_frame}{_SEPARATOR}{encoded_expiry}"
    if not hmac.compare_digest(_sign(secret, payload), signature):
        raise GrantError("grant does not verify")
    try:
        expiry = int(encoded_expiry)
        frame_id = _unb64(encoded_frame)
    except (ValueError, UnicodeDecodeError) as error:
        raise GrantError("malformed grant") from error
    if (time.time() if now is None else now) >= expiry:
        raise GrantError("grant expired")
    return frame_id


def split_path(path: str) -> tuple[str, str]:
    """Split ``/sandbox/<token>/<rest>`` into ``(token, "/<rest>")``.

    Raises :class:`GrantError` rather than returning a partial result, because
    every caller of this is deciding whether to serve bytes.
    """
    if not path.startswith(SANDBOX_PREFIX):
        raise GrantError("not a sandbox path")
    remainder = path[len(SANDBOX_PREFIX) :]
    token, separator, rest = remainder.partition("/")
    if not token or not separator:
        raise GrantError("no grant in path")
    return token, "/" + rest


def grant_path(token: str, ident: str) -> str:
    """The URL path a preview iframe points at, token first."""
    from urllib.parse import quote

    return f"{SANDBOX_PREFIX}{token}/preview/{quote(str(ident), safe='')}"


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "GrantError",
    "SANDBOX_PREFIX",
    "grant_path",
    "mint",
    "split_path",
    "verify",
]
