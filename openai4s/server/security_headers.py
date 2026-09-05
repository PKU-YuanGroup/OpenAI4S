"""Defense-in-depth response headers for the local web UI.

These do not replace correct output encoding — they bound the damage when it
fails. The UI renders plenty of externally-influenced strings (remote hostnames
harvested over ssh, GPU model names from nvidia-smi, package names, connector
metadata), and several still reach the DOM through innerHTML. A strict CSP is
what stops an injected `<script>` from running or phoning home.

All executable UI scripts are same-origin static files. Keeping executable
code out of HTML means the policy needs neither a nonce nor a dynamically
derived hash, and avoids having a security decision depend on duplicating the
browser's full HTML tokenizer. Nothing here reads a file: the policies are
constants, and the one directive that varies — `frame-ancestors` — varies by
who embeds a document rather than by what the document contains.
"""

from __future__ import annotations

from collections.abc import Sequence


def artifact_content_security_policy() -> str:
    """Policy for untrusted, user- or agent-authored Artifact bytes.

    Artifact HTML may be opened directly as well as inside the Workbench's
    sandboxed iframe, so the sandbox rides the response and applies in either
    navigation mode.

    **Artifact HTML never executes script in the product.** `script-src 'none'`
    says so, the sandbox has no `allow-scripts`, and `app.js` frames previews
    with `sandbox=""`; all three agree on purpose. A skill that emits an
    interactive dashboard — `retrosynthesis_planning`, `admet_genetic` — gets a
    static rendering in the Workbench and in a `/preview/` tab, and its
    interactivity only on a downloaded copy opened from the filesystem. That is
    a deliberate trade, not an oversight: these bytes are model-authored, and
    the alternative is executing them on the origin that holds the session
    cookie. Say it here rather than leaving a reader to infer it from three
    separate files.

    `allow-same-origin` is the one sandbox token granted, and it buys back the
    sub-resources a report needs. Without it the document is on an opaque
    origin, where `'self'` matches nothing and `<img src="figure.png">` — the
    standard Code-as-Action pair, which `store.artifact_by_unique_filename`
    exists to resolve — fails to load even on a top-level View. It grants no
    active capability while `script-src 'none'` and the missing `allow-scripts`
    stand: with no script there is nothing to read a cookie or a sibling
    document with.
    """
    return "; ".join(
        [
            "default-src 'none'",
            "script-src 'none'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:",
            "font-src 'self' data:",
            "media-src 'self' data: blob:",
            "connect-src 'none'",
            "object-src 'none'",
            "base-uri 'none'",
            "form-action 'none'",
            "frame-ancestors 'self'",
            "sandbox allow-same-origin",
        ]
    )


def sandboxed_artifact_content_security_policy(app_origins: Sequence[str]) -> str:
    """Policy for artifact bytes served on the *sandbox* origin.

    This is the one place artifact HTML is allowed to execute, and it is safe
    for a reason that has nothing to do with the script: the origin it runs on
    holds no cookie, answers nothing but grant-scoped artifact bytes, and is
    cross-origin with the app, so `parent.document` and the REST API are behind
    the same-origin policy rather than behind a directive.

    What stays closed is exfiltration. `default-src 'none'` with no host in any
    fetch directive means a script here cannot beacon out -- not by `fetch`
    (`connect-src 'none'`), not by an image URL, not by a form post. `'self'`
    here is the sandbox origin, so a report still loads its own sibling files.

    `frame-ancestors` names the app origins literally: `'self'` would mean the
    sandbox origin, which is not who embeds this.
    """
    ancestors = " ".join(app_origins) if app_origins else "'none'"
    return "; ".join(
        [
            "default-src 'none'",
            # The document's own inline script is the point of this origin.
            "script-src 'self' 'unsafe-inline'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:",
            "font-src 'self' data:",
            "media-src 'self' data: blob:",
            "connect-src 'none'",
            "object-src 'none'",
            "base-uri 'none'",
            "form-action 'none'",
            f"frame-ancestors {ancestors}",
            # `allow-same-origin` is safe here and not on the app origin: this
            # document's origin is the sandbox, so keeping it buys `'self'` for
            # sub-resources without granting reach into anything.
            "sandbox allow-scripts allow-same-origin",
        ]
    )


def sandboxed_artifact_security_headers(
    app_origins: Sequence[str],
) -> dict[str, str]:
    """Headers for an executable artifact preview on the sandbox origin."""
    headers = security_headers()
    headers["Content-Security-Policy"] = sandboxed_artifact_content_security_policy(
        app_origins
    )
    if app_origins:
        # Dropped, not set to a permissive value: `X-Frame-Options` has no
        # "these origins" form, and its non-standard `ALLOWALL` relies on
        # browsers ignoring a value they cannot parse. `frame-ancestors` is
        # the standard control and takes precedence wherever both appear, so
        # the honest thing is to let it be the only one.
        headers.pop("X-Frame-Options", None)
    return headers


def artifact_security_headers() -> dict[str, str]:
    """Hardened headers for an embeddable, untrusted Artifact document."""
    headers = security_headers()
    headers["Content-Security-Policy"] = artifact_content_security_policy()
    # The Workbench embeds same-origin previews. DENY would make that secure by
    # rendering nothing; SAMEORIGIN preserves the product while the CSP sandbox
    # removes the preview document's active capabilities.
    headers["X-Frame-Options"] = "SAMEORIGIN"
    return headers


def embeddable_security_headers() -> dict[str, str]:
    """Headers for a UI-owned document the Workbench loads in an iframe.

    `/ketcher` and the vendored editor it frames are first-party documents, not
    Artifact bytes: they keep the shell's `script-src 'self'` and same-origin
    `connect-src`. What they cannot keep is the shell's frame denial, because
    the product's only way to reach them is an iframe of the workbench page.
    `DENY` there is not a policy, it is the editor never rendering.
    """
    headers = security_headers()
    headers["Content-Security-Policy"] = content_security_policy(
        frame_ancestors="'self'"
    )
    headers["X-Frame-Options"] = "SAMEORIGIN"
    return headers


def content_security_policy(
    *,
    frame_ancestors: str = "'none'",
    frame_src: Sequence[str] = (),
) -> str:
    """Return the static UI-shell policy.

    Two directives vary, and both vary by *who is on the other side of a
    frame boundary* rather than by what this document contains:

    ``frame_ancestors``
        who may embed this document.
    ``frame_src``
        who this document may embed. It has to name the sandbox origin
        explicitly: `default-src 'self'` is the `frame-src` fallback, and the
        sandbox origin is deliberately a *different* origin, so the shell's own
        policy refuses to frame it until it is named. That is not theoretical
        -- it is what Chromium did, with "Framing 'http://localhost:PORT/'
        violates ... default-src 'self'", while every response header on the
        sandbox side was already correct.
    """
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
            " ".join(["frame-src", "'self'", *frame_src]),
            f"frame-ancestors {frame_ancestors}",
        ]
    )
    return policy


def security_headers(*, frame_src: Sequence[str] = ()) -> dict[str, str]:
    """Headers applied to every response the gateway emits."""
    return {
        "Content-Security-Policy": content_security_policy(frame_src=frame_src),
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
    "sandboxed_artifact_content_security_policy",
    "sandboxed_artifact_security_headers",
    "security_headers",
]
