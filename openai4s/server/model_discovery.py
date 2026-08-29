"""Bounded discovery of OpenAI-compatible model servers on loopback.

Discovery is deliberately an explicit, read-only operation.  It probes a
small fixed catalogue of loopback URLs with proxies disabled; callers cannot
turn this helper into a generic SSRF primitive by supplying an arbitrary URL.
The result is only a profile suggestion -- it never mutates model settings or
stores credentials.
"""

from __future__ import annotations

import ipaddress
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


@dataclass(frozen=True, slots=True)
class LocalModelEndpoint:
    """One known local server shape and its model-list probe."""

    kind: str
    label: str
    base_url: str
    models_url: str


DEFAULT_LOCAL_ENDPOINTS: tuple[LocalModelEndpoint, ...] = (
    LocalModelEndpoint(
        kind="ollama",
        label="Ollama",
        base_url="http://127.0.0.1:11434/v1",
        models_url="http://127.0.0.1:11434/api/tags",
    ),
    LocalModelEndpoint(
        kind="lm_studio",
        label="LM Studio",
        base_url="http://127.0.0.1:1234/v1",
        models_url="http://127.0.0.1:1234/v1/models",
    ),
    LocalModelEndpoint(
        kind="vllm",
        label="vLLM",
        base_url="http://127.0.0.1:8000/v1",
        models_url="http://127.0.0.1:8000/v1/models",
    ),
    LocalModelEndpoint(
        kind="llama_cpp",
        label="llama.cpp",
        base_url="http://127.0.0.1:8080/v1",
        models_url="http://127.0.0.1:8080/v1/models",
    ),
)


class _RejectRedirects(HTTPRedirectHandler):
    """Keep a loopback probe on the exact fixed endpoint.

    ``urllib`` follows redirects by default.  A local server must not be able
    to turn this bounded discovery request into a fetch of another loopback
    port, a private-network service, or a public URL.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


def _loopback_http(url: str) -> bool:
    """Return true only for literal loopback HTTP(S) probe URLs."""

    try:
        parsed = urlsplit(url)
        address = ipaddress.ip_address(parsed.hostname or "")
    except (ValueError, TypeError):
        return False
    return bool(
        parsed.scheme in {"http", "https"}
        and address.is_loopback
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
    )


class LocalModelDiscoveryService:
    """Probe a fixed loopback catalogue and cache the public result briefly."""

    def __init__(
        self,
        *,
        endpoints: Iterable[LocalModelEndpoint] = DEFAULT_LOCAL_ENDPOINTS,
        timeout_s: float = 0.25,
        cache_ttl_s: float = 5.0,
        max_response_bytes: int = 1_000_000,
        opener: Any | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.endpoints = tuple(endpoints)
        if not self.endpoints:
            raise ValueError("at least one local endpoint candidate is required")
        for endpoint in self.endpoints:
            if not _loopback_http(endpoint.base_url) or not _loopback_http(
                endpoint.models_url
            ):
                raise ValueError("local model discovery accepts loopback URLs only")
        self.timeout_s = max(0.01, float(timeout_s))
        self.cache_ttl_s = max(0.0, float(cache_ttl_s))
        self.max_response_bytes = max(1024, int(max_response_bytes))
        # Environment proxies are intentionally ignored for loopback discovery.
        self._opener = opener or build_opener(ProxyHandler({}), _RejectRedirects())
        self._clock = clock
        self._lock = threading.RLock()
        self._cached_at = float("-inf")
        self._cached: tuple[dict[str, Any], ...] = ()

    def discover(self, *, force: bool = False) -> dict[str, Any]:
        """Return reachable endpoints without changing the active model profile."""

        now = self._clock()
        with self._lock:
            if not force and now - self._cached_at < self.cache_ttl_s:
                return self._payload(self._cached, cached=True)

        # The candidates are independent and all have short socket timeouts.
        # Parallel probing keeps an all-offline scan near one timeout rather
        # than multiplying it by every well-known local runtime.
        with ThreadPoolExecutor(
            max_workers=min(4, len(self.endpoints)),
            thread_name_prefix="openai4s-model-discovery",
        ) as pool:
            results = tuple(
                item
                for item in pool.map(self._probe, self.endpoints)
                if item is not None
            )
        with self._lock:
            self._cached = results
            self._cached_at = self._clock()
        return self._payload(results, cached=False)

    def catalog(self) -> dict[str, Any]:
        """The fixed candidate list, with no sockets opened.

        First-run readiness must not refresh this catalogue in the
        background.  ``discover()`` remains the explicit, user-asked scan.
        """
        endpoints = [
            {
                "kind": endpoint.kind,
                "label": endpoint.label,
                "provider": "chatgpt",
                "base_url": endpoint.base_url,
                "models": [],
                "default_model": "",
                "local": True,
                "requires_api_key": False,
                "contacted": False,
            }
            for endpoint in self.endpoints
        ]
        return {
            "endpoints": endpoints,
            "probed": 0,
            "cached": False,
            "mutated_settings": False,
            "contacted": False,
            "background_refresh": False,
        }

    def _probe(self, endpoint: LocalModelEndpoint) -> dict[str, Any] | None:
        request = Request(
            endpoint.models_url,
            headers={
                "Accept": "application/json",
                "User-Agent": "OpenAI4S-local-model-discovery/1",
            },
            method="GET",
        )
        try:
            with self._opener.open(request, timeout=self.timeout_s) as response:
                raw = response.read(self.max_response_bytes + 1)
        except Exception:  # noqa: BLE001 - an absent local runtime is normal
            return None
        if len(raw) > self.max_response_bytes:
            return None
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        models = self._model_ids(payload)
        return {
            "kind": endpoint.kind,
            "label": endpoint.label,
            "provider": "chatgpt",
            "base_url": endpoint.base_url,
            "models": models,
            "default_model": models[0] if models else "",
            "local": True,
            "requires_api_key": False,
        }

    @staticmethod
    def _model_ids(payload: Any) -> list[str]:
        if not isinstance(payload, dict):
            return []
        candidates = payload.get("data")
        if not isinstance(candidates, list):
            candidates = payload.get("models")
        if not isinstance(candidates, list):
            return []
        result: list[str] = []
        seen: set[str] = set()
        for item in candidates[:500]:
            if isinstance(item, str):
                value = item
            elif isinstance(item, dict):
                value = item.get("id") or item.get("model") or item.get("name")
            else:
                continue
            model = " ".join(str(value or "").split())[:512]
            if model and model not in seen:
                seen.add(model)
                result.append(model)
        return result

    def _payload(
        self, endpoints: tuple[dict[str, Any], ...], *, cached: bool
    ) -> dict[str, Any]:
        return {
            "endpoints": [dict(item) for item in endpoints],
            "probed": len(self.endpoints),
            "cached": cached,
            "mutated_settings": False,
        }


__all__ = [
    "DEFAULT_LOCAL_ENDPOINTS",
    "LocalModelDiscoveryService",
    "LocalModelEndpoint",
]
