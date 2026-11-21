"""Host-side HTTPS trusts the system's roots on macOS, and nothing weaker.

The bug this pins (usability review ENV-1): a uv-installed CPython on macOS
verifies against `/private/etc/ssl/cert.pem`, which lags the system root
store. RCSB's chain ends in a root that file does not have, so the built-in
example, `science_search(pdb)` and `web_fetch` of two RCSB hosts failed with
"unable to get local issuer certificate" while `curl` worked.

Three things are asserted, in order of how the fix could silently rot:

1. the decision itself -- darwin only, Apple's shipped keychain only, soft
   failure, an explicit `SSL_CERT_FILE` wins, verification never relaxed;
2. every Host-side HTTPS opener really goes through the helper (one positive
   test per seam, because a helper wired to one call site of several is the
   failure this repository keeps having);
3. no *new* opener can skip it (an AST scan with a reasoned exemption table).

Offline: the platform and the `security` runner are injected, and the "root"
is a throwaway self-signed certificate with no private key anywhere.
"""

from __future__ import annotations

import ast
import re
import ssl
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from openai4s import tls_trust

_PACKAGE = Path(__file__).resolve().parent.parent / "openai4s"


def _hosts(text: str) -> set[str]:
    """The host names a text names, each an exact token (not a substring)."""
    return set(re.findall(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", text))


#: A throwaway self-signed CA certificate (public half only).
TEST_ROOT_PEM = """-----BEGIN CERTIFICATE-----
MIIBgDCCASagAwIBAgIUDNQ4sD/Dk1Gf75dyj1+2qdgAbYAwCgYIKoZIzj0EAwIw
JTEjMCEGA1UEAwwab3BlbmFpNHMgb2ZmbGluZSB0ZXN0IHJvb3QwIBcNMjYxMTIw
MDYxNDU4WhgPMjEyNjEwMjcwNjE0NThaMCUxIzAhBgNVBAMMGm9wZW5haTRzIG9m
ZmxpbmUgdGVzdCByb290MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEf38dLYkD
ocrhFzsj4DNRZMjDLUoNud4BgG2u7h5SNTUUWdTUnVDgWiMtPqiAOt1KlWchZ2iN
LozEis18cBAQ8aMyMDAwHQYDVR0OBBYEFIDwpY4EMnT/fyI8lEWOwC4TeN2GMA8G
A1UdEwEB/wQFMAMBAf8wCgYIKoZIzj0EAwIDSAAwRQIgFUYekVdCFlc98OvHt53/
rrf6cngDrqHbjz77Isus44sCIQDY78N0mK3CoipkZCH98sd6xbS5OmKX3u9ghq6/
fks83w==
-----END CERTIFICATE-----
"""
_TEST_ROOT_CN = "openai4s offline test root"


def _trusts_test_root(context: ssl.SSLContext) -> bool:
    return any(
        (("commonName", _TEST_ROOT_CN),) in cert.get("subject", ())
        for cert in context.get_ca_certs()
    )


@pytest.fixture
def system_root(monkeypatch):
    """Pretend the process-wide export already produced the test root."""
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setattr(tls_trust, "_apple_roots", TEST_ROOT_PEM)


# --------------------------------------------------------------------------
# 1. the decision
# --------------------------------------------------------------------------


def test_linux_never_runs_the_exporter():
    def runner():
        raise AssertionError("the keychain export must not run off darwin")

    assert tls_trust.apple_roots_pem(platform="linux", runner=runner) == ""


def test_darwin_returns_what_the_exporter_printed():
    pem = tls_trust.apple_roots_pem(platform="darwin", runner=lambda: TEST_ROOT_PEM)
    assert pem == TEST_ROOT_PEM


def test_the_export_reads_only_apples_shipped_keychain(monkeypatch):
    """The login and admin keychains hold certificates `find-certificate`
    cannot vouch for; naming either would turn "present" into "trusted"."""
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=TEST_ROOT_PEM, stderr="")

    monkeypatch.setattr(tls_trust.subprocess, "run", fake_run)

    assert tls_trust.apple_roots_pem(platform="darwin") == TEST_ROOT_PEM
    [(argv, kwargs)] = calls
    assert argv == [
        "/usr/bin/security",
        "find-certificate",
        "-a",
        "-p",
        "/System/Library/Keychains/SystemRootCertificates.keychain",
    ]
    # The daemon's environment (provider keys included) is not handed over.
    assert set(kwargs["env"]) == {"PATH"}
    assert kwargs["check"] is True


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("security"),
        subprocess.CalledProcessError(1, "security"),
        subprocess.TimeoutExpired("security", 15),
        PermissionError("denied"),
    ],
)
def test_a_failed_export_adds_nothing(failure):
    def runner():
        raise failure

    assert tls_trust.apple_roots_pem(platform="darwin", runner=runner) == ""


