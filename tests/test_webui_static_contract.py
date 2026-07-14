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
SCIENTIFIC_RENDERERS_PATH = WEBUI / "scientific_renderers.js"

INDEX_HTML = INDEX_PATH.read_text(encoding="utf-8")
APP_JS = APP_PATH.read_text(encoding="utf-8")
STYLE_CSS = STYLE_PATH.read_text(encoding="utf-8")
SCIENTIFIC_RENDERERS_JS = SCIENTIFIC_RENDERERS_PATH.read_text(encoding="utf-8")


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


def test_artifact_viewer_consumes_safe_renderer_descriptors() -> None:
    renderer_source = _extract_js_function(APP_JS, "artifactRendererDescriptor")
    body_source = _extract_js_function(APP_JS, "renderArtifactBody")
    dispatch_source = _extract_js_function(APP_JS, "renderArtifactDescriptor")

    assert INDEX_HTML.index("/static/scientific_renderers.js") < INDEX_HTML.index(
        "/static/app.js"
    )
    assert 'api("/renderers")' in APP_JS
    assert "/renderer${suffix}" in renderer_source
    assert "rendererIdFromDescriptor" in renderer_source
    assert "artifactRendererDescriptor(a)" in body_source
    for renderer_id in (
        "molecule-3d",
        "chemistry-2d",
        "genome-track",
        "sequence",
        "msa",
        "latex",
    ):
        assert f'rendererId === "{renderer_id}"' in dispatch_source
    for parser in (
        "parseAlignment",
        "parseGenome",
        "parseMolfile",
        "parseSequence",
        "latexPreview",
    ):
        assert f"function {parser}" in SCIENTIFIC_RENDERERS_JS
    assert ".renderer-shell" in STYLE_CSS
    assert ".genome-tracks" in STYLE_CSS
    assert ".chemistry-view" in STYLE_CSS
    assert ".latex-preview" in STYLE_CSS


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


def test_streaming_markdown_seals_only_complete_blocks_and_fully_renders_on_finish() -> (
    None
):
    stable_cut = APP_JS[
        APP_JS.index("function _mdStableCut") : APP_JS.index("function flushRender")
    ]
    flush = _extract_js_function(APP_JS, "flushRender")
    seal = _extract_js_function(APP_JS, "sealText")
    done = _extract_js_function(APP_JS, "turnDone")

    assert "openFence" in stable_cut
    assert "if (openFence)" in stable_cut
    assert "if (closes)" in stable_cut
    assert "else if (!line.trim()" in stable_cut
    assert "if (finalRender)" in flush
    assert "renderMd(text)" in flush
    assert "flushRender(st, true)" in seal
    assert "flushRender(S.stream, true)" in done


def test_promoted_markdown_allows_only_safe_raster_data_images() -> None:
    inline = _extract_js_function(APP_JS, "mdInline")

    assert "data:image\\/(?:png|jpeg|gif|webp);base64" in inline
    assert "svg" not in inline


def test_session_list_replaces_empty_state_and_keeps_nested_menus_keyboard_safe() -> (
    None
):
    row = _extract_js_function(APP_JS, "sessionRow")
    sessions = _extract_js_function(APP_JS, "renderSessions")

    assert sessions.index('list.innerHTML = ""') < sessions.index("if (!ss.length")
    assert "e.target === d" in row
    assert "e.target === head" in sessions


