"""Shared provider-layer errors and lightweight types."""

from __future__ import annotations

import email.utils
import time


class LLMError(RuntimeError):
    """Normalized failure raised by every LLM transport and provider."""

    usage: object = None


class MissingCredentialError(LLMError):
    """No API key is configured for a provider that needs one.

    Raised before any request is built. A subclass so every ``except
    LLMError`` keeps working; its own type so a daemon can tell this expected,
    user-fixable refusal from a real failure without matching on prose.
    """


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
        #: Backoff waits the logical call took before this error ended it, set
        #: by the transport's retry loop (``None``: not recorded, e.g. raised
        #: outside it). What "retries were attempted" may truthfully rest on.
        self.retries_attempted: int | None = None
        #: Why the retry loop stopped *early*, as a local, closed value:
        #: ``"budget"`` (the next wait exceeded the retry budget) or
        #: ``"deadline"`` (it exceeded what was left of the total timeout), or
        #: ``"request_limit"`` (this invocation's explicit attempt cap).
        self.retry_stop: str | None = None
        #: Internal fact, set only by the transport's ``_url_error``: the
        #: connection was refused or the host name did not resolve, and the
        #: logical call had not yet written a request byte. It names a cause
        #: (``llm_endpoint_unreachable``); it never decides a retry, and it is
        #: not part of ``to_dict``.
        self.endpoint_unreachable: bool = False

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


class LLMDeadlineExceeded(TransportError):
    """The local logical-call deadline expired; this request is not replayable."""


class LLMResponseTooLarge(TransportError):
    """A local input or buffer limit refused further model output."""


class StreamReadError(TransportError):
    """A response stream failed while being read, after connecting."""


class StreamTimeoutError(StreamReadError):
    """The upstream stopped sending bytes for the configured read timeout."""


# Provider error codes are untrusted strings, not a public protocol.  Only
# exact values named here may influence a user-facing classification; the raw
# value remains on ``TransportError.error_code`` for private diagnostics.
_REQUEST_BURST_CODES = frozenset({"RequestBurstTooFast"})
_UPSTREAM_OVERLOADED_CODES = frozenset({"ServerOverloaded"})


def llm_failure_code(exc: BaseException) -> str | None:
    """Return a stable, locally controlled LLM failure classification.

    Never return the provider's raw ``error.code``.  Providers do not treat
    that field as a closed enum, and some proxies have put whole sentences in
    it.  Consumers such as the gateway and browser may safely branch on the
    small vocabulary returned here without parsing exception prose.
    """

    if isinstance(exc, LLMDeadlineExceeded):
        return "llm_deadline_exceeded"
    if isinstance(exc, LLMResponseTooLarge):
        return "llm_response_too_large"
    if isinstance(exc, StreamTimeoutError):
        return "llm_stream_timeout"
    if isinstance(exc, StreamReadError):
        return "llm_stream_interrupted"
    raw_code = getattr(exc, "error_code", None)
    code = raw_code if type(raw_code) is str else ""
    if code in _REQUEST_BURST_CODES:
        return "llm_request_burst"
    if code in _UPSTREAM_OVERLOADED_CODES:
        return "llm_upstream_overloaded"
    if getattr(exc, "status", None) == 429:
        return "llm_rate_limited"
    return None


# Exact provider codes only, for the same reason as the sets above.
_AUTH_REJECTED_CODES = frozenset({"invalid_api_key", "unauthorized"})
_MODEL_NOT_FOUND_CODES = frozenset({"model_not_found"})
_QUOTA_EXHAUSTED_CODES = frozenset({"insufficient_quota"})
#: The provider machine codes that turn a 404 into "this model is not
#: available" (`llm_model_unavailable`). Deliberately an explicit allowlist:
#: every other 404 -- a wrong base URL path, a proxy's own page, a provider
#: code nobody listed here -- says only that the request target was not found.
_MODEL_UNAVAILABLE_CODES = frozenset({"model_not_found"})


