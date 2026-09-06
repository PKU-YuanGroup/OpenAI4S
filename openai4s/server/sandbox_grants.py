"""Capability grants for artifact bytes served on the sandbox origin.

The Workbench previews model-authored HTML. Executing it on the app's own
origin is the one thing the Artifact policy exists to prevent: a script there
reaches `parent.document`, the session cookie and the whole REST API. So the
preview is served from a *different* origin with a restrictive CSP, keeping
it separate from the embedding Workbench.

The preview must work without a session cookie on the alternate origin. This module is the
answer. The app origin, where the caller *is* authenticated, mints a grant; the
sandbox namespace accepts nothing else.

The signed scope enforces these properties:

* **The token is in the path, not a query or a cookie.** A grant URL is
  ``/sandbox/<token>/preview/<ident>``, so a relative ``<img src="figure.png">``
  inside the document resolves to ``/sandbox/<token>/preview/figure.png`` and
  carries the grant with it. The sandbox namespace sets no cookies and never uses them as authorization.
* **A grant names one frame, one spend origin, and one deadline.** Sibling files resolve because
  they share the frame; nothing else resolves, so a preview of one session's
  report cannot read another session's artifacts even though both are one
  filename lookup apart.

* **The primary document names an immutable version.** Later workspace edits
  cannot change the bytes addressed by an existing preview grant.

The path is a script-readable bearer. CSP blocks resource fetches but not
iframe self-navigation, so it is not a data-exfiltration boundary.

The key is the daemon's own access token, so there is no new secret to store or
rotate: restarting the daemon keeps grants valid exactly as long as the token
that signs them lives.
"""

from __future__ import annotations

import base64
import hmac
import json
import time
from dataclasses import dataclass
from hashlib import sha256
from urllib.parse import urlsplit

#: Path prefix that marks a request as arriving under a grant. Everything below
#: it is artifact bytes and nothing else -- no API, no app shell.
SANDBOX_PREFIX = "/sandbox/"

#: Long enough to open a report and click around it, short enough that a URL
#: copied out of devtools is not a lasting credential. Refreshed per preview.
DEFAULT_TTL_SECONDS = 3600

_SEPARATOR = "."


class GrantError(ValueError):
    """A grant that does not verify. Never says which half failed."""


@dataclass(frozen=True)
class Grant:
    frame_id: str
    app_origin: str
    sandbox_origin: str
    artifact_id: str
    version_id: str


#: The two loopback names, each the other's sandbox. Kept as one table so
#: `origin_pair`, `mint` and the gateway's `_app_origins` cannot drift apart.
LOOPBACK_PAIRS: tuple[tuple[str, str], ...] = (
    ("127.0.0.1", "localhost"),
    ("localhost", "127.0.0.1"),
)


def origin_pair(host: str, port: int) -> tuple[str, str]:
    """Accept only an exact HTTP loopback authority at the listener port.

    The raw ``Host`` header is compared as a whole after one lowercase, so a
    client the gateway's rebind allowlist admits (it lowercases too) is not
    refused here on case alone. Userinfo, a path, brackets, a padded port and
    any other port are refused; on port 80 the portless form is the origin.
    """
    authority = str(host or "").strip().lower()
    suffix = f":{port}" if port != 80 else ""
    for name, other in LOOPBACK_PAIRS:
        if authority in {f"{name}{suffix}", f"{name}:{port}"}:
            return f"http://{name}{suffix}", f"http://{other}{suffix}"
    raise GrantError("invalid preview origin")


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
    app_origin: str,
    artifact_id: str,
    version_id: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: float | None = None,
) -> str:
    """Return a token granting read access to one frame's artifacts.

    The spend origin is not a parameter: it is *derived* from the minting
    origin, so no caller can pair a grant with a sandbox it did not compute.
    """
    if not secret:
        raise GrantError("no signing secret")
    if not all(
        isinstance(value, str) and value.strip()
        for value in (frame_id, artifact_id, version_id)
    ):
        raise GrantError("missing preview scope")
    try:
        parsed = urlsplit(app_origin)
        pair = origin_pair(parsed.netloc, parsed.port or 80)
    except ValueError as error:  # GrantError is a ValueError too
        raise GrantError("invalid preview origin") from error
    if parsed.scheme != "http" or pair[0] != app_origin:
        raise GrantError("invalid preview origin")
    sandbox_origin = pair[1]
    expiry = int((time.time() if now is None else now) + max(1, int(ttl_seconds)))
    payload = _b64(
        json.dumps(
            [2, frame_id, app_origin, sandbox_origin, artifact_id, version_id, expiry],
            separators=(",", ":"),
        )
    )
    return f"{payload}{_SEPARATOR}{_sign(secret, payload)}"


def verify(secret: str, token: str, *, origin: str, now: float | None = None) -> Grant:
    """Return the scope only on its signed spend origin, or raise GrantError.

    Constant-time on the signature, and the signature is checked *before* the
    expiry so a forged token cannot be distinguished from an expired one by
    timing or by message.
    """
    if not secret:
        raise GrantError("no signing secret")
    parts = str(token or "").split(_SEPARATOR)
    if len(str(token or "")) > 8192 or len(parts) != 2:
        raise GrantError("malformed grant")
    payload, signature = parts
    if not signature.isascii() or not hmac.compare_digest(
        _sign(secret, payload), signature
    ):
        raise GrantError("grant does not verify")
    try:
        (
            version,
            frame_id,
            app_origin,
            sandbox_origin,
            artifact_id,
            version_id,
            expiry,
        ) = json.loads(_unb64(payload))
        if (
            version != 2
            or not all(
                isinstance(value, str) and value.strip()
                for value in (
                    frame_id,
                    app_origin,
                    sandbox_origin,
                    artifact_id,
                    version_id,
                )
            )
            or type(expiry) is not int
        ):
            raise ValueError("invalid scope")
    except (ValueError, TypeError, UnicodeDecodeError) as error:
        raise GrantError("malformed grant") from error
    if (time.time() if now is None else now) >= expiry:
        raise GrantError("grant expired")
    if origin != sandbox_origin or origin == app_origin:
        raise GrantError("grant does not verify")
    return Grant(frame_id, app_origin, sandbox_origin, artifact_id, version_id)


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
    "Grant",
    "GrantError",
    "LOOPBACK_PAIRS",
    "SANDBOX_PREFIX",
    "grant_path",
    "mint",
    "origin_pair",
    "split_path",
    "verify",
]
