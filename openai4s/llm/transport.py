"""Pure-stdlib HTTP transports used by the LLM provider adapters.

Failures here are typed (see ``TransportError``) rather than flattened into a
message string, which is what makes a bounded retry possible at all: a 429 with
a ``Retry-After`` is now distinguishable from a 401 without parsing English.

The retry policy is deliberately narrow:

  * only failures whose reply delivered nothing — a whole-response POST and a
    connection/response-header failure can be replayed; a stream that already
    delivered events cannot, because the caller has seen those bytes;
  * bounded attempts with exponential backoff and jitter (many clients hitting
    one rate-limited endpoint must not resynchronise on the same schedule);
  * ``Retry-After`` wins over the computed backoff when the server sent one;
  * cancellable between attempts, so a user's Stop is not held hostage by a
    sleep; and
  * a total budget, so a long Retry-After cannot silently park a turn for
    minutes.
"""

from __future__ import annotations

import functools
import http.client
import inspect
import json
import random
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from openai4s.http_deadline import (
    HTTPExchangeDeadline,
    HTTPExchangeTimeout,
    read_body_capped,
    response_body_exhausted,
    socket_timeout_setter,
)

from .models import (
    LLMDeadlineExceeded,
    LLMError,
    LLMResponseTooLarge,
    StreamReadError,
    StreamTimeoutError,
    TransportError,
    llm_failure_code,
    parse_retry_after,
    status_is_retryable,
)


def bind_call_context(fn, **context):
    """Attach provider/cancellation to a transport without breaking injection.

    The provider adapters call ``post_json(url, payload, headers, timeout)``
    positionally, so this binds the context once at the dispatch seam instead
    of touching all four wire adapters.

    Offline tests inject their own transports at two different depths — the
    ``openai4s.llm._post_json`` facade hook and ``transport.post_json``
    itself — and many of those are plain four-argument callables. The
    documented contract says they keep working, so only keywords the target
    actually accepts are bound; anything else is dropped rather than raising
    ``TypeError`` on a call that would otherwise have succeeded.
    """
    try:
        parameters = inspect.signature(fn).parameters
    except (TypeError, ValueError):  # builtins and other C callables
        return fn
    takes_kwargs = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()
    )
    accepted: dict[str, Any] = {
        name: value
        for name, value in context.items()
        if takes_kwargs or name in parameters
    }
    return functools.partial(fn, **accepted) if accepted else fn


# The defaults below are also ``LLMConfig``'s (``OPENAI4S_LLM_MAX_RETRIES`` /
# ``_RETRY_BUDGET`` / ``_RETRY_MAX_DELAY``); every call made through
# ``client.chat`` or the Agent runtime uses the configured values instead.
# Attempts include the first try: 3 == one initial call plus two retries.
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_BACKOFF = 0.5
DEFAULT_MAX_BACKOFF = 8.0
# A rate limit (HTTP 429, including Ark's burst protector) is a wait measured
# in seconds, not a blip: a full-jitter wait in [0, 0.5] almost always lands
# inside the same window and burns the attempt. A 429 without a usable
# ``Retry-After`` therefore gets a slower, strictly-positive backoff. It is
# still governed by the same attempt count, cap, total budget and cancellation
# polling as every other retryable transport failure.
REQUEST_BURST_BASE_BACKOFF = 4.0
# Ceiling on time spent sleeping across a call. A provider may advertise a
# 300s Retry-After; honouring that inside one turn would look like a hang.
DEFAULT_RETRY_BUDGET = 30.0


def _retry_count(max_retries: Any) -> int | None:
    """``max_retries`` if it is a valid configured count, else ``None``.

    Anything else (an injected adapter without the field, a bool, a count
    past ``LLMConfig``'s own ceiling) keeps the transport default rather than
    inventing a policy.
    """
    from openai4s.config import MAX_LLM_RETRIES

    if type(max_retries) is not int or not 0 <= max_retries <= MAX_LLM_RETRIES:
        return None
    return max_retries


def max_attempts_for_retries(max_retries: Any) -> int:
    """The send ceiling a configured retry count stands for.

    The one place that turns ``max_retries`` into sends: the transport's
    ``CallState`` and the quota bound in ``server/auto_budget.py`` both read
    it, so the reservation is priced for exactly the sends that can happen.

    ``retries + 1``, but never below two. The one blocking compatibility
    request a stream refused outright may make shares this ceiling, and it is
    a fallback, not a retry: with a ceiling of one, ``max_retries=0`` would
    also have removed it and failed every turn behind a proxy that refuses
    ``stream``. Retrying itself is limited separately (``CallState.max_retries``).
    """
    retries = _retry_count(max_retries)
    if retries is None:
        return DEFAULT_MAX_ATTEMPTS
    return max(retries + 1, 2)


