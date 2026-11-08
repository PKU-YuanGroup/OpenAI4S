"""Synthetic offline coverage of opt-in Parallel and held-open MCP replies."""

import io
import json
import select
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from openai4s import egress, parallel_search, search_settings, webtools
from openai4s.config import Config
from openai4s.host_dispatch import HostDispatcher
from openai4s.http_deadline import HTTPExchangeDeadline
from openai4s.mcp_http import MCPHTTPConnection, _read_sse_response
from openai4s.mcp_protocol import MCPError

PASSAGE = "alpha\u0085beta\u2028gamma\u2029delta"
SOURCE = {
    "url": "https://example.org/fixture",
    "title": "Synthetic result",
    "excerpts": [PASSAGE],
}
pytestmark = pytest.mark.stubbed_backend


@pytest.fixture
def peer():
    observations = []
    requests = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((body, dict(self.headers)))
            method = body["method"]
            if self.path == "/slow-handshake" and method in {
                "initialize",
                "notifications/initialized",
            }:
                time.sleep(0.9)
            if self.path == "/slow-recovery":
                if (
                    method == "tools/call"
                    and sum(req["method"] == method for req, _ in requests) == 1
                ):
                    time.sleep(0.9)
                    self.send_response(404)
                    self.end_headers()
                    return
                if method == "initialize" and len(requests) > 1:
                    time.sleep(0.9)
            if "id" not in body:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            reply = {"jsonrpc": "2.0", "id": body["id"]}
            if method == "initialize":
                reply["result"] = {"protocolVersion": "2025-06-18", "capabilities": {}}
            elif method == "tools/list":
                reply["result"] = {"tools": [{"name": "web_search"}]}
            elif self.path == "/error":
                reply["error"] = {"code": -32042, "message": "synthetic failure"}
            else:
                assert body["params"]["arguments"] == {
                    "objective": "fixture query",
                    "search_queries": ["fixture query"],
                }
                reply["result"] = {"structuredContent": {"results": [SOURCE]}}
            encoded = json.dumps(reply, ensure_ascii=False).encode()
            self.send_response(200)
            if self.path == "/slow-recovery" and method == "initialize":
                self.send_header("Mcp-Session-Id", "fixture-session")
            if method != "tools/call":
                self.send_header("Content-Type", "Application/JSON")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                return
            self.send_header("Content-Type", "Text/Event-Stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            event = (
                b'data: {"method":"notifications/tools/list_changed"}\r\n\r\ndata: '
                + encoded
                + b"\r\n\r\n"
            )
            self.wfile.write(("%x\r\n" % len(event)).encode() + event + b"\r\n")
            self.wfile.flush()
            self.close_connection = True
            ready, _, _ = select.select([self.connection], [], [], 4)
            try:
                closed = bool(ready) and self.connection.recv(1, socket.MSG_PEEK) == b""
            except ConnectionResetError:
                closed = True
            observations.append(closed)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{server.server_port}", requests, observations
        server.shutdown()
        thread.join()


@pytest.mark.parametrize("path", ["/success", "/error"])
def test_native_host_returns_before_stream_eof(tmp_path, monkeypatch, peer, path):
    root, requests, observations = peer
    monkeypatch.setattr(parallel_search, "ENDPOINT", root + path)
    # Only the fixture's loopback destination bypasses SSRF in this synthetic test.
    monkeypatch.setattr(webtools, "guard_url", lambda url: None)
    host = HostDispatcher(Config(data_dir=tmp_path), frame_id="parallel-test")
    try:
        host.store.seed_default_permission_rules()
        # Synthetic private data must never enter an anonymous provider request.
        host.store.set_setting("private_note", "synthetic-private-setting")
        host.store.set_secret_setting(
            "you_api_key", "synthetic-unused-credential", scope="search"
        )
        (tmp_path / "private-research.txt").write_text("synthetic-workspace-content")
        search_settings.apply_setup_choice(host.store, "parallel")
        start = time.monotonic()
        result = host("web_search", [{"query": "fixture query", "timeout": 2}])
        assert time.monotonic() - start < 3
        if path == "/success":
            assert result["source"] == "parallel"
            assert result["results"] == [
                {"url": SOURCE["url"], "title": SOURCE["title"], "snippet": PASSAGE}
            ]
        else:
            assert "-32042" in result["error"]
        for _ in range(100):
            if observations:
                break
            threading.Event().wait(0.01)
        assert observations == [True]
        assert [body["method"] for body, _ in requests] == [
            "initialize",
            "notifications/initialized",
            "tools/list",
            "tools/call",
        ]
        assert all(
            headers["User-Agent"] == parallel_search.USER_AGENT
            for _, headers in requests
        )
        assert all("Authorization" not in headers for _, headers in requests)
        wire = json.dumps(requests)
        for sentinel in (
            "synthetic-private-setting",
            "synthetic-unused-credential",
            "synthetic-workspace-content",
            str(tmp_path),
        ):
            assert sentinel not in wire
        assert requests[0][0]["params"] == {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "openai4s", "version": "1.0.0"},
        }
    finally:
        host.store.close()