def test_restart_approval_is_explicitly_continued_instead_of_auto_replayed() -> None:
    render_source = _extract_js_function(APP_JS, "renderPermissionCard")
    mark_source = _extract_js_function(APP_JS, "markPermCard")
    assert "resolution.ok !== true" in render_source
    assert 'resolution_context === "after_restart"' in mark_source
    assert "requires_continue === true" in mark_source
    assert 'send(t("perm.continuePrompt"))' in mark_source
    assert "perm.status.afterRestartAllowed" in mark_source
    assert "perm.status.afterRestartDenied" in mark_source
    assert ".perm-continue" in STYLE_CSS


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
    draft_source = _extract_js_function(APP_JS, "nbCellDraft")
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
        "notebook_cell_draft",
        "notebook_cell_start",
        "notebook_cell_chunk",
        "notebook_cell_finished",
    ):
        assert event_type in event_source
    assert "producing_cell_id" in start_source
    assert "event.draft_id" in draft_source
    assert "event.revision" in draft_source
    assert 'event.status === "discarded"' in draft_source
    assert "event.source.slice(0, 200000)" in draft_source
    assert "mergeNotebookCells" in draft_source
    assert "candidate.draft" in start_source
    assert "event.origin" in start_source
    assert "event.generation_id" in start_source
    assert "event.state_revision" in start_source
    assert "mergeNotebookCells" in start_source
    assert "previous.live === true" in start_source
    assert "inheritLiveOutput" in start_source
    assert "producing_cell_id" in chunk_source
    assert "appendLiveOutput" in chunk_source
    assert "producing_cell_id" in finished_source
    assert "...event" in finished_source
    assert "S.liveCells" in finished_source and "S.cells" in finished_source
    assert "event.producing_cell_id" in feed_source
    assert "appendLiveOutput" in feed_source
    assert "LIVE_OUTPUT_CHAR_CAP = 1000000" in APP_JS
    assert "S._executionLoadReq" in load_source
    assert "request !== S._executionLoadReq" in load_source
    assert "mergeNotebookCells" in load_source
    assert 't("nb.status.ready"' in status_source
    assert "st.turn_running" in status_source
    assert ".alive" in status_source
    state_source = _extract_js_function(APP_JS, "notebookCellState")
    assert "cell.draft" in state_source
    assert "cell.stale === true" in state_source
    assert "cell.stale_reasons" in state_source
    assert "revision < current" not in state_source
    cell_source = _extract_js_function(APP_JS, "cellNode")
    assert "e.draft" in cell_source
    assert "if (!e.draft)" in cell_source
    assert ".notebook-cell.draft" in STYLE_CSS
    assert ".nbc-state.drafting" in STYLE_CSS


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
    merger = _extract_js_function(APP_JS, "mergeActionTimelines")
    earlier = _extract_js_function(APP_JS, "loadEarlierActionTimeline")
    loader = _extract_js_function(APP_JS, "loadWorkbenchState")
    card = _extract_js_function(APP_JS, "actionTimelineCard")
    renderer = _extract_js_function(APP_JS, "renderActionTimeline")
    events = _extract_js_function(APP_JS, "onEvent")

    assert "dock-timeline" in INDEX_HTML
    assert "action_timeline" in events and "action-timeline" in events
    assert "ACTION_TIMELINE_PAGE_SIZE = 500" in APP_JS
    assert "ACTION_TIMELINE_MAX_GROUPS = 2000" in APP_JS
    assert ".slice(-ACTION_TIMELINE_PAGE_SIZE)" in sanitizer
    for field in ("first_ordinal", "last_ordinal", "has_more_before", "has_more_after"):
        assert field in sanitizer
    assert "new Map()" in merger
    assert "deduped.set(key(group), group)" in merger
    assert "(incoming.groups || []).concat(current.groups || [])" in merger
    assert "(current.groups || []).concat(incoming.groups || [])" in merger
    assert "all.slice(-ACTION_TIMELINE_MAX_GROUPS)" in merger
    assert 'direction === "before"' in merger
    assert "currentFirst <= incomingFirst" in merger
    assert "first_ordinal: groups.length ? groups[0].ordinal : null" in merger
    assert (
        'mergeActionTimelines(S.actionTimeline, sanitizeActionTimeline(timeline), "latest")'
        in loader
    )
    assert (
        'mergeActionTimelines(S.actionTimeline, sanitizeActionTimeline(m), "latest")'
        in events
    )
    assert "before_ordinal=${first}&limit=${ACTION_TIMELINE_PAGE_SIZE}" in earlier
    assert (
        'mergeActionTimelines(S.actionTimeline, sanitizeActionTimeline(page), "before")'
        in earlier
    )
    assert "if (timeline.has_more_before)" in renderer
    assert 'data-action", "load-earlier-timeline"' in renderer
    assert 't(loading ? "timeline.loadingEarlier" : "timeline.loadEarlier")' in renderer
    assert "workbenchErrors.timelineHistory" in renderer
    assert APP_JS.count('"timeline.loadEarlier"') >= 2
    for kind in (
        "native_tool",
        "python",
        "r",
        "delegate",
        "permission",
        "recovery",
        "finalize",
    ):
        assert f"timeline.kind.{kind}" in APP_JS
    # Raw provider/audit payloads may be inspected only while deriving a tiny
    # Artifact-name allowlist; the stored group/event projection must not copy
    # these fields and the card must never render them.
    assert "arguments:" not in sanitizer
    assert "wire_id:" not in sanitizer
    assert "tool_call_id:" not in sanitizer
    assert "assistant_content:" not in sanitizer
    assert "input_tokens" in sanitizer and "output_tokens" in sanitizer
    assert 'timelineMeta(t("timeline.cost"), timelineCost(group.cost))' in card
    assert 'timelineMeta(t("timeline.tokens")' in card
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
    assert 'response.status === "accepted"' in execute
    assert "if (!accepted" in execute
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
    assert "S.pendingReplIdentity = null" in state
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
    context_sanitizer = _extract_js_function(APP_JS, "sanitizeContext")
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
    assert "compaction_history" in context_sanitizer
    assert "artifact_count" in context_sanitizer
    assert "context-history" in context
    assert "sandbox" in security and "permission" in security
    assert "self_test_passed" in security and "network_policy" in security
    assert "generation_ended" in security and "generationEnded" in security


