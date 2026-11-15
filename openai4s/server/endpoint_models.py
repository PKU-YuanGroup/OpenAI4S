"""Explicit, bounded model-list discovery for configured LLM endpoints.

The first-run wizard uses this service only after the user asks for model
candidates (or continues with an empty model field). The caller resolves the
endpoint and credential exactly as dispatch would; this module sends that key
only to that endpoint, disables redirects and proxy environment variables, and
bounds the response before reducing it to model identifiers. The timeout is a
per-socket-operation timeout, not a deadline for the whole exchange.
"""

from __future__ import annotations

import json
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import ProxyHandler, Request, build_opener

from openai4s.server.model_discovery import _RejectRedirects

_MAX_RESPONSE_BYTES = 1_000_000
_MAX_MODELS = 500
_MAX_MODEL_ID_BYTES = 512
_DEFAULT_TIMEOUT_S = 8.0
_PROTOCOLS = frozenset(
    {"ark", "chatgpt", "claude", "gemini", "ollama", "openai_responses"}
)


#: `EndpointModelError.reason` when the endpoint answered the listing request
#: with 404: it authenticated nothing wrong, it simply has no such route.
NO_LISTING_ROUTE = "no_listing_route"


class EndpointModelError(ValueError):
    """A safe, user-facing failure while fetching endpoint model metadata."""

    def __init__(
        self, message: str, status_code: int = 502, *, reason: str = ""
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason


class EndpointModelFetcher:
    """Fetch and normalise one protocol's model-list response."""

    def __init__(
        self,
        opener: Any | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        max_response_bytes: int = _MAX_RESPONSE_BYTES,
    ) -> None:
        self.opener = opener or build_opener(ProxyHandler({}), _RejectRedirects())
        self.timeout_s = max(0.5, float(timeout_s))
        self.max_response_bytes = max(1024, int(max_response_bytes))

    def fetch(
        self,
        *,
        provider: str,
        base_url: str,
        api_key: str,
        preferred_model: str = "",
    ) -> dict[str, Any]:
        protocol = str(provider or "").strip().lower()
        if protocol not in _PROTOCOLS:
            raise EndpointModelError("unsupported model protocol", 400)
        base = _validate_base_url(base_url)
        url = _models_url(protocol, base)
        key = _header_safe_key(api_key)
        headers = _headers(protocol, key)
        keyed = bool(key)
        try:
            payload = self._get(url, headers)
        except HTTPError as error:
            # Ollama's native local API is not the OpenAI-compatible /models
            # route. The fallback remains restricted to the same host/path
            # family and is never followed through a redirect.
            native = (
                _ollama_native_models_url(base)
                if protocol == "ollama" and error.code == 404
                else None
            )
            if native is None:
                raise _http_model_error(error, keyed=keyed) from None
            try:
                payload = self._get(native, headers)
            except HTTPError as native_error:
                raise _http_model_error(native_error, keyed=keyed) from None
            url = native

        models = _model_ids(payload, protocol)
        if not models:
            raise EndpointModelError(
                "the endpoint returned no model ids; enter the model manually",
                422,
            )
        preferred = str(preferred_model or "").strip()
        return {
            "models": models,
            # The protocol's own default when the endpoint serves it: listing
            # order is arbitrary (embeddings and audio models come first on
            # some providers), so the first id is only the fallback.
            "default_model": preferred if preferred in models else models[0],
            "count": len(models),
            "source": url,
        }

    def _get(self, url: str, headers: dict[str, str]) -> Any:
        """One bounded GET; every non-HTTP-status failure is a safe error.

        `HTTPError` is re-raised for the caller's status mapping. Everything
        else -- `URLError`/`OSError`, and `http.client.HTTPException`
        (`BadStatusLine` from a non-HTTP service, `InvalidURL`), which is not an
        `OSError` -- becomes "could not reach", and an undecodable body becomes
        "invalid JSON", on the fallback request as much as the first one.
        """
        request = Request(url, headers=headers, method="GET")
        try:
            with self.opener.open(request, timeout=self.timeout_s) as response:
                raw = response.read(self.max_response_bytes + 1)
        except HTTPError:
            raise
        except (OSError, HTTPException, ValueError):
            raise EndpointModelError("could not reach the model endpoint") from None
        if len(raw) > self.max_response_bytes:
            raise EndpointModelError("the endpoint returned too much model metadata")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError):
            raise EndpointModelError("the endpoint returned invalid JSON") from None


