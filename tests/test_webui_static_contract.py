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
        "dock-timeline",
        "cust",
        "proj-modal",
        "pm-create",
        "pm-delete",
    }
    ids = set(SHELL.ids)
    assert critical <= ids, f"missing critical web UI ids: {sorted(critical - ids)}"

    duplicates = sorted(name for name, count in Counter(SHELL.ids).items() if count > 1)
    assert (
        not duplicates
    ), f"duplicate DOM ids make selector wiring ambiguous: {duplicates}"


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
    assert re.search(
        r"\bj\s*(?:\?\.|\.)\s*error\b", api_source
    ), "api() must surface the backend's {error: ...} message"


def test_send_starts_an_async_background_turn() -> None:
    send_source = _extract_js_function(APP_JS, "send")
    assert re.search(
        r"/message['\"`]", send_source
    ), "send() must post to the frame message endpoint"
    assert re.search(
        r"(?:\bwait\b|['\"]wait['\"])\s*:\s*false\b", send_source
    ), "the browser must send wait:false so the POST returns while the turn streams"
    assert not re.search(
        r"\bturnDone\(\s*['\"]completed['\"]", send_source
    ), "a 202 acknowledgement is not completion; wait for the terminal WS event"


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
    event_source = _extract_js_function(APP_JS, "onEvent")
    load_source = _extract_js_function(APP_JS, "loadExecutionLog")
    start_source = _extract_js_function(APP_JS, "nbCellStart")
    chunk_source = _extract_js_function(APP_JS, "nbCellChunk")
    finished_source = _extract_js_function(APP_JS, "nbCellFinished")
    feed_source = _extract_js_function(APP_JS, "feed")
    status_source = _extract_js_function(APP_JS, "_paintStatusStrip")

    assert ".alive" in notebook_source
    assert "Live" in notebook_source
    assert "refreshKernelState" in notebook_source
    assert re.search(r"el\(\s*['\"]details['\"]", output_source)
    assert re.search(r"el\(\s*['\"]summary['\"]", output_source)
    assert "output" in output_source
    for event_type in (
        "notebook_cell_start",
        "notebook_cell_chunk",
        "notebook_cell_finished",
    ):
        assert event_type in event_source
    assert "producing_cell_id" in start_source
    assert "event.origin" in start_source
    assert "mergeNotebookCells" in start_source
    assert "producing_cell_id" in chunk_source
    assert "producing_cell_id" in finished_source
    assert "S.liveCells" in finished_source and "S.cells" in finished_source
    assert "event.producing_cell_id" in feed_source
    assert "S._executionLoadReq" in load_source
    assert "request !== S._executionLoadReq" in load_source
    assert "mergeNotebookCells" in load_source
    assert 't("nb.status.ready"' in status_source
    assert "st.turn_running" in status_source
    assert ".alive" in status_source


def test_notebook_retry_projection_is_expandable_and_keeps_raw_attempts() -> None:
    projection = _extract_js_function(APP_JS, "projectNotebookCells")
    cell_source = _extract_js_function(APP_JS, "cellNode")

    assert "attempt_group_id" in projection
    assert 'previous.origin === "agent"' in projection
    assert "_revisions" in projection
    assert "attempts.slice(0, -1)" in projection
    assert 'el("details", "nbc-revisions")' in cell_source
    assert "revisions.forEach" in cell_source
    assert ".nbc-revisions" in STYLE_CSS