def test_project_global_timeline_and_lineage_are_safe_visible_views() -> None:
    timeline = _extract_js_function(APP_JS, "sanitizeActionTimeline")
    lineage = _extract_js_function(APP_JS, "sanitizeProjectLineage")
    viewer = _extract_js_function(APP_JS, "openProjectResearchView")
    menu = _extract_js_function(APP_JS, "renderProjMenu")

    assert "group.session.root_frame_id" in timeline
    assert "group.session.name" in timeline
    assert "new Set(nodes.map" in lineage
    assert "ids.has(item.from) && ids.has(item.to)" in lineage
    assert "/action-timeline?limit=500" in viewer
    assert "/lineage?limit=2000" in viewer
    assert "actionTimelineCard(group)" in viewer
    assert "projectResearch.menu" in menu
    assert ".project-research-tabs" in STYLE_CSS
    assert ".project-lineage-node" in STYLE_CSS


def test_session_package_export_import_is_visible_and_binary_safe() -> None:
    importer = _extract_js_function(APP_JS, "importSessionPackage")
    exporter = _extract_js_function(APP_JS, "exportSessionPackage")

    assert 'id="dash-import-session"' in INDEX_HTML
    assert 'id="session-package-input"' in INDEX_HTML
    assert "application/vnd.openai4s.session+zip" in INDEX_HTML
    assert 'fetch("/api/sessions/import"' in importer
    assert '"Content-Type": "application/vnd.openai4s.session+zip"' in importer
    assert "body: file" in importer
    assert "128 * 1024 * 1024" in importer
    assert "result.root_frame_id" in importer and "result.project_id" in importer
    assert "/session/export" in exporter
    assert ".openai4s-session.zip" in exporter
    assert APP_JS.count('"sessionPackage.import"') >= 2


def test_delegation_tree_uses_a_bounded_safe_workbench_projection() -> None:
    sanitizer = _extract_js_function(APP_JS, "sanitizeDelegations")
    loader = _extract_js_function(APP_JS, "loadWorkbenchState")
    renderer = _extract_js_function(APP_JS, "renderDelegationPanel")
    events = _extract_js_function(APP_JS, "onEvent")

    assert ".slice(0, 1000)" in sanitizer
    assert "parent_child_id" in sanitizer and "turn_boundary" in sanitizer
    assert "permission_count" in sanitizer and "capability_count" in sanitizer
    for forbidden in ("result:", "output:", "text_preview", "messages:"):
        assert forbidden not in sanitizer
    assert 'optionalApi([base + "/delegations"])' in loader
    assert "renderDelegationPanel()" in APP_JS
    assert "delegation_child_event" in events
    assert "delegation-child" in renderer
    assert ".delegation-child" in STYLE_CSS


