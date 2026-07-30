"""Where a managed-endpoint readiness probe is allowed to reach.

`register` takes `spec["url"]` and only falls back to
`http://127.0.0.1:<allocated port>` when the caller gave none. So the probe
target is agent-supplied, and `probe_ready` called `urllib.request.urlopen` on
it with no guard at all: a cell could register an endpoint pointing at
`169.254.169.254`, or at any port on any host the daemon can reach, call probe,
and read existence off the returned boolean.

It returns no body, which is why this is an oracle rather than exfiltration.
A port scanner is still a real capability, and cloud metadata endpoints are a
real target.

The fix could easily have been the wrong one. A managed local endpoint *is* a
loopback service, so applying the SSRF guard to everything would refuse the
normal case along with the attack. Guarding everything and exempting only the
one address this daemon allocated is narrower than trusting whatever URL was
registered — and narrower, specifically, than "allow 127.0.0.1", which is
exactly the local port scan.
"""

from __future__ import annotations

import http.server
import socketserver
import threading

import pytest

from openai4s.host.endpoints import _is_own_managed_endpoint, probe_ready


class _Ok(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_args):
        return


@pytest.fixture
def live_port(monkeypatch):
    monkeypatch.setenv("OPENAI4S_ALLOW_NETWORK", "1")
    with socketserver.TCPServer(("127.0.0.1", 0), _Ok) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield server.server_address[1]
        server.shutdown()
        thread.join(5)


def test_the_daemons_own_endpoint_is_still_reachable(live_port):
    """The feature has to keep working. A guard that refuses managed local
    endpoints turns "is my service up" into "no" forever."""
    assert probe_ready(
        f"http://127.0.0.1:{live_port}", "/health", 3, own_port=live_port
    )


def test_the_same_port_is_refused_when_it_is_not_the_endpoints_own(live_port):
    """The sharpest case, and the reason the exemption is not "allow
    loopback": a live local service the endpoint does not own is exactly the
    local port scan this guard exists to stop."""
    assert not probe_ready(
        f"http://127.0.0.1:{live_port}", "/health", 3, own_port=live_port + 1
    )


def test_cloud_metadata_is_refused(monkeypatch):
    monkeypatch.setenv("OPENAI4S_ALLOW_NETWORK", "1")
    assert not probe_ready(
        "http://169.254.169.254", "/latest/meta-data/", 2, own_port=1
    )


def test_a_private_lan_address_is_refused(monkeypatch):
    monkeypatch.setenv("OPENAI4S_ALLOW_NETWORK", "1")
    assert not probe_ready("http://192.168.1.1", "/health", 2, own_port=1)


def test_an_endpoint_with_no_allocated_port_gets_no_exemption():
    """A remote endpoint has no port of ours. Nothing should inherit the
    exemption by having `own_port` left unset."""
    assert not _is_own_managed_endpoint("http://127.0.0.1:8123", None)
    assert not _is_own_managed_endpoint("http://127.0.0.1:8123", 0)


def test_the_exemption_matches_on_host_and_port_together():
    assert _is_own_managed_endpoint("http://127.0.0.1:8123", 8123)
    assert _is_own_managed_endpoint("http://localhost:8123", 8123)
    # right port, wrong host — a remote service that happens to use the number
    assert not _is_own_managed_endpoint("http://example.com:8123", 8123)
    # right host, wrong port — the local scan
    assert not _is_own_managed_endpoint("http://127.0.0.1:22", 8123)


def test_a_refusal_is_reported_as_not_ready_rather_than_raised(monkeypatch):
    """The caller asked whether the endpoint is live. An endpoint this daemon
    may not reach is not live to it, and raising would turn a policy decision
    into an error the caller has to special-case."""
    monkeypatch.setenv("OPENAI4S_ALLOW_NETWORK", "1")
    assert probe_ready("http://169.254.169.254", "/health", 1, own_port=1) is False


def test_the_service_passes_its_allocated_port_through():
    """The guard is only as good as the call site. `EndpointService.probe`
    reads the port off the stored record; without it every managed endpoint
    would be refused."""
    import inspect

    from openai4s.host.endpoints import EndpointService

    source = inspect.getsource(EndpointService.probe)
    assert "own_port=" in source