def test_action_timeline_is_a_safe_allowlisted_projection() -> None:
    sanitizer = _extract_js_function(APP_JS, "sanitizeActionTimeline")
    card = _extract_js_function(APP_JS, "actionTimelineCard")
    renderer = _extract_js_function(APP_JS, "renderActionTimeline")
    events = _extract_js_function(APP_JS, "onEvent")

    assert "dock-timeline" in INDEX_HTML
    assert "action_timeline" in events and "action-timeline" in events
    assert ".slice(-500)" in sanitizer
    for kind in (
        "native_tool",
        "python",
        "r",
        "delegate",
        "permission",
        "recovery",
        "finalize",
    ):
        assert f'timeline.kind.{kind}' in APP_JS
    # Raw provider/audit payloads may be inspected only while deriving a tiny
    # Artifact-name allowlist; the stored group/event projection must not copy
    # these fields and the card must never render them.
    assert "arguments:" not in sanitizer
    assert "wire_id:" not in sanitizer
    assert "tool_call_id:" not in sanitizer
    assert "assistant_content:" not in sanitizer
    for forbidden in ("arguments", "wire_id", "tool_call_id", "assistant_content"):
        assert forbidden not in card
        assert forbidden not in renderer
    assert "textContent" not in card or "innerHTML" not in card
    assert ".timeline-card" in STYLE_CSS


def test_notebook_live_input_appends_cells_and_keeps_history_read_only() -> None:
    notebook = _extract_js_function(APP_JS, "renderNotebook")
    export = _extract_js_function(APP_JS, "notebookExportLink")
    provenance = _extract_js_function(APP_JS, "renderProvenanceInto")
    execute = _extract_js_function(APP_JS, "executeNotebookCode")
    cell = _extract_js_function(APP_JS, "cellNode")
    identity = _extract_js_function(APP_JS, "nbEventCellId")
    _extract_js_function(APP_JS, "nbCellStart")
    chunk = _extract_js_function(APP_JS, "nbCellChunk")
    finished = _extract_js_function(APP_JS, "nbCellFinished")

    assert 'el("textarea", "nb-repl-input")' in notebook
    assert "notebookExportLink(S.currentId)" in notebook
    assert "notebookExportLink(S.currentId)" in provenance
    assert 't("prov.exec.downloadNotebook")' in export
    assert "/notebook/export?language=bundle" in export
    assert ".notebooks.zip" in export
    assert 'download", "notebook.json"' not in APP_JS
    assert '[["python", "Python"], ["r", "R"]]' in notebook
    assert 'event.key === "Enter" && event.shiftKey' in notebook
    assert "JSON.stringify({ code, language, execution_id: executionId })" in execute
    assert 'owner: { kind: "user_repl", id: executionId }' in execute
    assert "/kernel/execute" in execute
    assert "nb.action.rerun" in cell
    assert "nb.action.copy" in cell
    assert "nb.action.fork" in cell
    assert "nb.action.promote" in cell
    assert "_historicalRevision" in cell
    assert "event.cell_id" in identity and "event.producing_cell_id" in identity
    for source in (chunk, finished):
        assert "event.cell_id" in source and "event.producing_cell_id" in source
    assert "_seenChunks" in chunk
    assert ".nb-repl-input" in STYLE_CSS
    assert ".nbc-actions" in STYLE_CSS


def test_execution_interrupts_send_the_exact_cached_identity() -> None:
    queue = _extract_js_function(APP_JS, "rememberExecutionQueue")
    state = _extract_js_function(APP_JS, "rememberExecutionState")
    exact = _extract_js_function(APP_JS, "exactExecutionIdentity")
    owner = _extract_js_function(APP_JS, "identityForOwner")
    scoped = _extract_js_function(APP_JS, "scopedExecutionRequest")
    cancel = _extract_js_function(APP_JS, "cancelTurn")
    notebook = _extract_js_function(APP_JS, "renderNotebook")

    assert "execution_id" in queue and "owner.kind" in queue and "owner.id" in queue
    assert "execution_id" in state and "owner.kind" in state and "owner.id" in state
    assert "execution_id: identity.execution_id" in scoped
    assert "owner: identity.owner" in scoped
    assert "owner_id: identity.owner.id" in scoped
    assert 'scopedExecutionRequest(S.currentId, "cancel"' in cancel
    assert 'scopedExecutionRequest(S.currentId, "kernel/interrupt"' in notebook
    assert '"user_repl"' in notebook
    assert 'identityForOwner(S.executionQueue, "user_repl")' in notebook
    assert '"repl-stop" + (replBusy ? "" : " hidden")' in notebook
    assert "inp.disabled = !S.currentId || replBusy" in notebook
    assert "ownerKind" in exact and "pendingReplIdentity" in exact
    assert 'ownerKind === "user_repl"' in exact
    assert "owner.kind === ownerKind" in owner


