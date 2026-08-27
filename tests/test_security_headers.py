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


def test_script_src_never_allows_unsafe_inline():
    """The load-bearing assertion: executable code lives in static files."""
    script_src = _directive(content_security_policy(), "script-src")
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


def test_policy_never_parses_html_to_authorize_scripts():
    """Escaped script tokenizer states cannot influence a static policy.

    This used to write adversarial HTML and compare two policies. Now the
    builder takes no path at all, so the property is structural: assert the
    signature rather than a value the function could not have varied anyway.
    """
    import inspect

    parameters = inspect.signature(content_security_policy).parameters
    assert [
        name
        for name, p in parameters.items()
        if p.kind is not inspect.Parameter.KEYWORD_ONLY
    ] == []
    assert set(parameters) == {"frame_ancestors", "frame_src"}


def test_untrusted_artifact_policy_cannot_execute_or_reach_same_origin():
    policy = artifact_content_security_policy()

    assert _directive(policy, "default-src") == "default-src 'none'"
    assert _directive(policy, "script-src") == "script-src 'none'"
    assert _directive(policy, "connect-src") == "connect-src 'none'"
    assert _directive(policy, "form-action") == "form-action 'none'"
    assert _directive(policy, "frame-ancestors") == "frame-ancestors 'self'"


def test_the_artifact_sandbox_grants_an_origin_and_never_scripts():
    """`allow-same-origin` alone, and it is load-bearing in both directions.

    Without it the document has an opaque origin, where `'self'` matches
    nothing: a report's own `<img src="figure.png">` fails to load even on a
    top-level View, which is the pair `store.artifact_by_unique_filename`
    exists to resolve. With `allow-scripts` added it would become an active
    document on the origin that holds the session cookie. Exactly one token.
    """
    policy = artifact_content_security_policy()

    assert _directive(policy, "sandbox") == "sandbox allow-same-origin"
    assert "allow-scripts" not in policy
    assert _directive(policy, "script-src") == "script-src 'none'"


def test_an_artifact_can_load_its_own_sibling_files():
    """The fetch directives the sandboxed origin makes reachable again."""
    policy = artifact_content_security_policy()

    assert _directive(policy, "img-src") == "img-src 'self' data: blob:"
    assert _directive(policy, "style-src") == "style-src 'self' 'unsafe-inline'"
    assert _directive(policy, "media-src") == "media-src 'self' data: blob:"
    assert _directive(policy, "font-src") == "font-src 'self' data:"
    # Reaching *out* stays closed: 'self' is for the artifact's own bytes.
    assert _directive(policy, "connect-src") == "connect-src 'none'"


def test_untrusted_artifact_headers_remain_embeddable_same_origin():
    headers = artifact_security_headers()

    assert headers["Content-Security-Policy"] == artifact_content_security_policy()
    assert headers["X-Frame-Options"] == "SAMEORIGIN"
    assert headers["X-Content-Type-Options"] == "nosniff"


def test_a_first_party_framed_document_keeps_the_shell_script_policy():
    """`/ketcher` is UI code, not Artifact bytes.

    It needs one thing the shell profile refuses -- being framed by the
    workbench -- and nothing else. Relaxing `script-src` or `connect-src` here
    would widen the policy for a first-party origin to solve a framing problem.
    """
    headers = embeddable_security_headers()
    policy = headers["Content-Security-Policy"]

    assert _directive(policy, "frame-ancestors") == "frame-ancestors 'self'"
    assert headers["X-Frame-Options"] == "SAMEORIGIN"
    assert _directive(policy, "script-src") == "script-src 'self' 'wasm-unsafe-eval'"
    assert "'unsafe-inline'" not in _directive(policy, "script-src")
    assert _directive(policy, "connect-src") == "connect-src 'self'"
    assert "sandbox" not in policy, "a first-party document keeps its own origin"


def test_the_shell_itself_is_still_unframeable():
    """The relaxation must be per-document, not a default that drifted."""
    assert _directive(content_security_policy(), "frame-ancestors") == (
        "frame-ancestors 'none'"
    )
    assert security_headers()["X-Frame-Options"] == "DENY"


def _html_preview_renderer(app_js: str) -> str:
    """The body of `renderHtmlPreview`, where the whole decision lives."""
    start = app_js.index("function renderHtmlPreview(")
    end = app_js.index("function renderArtifactDescriptor(", start)
    return app_js[start:end]


