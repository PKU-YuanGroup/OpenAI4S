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

import os

from openai4s.observability import correlation_id, log_event, redact_text

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


#: The only sentence an unrecognised exception is allowed to say in public.
#:
#: ``public_failure`` above is an *envelope*: it decorates whatever message the
#: payload already carries and deliberately never rewrites it, because those
#: messages are author-written and are the product. That left one hole it
#: cannot close by itself -- an ``except Exception`` that put ``str(e)`` into
#: the payload got the same envelope treatment, so a ``PermissionError`` naming
#: an absolute path, an ``OSError`` quoting the shell command it failed to
#: spawn, or an SDK error echoing an ``Authorization`` header was shipped to
#: the client with a tidy ``code`` bolted on. The fix is a *projector* at the
#: exception boundary, not a filter in the envelope: a filter would have to
#: guess which messages are safe, and it would guess wrong in the direction
#: that destroys the good ones.
INTERNAL_ERROR_MESSAGE = "internal error"

#: Long enough to identify a failure, short enough that a diagnostic cannot
#: become a channel for the data the exception was carrying.
_DIAGNOSTIC_CHARS = 600


def _redacted_detail(exc: BaseException | str) -> str:
    """What the operator-side record holds instead of the raw exception.

    ``redact_text`` fingerprints credential-shaped tokens but deliberately
    leaves paths alone -- it is written for a log a human has to be able to
    read, and a log with no paths in it is useless for the thing it exists for.
    A diagnostic outlives the request and is shipped in the support bundle, so
    on top of that the home directory is collapsed to ``~``: of an absolute
    path, the username is the part that identifies a person rather than a file.
    """
    subject = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
    text = redact_text(subject)
    home = os.path.expanduser("~")
    if home and home not in ("", "/"):
        text = text.replace(home, "~")
    return text[:_DIAGNOSTIC_CHARS]


def record_diagnostic(
    exc: BaseException, *, surface: str, request_id: str | None = None
) -> dict:
    """Put the original failure where an operator can reach it and a client cannot.

    The structured logger is the sink rather than a new in-process buffer: it
    already redacts field-wise, it already lands in the file
    ``diagnostics.build_bundle`` collects, and it is not served over HTTP. A
    second store would be a second thing to redact and a second thing to
    forget.

    Returns the emitted record so the projector can tie the public body and
    this line together by ``request_id`` -- that pairing is the whole reason
    the generic message is tolerable.
    """
    return log_event(
        "unhandled_exception",
        surface=str(surface or "unknown"),
        exception=type(exc).__name__,
        detail=_redacted_detail(exc),
        request_id=str(request_id or correlation_id() or ""),
    )


def public_exception(
    exc: BaseException,
    *,
    surface: str,
    request_id: str | None = None,
    status: int = 500,
    error_code: str | None = None,
) -> tuple[dict, int]:
    """The one projector: an exception in, a safe public body and status out.

    Every surface that used to answer with ``str(e)`` -- the HTTP dispatcher's
    catch-all, the three job spawners, the turn's ``text_chunk`` over the
    WebSocket, the connector call, the remote-compute refresh -- goes through
    here, so they cannot disagree about what a failure is allowed to reveal.

    A ``GatewayError`` passes its message through untouched. That is not an
    exemption: its message is a literal someone wrote for a client to read, and
    the whole class exists to distinguish "this is what happened, and you may
    know" from "something threw". Anything else is unknown provenance and gets
    ``INTERNAL_ERROR_MESSAGE`` plus a stable code; the original goes to
    ``record_diagnostic`` alone.

    The returned body is already enriched, because these surfaces are not all
    HTTP -- a job result read back over a 200 and a chunk streamed over the
    WebSocket never reach ``Handler._json``. Re-enriching in ``_json`` is
    harmless: ``public_failure`` defers to a value that is already set.
    """
    # The id is the client's half of the pair, and it is *local*: it comes from
    # this daemon's correlation context, never from an upstream provider's
    # response. A provider's id names a request the user cannot look up and
    # this daemon cannot either, so quoting one in a support ticket wastes the
    # one field the ticket has.
    request_id = str(request_id or correlation_id() or "")
    if isinstance(exc, GatewayError):
        return _enriched(gateway_error_payload(exc), exc.code, request_id), exc.code
    record_diagnostic(exc, surface=surface, request_id=request_id)
    status = int(status)
    payload = {
        "error": INTERNAL_ERROR_MESSAGE,
        "code": error_code or error_code_for(status),
    }
    # The retry veto, carried out to the client.
    #
    # `openai4s/llm/models.py` defines `output_committed` and says what it
    # decides: "Once a stream has handed bytes to the caller -- or a tool has
    # run -- a transparent retry would duplicate visible output or re-fire a
    # side effect, so it is never safe regardless of how retryable the status
    # looks." It was set on the exception and read by nobody outside the LLM
    # layer, so the one fact that decides whether a retry is safe never reached
    # the surface that offers the retry. A 502 looks retryable; a 502 after a
    # tool has already run is not, and nothing said so.
    #
    # Only ever emitted as `True`. Absent means "no claim", which is what an
    # exception that knows nothing about committed output is entitled to say;
    # emitting `False` there would assert safety this projector cannot know.
    if getattr(exc, "output_committed", False):
        payload["output_committed"] = True
    return _enriched(payload, status, request_id), status


def _enriched(payload: dict, status: int, request_id: str) -> dict:
    """``public_failure`` narrowed back to a dict.

    It is typed to take and return ``object`` because it is also handed list
    and non-error bodies at the ``_json`` chokepoint. Here the input is always
    an error dict with a 4xx/5xx status, so the enrichment always applies --
    but assuming that silently is how a `None` would reach a caller doing
    ``body["error"]``.
    """
    enriched = public_failure(payload, status, request_id)
    return enriched if isinstance(enriched, dict) else payload


__all__ = [
    "ERROR_CODES",
    "INTERNAL_ERROR_MESSAGE",
    "GatewayError",
    "error_code_for",
    "gateway_error_payload",
    "public_exception",
    "public_failure",
    "record_diagnostic",
]
