"""Defense-in-depth response headers for the local web UI.

These do not replace correct output encoding — they bound the damage when it
fails. The UI renders plenty of externally-influenced strings (remote hostnames
harvested over ssh, GPU model names from nvidia-smi, package names, connector
metadata), and several still reach the DOM through innerHTML. A strict CSP is
what stops an injected `<script>` from running or phoning home.

All executable UI scripts are same-origin static files. Keeping executable
code out of HTML means the policy needs neither a nonce nor a dynamically
derived hash, and avoids having a security decision depend on duplicating the
browser's full HTML tokenizer.
"""

from __future__ import annotations

from pathlib import Path


def artifact_content_security_policy() -> str:
    """Policy for untrusted, user- or agent-authored Artifact bytes.

    Artifact HTML may be opened directly as well as inside the Workbench's
    sandboxed iframe. A response-level sandbox therefore keeps the document on
    an opaque origin in either navigation mode, while the explicit script and
    connection bans prevent it from composing executable sibling Artifacts.
    """
    return "; ".join(
        [
            "default-src 'none'",
            "script-src 'none'",
            "style-src 'unsafe-inline'",
            "img-src data: blob:",
            "font-src data:",
            "media-src data: blob:",
            "connect-src 'none'",
            "object-src 'none'",
            "base-uri 'none'",
            "form-action 'none'",
            "frame-ancestors 'self'",
            "sandbox",
        ]
    )


def artifact_security_headers(index_html: Path) -> dict[str, str]:
    """Hardened headers for an embeddable, untrusted Artifact document."""
    headers = security_headers(index_html)
    headers["Content-Security-Policy"] = artifact_content_security_policy()
    # The Workbench embeds same-origin previews. DENY would make that secure by
    # rendering nothing; SAMEORIGIN preserves the product while the CSP sandbox
    # removes the preview document's origin and active capabilities.
    headers["X-Frame-Options"] = "SAMEORIGIN"
    return headers


def embeddable_security_headers(index_html: Path) -> dict[str, str]:
    """Headers for a UI-owned document the Workbench loads in an iframe.

    `/ketcher` and the vendored editor it frames are first-party documents, not
    Artifact bytes: they keep the shell's `script-src 'self'` and same-origin
    `connect-src`. What they cannot keep is the shell's frame denial, because
    the product's only way to reach them is an iframe of the workbench page.
    `DENY` there is not a policy, it is the editor never rendering.
    """
    headers = security_headers(index_html)
    headers["Content-Security-Policy"] = content_security_policy(
        index_html, frame_ancestors="'self'"
    )
    headers["X-Frame-Options"] = "SAMEORIGIN"
    return headers


def content_security_policy(
    index_html: Path, *, frame_ancestors: str = "'none'"
) -> str:
    """Return the static policy; ``index_html`` remains for API compatibility.

    ``frame_ancestors`` is the one directive that varies by document, and it
    varies because of who embeds it, not because of what it contains.
    """
    _ = index_html
    script_src = ["'self'"]
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
            f"frame-ancestors {frame_ancestors}",
        ]
    )
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


__all__ = [
    "artifact_content_security_policy",
    "artifact_security_headers",
    "content_security_policy",
    "embeddable_security_headers",
    "security_headers",
]
