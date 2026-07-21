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

import json
import random
import time
import urllib.error
import urllib.request

from .models import LLMError, TransportError, parse_retry_after, status_is_retryable

# Attempts include the first try: 3 == one initial call plus two retries.
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_BACKOFF = 0.5
DEFAULT_MAX_BACKOFF = 8.0
# Ceiling on time spent sleeping across a call. A provider may advertise a
# 300s Retry-After; honouring that inside one turn would look like a hang.
DEFAULT_RETRY_BUDGET = 30.0


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


def _http_error(
    e: urllib.error.HTTPError, *, provider: str | None, operation: str
) -> TransportError:
    body = ""
    try:
        body = e.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - a body we cannot read must not mask the status
        pass
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


def _url_error(
    e: urllib.error.URLError, *, provider: str | None, operation: str
) -> TransportError:
    return TransportError(
        f"LLM connection error: {e.reason}",
        provider=provider,
        operation=operation,
        # Never reached the server, so nothing was committed and a replay is
        # safe. This is the one case where "no response" implies retryable.
        retryable=True,
    )


def _sleep_for(err: TransportError, attempt: int, base: float, cap: float) -> float:
    """Honour Retry-After when present, else exponential backoff with jitter."""
    if err.retry_after is not None:
        return err.retry_after
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
):
    # Resolved per call, not captured as a default: a default argument is
    # evaluated once at def time, which would pin the original time.sleep and
    # silently ignore any test that patches it.
    do_sleep = sleep if sleep is not None else time.sleep
    spent = 0.0
    for attempt in range(1, max_attempts + 1):
        try:
            return attempt_fn()
        except TransportError as err:
            last = err
            if not err.retryable or err.output_committed:
                raise
            if attempt >= max_attempts:
                raise
            delay = _sleep_for(err, attempt, base_backoff, max_backoff)
            if spent + delay > retry_budget:
                # Report the real reason rather than silently giving up: a
                # 300s Retry-After is a legitimate answer that this call is
                # simply not allowed to wait out.
                raise TransportError(
                    f"{last} (retry budget of {retry_budget}s exhausted; the "
                    f"provider asked for {delay:.1f}s more)",
                    provider=provider,
                    operation=operation,
                    status=err.status,
                    error_code=err.error_code,
                    headers=err.headers,
                    request_id=err.request_id,
                    retryable=True,
                    retry_after=err.retry_after,
                    body=err.body,
                ) from err
            if should_cancel is not None and should_cancel():
                raise TransportError(
                    f"{last} (cancelled before retry)",
                    provider=provider,
                    operation=operation,
                    status=err.status,
                    retryable=False,
                ) from err
            do_sleep(delay)
            spent += delay
            if should_cancel is not None and should_cancel():
                raise TransportError(
                    f"{last} (cancelled before retry)",
                    provider=provider,
                    operation=operation,
                    status=err.status,
                    retryable=False,
                ) from err
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
) -> dict:
    """POST JSON and decode the whole response.

    Retryable because it is all-or-nothing: the caller sees the response only
    once it is complete, so a replayed attempt cannot duplicate output.
    """
    data = json.dumps(payload).encode("utf-8")

    def attempt() -> dict:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise _http_error(e, provider=provider, operation="post_json") from e
        except urllib.error.URLError as e:
            raise _url_error(e, provider=provider, operation="post_json") from e

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

    def attempt() -> None:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            raise _http_error(e, provider=provider, operation="post_sse") from e
        except urllib.error.URLError as e:
            raise _url_error(e, provider=provider, operation="post_sse") from e
        _consume(resp, on_event, provider=provider)

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
    )


def _consume(resp, on_event, *, provider: str | None) -> None:
    data_lines: list[str] = []
    committed = False

    def dispatch() -> None:
        nonlocal committed
        if not data_lines:
            return
        chunk = "\n".join(data_lines).strip()
        data_lines.clear()
        if not chunk or chunk == "[DONE]":
            return
        try:
            event = json.loads(chunk)
        except ValueError as e:
            raise LLMError(f"invalid JSON in LLM event stream: {chunk[:400]}") from e
        if not isinstance(event, dict):
            raise LLMError("LLM event stream yielded a non-object JSON event")
        committed = True
        on_event(event)

    try:
        try:
            for raw in resp:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not line:
                    dispatch()
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    value = line[5:]
                    data_lines.append(value[1:] if value.startswith(" ") else value)
            dispatch()
        except LLMError:
            raise
        except Exception as e:  # noqa: BLE001 - normalize transport read failures
            # A mid-stream read failure after events were delivered must not be
            # replayed: the caller already saw partial output.
            raise TransportError(
                f"LLM event stream read error: {e}",
                provider=provider,
                operation="post_sse",
                retryable=not committed,
                output_committed=committed,
            ) from e
    finally:
        try:
            resp.close()
        except Exception:  # noqa: BLE001
            pass