@pytest.mark.parametrize("path", ["/slow-handshake", "/slow-recovery"])
def test_search_deadline_spans_handshake_and_session_recovery(
    tmp_path, monkeypatch, peer, path
):
    root, requests, _ = peer
    monkeypatch.setattr(parallel_search, "ENDPOINT", root + path)
    monkeypatch.setattr(webtools, "guard_url", lambda url: None)
    host = HostDispatcher(Config(data_dir=tmp_path), frame_id="deadline")
    try:
        host.store.seed_default_permission_rules()
        search_settings.apply_setup_choice(host.store, "parallel")
        start = time.monotonic()
        reply = host("web_search", [{"query": "fixture query", "timeout": 1.2}])
        elapsed = time.monotonic() - start
        assert elapsed < 1.6, (elapsed, [req["method"] for req, _ in requests])
        assert "error" in reply
        methods = [req["method"] for req, _ in requests]
        if path == "/slow-handshake":
            assert methods == ["initialize", "notifications/initialized"]
        else:
            assert methods == [
                "initialize",
                "notifications/initialized",
                "tools/list",
                "tools/call",
                "initialize",
            ]
    finally:
        host.store.close()


@pytest.mark.parametrize("separator", [b"\r", b"\n", b"\r\n"])
def test_sse_line_boundaries_and_unicode(separator):
    body = json.dumps({"id": 7, "result": PASSAGE}, ensure_ascii=False).encode()
    stream = io.BytesIO(b"data: " + body + separator + separator)

    # Single-byte chunks split CRLF and UTF-8 sequences across reads.
    response = _read_sse_response(iter(lambda: stream.read(1), b""), 7)
    assert PASSAGE in response.decode()


def test_parallel_is_opt_in_and_legacy_clients_preserve_switch(tmp_path):
    host = HostDispatcher(Config(data_dir=tmp_path))
    try:
        assert search_settings.engine_chain(host.store) == webtools.SEARCH_ENGINES
        search_settings.apply_setup_choice(host.store, "parallel")
        assert search_settings.engine_chain(host.store)[0] == "parallel"
        search_settings.save(
            host.store,
            order=list(webtools.SEARCH_ENGINES),
            enabled={e: True for e in webtools.SEARCH_ENGINES},
        )
        assert search_settings.load(host.store)["enabled"]["parallel"]
        assert search_settings.engine_chain(host.store)[0] == "parallel"
        search_settings.choose_primary(host.store, "builtin")
        assert search_settings.engine_chain(host.store) == ("builtin",)
    finally:
        host.store.close()


@pytest.mark.parametrize("policy", ["offline", "egress", "ssrf"])
def test_policy_blocks_before_open(monkeypatch, policy):
    def refuse(url):
        if policy == "ssrf":
            raise webtools.SSRFBlocked("synthetic private destination")
        raise egress.EgressBlocked("synthetic egress refusal")

    def opened(*args, **kwargs):
        pytest.fail("network opened after policy denial")

    monkeypatch.setattr(HTTPExchangeDeadline, "open", opened)
    if policy == "offline":
        monkeypatch.setenv("OPENAI4S_ALLOW_NETWORK", "0")
    elif policy == "egress":
        monkeypatch.setattr(egress, "check_url", refuse)
    else:
        monkeypatch.setattr(webtools, "guard_url", refuse)
    with pytest.raises(MCPError, match="blocked by network policy"):
        parallel_search.search("query", 1, 2)


def test_parallel_exact_allowlist(monkeypatch):
    monkeypatch.setenv("OPENAI4S_EGRESS", "allowlist")
    assert egress.domain_allowed("mcp.parallel.ai")
    assert not egress.domain_allowed("child.mcp.parallel.ai")
    assert not egress.domain_allowed("parallel.ai")


def test_disabled_engine_never_calls_parallel(tmp_path, monkeypatch):
    def unexpected(*args):
        pytest.fail("disabled Parallel was called")

    monkeypatch.setattr(parallel_search, "search", unexpected)
    monkeypatch.setattr(webtools, "_identifier_route", lambda *a: None)
    monkeypatch.setattr(webtools, "_builtin_search", lambda *a: ([], "", None))
    monkeypatch.setattr(webtools, "_you_search", lambda *a: [])
    host = HostDispatcher(Config(data_dir=tmp_path))
    try:
        webtools.web_search("query", engines=search_settings.engine_chain(host.store))
    finally:
        host.store.close()


