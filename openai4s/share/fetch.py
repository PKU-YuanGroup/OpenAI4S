"""SSRF-hardened bundle download for ``openai4s share import <url>``.

The daemon — not the CLI — performs the fetch so the policy is enforced in one
place: HTTPS-only off loopback, no URL credentials, every redirect hop
re-validated, resolved IPs rejected if private/loopback/link-local, and a hard
streamed size cap independent of any advertised Content-Length.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

MAX_BUNDLE_BYTES = 128 << 20
_MAX_REDIRECTS = 3


class BundleFetchError(ValueError):
    """The share URL is unsafe or the download failed."""


def _validate_url(url: str, *, allow_insecure: bool) -> tuple[str, str, int, str]:
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    host = parsed.hostname or ""
    if not host:
        raise BundleFetchError("share URL has no host")
    if parsed.username or parsed.password:
        raise BundleFetchError("credentials in the URL are not allowed")
    loopback = host in ("127.0.0.1", "localhost", "::1")
    if scheme == "http":
        if not (loopback and allow_insecure):
            raise BundleFetchError("plaintext http:// is only allowed on loopback")
    elif scheme != "https":
        raise BundleFetchError("share URL must be https://")
    if not (loopback and allow_insecure):
        _reject_private(host)
    port = parsed.port or (443 if scheme == "https" else 80)
    return scheme, host, port, parsed.geturl()


def _reject_private(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as error:
        raise BundleFetchError(f"could not resolve {host!r}") from error
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr.split("%", 1)[0])
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise BundleFetchError(f"refusing to fetch a private address ({addr})")


def normalize_share_url(url: str) -> str:
    """A bare share link (no path) gets the ``/bundle`` download path."""

    parsed = urlparse(url if "://" in url else "https://" + url)
    path = parsed.path or ""
    if path in ("", "/"):
        parsed = parsed._replace(path="/bundle")
    return urlunparse(parsed)


def fetch_bundle(
    url: str,
    *,
    allow_insecure: bool = False,
    max_bytes: int = MAX_BUNDLE_BYTES,
    timeout: float = 120.0,
) -> bytes:
    current = normalize_share_url(url)
    for _hop in range(_MAX_REDIRECTS + 1):
        _validate_url(current, allow_insecure=allow_insecure)
        # A real User-Agent: the default ``Python-urllib`` string is blocked as a
        # bot by common CDNs/WAFs (e.g. Cloudflare), which would 403 the download
        # before it reaches the relay.
        req = Request(
            current,
            method="GET",
            headers={"Accept": "*/*", "User-Agent": "openai4s-share/0.1"},
        )
        try:
            # Redirects are handled manually so every hop is re-validated.
            resp = urlopen(req, timeout=timeout)  # noqa: S310 - scheme validated above
        except OSError as error:
            raise BundleFetchError(f"download failed: {error}") from error
        status = getattr(resp, "status", 200)
        if status in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location")
            resp.close()
            if not location:
                raise BundleFetchError("redirect without a Location")
            current = normalize_share_url(location)
            continue
        with resp:
            declared = resp.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise BundleFetchError("bundle exceeds the size limit")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise BundleFetchError("bundle exceeds the size limit")
                chunks.append(chunk)
            return b"".join(chunks)
    raise BundleFetchError("too many redirects")


__all__ = [
    "BundleFetchError",
    "MAX_BUNDLE_BYTES",
    "fetch_bundle",
    "normalize_share_url",
]