def test_branch_context_and_security_controls_fail_closed_when_absent() -> None:
    sanitizer = _extract_js_function(APP_JS, "sanitizeBranches")
    branches = _extract_js_function(APP_JS, "renderBranchPanel")
    context = _extract_js_function(APP_JS, "renderContextPanel")
    security = _extract_js_function(APP_JS, "renderSecurityPanel")
    button = _extract_js_function(APP_JS, "disabledWorkbenchButton")

    assert "branchCapability" in branches
    assert "value.enabled === true" in sanitizer
    assert "fork_from_cell" in sanitizer
    assert 'branchCapability("fork_from_cell")' in APP_JS
    assert "revert_preview" in branches
    assert "button.disabled = !enabled" in button
    assert "nb.action.unavailable" in button
    assert "token_count" in context and "layer" in context
    assert "sandbox" in security and "permission" in security
    assert "self_test_passed" in security and "network_policy" in security
    assert "generation_ended" in security and "generationEnded" in security


def test_provenance_caches_follow_artifact_versions_and_refresh_mutations() -> None:
    key_source = _extract_js_function(APP_JS, "artifactCacheKey")
    sync_source = _extract_js_function(APP_JS, "syncArtifactVersion")
    event_source = _extract_js_function(APP_JS, "onEvent")
    artifacts_source = _extract_js_function(APP_JS, "loadArtifacts")
    execution_source = _extract_js_function(APP_JS, "loadExecutionLog")
    show_source = _extract_js_function(APP_JS, "showProvenance")
    render_source = _extract_js_function(APP_JS, "renderProvenanceInto")
    environment_source = _extract_js_function(APP_JS, "renderProvEnvironment")
    editor_source = _extract_js_function(APP_JS, "renderArtifactEditor")
    versions_source = _extract_js_function(APP_JS, "showVersions")
    review_source = _extract_js_function(APP_JS, "renderProvReview")

    assert "a.id" in key_source
    assert "a.version_id" in key_source
    assert "a.latest_version_id" in key_source
    assert "S._artVer" in key_source
    assert "S.openTabs" in sync_source and "S.dockArtifact" in sync_source
    assert "S.lineage = null" in sync_source and "S._lineageFor = null" in sync_source
    assert "S._lineageReq" in sync_source
    assert "syncArtifactVersion(art, true)" in event_source
    assert "syncArtifactVersion(x, false)" in artifacts_source
    assert "S._artifactLoadReq" in artifacts_source
    assert "request !== S._artifactLoadReq" in artifacts_source
    assert "showProvenance(S.dockArtifact)" in execution_source
    assert "S._lineageReq" in execution_source
    assert "artifactCacheKey(a)" in show_source
    assert "artifactCacheKey(S.dockArtifact)" in show_source
    assert "request !== S._lineageReq" in show_source
    assert "artifactCacheKey(a)" in render_source
    assert "artifactCacheKey(a)" in environment_source
    assert "artifactCacheKey(S.dockArtifact)" in environment_source
    assert "syncArtifactVersion({ id: a.id" in editor_source
    assert "syncArtifactVersion((restored && restored.artifact)" in versions_source
    assert "Array.isArray(mapped)" in review_source
    assert "cell.files_read && cell.files_read.length" not in review_source


def test_session_and_project_menus_download_artifact_zip() -> None:
    project_source = _extract_js_function(APP_JS, "renderProjMenu")
    session_source = _extract_js_function(APP_JS, "sessionMenu")
    assert re.search(r"/projects/\$\{[^}]+\}/artifacts\.zip", project_source)
    assert re.search(r"/frames/\$\{[^}]+\}/artifacts\.zip", session_source)