def test_the_html_preview_starts_inert_and_says_why(index_html):
    """The safe state is the *initial* state, not a state it falls back to.

    A grant can be unavailable (no signing secret), refused, or simply slow.
    Building the frame inert and upgrading only on success means every one of
    those paths ends somewhere safe without a single error branch having to
    remember to. While it is inert it says so, because an interactive
    dashboard that renders its chrome and never draws is indistinguishable
    from a corrupt artifact.
    """
    app_js = index_html.with_name("app.js").read_text(encoding="utf-8")
    body = _html_preview_renderer(app_js)

    inert = body.index('frame.setAttribute("sandbox", "")')
    upgrade = body.index('frame.setAttribute("sandbox", "allow-scripts')
    assert inert < upgrade, "the frame must be built inert before any upgrade"
    assert 't("viewer.renderer.noscript")' in body
    for language_marker in (
        '"viewer.renderer.noscript": "预览不执行脚本',
        '"viewer.renderer.noscript": "This preview runs no scripts',
    ):
        assert language_marker in app_js, "the note must exist in both languages"


def test_scripts_are_only_ever_enabled_against_the_sandbox_origin(index_html):
    """The load-bearing assertion of the whole sandbox-origin design.

    `allow-scripts` on a *same-origin* frame would hand model-authored HTML
    the session cookie and `parent.document`. It is admissible only because
    the document is served from another origin, so the two must be
    inseparable: the upgrade sits behind the `S.sandboxOrigin` guard and sets
    the src from it.
    """
    app_js = index_html.with_name("app.js").read_text(encoding="utf-8")
    body = _html_preview_renderer(app_js)

    guard = body.index("if (!S.sandboxOrigin) return;")
    upgrade = body.index('frame.setAttribute("sandbox", "allow-scripts')
    src = body.index("frame.src = S.sandboxOrigin + path;")
    assert guard < upgrade < src

    # And nowhere else in the client.
    enabling = [
        line
        for line in app_js.splitlines()
        if "allow-scripts" in line and "sandbox" in line
    ]
    assert len(enabling) == 1, f"allow-scripts appears outside the upgrade: {enabling}"


def test_the_sandbox_origin_is_a_different_loopback_name(index_html):
    """Distinctness is the security property; loopback is the safety bound."""
    app_js = index_html.with_name("app.js").read_text(encoding="utf-8")
    start = app_js.index("function defaultSandboxOrigin()")
    body = app_js[start : app_js.index("const api = async", start)]

    assert '"127.0.0.1": "localhost"' in body and '"localhost": "127.0.0.1"' in body
    # Anything else gets "" and the inert preview rather than an origin we
    # have not verified.
    assert 'if (!other || location.protocol !== "http:") return "";' in body


def test_connect_src_is_same_origin_only():
    """The exfiltration bound: an injected script must not be able to POST
    harvested data anywhere. Verified in-browser — a cross-origin fetch and a
    cross-origin WebSocket both raise connect-src violations."""
    assert _directive(content_security_policy(), "connect-src") == (
        "connect-src 'self'"
    )


def test_wasm_is_permitted_without_reopening_eval(index_html):
    """3Dmol compiles WebAssembly for molecular surfaces. 'wasm-unsafe-eval'
    covers that alone; 'unsafe-eval' would also hand eval() back to injected
    script."""
    script_src = _directive(content_security_policy(), "script-src")
    assert "'wasm-unsafe-eval'" in script_src


def test_dangerous_sinks_are_closed(index_html):
    policy = content_security_policy()
    assert _directive(policy, "object-src") == "object-src 'none'"
    assert _directive(policy, "base-uri") == "base-uri 'none'"
    assert _directive(policy, "frame-ancestors") == "frame-ancestors 'none'"


def test_default_src_is_self(index_html):
    assert _directive(content_security_policy(), "default-src") == (
        "default-src 'self'"
    )


def test_style_src_inline_is_a_deliberate_concession(index_html):
    """The UI sets style="" through innerHTML in a couple of places. Style
    injection cannot execute script, so this stays permitted — the point is
    that the concession is here and not in script-src."""
    style_src = _directive(content_security_policy(), "style-src")
    assert "'unsafe-inline'" in style_src


def test_all_expected_headers_present():
    h = security_headers()
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert h["Referrer-Policy"] == "same-origin"
    assert "Content-Security-Policy" in h
    assert "Permissions-Policy" in h


def test_the_policy_is_a_constant():
    """The static policy does not depend on any file being readable."""
    policy = content_security_policy()
    assert policy == content_security_policy()
    assert "default-src 'self'" in policy
    assert "'unsafe-inline'" not in _directive(policy, "script-src")