def _validate_base_url(value: str) -> str:
    text = str(value or "").strip().rstrip("/")
    try:
        parts = urlsplit(text)
        # `.port` is parsed lazily: read it here so a non-numeric or
        # out-of-range port is the user's 400, not `http.client.InvalidURL`
        # raised from inside the transport.
        parts.port
    except ValueError:
        parts = None
    if (
        parts is None
        or parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise EndpointModelError(
            "base URL must be an absolute http(s) URL with a valid port and "
            "no credentials or query parameters",
            400,
        )
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def _models_url(provider: str, base: str) -> str:
    # The same prefix the dispatch adds (`providers/anthropic.py` posts to
    # `{base}/v1/messages`, `providers/gemini.py` to `{base}/v1beta/models/...`),
    # so a listing that succeeds names a base URL a request can actually use.
    # Both paginate (Anthropic 20 per page, Gemini 50); one 1000-entry page,
    # each API's maximum, stands in for following the cursor.
    if provider == "claude":
        return base + "/v1/models?limit=1000"
    if provider == "gemini":
        return base + "/v1beta/models?pageSize=1000"
    return base + "/models"


def _ollama_native_models_url(base: str) -> str | None:
    parts = urlsplit(base)
    path = parts.path.rstrip("/")
    if not path.endswith("/v1"):
        return None
    return urlunsplit((parts.scheme, parts.netloc, path[:-3] + "/api/tags", "", ""))


def _header_safe_key(api_key: str) -> str:
    """The key as it can travel in a header, or a 400 naming the problem.

    `http.client` refuses control characters and anything outside latin-1 while
    writing the request; caught downstream, that read as a network or JSON
    failure for a request that was never sent.
    """
    key = str(api_key or "").strip()
    if key and not (key.isascii() and key.isprintable()):
        raise EndpointModelError(
            "the API key contains characters that cannot be sent in a header", 400
        )
    return key


def _headers(provider: str, key: str) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "OpenAI4S-model-discovery/1",
    }
    if provider == "claude":
        if key:
            headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"
    elif provider == "gemini":
        if key:
            headers["x-goog-api-key"] = key
    elif key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _http_model_error(error: HTTPError, *, keyed: bool) -> EndpointModelError:
    if error.code in (401, 403):
        return EndpointModelError(
            "the endpoint rejected the API key"
            if keyed
            else "the endpoint requires an API key; enter it and fetch again"
        )
    if 300 <= error.code < 400:
        # Redirects are refused, so they surface here as a status.
        return EndpointModelError(
            f"the endpoint redirected (HTTP {error.code}); enter the final URL"
        )
    if error.code == 404:
        return EndpointModelError(
            "the endpoint does not expose a model-listing route for this protocol",
            reason=NO_LISTING_ROUTE,
        )
    if 500 <= error.code < 600:
        return EndpointModelError("the endpoint returned a server error")
    return EndpointModelError(
        f"the endpoint rejected model discovery (HTTP {error.code})"
    )


def _model_ids(payload: Any, provider: str) -> list[str]:
    if not isinstance(payload, dict):
        return []
    candidates = payload.get("data")
    if not isinstance(candidates, list):
        candidates = payload.get("models")
    if not isinstance(candidates, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in candidates[:_MAX_MODELS]:
        if isinstance(item, dict):
            if provider == "gemini" and not _generates_content(item):
                continue
            value = next(
                (
                    item[field]
                    for field in ("id", "model", "name")
                    if isinstance(item.get(field), str) and item[field].strip()
                ),
                "",
            )
        else:
            value = item
        if not isinstance(value, str):
            # A dict or a boolean is not an id; `str()` of one used to become
            # a candidate (and possibly the auto-filled default).
            continue
        model = " ".join(value.split())
        if len(model.encode("utf-8")) > _MAX_MODEL_ID_BYTES:
            continue
        if provider == "gemini" and model.startswith("models/"):
            model = model[len("models/") :]
        if model and model not in seen:
            seen.add(model)
            result.append(model)
    return result


def _generates_content(item: dict[str, Any]) -> bool:
    """Gemini lists embedding and attribution models beside chat models.

    Only a model whose declared methods include `generateContent` can serve
    the wire this protocol dispatches; an entry that declares nothing is kept.
    """
    methods = item.get("supportedGenerationMethods")
    return not isinstance(methods, list) or "generateContent" in methods


__all__ = ["EndpointModelError", "EndpointModelFetcher"]
