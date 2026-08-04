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
from typing import Any, Callable

from openai4s.telemetry import consent as consent_mod
from openai4s.telemetry import gate
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


def _default_transport(request: urllib.request.Request) -> int:
    """Open the socket. The only place in the tree that does, for telemetry."""
    opener = urllib.request.build_opener(_NoRedirects)
    with opener.open(request, timeout=_TIMEOUT_S) as response:
        return int(getattr(response, "status", 0))


def send(
    store: Any,
    payload: SealedPayload,
    *,
    transport: Callable[[urllib.request.Request], int] | None = None,
) -> bool:
    """Transmit one sealed payload. Returns whether it went.

    Every refusal here is silent and returns False. Telemetry that reports its
    own failures loudly trains people to make it work, and making it work is
    not a goal worth a single user-visible error.

    ``transport`` replaces the socket, and nothing above it: the consent row,
    the sealed-payload type, the install-id comparison and the endpoint
    validation all still run exactly as they do in production, which is the
    only reason the seam is worth having. It exists because the benchmark has
    to drive the identity gate — the thing this module is *for* — and the only
    honest way to observe that gate is to run the real refusals against a
    transport that goes nowhere. The alternative was for the caller to reach in
    and swap ``urllib.request.build_opener``, which is what it used to do, and
    which put an outbound primitive in a module that has no business naming one
    (see ``tests/test_egress_surface.py``).

    It grants no reach: ``endpoint()`` still decides the destination, and a
    caller able to pass this is already inside the process.
    """
    if not isinstance(payload, SealedPayload):
        # Not a defensive nicety: this is the check that makes the sealed type
        # mean something, since only wire.seal can produce one.
        return False
    if len(payload.body) > MAX_BODY_BYTES:
        return False
    # The authorisation check and the socket open are one critical section.
    # Read consent here and open the socket after releasing, and a revoke that
    # lands in between returns to its caller while this payload — sealed under
    # the identity it just destroyed — is still on its way to the opener. The
    # barrier is the same lock `consent.revoke` takes, so the two cannot
    # interleave: either this send begins before the revoke, or it re-reads a
    # row that is gone and refuses.
    with gate.transmitting():
        return _send_locked(store, payload, transport)


def _send_locked(
    store: Any,
    payload: SealedPayload,
    transport: Callable[[urllib.request.Request], int] | None = None,
) -> bool:
    current = consent_mod.read(store)
    if current is None:
        return False
    # Not "is telemetry enabled" but "is *this identity* still the one that was
    # authorised". The payload was sealed on another thread, at an earlier
    # moment; a revoke since then destroys the id it was stamped with, and a
    # re-grant after the revoke mints a different one. Checking only that some
    # consent exists would send the old id under the new permission — linking
    # two participation periods that revocation promised were unlinkable, which
    # is precisely the property the id-inside-the-consent-record design exists
    # to provide.
    if not payload.install_id or payload.install_id != current.install_id:
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
    try:
        # The comparison stays *inside* the try, which is where it was before
        # the seam existed. Moved out, a transport returning something that is
        # not an int raised TypeError out of `send` — and this module's promise
        # is that every refusal here is silent. A failure to report is never
        # worth an exception in the caller's path.
        return 200 <= (transport or _default_transport)(request) < 300
    except Exception:  # noqa: BLE001 - a failed report is not the user's problem
        return False


__all__ = ["DEFAULT_ENDPOINT", "ENDPOINT_VAR", "MAX_BODY_BYTES", "endpoint", "send"]
