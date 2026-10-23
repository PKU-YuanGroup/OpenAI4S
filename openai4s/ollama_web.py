"""Bounded stdlib access to Ollama's authenticated web search and fetch APIs."""

from __future__ import annotations

import hashlib
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any, Protocol

from openai4s import egress, webtools
from openai4s.config import LLMConfig, is_placeholder_api_key, provider_env_api_key
from openai4s.endpoint_identity import normalize_endpoint
from openai4s.http_deadline import HTTPExchangeDeadline, read_body_capped
from openai4s.mcp_protocol import redact_reflected_secret
from openai4s.security.secret_broker import SecretStoreUnavailable

BASE_URL = "https://ollama.com"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
WEB_API_KEY_SETTING = "ollama_web_api_key"
WEB_API_KEY_SCOPE = "ollama_web"


class WebCredentialStore(Protocol):
    def get_setting(self, key: str, default: str | None = None) -> str | None: ...

    def get_secret_setting(self, key: str, *, scope: str | None = None) -> str: ...

    def set_secret_setting(self, key: str, value: str, *, scope: str) -> str: ...


class OllamaWebError(RuntimeError):
    """A controlled provider failure, with no reflected response body."""


def _web_credential(
    cfg: LLMConfig, store: WebCredentialStore | None
) -> tuple[str, str]:
    """The independent stored key wins; read it afresh for every operation.

    An unconfigured installation can read status without a secure store. Local
    servers and custom proxies never donate model credentials to ollama.com.
    """
    if store is not None:
        saved = bool(store.get_setting(WEB_API_KEY_SETTING))
        try:
            key = store.get_secret_setting(
                WEB_API_KEY_SETTING, scope=WEB_API_KEY_SCOPE
            ).strip()
        except SecretStoreUnavailable:
            if saved:
                raise
            key = ""
        if key:
            return key, "saved" if saved else "environment"
    endpoint = cfg.base_url
    try:
        parts = urllib.parse.urlsplit(endpoint)
        cloud = (
            not any(ord(ch) < 32 for ch in endpoint)
            and not parts.username
            and not parts.password
            and not parts.query
            and not parts.fragment
            and normalize_endpoint(endpoint) == BASE_URL + "/v1"
        )
    except ValueError:
        cloud = False
    if cfg.provider == "ollama" and cloud and not is_placeholder_api_key(cfg.api_key):
        return cfg.api_key.strip(), "model"
    key = provider_env_api_key("ollama")
    return key, "environment" if key else ""


def resolve_web_key(cfg: LLMConfig, store: WebCredentialStore | None = None) -> str:
    return _web_credential(cfg, store)[0]


def web_config_status(cfg: LLMConfig, store: WebCredentialStore) -> dict:
    """Public configuration only; never return the key or its reference."""
    key, source = _web_credential(cfg, store)
    return {
        "key_configured": source == "saved",
        "available": bool(key),
        "source": source,
    }


def save_web_config(store: WebCredentialStore, body: Any) -> None:
    """Save or clear only the web credential, without touching model settings."""
    if not isinstance(body, dict) or set(body) not in (
        {"api_key"},
        {"clear_api_key"},
    ):
        raise ValueError("provide either api_key or clear_api_key")
    if "clear_api_key" in body:
        if body["clear_api_key"] is not True:
            raise ValueError("clear_api_key must be true")
        key = ""
    else:
        value = body["api_key"]
        if not isinstance(value, str):
            raise ValueError("api_key must be a string")
        key = value.strip()
        if (
            not 8 <= len(key) <= 8192
            or not key.isascii()
            or any(ord(ch) < 33 or ord(ch) == 127 for ch in key)
            or is_placeholder_api_key(key)
        ):
            raise ValueError("api_key must be a valid credential (8–8192 characters)")
    store.set_secret_setting(WEB_API_KEY_SETTING, key, scope=WEB_API_KEY_SCOPE)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _check_url(url: str) -> None:
    if not webtools.network_allowed():
        raise webtools.NetworkDisabled("networking is disabled")
    egress.check_url(url)
    webtools.guard_url(url)


def _public_url(value: Any) -> str:
    if not isinstance(value, str):
        raise OllamaWebError("Ollama web URL must be a string")
    try:
        parts = urllib.parse.urlsplit(value)
        port = parts.port
    except ValueError:
        raise OllamaWebError("Ollama web URL is invalid") from None
    if (
        parts.scheme not in ("https", "http")
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or any(ord(ch) < 32 for ch in value)
        or port == 0
    ):
        raise OllamaWebError("Ollama web requires an HTTP(S) URL without credentials")
    return value


