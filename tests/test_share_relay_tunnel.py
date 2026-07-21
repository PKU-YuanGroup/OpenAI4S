from __future__ import annotations

import http.client
import time

import pytest

from openai4s.share.relay import RelayConfig, RelayServer
from openai4s.share.tunnel import TunnelClient

_TOKEN = "secret-token-abcdefghijklmnop"
_OTHER_TOKEN = "other-token-zyxwvutsrqponml"
_DOMAIN = "localtest.me"
_SHARE = "abcdefghijklmnopqrstuvwxyz"  # 26 chars, matches label regex


def _relay(**kw):
    config = RelayConfig(base_domain=_DOMAIN, tokens={"dev": _TOKEN}, **kw)
    server = RelayServer(("127.0.0.1", 0), config)
    import threading

    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def _handler_factory(routes):
    def handler(request):
        key = (request["share_id"], request["path"])
        if key in routes:
            return routes[key]
        return {"status": 404, "headers": {}, "body": None}

    return handler


def _tunnel(port, token, handler, shares):
    client = TunnelClient(
        f"ws://127.0.0.1:{port}/tunnel", token, handler, allow_insecure=True
    )
    client.set_shares(shares)
    assert client.wait_connected(5.0), "tunnel failed to connect"
    return client


def _get(port, host, path, method="GET", headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    conn.putheader("Host", host)
    for k, v in (headers or {}).items():
        conn.putheader(k, v)
    conn.endheaders()
    resp = conn.getresponse()
    body = resp.read()
    result = (resp.status, resp.headers, body)  # email.message: case-insensitive .get
    conn.close()
    return result


def _wait_registered(client, deadline=5.0):
    # allow the async share_register round-trip to complete
    time.sleep(0.2)


def test_end_to_end_visitor_fetch(tmp_path):
    server, port = _relay()
    try:
        routes = {
            (_SHARE, "/"): {
                "status": 200,
                "headers": {"content-type": "text/html", "content-length": "5"},
                "body": b"hello",
            }
        }
        client = _tunnel(port, _TOKEN, _handler_factory(routes), {_SHARE: {}})
        _wait_registered(client)
        try:
            status, headers, body = _get(port, f"{_SHARE}.{_DOMAIN}", "/")
            assert status == 200
            assert body == b"hello"
            assert headers.get("Content-Type") == "text/html"
            # relay stamps the security baseline
            assert headers.get("X-Content-Type-Options") == "nosniff"
        finally:
            client.close()
    finally:
        server.shutdown()
        server.server_close()


def test_range_streaming(tmp_path):
    server, port = _relay()
    try:
        routes = {
            (_SHARE, "/big"): {
                "status": 206,
                "headers": {
                    "content-type": "application/octet-stream",
                    "content-length": "4",
                    "content-range": "bytes 0-3/10",
                },
                "body": b"ABCD",
            }
        }
        client = _tunnel(port, _TOKEN, _handler_factory(routes), {_SHARE: {}})
        _wait_registered(client)
        try:
            status, headers, body = _get(port, f"{_SHARE}.{_DOMAIN}", "/big")
            assert status == 206
            assert body == b"ABCD"
            assert headers.get("Content-Range") == "bytes 0-3/10"
        finally:
            client.close()
    finally:
        server.shutdown()
        server.server_close()


def test_head_has_no_body(tmp_path):
    server, port = _relay()
    try:
        routes = {
            (_SHARE, "/"): {
                "status": 200,
                "headers": {"content-type": "text/html", "content-length": "5"},
                "body": None,  # daemon returns no body for HEAD
            }
        }
        client = _tunnel(port, _TOKEN, _handler_factory(routes), {_SHARE: {}})
        _wait_registered(client)
        try:
            status, headers, body = _get(
                port, f"{_SHARE}.{_DOMAIN}", "/", method="HEAD"
            )
            assert status == 200
            assert body == b""
            assert headers.get("Content-Length") == "5"
        finally:
            client.close()
    finally:
        server.shutdown()
        server.server_close()


def test_unknown_share_is_404(tmp_path):
    server, port = _relay()
    try:
        status, _, body = _get(port, f"{'z' * 26}.{_DOMAIN}", "/")
        assert status == 404
        assert body == b"This share is unavailable.\n"
    finally:
        server.shutdown()
        server.server_close()


def test_bad_host_label_is_404(tmp_path):
    server, port = _relay()
    try:
        status, _, _ = _get(port, f"not-a-valid-label.{_DOMAIN}", "/")
        assert status == 404
        status2, _, _ = _get(port, "wrong.example.org", "/")
        assert status2 == 404
    finally:
        server.shutdown()
        server.server_close()


def test_method_not_allowed(tmp_path):
    server, port = _relay()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.putrequest("POST", "/", skip_host=True)
        conn.putheader("Host", f"{_SHARE}.{_DOMAIN}")
        conn.putheader("Content-Length", "0")
        conn.endheaders()
        resp = conn.getresponse()
        assert resp.status == 405
        conn.close()
    finally:
        server.shutdown()
        server.server_close()


def test_poison_response_header_becomes_502(tmp_path):
    server, port = _relay()
    try:
        routes = {
            (_SHARE, "/"): {
                "status": 200,
                "headers": {"content-length": "2", "set-cookie": "evil=1"},
                "body": b"hi",
            }
        }
        client = _tunnel(port, _TOKEN, _handler_factory(routes), {_SHARE: {}})
        _wait_registered(client)
        try:
            status, headers, _ = _get(port, f"{_SHARE}.{_DOMAIN}", "/")
            assert status == 502
            assert headers.get("Set-Cookie") is None
        finally:
            client.close()
    finally:
        server.shutdown()
        server.server_close()


def test_different_principal_conflict(tmp_path):
    tokens_file = tmp_path / "tokens"
    tokens_file.write_text(f"alice {_TOKEN}\nbob {_OTHER_TOKEN}\n", encoding="utf-8")
    server, port = _relay(tokens_file=str(tokens_file))
    try:
        client_a = _tunnel(port, _TOKEN, _handler_factory({}), {_SHARE: {}})
        _wait_registered(client_a)
        # a different principal claiming the same share id must be refused; the
        # first principal keeps serving.
        client_b = TunnelClient(
            f"ws://127.0.0.1:{port}/tunnel",
            _OTHER_TOKEN,
            _handler_factory(
                {
                    (_SHARE, "/"): {
                        "status": 200,
                        "headers": {"content-length": "3"},
                        "body": b"bad",
                    }
                }
            ),
            allow_insecure=True,
        )
        client_b.set_shares({_SHARE: {}})
        client_b.wait_connected(5.0)
        time.sleep(0.3)
        conn = server.registry.get(_SHARE)
        assert conn is not None and conn.principal_fp == client_a_fp(_TOKEN)
        client_a.close()
        client_b.close()
    finally:
        server.shutdown()
        server.server_close()


def test_xff_ignored_without_trust_proxy(tmp_path):
    server, port = _relay()  # trust_proxy defaults False
    try:
        # spoofed XFF must be ignored; request still routes normally (404 here,
        # since no tunnel) and does not error.
        status, _, _ = _get(
            port, f"{'z' * 26}.{_DOMAIN}", "/", headers={"X-Forwarded-For": "1.2.3.4"}
        )
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()


def client_a_fp(token: str) -> str:
    import hashlib

    return hashlib.sha256(token.encode()).hexdigest()