def test_historic_cell_fork_requires_exact_checkpoint_proof() -> None:
    sanitizer = _extract_js_function(APP_JS, "sanitizeBranches")
    fork_cell = _extract_js_function(APP_JS, "forkNotebookCell")
    cell = _extract_js_function(APP_JS, "cellNode")
    branches = _extract_js_function(APP_JS, "renderBranchPanel")

    assert "internal: cp.internal === true" in sanitizer
    assert "source_kind: publicText" in sanitizer
    assert "fork_from_message" in sanitizer
    assert "cell.fork_checkpoint_id" in fork_cell
    assert 'branchCapability("fork_from_cell")' in fork_cell
    assert "from_cell_id: nbCellKey(cell)" in fork_cell
    assert "branch_id" not in fork_cell
    assert "!e.live" in cell and "e.fork_checkpoint_id" in cell
    assert "if (canForkCell)" in cell
    assert "internalCheckpoints" in branches
    assert 'el("details", "internal-checkpoints")' in branches


def test_recovery_and_branch_mutations_are_safe_visible_workbench_controls() -> None:
    loader = _extract_js_function(APP_JS, "loadWorkbenchState")
    recovery_sanitizer = _extract_js_function(APP_JS, "sanitizeRecoveryActions")
    recovery_current = _extract_js_function(APP_JS, "recoveryIsCurrentBranch")
    recovery_execute = _extract_js_function(APP_JS, "executeRecoveryAction")
    recovery_card = _extract_js_function(APP_JS, "recoveryTimelineCard")
    undo_projection = _extract_js_function(APP_JS, "branchUndoFromProjection")
    fork = _extract_js_function(APP_JS, "forkSessionCheckpoint")
    activate = _extract_js_function(APP_JS, "activateSessionBranch")
    revert_sanitizer = _extract_js_function(APP_JS, "sanitizeRevertPreview")
    mutation_sanitizer = _extract_js_function(APP_JS, "sanitizeRevertMutationResult")
    apply_revert = _extract_js_function(APP_JS, "applySessionRevert")
    undo = _extract_js_function(APP_JS, "undoSessionRevert")
    branches = _extract_js_function(APP_JS, "renderBranchPanel")

    assert 'base + "/recovery/actions"' in loader
    assert 'RECOVERY_ACTION_IDS = ["restore", "retry", "restart_fresh"]' in APP_JS
    assert "RECOVERY_ACTION_IDS.map" in recovery_sanitizer
    assert (
        "enabled: !!" in recovery_sanitizer
        and "reason: publicText" in recovery_sanitizer
    )
    for forbidden in ("detail", "events", "environment", "arguments", "wire_id"):
        assert forbidden not in recovery_sanitizer
        assert forbidden not in recovery_card
    assert "actions.root_frame_id === S.currentId" in recovery_current
    assert "actions.branch_id === projectedBranch" in recovery_current
    assert 'confirm(t("recovery.freshConfirm"))' in recovery_execute
    assert 'confirm: actionId === "restart_fresh"' in recovery_execute
    assert "/recovery/actions/${actionId}" in recovery_execute
    assert "loadWorkbenchState(frameId, true)" in recovery_execute
    assert "workbenchErrors.recoveryAction" in recovery_card
    assert "action.reason" in recovery_card and "action.enabled" in recovery_card

    assert 'prompt(t("branch.forkName")' in fork
    assert "from_checkpoint_id: checkpointId" in fork
    assert "/branches/fork" in fork
    assert "from_cell_id" not in fork
    assert "body.name = name" in fork
    assert "/branches/${encodeURIComponent(branchId)}/activate" in activate
    assert "openConversation(frameId, S.project)" in activate
    assert 'branchCapability("activate")' in activate
    assert "sanitizeRevertMutationResult(response)" in apply_revert
    assert "openConversation(frameId, S.project)" in apply_revert
    assert "/revert/undo" in undo and "revert_checkpoint_id" in undo
    assert "openConversation(frameId, S.project)" in undo
    assert "head_checkpoint_id" in undo_projection
    assert "undo_revert_checkpoint_id" in undo_projection
    for forbidden in ("workspace", "preview", "operation", "generation_refs"):
        assert forbidden not in mutation_sanitizer
    assert "writes_count" in revert_sanitizer and "conflicts_count" in revert_sanitizer
    assert "publicList" not in revert_sanitizer
    assert "ws.writes_count" in branches and "ws.conflicts_count" in branches
    assert 't("branch.currentSummary"' in branches
    assert 't("branch.viewOnly")' in branches
    assert "activateSessionBranch" in branches
    assert "forkSessionCheckpoint" in branches and "undoSessionRevert" in branches
    assert '"branch_projection_restored"' in APP_JS
    assert "scheduleBranchConversationResync" in APP_JS
    assert ".recovery-action-list" in STYLE_CSS
    assert ".checkpoint-actions" in STYLE_CSS
    assert APP_JS.count('"recovery.freshConfirm"') >= 2
    assert APP_JS.count('"branch.undo"') >= 2