def _text(payload: dict, field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise OllamaWebError(f"Ollama web returned an invalid {field} field")
    return value


class OllamaWebService:
    """One credential origin, no redirects, finite body and exchange bounds."""

    def __init__(self, key: Callable[[], str], *, opener: Any | None = None) -> None:
        self._key = key
        self._opener = opener

    def configured(self) -> bool:
        return bool(self._key())

    def _request(
        self, operation: str, payload: dict, timeout: float
    ) -> tuple[dict, bytes, dict]:
        key = self._key()
        if not key or len(key) > 8192 or any(ord(ch) < 32 for ch in key):
            raise OllamaWebError("Ollama API key is missing or invalid")
        if not math.isfinite(timeout) or not 1 <= timeout <= 120:
            raise OllamaWebError("Ollama web timeout must be between 1 and 120 seconds")
        url = BASE_URL + "/api/" + operation
        _check_url(url)
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {key}",
            },
            method="POST",
        )
        try:
            with HTTPExchangeDeadline(timeout) as exchange:
                opener = self._opener or exchange.build_opener(_NoRedirect)
                with exchange.open(opener, request) as response:
                    status = int(response.status)
                    if status != 200:
                        raise OllamaWebError(
                            f"Ollama {operation} failed (HTTP {status})"
                        )
                    raw = read_body_capped(
                        response,
                        limit=MAX_RESPONSE_BYTES,
                        exchange=exchange,
                        on_timeout=lambda: OllamaWebError("Ollama web timed out"),
                        on_oversize=lambda: OllamaWebError(
                            "Ollama web response too large"
                        ),
                        on_truncated=lambda: OllamaWebError(
                            "Ollama web response truncated"
                        ),
                        on_unbounded=(
                            (
                                lambda: OllamaWebError(
                                    "Ollama web transport is unbounded"
                                )
                            )
                            if self._opener is None
                            else None
                        ),
                    )
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            hint = "; check API key and quota" if status in (401, 402, 403, 429) else ""
            raise OllamaWebError(
                f"Ollama {operation} failed (HTTP {status}){hint}"
            ) from None
        except OllamaWebError:
            raise
        except Exception:
            raise OllamaWebError(f"Ollama {operation} failed (network error)") from None
        try:
            decoded = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise OllamaWebError("Ollama web returned invalid JSON") from None
        if not isinstance(decoded, dict) or "error" in decoded:
            raise OllamaWebError("Ollama web returned an invalid or refused response")
        # Also cover rotation during the request; do not publish reflected keys.
        for secret in sorted({key, self._key()} - {""}, key=len, reverse=True):
            decoded = redact_reflected_secret(decoded, secret)
            payload = redact_reflected_secret(payload, secret)
        return decoded, raw, payload

    def search(
        self, query: str, *, num_results: int = 8, timeout: float = 20.0
    ) -> dict:
        if not isinstance(query, str) or not query.strip():
            raise OllamaWebError("Ollama search query is required")
        if (
            isinstance(num_results, bool)
            or not isinstance(num_results, int)
            or not 1 <= num_results <= 50
        ):
            raise OllamaWebError("Ollama search count must be between 1 and 50")
        count = min(num_results, 10)
        payload, _raw, request = self._request(
            "web_search", {"query": query, "max_results": count}, timeout
        )
        rows = payload.get("results")
        if not isinstance(rows, list):
            raise OllamaWebError("Ollama search returned no valid results list")
        results = []
        for row in rows[:count]:
            if not isinstance(row, dict):
                raise OllamaWebError("Ollama search returned an invalid result")
            results.append(
                {
                    "title": _text(row, "title"),
                    "url": _public_url(row.get("url")),
                    "snippet": _text(row, "content"),
                }
            )
        return {
            "query": request["query"],
            "count": len(results),
            "results": results,
            "source": "ollama",
        }

    def fetch(
        self,
        url: str,
        *,
        fmt: str = "markdown",
        timeout: float = 30.0,
        max_chars: int = 20000,
    ) -> dict:
        if fmt not in ("markdown", "text") or not 1 <= max_chars <= 1_000_000:
            raise OllamaWebError(
                "Ollama fetch requires markdown/text and a valid limit"
            )
        if "://" not in url:
            url = "https://" + url
        url = _public_url(url)
        _check_url(url)
        payload, raw, request = self._request("web_fetch", {"url": url}, timeout)
        title = _text(payload, "title")
        content = _text(payload, "content")
        links = payload.get("links")
        if links is None:
            links = []
        if not isinstance(links, list) or not all(
            isinstance(link, str) for link in links
        ):
            raise OllamaWebError("Ollama fetch returned an invalid links field")
        # Keep all provider prose in the Host's existing screened content field.
        content = "\n\n".join(part for part in (title, content) if part)
        if links:
            # Links are untrusted page text, not additional fetch targets.
            # Relative and mailto links must not invalidate a usable page.
            content += "\n\n" + "\n".join(links)
        return {
            "url": request["url"],
            "content": content[:max_chars],
            "content_type": "text/markdown" if fmt == "markdown" else "text/plain",
            "truncated": len(content) > max_chars,
            "source": "ollama",
            # These describe the provider response, not the origin page bytes.
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "raw_bytes": len(raw),
        }
