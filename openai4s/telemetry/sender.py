"""The only code in the tree that transmits telemetry.

It is deliberately dull, and everything interesting about it is a refusal.

`send` will not run without a consent record. It will not accept a payload it
did not get from `wire.seal`. It will not follow a redirect -- a redirect is a
third party choosing where this data goes, and there is no destination worth
reaching that badly. It will not use plain HTTP. It resolves nothing and opens
nothing until all of that holds, because "with no consent, not a single packet
leaves the machine" includes the DNS query: a lookup of log.openai4s.org tells
a resolver that this install exists, which is the fact telemetry is supposed
to ask permission for.

There is no queue that survives a revoke, no retry that outlives one, and no
flush at exit. Buffered-then-flushed telemetry would send events recorded
*before* consent, which is the opposite of what consent means.
"""
from __future__ import annotations

import os
import urllib.request
from typing import Any

from openai4s.telemetry import consent as consent_mod
from openai4s.telemetry.wire import SealedPayload

#: The only endpoint built in. Not configurable except for self-hosting, and
#: the override is validated exactly as strictly.
DEFAULT_ENDPOINT = "https://log.openai4s.org/v1/events"

ENDPOINT_VAR = "OPENAI4S_TELEMETRY_ENDPOINT"

#: A payload larger than this is a bug in the caller, not something to send.
MAX_BODY_BYTES = 64 * 1024

_TIMEOUT_S = 5.0


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect is a third party choosing where research telemetry goes."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def endpoint() -> str | None:
    """Where to send, or None if the configured value is not acceptable.

    HTTPS only, and no credentials in the URL. A downgrade to http:// would put
    the payload -- and the install id -- on the wire in clear text for anyone
    on the path, which is a different privacy promise from the one made.
    """
    raw = (os.environ.get(ENDPOINT_VAR) or DEFAULT_ENDPOINT).strip()
    if not raw.startswith("https://"):
        return None
    if "@" in raw.split("://", 1)[1].split("/", 1)[0]:
        return None
    return raw


def send(store: Any, payload: SealedPayload) -> bool:
    """Transmit one sealed payload. Returns whether it went.

    Every refusal here is silent and returns False. Telemetry that reports its
    own failures loudly trains people to make it work, and making it work is
    not a goal worth a single user-visible error.
    """
    if not isinstance(payload, SealedPayload):
        # Not a defensive nicety: this is the check that makes the sealed type
        # mean something, since only wire.seal can produce one.
        return False
    if len(payload.body) > MAX_BODY_BYTES:
        return False
    if not consent_mod.enabled(store):
        return False
    target = endpoint()
    if target is None:
        return False

    request = urllib.request.Request(
        target,
        data=payload.body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            # No User-Agent beyond the version already inside the payload: a
            # default urllib UA would add a Python build string, which is one
            # more thing about the machine than was agreed to.
            "User-Agent": "openai4s-telemetry",
        },
    )
    opener = urllib.request.build_opener(_NoRedirects)
    try:
        with opener.open(request, timeout=_TIMEOUT_S) as response:
            return 200 <= getattr(response, "status", 0) < 300
    except Exception:  # noqa: BLE001 - a failed report is not the user's problem
        return False


__all__ = ["DEFAULT_ENDPOINT", "ENDPOINT_VAR", "MAX_BODY_BYTES", "endpoint", "send"]