@pytest.mark.parametrize("output", ["", "security: no certificates\n", None])
def test_output_without_a_certificate_adds_nothing(output):
    assert tls_trust.apple_roots_pem(platform="darwin", runner=lambda: output) == ""


def test_the_context_trusts_the_added_root_and_verifies_as_before():
    plain = ssl.create_default_context()
    context = tls_trust.default_context(environ={}, extra_roots=TEST_ROOT_PEM)

    assert not _trusts_test_root(plain)
    assert _trusts_test_root(context)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.minimum_version == plain.minimum_version


def test_nothing_to_add_is_exactly_the_default_context():
    context = tls_trust.default_context(environ={}, extra_roots="")
    plain = ssl.create_default_context()
    assert len(context.get_ca_certs()) == len(plain.get_ca_certs())
    assert context.verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize("variable", ["SSL_CERT_FILE", "SSL_CERT_DIR"])
def test_an_operator_pinned_trust_store_is_left_alone(variable):
    """An explicit bundle is the whole answer, not a floor to add roots to."""
    context = tls_trust.default_context(
        environ={variable: "/some/corporate/bundle"}, extra_roots=TEST_ROOT_PEM
    )
    assert not _trusts_test_root(context)


def test_an_unparseable_export_keeps_the_defaults():
    garbage = "-----BEGIN CERTIFICATE-----\nnot base64\n-----END CERTIFICATE-----\n"
    context = tls_trust.default_context(environ={}, extra_roots=garbage)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert not _trusts_test_root(context)


