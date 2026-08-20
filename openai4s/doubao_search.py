"""Credential-brokered client for the fixed Doubao Search endpoint.

The Agent Plan Key is resolved from the calling :class:`Store` immediately
before each request.  It is never copied into process-global state, persisted
as connector configuration, or included in a returned value or exception.
"""

from __future__ import annotations

import json
import math
import socket
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from openai4s import datapro, egress, webtools
from openai4s.http_deadline import HTTPExchangeDeadline, read_body_capped
from openai4s.mcp_protocol import redact_reflected_secret

ENDPOINT = "https://open.feedcoopapi.com/search_api/web_search"
TRAFFIC_TAG = "ark_mcp_server_web_search"
MAX_QUERY_CHARS = 100
MAX_RESULTS = 50
MAX_RESPONSE_BYTES = 4 * 1024 * 1024

_AUTH_OR_QUOTA_CODES = frozenset(
    {
        "429",
        "10403",
        "10406",
        "10407",
        "10408",
        "10409",
        "10412",
        "100013",
        "700429",
        "700901",
        "flowlimitexceeded",
        "functionunavailable",
        "invalid_api_key",
    }
)
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


class DoubaoSearchStore(Protocol):
    """SecretBroker-backed Store subset needed by this integration."""

    def get_setting(self, key: str, default: str | None = None) -> str | None: ...

    def get_secret_setting(self, key: str) -> str: ...


class DoubaoSearchError(RuntimeError):
    """Base controlled failure for the managed search provider."""

    error_code = "doubao_search_failed"


class DoubaoSearchAuthError(DoubaoSearchError):
    """The configured key, entitlement, rate, or quota was rejected."""

    error_code = "doubao_search_auth_or_quota"


