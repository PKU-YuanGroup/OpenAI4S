"""Race-free port allocation for tests that bind a real gateway server.

The pattern this replaces probed for a port and rebound it later::

    port = _free_port()          # bind 0, read the port, close
    ...                          # <- any concurrent process can take it here
    ThreadingHTTPServer(("127.0.0.1", port), handler_cls)

Under ``pytest -n auto`` four workers run that gap concurrently, and it is
real: the Frozen-shapes CI job lost ``test_team_auth_routes`` to
``EADDRINUSE`` exactly this way. The only race-free allocation is to bind
port 0 once and *keep* the bound socket.

What kept the tests on probe-then-rebind is a construction loop:
``make_handler`` snapshots ``cfg.port`` when the handler class is built (the
DNS-rebind Host allowlist), so the real port must exist before the handler
class can — while ``ThreadingHTTPServer`` wants the handler class in its
constructor. The loop is cut by binding first with a placeholder handler and
attaching the real one afterwards: ``socketserver`` looks
``RequestHandlerClass`` up per accepted request, and nothing is accepted
until ``serve_forever`` runs, so the placeholder never handles anything.

Tests that put a port into a ``Config`` without ever binding it keep using
their local ``_free_port`` — an unbound port number has no race to lose.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def bound_gateway_server() -> tuple[ThreadingHTTPServer, int]:
    """A ThreadingHTTPServer already bound and listening on a fresh loopback
    port, and that port.

    Build the real handler class with the returned port (so the Host
    allowlist snapshot matches the socket), assign it to
    ``server.RequestHandlerClass``, and only then start ``serve_forever``.
    """
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    httpd.daemon_threads = True
    return httpd, int(httpd.server_address[1])
