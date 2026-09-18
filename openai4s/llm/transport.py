"""Pure-stdlib HTTP transports used by the LLM provider adapters.

Failures here are typed (see ``TransportError``) rather than flattened into a
message string, which is what makes a bounded retry possible at all: a 429 with
a ``Retry-After`` is now distinguishable from a 401 without parsing English.

The retry policy is deliberately narrow:

  * only statuses that are retryable *and* whose request committed nothing —
    a whole-response POST can be replayed; a stream that already delivered
    events cannot, because the caller has seen those bytes;
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


# Attempts include the first try: 3 == one initial call plus two retries.
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_BACKOFF = 0.5
DEFAULT_MAX_BACKOFF = 8.0
# Ark's burst protector explicitly asks clients to grow traffic gradually.  A
# full-jitter wait in [0, 0.5] can immediately collide again, so this one exact
# controlled classification gets a slower, strictly-positive backoff.  It is
# still governed by the same attempt count, cap, total budget and cancellation
# polling as every other retryable transport failure.
REQUEST_BURST_BASE_BACKOFF = 4.0
# Ceiling on time spent sleeping across a call. A provider may advertise a
# 300s Retry-After; honouring that inside one turn would look like a hang.
DEFAULT_RETRY_BUDGET = 30.0
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
    total_timeout_s: float = 600.0
    deadline: float = field(init=False)

    def __post_init__(self) -> None:
        self.deadline = time.monotonic() + self.total_timeout_s

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
        LLMDeadlineExceeded if total else TransportError,
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
    return exchange.open(exchange.build_opener(), request)


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
    """Honour Retry-After when present, else exponential backoff with jitter."""
    failure_code = llm_failure_code(err)
    if err.retry_after is not None and (
        err.retry_after > 0 or failure_code != "llm_request_burst"
    ):
        return err.retry_after
    if failure_code == "llm_request_burst":
        backoff = min(cap, REQUEST_BURST_BASE_BACKOFF * (2 ** (attempt - 1)))
        # Equal jitter: unlike full jitter its lower bound is non-zero, while
        # concurrent clients still do not resynchronise on one fixed delay.
        return random.uniform(backoff / 2.0, backoff)
    backoff = min(cap, base * (2 ** (attempt - 1)))
    # Full jitter: without it, N clients rate-limited at the same instant all
    # come back at the same instant.
    return random.uniform(0, backoff)


def _retry_loop(
    attempt_fn,
    *,
    provider: str | None,
    operation: str,
    max_attempts: int,
    base_backoff: float,
    max_backoff: float,
    retry_budget: float,
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
        max_attempts=max_attempts,
        retry_budget=retry_budget,
        should_cancel=should_cancel,
    )
    for attempt in range(1, max_attempts + 1):
        state.check_send(provider, operation)
        state.attempts += 1
        try:
            return attempt_fn()
        except TransportError as err:
            state.last_error = err
            if not err.retryable or err.output_committed:
                raise
            if attempt >= max_attempts or state.attempts >= state.max_attempts:
                raise
            delay = _sleep_for(err, state.attempts, base_backoff, max_backoff)
            if state.spent + delay > min(retry_budget, state.retry_budget):
                # Report the real reason rather than silently giving up: a
                # 300s Retry-After is a legitimate answer that this call is
                # simply not allowed to wait out.
                raise TransportError(
                    f"{err} (retry budget of {min(retry_budget, state.retry_budget)}s exhausted; the "
                    f"provider asked for {delay:.1f}s more)",
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
                ) from err
            remaining = state.remaining(provider, operation)
            deadline_limited = delay >= remaining
            delay = min(delay, remaining)
            if _wait(delay, do_sleep, state.should_cancel):
                raise state.failure(
                    "cancelled before retry", provider, operation
                ) from err
            state.spent += delay
            if deadline_limited:
                raise state.deadline_error(provider, operation) from err
            state.remaining(provider, operation)
    raise AssertionError("unreachable")  # pragma: no cover


def post_json(
    url: str,
    payload: dict,
    headers: dict,
    timeout: float,
    *,
    provider: str | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    retry_budget: float = DEFAULT_RETRY_BUDGET,
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
        max_attempts=max_attempts,
        retry_budget=retry_budget,
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
            raise TransportError(
                "LLM connection or response headers failed",
                provider=provider,
                operation="post_json",
                retryable=False,
            ) from error

    return _retry_loop(
        attempt,
        provider=provider,
        operation="post_json",
        max_attempts=max_attempts,
        base_backoff=DEFAULT_BASE_BACKOFF,
        max_backoff=DEFAULT_MAX_BACKOFF,
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
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    retry_budget: float = DEFAULT_RETRY_BUDGET,
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
        max_attempts=max_attempts,
        retry_budget=retry_budget,
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
            raise TransportError(
                "LLM connection or response headers failed",
                provider=provider,
                operation="post_sse",
                retryable=False,
            ) from error

    return _retry_loop(
        attempt,
        provider=provider,
        operation="post_sse",
        max_attempts=max_attempts,
        base_backoff=DEFAULT_BASE_BACKOFF,
        max_backoff=DEFAULT_MAX_BACKOFF,
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
        return on_event(event) is True

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
            raise TransportError(
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