def llm_error_kind(exc: BaseException) -> str | None:
    """What a person has to do about a failed model call, as a closed value.

    A superset of `llm_failure_code`, for the surface that tells the user. It
    adds the failures that are fixed in Settings rather than by waiting:

    * ``llm_no_credential`` -- no API key is configured at all;
    * ``llm_authentication_failed`` -- the service did not accept the
      credential (HTTP 401);
    * ``llm_access_denied`` -- the service refused access (HTTP 403);
    * ``llm_quota_exhausted`` -- the account is out of quota (402), which a
      retry policy cannot help and a rate limit can;
    * ``llm_model_unavailable`` -- a 404 whose provider code is in
      ``_MODEL_UNAVAILABLE_CODES``;
    * ``llm_request_target_not_found`` -- any other 404: check the endpoint
      and the model id;
    * ``llm_endpoint_unreachable`` -- the connection was refused or the host
      did not resolve before a request byte was written (the transport's
      ``endpoint_unreachable`` fact);
    * ``llm_network`` -- any other failure without an HTTP answer (connect
      timeout, TLS, a reset).

    Two codes from the first version remain for structured signals the
    status-based ones above do not cover: ``llm_auth_rejected`` (an
    ``invalid_api_key``/``unauthorized`` code on another status) and
    ``llm_model_not_found`` (``model_not_found`` on a status other than 404).

    Kept apart from `llm_failure_code` because that one also feeds the retry
    records and the recovery ledger, where these would be new vocabulary.
    ``None`` is "no claim": every other failure keeps the generic message.
    """

    if isinstance(exc, MissingCredentialError):
        return "llm_no_credential"
    raw_code = getattr(exc, "error_code", None)
    code = raw_code if type(raw_code) is str else ""
    status = getattr(exc, "status", None)
    if status == 402 or code in _QUOTA_EXHAUSTED_CODES:
        return "llm_quota_exhausted"
    known = llm_failure_code(exc)
    if known is not None:
        return known
    if status == 401:
        return "llm_authentication_failed"
    if status == 403:
        return "llm_access_denied"
    if code in _AUTH_REJECTED_CODES:
        return "llm_auth_rejected"
    if status == 404:
        if code in _MODEL_UNAVAILABLE_CODES:
            return "llm_model_unavailable"
        return "llm_request_target_not_found"
    if code in _MODEL_NOT_FOUND_CODES:
        return "llm_model_not_found"
    if isinstance(exc, TransportError) and status is None:
        if getattr(exc, "endpoint_unreachable", False) is True:
            return "llm_endpoint_unreachable"
        return "llm_network"
    return None


