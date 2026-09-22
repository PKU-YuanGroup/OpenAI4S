"""Credential-brokered TypeSafe System One client.

Mirrors ``openai4s.doubao_search``: the key is resolved immediately before
each request, never copied into process-global state, and every exception or
log line is redacted with ``redact_reflected_secret`` before it leaves this
module. The HTTP contract is the official System One endpoint, not the SDK.
"""

from __future__ import annotations

import dataclasses
import email.utils
import json
import logging
import math
import os
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from openai4s import egress
from openai4s.http_deadline import (
    HTTPExchangeDeadline,
    HTTPExchangeTimeout,
    read_body_capped,
)
from openai4s.judgment.port import BackendError
from openai4s.judgment.types import BackendReply, Question
from openai4s.judgment.validate import parse_response
from openai4s.mcp_protocol import redact_reflected_secret

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_FAKE_ENV = "OPENAI4S_JUDGMENT_FAKE_ENDPOINT"
_KEY_ENV = "OPENAI4S_TYPESAFE_API_KEY"
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
_RETRYABLE_CODES = frozenset({"rate_limited", "overloaded"})
_BACKOFF_BASE_S = 0.25
_BACKOFF_CAP_S = 8.0
_MIN_RETRY_REMAINING_S = 0.01

_LOG = logging.getLogger(__name__)


class TypeSafeStore(Protocol):
    """SecretBroker-backed Store subset needed to resolve the TypeSafe key."""

    def get_secret_setting(self, key: str, *, scope: str | None = None) -> str: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect so the Bearer credential stays on one origin."""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _require_loopback_http_url(raw: str) -> str:
    """Accept only ``http://127.0.0.1:<port>/...`` or ``http://[::1]:<port>/...``."""

    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError as exc:
        raise ValueError("OPENAI4S_JUDGMENT_FAKE_ENDPOINT is not a valid URL") from exc
    if parsed.scheme != "http":
        raise ValueError(
            "OPENAI4S_JUDGMENT_FAKE_ENDPOINT must be http://127.0.0.1:<port>/... "
            "or http://[::1]:<port>/..."
        )
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("OPENAI4S_JUDGMENT_FAKE_ENDPOINT must not carry credentials")
    if parsed.query or parsed.fragment:
        raise ValueError(
            "OPENAI4S_JUDGMENT_FAKE_ENDPOINT must not carry a query or fragment"
        )
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(
            "OPENAI4S_JUDGMENT_FAKE_ENDPOINT must include an explicit port"
        ) from exc
    host = parsed.hostname
    if port is None:
        raise ValueError(
            "OPENAI4S_JUDGMENT_FAKE_ENDPOINT must include an explicit port"
        )
    path = parsed.path or "/"
    if host == "127.0.0.1":
        return f"http://127.0.0.1:{port}{path}"
    if host == "::1":
        return f"http://[::1]:{port}{path}"
    raise ValueError("OPENAI4S_JUDGMENT_FAKE_ENDPOINT must target 127.0.0.1 or [::1]")


def _configured_endpoint() -> tuple[str, bool]:
    raw = os.environ.get(_FAKE_ENV)
    if raw is None or not str(raw).strip():
        return ENDPOINT, False
    return _require_loopback_http_url(str(raw).strip()), True


def _parse_retry_after(value: str | None, *, now: float | None = None) -> float | None:
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


def _redact(value: Any, secret: str) -> Any:
    return redact_reflected_secret(value, secret)


def _safe_text(value: Any, secret: str) -> str:
    redacted = _redact(str(value), secret)
    if isinstance(redacted, str):
        return redacted
    return "[REDACTED]"


def _backend_error(code: str, message: str, secret: str) -> BackendError:
    safe = _safe_text(message, secret)
    _LOG.warning("typesafe backend error %s: %s", code, safe)
    return BackendError(code, safe)


