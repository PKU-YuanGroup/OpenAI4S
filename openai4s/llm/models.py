"""Shared provider-layer errors and lightweight types."""

from __future__ import annotations

import email.utils
import time


class LLMError(RuntimeError):
    """Normalized failure raised by every LLM transport and provider."""


class TransportError(LLMError):
    """An HTTP/transport failure with its evidence intact.

    Every transport failure used to be flattened into an f-string —
    ``LLMError(f"LLM HTTP {e.code}: {detail}")`` — which threw away the status
    code, the response headers, and with them ``Retry-After``. A caller could
    not tell a 429 from a 401 without parsing English, so nothing retried and a
    rate limit surfaced as a hard failure on the first attempt. The repo's own
    golden trace records that as `rate_limit_single_attempt`.

    Subclasses LLMError on purpose: existing `except LLMError` handlers keep
    working unchanged, and callers that want the detail opt in.

    ``output_committed`` is the retry veto. Once a stream has handed bytes to
    the caller — or a tool has run — a transparent retry would duplicate
    visible output or re-fire a side effect, so it is never safe regardless of
    how retryable the status looks.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        operation: str | None = None,
        status: int | None = None,
        error_code: str | None = None,
        headers: dict[str, str] | None = None,
        request_id: str | None = None,
        retryable: bool = False,
        retry_after: float | None = None,
        output_committed: bool = False,
        body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.operation = operation
        self.status = status
        self.error_code = error_code
        self.headers = headers or {}
        self.request_id = request_id
        self.retryable = retryable
        self.retry_after = retry_after
        self.output_committed = output_committed
        self.body = body

    @property
    def is_rate_limit(self) -> bool:
        return self.status == 429

    def to_dict(self) -> dict:
        """Structured projection for logs and the error envelope."""
        return {
            "provider": self.provider,
            "operation": self.operation,
            "status": self.status,
            "error_code": self.error_code,
            "request_id": self.request_id,
            "retryable": self.retryable,
            "retry_after": self.retry_after,
            "output_committed": self.output_committed,
            "message": str(self),
        }


# 408 request timeout, 429 rate limit, 5xx server-side. 5xx is retryable for a
# *whole-response* POST because nothing was committed; a stream that already
# emitted events is vetoed by output_committed instead of by status.
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


def status_is_retryable(status: int | None) -> bool:
    return status in _RETRYABLE_STATUS


def parse_retry_after(value: str | None, *, now: float | None = None) -> float | None:
    """Parse a Retry-After header into seconds.

    RFC 9110 permits either a delta in seconds or an HTTP-date; providers use
    both. Returns None when absent or unparseable, and never a negative delay
    (a date already in the past means "now", not "go back in time").
    """
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        return max(0.0, float(int(raw)))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    reference = now if now is not None else time.time()
    return max(0.0, when.timestamp() - reference)