def test_permission_deny_precedes_parallel(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("denied search reached Parallel")

    monkeypatch.setattr(parallel_search, "search", unexpected)
    host = HostDispatcher(Config(data_dir=tmp_path), frame_id="denied-parallel")
    try:
        host.store.seed_default_permission_rules()
        host.store.set_permission_rule(
            scope="global", scope_id="", tool="web_search", pattern="*", decision="deny"
        )
        search_settings.apply_setup_choice(host.store, "parallel")
        result = host("web_search", [{"query": "denied query"}])
        assert "error" in result
    finally:
        host.store.close()


@pytest.mark.parametrize(
    "phase", ["initialize", "notifications/initialized", "tools/list", "tools/call"]
)
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirect_never_forwards_query_session_or_credentials(
    monkeypatch, phase, status
):
    """Synthetic two-origin server proves the real urllib client refuses hops."""
    destination_requests = []
    requests = []

    class Destination(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            destination_requests.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()

        do_POST = do_GET

    class Origin(Destination):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((body, dict(self.headers)))
            if body["method"] == phase:
                self.send_response(status)
                self.send_header("Location", destination)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            reply = {
                "id": body.get("id"),
                "result": {
                    "protocolVersion": "2025-06-18",
                    "tools": [{"name": "web_search"}],
                },
            }
            encoded = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Mcp-Session-Id", "synthetic-session")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    monkeypatch.setattr(webtools, "guard_url", lambda url: None)
    with ThreadingHTTPServer(("127.0.0.1", 0), Destination) as target:
        destination = f"http://127.0.0.1:{target.server_port}/unintended"
        target_thread = threading.Thread(target=target.serve_forever, daemon=True)
        target_thread.start()
        with ThreadingHTTPServer(("127.0.0.1", 0), Origin) as origin:
            origin_thread = threading.Thread(target=origin.serve_forever, daemon=True)
            origin_thread.start()
            connection = None
            try:
                connection = MCPHTTPConnection(
                    f"http://127.0.0.1:{origin.server_port}/mcp",
                    headers={
                        "User-Agent": parallel_search.USER_AGENT,
                        "Authorization": "Bearer synthetic-credential",
                    },
                    timeout=2,
                )
                if phase == "notifications/initialized":
                    assert (
                        connection.faulted()
                    )  # Existing notification-failure contract.
                elif phase == "tools/list":
                    with pytest.raises(MCPError, match="redirect was refused"):
                        connection.list_tools()
                elif phase == "tools/call":
                    with pytest.raises(MCPError, match="redirect was refused"):
                        connection.call_tool(
                            "web_search", {"objective": "synthetic-private-query"}
                        )
            except MCPError as error:
                assert phase == "initialize" and "redirect was refused" in str(error)
            finally:
                if connection is not None:
                    connection.close()
                origin.shutdown()
                origin_thread.join()
        target.shutdown()
        target_thread.join()
    assert not destination_requests
    assert any(body["method"] == phase for body, _ in requests)
    assert all(
        headers["User-Agent"] == parallel_search.USER_AGENT for _, headers in requests
    )


@pytest.mark.parametrize("media_type", ["application/json", "text/event-stream"])
@pytest.mark.parametrize("early_reply", [False, True])
def test_actual_http_client_caps_response(monkeypatch, media_type, early_reply):
    from openai4s import mcp_http
    from openai4s.mcp_protocol import MCPOversizedResponse

    class Oversized(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", media_type)
            if early_reply:
                self.send_header("Content-Length", "2048")
            self.end_headers()
            if early_reply:
                prefix = b'data: {"id":1,"result":{}}\n\n'
                self.wfile.write(prefix)
                self.wfile.flush()
                time.sleep(0.05)
                try:
                    self.wfile.write(b" " * (2048 - len(prefix)))
                except (BrokenPipeError, ConnectionResetError):
                    pass  # A client rejecting the declared size may already be gone.
            else:
                self.wfile.write(b" " * 2048)

    monkeypatch.setattr(webtools, "guard_url", lambda url: None)
    monkeypatch.setattr(mcp_http, "_MAX_FRAME_BYTES", 1024)
    with ThreadingHTTPServer(("127.0.0.1", 0), Oversized) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with pytest.raises(MCPOversizedResponse):
                MCPHTTPConnection(
                    f"http://127.0.0.1:{server.server_port}/mcp", timeout=2
                )
        finally:
            server.shutdown()
            thread.join()


def test_documented_cli_choice_survives_config_reload(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys

    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI4S_LLM_PROVIDER", "ark")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "openai4s",
            "init",
            "--provider",
            "ark",
            "--search",
            "parallel",
            "--non-interactive",
            "--json",
        ],
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["search"]["order"][0] == "parallel"
    host = HostDispatcher(Config(data_dir=tmp_path))
    try:
        assert search_settings.engine_chain(host.store)[0] == "parallel"
        assert search_settings.project(host.store)["engines"][0]["accepts_key"] is False
    finally:
        host.store.close()
