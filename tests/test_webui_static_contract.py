"""Offline contracts for the dependency-free gateway web UI.

These tests intentionally inspect the static sources instead of starting the
gateway or a browser.  They catch broken asset links and the most important
HTML/JavaScript integration seams while keeping the default test suite fully
offline and stdlib-only.
"""

from __future__ import annotations

import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "openai4s" / "server" / "webui"
INDEX_PATH = WEBUI / "index.html"
APP_PATH = WEBUI / "app.js"
STYLE_PATH = WEBUI / "style.css"

INDEX_HTML = INDEX_PATH.read_text(encoding="utf-8")
APP_JS = APP_PATH.read_text(encoding="utf-8")
STYLE_CSS = STYLE_PATH.read_text(encoding="utf-8")


class _WebUIShellParser(HTMLParser):
    """Collect the small part of the HTML surface these contracts need."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.classes: set[str] = set()
        self.data_icons: set[str] = set()
        self.static_assets: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value for name, value in attrs}
        if values.get("id"):
            self.ids.append(values["id"] or "")
        self.classes.update((values.get("class") or "").split())
        if values.get("data-icon"):
            self.data_icons.add(values["data-icon"] or "")
        for attr in ("href", "src"):
            value = values.get(attr) or ""
            if value.startswith("/static/"):
                self.static_assets.add(value.split("?", 1)[0].split("#", 1)[0])


SHELL = _WebUIShellParser()
SHELL.feed(INDEX_HTML)


def _extract_js_function(source: str, name: str) -> str:
    """Return a named classic JS function, balancing braces outside strings.

    The web UI deliberately has no build tool or JavaScript parser dependency.
    This tiny scanner is enough for its classic ``function name(...)`` forms
    and is more stable than stopping at the first nested closing brace.
    """

    match = re.search(
        rf"\b(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{",
        source,
    )
    assert match, f"app.js must define function {name}()"
    start = match.end() - 1
    depth = 0
    quote: str | None = None
    escaped = False
    line_comment = False
    block_comment = False
    index = start
    while index < len(source):
        char = source[index]
        nxt = source[index + 1] if index + 1 < len(source) else ""
        if line_comment:
            if char == "\n":
                line_comment = False
        elif block_comment:
            if char == "*" and nxt == "/":
                block_comment = False
                index += 1
        elif quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char == "/" and nxt == "/":
            line_comment = True
            index += 1
        elif char == "/" and nxt == "*":
            block_comment = True
            index += 1
        elif char in {'"', "'", "`"}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : index + 1]
        index += 1
    raise AssertionError(f"unterminated function {name}() in app.js")


def _icon_definitions() -> set[str]:
    match = re.search(
        r"\bconst\s+ICONS\s*=\s*\{(?P<body>.*?)^\};",
        APP_JS,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match, "app.js must define the ICONS object"
    return set(
        re.findall(r"^\s*['\"]([^'\"]+)['\"]\s*:", match.group("body"), re.MULTILINE)
    )


def test_every_referenced_static_asset_exists() -> None:
    # Include JavaScript-loaded first-party assets (for example vendored
    # 3Dmol), not only <link>/<script> elements in the HTML shell.
    refs = set(SHELL.static_assets)
    refs.update(re.findall(r"['\"`](/static/[A-Za-z0-9_./-]+)", APP_JS))
    assert refs, "the UI shell should load first-party static assets"

    missing: list[str] = []
    escaped: list[str] = []
    webui_root = WEBUI.resolve()
    for ref in sorted(refs):
        local = (WEBUI / ref.removeprefix("/static/")).resolve()
        if not local.is_relative_to(webui_root):
            escaped.append(ref)
        elif not local.is_file():
            missing.append(ref)
    assert not escaped, f"/static references must stay inside webui/: {escaped}"
    assert not missing, f"referenced /static assets do not exist: {missing}"


def test_critical_dom_ids_are_present_and_unique() -> None:
    critical = {
        "dashboard",
        "workspace",
        "dash-projects",
        "dash-sessions",
        "messages",
        "composer",
        "attach-btn",
        "session-options-btn",
        "plan-toggle",
        "explore-toggle",
        "rightdock",
        "dock-notebook",
        "cust",
        "proj-modal",
        "pm-create",
        "pm-delete",
    }
    ids = set(SHELL.ids)
    assert critical <= ids, f"missing critical web UI ids: {sorted(critical - ids)}"

    duplicates = sorted(name for name, count in Counter(SHELL.ids).items() if count > 1)
    assert not duplicates, (
        f"duplicate DOM ids make selector wiring ambiguous: {duplicates}"
    )


def test_shell_keeps_minimal_controls() -> None:
    ids = set(SHELL.ids)
    absent = {"conn-dot", "send-btn", "pm-template-grid"}
    assert not (absent & ids), (
        "the shell should not grow separate connection/send/template "
        f"controls: {sorted(absent & ids)}"
    )


def test_project_modal_reuses_create_button_for_create_and_patch() -> None:
    expected = {"pm-name", "pm-desc", "pm-ctx", "pm-create", "pm-delete"}
    ids = set(SHELL.ids)
    assert expected <= ids, f"missing project modal fields: {sorted(expected - ids)}"

    open_source = _extract_js_function(APP_JS, "openProjectModal")
    submit_source = _extract_js_function(APP_JS, "submitProjectModal")
    assert '$("#pm-create")' in open_source
    assert "S.editingProject" in open_source
    assert '$("#pm-name")' in open_source
    assert '$("#pm-desc")' in open_source
    assert '$("#pm-ctx")' in open_source
    assert '$("#pm-delete")' in open_source
    assert '.classList.toggle("hidden", !p)' in open_source
    assert '$("#pm-create")' in submit_source
    assert "S.editingProject" in submit_source
    assert re.search(r"/projects/\$\{S\.editingProject\}", submit_source)
    assert '$("#pm-delete").onclick' in APP_JS
    assert "await deleteProject(id)" in APP_JS
    assert re.search(r"method\s*:\s*['\"]PATCH['\"]", submit_source)


def test_add_to_message_and_session_options_are_wired() -> None:
    add_source = _extract_js_function(APP_JS, "addToMessageMenu")
    for key in (
        "composer.menu.attachFiles",
        "composer.menu.yourFiles",
        "composer.menu.requestReview",
        "composer.menu.saveAsSkill",
        "composer.menu.contextUsage",
    ):
        assert key in add_source

    options_source = _extract_js_function(APP_JS, "sessionOptionsMenu")
    for key in (
        "composer.option.delegation",
        "composer.option.autoReview",
        "composer.option.reviewerModel",
        "composer.option.memory",
        "composer.option.specialist",
        "composer.option.compute",
    ):
        assert key in options_source
    assert "/review-settings" in options_source
    assert re.search(r"method\s*:\s*['\"]PATCH['\"]", options_source)


def test_all_literal_icon_names_have_svg_definitions() -> None:
    used = set(SHELL.data_icons)
    # iconEl() is the DOM-returning companion to icon(); both ultimately read
    # ICONS and therefore have the same missing-definition failure mode.
    used.update(re.findall(r"\bicon(?:El)?\(\s*['\"]([^'\"]+)['\"]", APP_JS))
    used.update(re.findall(r"data-icon\s*=\s*['\"]([^'\"]+)['\"]", APP_JS))
    definitions = _icon_definitions()
    missing = sorted(used - definitions)
    assert not missing, f"literal icon names missing from ICONS: {missing}"


def test_frontend_uses_backend_error_envelope() -> None:
    api_source = APP_JS[APP_JS.index("const api =") : APP_JS.index("const S =")]
    assert re.search(r"\bj\s*(?:\?\.|\.)\s*error\b", api_source), (
        "api() must surface the backend's {error: ...} message"
    )


def test_send_starts_an_async_background_turn() -> None:
    send_source = _extract_js_function(APP_JS, "send")
    assert re.search(r"/message['\"`]", send_source), (
        "send() must post to the frame message endpoint"
    )
    assert re.search(r"(?:\bwait\b|['\"]wait['\"])\s*:\s*false\b", send_source), (
        "the browser must send wait:false so the POST returns while the turn streams"
    )
    assert not re.search(r"\bturnDone\(\s*['\"]completed['\"]", send_source), (
        "a 202 acknowledgement is not completion; wait for the terminal WS event"
    )


def test_review_is_a_streamed_step_with_manual_and_session_controls() -> None:
    body_source = _extract_js_function(APP_JS, "stepBody")
    state_source = _extract_js_function(APP_JS, "applyStepState")
    manual_source = _extract_js_function(APP_JS, "requestReview")

    assert re.search(r"k\s*===\s*['\"]review['\"]", body_source)
    assert "out.issues" in body_source and "out.verdict" in body_source
    assert "Reviewing" in state_source
    assert "review-pass" in state_source and "review-issues" in state_source
    assert re.search(r"/frames/\$\{S\.currentId\}/review", manual_source)
    assert re.search(r"method\s*:\s*['\"]POST['\"]", manual_source)
    assert '$("#cancel-btn").classList.remove("hidden")' in manual_source
    assert 'turnDone("failed")' in manual_source


def test_context_menus_remain_scrollable_inside_the_viewport() -> None:
    rule = re.search(r"\.ctx-menu\s*\{(?P<body>[^}]+)\}", STYLE_CSS)
    assert rule, "style.css must define .ctx-menu"
    body = rule.group("body")
    assert "max-height:" in body and "100vh" in body
    assert re.search(r"overflow-y\s*:\s*auto", body)


def test_notebook_live_state_and_outputs_follow_the_ui_contract() -> None:
    notebook_source = _extract_js_function(APP_JS, "renderNotebook")
    output_source = _extract_js_function(APP_JS, "notebookOutputBlock")

    assert ".alive" in notebook_source
    assert "Live" in notebook_source
    assert "refreshKernelState" in notebook_source
    assert re.search(r"el\(\s*['\"]details['\"]", output_source)
    assert re.search(r"el\(\s*['\"]summary['\"]", output_source)
    assert "output" in output_source


def test_session_and_project_menus_download_artifact_zip() -> None:
    project_source = _extract_js_function(APP_JS, "renderProjMenu")
    session_source = _extract_js_function(APP_JS, "sessionMenu")
    assert re.search(r"/projects/\$\{[^}]+\}/artifacts\.zip", project_source)
    assert re.search(r"/frames/\$\{[^}]+\}/artifacts\.zip", session_source)
