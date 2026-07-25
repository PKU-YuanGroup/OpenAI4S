"""The HTTP failure type and its stable machine codes.

Its own module, and not because gateway.py is long. ``GatewayError`` is defined
around line 5870 of gateway.py, some 5,800 lines below that file's own import
block, so a sibling module that does the natural
``from openai4s.server.gateway import GatewayError`` at module scope hits a
circular import and the daemon fails at *boot* rather than at request time.
Every route module carved out of ``Handler._api`` needs to raise this type, so
the cycle would have been discovered once per extraction. It lives here
instead, and gateway re-exports it so existing importers keep working.
"""
from __future__ import annotations

# Stable, machine-readable error codes. A client that has to match on English
# prose is coupled to wording nobody thinks of as an interface, so it breaks the
# first time a message is improved. Status alone is too coarse: several distinct
# failures share 400, and a client retrying "invalid cursor" the way it retries
# "rate limited" is a bug the contract should prevent.
ERROR_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "unprocessable",
    423: "locked",
    429: "rate_limited",
    500: "internal_error",
    503: "unavailable",
}


def error_code_for(status: int) -> str:
    return ERROR_CODES.get(int(status), "error" if status < 500 else "internal_error")


class GatewayError(Exception):
    """An HTTP failure with a status, a human message, and an optional stable
    machine code. ``error_code`` overrides the status-derived default when a
    single status covers genuinely different failures a client must tell
    apart."""

    def __init__(self, code: int, message: str, error_code: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.error_code = error_code


def gateway_error_payload(error: GatewayError) -> dict:
    """The response body a raised GatewayError becomes.

    Shared with the contract capture, which has to reproduce the dispatcher's
    conversion exactly: a second copy of this two-line shape is how a captured
    error contract comes to describe a body the server does not send.
    """
    payload = {"error": error.message}
    if error.error_code:
        payload["code"] = error.error_code
    return payload


def public_failure(payload: object, status: int, request_id: str | None) -> object:
    """The body an error response actually carries.

    Enrichment is deliberately ADDITIVE: ``error`` keeps the human message it
    always had, so a consumer reading ``j.error`` -- including this repo's own
    ``app.js`` -- is unaffected. Success bodies are returned untouched, and so
    is a 2xx body that merely happens to contain an ``error`` key: a job result
    describing a prior failure is data, not an error envelope.

    This lives here rather than inline in ``Handler._json`` because the shape
    had grown a second definition, which is precisely what
    ``gateway_error_payload`` above warns about -- arriving by a different
    route than a copied literal. ``response_capture`` observes the body
    *before* the dispatcher enriches it, so the frozen artifacts recorded a
    body the server does not send: ``request_id`` appears nowhere in either
    ``docs/response-schemas.json`` or ``docs/response-contract.json``. One
    callable both the dispatcher and the capture can reach is what lets those
    two stop disagreeing.
    """
    if status < 400 or not isinstance(payload, dict) or "error" not in payload:
        return payload
    return {
        **payload,
        "code": payload.get("code") or error_code_for(status),
        # Never clobber a value the route deliberately set. `code` has always
        # deferred this way; `status` did not, and the difference was invisible
        # because the contract capture observed bodies *before* enrichment.
        # `POST /frames/<id>/recovery/actions/restart_fresh` returns its whole
        # domain result with HTTP 409 when the action fails, and that result
        # carries its own `status` ("failed", "partial", ...) -- which the
        # envelope silently replaced with the integer 409. The HTTP status is
        # never lost by deferring: it is on the status line, and `code` names
        # it. A destroyed domain field has no such second copy.
        "status": payload.get("status", status),
        "request_id": request_id or None,
    }


__all__ = [
    "ERROR_CODES",
    "GatewayError",
    "error_code_for",
    "gateway_error_payload",
    "public_failure",
]