class DoubaoSearchResponseError(DoubaoSearchError):
    """The provider returned an invalid or refused response."""

    error_code = "doubao_search_response_invalid"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect so the Bearer credential stays on one origin."""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _query(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Doubao search query must be a string")
    query = value.strip()
    if not query:
        raise ValueError("Doubao search query is required")
    if len(query) > MAX_QUERY_CHARS:
        raise ValueError(
            f"Doubao search query must be at most {MAX_QUERY_CHARS} characters"
        )
    return query


def _count(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("Doubao search result count must be an integer")
    try:
        count = int(value)
    except (TypeError, ValueError):
        raise ValueError("Doubao search result count must be an integer") from None
    if count < 1 or count > MAX_RESULTS:
        raise ValueError(
            f"Doubao search result count must be between 1 and {MAX_RESULTS}"
        )
    return count


def _timeout(value: Any) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        raise ValueError("Doubao search timeout must be a finite number") from None
    if not math.isfinite(timeout) or timeout < 1 or timeout > 120:
        raise ValueError("Doubao search timeout must be between 1 and 120 seconds")
    return timeout


def _read_capped(
    response: Any,
    *,
    limit: int,
    exchange: HTTPExchangeDeadline,
    require_bound: bool = True,
) -> bytes:
    """This service's failure vocabulary over the shared bounded reader.

    Every property of the loop -- ``read1`` over buffered ``read``, the
    absolute deadline, the byte cap, stopping at end-of-body, refusing a
    truncated body -- is stated once in ``http_deadline`` and shared with the
    MCP transport, which had to be given each of them separately while the two
    loops were maintained side by side.
    """

    def _oversize() -> BaseException:
        return DoubaoSearchResponseError(
            f"Doubao search response exceeded the {limit}-byte limit"
        )

    return read_body_capped(
        response,
        limit=limit,
        exchange=exchange,
        on_timeout=lambda: DoubaoSearchError("Doubao search request timed out"),
        on_oversize=_oversize,
        on_truncated=lambda: DoubaoSearchError(
            "Doubao search response ended before its declared length"
        ),
        # An injected opener is a test transport with no socket beneath it.  The
        # daemon's own opener always has one, and a body read with no bound
        # there is the slow-drip exposure this module exists to close.
        on_unbounded=(
            (
                lambda: DoubaoSearchError(
                    "Doubao search response has no bounded read transport"
                )
            )
            if require_bound
            else None
        ),
    )


def _safe_error_code(value: Any) -> str:
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        text = value.strip()
        # Provider-defined symbolic codes are admitted only as short ASCII
        # identifiers.  Free-form upstream text (which may reflect a key) is
        # never placed in an exception.
        if 0 < len(text) <= 64 and all(
            ch.isascii() and (ch.isalnum() or ch in "_.-") for ch in text
        ):
            return text
    return ""


def _provider_error(payload: Mapping[str, Any]) -> tuple[str, bool] | None:
    metadata = payload.get("ResponseMetadata")
    if not isinstance(metadata, Mapping):
        return None
    error = metadata.get("Error")
    if not isinstance(error, Mapping) or not error:
        return None
    codes = [
        code
        for code in (
            _safe_error_code(error.get("Code")),
            _safe_error_code(error.get("CodeN")),
        )
        if code
    ]
    code = codes[0] if codes else ""
    return code, any(
        candidate.casefold() in _AUTH_OR_QUOTA_CODES for candidate in codes
    )


def _redact_provider_payload(payload: Any, secret: str) -> Any:
    """Scrub provider data while restoring only the trusted wire skeleton.

    A short (invalid but accepted) credential may occur inside protocol field
    names such as ``Result`` or ``WebResults``. The generic redactor must still
    replace it in arbitrary mapping keys, so rebuild the handful of fixed keys
    consumed by this adapter from their original locations. Values remain
    recursively redacted, and provider-only fields are never projected.
    """

    safe = redact_reflected_secret(payload, secret)
    if not isinstance(payload, Mapping) or not isinstance(safe, dict):
        return safe

    metadata = payload.get("ResponseMetadata")
    if isinstance(metadata, Mapping):
        safe_metadata = redact_reflected_secret(metadata, secret)
        if not isinstance(safe_metadata, dict):
            safe_metadata = {}
        error = metadata.get("Error")
        if isinstance(error, Mapping):
            safe_error = redact_reflected_secret(error, secret)
            if not isinstance(safe_error, dict):
                safe_error = {}
            for field in ("Code", "CodeN"):
                if field in error:
                    safe_error[field] = redact_reflected_secret(error[field], secret)
            safe_metadata["Error"] = safe_error
        safe["ResponseMetadata"] = safe_metadata

    result = payload.get("Result")
    if isinstance(result, Mapping):
        safe_result = redact_reflected_secret(result, secret)
        if not isinstance(safe_result, dict):
            safe_result = {}
        web_results = result.get("WebResults")
        if isinstance(web_results, list):
            safe_items: list[Any] = []
            for item in web_results:
                safe_item = redact_reflected_secret(item, secret)
                if isinstance(item, Mapping):
                    if not isinstance(safe_item, dict):
                        safe_item = {}
                    for field in ("Title", "Url", "Snippet", "Summary", "Content"):
                        if field in item:
                            safe_item[field] = redact_reflected_secret(
                                item[field], secret
                            )
                safe_items.append(safe_item)
            safe_result["WebResults"] = safe_items
        safe["Result"] = safe_result
    return safe


def _text(item: Mapping[str, Any], field: str, *, required: bool = False) -> str:
    value = item.get(field)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise DoubaoSearchResponseError(
            f"Doubao search returned an invalid {field} field"
        )
    return value.strip()


def _result_url(item: Mapping[str, Any]) -> str:
    url = _text(item, "Url")
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlsplit(url)
        _port = parsed.port
    except ValueError:
        raise DoubaoSearchResponseError(
            "Doubao search returned an invalid Url field"
        ) from None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise DoubaoSearchResponseError("Doubao search returned an invalid Url field")
    if parsed.username is not None or parsed.password is not None:
        raise DoubaoSearchResponseError(
            "Doubao search returned a credential-bearing Url field"
        )
    return url


def _normalize(payload: Any, *, query: str, limit: int) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise DoubaoSearchResponseError("Doubao search returned a non-object response")

    provider_error = _provider_error(payload)
    if provider_error is not None:
        code, auth_or_quota = provider_error
        suffix = f" (code {code})" if code else ""
        if auth_or_quota:
            raise DoubaoSearchAuthError(
                "Doubao search rejected the Agent Plan Key, entitlement, rate, "
                f"or quota{suffix}"
            )
        raise DoubaoSearchResponseError(
            f"Doubao search upstream rejected the request{suffix}"
        )

    result = payload.get("Result")
    if not isinstance(result, Mapping):
        raise DoubaoSearchResponseError(
            "Doubao search response did not contain a valid Result object"
        )
    raw_results = result.get("WebResults")
    if not isinstance(raw_results, list):
        raise DoubaoSearchResponseError(
            "Doubao search response did not contain a valid WebResults list"
        )

    results: list[dict[str, Any]] = []
    for raw in raw_results[:limit]:
        if not isinstance(raw, Mapping):
            raise DoubaoSearchResponseError(
                "Doubao search returned an invalid WebResults item"
            )
        summary = _text(raw, "Summary")
        content = _text(raw, "Content")
        normalized: dict[str, Any] = {
            # A blank title is data, not a structural violation -- aggregator
            # rows routinely have one.  ``_text`` still rejects a wrong *type*,
            # and ``_result_url`` already tolerates a missing Url the same way;
            # requiring it here discarded every other result in the response.
            "title": _text(raw, "Title"),
            "url": _result_url(raw),
            "snippet": _text(raw, "Snippet") or summary or content,
        }
        # Preserve the established web_search result envelope exactly.  The
        # Host's untrusted-output scanner covers title + snippet; publishing
        # additional provider prose here would create an unscreened path into
        # the Agent/audit result.  Summary/Content are admitted only as the
        # screened snippet fallback above.
        results.append(normalized)

    return {
        "query": query,
        "count": len(results),
        "results": results,
        "source": "doubao",
    }


class DoubaoSearchService:
    """Search through Doubao with one just-in-time Agent Plan credential."""

    def __init__(
        self,
        store: DoubaoSearchStore,
        *,
        opener: Any | None = None,
    ) -> None:
        self.store = store
        self._opener = opener

    def configured(self) -> bool:
        """Whether this Store can currently resolve an Agent Plan Key."""

        try:
            self._resolved_key()
        except DoubaoSearchAuthError:
            return False
        return True

    def _resolved_key(self) -> str:
        key = str(datapro.resolve_agent_plan_key(self.store) or "").strip()
        if not key:
            raise DoubaoSearchAuthError("Agent Plan Key is not configured")
        if (
            len(key) < datapro.MIN_AGENT_PLAN_KEY_CHARS
            or len(key) > 8_192
            or any(ch in key for ch in ("\r", "\n", "\x00"))
        ):
            raise DoubaoSearchAuthError("Agent Plan Key is invalid")
        return key

    @staticmethod
    def _check_network_policy() -> None:
        if not webtools.network_allowed():
            raise webtools.NetworkDisabled(
                "networking is disabled (enable it in Customize \u2192 Network / set "
                "OPENAI4S_ALLOW_NETWORK=1)"
            )
        egress.check_url(ENDPOINT)
        webtools.guard_url(ENDPOINT)

    def _search_with_key(
        self,
        key: str,
        query: Any,
        *,
        num_results: Any,
        timeout: Any,
    ) -> dict[str, Any]:
        normalized_query = _query(query)
        count = _count(num_results)
        request_timeout = _timeout(timeout)
        self._check_network_policy()

        body = json.dumps(
            {
                "Query": normalized_query,
                "SearchType": "web",
                "Count": count,
                "Filter": {"NeedUrl": True},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request = urllib.request.Request(
            ENDPOINT,
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
                "X-Traffic-Tag": TRAFFIC_TAG,
            },
        )
        response = None
        try:
            with HTTPExchangeDeadline(request_timeout) as exchange:
                opener = self._opener or exchange.build_opener(_NoRedirect)
                response = exchange.open(opener, request)
                status = getattr(response, "status", None) or response.getcode()
                if status in _REDIRECT_CODES:
                    raise DoubaoSearchError(
                        f"Doubao search redirect was refused (status {int(status)})"
                    )
                if int(status) in (401, 402, 403, 429):
                    raise DoubaoSearchAuthError(
                        "Doubao search rejected the Agent Plan Key, entitlement, "
                        f"rate, or quota (HTTP {int(status)})"
                    )
                if int(status) != 200:
                    raise DoubaoSearchError(
                        f"Doubao search request failed with HTTP {int(status)}"
                    )
                raw = _read_capped(
                    response,
                    limit=MAX_RESPONSE_BYTES,
                    exchange=exchange,
                    require_bound=self._opener is None,
                )
        except urllib.error.HTTPError as error:
            status = int(error.code)
            try:
                error.close()
            except Exception:  # noqa: BLE001 - best-effort socket cleanup
                pass
            if status in _REDIRECT_CODES:
                raise DoubaoSearchError(
                    f"Doubao search redirect was refused (status {status})"
                ) from None
            if status in (401, 402, 403, 429):
                raise DoubaoSearchAuthError(
                    "Doubao search rejected the Agent Plan Key, entitlement, rate, "
                    f"or quota (HTTP {status})"
                ) from None
            raise DoubaoSearchError(
                f"Doubao search request failed with HTTP {status}"
            ) from None
        except (socket.timeout, TimeoutError):
            raise DoubaoSearchError("Doubao search request timed out") from None
        except urllib.error.URLError:
            raise DoubaoSearchError(
                "Doubao search request failed (network error)"
            ) from None
        except DoubaoSearchError:
            raise
        except Exception as error:  # noqa: BLE001 - project a controlled boundary
            raise DoubaoSearchError(
                f"Doubao search request failed ({type(error).__name__})"
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
            raise DoubaoSearchResponseError(
                "Doubao search returned invalid JSON"
            ) from None
        # A key can rotate while this request is in flight.  Scrub both the
        # exact credential sent and the credential currently brokered before
        # either provider data or the echoed query leaves this service.
        try:
            current_key = str(datapro.resolve_agent_plan_key(self.store) or "").strip()
        except Exception:  # noqa: BLE001 - the sent credential was already scrubbed
            current_key = ""
        safe = decoded
        safe_query: Any = normalized_query
        # Longest first: one rotated key may contain the other as a prefix.
        # Redacting a short prefix first would leave the longer key's suffix
        # visible and prevent the following whole-secret match.
        for secret in sorted({key, current_key} - {""}, key=len, reverse=True):
            safe = _redact_provider_payload(safe, secret)
            safe_query = redact_reflected_secret(safe_query, secret)
        return _normalize(safe, query=str(safe_query), limit=count)

    def search(
        self,
        query: Any,
        *,
        num_results: Any = 8,
        timeout: Any = 20.0,
    ) -> dict[str, Any]:
        """Perform one real Doubao query; this method never falls back."""

        key = self._resolved_key()
        return self._search_with_key(
            key,
            query,
            num_results=num_results,
            timeout=timeout,
        )

    def search_primary(
        self,
        query: Any,
        *,
        num_results: Any = 8,
        timeout: Any = 20.0,
        fallback: Callable[..., dict[str, Any]],
    ) -> dict[str, Any]:
        """Use Doubao when configured, falling back when it cannot serve the ask."""

        key = str(datapro.resolve_agent_plan_key(self.store) or "").strip()
        if not key:
            return fallback(query, num_results=num_results, timeout=timeout)
        if (
            len(key) < datapro.MIN_AGENT_PLAN_KEY_CHARS
            or len(key) > 8_192
            or any(ch in key for ch in ("\r", "\n", "\x00"))
        ):
            raise DoubaoSearchAuthError("Agent Plan Key is invalid")
        try:
            _query(query)
            _count(num_results)
        except ValueError:
            # Doubao's 100-character / 50-result bounds are a provider quirk the
            # built-in engines do not share.  Refusing the shape is not a reason
            # to lose the search: without this a 101-character query raised a
            # bare ``ValueError`` past ``WebSearchTool``'s DoubaoSearchError
            # handler and the turn got no results at all.
            return fallback(query, num_results=num_results, timeout=timeout)
        return self._search_with_key(
            key,
            query,
            num_results=num_results,
            timeout=timeout,
        )


__all__ = [
    "ENDPOINT",
    "MAX_RESPONSE_BYTES",
    "TRAFFIC_TAG",
    "DoubaoSearchAuthError",
    "DoubaoSearchError",
    "DoubaoSearchResponseError",
    "DoubaoSearchService",
]
