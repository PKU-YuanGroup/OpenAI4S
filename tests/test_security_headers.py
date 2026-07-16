"""Regressions for the gateway's defense-in-depth response headers.

The UI renders externally-influenced strings — remote hostnames and GPU model
names harvested over ssh, package names, connector metadata — and several
reach the DOM through innerHTML. Correct encoding is the real fix; the CSP is
what bounds the damage when a sink is missed.

The policy's value rests on two properties that are easy to lose in an edit:
`script-src` must never gain 'unsafe-inline' (which would make it decorative
against exactly the injection it exists to stop), and `connect-src` must stay
same-origin (the exfiltration bound). Both are pinned below.

Verified live in a browser as well: with this policy the app loads and its
same-origin WebSocket connects, while an injected onerror handler and an
external script are blocked (`script-src-attr`/`script-src-elem` violations)
and a cross-origin fetch/WebSocket is refused by `connect-src`.
"""
import os
import re

import pytest

from openai4s.server.security_headers import content_security_policy, security_headers

_WEBUI = None


@pytest.fixture
def index_html():
    from openai4s.server.gateway import WEBUI_DIR

    return WEBUI_DIR / "index.html"


def _directive(policy: str, name: str) -> str:
    for part in policy.split(";"):
        part = part.strip()
        if part.split(" ")[0] == name:
            return part
    raise AssertionError(f"{name} missing from policy: {policy}")


def test_script_src_never_allows_unsafe_inline(index_html):
    """The load-bearing assertion. index.html has exactly one inline script (a
    static theme bootstrap) and zero inline event handlers, so the policy can
    hash that one script instead of opening the door to every injected one."""
    script_src = _directive(content_security_policy(index_html), "script-src")
    assert "'unsafe-inline'" not in script_src
    assert "'unsafe-eval'" not in script_src


def test_script_src_hashes_the_real_inline_script(index_html):
    """Derived from the file, not pinned as a constant: webui/ is served live
    off the working tree with no build step, so a hardcoded hash would drift
    the moment someone edited the bootstrap — and a stale hash means a blank
    page, not a loud failure."""
    script_src = _directive(content_security_policy(index_html), "script-src")
    hashes = re.findall(r"'sha256-[A-Za-z0-9+/=]+'", script_src)
    assert len(hashes) == 1, f"expected one inline-script hash, got {hashes}"


def test_the_hash_tracks_edits_to_the_inline_script(tmp_path):
    original = tmp_path / "a.html"
    original.write_text("<script>var a = 1;</script>")
    edited = tmp_path / "b.html"
    edited.write_text("<script>var a = 2;</script>")
    assert content_security_policy(original) != content_security_policy(edited)


def test_editing_index_html_in_place_reissues_the_hash(tmp_path):
    """webui/ is served live off the working tree — the same path's contents
    change under a running daemon. A path-keyed cache would keep serving the
    old hash and blank the page by blocking the very script it was built for,
    with no error anywhere on the server.
    """
    page = tmp_path / "index.html"
    page.write_text("<script>var a = 1;</script>")
    before = content_security_policy(page)

    os.utime(page, ns=(0, 0))  # make the rewrite's mtime unambiguously different
    page.write_text("<script>var a = 22;</script>")
    after = content_security_policy(page)

    assert before != after, "cache must invalidate when index.html changes"
    assert _directive(after, "script-src") != _directive(before, "script-src")


def test_connect_src_is_same_origin_only(index_html):
    """The exfiltration bound: an injected script must not be able to POST
    harvested data anywhere. Verified in-browser — a cross-origin fetch and a
    cross-origin WebSocket both raise connect-src violations."""
    assert _directive(content_security_policy(index_html), "connect-src") == (
        "connect-src 'self'"
    )


def test_wasm_is_permitted_without_reopening_eval(index_html):
    """3Dmol compiles WebAssembly for molecular surfaces. 'wasm-unsafe-eval'
    covers that alone; 'unsafe-eval' would also hand eval() back to injected
    script."""
    script_src = _directive(content_security_policy(index_html), "script-src")
    assert "'wasm-unsafe-eval'" in script_src


def test_dangerous_sinks_are_closed(index_html):
    policy = content_security_policy(index_html)
    assert _directive(policy, "object-src") == "object-src 'none'"
    assert _directive(policy, "base-uri") == "base-uri 'none'"
    assert _directive(policy, "frame-ancestors") == "frame-ancestors 'none'"


def test_default_src_is_self(index_html):
    assert _directive(content_security_policy(index_html), "default-src") == (
        "default-src 'self'"
    )


def test_style_src_inline_is_a_deliberate_concession(index_html):
    """The UI sets style="" through innerHTML in a couple of places. Style
    injection cannot execute script, so this stays permitted — the point is
    that the concession is here and not in script-src."""
    style_src = _directive(content_security_policy(index_html), "style-src")
    assert "'unsafe-inline'" in style_src


def test_all_expected_headers_present(index_html):
    h = security_headers(index_html)
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert h["Referrer-Policy"] == "same-origin"
    assert "Content-Security-Policy" in h
    assert "Permissions-Policy" in h


def test_missing_index_html_still_yields_a_usable_policy(tmp_path):
    """A policy without the hash is wrong-but-closed (the page breaks loudly)
    rather than absent — never fail open into no CSP at all."""
    policy = content_security_policy(tmp_path / "nope.html")
    assert "default-src 'self'" in policy
    assert "'unsafe-inline'" not in _directive(policy, "script-src")
