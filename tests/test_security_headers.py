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

from html.parser import HTMLParser

import pytest

from openai4s.server.security_headers import (
    artifact_content_security_policy,
    artifact_security_headers,
    content_security_policy,
    embeddable_security_headers,
    security_headers,
)

_WEBUI = None


class _ScriptInventory(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.sources: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self.sources.append(dict(attrs).get("src"))


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
    """The load-bearing assertion: executable code lives in static files."""
    script_src = _directive(content_security_policy(index_html), "script-src")
    assert "'unsafe-inline'" not in script_src
    assert "'unsafe-eval'" not in script_src
    assert "'sha256-" not in script_src


def test_index_html_has_only_same_origin_external_scripts(index_html):
    parser = _ScriptInventory()
    parser.feed(index_html.read_text(encoding="utf-8"))
    parser.close()

    assert parser.sources
    assert None not in parser.sources
    assert all(source.startswith("/static/") for source in parser.sources)
    assert parser.sources[0] == "/static/theme-bootstrap.js"


def test_policy_never_parses_html_to_authorize_scripts(tmp_path):
    """Escaped script tokenizer states cannot influence a static policy."""
    adversarial = tmp_path / "escaped-script.html"
    adversarial.write_text(
        "<script><!--<script>window.x=1;</script>-->window.y=2;</script>"
    )

    assert content_security_policy(adversarial) == content_security_policy(
        tmp_path / "missing.html"
    )


def test_untrusted_artifact_policy_cannot_execute_or_reach_same_origin():
    policy = artifact_content_security_policy()

    assert _directive(policy, "default-src") == "default-src 'none'"
    assert _directive(policy, "script-src") == "script-src 'none'"
    assert _directive(policy, "connect-src") == "connect-src 'none'"
    assert _directive(policy, "form-action") == "form-action 'none'"
    assert _directive(policy, "frame-ancestors") == "frame-ancestors 'self'"
    assert _directive(policy, "sandbox") == "sandbox"


def test_untrusted_artifact_headers_remain_embeddable_same_origin(index_html):
    headers = artifact_security_headers(index_html)

    assert headers["Content-Security-Policy"] == artifact_content_security_policy()
    assert headers["X-Frame-Options"] == "SAMEORIGIN"
    assert headers["X-Content-Type-Options"] == "nosniff"


def test_a_first_party_framed_document_keeps_the_shell_script_policy(index_html):
    """`/ketcher` is UI code, not Artifact bytes.

    It needs one thing the shell profile refuses -- being framed by the
    workbench -- and nothing else. Relaxing `script-src` or `connect-src` here
    would widen the policy for a first-party origin to solve a framing problem.
    """
    headers = embeddable_security_headers(index_html)
    policy = headers["Content-Security-Policy"]

    assert _directive(policy, "frame-ancestors") == "frame-ancestors 'self'"
    assert headers["X-Frame-Options"] == "SAMEORIGIN"
    assert _directive(policy, "script-src") == "script-src 'self' 'wasm-unsafe-eval'"
    assert "'unsafe-inline'" not in _directive(policy, "script-src")
    assert _directive(policy, "connect-src") == "connect-src 'self'"
    assert "sandbox" not in policy, "a first-party document keeps its own origin"


def test_the_shell_itself_is_still_unframeable(index_html):
    """The relaxation must be per-document, not a default that drifted."""
    assert _directive(content_security_policy(index_html), "frame-ancestors") == (
        "frame-ancestors 'none'"
    )
    assert security_headers(index_html)["X-Frame-Options"] == "DENY"


def test_html_preview_iframe_does_not_reenable_artifact_capabilities(index_html):
    app_js = index_html.with_name("app.js").read_text(encoding="utf-8")
    preview_line = next(
        line for line in app_js.splitlines() if 'rendererId === "html-preview"' in line
    )

    assert 'frame.setAttribute("sandbox", "")' in preview_line
    assert "allow-scripts" not in preview_line
    assert "allow-forms" not in preview_line


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
    """The static policy does not depend on an HTML file being readable."""
    policy = content_security_policy(tmp_path / "nope.html")
    assert "default-src 'self'" in policy
    assert "'unsafe-inline'" not in _directive(policy, "script-src")
