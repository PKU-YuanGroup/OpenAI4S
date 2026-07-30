"""Whether the SSRF and egress checks survive a redirect.

`_http_get` says it follows redirects manually "so the SSRF guard is applied to
every hop (a public URL can 30x-redirect to a metadata/loopback target)". That
was true only on the optional `requests` branch. The fallback branch called
`urllib.request.urlopen`, which follows redirects inside the stdlib — and that
is the branch a zero-dependency install always takes, because `requests` is not
a core dependency of this project.

So the guard saw the URL the caller passed and nothing else. Measured before
the fix, against a local server that 302s: two URLs fetched, one guarded.

`web_probe` had already solved this for itself with a private `_NoRedirect`
handler. The handler is now shared, which is why it is worth saying out loud
that the two call sites needed the same thing and only one of them had it.
"""

from __future__ import annotations

import http.server
import socketserver
import threading

import pytest

from openai4s import webtools

# Deliberately NOT marked `network`. The server below is an in-process loopback
# socket, not a live external resource, and `addopts` deselects `network` by
# default — so the marker would have removed three security tests from every
# run while leaving them looking present. `test_share_relay_tunnel.py` and
# `test_telemetry_collector.py` bind loopback unmarked for the same reason.


class _Redirector(http.server.BaseHTTPRequestHandler):
    """/start 302s to /target; /target answers. Two distinct URLs, so "which
    ones did the guard see" is a question with an unambiguous answer."""

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's interface
        if self.path == "/start":
            self.send_response(302)
            self.send_header(
                "Location", f"http://127.0.0.1:{self.server.server_address[1]}/target"
            )
            self.end_headers()
            return
        body = b"TARGET"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_HEAD = do_GET

    def log_message(self, *_args):
        return


@pytest.fixture
def redirect_server():
    with socketserver.TCPServer(("127.0.0.1", 0), _Redirector) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{server.server_address[1]}"
        server.shutdown()
        thread.join(5)


@pytest.fixture
def guard_spy(monkeypatch):
    """Record every URL the SSRF guard is asked about."""
    seen: list[str] = []
    real = webtools._guard_url

    def _spy(url):
        seen.append(url)
        return real(url)

    monkeypatch.setattr(webtools, "_guard_url", _spy)
    monkeypatch.setenv("OPENAI4S_ALLOW_NETWORK", "1")
    # Loopback is what the test server binds, and the guard blocks it by
    # default — correctly. Allowing it here makes the *count of guarded hops*
    # the measurement rather than the first hop's refusal.
    monkeypatch.setenv("OPENAI4S_ALLOW_PRIVATE_FETCH", "1")
    return seen


@pytest.fixture
def no_requests(monkeypatch):
    """Force the stdlib branch — the one a core install takes.

    Without this the test would exercise the `requests` path, which was always
    correct, and pass while the defect was live on the path everyone uses.
    """
    import builtins

    real_import = builtins.__import__

    def _import(name, *args, **kwargs):
        if name == "requests":
            raise ImportError("requests is not a core dependency")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _import)


def test_every_hop_is_guarded_not_just_the_first(
    redirect_server, guard_spy, no_requests
):
    """The defect, stated as a count."""
    body, final, _ctype = webtools._http_get(f"{redirect_server}/start", timeout=5)
    assert body == b"TARGET"
    assert final.endswith("/target")
    assert len(guard_spy) == 2, f"only {len(guard_spy)} hop(s) guarded: {guard_spy}"
    assert guard_spy[0].endswith("/start")
    assert guard_spy[1].endswith("/target")


def test_a_redirect_to_a_blocked_target_is_refused(redirect_server, monkeypatch):
    """The consequence the count stands for. With loopback NOT allowed, the
    redirect destination must be refused even though the first hop was fine.
    """
    monkeypatch.setenv("OPENAI4S_ALLOW_NETWORK", "1")
    monkeypatch.setenv("OPENAI4S_ALLOW_PRIVATE_FETCH", "1")
    calls: list[str] = []
    real = webtools._guard_url

    def _guard(url):
        calls.append(url)
        if url.endswith("/target"):
            raise webtools.SSRFBlocked("pretend this resolves to a metadata address")
        return real(url)

    monkeypatch.setattr(webtools, "_guard_url", _guard)
    import builtins

    real_import = builtins.__import__
    monkeypatch.setattr(
        builtins,
        "__import__",
        lambda name, *a, **k: (_ for _ in ()).throw(ImportError())
        if name == "requests"
        else real_import(name, *a, **k),
    )

    with pytest.raises(webtools.SSRFBlocked):
        webtools._http_get(f"{redirect_server}/start", timeout=5)
    assert len(calls) == 2, "the destination was never offered to the guard"


def test_a_redirect_chain_still_terminates(redirect_server, guard_spy, no_requests):
    """Handling redirects ourselves means owning the loop bound too. A server
    that redirects forever must raise rather than spin."""
    with pytest.raises(RuntimeError, match="too many redirects"):
        webtools._http_get(f"{redirect_server}/start", timeout=5, _max_redirects=0)


def test_the_no_redirect_handler_is_shared_rather_than_copied():
    """`web_probe` had this right in a private class while `_http_get` did not.
    One handler now, so the next caller inherits the behaviour instead of
    rediscovering it."""
    import inspect

    source = inspect.getsource(webtools)
    assert source.count("class _NoRedirect") == 1
    probe = inspect.getsource(webtools.web_probe)
    assert "_no_redirect_opener()" in probe
    assert "build_opener" not in probe