def test_imported_session_quarantine_is_visible_and_blocks_live_controls() -> None:
    recovery = _extract_js_function(APP_JS, "sanitizeRecovery")
    recovery_actions = _extract_js_function(APP_JS, "sanitizeRecoveryActions")
    summary = _extract_js_function(APP_JS, "runtimeSummary")
    summary_node = _extract_js_function(APP_JS, "runtimeSummaryNode")
    send = _extract_js_function(APP_JS, "send")
    kernel = _extract_js_function(APP_JS, "_paintKernel")
    notebook = _extract_js_function(APP_JS, "renderNotebook")

    for sanitizer in (recovery, recovery_actions):
        assert "view_only: source.view_only === true" in sanitizer
        assert "trust_state: publicText(source.trust_state" in sanitizer
        assert "explicit_recovery_required" in sanitizer

    assert "viewOnly, trustState" in summary
    assert "runtime.trust.quarantined" in summary_node
    assert 'runtime.viewOnly && runtime.trustState === "quarantined"' in send
    assert 'hint(t("runtime.quarantineHint"), true)' in send
    assert 'st.view_only === true && st.trust_state === "quarantined"' in kernel
    assert "bStart.disabled = st.alive || quarantined" in kernel
    assert "st.alive || st.turn_running || quarantined" in kernel
    assert (
        'st.repl_enabled && !(_kc.st.view_only && _kc.st.trust_state === "quarantined")'
        in notebook
    )
    assert APP_JS.count('"runtime.trust.quarantined"') >= 2
    assert APP_JS.count('"runtime.quarantineHint"') >= 2


def test_variable_inspector_is_manual_read_only_and_strictly_sanitized() -> None:
    sanitizer = _extract_js_function(APP_JS, "sanitizeVariableInspection")
    refresh = _extract_js_function(APP_JS, "refreshVariableInspector")
    renderer = _extract_js_function(APP_JS, "renderVariableInspector")
    notebook = _extract_js_function(APP_JS, "renderNotebook")
    reset = _extract_js_function(APP_JS, "openConversation")

    assert "exactScope" in sanitizer
    assert "source.root_frame_id" in sanitizer and "source.branch_id" in sanitizer
    assert "source.language === language" in sanitizer
    assert "Array.isArray(source.variables)" in sanitizer
    assert ".slice(0, 500)" in sanitizer
    assert "Number.isSafeInteger(item.length)" in sanitizer
    assert 'typeof value === "string"' in sanitizer
    assert 'typeof value === "number"' in sanitizer
    assert "variables: available ? variables : []" in sanitizer
    for forbidden in ("innerHTML", "workspace", "detail", "arguments", "wire_id"):
        assert forbidden not in sanitizer

    assert "/kernel/variables?language=${language}" in refresh
    assert 'method: "POST"' not in refresh
    assert "sanitizeVariableInspection(payload, frameId, language)" in refresh
    assert "request !== S.variableInspector.request" in refresh
    assert "data-variable-inspector" in renderer
    assert 'data-action", "refresh-variables"' in renderer
    assert '[["python", "Python"], ["r", "R"]]' in renderer
    assert "nb.variables.generation" in renderer
    assert "nb.variables.revision" in renderer
    assert "nb.variables.loading" in renderer
    assert "nb.variables.empty" in renderer
    assert "nb.variables.error" in renderer
    assert "refreshVariableInspector()" not in renderer
    assert "nb.appendChild(renderVariableInspector())" in notebook
    assert 'variableInspector = { language: "python", results: {}' in reset
    assert ".nb-variables" in STYLE_CSS
    assert ".nb-variable-row" in STYLE_CSS


