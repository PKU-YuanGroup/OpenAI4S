"""The trust store Host-side HTTPS verifies against.

Every outbound TLS connection the Host opens builds its context here, so the
answer to "which roots does this process trust" has one place to live.

The default answer is the interpreter's own: ``ssl.create_default_context()``.
On macOS that is not enough. A python-build-standalone interpreter (what ``uv``
installs) points OpenSSL at ``/private/etc/ssl/cert.pem``, a static bundle that
lags the system root store -- so a server whose chain ends in a newer root
verifies in ``curl`` and every browser and fails here with ``unable to get
local issuer certificate``. On darwin the context therefore *additionally*
trusts the roots Apple ships, exported once per process with ``security``.

What this deliberately does not do:

* It never relaxes verification. ``CERT_REQUIRED`` and hostname checking stay
  exactly as ``create_default_context`` set them; roots are only ever added.
* It reads only ``SystemRootCertificates.keychain``. The login and admin
  keychains also hold certificates, but ``find-certificate`` cannot say whether
  one of those is *trusted*, and a certificate that is merely present is not a
  trust anchor.
* It does nothing when the operator pinned a trust store with ``SSL_CERT_FILE``
  or ``SSL_CERT_DIR``: an explicit bundle is the whole answer, not a floor.
* It does nothing off darwin, and falls back to the defaults when ``security``
  is missing, fails, or prints no certificate.
"""

from __future__ import annotations

import os
import ssl
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Mapping

#: Apple's shipped roots, and nothing a user or an MDM profile added.
APPLE_ROOTS_KEYCHAIN = "/System/Library/Keychains/SystemRootCertificates.keychain"

_SECURITY = "/usr/bin/security"
_PEM_MARKER = "-----BEGIN CERTIFICATE-----"

#: OpenSSL verify codes that mean "no trust anchor for this chain": unable to
#: get issuer (2), self-signed leaf (18), self-signed in chain (19), unable to
#: get local issuer (20), unable to verify the first certificate (21). An
#: expired certificate or a hostname mismatch is a different problem, and
#: telling the user to change their CA bundle for it would be wrong.
_NO_TRUST_ANCHOR = frozenset({2, 18, 19, 20, 21})

_lock = threading.Lock()
#: ``None`` until the first export; ``""`` when there is nothing to add.
_apple_roots: str | None = None


def _export_apple_roots() -> str:
    completed = subprocess.run(  # noqa: S603 - fixed argv, absolute binary
        [_SECURITY, "find-certificate", "-a", "-p", APPLE_ROOTS_KEYCHAIN],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
        # Nothing the daemon holds in its environment is this tool's business.
        env={"PATH": "/usr/bin:/bin"},
    )
    return completed.stdout


def apple_roots_pem(
    *,
    platform: str | None = None,
    runner: Callable[[], str] | None = None,
) -> str:
    """Apple's shipped roots as PEM, or ``""`` when there are none to add.

    Uncached and injectable: ``platform`` and ``runner`` exist so both branches
    are testable on either OS. Any failure of the export is "nothing to add".
    """
    if (sys.platform if platform is None else platform) != "darwin":
        return ""
    try:
        pem = (runner or _export_apple_roots)()
    except Exception:  # noqa: BLE001 - fail soft to the interpreter defaults
        return ""
    return pem if isinstance(pem, str) and _PEM_MARKER in pem else ""


def _cached_apple_roots() -> str:
    global _apple_roots
    with _lock:
        if _apple_roots is None:
            _apple_roots = apple_roots_pem()
        return _apple_roots


def _trust_store_pinned(environ: Mapping[str, str]) -> bool:
    return bool(environ.get("SSL_CERT_FILE") or environ.get("SSL_CERT_DIR"))


def default_context(
    *,
    environ: Mapping[str, str] | None = None,
    extra_roots: str | None = None,
) -> ssl.SSLContext:
    """``ssl.create_default_context()``, plus Apple's shipped roots on darwin.

    A fresh context per call, like the stdlib: callers may set a protocol floor
    on theirs without changing anybody else's.
    """
    context = ssl.create_default_context()
    if _trust_store_pinned(os.environ if environ is None else environ):
        return context
    pem = _cached_apple_roots() if extra_roots is None else extra_roots
    if pem:
        try:
            context.load_verify_locations(cadata=pem)
        except (ssl.SSLError, ValueError):
            # An unparseable export adds nothing; the defaults still stand.
            pass
    return context


def https_context() -> ssl.SSLContext:
    """The context urllib would have built for HTTPS, over `default_context`.

    Mirrors ``http.client._create_https_context``: a context handed to urllib
    is used as given, so the two settings it applies to its own are applied
    here to keep the wire behaviour identical apart from the trust store.
    """
    context = default_context()
    context.set_alpn_protocols(["http/1.1"])
    if context.post_handshake_auth is not None:
        context.post_handshake_auth = True
    return context


def https_handler() -> urllib.request.HTTPSHandler:
    """An HTTPS handler for ``build_opener`` that verifies with `https_context`."""
    return urllib.request.HTTPSHandler(context=https_context())


class UntrustedCertificate(urllib.error.URLError):
    """A server certificate this process could not verify, said actionably.

    Still a ``URLError`` (and so an ``OSError``) whose ``reason`` is the
    original ``SSLCertVerificationError``: every existing handler keeps
    working, and only the text a user reads changes.
    """

    def __init__(self, host: str, reason: ssl.SSLCertVerificationError) -> None:
        super().__init__(reason)
        self.host = host

    def __str__(self) -> str:
        return certificate_help(self.host, self.reason)


def certificate_failure(
    error: BaseException | None,
) -> ssl.SSLCertVerificationError | None:
    """The certificate-verification failure inside ``error``, if it is one."""
    # urllib wraps it as ``URLError.reason``; a client's own transport error
    # carries it as ``__cause__``. A few links is as deep as either goes.
    for _ in range(4):
        if isinstance(error, ssl.SSLCertVerificationError):
            return error
        if not isinstance(error, BaseException):
            return None
        reason = getattr(error, "reason", None)
        error = reason if isinstance(reason, BaseException) else error.__cause__
    return None


def certificate_help(host: str, failure: ssl.SSLCertVerificationError) -> str:
    """One sentence a user can act on: which host, what is wrong, what to set."""
    detail = str(getattr(failure, "verify_message", "") or "certificate verify failed")
    if getattr(failure, "verify_code", None) in _NO_TRUST_ANCHOR:
        return (
            f"TLS certificate for {host} could not be verified ({detail}): the CA "
            "bundle this Python uses does not contain the issuer of that "
            "certificate. Set SSL_CERT_FILE to a PEM bundle that does (your "
            "system or corporate roots) and restart OpenAI4S."
        )
    return (
        f"TLS certificate for {host} could not be verified ({detail}). "
        "Verification is never skipped; check the host name and this "
        "machine's clock."
    )


def untrusted_certificate(
    error: BaseException, url: str
) -> UntrustedCertificate | None:
    """``error`` as an `UntrustedCertificate` for ``url``, or ``None``."""
    failure = certificate_failure(error)
    if failure is None:
        return None
    host = urllib.parse.urlsplit(url).hostname or url
    return UntrustedCertificate(host, failure)


__all__ = [
    "APPLE_ROOTS_KEYCHAIN",
    "UntrustedCertificate",
    "apple_roots_pem",
    "certificate_failure",
    "certificate_help",
    "default_context",
    "https_context",
    "https_handler",
    "untrusted_certificate",
]