#: What to do about each failure that is fixed in Settings, as (zh, en). Every
#: sentence is written here; nothing is taken from the exception's own text.
_SETUP_MESSAGES: dict[str, tuple[str, str]] = {
    "llm_no_credential": (
        "**还没有配置模型的 API Key。** 请在 设置 → 模型 填写 API Key 后再发送；"
        "也可以在 `.env` 设置 `OPENAI4S_LLM_API_KEY` 后重启。",
        "**No API key is configured for the model.** Add one in Settings → "
        "Models, then send again; or set `OPENAI4S_LLM_API_KEY` in `.env` and "
        "restart.",
    ),
    "llm_auth_rejected": (
        "**模型服务拒绝了这个 API Key（401/403）。** 请在 设置 → 模型 检查 Key "
        "是否正确、是否过期，以及它是否属于当前的服务地址。",
        "**The model provider rejected the API key (401/403).** In Settings → "
        "Models, check that the key is correct, not expired, and belongs to "
        "this endpoint.",
    ),
    "llm_quota_exhausted": (
        "**模型账户的额度已用完。** 重试不会恢复：请到服务商处充值或更换套餐，"
        "或在 设置 → 模型 换一个模型配置。",
        "**The model account is out of quota.** Retrying will not help: top up "
        "or change the plan with the provider, or pick another model in "
        "Settings → Models.",
    ),
    "llm_model_not_found": (
        "**模型服务找不到这个模型或接口（404）。** 请在 设置 → 模型 检查模型名称"
        "和服务地址。",
        "**The model provider has no such model or route (404).** Check the "
        "model name and the base URL in Settings → Models.",
    ),
    "llm_authentication_failed": (
        "**模型服务未接受身份凭据（HTTP 401）。** 请在 设置 → 模型 检查 API Key "
        "是否正确、是否过期，以及它是否属于当前的服务地址。",
        "**The model service did not accept the credentials (HTTP 401).** In "
        "Settings → Models, check that the API key is correct, not expired, and "
        "belongs to this endpoint.",
    ),
    "llm_access_denied": (
        "**模型服务拒绝了访问（HTTP 403）。** 请在 设置 → 模型 检查这个账户或 Key "
        "是否有权使用所选的模型和服务地址。",
        "**The model service refused access (HTTP 403).** In Settings → Models, "
        "check that this account or key may use the selected model and endpoint.",
    ),
    "llm_model_unavailable": (
        "**模型服务报告这个模型不可用（HTTP 404）。** 请在 设置 → 模型 检查模型 ID，"
        "或改用该服务提供的模型。",
        "**The model service reports that this model is not available (HTTP "
        "404).** In Settings → Models, check the model id or choose a model this "
        "service offers.",
    ),
    "llm_request_target_not_found": (
        "**模型服务上找不到所请求的地址（HTTP 404）。** 请在 设置 → 模型 检查服务地址"
        "和模型 ID。",
        "**The model service found nothing at the requested address (HTTP "
        "404).** Check the endpoint and the model id in Settings → Models.",
    ),
    "llm_endpoint_unreachable": (
        "**无法连接到模型服务地址：连接被拒绝或域名无法解析，请求没有发出。** 请在 "
        "设置 → 模型 检查服务地址，并确认该服务正在运行、本机网络可用。",
        "**The model endpoint could not be reached: the connection was refused "
        "or the host name did not resolve, so no request was sent.** Check the "
        "base URL in Settings → Models, and that the service is running and this "
        "machine is online.",
    ),
    "llm_network": (
        "**无法连接到模型服务（或请求中断）。** 请检查网络、代理，以及 设置 → 模型 "
        "里的服务地址。",
        "**The model service could not be reached, or the request was "
        "interrupted.** Check the network, any proxy, and the base URL in "
        "Settings → Models.",
    ),
}
_TLS_NOTE = (
    " TLS 证书校验失败（{detail}）：若经过公司代理，请把 `SSL_CERT_FILE` 指向"
    "包含其根证书的 PEM 文件后重启。",
    " The TLS certificate could not be verified ({detail}): behind a corporate "
    "proxy, point `SSL_CERT_FILE` at a PEM bundle containing its root and "
    "restart.",
)


def llm_setup_message(exc: BaseException, *, zh: bool) -> str | None:
    """The sentence for a failure the user fixes in Settings, else ``None``."""

    pair = _SETUP_MESSAGES.get(llm_error_kind(exc) or "")
    if pair is None:
        return None
    text = pair[0 if zh else 1]
    if llm_error_kind(exc) == "llm_network":
        from openai4s.tls_trust import certificate_failure

        failure = certificate_failure(exc)
        if failure is not None:
            # OpenSSL's own verify message: a fixed vocabulary, not the URL,
            # the request or anything a provider sent.
            detail = str(getattr(failure, "verify_message", "") or "verify failed")
            text += _TLS_NOTE[0 if zh else 1].format(detail=detail)
    return text


def llm_retry_outcome(exc: BaseException) -> str | None:
    """How the transport's retry policy ended a failed call, from typed fields.

    ``"committed"`` (output reached the caller, so replay was vetoed),
    ``"deadline"`` / ``"budget"`` (the next wait did not fit), ``"not_retried"``
    or ``"retried"``, or ``"request_limit"`` (an invocation exhausted its own
    cap); ``None`` when the error carries no record of it. Lets a user-facing
    message say "retries were attempted" only when they were.
    """

    if getattr(exc, "output_committed", False):
        return "committed"
    stop = getattr(exc, "retry_stop", None)
    if stop in ("deadline", "budget", "request_limit"):
        return str(stop)
    retries = getattr(exc, "retries_attempted", None)
    if type(retries) is not int:
        return None
    return "retried" if retries > 0 else "not_retried"


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
