"""Explicit, bounded model-list discovery for configured LLM endpoints.

The first-run wizard uses this service only after the user asks for model
candidates (or continues with an empty model field). Credentials are sent only
to the endpoint the user supplied, redirects and proxy environment variables
are disabled, and the response is bounded and reduced to model identifiers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

_MAX_RESPONSE_BYTES = 1_000_000
_MAX_MODELS = 500
_MAX_MODEL_ID_BYTES = 512
_DEFAULT_TIMEOUT_S = 8.0


class EndpointModelError(ValueError):
    """A safe, user-facing failure while fetching endpoint model metadata."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Any,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Any:
        del req, fp, code, msg, headers, newurl
        return None


@dataclass(frozen=True)
class EndpointModelFetcher:
    """Fetch and normalise one protocol's model-list response."""

    opener: Any | None = None
    timeout_s: float = _DEFAULT_TIMEOUT_S
    max_response_bytes: int = _MAX_RESPONSE_BYTES

    def __post_init__(self) -> None:
        if self.opener is None:
            object.__setattr__(
                self,
                "opener",
                build_opener(ProxyHandler({}), _RejectRedirects()),
            )
        object.__setattr__(self, "timeout_s", max(0.5, float(self.timeout_s)))
        object.__setattr__(
            self,
            "max_response_bytes",
            max(1024, int(self.max_response_bytes)),
        )

    def fetch(
        self,
        *,
        provider: str,
        base_url: str,
        api_key: str,
    ) -> dict[str, Any]:
        protocol = str(provider or "").strip().lower()
        if protocol not in {
            "ark",
            "chatgpt",
            "claude",
            "gemini",
            "ollama",
            "openai_responses",
        }:
            raise EndpointModelError("unsupported model protocol", 400)
        base = _validate_base_url(base_url)
        url = _models_url(protocol, base)
        headers = _headers(protocol, api_key)
        try:
            payload = self._get(url, headers)
        except EndpointModelError:
            raise
        except HTTPError as error:
            if error.code in (401, 403):
                raise EndpointModelError(
                    "the endpoint rejected the API key", 502
                ) from None
            if error.code == 404 and protocol == "ollama":
                # Ollama's native local API is not the OpenAI-compatible /models
                # route. The fallback remains restricted to the same host/path
                # family and is never followed through a redirect.
                native = _ollama_native_models_url(base)
                if native:
                    try:
                        payload = self._get(native, headers)
                        url = native
                    except HTTPError as native_error:
                        raise _http_model_error(native_error) from None
                    except (OSError, URLError):
                        raise EndpointModelError(
                            "could not reach the model endpoint"
                        ) from None
                else:
                    raise EndpointModelError(
                        "the endpoint does not expose a model-listing route"
                    ) from None
            else:
                raise _http_model_error(error) from None
        except (OSError, URLError):
            raise EndpointModelError("could not reach the model endpoint") from None
        except (UnicodeError, ValueError, TypeError):
            raise EndpointModelError("the endpoint returned invalid JSON") from None

        models = _model_ids(payload, protocol)
        if not models:
            raise EndpointModelError(
                "the endpoint returned no model ids; enter the model manually",
                422,
            )
        return {
            "models": models,
            "default_model": models[0],
            "count": len(models),
            "source": url,
        }

    def _get(self, url: str, headers: dict[str, str]) -> Any:
        request = Request(url, headers=headers, method="GET")
        opener = self.opener
        if opener is None:  # pragma: no cover - post-init always installs one
            raise RuntimeError("model discovery opener is unavailable")
        with opener.open(request, timeout=self.timeout_s) as response:
            raw = response.read(self.max_response_bytes + 1)
        if len(raw) > self.max_response_bytes:
            raise EndpointModelError("the endpoint returned too much model metadata")
        return json.loads(raw.decode("utf-8"))


def _validate_base_url(value: str) -> str:
    text = str(value or "").strip().rstrip("/")
    try:
        parts = urlsplit(text)
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
            "base URL must be an absolute http(s) URL without credentials or query parameters",
            400,
        )
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def _models_url(provider: str, base: str) -> str:
    path = urlsplit(base).path.rstrip("/")
    if provider == "claude":
        return base + ("/models" if path.endswith("/v1") else "/v1/models")
    if provider == "gemini":
        return base + ("/models" if path.endswith("/v1beta") else "/v1beta/models")
    return base + "/models"


def _ollama_native_models_url(base: str) -> str | None:
    parts = urlsplit(base)
    path = parts.path.rstrip("/")
    if not path.endswith("/v1"):
        return None
    return urlunsplit((parts.scheme, parts.netloc, path[:-3] + "/api/tags", "", ""))


def _headers(provider: str, api_key: str) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "OpenAI4S-model-discovery/1",
    }
    key = str(api_key or "").strip()
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


def _http_model_error(error: HTTPError) -> EndpointModelError:
    if error.code == 404:
        return EndpointModelError(
            "the endpoint does not expose a model-listing route for this protocol"
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
        if isinstance(item, str):
            value = item
        elif isinstance(item, dict):
            candidate = item.get("id") or item.get("model") or item.get("name")
            value = candidate if isinstance(candidate, str) else str(candidate or "")
        else:
            continue
        model = " ".join(str(value or "").split())
        if len(model.encode("utf-8")) > _MAX_MODEL_ID_BYTES:
            continue
        if provider == "gemini" and model.startswith("models/"):
            model = model[len("models/") :]
        if model and model not in seen:
            seen.add(model)
            result.append(model)
    return result


__all__ = ["EndpointModelError", "EndpointModelFetcher"]
