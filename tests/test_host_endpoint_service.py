"""Direct contracts for the managed endpoint host service."""

from __future__ import annotations

import urllib.error

import pytest

from openai4s.config import Config
from openai4s.host.endpoints import (
    EndpointService,
    endpoint_fingerprint,
    free_port,
    probe_ready,
)
from openai4s.host_dispatch import HostDispatcher


class FakeEndpointStore:
    def __init__(self) -> None:
        self.endpoints: dict[str, dict] = {}
        self.upserts: list[tuple[str, dict]] = []

    def list_endpoints(self) -> list[dict]:
        return [dict(endpoint) for endpoint in self.endpoints.values()]

    def upsert_endpoint(self, name: str, **fields) -> None:
        self.upserts.append((name, dict(fields)))
        endpoint = self.endpoints.setdefault(name, {"name": name})
        endpoint.update(fields)


def test_local_registration_reuses_port_and_requires_change_approval():
    store = FakeEndpointStore()
    allocated = []

    def allocate():
        allocated.append(24100)
        return 24100

    service = EndpointService(store, allocate_port=allocate)
    spec = {
        "name": "vllm",
        "start": "run --model demo",
        "stop": "kill server",
        "live": "/ready",
        "skill": "serve-model",
        "credential": "HF_TOKEN",
    }

    first = service.register(spec)
    assert first == {
        "name": "vllm",
        "url": "http://127.0.0.1:24100",
        "port": 24100,
        "status": "registered",
        "remote": False,
        "changed": True,
    }
    assert store.endpoints["vllm"] == {
        "name": "vllm",
        "url": "http://127.0.0.1:24100",
        "skill": "serve-model",
        "port": 24100,
        "status": "registered",
        "credential": "HF_TOKEN",
        "start_script": "run --model demo",
        "stop_script": "kill server",
        "live_route": "/ready",
    }

    identical = service.register(dict(spec))
    assert identical == {
        "name": "vllm",
        "url": "http://127.0.0.1:24100",
        "port": 24100,
        "status": "registered",
        "changed": False,
    }
    assert allocated == [24100]
    assert len(store.upserts) == 1

    changed = service.register({**spec, "start": "run --model demo --tp 2"})
    assert changed["status"] == "awaiting_approval"
    assert changed["approval"] == {
        "required": True,
        "reason": "endpoint script changed",
        "start_script": "run --model demo --tp 2",
        "stop_script": "kill server",
    }
    assert store.endpoints["vllm"]["status"] == "awaiting_approval"
    assert store.endpoints["vllm"]["start_script"] == "run --model demo --tp 2"

    # Preserve the legacy no-op ordering: approving the exact payload already
    # stored while awaiting approval does not change its status.  Fixing that
    # workflow is a separate behavior change, not part of this extraction.
    same_approved = service.register(
        {**spec, "start": "run --model demo --tp 2", "approved": True}
    )
    assert same_approved["changed"] is False
    assert same_approved["status"] == "awaiting_approval"

    approved = service.register(
        {**spec, "start": "run --model demo --tp 4", "approved": True}
    )
    assert approved["status"] == "registered"
    assert "approval" not in approved


def test_remote_registration_discards_local_runtime_fields():
    store = FakeEndpointStore()

    def unexpected_allocation():
        raise AssertionError("remote endpoints must not allocate local ports")

    service = EndpointService(store, allocate_port=unexpected_allocation)
    result = service.register(
        {
            "name": "remote",
            "url": "https://api.example.test/v1",
            "port": 25000,
            "start": "must not survive",
            "stop": "must not survive",
            "live": "/must-not-survive",
            "skill": "remote-client",
            "credential": "REMOTE_TOKEN",
        }
    )

    assert result == {
        "name": "remote",
        "url": "https://api.example.test/v1",
        "port": None,
        "status": "registered",
        "remote": True,
        "changed": True,
    }
    assert store.endpoints["remote"] == {
        "name": "remote",
        "url": "https://api.example.test/v1",
        "skill": "remote-client",
        "port": None,
        "status": "registered",
        "credential": "REMOTE_TOKEN",
        "start_script": None,
        "stop_script": None,
        "live_route": None,
    }


def test_status_list_and_probe_preserve_hard_errors_and_minimal_updates():
    store = FakeEndpointStore()
    probes = []

    def readiness(url, route, **_kwargs):
        probes.append((url, route))
        return False

    service = EndpointService(
        store,
        allocate_port=lambda: 24200,
        readiness_probe=readiness,
    )
    service.register({"name": "local"})
    service.register({"name": "remote", "url": "https://remote.example.test"})

    assert [endpoint["name"] for endpoint in service.list()] == ["local", "remote"]
    assert service.status("local")["port"] == 24200
    with pytest.raises(KeyError, match="no endpoint 'missing'"):
        service.status("missing")
    with pytest.raises(KeyError, match="no endpoint 'missing'"):
        service.probe("missing")

    store.upserts.clear()
    local = service.probe("local")
    assert local == {
        "name": "local",
        "url": "http://127.0.0.1:24200",
        "ready": False,
        "status": "starting",
    }
    assert probes == [("http://127.0.0.1:24200", "/health")]
    assert store.upserts == [("local", {"status": "starting"})]

    store.upserts.clear()
    remote = service.probe("remote")
    assert remote["ready"] is True
    assert remote["status"] == "live"
    assert len(probes) == 1
    assert store.upserts == [("remote", {"status": "live"})]