def test_the_export_runs_once_per_process(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    exports = []
    monkeypatch.setattr(tls_trust, "_apple_roots", None)
    monkeypatch.setattr(
        tls_trust, "apple_roots_pem", lambda: exports.append(1) or TEST_ROOT_PEM
    )

    contexts = [tls_trust.https_context() for _ in range(3)]

    assert len(exports) == 1
    assert all(_trusts_test_root(context) for context in contexts)
    # Fresh objects: one caller's protocol floor is not another's.
    assert len({id(context) for context in contexts}) == 3


def test_https_context_keeps_urllibs_own_settings(system_root):
    context = tls_trust.https_context()
    assert _trusts_test_root(context)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    if context.post_handshake_auth is not None:
        assert context.post_handshake_auth is True


# --------------------------------------------------------------------------
# 2. every Host-side HTTPS seam uses it
# --------------------------------------------------------------------------


def _https_handler_of(opener) -> urllib.request.HTTPSHandler:
    [handler] = [
        h for h in opener.handlers if isinstance(h, urllib.request.HTTPSHandler)
    ]
    return handler


def test_web_fetch_opener_trusts_the_system_roots(system_root):
    from openai4s import webtools

    handler = _https_handler_of(webtools._no_redirect_opener())
    assert _trusts_test_root(handler._context)


def test_deadline_transport_trusts_the_system_roots(system_root):
    """The LLM client, MCP HTTP, Doubao/Ollama search and TypeSafe all build
    their opener from this one class."""
    from openai4s.http_deadline import HTTPExchangeDeadline

    handler = _https_handler_of(HTTPExchangeDeadline(5.0).build_opener())
    assert _trusts_test_root(handler._context)


def test_endpoint_model_lookup_trusts_the_system_roots(system_root):
    from openai4s.server.endpoint_models import EndpointModelFetcher

    handler = _https_handler_of(EndpointModelFetcher().opener)
    assert _trusts_test_root(handler._context)


def test_telemetry_transport_trusts_the_system_roots(system_root, monkeypatch):
    from openai4s.telemetry import sender

    seen = []

    class _Response:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    class _Opener:
        def open(self, _request, timeout=None):
            return _Response()

    def build_opener(*handlers):
        seen.extend(handlers)
        return _Opener()

    monkeypatch.setattr(sender.urllib.request, "build_opener", build_opener)

    sender._default_transport(urllib.request.Request("https://telemetry.invalid/"))

    [handler] = [h for h in seen if isinstance(h, urllib.request.HTTPSHandler)]
    assert _trusts_test_root(handler._context)


def test_share_bundle_download_trusts_the_system_roots(system_root, monkeypatch):
    from openai4s.share import fetch

    seen = {}

    def urlopen(_request, **kwargs):
        seen.update(kwargs)
        raise OSError("stop before the network")

    monkeypatch.setattr(fetch, "urlopen", urlopen)
    monkeypatch.setattr(fetch, "_reject_private", lambda _host: None)

    with pytest.raises(fetch.BundleFetchError):
        fetch.fetch_bundle("https://share.invalid/bundle")
    assert _trusts_test_root(seen["context"])


def test_share_tunnel_trusts_the_system_roots(system_root, monkeypatch):
    from openai4s.share import ws_client

    wrapped = []

    class _Raw:
        def close(self):
            pass

    real_default_context = tls_trust.default_context

    def default_context():
        context = real_default_context()

        class _Recording:
            minimum_version = context.minimum_version

            def wrap_socket(self, _raw, server_hostname=None):
                wrapped.append((context, self.minimum_version, server_hostname))
                raise ssl.SSLError("stop before the handshake")

        return _Recording()

    monkeypatch.setattr(ws_client.socket, "create_connection", lambda *_a, **_k: _Raw())
    monkeypatch.setattr(ws_client.tls_trust, "default_context", default_context)

    with pytest.raises(ws_client.WSClientError):
        ws_client.WSClient.connect("wss://relay.invalid/tunnel")
    [(context, floor, hostname)] = wrapped
    assert _trusts_test_root(context)
    assert floor == ssl.TLSVersion.TLSv1_2
    assert hostname == "relay.invalid"


def test_managed_endpoint_probe_trusts_the_system_roots(system_root, monkeypatch):
    from openai4s.host import endpoints

    seen = {}

    def urlopen(_url, **kwargs):
        seen.update(kwargs)
        raise urllib.error.URLError("stop before the network")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    ready = endpoints.probe_ready("http://127.0.0.1:18999", "/health", own_port=18999)

    assert ready is False
    assert _trusts_test_root(seen["context"])


# --------------------------------------------------------------------------
# 3. and no new opener can skip it
# --------------------------------------------------------------------------

#: Names that create an HTTPS client or the context one verifies with.
_TLS_CLIENT_NAMES = frozenset(
    {"build_opener", "urlopen", "create_default_context", "HTTPSHandler"}
)

_STDLIB_BASES = ("urllib.request", "urllib_request", "ssl")

#: Modules that name one and do not use the helper, each with the reason.
_NOT_TLS: dict[str, str] = {
    "openai4s/tls_trust.py": "the helper itself.",
    "openai4s/cli/main.py": (
        "`_open_daemon` talks to this machine's own daemon over plain http."
    ),
    "openai4s/server/model_discovery.py": (
        "refuses any URL that is not loopback http before it builds a request."
    ),
}


def _tls_client_sites() -> dict[str, list[int]]:
    sites: dict[str, list[int]] = {}
    for path in sorted(_PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        rel = path.relative_to(_PACKAGE.parent).as_posix()
        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute) and ast.unparse(
                    func.value
                ).endswith(_STDLIB_BASES):
                    # `urllib.request.build_opener(...)`, not the deadline
                    # exchange's own `exchange.build_opener(...)`, which is
                    # the helper-backed transport tested above.
                    name = func.attr
            if name in _TLS_CLIENT_NAMES:
                sites.setdefault(rel, []).append(node.lineno)
    return sites


def test_every_https_client_goes_through_the_trust_helper():
    missing = []
    for module, lines in _tls_client_sites().items():
        if module in _NOT_TLS:
            continue
        if "tls_trust." not in (_PACKAGE.parent / module).read_text("utf-8"):
            missing.append(f"{module}:{lines[0]}")
    assert missing == [], (
        "these modules open an HTTPS client without openai4s.tls_trust, so on "
        "macOS they verify against a CA bundle that lags the system roots: "
        f"{missing}. Build the context with tls_trust.https_context() / "
        "https_handler(), or add the module to _NOT_TLS with the reason it "
        "never speaks TLS."
    )


def test_the_exemptions_are_not_stale():
    sites = _tls_client_sites()
    assert sorted(set(_NOT_TLS) - set(sites)) == []


# --------------------------------------------------------------------------
# the failure, said so a person can act on it
# --------------------------------------------------------------------------


def _verify_failure(code: int, message: str) -> ssl.SSLCertVerificationError:
    failure = ssl.SSLCertVerificationError(
        1, f"[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: {message}"
    )
    failure.verify_code = code
    failure.verify_message = message
    return failure


def test_a_missing_issuer_names_the_host_and_the_remedy():
    failure = _verify_failure(20, "unable to get local issuer certificate")
    error = urllib.error.URLError(failure)

    untrusted = tls_trust.untrusted_certificate(error, "https://search.example.org/q")

    assert isinstance(untrusted, urllib.error.URLError)
    assert untrusted.reason is failure
    text = str(untrusted)
    assert {"search.example.org"} <= _hosts(text)
    assert "unable to get local issuer certificate" in text
    assert "SSL_CERT_FILE" in text
    assert "/q" not in text


def test_an_expired_certificate_is_not_blamed_on_the_ca_bundle():
    failure = _verify_failure(10, "certificate has expired")
    text = str(tls_trust.untrusted_certificate(failure, "https://old.example.org/"))
    assert {"old.example.org"} <= _hosts(text)
    assert "certificate has expired" in text
    assert "SSL_CERT_FILE" not in text


def test_other_connection_failures_are_left_alone():
    assert (
        tls_trust.untrusted_certificate(
            urllib.error.URLError(ConnectionRefusedError(61, "refused")), "https://x/"
        )
        is None
    )
    assert (
        tls_trust.untrusted_certificate(
            urllib.error.URLError("plain prose"), "https://x/"
        )
        is None
    )
    assert tls_trust.untrusted_certificate(ssl.SSLError("EOF"), "https://x/") is None


def test_a_wrapped_failure_is_found_through_its_cause():
    failure = _verify_failure(20, "unable to get local issuer certificate")
    try:
        try:
            raise urllib.error.URLError(failure)
        except urllib.error.URLError as inner:
            raise RuntimeError("LLM connection error") from inner
    except RuntimeError as outer:
        assert tls_trust.certificate_failure(outer) is failure


@pytest.mark.stubbed_backend
def test_web_fetch_reports_the_untrusted_host(monkeypatch):
    import sys

    from openai4s import webtools

    failure = _verify_failure(20, "unable to get local issuer certificate")

    class _Opener:
        def open(self, _request, timeout=None):
            raise urllib.error.URLError(failure)

    monkeypatch.setenv("OPENAI4S_ALLOW_NETWORK", "1")
    monkeypatch.setitem(sys.modules, "requests", None)
    monkeypatch.setattr(webtools, "_guard_url", lambda _url: None)
    monkeypatch.setattr(webtools, "_no_redirect_opener", lambda: _Opener())

    with pytest.raises(urllib.error.URLError) as caught:
        webtools.web_fetch("https://search.example.org/api")
    assert {"search.example.org"} <= _hosts(str(caught.value))
    assert "SSL_CERT_FILE" in str(caught.value)

    probe = webtools.web_probe("https://search.example.org/api")
    assert probe["exists"] is False and probe["status"] == 0
    assert {"search.example.org"} <= _hosts(probe["error"])
    assert "SSL_CERT_FILE" in probe["error"]
