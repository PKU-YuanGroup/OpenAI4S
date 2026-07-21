"""Defense-in-depth response headers for the local web UI.

These do not replace correct output encoding — they bound the damage when it
fails. The UI renders plenty of externally-influenced strings (remote hostnames
harvested over ssh, GPU model names from nvidia-smi, package names, connector
metadata), and several still reach the DOM through innerHTML. A strict CSP is
what stops an injected `<script>` from running or phoning home.

The policy is hash-based rather than nonce-based on purpose: index.html is
served straight off the working tree as a static file, so there is no render
step in which to stamp a nonce. Its single inline <script> is a static theme
bootstrap, and hashing it keeps `script-src` free of 'unsafe-inline' — the
concession that would otherwise make the whole policy decorative.

The hash is derived from the file at runtime rather than pinned as a constant
so that editing the bootstrap cannot silently break the page: the header
follows the file. `webui/` is served live from the tree with no build step,
which is exactly the condition under which a hardcoded hash would drift.
"""
from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

_INLINE_SCRIPT_RE = re.compile(rb"<script>(.*?)</script>", re.DOTALL)

# Keyed by (path, mtime, size), not path alone. webui/ is served live off the
# working tree with no build step, so index.html can change under a running
# daemon — and a stale hash does not degrade, it blanks the page by blocking
# the very script the policy was built for. Re-hash when the file moves.
_cache: dict[tuple[str, int, int], str] = {}


def _inline_script_hashes(index_html: Path) -> list[str]:
    """CSP source-expressions for every inline <script> in the document.

    The hash covers the element's exact text content, which is what the CSP
    spec digests — including the surrounding newlines and indentation.
    """
    try:
        raw = index_html.read_bytes()
    except OSError:
        return []
    out = []
    for body in _INLINE_SCRIPT_RE.findall(raw):
        digest = base64.b64encode(hashlib.sha256(body).digest()).decode("ascii")
        out.append(f"'sha256-{digest}'")
    return out


def _cache_key(index_html: Path) -> tuple[str, int, int]:
    try:
        st = index_html.stat()
        return (str(index_html), int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        return (str(index_html), 0, 0)


def content_security_policy(index_html: Path) -> str:
    key = _cache_key(index_html)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    script_src = ["'self'", *_inline_script_hashes(index_html)]
    # 3Dmol compiles WebAssembly for molecular surfaces. 'wasm-unsafe-eval'
    # permits exactly that and nothing else — unlike 'unsafe-eval', it does not
    # re-enable eval()/new Function() for injected script.
    script_src.append("'wasm-unsafe-eval'")

    policy = "; ".join(
        [
            # Everything the app needs ships with it; nothing is fetched from a
            # third party, so the default can be closed.
            "default-src 'self'",
            f"script-src {' '.join(script_src)}",
            # style-src keeps 'unsafe-inline': the UI sets style="" attributes
            # through innerHTML in a handful of places. Style injection cannot
            # execute script, so this is the cheap concession, not script-src.
            "style-src 'self' 'unsafe-inline'",
            # data: for icons, blob: for figures/structures built client-side.
            "img-src 'self' data: blob:",
            "font-src 'self' data:",
            # Same-origin only. This is the exfiltration bound: an injected
            # script cannot POST harvested data to an attacker's host.
            "connect-src 'self'",
            "worker-src 'self' blob:",
            "object-src 'none'",
            "base-uri 'none'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )
    _cache[key] = policy
    return policy


def security_headers(index_html: Path) -> dict[str, str]:
    """Headers applied to every response the gateway emits."""
    return {
        "Content-Security-Policy": content_security_policy(index_html),
        # The gateway serves user/agent-authored artifacts; sniffing turns a
        # text/plain artifact into an executable document.
        "X-Content-Type-Options": "nosniff",
        # frame-ancestors already covers this for modern browsers; kept for
        # older ones since clickjacking a localhost control plane is cheap.
        "X-Frame-Options": "DENY",
        # The daemon is loopback-only, but paths and session ids do not belong
        # in a Referer header if a user ever proxies it.
        "Referrer-Policy": "same-origin",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    }


__all__ = ["content_security_policy", "security_headers"]