def test_local_model_discovery_is_loopback_only_and_requires_explicit_add() -> None:
    loopback = _extract_js_function(APP_JS, "loopbackModelBase")
    sanitizer = _extract_js_function(APP_JS, "sanitizeLocalModelDiscovery")
    renderer = _extract_js_function(APP_JS, "renderLocalModelEndpoints")
    models = _extract_js_function(APP_JS, "custModels")

    assert 'host === "127.0.0.1"' in loopback
    assert 'host === "[::1]"' in loopback
    assert '["http:", "https:"]' in loopback
    assert "!parsed.username" in loopback and "!parsed.password" in loopback
    assert "!parsed.search" in loopback and "!parsed.hash" in loopback
    assert "LOCAL_MODEL_KINDS.has(kind)" in sanitizer
    assert "loopbackModelBase(raw.base_url)" in sanitizer
    assert 'raw.local !== true || raw.provider !== "chatgpt"' in sanitizer
    assert 'typeof value !== "string"' in sanitizer
    assert ".slice(0, 500)" in sanitizer
    assert "mutated_settings: false" in sanitizer
    for forbidden in ("raw.api_key", "innerHTML", "fetch(", "raw.error"):
        assert forbidden not in sanitizer

    assert 'api("/model-profiles", { method: "POST"' in renderer
    assert "add.onclick" in renderer
    assert "endpoint.base_url" in renderer and "endpoint.provider" in renderer
    assert "loopbackModelBase(profile.base_url)" in renderer
    assert 'api("/model-endpoints/discover"' in models
    assert "runLocalScan(false)" in models
    assert 'const provIn = el("select", "cust-input")' in models
    assert '["chatgpt", "cust.models.protocol.openai"]' in models
    assert '["claude", "cust.models.protocol.anthropic"]' in models
    assert '["ark", "cust.models.protocol.ark"]' in models
    assert "datalist" not in models
    assert "known_providers" not in models
    # Discovery itself is GET-only; profile mutation exists solely behind the
    # explicit per-endpoint Add button above.
    scan = models[
        models.index("const runLocalScan") : models.index("// --- add / edit form")
    ]
    assert 'method: "POST"' not in scan
    assert ".local-model-results" in STYLE_CSS


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


def test_customize_skills_exposes_scoped_version_history_and_safe_rollback() -> None:
    catalog = _extract_js_function(APP_JS, "custSkills")
    path = _extract_js_function(APP_JS, "skillVersionPath")
    history = _extract_js_function(APP_JS, "skillVersionHistory")

    assert 'api("/skills/catalog")' in catalog
    assert "/projects/${encodeURIComponent(pid)}/skills/catalog" in catalog
    assert "s.versioned" in catalog
    assert "skillVersionHistory(s.name, scope" in catalog
    assert 'scope === "project"' in path
    assert "encodeURIComponent(projectId)" in path
    assert '"/versions?limit=100"' in history
    assert '"/rollback", { method: "POST"' in history
    assert "JSON.stringify({ version_id: versionId })" in history
    assert "data.status && data.status.read_only" in history
    assert "document.createElement" not in history
    assert APP_JS.count('"skill.historyBtn"') >= 2
    assert APP_JS.count('"skill.rollbackConfirm"') >= 2
    assert ".skill-version-list" in STYLE_CSS
    assert ".skill-version-card" in STYLE_CSS