def test_registration_requires_name_and_remote_detection_stays_case_sensitive():
    store = FakeEndpointStore()
    service = EndpointService(store, allocate_port=lambda: 24300)

    with pytest.raises(KeyError, match="name"):
        service.register({})

    result = service.register(
        {"name": "uppercase", "url": "HTTPS://example.test", "port": 24301}
    )
    assert result["remote"] is False
    assert result["port"] == 24301
    assert store.endpoints["uppercase"]["live_route"] == "/health"


def test_endpoint_helpers_keep_fingerprint_and_http_readiness_contract(monkeypatch):
    base = endpoint_fingerprint("url", "start", "stop", "/live", "skill", "TOKEN")
    assert base == endpoint_fingerprint(
        "url", "start", "stop", "/live", "skill", "TOKEN"
    )
    assert base != endpoint_fingerprint(
        "url", "start --changed", "stop", "/live", "skill", "TOKEN"
    )

    seen = []

    class Response:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def getcode(self):
            return self.status

    def open_ok(url, timeout):
        seen.append((url, timeout))
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", open_ok)
    # `own_port` says this is the daemon's own managed endpoint. Without it the
    # SSRF guard refuses the probe before `urlopen` is ever reached — which is
    # the point of the guard, since the endpoint URL is agent-supplied, and it
    # is why this line carries the port now.
    assert (
        probe_ready("http://localhost:8000/", "/ready", timeout=0.25, own_port=8000)
        is True
    )
    assert seen == [("http://localhost:8000/ready", 0.25)]

    def open_failed(_url, timeout):
        raise urllib.error.URLError(f"timeout={timeout}")

    monkeypatch.setattr("urllib.request.urlopen", open_failed)
    assert probe_ready("http://localhost:8000", "/ready", own_port=8000) is False

    # ...and a target that is NOT this endpoint's own is refused without any
    # request being made at all.
    seen.clear()
    monkeypatch.setattr("urllib.request.urlopen", open_ok)
    assert probe_ready("http://169.254.169.254", "/ready", own_port=8000) is False
    assert seen == [], "a guarded target still reached the network"


def test_free_port_scans_closes_sockets_and_falls_back_on_permission(monkeypatch):
    sockets = []

    class FakeSocket:
        def __init__(self, outcome):
            self.outcome = outcome
            self.closed = False

        def setsockopt(self, *_args):
            return None

        def bind(self, address):
            if self.outcome == "occupied":
                raise OSError(f"occupied: {address}")
            if self.outcome == "denied":
                raise PermissionError(f"denied: {address}")

        def close(self):
            self.closed = True

    outcomes = iter(["occupied", "open"])

    def socket_factory(*_args):
        instance = FakeSocket(next(outcomes))
        sockets.append(instance)
        return instance

    monkeypatch.setattr("openai4s.host.endpoints.socket.socket", socket_factory)
    assert free_port(25000, 25002, tries=2) == 25001
    assert all(instance.closed for instance in sockets)

    denied = []

    def denied_factory(*_args):
        instance = FakeSocket("denied")
        denied.append(instance)
        return instance

    monkeypatch.setattr("openai4s.host.endpoints.socket.socket", denied_factory)
    fallback = free_port(26000, 26002, tries=1)
    assert 26000 <= fallback <= 26002
    assert denied[0].closed is True

    monkeypatch.setattr(
        "openai4s.host.endpoints.socket.socket",
        lambda *_args: FakeSocket("occupied"),
    )
    with pytest.raises(RuntimeError, match="no free port found in 27000-27001"):
        free_port(27000, 27001, tries=2)


def test_dispatcher_endpoint_wrappers_share_the_extracted_service(
    tmp_path, monkeypatch
):
    fingerprint_calls = []

    def traced_fingerprint(*fields):
        fingerprint_calls.append(fields)
        return endpoint_fingerprint(*fields)

    dispatcher = HostDispatcher(Config(data_dir=tmp_path))
    monkeypatch.setattr("openai4s.host_dispatch._free_port", lambda: 24400)
    monkeypatch.setattr(
        "openai4s.host_dispatch._probe_ready",
        lambda _url, _route, **_kwargs: True,
    )
    monkeypatch.setattr(
        "openai4s.host_dispatch._endpoint_fingerprint",
        traced_fingerprint,
    )

    registered = dispatcher._m_endpoints_register({"name": "local"})
    assert registered["port"] == 24400
    assert dispatcher._m_endpoints_status("local")["status"] == "registered"
    assert dispatcher._m_endpoints_probe("local")["status"] == "live"
    assert dispatcher._m_endpoints_list()[0]["name"] == "local"
    assert dispatcher._m_endpoints_free_port() == 24400
    assert fingerprint_calls
