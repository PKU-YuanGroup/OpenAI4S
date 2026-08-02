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
from typing import Any

from openai4s.observability import correlation_id, fingerprint, log_event

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


#: What an unknown failure is allowed to say on a record that outlives the
#: request. It says nothing about *this* failure on purpose: the pair
#: (`surface`, `error_class`) is the identity, and the message is the part that
#: cannot be bounded.
DIAGNOSTIC_DETAIL = "an unhandled exception was recorded"


#: The exception categories this repository is willing to name, written down
#: here rather than derived from the object.
#:
#: A class name looked like metadata and is not. `type("PRIVATE_COHORT_ALPHA_
#: SEVEN", (RuntimeError,), {})` is three tokens of work, the name is a
#: perfectly legal identifier with no digits, and it therefore satisfies every
#: pattern a syntax check can apply -- which is the point: syntax is not
#: provenance. Membership of a set someone wrote down is.
#:
#: The MRO walk is what keeps this useful rather than merely safe. A caller's
#: `MyLibError(OSError)` reports `OSError`: the family is what tells an
#: operator what kind of thing broke, and it comes from this set, never from
#: the object.
_KNOWN_EXCEPTIONS = frozenset(
    {
        "ArithmeticError",
        "AssertionError",
        "AttributeError",
        "BaseException",
        "BlockingIOError",
        "BrokenPipeError",
        "BufferError",
        "ChildProcessError",
        "ConnectionError",
        "ConnectionRefusedError",
        "ConnectionResetError",
        "EOFError",
        "Exception",
        "FileExistsError",
        "FileNotFoundError",
        "FloatingPointError",
        "GeneratorExit",
        "ImportError",
        "IndentationError",
        "IndexError",
        "InterruptedError",
        "IsADirectoryError",
        "KeyError",
        "KeyboardInterrupt",
        "LookupError",
        "MemoryError",
        "ModuleNotFoundError",
        "NameError",
        "NotADirectoryError",
        "NotImplementedError",
        "OSError",
        "OverflowError",
        "PermissionError",
        "ProcessLookupError",
        "RecursionError",
        "ReferenceError",
        "RuntimeError",
        "StopAsyncIteration",
        "StopIteration",
        "SyntaxError",
        "SystemError",
        "SystemExit",
        "TimeoutError",
        "TypeError",
        "UnboundLocalError",
        "UnicodeDecodeError",
        "UnicodeEncodeError",
        "UnicodeError",
        "ValueError",
        "ZeroDivisionError",
        # stdlib modules this daemon actually fails through
        "JSONDecodeError",
        "CalledProcessError",
        "TimeoutExpired",
        "HTTPError",
        "URLError",
        "OperationalError",
        "IntegrityError",
        "DatabaseError",
        "BadZipFile",
        # openai4s' own
        "GatewayError",
        "KernelBusyError",
        "ExecutionCancelled",
        "LLMError",
        "SandboxUnavailableError",
    }
)
UNKNOWN_TYPE_NAME = "unknown"


def safe_type_name(exc: BaseException) -> str:
    """The nearest exception category this module names, or ``unknown``.

    Correlation does not depend on it: `error_class` fingerprints the fully
    qualified name, so two failures of one private type still match each other
    without the name ever being reproduced.
    """
    try:
        mro = type(exc).__mro__
    except Exception:  # noqa: BLE001 — a broken type must not break reporting
        return UNKNOWN_TYPE_NAME
    for klass in mro:
        try:
            name = klass.__name__
        except Exception:  # noqa: BLE001
            continue
        if isinstance(name, str) and name in _KNOWN_EXCEPTIONS:
            return name
    return UNKNOWN_TYPE_NAME


def safe_protocol_value(value: Any, allowed: frozenset) -> str:
    """A caller-supplied string, kept only if this repository named it."""
    return value if isinstance(value, str) and value in allowed else UNKNOWN_TYPE_NAME


def error_class(exc: BaseException) -> str:
    """A stable, content-free identity for a kind of failure.

    Built from the exception's *type* and never from its message, so nothing
    here can render an arbitrary ``__str__`` — not to redact it, not to bound
    it, not to fingerprint it. That is the whole point: a rendering is the one
    operation an unknown exception gets to influence, and it can be enormous,
    hostile, or itself raise.

    What it buys back is the thing losing the message costs: two occurrences of
    the same failure at the same surface are recognisably the same failure, so
    a support ticket quoting a request id still leads somewhere.
    """
    try:
        kind = type(exc)
        name = f"{kind.__module__}.{kind.__qualname__}"
    except Exception:  # noqa: BLE001 — a broken type must not break reporting
        name = "unknown"
    return fingerprint(name)


#: `redacted_detail` lived here: it rendered an unknown exception and scrubbed
#: the result with patterns. It is deleted rather than kept private, because a
#: helper whose whole job is "render an arbitrary exception safely" is an entry
#: point back to the defect -- the patterns lost to an ordinary English
#: sentence, a `/srv` path and a command with no identity in it, and the next
#: caller would have had no way to know that from the name. What replaced it is
#: `DIAGNOSTIC_DETAIL` plus `error_class`: nothing rendered, correlation kept.


def _exact_id(value: Any) -> str:
    """An id, or nothing. Never a stringification of something else."""
    return value if isinstance(value, str) and value else ""


def record_diagnostic(
    exc: BaseException, *, surface: str, request_id: str | None = None
) -> dict:
    """Put the original failure where an operator can reach it and a client cannot.

    The structured logger is the sink rather than a new in-process buffer: it
    already redacts field-wise, and it is not served over HTTP. A second store
    would be a second thing to redact and a second thing to forget.

    Where it ends up is worth being exact about, because this docstring used to
    claim it "lands in the file ``diagnostics.build_bundle`` collects" and that
    was false in two ways at once. ``log_event`` writes to **stderr**, and only
    when ``OPENAI4S_STRUCTURED_LOGS`` is set — off by default, deliberately
    (``docs/security.md``). On a packaged install stderr is redirected to
    ``<data_dir>/logs/app.out``, which ``build_bundle`` skipped, because it
    globbed ``*.log*`` and nothing in the product writes such a file. So the
    operator half of this contract — a generic public message redeemable by
    pairing ``request_id`` against the diagnostic — was not redeemable from a
    support bundle at all. The glob is fixed; the default-off part is a posture
    choice and stands, so on a default install the pairing is redeemable from
    the terminal the daemon is running in, and nowhere else.

    Returns the emitted record so the projector can tie the public body and
    this line together by ``request_id`` -- that pairing is the whole reason
    the generic message is tolerable.
    """
    return log_event(
        "unhandled_exception",
        # No `str()`: a surface that is not exactly a string is not a
        # surface, and coercing one is how an object's `__str__` gets
        # invited into a record that outlives the request.
        surface=surface if isinstance(surface, str) and surface else "unknown",
        exception=safe_type_name(exc),
        detail=DIAGNOSTIC_DETAIL,
        error_class=error_class(exc),
        request_id=_exact_id(request_id) or _exact_id(correlation_id()) or "",
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
    "DIAGNOSTIC_DETAIL",
    "UNKNOWN_TYPE_NAME",
    "error_class",
    "safe_protocol_value",
    "safe_type_name",
    "record_diagnostic",
]