def _positive_seconds(value: Any, default: float, *, allow_zero: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    try:
        seconds = float(value)
    except OverflowError:  # an int too large for a float
        return default
    if seconds != seconds or seconds in (float("inf"), float("-inf")):
        return default
    if seconds < 0 or (seconds == 0 and not allow_zero):
        return default
    return seconds


MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_ERROR_BYTES = 64 * 1024
MAX_SSE_LINE_BYTES = 1024 * 1024
MAX_SSE_EVENT_BYTES = 4 * 1024 * 1024
MAX_SSE_BYTES = 64 * 1024 * 1024


@dataclass
class CallState:
    """One logical chat's send, backoff and cancellation budget.

    The JSON compatibility attempt shares this state with the original SSE
    request. It is never stored on a reusable config or cancellation probe.
    """

    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    retry_budget: float = DEFAULT_RETRY_BUDGET
    should_cancel: Any = None
    attempts: int = 0
    sent: bool = False
    spent: float = 0.0
    last_error: TransportError | None = None
    total_timeout_s: float = 900.0
    #: Ceiling on one computed backoff wait (never on a ``Retry-After``).
    max_delay: float = DEFAULT_MAX_BACKOFF
    #: Retries allowed after a failed send, separately from ``max_attempts``
    #: (which also admits the stream-compatibility request). ``None``: every
    #: send the ceiling allows may be a retry.
    max_retries: int | None = None
    #: Backoff waits taken so far, i.e. retries actually attempted.
    retries: int = 0
    deadline: float = field(init=False)

    def __post_init__(self) -> None:
        self.deadline = time.monotonic() + self.total_timeout_s

    @classmethod
    def from_config(cls, cfg: Any, *, should_cancel: Any = None) -> "CallState":
        """A fresh logical-call state carrying ``cfg``'s retry policy.

        ``cfg`` is typed ``Any`` on purpose: the Agent runtime and tests hand
        over duck-typed configs, so a missing or malformed field keeps the
        transport default instead of failing the call.
        """
        retries = getattr(cfg, "max_retries", None)
        return cls(
            max_attempts=max_attempts_for_retries(retries),
            max_retries=_retry_count(retries),
            retry_budget=_positive_seconds(
                getattr(cfg, "retry_budget_s", None),
                DEFAULT_RETRY_BUDGET,
                allow_zero=True,
            ),
            should_cancel=should_cancel,
            total_timeout_s=_positive_seconds(
                getattr(cfg, "total_timeout_s", None), 900.0, allow_zero=False
            ),
            max_delay=_positive_seconds(
                getattr(cfg, "retry_max_delay_s", None),
                DEFAULT_MAX_BACKOFF,
                allow_zero=False,
            ),
        )

    def remaining(self, provider: str | None = None, operation: str = "chat") -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise self.deadline_error(provider, operation)
        return remaining

    def deadline_error(
        self, provider: str | None, operation: str
    ) -> LLMDeadlineExceeded:
        prior = self.last_error
        return LLMDeadlineExceeded(
            "LLM logical call exceeded its total deadline",
            provider=prior.provider if prior is not None else provider,
            operation=operation,
            status=prior.status if prior is not None else None,
            headers=prior.headers if prior is not None else None,
            request_id=prior.request_id if prior is not None else None,
            body=prior.body if prior is not None else None,
            output_committed=prior.output_committed if prior is not None else False,
        )

    def check_send(self, provider: str | None, operation: str) -> None:
        if self.should_cancel is not None and self.should_cancel():
            raise self.failure("cancelled before send", provider, operation)
        self.remaining(provider, operation)
        if self.attempts >= self.max_attempts:
            if self.last_error is not None:
                raise self.last_error
            raise self.failure("send budget exhausted", provider, operation)

    def failure(
        self, reason: str, provider: str | None, operation: str
    ) -> TransportError:
        err = self.last_error
        failure = TransportError(
            f"{err or 'LLM request'} ({reason})",
            provider=err.provider if err is not None else provider,
            operation=err.operation if err is not None else operation,
            status=err.status if err is not None else None,
            error_code=err.error_code if err is not None else None,
            headers=err.headers if err is not None else None,
            request_id=err.request_id if err is not None else None,
            retry_after=err.retry_after if err is not None else None,
            output_committed=err.output_committed if err is not None else False,
            body=err.body if err is not None else None,
            retryable=False,
        )

        failure.llm_not_started = not self.sent
        return failure


def streaming_refused(error: TransportError) -> bool:
    """Only an explicit structured refusal of `stream` permits compatibility."""
    if error.status not in (400, 422) or error.retryable or error.output_committed:
        return False
    try:
        body = json.loads(error.body or "")
    except (ValueError, TypeError):
        body = None
    detail = body.get("error", body) if isinstance(body, dict) else None
    named = detail.get("param") if isinstance(detail, dict) else None
    if isinstance(detail, dict) and "param" in detail and named != "stream":
        return False
    if error.error_code == "streaming_not_supported":
        return True
    # The `code` vocabulary is not a protocol. OpenAI leaves `code` null on its
    # canonical unsupported-parameter body and Anthropic reports only
    # `invalid_request_error` through `type`, so keying on an allowlist of
    # codes made this gate unreachable for both -- including the whole
    # `_StreamStartError` branch in the Anthropic adapter. A body that names
    # `stream` as the offending parameter *is* the explicit structured refusal
    # this function exists to recognise, whatever it calls the code.
    return named == "stream"


def _header_dict(e: urllib.error.HTTPError) -> dict[str, str]:
    try:
        return {k.lower(): v for k, v in e.headers.items()}
    except Exception:  # noqa: BLE001 - headers must never break error handling
        return {}


def _request_id(headers: dict[str, str]) -> str | None:
    for key in ("x-request-id", "request-id", "x-amzn-requestid", "cf-ray"):
        if headers.get(key):
            return headers[key]
    return None


def _error_code(body: str) -> str | None:
    """Best-effort provider error code. Providers agree on neither the shape
    nor the nesting, so this stays advisory — the status is the contract."""
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    err = parsed.get("error")
    if isinstance(err, dict):
        code = err.get("code") or err.get("type")
        if code:
            return str(code)
    if isinstance(err, str):
        return err
    code = parsed.get("code") or parsed.get("type")
    return str(code) if code else None


def _response_error(kind, message, response, *, provider, operation):
    headers = {
        k.lower(): v for k, v in (getattr(response, "headers", None) or {}).items()
    }
    return kind(
        message,
        provider=provider,
        operation=operation,
        status=getattr(response, "status", getattr(response, "code", None)),
        headers=headers,
        request_id=_request_id(headers),
    )


def _read_timeout(response, exchange, state, *, provider, operation):
    total = exchange.expired or time.monotonic() >= state.deadline
    return _response_error(
        # The non-total branch IS "the upstream stopped sending bytes for the
        # configured read timeout" -- the condition `StreamTimeoutError` was
        # introduced for. Classifying it keeps the recovery affordance on the
        # whole-response wire too, instead of only where SSE happens to be on.
        LLMDeadlineExceeded if total else StreamTimeoutError,
        (
            "LLM logical call exceeded its total deadline"
            if total
            else "LLM response idle timeout"
        ),
        response,
        provider=provider,
        operation=operation,
    )


def _read_body(response, limit, exchange, state, *, provider, operation):
    return read_body_capped(
        response,
        limit=limit,
        exchange=exchange,
        on_timeout=lambda: _read_timeout(
            response, exchange, state, provider=provider, operation=operation
        ),
        on_oversize=lambda: _response_error(
            LLMResponseTooLarge,
            "LLM response exceeds its byte limit",
            response,
            provider=provider,
            operation=operation,
        ),
        on_truncated=lambda: _response_error(
            TransportError,
            "LLM response body was truncated",
            response,
            provider=provider,
            operation=operation,
        ),
    )


def _urlopen(request, *, timeout, exchange):
    """The injectable open seam; real HTTP always uses the shared watchdog."""
    del timeout
    # urllib forwards Authorization on a 301/302/303 even when the redirect
    # changes hosts. A destination-scoped LLM key must not follow a provider's
    # open redirect to an endpoint nobody configured.
    return exchange.open(exchange.build_opener(_RejectRedirects), request)


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Keep every LLM request on the endpoint its credential was resolved for."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _exchange(state, timeout, provider, operation):
    def before_send(phase):
        if state.should_cancel is not None and state.should_cancel():
            raise state.failure("cancelled before send", provider, operation)
        state.remaining(provider, operation)
        if phase == "send":
            state.sent = True

    exchange = HTTPExchangeDeadline(
        state.remaining(provider, operation),
        idle_timeout=timeout,
        before_send=before_send,
    )
    exchange.deadline = state.deadline
    return exchange


def _http_error(
    e: urllib.error.HTTPError,
    *,
    provider: str | None,
    operation: str,
    exchange: HTTPExchangeDeadline,
    state: CallState,
) -> TransportError:
    body = ""
    try:
        exchange.register_response(e)
        body = _read_body(
            e, MAX_ERROR_BYTES, exchange, state, provider=provider, operation=operation
        ).decode("utf-8", "replace")
    except LLMResponseTooLarge:
        body = f"[response body omitted: exceeds {MAX_ERROR_BYTES} bytes]"
    except LLMDeadlineExceeded:
        raise
    except Exception:  # noqa: BLE001 - a body we cannot read must not mask the status
        if exchange.expired or time.monotonic() >= state.deadline:
            raise _read_timeout(
                e, exchange, state, provider=provider, operation=operation
            ) from None
        body = "[response body unavailable]"
    finally:
        e.close()
    headers = _header_dict(e)
    return TransportError(
        f"LLM HTTP {e.code}: {body}",
        provider=provider,
        operation=operation,
        status=e.code,
        error_code=_error_code(body),
        headers=headers,
        request_id=_request_id(headers),
        retryable=status_is_retryable(e.code),
        retry_after=parse_retry_after(headers.get("retry-after")),
        body=body,
    )


#: Structured reasons that can only come from the connect phase. Prose is not
#: one of them: a `URLError("connection refused")` is a string, not evidence.
_CONNECT_PHASE_REASONS = (ConnectionRefusedError, socket.gaierror, TimeoutError)


def _url_error(
    e: urllib.error.URLError, *, provider: str | None, operation: str, sent: bool
) -> TransportError:
    return TransportError(
        f"LLM connection error: {e.reason}",
        provider=provider,
        operation=operation,
        # `URLError` wraps both "never left this machine" and "timed out or was
        # reset after the POST was written". `state.sent` is the discriminator
        # the transport already tracks (`_DeadlineSend.send` latches it at the
        # byte boundary), so a refused connection, an unresolved host and a
        # *connect* timeout stay replayable while anything after the write, and
        # any reason that is only prose, does not.
        retryable=not sent and isinstance(e.reason, _CONNECT_PHASE_REASONS),
    )


#: How often a backoff wait looks up to see whether the turn was cancelled.
#: Short enough that Stop feels immediate, long enough that a five-minute
#: `Retry-After` costs a bounded number of checks rather than a busy loop.
CANCEL_POLL_S = 0.25


def _wait(delay: float, do_sleep, should_cancel) -> bool:
    """Wait ``delay`` seconds, returning early if the turn is cancelled.

    Sliced only when there is something to poll for. With no ``should_cancel``
    the wait is a single call, which keeps the injected-sleep contract the
    tests rely on — one sleep, one recorded delay — and avoids inventing
    wake-ups for a caller that cannot be interrupted anyway.
    """
    if should_cancel is None:
        do_sleep(delay)
        return False
    remaining = float(delay)
    while remaining > 0:
        if should_cancel():
            return True
        step = remaining if remaining < CANCEL_POLL_S else CANCEL_POLL_S
        do_sleep(step)
        remaining -= step
    return bool(should_cancel())


def _sleep_for(err: TransportError, attempt: int, base: float, cap: float) -> float:
    """Honour Retry-After when present, else exponential backoff with jitter.

    ``cap`` bounds only the computed backoff. A server-sent ``Retry-After`` is
    returned as-is: shortening it would re-send inside the window the server
    just named and spend an attempt for nothing. Only the retry budget and the
    total deadline decide whether such a wait is affordable.
    """
    rate_limited = err.status == 429 or llm_failure_code(err) == "llm_request_burst"
    # ``Retry-After: 0`` on a rate limit is not a promise that the window has
    # reopened (Ark sends it with its burst protector), so it falls through.
    if err.retry_after is not None and (err.retry_after > 0 or not rate_limited):
        return err.retry_after
    if rate_limited:
        backoff = min(cap, REQUEST_BURST_BASE_BACKOFF * (2 ** (attempt - 1)))
        # Equal jitter: unlike full jitter its lower bound is non-zero, while
        # concurrent clients still do not resynchronise on one fixed delay.
        return random.uniform(backoff / 2.0, backoff)
    backoff = min(cap, base * (2 ** (attempt - 1)))
    # Full jitter: without it, N clients rate-limited at the same instant all
    # come back at the same instant.
    return random.uniform(0, backoff)


def _give_up(
    err: TransportError,
    reason: str,
    *,
    stop: str,
    provider: str | None,
    operation: str,
) -> TransportError:
    """``err`` restated with why no further retry was attempted.

    Report the real reason rather than silently giving up: a 300s Retry-After
    is a legitimate answer that this call is simply not allowed to wait out.
    ``type(err)``: a stream read failure keeps its class, and a rate limit its
    status, so the stable failure code the recovery path keys on survives.
    """
    restated = type(err)(
        f"{err} ({reason})",
        provider=provider,
        operation=operation,
        status=err.status,
        error_code=err.error_code,
        headers=err.headers,
        request_id=err.request_id,
        retryable=True,
        retry_after=err.retry_after,
        output_committed=err.output_committed,
        body=err.body,
    )
    restated.retries_attempted = err.retries_attempted
    restated.retry_stop = stop
    return restated


def _retry_loop(
    attempt_fn,
    *,
    provider: str | None,
    operation: str,
    max_attempts: int | None,
    base_backoff: float,
    max_backoff: float | None,
    retry_budget: float | None,
    should_cancel=None,
    sleep=None,
    call_state: CallState | None = None,
):  # noqa: C901
    # Resolved per call, not captured as a default: a default argument is
    # evaluated once at def time, which would pin the original time.sleep and
    # silently ignore any test that patches it.
    do_sleep = sleep if sleep is not None else time.sleep
    # A retry that has already begun waiting must still be stoppable. The
    # cancellation checks used to sit either side of a single blocking sleep,
    # so pressing Stop one millisecond into a 300-second `Retry-After` left the
    # turn parked for the full five minutes with nothing able to interrupt it —
    # and the only test for it cancelled *before* the wait began, which is the
    # case that already worked.
    state = call_state or CallState(
        max_attempts=DEFAULT_MAX_ATTEMPTS if max_attempts is None else max_attempts,
        retry_budget=DEFAULT_RETRY_BUDGET if retry_budget is None else retry_budget,
        should_cancel=should_cancel,
        max_delay=DEFAULT_MAX_BACKOFF if max_backoff is None else max_backoff,
    )
    # ``None`` means "the logical call's policy", which is how a configured
    # retry count reaches the loop. An explicit value is an *extra* cap for
    # this one invocation (the providers' one-shot compatibility POST passes
    # ``max_attempts=1``) and is never written back into the shared state.
    # ``is None``, never ``or``: an explicit 0.0 budget means "do not wait".
    local_attempts = state.max_attempts if max_attempts is None else max_attempts
    budget = (
        state.retry_budget
        if retry_budget is None
        else min(retry_budget, state.retry_budget)
    )
    cap = state.max_delay if max_backoff is None else max_backoff
    retry_limit = (
        state.max_attempts - 1 if state.max_retries is None else state.max_retries
    )
    for attempt in range(1, local_attempts + 1):
        state.check_send(provider, operation)
        state.attempts += 1
        try:
            return attempt_fn()
        except TransportError as err:
            state.last_error = err
            # Recorded on every error this loop lets out, so a message can say
            # whether a retry happened without parsing this module's prose.
            err.retries_attempted = state.retries
            if not err.retryable or err.output_committed:
                raise
            if (
                attempt >= local_attempts
                or state.attempts >= state.max_attempts
                or state.retries >= retry_limit
            ):
                if max_attempts is not None and attempt >= local_attempts:
                    # Raising MAX_RETRIES cannot lift an explicit invocation
                    # cap, even when the logical call's ceiling also ran out.
                    # In particular, the compatibility POST stays one-shot.
                    err.retry_stop = "request_limit"
                raise
            delay = _sleep_for(err, state.attempts, base_backoff, cap)
            if state.spent + delay > budget:
                raise _give_up(
                    err,
                    f"retry budget of {budget}s exhausted; the provider asked "
                    f"for {delay:.1f}s more",
                    stop="budget",
                    provider=provider,
                    operation=operation,
                ) from err
            remaining = state.remaining(provider, operation)
            if delay >= remaining:
                # Sleeping out the rest of the deadline only to fail with
                # ``llm_deadline_exceeded`` would hide a rate limit behind a
                # timeout after making the user wait for it. Say so now.
                raise _give_up(
                    err,
                    f"the next retry wait of {delay:.1f}s does not fit in the "
                    f"{remaining:.1f}s left of the call's total timeout",
                    stop="deadline",
                    provider=provider,
                    operation=operation,
                ) from err
            if _wait(delay, do_sleep, state.should_cancel):
                raise state.failure(
                    "cancelled before retry", provider, operation
                ) from err
            state.spent += delay
            state.retries += 1
            state.remaining(provider, operation)
    raise AssertionError("unreachable")  # pragma: no cover


def post_json(
    url: str,
    payload: dict,
    headers: dict,
    timeout: float,
    *,
    provider: str | None = None,
    max_attempts: int | None = None,
    retry_budget: float | None = None,
    should_cancel=None,
    sleep=None,
    call_state: CallState | None = None,
) -> dict:
    """POST JSON and decode the whole response.

    Retryable because it is all-or-nothing: the caller sees the response only
    once it is complete, so a replayed attempt cannot duplicate output.
    """
    data = json.dumps(payload).encode("utf-8")
    state = call_state or CallState(
        max_attempts=DEFAULT_MAX_ATTEMPTS if max_attempts is None else max_attempts,
        retry_budget=DEFAULT_RETRY_BUDGET if retry_budget is None else retry_budget,
        should_cancel=should_cancel,
    )

    def attempt() -> dict:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with _exchange(state, timeout, provider, "post_json") as exchange:
                try:
                    resp = _urlopen(
                        req, timeout=exchange.io_timeout(), exchange=exchange
                    )
                except urllib.error.HTTPError as e:
                    state.sent = True
                    raise _http_error(
                        e,
                        provider=provider,
                        operation="post_json",
                        exchange=exchange,
                        state=state,
                    ) from e
                state.sent = True
                with resp:
                    body = _read_body(
                        resp,
                        MAX_JSON_BYTES,
                        exchange,
                        state,
                        provider=provider,
                        operation="post_json",
                    )
                    try:
                        result = json.loads(body.decode("utf-8"))
                    except (ValueError, UnicodeError) as error:
                        raise LLMError("LLM response contained invalid JSON") from error
                    state.remaining(provider, "post_json")
                    return result
        except HTTPExchangeTimeout as e:
            raise state.deadline_error(provider, "post_json") from e
        except urllib.error.URLError as e:
            state.remaining(provider, "post_json")
            raise _url_error(
                e, provider=provider, operation="post_json", sent=state.sent
            ) from e
        except (OSError, http.client.HTTPException) as error:
            state.remaining(provider, "post_json")
            # A whole-response call is all-or-nothing: the caller only sees a
            # reply once the body is complete, so replaying a connection or
            # header failure here cannot duplicate visible output. A dropped
            # connection / reset is a transient upstream condition, not a
            # permanent refusal, so it joins the bounded backoff loop instead
            # of failing the turn on the first blip.
            raise TransportError(
                "LLM connection or response headers failed",
                provider=provider,
                operation="post_json",
                retryable=True,
            ) from error

    return _retry_loop(
        attempt,
        provider=provider,
        operation="post_json",
        max_attempts=max_attempts,
        base_backoff=DEFAULT_BASE_BACKOFF,
        max_backoff=None,
        retry_budget=retry_budget,
        should_cancel=should_cancel,
        sleep=sleep,
        call_state=state,
    )


_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def post_sse(
    url: str,
    payload: dict,
    headers: dict,
    timeout: float,
    on_event,
    *,
    provider: str | None = None,
    max_attempts: int | None = None,
    retry_budget: float | None = None,
    should_cancel=None,
    sleep=None,
    call_state: CallState | None = None,
) -> None:
    """POST and decode a Server-Sent-Events stream.

    SSE events are delimited by a blank line and may contain multiple ``data:``
    rows. Tool calls are control-plane actions, so a malformed non-empty event
    is surfaced instead of being silently discarded.

    Only the *connect* is retried. The moment an event reaches ``on_event`` the
    caller has observed output, and replaying the request would re-emit it —
    so any failure from that point carries ``output_committed=True`` and is
    raised as-is.
    """
    data = json.dumps(payload).encode("utf-8")
    state = call_state or CallState(
        max_attempts=DEFAULT_MAX_ATTEMPTS if max_attempts is None else max_attempts,
        retry_budget=DEFAULT_RETRY_BUDGET if retry_budget is None else retry_budget,
        should_cancel=should_cancel,
    )

    def attempt() -> None:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with _exchange(state, timeout, provider, "post_sse") as exchange:
                try:
                    resp = _urlopen(
                        req, timeout=exchange.io_timeout(), exchange=exchange
                    )
                except urllib.error.HTTPError as e:
                    state.sent = True
                    raise _http_error(
                        e,
                        provider=provider,
                        operation="post_sse",
                        exchange=exchange,
                        state=state,
                    ) from e
                state.sent = True
                _consume(
                    resp,
                    on_event,
                    provider=provider,
                    should_cancel=state.should_cancel,
                    exchange=exchange,
                    call_state=state,
                )
        except HTTPExchangeTimeout as e:
            raise state.deadline_error(provider, "post_sse") from e
        except urllib.error.URLError as e:
            state.remaining(provider, "post_sse")
            raise _url_error(
                e, provider=provider, operation="post_sse", sent=state.sent
            ) from e
        except (OSError, http.client.HTTPException) as error:
            state.remaining(provider, "post_sse")
            # This branch is reached while opening the response, before any
            # event has been handed to ``on_event`` -- a read failure inside
            # the stream is turned into ``StreamReadError`` by ``_consume``
            # and keeps its own no-replay rule. Nothing visible was committed
            # here, so a connection reset / dropped header is retried within
            # the same bounded attempt, budget and deadline as a 503.
            raise TransportError(
                "LLM connection or response headers failed",
                provider=provider,
                operation="post_sse",
                retryable=True,
            ) from error

    return _retry_loop(
        attempt,
        provider=provider,
        operation="post_sse",
        max_attempts=max_attempts,
        base_backoff=DEFAULT_BASE_BACKOFF,
        max_backoff=None,
        retry_budget=retry_budget,
        should_cancel=should_cancel,
        sleep=sleep,
        call_state=state,
    )


def _consume(
    resp,
    on_event,
    *,
    provider: str | None,
    should_cancel=None,
    exchange: HTTPExchangeDeadline,
    call_state: CallState,
) -> None:
    data_lines: list[str] = []
    committed = False
    total_bytes = 0
    event_bytes = 0

    # Two policies, chosen by the caller through the probe it hands over:
    # abort the stream at the next event (the default -- frees the thread,
    # the socket and the provider's generation within one chunk), or drain it
    # to the end while the deltas are discarded upstream. Draining is what a
    # metered session asks for: the team quota ledger is charged from the
    # terminal usage event, and a stream closed before it would let a member
    # Stop-and-resend past the quota with every abandoned call unbilled.
    abort_stream = bool(getattr(should_cancel, "abort_stream", True))

    def cancelled() -> bool:
        if should_cancel is None or not abort_stream:
            return False
        try:
            return bool(should_cancel())
        except Exception:  # noqa: BLE001 - cancellation telemetry is fail-soft
            return False

    def dispatch() -> bool:
        nonlocal committed
        if not data_lines:
            return False
        chunk = "\n".join(data_lines).strip()
        data_lines.clear()
        if chunk == "[DONE]":
            return True
        if not chunk:
            return False
        # Stop reaches a live stream here, once per event. Until it did, a
        # cancelled streaming call kept its thread, its socket and the
        # provider's generation (and bill) running to the end of the reply --
        # minutes for a long answer -- with every delta discarded on arrival;
        # that lifetime is what the detached-call budget had to bound. Ending
        # the read closes the response in ``finally`` and lets the abandoned
        # call settle within one chunk. The cost is the terminal ``usage``
        # event of a reply nobody will read: the deltas already delivered are
        # its lower bound, and the provider stops generating at disconnect.
        if cancelled():
            raise TransportError(
                "LLM event stream abandoned: the caller cancelled mid-stream",
                provider=provider,
                operation="post_sse",
                retryable=False,
                output_committed=committed,
            )
        try:
            event = json.loads(chunk)
        except ValueError as e:
            raise LLMError(f"invalid JSON in LLM event stream: {chunk[:400]}") from e
        if not isinstance(event, dict):
            raise LLMError("LLM event stream yielded a non-object JSON event")
        committed = True
        try:
            stop = on_event(event)
        except LLMError:
            raise
        except Exception as e:  # noqa: BLE001 - a handler bug is not a read failure
            # Typed here, inside the read loop's own handler, so a local
            # failure in the caller's event handler is never classified as the
            # upstream interrupting the stream -- that class offers the user a
            # continuation which would only reproduce the same local error.
            raise TransportError(
                f"LLM event handler failed: {type(e).__name__}",
                provider=provider,
                operation="post_sse",
                retryable=False,
                output_committed=True,
            ) from e
        return stop is True

    def check_read() -> None:
        call_state.remaining(provider, "post_sse")
        if cancelled():
            raise TransportError(
                "LLM event stream abandoned: the caller cancelled mid-stream",
                provider=provider,
                operation="post_sse",
                output_committed=committed,
            )

    def lines():
        nonlocal total_bytes
        read_once = getattr(resp, "read1", None)
        if not callable(read_once):
            # Historical injected SSE readers are iterable line fixtures.
            iterator = iter(resp)
            while True:
                check_read()
                raw = next(iterator, b"")
                if not raw:
                    break
                total_bytes += len(raw)
                yield raw
            return
        pending = bytearray()
        arm = socket_timeout_setter(resp)
        while not response_body_exhausted(resp):
            check_read()
            try:
                if arm is not None:
                    arm(exchange.io_timeout())
                chunk = read_once(min(8192, MAX_SSE_BYTES - total_bytes + 1))
            except Exception:
                if exchange.expired or time.monotonic() >= call_state.deadline:
                    raise _read_timeout(
                        resp,
                        exchange,
                        call_state,
                        provider=provider,
                        operation="post_sse",
                    ) from None
                raise
            check_read()
            if not chunk:
                if exchange.expired:
                    raise _read_timeout(
                        resp,
                        exchange,
                        call_state,
                        provider=provider,
                        operation="post_sse",
                    )
                break
            total_bytes += len(chunk)
            if total_bytes > MAX_SSE_BYTES:
                raise _response_error(
                    LLMResponseTooLarge,
                    "LLM event stream exceeds its total byte limit",
                    resp,
                    provider=provider,
                    operation="post_sse",
                )
            pending.extend(chunk)
            while True:
                end = pending.find(b"\n")
                if end < 0:
                    if len(pending) > MAX_SSE_LINE_BYTES:
                        raise _response_error(
                            LLMResponseTooLarge,
                            "LLM event stream exceeds its line byte limit",
                            resp,
                            provider=provider,
                            operation="post_sse",
                        )
                    break
                raw = bytes(pending[: end + 1])
                del pending[: end + 1]
                yield raw
        if pending:
            yield bytes(pending)

    try:
        try:
            check_read()
            for raw in lines():
                check_read()
                event_bytes += len(raw)
                if (
                    len(raw) > MAX_SSE_LINE_BYTES
                    or total_bytes > MAX_SSE_BYTES
                    or event_bytes > MAX_SSE_EVENT_BYTES
                ):
                    raise _response_error(
                        LLMResponseTooLarge,
                        "LLM event stream exceeds its byte limit",
                        resp,
                        provider=provider,
                        operation="post_sse",
                    )
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not line:
                    event_bytes = 0
                    if dispatch():
                        return
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    value = line[5:]
                    data_lines.append(value[1:] if value.startswith(" ") else value)
            dispatch()
        except TransportError as err:
            if isinstance(err, (LLMDeadlineExceeded, LLMResponseTooLarge)):
                err.output_committed |= committed
            # An HTTP-200 SSE response may carry the failure in an event.
            # Preserve its HTTP evidence just as for an HTTPError response,
            # without replacing fields explicitly supplied by the adapter.
            response_headers = getattr(resp, "headers", None)
            if response_headers is not None:
                err.headers = {
                    **{k.lower(): v for k, v in response_headers.items()},
                    **err.headers,
                }
                if err.request_id is None:
                    err.request_id = _request_id(err.headers)
                if err.retry_after is None:
                    err.retry_after = parse_retry_after(err.headers.get("retry-after"))
            raise
        except LLMError:
            raise
        except Exception as e:  # noqa: BLE001 - normalize transport read failures
            # A read failure cannot prove the provider did not receive the
            # POST, even when no event has arrived. Never transparently replay.
            # The class is still the typed one: the recovery path keys on the
            # failure code to offer the user a continuation, which is a
            # deliberate re-ask, not a transparent replay.
            error_type = (
                StreamTimeoutError if isinstance(e, TimeoutError) else StreamReadError
            )
            raise error_type(
                f"LLM event stream read error: {e}",
                provider=provider,
                operation="post_sse",
                retryable=False,
                output_committed=committed,
            ) from e
    finally:
        try:
            resp.close()
        except Exception:  # noqa: BLE001
            pass
