"""The contract of ``tests._ports``: one bind of port 0, never a rebind.

A probe-then-rebind helper opens a window — between the probe's ``close()``
and the server's ``bind()`` — in which any concurrent process can take the
port: the ``EADDRINUSE`` that took the Frozen-shapes CI job down through
``test_team_auth_routes``. "The port is held after the call returns" cannot
distinguish the two shapes (a rebinder also holds the port by then), so the
first test asserts the decidable equivalent instead: the allocation performs
exactly one ``bind``, and the port it requests is 0 — the number only exists
once the socket that will keep it owns it. A probe necessarily binds twice,
the second time to a concrete port, and fails this.
"""

from __future__ import annotations

import http.client
import socket
import threading
from http.server import BaseHTTPRequestHandler

from tests._ports import bound_gateway_server


def test_allocation_is_a_single_bind_of_port_zero(monkeypatch):
    binds: list[tuple] = []
    real_bind = socket.socket.bind

    def recording_bind(self, address):
        binds.append(tuple(address))
        return real_bind(self, address)

    monkeypatch.setattr(socket.socket, "bind", recording_bind)
    httpd, port = bound_gateway_server()
    try:
        assert binds == [("127.0.0.1", 0)], (
            "the allocation must be a single bind of port 0; a probe that "
            "closes and rebinds a concrete port reopens the EADDRINUSE "
            f"window the helper exists to close (saw: {binds})"
        )
        assert port == int(httpd.server_address[1])
    finally:
        httpd.server_close()


def test_the_handler_attached_after_the_port_is_known_is_the_one_answering():
    """The half that lets ``make_handler`` snapshot the real ``cfg.port``."""

    httpd, port = bound_gateway_server()

    class _Real(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server contract
            self.send_response(204)
            self.send_header("X-Attached-After-Bind", "yes")
            self.end_headers()

        def log_message(self, *args):  # keep pytest output clean
            return None

    httpd.RequestHandlerClass = _Real
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            conn.request("GET", "/")
            reply = conn.getresponse()
            assert reply.status == 204
            assert reply.getheader("X-Attached-After-Bind") == "yes"
        finally:
            conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