def _retry_delay(error: BackendError, attempt: int) -> float:
    retry_after = getattr(error, "retry_after", None)
    if isinstance(retry_after, (int, float)) and not isinstance(retry_after, bool):
        if math.isfinite(float(retry_after)):
            return max(0.0, float(retry_after))
    backoff = min(_BACKOFF_CAP_S, _BACKOFF_BASE_S * (2 ** max(0, attempt - 1)))
    return random.uniform(0.0, backoff)


class TypeSafeBackend:
    """``JudgmentBackend`` that POSTs to TypeSafe System One over stdlib HTTP."""

    ENDPOINT = ENDPOINT

    def __init__(
        self,
        store_provider: Callable[[], TypeSafeStore],
        *,
        opener: Any | None = None,
    ) -> None:
        if not callable(store_provider):
            raise TypeError("store_provider must be callable")
        self._store_provider = store_provider
        self._opener = opener
        self._endpoint, self._fake = _configured_endpoint()

    def evaluate(
        self,
        *,
        state: object,
        questions: Mapping[str, Question],
        model: str,
        timeout: float,
    ) -> BackendReply:
        key = self._resolved_key()
        if not self._fake:
            try:
                egress.check_url(self._endpoint)
            except egress.EgressBlocked as exc:
                raise _backend_error("egress_blocked", str(exc), key) from None
        try:
            budget = float(timeout)
        except (TypeError, ValueError):
            raise _backend_error(
                "invalid_request", "timeout must be a finite number", key
            ) from None
        if not math.isfinite(budget) or budget <= 0:
            raise _backend_error("timeout", "TypeSafe request timed out", key)

        deadline = time.monotonic() + budget
        last_error: BackendError | None = None
        attempt = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if last_error is not None:
                    raise last_error
                raise _backend_error("timeout", "TypeSafe request timed out", key)
            try:
                return self._exchange(
                    key,
                    state=state,
                    questions=questions,
                    model=model,
                    timeout=remaining,
                )
            except BackendError as err:
                if err.code not in _RETRYABLE_CODES:
                    raise
                last_error = err
                attempt += 1
                delay = _retry_delay(err, attempt)
                remaining = deadline - time.monotonic()
                if delay > remaining or remaining - delay < _MIN_RETRY_REMAINING_S:
                    raise err
                _LOG.info(
                    "typesafe backend retrying after %s (attempt %s, sleep %.3fs)",
                    err.code,
                    attempt,
                    delay,
                )
                if delay > 0:
                    time.sleep(delay)

    def _resolved_key(self) -> str:
        env = os.environ.get(_KEY_ENV)
        if isinstance(env, str) and env.strip():
            key = env.strip()
        else:
            try:
                store = self._store_provider()
                raw = store.get_secret_setting("typesafe_api_key")
                key = str(raw or "").strip()
            except BackendError:
                raise
            except Exception:
                key = ""
        if not key or any(ch in key for ch in ("\r", "\n", "\x00")):
            raise BackendError("unconfigured")
        return key

    def _exchange(
        self,
        key: str,
        *,
        state: object,
        questions: Mapping[str, Question],
        model: str,
        timeout: float,
    ) -> BackendReply:
        if not isinstance(questions, Mapping) or not questions:
            raise _backend_error(
                "invalid_request", "questions must be a non-empty mapping", key
            )
        if not isinstance(model, str) or not model.strip():
            raise _backend_error(
                "invalid_request", "model must be a non-empty string", key
            )
        try:
            encoded_questions = {
                str(qid): question.to_api() for qid, question in questions.items()
            }
            body = json.dumps(
                {
                    "model": model.strip(),
                    "state": state,
                    "questions": encoded_questions,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise _backend_error(
                "invalid_request",
                f"request could not be encoded ({type(exc).__name__})",
                key,
            ) from None

        request = urllib.request.Request(
            self._endpoint,
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
        )
        response = None
        raw = b""
        request_id: str | None = None
        try:
            with HTTPExchangeDeadline(timeout) as exchange:
                opener = self._opener or exchange.build_opener(_NoRedirect)
                response = exchange.open(opener, request)
                status = int(getattr(response, "status", None) or response.getcode())
                request_id = _header(response, "X-Request-Id") or _header(
                    response, "Request-Id"
                )
                if status in _REDIRECT_CODES:
                    raise _backend_error(
                        "unavailable",
                        f"TypeSafe redirect was refused (status {status})",
                        key,
                    )
                if status != 200:
                    raise _status_error(status, response, key)
                raw = read_body_capped(
                    response,
                    limit=MAX_RESPONSE_BYTES,
                    exchange=exchange,
                    on_timeout=lambda: _backend_error(
                        "timeout", "TypeSafe request timed out", key
                    ),
                    on_oversize=lambda: _backend_error(
                        "invalid_response",
                        f"TypeSafe response exceeded the {MAX_RESPONSE_BYTES}-byte limit",
                        key,
                    ),
                    on_truncated=lambda: _backend_error(
                        "unavailable",
                        "TypeSafe response ended before its declared length",
                        key,
                    ),
                    on_unbounded=(
                        (
                            lambda: _backend_error(
                                "unavailable",
                                "TypeSafe response has no bounded read transport",
                                key,
                            )
                        )
                        if self._opener is None
                        else None
                    ),
                )
        except BackendError:
            raise
        except urllib.error.HTTPError as error:
            status = int(error.code)
            mapped = _status_error(status, error, key)
            try:
                error.close()
            except Exception:  # noqa: BLE001 - best-effort socket cleanup
                pass
            raise mapped from None
        except (socket.timeout, TimeoutError, HTTPExchangeTimeout):
            raise _backend_error("timeout", "TypeSafe request timed out", key) from None
        except urllib.error.URLError:
            raise _backend_error(
                "unavailable", "TypeSafe request failed (network error)", key
            ) from None
        except OSError:
            raise _backend_error(
                "unavailable", "TypeSafe request failed (network error)", key
            ) from None
        except Exception as exc:  # noqa: BLE001 - project a controlled boundary
            raise _backend_error(
                "unavailable",
                f"TypeSafe request failed ({type(exc).__name__})",
                key,
            ) from None
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:  # noqa: BLE001 - best-effort socket cleanup
                    pass

        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise _backend_error(
                "invalid_response", "TypeSafe returned invalid JSON", key
            ) from None
        decoded = _redact(decoded, key)
        if not isinstance(decoded, dict):
            raise _backend_error(
                "invalid_response", "TypeSafe returned a non-object response", key
            )
        try:
            reply = parse_response(decoded, questions)
        except BackendError as err:
            raise _backend_error(err.code, err.message or err.code, key) from None
        if request_id is not None:
            request_id = _safe_text(request_id, key)
        if request_id or self._fake:
            reply = dataclasses.replace(reply, request_id=request_id, fake=self._fake)
        return reply


def _header(response: Any, name: str) -> str | None:
    try:
        headers = response.headers
    except Exception:  # noqa: BLE001
        return None
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    value = getter(name)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _status_error(status: int, response: Any, key: str) -> BackendError:
    if status in _REDIRECT_CODES:
        return _backend_error(
            "unavailable",
            f"TypeSafe redirect was refused (status {status})",
            key,
        )
    if status in (400, 422):
        code = "invalid_request"
    elif status in (401, 403):
        code = "auth"
    elif status == 429:
        code = "rate_limited"
    elif status in (503, 529):
        code = "overloaded"
    elif 500 <= status <= 599:
        code = "unavailable"
    else:
        code = "invalid_request"
    error = _backend_error(code, f"TypeSafe request failed with HTTP {status}", key)
    retry_after = _parse_retry_after(_header(response, "Retry-After"))
    if retry_after is not None:
        setattr(error, "retry_after", retry_after)
    return error


__all__ = [
    "ENDPOINT",
    "MAX_RESPONSE_BYTES",
    "TypeSafeBackend",
    "TypeSafeStore",
]
