"""Managed endpoint registration and readiness behavior for host RPC calls.

The service preserves the legacy metadata/approval model.  It stores start and
stop scripts but never executes them, and its readiness probe does not add a new
permission or egress policy; those security changes require a separate design.
"""

from __future__ import annotations

import hashlib
import socket
import threading
from typing import Callable, Protocol


class EndpointStore(Protocol):
    def list_endpoints(self) -> list[dict]:
        ...

    def upsert_endpoint(self, name: str, **fields) -> None:
        ...


_FALLBACK_PORT_LOCK = threading.Lock()
_FALLBACK_PORT_NEXT = 19999


def fallback_port(lo: int, hi: int) -> int:
    global _FALLBACK_PORT_NEXT
    with _FALLBACK_PORT_LOCK:
        if _FALLBACK_PORT_NEXT < lo or _FALLBACK_PORT_NEXT >= hi:
            _FALLBACK_PORT_NEXT = lo
        else:
            _FALLBACK_PORT_NEXT += 1
        return _FALLBACK_PORT_NEXT


def free_port(lo: int = 20000, hi: int = 29999, tries: int | None = None) -> int:
    """Pick a free port from the managed endpoint band."""
    attempts = tries if tries is not None else max(0, hi - lo + 1)
    permission_denied = False
    for port in range(lo, hi + 1):
        if attempts <= 0:
            break
        attempts -= 1
        candidate = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            candidate.bind(("127.0.0.1", port))
            return port
        except PermissionError:
            permission_denied = True
            continue
        except OSError:
            continue
        finally:
            candidate.close()
    if permission_denied:
        return fallback_port(lo, hi)
    raise RuntimeError(f"free_port: no free port found in {lo}-{hi}")


def endpoint_fingerprint(url, start, stop, live, skill, credential) -> str:
    """Hash every identity-bearing field used for no-op/change detection."""
    blob = "\x00".join(
        str(value or "") for value in (url, start, stop, live, skill, credential)
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def probe_ready(url: str, live_route: str, timeout: float = 2.0) -> bool:
    """Return whether the endpoint's live route responds with any 2xx status."""
    import urllib.error
    import urllib.request

    route = live_route or "/health"
    probe_url = url.rstrip("/") + "/" + route.lstrip("/")
    try:
        with urllib.request.urlopen(probe_url, timeout=timeout) as response:
            return 200 <= getattr(response, "status", response.getcode()) < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


class EndpointService:
    """Own managed endpoint metadata, change detection, and readiness probes."""

    def __init__(
        self,
        store: EndpointStore,
        *,
        allocate_port: Callable[[], int] = free_port,
        readiness_probe: Callable[[str, str], bool] = probe_ready,
        fingerprint: Callable[..., str] = endpoint_fingerprint,
    ) -> None:
        self.store = store
        self.allocate_port = allocate_port
        self.readiness_probe = readiness_probe
        self.fingerprint = fingerprint

    def free_port(self) -> int:
        return self.allocate_port()

    def list(self) -> list[dict]:
        return self.store.list_endpoints()

    def register(self, spec: dict) -> dict:
        name = spec["name"]
        url = spec.get("url") or ""
        is_remote = url.startswith("https://")
        existing = next(
            (
                endpoint
                for endpoint in self.store.list_endpoints()
                if endpoint["name"] == name
            ),
            None,
        )

        if is_remote:
            start = stop = live = None
            port = None
        else:
            port = (
                spec.get("port") or (existing or {}).get("port") or self.allocate_port()
            )
            url = url or f"http://127.0.0.1:{port}"
            start = spec.get("start") or spec.get("start_script")
            stop = spec.get("stop") or spec.get("stop_script")
            live = spec.get("live") or spec.get("live_route") or "/health"

        credential = spec.get("credential")
        new_fingerprint = self.fingerprint(
            url,
            start,
            stop,
            live,
            spec.get("skill"),
            credential,
        )
        approval = None
        if existing is not None:
            old_fingerprint = self.fingerprint(
                existing.get("url"),
                existing.get("start_script"),
                existing.get("stop_script"),
                existing.get("live_route"),
                existing.get("skill"),
                existing.get("credential"),
            )
            if old_fingerprint == new_fingerprint:
                return {
                    "name": name,
                    "url": url,
                    "port": port,
                    "status": existing.get("status", "registered"),
                    "changed": False,
                }
            if not spec.get("approved"):
                approval = {
                    "required": True,
                    "reason": "endpoint script changed",
                    "start_script": start,
                    "stop_script": stop,
                }

        status = "registered" if approval is None else "awaiting_approval"
        self.store.upsert_endpoint(
            name,
            url=url,
            skill=spec.get("skill"),
            port=port,
            status=status,
            credential=credential,
            start_script=start,
            stop_script=stop,
            live_route=live,
        )
        result = {
            "name": name,
            "url": url,
            "port": port,
            "status": status,
            "remote": is_remote,
            "changed": True,
        }
        if approval is not None:
            result["approval"] = approval
        return result

    def status(self, name: str) -> dict:
        for endpoint in self.store.list_endpoints():
            if endpoint["name"] == name:
                return endpoint
        raise KeyError(f"no endpoint {name!r}")

    def probe(self, name: str) -> dict:
        endpoint = self.status(name)
        url = endpoint.get("url") or ""
        if url.startswith("https://"):
            ready = True
        else:
            ready = self.readiness_probe(
                url,
                endpoint.get("live_route") or "/health",
            )
        status = "live" if ready else "starting"
        self.store.upsert_endpoint(name, status=status)
        return {"name": name, "url": url, "ready": ready, "status": status}


__all__ = [
    "EndpointService",
    "endpoint_fingerprint",
    "fallback_port",
    "free_port",
    "probe_ready",
]
