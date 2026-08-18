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

import pytest

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


def test_datapro_card_keeps_credentials_ephemeral_and_authenticates_by_search():
    card = _extract_js_function(APP_JS, "dataproCard")
    connectors = _extract_js_function(APP_JS, "custConnectors")
    code_reader = _extract_js_function(APP_JS, "dataproResponseCode")
    index_reader = _extract_js_function(APP_JS, "dataproIndexComplete")

    for selector in (
        "datapro-plan-key",
        "datapro-save-key",
        "datapro-query",
        "datapro-search",
        "datapro-enable-skill",
        "dataproStatus",
        "dataproIndexStatus",
        "dataproResult",
        "dataproArtifact",
    ):
        assert selector in card
    assert 'keyInput.type = "password"' in card
    assert 'keyInput.autocomplete = "off"' in card
    assert card.count('keyInput.value = ""') >= 2
    assert "/datapro/config" in card
    assert "/datapro/search" in card
    assert "/probe" not in card
    assert ".textContent = dataproResultText" in card
    assert "response.structuredContent" in code_reader
    assert "response.code" not in code_reader
    assert "code === 0 && dataproIndexComplete(response)" in card
    assert 'indexed ? t("cust.datapro.available")' in card
    assert 'code === 0 ? t("cust.datapro.indexFailed")' in card
    assert "index.complete === true" in index_reader
    assert "index.source_leaf_count === index.indexed_leaf_count" in index_reader
    assert 'typeof index.source_digest === "string"' in index_reader
    assert "index.source_digest === index.indexed_digest" in index_reader
    assert "response.index.entry_count" in card
    assert 'code === 4011 ? t("cust.datapro.auth4011")' in card
    assert "conns.filter(k => k.connector_id !== DATAPRO_CONNECTOR_ID)" in connectors
    assert "已完整索引本次返回的 {0} 条记录（{1} 个内容叶节点）" in APP_JS
    assert "Key 无效、额度不足，或者专业数据集 Harness 未开启。" in APP_JS


def test_doubao_search_is_the_primary_no_fallback_network_card():
    card = _extract_js_function(APP_JS, "doubaoSearchCard")
    result_text = _extract_js_function(APP_JS, "doubaoSearchResultText")
    network = _extract_js_function(APP_JS, "custNetwork")

    for selector in (
        "doubao-search-plan-key",
        "doubao-search-save-key",
        "doubao-search-query",
        "doubao-search-run",
        "doubaoSearchStatus",
        "doubaoSearchResult",
    ):
        assert selector in card
    assert 'keyInput.type = "password"' in card
    assert 'keyInput.autocomplete = "off"' in card
    assert card.count('keyInput.value = ""') >= 2
    assert "/doubao-search/config" in card
    assert "/doubao-search/search" in card
    assert "/search/config" not in card
    assert "response.available === true" in card
    assert 'response.source === "doubao"' in card
    assert "results.length > 0" in card
    assert "response.count === results.length" in card
    assert "result.textContent = doubaoSearchResultText" in card
    assert "innerHTML" not in result_text
    assert (
        "textContent" not in result_text
    )  # pure string builder, assigned safely by card
    assert network.index("doubaoSearchCard") < network.index(
        't("cust.network.allowName")'
    )
    assert "主选" in APP_JS
    assert "备用搜索 API Key（Tavily）" in APP_JS
    assert "专用测试不会回退" in APP_JS


def test_command_palette_surfaces_safe_datapro_index_hits():
    search = _extract_js_function(APP_JS, "palSearch")
    summary = _extract_js_function(APP_JS, "dataproPaletteSummary")
    opener = _extract_js_function(APP_JS, "openDataproSearchHit")

    assert "r.datapro" in search
    assert 't("palette.group.datapro")' in search
    assert "publicText" in search
    assert "publicText" in summary
    assert "innerHTML" not in summary
    assert "hit.artifact_id" in opener
    assert 'openCust("connectors")' in opener


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


def test_frontend_keeps_the_whole_error_envelope_not_just_the_prose() -> None:
    """This used to assert only that `api()` mentioned `j.error`.

    That was the right check when `error` was all the backend sent. The
    envelope is now `{error, code, status, request_id}`, and `api()` parsed all
    four and threw away three: `code` is the stable machine-readable contract
    -- the backend documents the prose as explicitly *not* an interface -- and
    `request_id` is the string that ties a user's report to a server log line,
    which existed on both ends and was displayed at neither.

    So assert the whole envelope survives, which the weaker check could not
    distinguish from dropping it.
    """
    start = APP_JS.index("class ApiError")
    error_source = APP_JS[start : APP_JS.index("const S =")]
    for field in ("error", "code", "status", "request_id"):
        assert re.search(rf"\b{field}\b", error_source), (
            f"the failure envelope's `{field}` is parsed and then dropped; "
            "a client cannot branch on what it never receives"
        )
    assert "throw new ApiError(" in error_source, (
        "api() must throw the structured error, not a bare Error that flattens "
        "the envelope into one string"
    )


def test_every_user_facing_error_shows_the_request_id() -> None:
    """A `request_id` nobody sees ties nothing to anything.

    The whole point of the correlation id is that a user can quote it and an
    operator can find the matching log line. Rendering `e.message` alone in the
    composer hint drops it at the last step, after both ends went to the
    trouble of carrying it. `apiErrorText` appends it when there is one.
    """
    raw = re.findall(r'hint\(t\("[^"]+",\s*\w+\.message', APP_JS)
    assert not raw, (
        "these error hints render the message without the request id: "
        f"{sorted(set(raw))}"
    )
    assert "function apiErrorText(" in APP_JS


def test_no_response_path_builds_its_own_lossy_error() -> None:
    """`api()` was not the only converter.

    Three call sites re-implemented the same `!ok -> new Error(string)` by
    hand -- `shareCall`, the session-package import, and `fetchArtifactText`,
    which never parsed the body at all. Each discarded the envelope
    independently, so fixing `api()` alone would have left three paths whose
    failures carry less information than the rest, with nothing marking them
    as different.
    """
    # Scoped to conversions of a *failed* HTTP response, which is the only
    # place an envelope exists to keep.
    #
    # This carried a note that the Customize skill routes reported domain
    # failures at HTTP 200 and were therefore out of reach. That gap is closed:
    # `server/skills.py` attaches a stable code to every soft failure and the
    # gateway projects it to a real status, so those bodies go through
    # `public_failure` like any other. The one client-side `if (r.error) throw`
    # that existed to work around it is gone, which is why nothing here needs
    # to carve out an exception any more.
    lossy = re.findall(
        r"if\s*\(!\s*\w+\.ok\)[^;]*throw new Error\([^)]*\)"
        r"|throw new Error\(\s*result\.error[^)]*\)",
        APP_JS,
    )
    assert not lossy, (
        "these build an error from a failed response without keeping the "
        f"envelope: {sorted(set(lossy))}"
    )


def test_no_error_branch_reads_a_status_out_of_prose() -> None:
    """The annotation save had `/404/.test(e.message)`.

    It was testing `api()`'s *fallback* string (`"HTTP 404"`), which the
    gateway never produces -- every failure carries a JSON body, so the message
    was `"not found"` and the regex never matched. The specific guidance it
    selected ("backend annotation API not loaded, restart the service") was
    therefore unreachable, and the user got the generic message instead. Not a
    style problem: a live dead branch, and exactly what a structured `status`
    and `code` exist to prevent.
    """
    prose_status = re.findall(r"/\s*\d{3}\s*/\s*\.test\(", APP_JS)
    assert (
        not prose_status
    ), f"an error branch is matching a status code out of prose: {prose_status}"
    assert (
        "e.status === 404" in APP_JS
    ), "the annotation-save branch should read the structured status"


def test_a_paused_plan_is_rendered_and_can_be_resumed() -> None:
    """The backend could hold `paused` before anything could show it.

    `renderPlanCard` handled draft/executing/completed/failed and fell through
    for `paused` to an empty status line and no controls -- so a plan that
    stopped with steps left looked like a plan with nothing to say, and the
    only way out was to discard it and start over. The status existed, the
    reconciliation that produces it existed, and the user could not act on it.
    """
    assert '"plan.eyebrow.paused"' in APP_JS
    assert '"plan.status.paused"' in APP_JS
    assert "async function resumePlan()" in APP_JS
    assert "/plan/resume" in APP_JS
    # Both translation tables, not just the one the developer reads.
    assert (
        APP_JS.count('"plan.resume":') == 2
    ), "the resume control is missing from one of the two i18n tables"


def test_frontend_never_hardcodes_an_unversioned_api_path() -> None:
    """Every request must go through ``API``, never a literal ``/api/...``.

    The un-versioned surface was deleted when the contract moved to ``/api/v1``
    (there is deliberately no legacy alias), so a hardcoded ``"/api/share/…"``
    is not a stylistic slip — it is a route that 404s at runtime while every
    offline test still passes.  Web sharing shipped exactly that way: six
    literals that no unit test exercised because they only run in a browser.
    """
    literals = re.findall(r"""["'`]/api/(?!v\d)[^"'`]*""", APP_JS)
    assert not literals, (
        "these frontend paths bypass the API prefix and will 404: "
        f"{sorted(set(literals))}"
    )
    assert 'const API = "/api/v1"' in APP_JS


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


def test_standard_environment_readiness_is_advisory_until_a_cell_is_routed() -> None:
    """Control-only turns stay routable; a refused Cell opens managed repair."""

    send_source = _extract_js_function(APP_JS, "send")
    refresh = _extract_js_function(APP_JS, "refreshEnvironmentStatus")
    terminal = _extract_js_function(APP_JS, "handleEnvironmentReadinessTerminal")
    on_event = _extract_js_function(APP_JS, "onEvent")
    init = _extract_js_function(APP_JS, "init")

    assert "environmentReadinessPreflight" not in APP_JS
    assert 'api("/environments/status")' not in send_source
    assert 'api("/environments/status")' in refresh
    assert ".standard_profile_readiness" in refresh
    assert "await refreshEnvironmentStatus()" in init
    assert 'detail.status !== "failed"' in terminal
    assert '"environment_not_ready"' in terminal
    assert '"environment_readiness_unavailable"' in terminal
    assert 'openCust("compute")' in terminal
    assert "refreshEnvironmentStatus().finally" in terminal
    assert "handleEnvironmentReadinessTerminal(m)" in on_event
    assert on_event.index("handleEnvironmentReadinessTerminal(m)") < on_event.index(
        "turnDone(m.status, m)"
    )

    for status, code in (
        (409, "environment_not_ready"),
        (503, "environment_readiness_unavailable"),
    ):
        assert f'error.status === {status} && error.code === "{code}"' in APP_JS
    assert "if (isEnvironmentReadinessError(e))" in send_source
    assert "await refreshEnvironmentStatus()" in send_source
    assert "if (S._environmentStatusRefreshFailed)" in send_source
    assert 'reason: "status_refresh_failed"' in send_source
    assert "composer.value = text" in send_source


def test_standard_readiness_ui_is_complete_copy_only_and_text_safe() -> None:
    sanitize = _extract_js_function(APP_JS, "sanitizeStandardProfileReadiness")
    banner = _extract_js_function(APP_JS, "renderEnvironmentReadinessBanner")
    card = _extract_js_function(APP_JS, "renderStandardProfileReadiness")
    compute = _extract_js_function(APP_JS, "custCompute")

    assert INDEX_HTML.count('class="environment-readiness-banner hidden"') == 2
    assert "readiness.enabled === true && readiness.ready !== true" in banner
    assert "textContent" in banner and "innerHTML" not in banner
    assert "missing_environments.forEach" in card
    assert "Object.entries(readiness.missing_packages).forEach" in card
    assert ".slice(" not in card, "the Compute card must show the complete missing list"
    assert "remediation.requires_explicit_action" in card
    assert "navigator.clipboard.writeText(item.command)" in card
    assert "api(" not in card, "copying remediation must never execute or install it"
    assert "innerHTML" not in card, "server-projected names and commands are text only"
    assert "refreshEnvironmentStatus()" in compute
    assert "renderStandardProfileReadiness(readiness)" in compute
    assert "sourceRemediation.commands" in sanitize
    assert "sourceRemediation.requires_explicit_action === true" in sanitize

    for key in (
        "environment.readiness.bannerTitle",
        "environment.readiness.openCompute",
        "environment.readiness.sendBlocked",
        "environment.readiness.explicitOnly",
    ):
        assert APP_JS.count(f'"{key}":') == 2, f"{key} needs zh and en text"
    assert ".environment-readiness-banner" in STYLE_CSS
    assert ".standard-readiness-card" in STYLE_CSS


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


def test_search_result_links_pin_the_http_scheme_before_writing_href() -> None:
    sanitizer = _extract_js_function(APP_JS, "searchResultHttpUrl")
    body = _extract_js_function(APP_JS, "stepBody")

    assert 'lower.startsWith("https://")' in sanitizer
    assert 'lower.startsWith("http://")' in sanitizer
    assert 'return "https://" + raw.slice(8)' in sanitizer
    assert 'return "http://" + raw.slice(7)' in sanitizer
    assert "searchResultHttpUrl(r.url)" in body
    assert "a.href = safeUrl" in body
    assert "a.href = u" not in body


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
    details = _extract_js_function(APP_JS, "actionTimelineDetails")
    append_details = _extract_js_function(APP_JS, "appendActionTimelineDetails")
    row = _extract_js_function(APP_JS, "actionTimelineLedgerRow")
    selector = _extract_js_function(APP_JS, "selectActionTimelineGroup")
    ledger = _extract_js_function(APP_JS, "actionTimelineLedger")
    creator = _extract_js_function(APP_JS, "createActionTimelineView")
    virtualizer = _extract_js_function(APP_JS, "reconcileActionTimelineWindow")
    viewport = _extract_js_function(APP_JS, "actionTimelineViewportScrolled")
    keyboard = _extract_js_function(APP_JS, "actionTimelineLedgerKeydown")
    updater = _extract_js_function(APP_JS, "updateActionTimelineLedger")
    history = _extract_js_function(APP_JS, "syncActionTimelineHistoryState")
    inspector = _extract_js_function(APP_JS, "actionTimelineInspector")
    renderer = _extract_js_function(APP_JS, "renderActionTimeline")
    events = _extract_js_function(APP_JS, "onEvent")

    assert "dock-timeline" in INDEX_HTML
    assert "action_timeline" in events and "action-timeline" in events
    assert "ACTION_TIMELINE_PAGE_SIZE = 500" in APP_JS
    assert "ACTION_TIMELINE_ROW_HEIGHT = 46" in APP_JS
    assert "ACTION_TIMELINE_MAX_GROUPS" not in APP_JS
    assert '"timeline.historyLimit"' not in APP_JS
    assert "history_limit_reached" not in APP_JS
    assert ".slice(-ACTION_TIMELINE_PAGE_SIZE)" in sanitizer
    assert ".slice(-50)" in sanitizer
    assert "events: (group.events || []).map" in sanitizer
    assert "publicList(event.resource_keys, 64, 160)" in sanitizer
    assert "publicList(event.artifacts, 32, 200)" in sanitizer
    for field in ("first_ordinal", "last_ordinal", "has_more_before", "has_more_after"):
        assert field in sanitizer
    assert "new Map()" in merger
    assert "deduped.set(group.group_id, group)" in merger
    assert "(incoming.groups || []).concat(current.groups || [])" in merger
    assert "(current.groups || []).concat(incoming.groups || [])" in merger
    assert ".slice(" not in merger
    assert 'direction === "before"' in merger
    assert "currentFirst <= incomingFirst" in merger
    assert "first_ordinal: groups.length ? groups[0].ordinal : null" in merger
    assert (
        'mergeActionTimelines(S.actionTimeline, sanitizeActionTimeline(timeline), "latest")'
        in loader
    )
    assert 'mergeActionTimelines(S.actionTimeline, incoming, "latest")' in events
    assert "incoming.branch_id === currentBranch" in events
    assert "before_ordinal=${first}&limit=${ACTION_TIMELINE_PAGE_SIZE}" in earlier
    assert "branch_id=${encodeURIComponent(branchId)}" in earlier
    assert 'mergeActionTimelines(current, incoming, "before")' in earlier
    assert "scrollHeight: view.scroll.scrollHeight" in earlier
    assert "scrollTop: view.scroll.scrollTop" in earlier
    assert "prependSnapshot" in earlier and "updateActionTimelineLedger" in earlier
    assert "actionTimelineFilterScrollSnapshot(view)" in earlier
    assert "view.pendingPrependRestore = pendingPrependRestore" in earlier
    assert "options.filterChanged || pendingPrependRestore" in updater
    assert "if (timeline.has_more_before)" in history
    assert 'data-action", "load-earlier-timeline"' in history
    assert 't(loading ? "timeline.loadingEarlier" : "timeline.loadEarlier")' in history
    assert "workbenchErrors.timelineHistory" in history
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
    # these fields and neither the ledger nor its inspector may render them.
    assert "arguments:" not in sanitizer
    assert "wire_id:" not in sanitizer
    assert "tool_call_id:" not in sanitizer
    assert "assistant_content:" not in sanitizer
    assert "input_tokens" in sanitizer and "output_tokens" in sanitizer
    for key in (
        "owner",
        "permission",
        "resources",
        "artifacts",
        "generation",
        "replay",
        "duration",
        "tokens",
        "cost",
    ):
        assert f'timelineMeta(t("timeline.{key}")' in append_details
    assert "details.latest.error" in append_details
    assert "appendActionTimelineDetails(panel, group)" in inspector
    assert "appendActionTimelineDetails(card, group)" in card

    # The session view is a semantic table. Every action row is reconciled by
    # the durable group id; ordinal and array position are display/order only.
    assert 'el("table", "timeline-ledger")' in creator
    assert 'el("thead")' in creator and 'el("tbody", "timeline-ledger-body")' in creator
    assert "timelineOrdinal(group.ordinal)" in row
    assert '"#" + ordinalText' in row
    assert "reusableRows.get(group.group_id)" in virtualizer
    assert "row.dataset.groupId = group.group_id" in row
    assert 'el("button", "timeline-row-button", title)' in row
    assert 'row.setAttribute("role", "button")' not in row
    assert 'titleButton.setAttribute("aria-expanded"' in row
    assert "entry.turnBoundary" in virtualizer
    assert '" turn-boundary"' in row
    assert "actionTimelineLedger(groups, branchScope, rootFrameScope)" in renderer
    assert "actionTimelineCard(group)" not in renderer
    assert "root.dataset.timelineBranch" in renderer
    assert "entries.slice(start, end)" in virtualizer
    assert "translateY(${index * ACTION_TIMELINE_ROW_HEIGHT}px)" in virtualizer
    assert "clientHeight" in virtualizer and "ACTION_TIMELINE_OVERSCAN" in virtualizer
    assert "view.tbody.style.height" in updater
    assert "snapshot.scrollTop + delta" in updater and "snapshot.followTail" in updater
    assert "view.followTail" in updater and "view.followTail" in viewport
    assert "loadEarlierActionTimeline()" in viewport
    assert "view.scroll.scrollTop <= ACTION_TIMELINE_TOP_THRESHOLD" in viewport
    assert 'updateActionTimelineLedger({ direction: "latest" })' in events
    assert (
        "renderActionTimeline()"
        not in events.split("action_timeline", 1)[1].split("execution_queue", 1)[0]
    )
    assert "focusTarget.focus({ preventScroll: true })" in virtualizer
    for key in ("ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End"):
        assert key in keyboard
    assert "S.actionTimelineSelectedGroupId" in inspector
    assert "selectActionTimelineGroup(groupId, branchScope, false)" in row
    assert "S.actionTimelineSelectedGroupId = groupId" in selector
    side_order = (
        "side.appendChild(renderBranchPanel()); "
        "side.appendChild(renderDelegationPanel()); "
        "side.appendChild(renderComputeTasksPanel()); "
        "side.appendChild(renderContextPanel()); "
        "side.appendChild(renderSecurityPanel())"
    )
    assert side_order in renderer
    for forbidden in ("arguments", "wire_id", "tool_call_id", "assistant_content"):
        for public_renderer in (
            card,
            details,
            append_details,
            row,
            ledger,
            creator,
            virtualizer,
            updater,
            inspector,
            renderer,
        ):
            assert forbidden not in public_renderer
    assert "innerHTML" not in inspector
    for key in (
        "timeline.column.ordinal",
        "timeline.column.kind",
        "timeline.column.action",
        "timeline.turnBoundary",
        "timeline.inspector",
        "timeline.inspector.close",
        "timeline.row.open",
    ):
        assert APP_JS.count(f'"{key}"') >= 3
    assert ".timeline-card" in STYLE_CSS  # recovery + project-level timeline
    assert ".timeline-ledger" in STYLE_CSS
    assert ".timeline-inspector" in STYLE_CSS
    assert ".timeline-ledger-scroll{max-height:clamp" in STYLE_CSS
    assert "overflow:auto" in STYLE_CSS
    assert ".timeline-ledger-row{position:absolute" in STYLE_CSS
    assert ".timeline-ledger-row.turn-boundary td{border-top:3px" in STYLE_CSS


def test_action_timeline_search_and_turn_folding_are_loaded_scope_and_keyed() -> None:
    search_doc = _extract_js_function(APP_JS, "actionTimelineSearchDocument")
    search_index = _extract_js_function(APP_JS, "syncActionTimelineSearchIndex")
    search_groups = _extract_js_function(APP_JS, "searchActionTimelineGroups")
    change_search = _extract_js_function(APP_JS, "changeActionTimelineSearch")
    toolbar = _extract_js_function(APP_JS, "createActionTimelineToolbar")
    toolbar_sync = _extract_js_function(APP_JS, "syncActionTimelineSearchToolbar")
    entries = _extract_js_function(APP_JS, "actionTimelineLedgerEntries")
    toggle = _extract_js_function(APP_JS, "toggleActionTimelineTurn")
    turn_row = _extract_js_function(APP_JS, "actionTimelineTurnSummaryRow")
    turn_toggle = _extract_js_function(APP_JS, "actionTimelineTurnToggle")
    updater = _extract_js_function(APP_JS, "updateActionTimelineLedger")
    virtualizer = _extract_js_function(APP_JS, "reconcileActionTimelineWindow")
    viewport = _extract_js_function(APP_JS, "actionTimelineViewportScrolled")
    model = _extract_js_function(APP_JS, "actionTimelineOverviewModel")
    hit = _extract_js_function(APP_JS, "actionTimelineOverviewHit")
    creator = _extract_js_function(APP_JS, "createActionTimelineView")
    opener = _extract_js_function(APP_JS, "openConversation")

    # The local index is deliberately narrow: only the four projected fields
    # named by the product contract, across every projected event.
    for field in ("title", "kind", "resource_keys", "artifacts"):
        assert field in search_doc
    for forbidden in ("owner", "permission", "error", "canonical_arguments", "wire"):
        assert forbidden not in search_doc
    assert 'join("\\u0000")' in search_doc
    assert "new Map()" in search_index and "group.group_id" in search_index
    assert "cached.group === group" in search_index
    assert ".includes(view.searchNeedle)" in search_groups

    assert 'el("form", "timeline-toolbar")' in toolbar
    assert 'setAttribute("role", "search")' in toolbar
    assert 'input.type = "search"' in toolbar
    assert 'el("label", "timeline-search-label"' in toolbar
    assert 'setAttribute("aria-live", "polite")' in toolbar
    assert "event => event.preventDefault()" in toolbar
    assert "timeline.search.scope" in toolbar_sync
    assert "loadedCount" in toolbar_sync and "matchCount" in toolbar_sync
    assert (
        "searchMatchCount" in toolbar_sync
        and "timeline.search.matchesInSelection" in toolbar_sync
    )
    assert (
        "filterChanged: true" in change_search
        and "view.autoLoadArmed = false" in change_search
    )
    assert "!view.searchNeedle" in viewport

    # Collapsed summaries are virtual fixed-height entries keyed by turn_id;
    # durable action rows continue to be keyed only by group_id.
    assert 'entries.push({ type: "turn", turnId' in entries
    assert 'entries.push({ type: "group", group' in entries
    assert "view.collapsedTurns.has(turnId)" in entries
    assert "view.searchNeedle" in entries  # search temporarily reveals matches
    assert "view.collapsedTurns.add(turnId)" in toggle
    assert "view.collapsedTurns.delete(turnId)" in toggle
    assert "row.dataset.turnId = entry.turnId" in turn_row
    assert "delete row.dataset.groupId" in turn_row
    assert 'el("button", "timeline-turn-toggle")' in turn_toggle
    assert 'setAttribute("aria-expanded"' in turn_toggle
    assert "event.stopPropagation()" in turn_toggle
    assert "reusableTurns.get(entry.turnId)" in virtualizer
    assert "reusableRows.get(group.group_id)" in virtualizer
    assert "entries.length * ACTION_TIMELINE_ROW_HEIGHT" in updater
    assert "snapshot.scrollTop + delta" in updater

    # Search controls the painted items, while the loaded groups continue to
    # define the truthful time axis and omitted-prefix position.
    assert "domainGroups = groups" in model
    assert "domainGroups.forEach" in model
    assert "drawableItems" in model and "item.rank = rank" in model
    assert "model.items[candidateRank]" in hit
    assert "view.allGroups[candidateRank]" not in hit
    assert "drawActionTimelineOverview(view, searchGroups, force, allGroups)" in updater
    assert "searchActionTimelineGroups(view, allGroups)" in updater
    assert "filteredActionTimelineGroups(view, searchGroups)" in updater
    assert "actionTimelineLedgerEntries(view, groups)" in updater

    # View-local state survives tab detaches but is discarded with the view on
    # session/root or branch scope changes. It is never persisted globally.
    for state in (
        "searchQuery",
        "searchNeedle",
        "searchIndex",
        "collapsedTurns",
        "entries",
    ):
        assert state in creator
    assert (
        "firstVisible" in creator
        and "view.start"
        not in creator.split('scroll.addEventListener("keydown"', 1)[1].split(
            "table.addEventListener", 1
        )[0]
    )
    assert "destroyActionTimelineView()" in opener
    assert "S.actionTimeline = null" in opener
    assert "localStorage" not in change_search and "localStorage" not in toggle
    for interaction in (change_search, toggle):
        assert "fetch(" not in interaction and "api(" not in interaction
        assert "loadEarlierActionTimeline" not in interaction

    for key in (
        "timeline.search.label",
        "timeline.search.placeholder",
        "timeline.search.scope",
        "timeline.search.matches",
        "timeline.search.clear",
        "timeline.turn.collapse",
        "timeline.turn.expand",
        "timeline.turn.summary",
        "timeline.ledger.keyboard",
    ):
        assert APP_JS.count(f'"{key}"') >= 3
    assert ".timeline-toolbar{" in STYLE_CSS
    assert ".timeline-ledger-row.search-match td{" in STYLE_CSS
    assert ".timeline-turn-toggle{" in STYLE_CSS
    assert ".timeline-turn-toggle:focus-visible{" in STYLE_CSS
    assert ".timeline-turn-summary" in STYLE_CSS


def test_action_timeline_overview_is_truthful_interactive_and_constant_dom() -> None:
    latest = _extract_js_function(APP_JS, "latestActionTimelineAttempt")
    span = _extract_js_function(APP_JS, "actionTimelineSpan")
    model = _extract_js_function(APP_JS, "actionTimelineOverviewModel")
    creator = _extract_js_function(APP_JS, "createActionTimelineOverview")
    painter = _extract_js_function(APP_JS, "renderActionTimelineOverviewPaths")
    hover = _extract_js_function(APP_JS, "actionTimelineOverviewPointerMove")
    tooltip = _extract_js_function(APP_JS, "showActionTimelineOverviewTooltip")
    overlap = _extract_js_function(APP_JS, "actionTimelineSelectionOverlaps")
    commit = _extract_js_function(APP_JS, "commitActionTimelineOverviewSelection")
    wheel = _extract_js_function(APP_JS, "actionTimelineOverviewWheel")
    begin_gesture = _extract_js_function(APP_JS, "beginActionTimelineOverviewGesture")
    gesture = _extract_js_function(APP_JS, "moveActionTimelineOverviewGesture")
    keydown = _extract_js_function(APP_JS, "actionTimelineOverviewKeydown")
    controls = _extract_js_function(APP_JS, "syncActionTimelineOverviewControls")
    reveal = _extract_js_function(APP_JS, "revealActionTimelineOverviewGroup")
    view_creator = _extract_js_function(APP_JS, "createActionTimelineView")

    assert ".slice(-1)[0]" in latest
    assert "times.allocated, times.started" in span
    assert "times.started, times.response" in span
    assert "times.response, times.finished" in span
    assert "times.finished == null" in span
    assert "times.capture" in span
    assert "markerAt: running ? times.allocated : null" in span
    assert "end: running ? latestKnown : times.finished" in span
    assert "if (!running" in span and "segments" in span
    assert "Date.now()" not in span
    assert "byId.set(item.groupId, item)" in model
    assert (
        "groups.map(group => actionTimelineSpan(group, 0, 1)).filter(Boolean)" in model
    )
    assert "item.rank = rank" in model and "item.laneCount = laneCount" in model
    assert 'svgElement("svg"' in creator
    assert "timeline-overview-phase queue" in creator
    assert "timeline-overview-phase ttft" in creator
    assert "timeline-overview-phase decode" in creator
    assert "model.items.forEach" in painter
    assert "replaceChildren" not in painter
    assert "ACTION_TIMELINE_OVERVIEW_HOVER_DELAY" in hover
    assert "const ACTION_TIMELINE_OVERVIEW_HOVER_DELAY = 500;" in APP_JS
    assert "setTimeout" in hover
    assert "timelineOverviewExactTime" in tooltip
    assert "timelineOverviewExactDuration" in tooltip
    assert "item.start <= right && item.end >= left" in overlap
    assert "latestKnown" in span and "Date.now()" not in span
    assert "Math.floor" in commit and "Math.ceil" in commit
    assert "filterChanged: true" in commit
    assert "Math.exp" in wheel and "preventDefault" in wheel
    assert "event.ctrlKey" in begin_gesture and "? 2 : event.button" in begin_gesture
    assert "gesture.button === 2" in gesture
    assert "startViewStart" in gesture and "startViewEnd" in gesture
    assert (
        "timelineOverviewXToDomainTime(gesture.startViewStart, gesture.startViewEnd"
        in gesture
    )
    assert (
        "event.shiftKey" in keydown
        and "commitActionTimelineOverviewSelection" in keydown
    )
    assert "actionTimelineOverviewVisualExtent" in reveal
    for interaction in (commit, wheel, begin_gesture, gesture):
        assert "loadEarlierActionTimeline" not in interaction
        assert "fetch(" not in interaction and "api(" not in interaction
    assert "timeline.has_more_before" in controls
    assert "includesLoadedStart" in controls
    assert "overview.dataStart == null || includesLoadedStart" in controls
    # The latch arms only when the load actually took the history lock.
    # Arming it unconditionally survived every early return in
    # loadEarlierActionTimeline, and the next repaint then stole focus.
    assert (
        "overview.restoreFocusAfterPrefix = !!S._timelineHistoryLoading" in view_creator
    )
    assert "overview.restoreFocusAfterPrefix = true" not in view_creator
    assert "loadEarlierActionTimeline()" in view_creator
    assert 'data-action", "load-omitted-timeline"' in creator
    assert (
        'prefixButton = el("button", "timeline-overview-prefix hidden", "…")' in creator
    )
    assert 'overview.svg.addEventListener("wheel"' in view_creator
    assert 'overview.svg.addEventListener("contextmenu"' in view_creator
    assert 'overview.tooltip.addEventListener("pointerenter"' in view_creator
    assert "allGroups" in _extract_js_function(APP_JS, "updateActionTimelineLedger")
    assert "filteredActionTimelineGroups" in _extract_js_function(
        APP_JS, "updateActionTimelineLedger"
    )
    for forbidden in ("arguments", "wire_id", "tool_call_id", "assistant_content"):
        for public_renderer in (span, model, creator, painter, hover, tooltip):
            assert forbidden not in public_renderer
    for key in (
        "timeline.overview",
        "timeline.overview.queue",
        "timeline.overview.ttft",
        "timeline.overview.decode",
        "timeline.overview.clear",
        "timeline.overview.omitted",
    ):
        assert APP_JS.count(f'"{key}"') >= 3
    assert ".timeline-overview{" in STYLE_CSS
    assert ".timeline-overview-phase.queue" in STYLE_CSS
    assert ".timeline-overview-prefix{" in STYLE_CSS
    assert ".timeline-overview-selection{" in STYLE_CSS
    assert "pointer-events:auto;user-select:text" in STYLE_CSS


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
    # The default action, asserted as behaviour rather than as a literal. This
    # read `t("prov.exec.downloadNotebook")`, which was a proxy for "the button
    # says the right thing" and broke the moment the label came from a table
    # instead of a call site — without anything about the button changing.
    assert "NOTEBOOK_EXPORTS[0]" in export
    assert 'language: "bundle"' in APP_JS
    assert "${primary.suffix}" in export
    assert "notebooks.zip" in APP_JS
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
    assert 'fetch(API + "/sessions/import"' in importer
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


def test_runtime_summary_treats_explicit_recovery_as_view_only() -> None:
    summary = _extract_js_function(APP_JS, "runtimeSummary")
    undo = _extract_js_function(APP_JS, "branchUndoFromProjection")

    assert "explicitRecoveryRequired" in summary
    assert "recovery.explicit_recovery_required === true" in summary
    assert "(S.recoveryActions || {}).explicit_recovery_required === true" in summary
    assert "(_kc.st || {}).explicit_recovery_required === true" in summary
    assert "const viewOnly = explicitRecoveryRequired ||" in summary
    assert "state.capabilities.revert !== true" in undo


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
    # Not on render. The pane used to run the scan when it opened -- on first
    # visit and on every re-render after a save, activate or delete -- which is
    # the implicit outbound call readiness was made local-only to avoid.
    # tests/test_model_protocol_menu.py opens the pane and asserts what it did
    # and did not contact.
    assert "runLocalScan(false)" not in models
    assert 'const provIn = el("select", "cust-input")' in models
    # Pinning the three hardcoded protocol pairs here is what kept `gemini` and
    # `openai_responses` unreachable: both were accepted by the daemon and
    # served in `protocols`, and this file asserted the client's stale copy.
    # The menu is generated now, and its contents are asserted by running it.
    assert "modelProtocolOptions(data.protocols)" in models
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
    assert "capture.frame_id" in review_source
    assert "capture.frame_kind" in review_source
    assert 'capture.capture_kind === "head_checksum_reused" || !cell' in review_source
    assert "producer.frame_id" in review_source
    assert 'producer.kind === "cell"' in review_source
    assert "if (capture.cell_index != null)" in review_source
    assert 't("prov.review.producedByIdentity"' in review_source
    assert 't("prov.review.nonCellProducer"' in review_source


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


def test_no_tabular_parser_hardcodes_a_delimiter() -> None:
    """`csv()` split on a literal comma, so every `.tsv` parsed as one column.

    The artifact tile for a three-column differential-expression table reported
    "1 column", and the column's *name* was the whole header line. Wrong
    numbers about scientific output, displayed with the same confidence as
    right ones -- and nothing about the tile suggested it was guessing.

    Both parsers now take the delimiter from `delimiterFor`, which trusts the
    extension when there is one and sniffs the header when there is not,
    because science writes tab-separated `.txt` and `.dat` constantly.
    """
    assert "function delimiterFor(" in APP_JS
    # The literal-comma split, which is what made this filename-blind.
    assert (
        'else if (c === ",")' not in APP_JS
    ), "a tabular parser still splits on a hardcoded comma"
    # And no caller decides the delimiter from the suffix alone.
    assert '/\\.tsv$/i.test(fname) ? "\\t" : ","' not in APP_JS


def test_a_truncated_table_says_which_dimension_was_cut() -> None:
    """Columns beyond 24 were dropped with no notice at all.

    A 101-column table rendered 24 and looked complete: nothing distinguishes a
    narrow table from a truncated view of a wide one, so the reader cannot know
    to go and open the file. The row cap had a banner from the start, which is
    what makes the column one an omission rather than a decision.
    """
    for key in ("nb.table.rowsHidden", "nb.table.colsHidden", "nb.table.bothHidden"):
        assert (
            APP_JS.count(f'"{key}"') >= 3
        ), f"{key} is missing from a translation table or from the renderer"


def test_the_at_menu_inserts_a_pinned_reference() -> None:
    """The menu lists artifacts from across the *project*; the resolver only
    ever looked inside the current session.

    So picking a file from another conversation inserted a reference that
    resolved to nothing, silently — the menu was offering files it could not
    deliver. Inserting `name#version_id` makes them resolvable (materialised at
    send) and fixes what they mean, instead of leaving them to follow whatever
    a later cell writes to that filename.
    """
    assert "insert: version ? `${name}#${version}` : name" in APP_JS
    # And the pick tells the user when a file is about to be copied in, which
    # is the one thing a filename cannot show.
    assert APP_JS.count('"ac.fromOtherSession"') == 3


def test_unresolved_references_are_rendered_not_swallowed() -> None:
    """The server emits `artifact_ref_problems` precisely so the user learns a
    reference failed. Emitting it and then dropping it in the client would
    reproduce the original defect one layer up."""
    assert 'm.type === "artifact_ref_problems"' in APP_JS
    assert "function renderRefProblems(" in APP_JS
    assert APP_JS.count('"refs.problemsTitle"') == 3


def test_every_notebook_export_format_is_reachable_from_the_ui() -> None:
    """The export has always produced three things; the UI could ask for one.

    `notebook/export` accepts `python`, `r` and `bundle`, and the client
    hardcoded `?language=bundle`. Two working formats were unreachable — a user
    who wanted the Python notebook had to download a zip and unpack it, and
    nothing in the UI said the other options existed.
    """
    for language in ("bundle", "python", "r"):
        assert (
            f'language: "{language}"' in APP_JS
        ), f"the {language} export has no UI entry point"
    # The default action must stay what it was: one click, same file. Scoped to
    # the export table -- `{ language: "python" }` also appears in the variable
    # inspector's state, and an unscoped index comparison compares the wrong
    # occurrences and passes or fails for unrelated reasons.
    table = APP_JS[
        APP_JS.index("const NOTEBOOK_EXPORTS") : APP_JS.index(
            "function notebookExportLink"
        )
    ]
    assert table.index('language: "bundle"') < table.index(
        'language: "python"'
    ), "the bundle must remain the default action"
    assert "NOTEBOOK_EXPORTS[0]" in APP_JS
    for key in ("prov.exec.downloadPython", "prov.exec.downloadR"):
        assert (
            APP_JS.count(f'"{key}"') == 3
        ), f"{key} is missing from a translation table"


def test_the_retrieval_panel_renders_only_what_the_server_sent() -> None:
    """The client must not re-derive or re-format the provenance.

    Every value in it has already been through the server's allowlist, length
    cap and redaction. A client that reassembled a URL, or decided for itself
    which fields to show, would be a second implementation of the rule that
    keeps an API key out of the UI — and the two would drift.
    """
    assert "function retrievalSourcePanel(" in APP_JS
    # It renders a fixed field order, not `Object.keys(src)`: iterating the
    # payload would display any field a future server version adds, which is
    # the allowlist decision being made in the wrong place.
    assert "RETRIEVAL_FIELD_ORDER" in APP_JS
    assert "Object.keys(src)" not in APP_JS
    # Both notes are shown, because a clipped value rendered plain reads as the
    # whole value and withheld fields with no count read as absent ones.
    for key in ("versions.retrievalTruncated", "versions.retrievalWithheld"):
        assert (
            APP_JS.count(f'"{key}"') == 3
        ), f"{key} is missing from a translation table"


def test_the_context_panel_carries_the_servers_omission_report() -> None:
    """The server reports what a budget left out; the client has to keep it.

    `sanitizeContext` rebuilds the payload field by field, so anything it does
    not name is dropped on the floor. That is how a projection stays looking
    complete while quietly becoming partial — and it is the same shape as the
    retrieval `source` envelope, the specialist allowlist, and the notebook
    export formats: server-side work that reached nothing.
    """
    sanitizer = APP_JS[APP_JS.index("function sanitizeContext(") :]
    sanitizer = sanitizer[: sanitizer.index("\nfunction ")]
    assert "omitted:" in sanitizer, "the omission report is dropped by the normaliser"

    panel = APP_JS[APP_JS.index("function renderContextPanel(") :]
    panel = panel[: panel.index("\nfunction ")]
    assert "state.omitted" in panel, "the omission report is never rendered"


def test_an_optional_label_can_actually_fall_back() -> None:
    """`t()` returns the key itself when it is missing, which makes
    `t(key) || fallback` a dead branch — the fallback can never run and a user
    sees `context.omitted.images` rendered as text. Optional labels go through
    a lookup that can return null."""
    panel = APP_JS[APP_JS.index("function renderContextPanel(") :]
    panel = panel[: panel.index("\nfunction ")]
    for computed in ('t("context.omitted." +', 't("context.reason." +'):
        assert computed not in panel, f"{computed} can never fall back"
    assert 'tOptional("context.omitted." +' in panel
    assert 'tOptional("context.reason." +' in panel


def test_no_panel_calls_a_helper_that_does_not_exist() -> None:
    """`node --check` parses; it does not resolve names.

    Three calls in one new panel — `toast(...)`, `formatBytes(...)`, and
    `apiErrorText(e, fallback)` with an arity the function does not have —
    parsed cleanly and would have thrown the first time the panel rendered.
    The helpers were real, under other names (`hint`, `bytes`), which is why
    reading the code did not catch it either.

    Scoped to the workbench panels because that is where a throw blanks a
    surface the user opened deliberately, and because a whole-file sweep of a
    9,000-line script would drown the signal in browser globals.
    """
    import re

    known = set(re.findall(r"function\s+([A-Za-z_$][\w$]*)\s*\(", APP_JS))
    known |= set(re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", APP_JS))
    # Globals the browser supplies, plus the ones this file gets from vendor.
    known |= {
        "Array",
        "Boolean",
        "Date",
        "Error",
        "JSON",
        "Math",
        "Number",
        "Object",
        "Promise",
        "RegExp",
        "String",
        "Set",
        "Map",
        "URL",
        "URLSearchParams",
        "encodeURIComponent",
        "decodeURIComponent",
        "parseInt",
        "parseFloat",
        "isNaN",
        "setTimeout",
        "clearTimeout",
        "setInterval",
        "clearInterval",
        "fetch",
        "alert",
        "confirm",
        "prompt",
        "require",
        "import",
        "await",
        "if",
        "for",
        "while",
        "switch",
        "catch",
        "return",
        "typeof",
        "super",
        "function",
        "of",
        "in",
        "new",
        "$3Dmol",
    }

    missing: list[tuple[str, str]] = []
    for name in (
        "renderComputeTasksPanel",
        "refreshComputeTask",
        "sanitizeComputeTasks",
    ):
        start = APP_JS.index(f"function {name}(")
        body = APP_JS[start : APP_JS.index("\nfunction ", start + 1)]
        # `(?<![.\w$])` excludes method calls: `row.appendChild(...)` is a
        # property of an object, not a free identifier this file must define.
        for called in re.findall(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(", body):
            if called not in known and not called.startswith("_"):
                missing.append((name, called))
    assert not missing, f"undefined helpers called: {missing}"


def test_every_icon_a_button_asks_for_exists() -> None:
    """`ghostIconBtn("square", …)` renders a button with nothing in it.

    `icon()` looks the name up in `ICONS`; a miss is not an error, it is an
    empty string, so the control is present, clickable, and invisible. That is
    worse than a broken button, because nothing in the page or the console says
    anything is wrong — and `node --check` cannot see it either.
    """
    import re

    block = APP_JS[APP_JS.index("const ICONS = {") :]
    block = block[: block.index("\n};")]
    known = set(re.findall(r'^\s*"?([\w-]+)"?\s*:', block, re.M))
    asked = set(re.findall(r'ghostIconBtn\(\s*"([\w-]+)"', APP_JS))
    asked |= set(re.findall(r'\bicon\(\s*"([\w-]+)"', APP_JS))
    missing = sorted(asked - known)
    assert not missing, f"buttons ask for icons that do not exist: {missing}"


def test_the_delegation_controls_reach_their_routes() -> None:
    """The routes exist so a user can stop a runaway sub-agent. A panel that
    renders the tree and offers nothing leaves them where they started —
    which is the state this work was opened to fix, and exactly the shape
    (server-side capability, no client call site) that this file has caught
    for retrieval provenance, notebook exports and skill allowlists.
    """
    panel = APP_JS[APP_JS.index("function renderDelegationPanel(") :]
    panel = panel[: panel.index("\nfunction ")]
    assert "stopDelegationChild(" in panel
    assert "steerDelegationChild(" in panel

    for name, path in (
        ("stopDelegationChild", "/stop"),
        ("steerDelegationChild", "/steer"),
    ):
        body = APP_JS[APP_JS.index(f"async function {name}(") :]
        body = body[: body.index("\n}")]
        assert "/delegations/" in body and path in body
        assert (
            'method: "POST"' in body
        ), "a control that mutates a run must not be a GET"


def test_a_finished_sub_agent_is_not_offered_a_control_that_cannot_work() -> None:
    """After a daemon restart every child in the record is `stopped`. Offering
    Stop there produces a 409 the user can do nothing about, so the buttons are
    gated on a status that can still act."""
    panel = APP_JS[APP_JS.index("function renderDelegationPanel(") :]
    panel = panel[: panel.index("\nfunction ")]
    gate = panel[
        panel.index("delegation-child-controls")
        - 400 : panel.index("delegation-child-controls")
    ]
    assert "running" in gate and "pending" in gate


def test_the_attachment_problem_event_reaches_the_user() -> None:
    """The server emits `attachment_problems` to say which pinned figures were
    left out of a turn, and nothing listened for it.

    The model is told separately in a system note, so the assistant usually
    mentions it — but only usually, and never with the reason, the limit, or
    what to do instead. A pin the user placed and the model never received
    reads as "the model is broken" rather than "that figure was too large".
    """
    assert '"attachment_problems"' in APP_JS, "the event type is never matched"

    dispatch = APP_JS[APP_JS.index('m.type === "attachment_problems"') :][:200]
    assert "renderAttachmentProblems" in dispatch

    body = APP_JS[APP_JS.index("function renderAttachmentProblems(") :]
    body = body[: body.index("\nfunction ")]
    # Every reason the server can send has wording here; an unhandled one would
    # render the raw enum to a user.
    for reason in ("too_large", "budget_exhausted", "too_many"):
        assert reason in body, f"no wording for {reason}"


def test_every_attachment_reason_the_server_sends_is_handled() -> None:
    """Read the reasons out of the gateway rather than listing them here, so a
    new one added server-side fails this instead of reaching a user as a bare
    identifier."""
    import re
    from pathlib import Path

    gateway = Path("openai4s/server/gateway.py").read_text(encoding="utf-8")
    # Bounded to the block that builds this list. A wider slice picked up
    # `"reason"` keys from unrelated features — quarantine records, review
    # state — and the test failed for reasons that were never attachment
    # problems at all.
    block = gateway[gateway.index("dropped: list[dict] = []") :]
    block = block[: block.index('"type": "attachment_problems"')]
    # `_pinned_image_bytes` refuses on the version binding before a budget is
    # ever reached, so its reasons are attachment reasons too. Without this
    # slice the test would keep passing while four of the seven reasons the
    # server can send had no wording at all.
    binding = gateway[gateway.index("def _pinned_image_bytes(") :]
    binding = binding[: binding.index("def _figure_with_pins(")]
    reasons = set(re.findall(r'"reason":\s*"([a-z_]+)"', block + binding))
    assert reasons, "the attachment budget no longer reports reasons"

    body = APP_JS[APP_JS.index("function renderAttachmentProblems(") :]
    body = body[: body.index("\nfunction ")]
    missing = sorted(r for r in reasons if r not in body)
    assert not missing, f"the client has no wording for: {missing}"


# --- a failed turn's identity, in the browser ---------------------------------
#
# The backend mutations for this feature are backend mutations: they say
# nothing about whether the JS reads the fields. These are the JS half. They
# are source contracts, not behaviour -- browser acceptance is owed on top --
# but each one fails if the branch it names is removed.


def _fn(name: str) -> str:
    """The body of one top-level function, for assertions scoped to it."""
    start = APP_JS.index(f"function {name}(")
    # Keep the `async` keyword: lifted without it, a body containing `await`
    # is a syntax error, and the test then fails for a reason that has nothing
    # to do with what it asserts.
    if APP_JS[max(0, start - 6) : start] == "async ":
        start -= 6
    depth = 0
    for index in range(APP_JS.index("{", start), len(APP_JS)):
        if APP_JS[index] == "{":
            depth += 1
        elif APP_JS[index] == "}":
            depth -= 1
            if depth == 0:
                return APP_JS[start : index + 1]
    raise AssertionError(f"{name} is not a closed function")


def test_a_live_failure_shows_the_request_id_the_server_named() -> None:
    """Without this the user is told to quote an id they were never shown."""
    hint = _fn("failureHint")
    assert "turn.supportId" in hint, hint
    assert "request_id" in hint
    # The 202 is the fallback: a terminal event that arrives without an id
    # still has one, because `wait:false` means the 202 was the only
    # synchronous thing this client received.
    assert "pendingRequestId" in hint
    assert '"turn.supportId"' in APP_JS  # zh
    assert APP_JS.count('"turn.supportId"') >= 2  # and en


def test_the_committed_wording_is_a_real_branch() -> None:
    """ "Please try again" is the wrong advice once a tool has already run."""
    hint = _fn("failureHint")
    assert "output_committed" in hint
    assert "turn.failedCommitted" in hint and "turn.failed" in hint
    assert APP_JS.count('"turn.failedCommitted"') >= 2


def test_typed_llm_failures_keep_their_cause_in_live_and_reopened_hints() -> None:
    """A burst refusal must not decay back into the generic retry/key advice.

    The live terminal event and the stored message take different paths.  Both
    have to retain the existing ``code`` field or reopening the same failed
    session gives a less accurate explanation than the one originally shown.
    """
    classifier = _fn("failureCodeHint")
    for code in (
        "llm_request_burst",
        "llm_rate_limited",
        "llm_upstream_overloaded",
    ):
        assert code in classifier
    for key in (
        "turn.failure.llmRequestBurst",
        "turn.failure.llmRateLimited",
        "turn.failure.llmUpstreamOverloaded",
    ):
        assert APP_JS.count(f'"{key}"') >= 2, f"{key} needs zh and en wording"

    hint = _fn("failureHint")
    assert "failureCodeHint" in hint and "detail.code" in hint
    # The retry veto still wins when a prior action already happened.
    assert "turn.failedCommitted" in hint

    meta = _fn("failureMeta")
    assert "failure.code" in meta and "failureCode" in meta
    last = _fn("lastTerminalFailure")
    assert "failureCode" in last and "code:" in last


def test_turn_done_passes_the_event_through_and_retires_the_ticket() -> None:
    """A ticket outliving its turn lets one turn's id be quoted on the next."""
    done = _fn("turnDone")
    assert "failureHint(detail)" in done, done
    assert "closeTurnTicket()" in done, done
    assert "turnDone(m.status, m)" in APP_JS, "the event is dropped before the hint"


def test_a_stored_failure_is_inline_and_never_the_global_hint() -> None:
    """A session renders oldest-first and prepends older pages later.

    Calling `hint()` from the row renderer therefore lets any past failure --
    including one several successful turns ago, or one on a page the reader
    scrolled back to -- become the current state of the whole UI.
    """
    stored = _fn("renderStored")
    assert "failureMeta(m.failure)" in stored, stored
    code = "\n".join(
        line for line in stored.splitlines() if not line.strip().startswith("//")
    )
    assert "hint(" not in code, "a rendered row is changing global UI state"
    assert ".msg-failure-meta" in STYLE_CSS


def test_the_inline_failure_carries_the_id_and_the_veto() -> None:
    meta = _fn("failureMeta")
    assert "turn.supportId" in meta and "turn.failedCommitted" in meta
    assert "requestId" in meta and "committed" in meta


def test_the_global_hint_is_restored_only_for_a_currently_failed_session() -> None:
    """Once, from the frame's own status -- not from any stored failure.

    `running: false` covers completed, cancelled and failed alike, so the
    restore reads `status`, which `GET /frames/{id}/status` reports for exactly
    this reason.
    """
    open_conv = _fn("openConversation")
    assert 'stt.status === "failed"' in open_conv, open_conv
    assert "lastTerminalFailure()" in open_conv
    # And it is the LAST message that decides, not any of them.
    last = _fn("lastTerminalFailure")
    assert "rows[rows.length - 1]" in last, last


def test_the_pending_ticket_does_not_outlive_its_session() -> None:
    open_conv = _fn("openConversation")
    assert "closeTurnTicket()" in open_conv, open_conv


def test_send_captures_the_ticket_the_202_named() -> None:
    """The one place the id can enter the client at all.

    `send()` posts `wait: false`, so the 202 is the only thing this client
    receives synchronously -- "accepted, watch elsewhere". If the ticket is not
    taken from that body here, `failureHint`'s fallback has nothing to fall
    back to and a terminal event arriving without an id shows none.

    Scoped to `send` on purpose: the cleanup and hint tests below assert that
    `S.pendingRequestId` is *read* and *cleared*, and both stay green when
    nothing ever writes it -- an id that is always empty is cleared correctly
    and read correctly, and is still absent from the screen.
    """
    body = _fn("send")

    assert "wait: false" in body, "send no longer posts the ticketed form"
    assert (
        "acceptTurnTicket(turnTicket, accepted)" in body
    ), "the 202's request id is never stored, so the fallback is dead"
    # Taken BEFORE the message POST is awaited, or the guard has nothing to
    # compare against. Anchored to that POST specifically: `send` awaits other
    # calls first (creating the frame, reconciling annotations).
    post = body.index("await api(`/frames/${S.currentId}/message`")
    assert body.index("openTurnTicket()") < post, body[max(0, post - 400) : post]
    # From the awaited POST, not from some other object that happens to carry
    # the name.
    assert re.search(
        r"const accepted = await api\(`/frames/\$\{S\.currentId\}/message`", body
    ), body[:800]


# --- the ticket race, driven rather than read ---------------------------------
#
# A string contract cannot show an ordering. The job runs on its own thread and
# can fail before the handler returns the 202, so the terminal WS event -- and
# `turnDone` with it -- can arrive first, clear the ticket, and then `send`'s
# POST promise resolves and writes the finished turn's id back. These run the
# shipped functions under node in that exact order.

import json
import shutil
import subprocess

NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="no node on this machine")
def test_search_result_http_url_rejects_executable_and_relative_schemes() -> None:
    source = _extract_js_function(APP_JS, "searchResultHttpUrl")
    cases = [
        [" https://Example.com/A?X=Y ", "https://Example.com/A?X=Y"],
        ["HTTPS://Example.com/A?X=Y", "https://Example.com/A?X=Y"],
        ["hTtP://Example.com/A", "http://Example.com/A"],
        ["javascript:alert(1)", ""],
        ["data:text/html,<script>alert(1)</script>", ""],
        ["//evil.example/path", ""],
        ["ftp://evil.example/path", ""],
        ["https:/missing-slash.example", ""],
        [None, ""],
        [42, ""],
    ]
    script = (
        source
        + "\nconst cases = "
        + json.dumps(cases)
        + ";\nconsole.log(JSON.stringify(cases.map(([input]) => "
        + "searchResultHttpUrl(input))));"
    )
    out = subprocess.run(
        [NODE, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr[:800]
    assert json.loads(out.stdout) == [expected for _, expected in cases]


def _drive(program: str) -> dict:
    """Run the shipped ticket functions, lifted from app.js, under node.

    Lifted rather than reimplemented, for the same reason the R producer tests
    lift `.oai4s_cap_message`: a hand-written stand-in is the thing that drifts.
    """
    sources = "\n".join(
        _fn(name)
        for name in (
            "openTurnTicket",
            "commitTurnTicket",
            "closeTurnTicket",
            "activateTurnTicket",
            "ownsTurnTicket",
            "acceptTurnTicket",
            "retireTurnTicket",
            "isStaleTurnEvent",
        )
    )
    script = f"const S = {{ running: false }};\n{sources}\n{program}"
    out = subprocess.run(
        [NODE, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr[:800]
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(NODE is None, reason="no node on this machine")
def test_a_202_that_resolves_after_the_turn_failed_writes_nothing() -> None:
    """The race, in the order that actually happens.

    Terminal WS first, 202 second. Before the guard the slot ended up holding a
    finished turn's id, and the *next* turn quoted it -- sending an operator to
    the wrong request, which is worse than showing none.
    """
    state = _drive("""
        S.running = true;
        const ticket = openTurnTicket();          // send() takes it, then awaits
        closeTurnTicket();                        // terminal WS wins the race
        S.running = false;
        const wrote = commitTurnTicket(ticket, { request_id: "req-old" });
        console.log(JSON.stringify({ wrote, pending: S.pendingRequestId }));
        """)
    assert state["wrote"] is False
    assert state.get("pending") in (None, ""), state


@pytest.mark.skipif(NODE is None, reason="no node on this machine")
def test_closing_the_ticket_alone_is_enough_to_refuse_a_late_202() -> None:
    """Isolates the generation half.

    `S.running` is left true, so only the generation moved. Without this the
    test above passes on a `closeTurnTicket` that merely nulls the slot and
    never advances the generation -- and then a late 202 writes the id straight
    back in, which is the whole defect.
    """
    state = _drive("""
        S.running = true;
        const ticket = openTurnTicket();
        closeTurnTicket();
        const wrote = commitTurnTicket(ticket, { request_id: "req-old" });
        console.log(JSON.stringify({ wrote, pending: S.pendingRequestId }));
        """)
    assert state["wrote"] is False, "a closed ticket still accepted its 202"
    assert state.get("pending") in (None, ""), state


@pytest.mark.skipif(NODE is None, reason="no node on this machine")
def test_a_turn_that_is_no_longer_running_refuses_its_own_202() -> None:
    """Isolates the running half.

    The generation is untouched, so only `S.running` can refuse. `turnDone`
    clears `S.running` at its first statement and closes the ticket near its
    last, so this is the window between them -- narrow today, and the kind of
    thing a later edit widens without noticing.
    """
    state = _drive("""
        S.running = true;
        const ticket = openTurnTicket();
        S.running = false;                        // turn ended, ticket not yet closed
        const wrote = commitTurnTicket(ticket, { request_id: "req-old" });
        console.log(JSON.stringify({ wrote, pending: S.pendingRequestId }));
        """)
    assert state["wrote"] is False, "an ended turn still stored its 202"
    assert state.get("pending") in (None, ""), state


@pytest.mark.skipif(NODE is None, reason="no node on this machine")
def test_the_next_turn_does_not_inherit_the_previous_ticket() -> None:
    """A turn with no id of its own must quote nothing, not the last one."""
    state = _drive("""
        S.running = true;
        const first = openTurnTicket();
        commitTurnTicket(first, { request_id: "req-first" });
        closeTurnTicket();                        // first turn ends
        S.running = true;
        openTurnTicket();                         // second turn starts
        const late = commitTurnTicket(first, { request_id: "req-first" });
        console.log(JSON.stringify({ late, pending: S.pendingRequestId }));
        """)
    assert state["late"] is False, "a stale 202 revived the previous turn's id"
    assert state.get("pending") in (None, ""), state


@pytest.mark.skipif(NODE is None, reason="no node on this machine")
def test_the_ordinary_ordering_still_stores_the_ticket() -> None:
    """The guard must not make every turn id-less, or it is just a deletion."""
    state = _drive("""
        S.running = true;
        const ticket = openTurnTicket();
        const wrote = commitTurnTicket(ticket, { request_id: "req-live" });
        console.log(JSON.stringify({ wrote, pending: S.pendingRequestId }));
        """)
    assert state["wrote"] is True
    assert state["pending"] == "req-live"


# --- queued follow-ups ---------------------------------------------------------


def test_a_queued_send_takes_no_ticket() -> None:
    """A follow-up is not the running turn.

    Its 202 resolves while the previous turn still owns the screen, so writing
    its id into the slot makes the *active* turn's failure quote it -- an
    operator sent to a request that had not started.
    """
    body = _fn("send")
    assert "const sawRunningAtDispatch = S.running;" in body, body[:900]
    assert "sawRunningAtDispatch ? null : openTurnTicket()" in body
    # The snapshot from the top of `send` may not decide this: by the time the
    # POST goes out it is stale, and `S.running` has been set by this send.
    assert "queueing ? null" not in body
    assert (
        "if (!acceptTurnTicket(turnTicket, accepted)) retireTurnTicket(turnTicket)"
        in body
    )


def test_a_rejected_send_only_tears_down_a_turn_it_owns() -> None:
    """`queueing` is a snapshot, and the catch runs long after it was taken."""
    body = _fn("send")
    assert 'if (ownsTurnTicket(turnTicket)) turnDone("failed")' in body, body[-2500:]
    assert 'if (queueing) w.classList.add("cancelled");' not in body


def test_the_processing_event_hands_the_slot_over() -> None:
    """And unconditionally: `S.running` is already true when a queued turn starts."""
    assert (
        'if (m.status === "processing") activateTurnTicket(m.request_id, m.execution_id);'
        in APP_JS
    )
    index = APP_JS.index('if (m.status === "processing") activateTurnTicket')
    guarded = APP_JS.index('if (m.status === "processing" && !S.running)')
    assert index < guarded, "the hand-off sits inside the not-running guard"


@pytest.mark.skipif(NODE is None, reason="no node on this machine")
def test_only_queue_position_zero_proves_this_send_is_the_active_turn() -> None:
    """Every acceptance shape, through the shipped helper.

    `> 0` is queued behind someone else. Absent means the snapshot could not be
    taken -- typically a job that finished before it was read -- and an unknown
    is not a yes. Both used to write, and both put a non-running turn's id in
    the slot the running turn's failure quotes.
    """
    state = _drive("""
        const out = {};
        const run = (label, accepted) => {
          S.running = true;
          closeTurnTicket();
          const token = openTurnTicket();
          commitTurnTicket(token, { request_id: "req-ACTIVE" });
          out[label] = {
            wrote: acceptTurnTicket(token, accepted),
            pending: S.pendingRequestId,
          };
        };
        run("zero", { request_id: "req-NEW", queue_position: 0 });
        run("queued", { request_id: "req-NEW", queue_position: 3 });
        run("absent", { request_id: "req-NEW" });
        run("noId", { queue_position: 0 });
        console.log(JSON.stringify(out));
        """)
    assert state["zero"] == {"wrote": True, "pending": "req-NEW"}
    assert state["queued"] == {"wrote": False, "pending": "req-ACTIVE"}
    assert state["absent"] == {"wrote": False, "pending": "req-ACTIVE"}
    assert state["noId"] == {"wrote": False, "pending": "req-ACTIVE"}


@pytest.mark.skipif(NODE is None, reason="no node on this machine")
def test_a_stale_or_absent_token_can_never_claim_the_slot() -> None:
    """The two ways a send stops owning the turn: superseded, or never owned it.

    `null` is the queued send, which took no ticket at all. Stale is the send
    whose turn ended -- or whose slot another turn's `processing` claimed --
    while its POST was still in flight.
    """
    state = _drive("""
        S.running = true;
        const token = openTurnTicket();
        commitTurnTicket(token, { request_id: "req-A" });
        activateTurnTicket("req-B");                     // another turn started
        const stale = acceptTurnTicket(token, { request_id: "req-A", queue_position: 0 });
        const queued = acceptTurnTicket(null, { request_id: "req-C", queue_position: 0 });
        console.log(JSON.stringify({
          stale, queued, owns: ownsTurnTicket(token), pending: S.pendingRequestId
        }));
        """)
    assert state["stale"] is False, "a superseded send reclaimed the slot"
    assert state["queued"] is False, "a queued send with no ticket claimed the slot"
    assert state["owns"] is False
    assert state["pending"] == "req-B"


@pytest.mark.skipif(NODE is None, reason="no node on this machine")
def test_two_queued_202s_in_any_order_leave_the_active_id_alone() -> None:
    """Driven, not asserted from a comment.

    Both follow-ups took no ticket and both are answered as queued, so each is
    refused twice over. Order does not matter, which is the point: they resolve
    whenever the network says.
    """
    state = _drive("""
        S.running = true;
        const a = openTurnTicket();
        commitTurnTicket(a, { request_id: "req-A" });
        const generationBefore = S.turnTicket;

        const wrote = [
          { request_id: "req-C", queue_position: 2 },
          { request_id: "req-B", queue_position: 1 },
        ].map(accepted => acceptTurnTicket(null, accepted));

        console.log(JSON.stringify({
          wrote,
          sameGeneration: generationBefore === S.turnTicket,
          pending: S.pendingRequestId,
          stillLive: acceptTurnTicket(a, { request_id: "req-A", queue_position: 0 })
        }));
        """)
    assert state["wrote"] == [False, False]
    assert state["sameGeneration"] is True, "a queued send advanced the generation"
    assert state["pending"] == "req-A"
    assert state["stillLive"] is True, "the running turn's own ticket was invalidated"


@pytest.mark.skipif(NODE is None, reason="no node on this machine")
def test_a_rejected_follow_up_does_not_tear_down_the_running_turn() -> None:
    """The `false -> true` window, which the snapshot gets wrong.

    A send begins while the session is idle, so it takes a ticket. Before its
    POST is answered, another turn starts and its `processing` claims the slot.
    The POST then fails. Deciding from `queueing` (still false) tears down the
    turn that is actually running; deciding from ownership does not.
    """
    state = _drive("""
        S.running = true;
        const mine = openTurnTicket();       // began idle, took a ticket
        activateTurnTicket("req-OTHER");     // someone else's turn started
        console.log(JSON.stringify({
          tearsDown: ownsTurnTicket(mine),
          pending: S.pendingRequestId
        }));
        """)
    assert (
        state["tearsDown"] is False
    ), "a rejected follow-up would tear down a live turn"
    assert state["pending"] == "req-OTHER"


@pytest.mark.skipif(NODE is None, reason="no node on this machine")
def test_ownership_is_false_before_any_turn_has_started() -> None:
    """`undefined === undefined` is the trap.

    `S.turnTicket` is unset until the first send, so a bare `token ===
    S.turnTicket` answers *true* for a caller that passes nothing -- and the
    catch would tear down a turn that does not exist.
    """
    state = _drive("""
        S.turnTicket = undefined;
        console.log(JSON.stringify({
          nothing: ownsTurnTicket(undefined),
          queued: ownsTurnTicket(null),
          first: (() => { const t = openTurnTicket(); return ownsTurnTicket(t); })()
        }));
        """)
    assert state["nothing"] is False, "a caller with no ticket owned the turn"
    assert state["queued"] is False
    assert state["first"] is True


def test_no_correctness_branch_reads_the_dispatch_snapshot() -> None:
    """`queueing` may style the bubble; it may not decide who owns the turn.

    Every later read of it is stale by construction -- and one of them, the
    rebind prompt, is modal, so the window is however long the user takes.
    """
    body = _fn("send")
    for branch in (
        "if (S.running && !queueing) turnDone",
        'else if (S.running) turnDone("failed")',
        'if (queueing) w.classList.add("cancelled")',
    ):
        assert branch not in body, branch
    assert body.count("ownsTurnTicket(turnTicket)") >= 2, body[-3000:]


@pytest.mark.skipif(NODE is None, reason="no node on this machine")
def test_a_ticket_retired_by_a_queued_answer_can_never_claim_the_slot() -> None:
    """`queue_position: 1` on a send that thought it was active.

    The ticket was taken in good faith at dispatch, and the server's answer
    says otherwise. Retiring it is what stops a later resolution -- a retry, a
    duplicated promise -- from using it, and `pendingRequestId` is left alone
    because it does not belong to this turn.
    """
    state = _drive("""
        S.running = false;
        const token = openTurnTicket();
        S.running = true;                                  // this send locked the UI
        const accepted = { request_id: "req-B", queue_position: 1 };
        const wrote = acceptTurnTicket(token, accepted);
        const retired = retireTurnTicket(token);
        console.log(JSON.stringify({
          wrote, retired,
          ownsAfter: ownsTurnTicket(token),
          reclaim: acceptTurnTicket(token, { request_id: "req-B", queue_position: 0 })
        }));
        """)
    assert state["wrote"] is False
    assert state["retired"] is True
    assert state["ownsAfter"] is False
    assert state["reclaim"] is False, "a retired ticket claimed the slot later"


@pytest.mark.skipif(NODE is None, reason="no node on this machine")
def test_retiring_never_touches_a_turn_it_does_not_own() -> None:
    state = _drive("""
        S.running = true;
        const mine = openTurnTicket();
        activateTurnTicket("req-OTHER");        // another turn took over
        const retired = retireTurnTicket(mine);
        console.log(JSON.stringify({ retired, pending: S.pendingRequestId }));
        """)
    assert state["retired"] is False
    assert state["pending"] == "req-OTHER", "retiring cleared another turn's id"


# --- a terminal event for a turn that is no longer on screen -------------------


def test_every_late_turn_event_goes_through_the_stale_filter() -> None:
    """Prose as well as the terminal.

    A failure that arrives after the next turn started would otherwise wipe the
    running turn's stream and print its predecessor's error into it.
    """
    assert "if (mine(fid) && !isStaleTurnEvent(m)) startStream();" in APP_JS
    assert "if (mine(fid) && !isStaleTurnEvent(m)) feed(" in APP_JS
    assert "if (isStaleTurnEvent(m)) scheduleWorkbenchRefresh();" in APP_JS
    assert "activateTurnTicket(m.request_id, m.execution_id)" in APP_JS


@pytest.mark.skipif(NODE is None, reason="no node on this machine")
def test_an_idless_processing_retires_tickets_without_forgetting_who_is_running():
    """An older daemon sends `processing` with no ids at all.

    The generation must still advance -- another turn is running, so every
    ticket in flight is stale. But clearing the identity there throws away what
    the 202 already gave us, leaving the running turn's own failure with
    nothing to quote and nothing to filter on.
    """
    state = _drive("""
        S.running = true;
        const token = openTurnTicket();
        commitTurnTicket(token, { request_id: "req-A", execution_id: "exec-A" });
        const before = S.turnTicket;
        activateTurnTicket(undefined, undefined);
        console.log(JSON.stringify({
          bumped: S.turnTicket === before + 1,
          request: S.pendingRequestId,
          execution: S.pendingExecutionId,
          stale: ownsTurnTicket(token)
        }));
        """)
    assert state["bumped"] is True, "an idless processing left stale tickets valid"
    assert state["request"] == "req-A", "the running turn's id was forgotten"
    assert state["execution"] == "exec-A"
    assert state["stale"] is False


@pytest.mark.skipif(NODE is None, reason="no node on this machine")
def test_a_late_terminal_from_the_previous_execution_is_stale() -> None:
    """processing(A) -> processing(B) -> failed(A), the order that reproduces.

    A fails inside the turn, persists its row, and finishes unwinding only
    after B has been promoted out of the queue. Acting on A's terminal closes
    B's turn and unlocks the composer under a turn that is still running.
    """
    state = _drive("""
        activateTurnTicket("req-A", "exec-A");
        activateTurnTicket("req-B", "exec-B");
        console.log(JSON.stringify({
          lateA: isStaleTurnEvent({ request_id: "req-A", execution_id: "exec-A" }),
          ownB: isStaleTurnEvent({ request_id: "req-B", execution_id: "exec-B" })
        }));
        """)
    assert state["lateA"] is True, "A's terminal would have closed B's turn"
    assert state["ownB"] is False, "B's own terminal was refused"


@pytest.mark.skipif(NODE is None, reason="no node on this machine")
def test_a_reused_request_id_is_still_told_apart_by_execution() -> None:
    """Clients may reuse `X-Request-Id`, so A and B can share one.

    A filter that compared only request ids would call A's late terminal
    current, which is the whole reason the execution id is on the wire.
    """
    state = _drive("""
        activateTurnTicket("req-same", "exec-A");
        activateTurnTicket("req-same", "exec-B");
        console.log(JSON.stringify({
          lateA: isStaleTurnEvent({ request_id: "req-same", execution_id: "exec-A" }),
          ownB: isStaleTurnEvent({ request_id: "req-same", execution_id: "exec-B" })
        }));
        """)
    assert state["lateA"] is True, "two turns sharing a request id were confused"
    assert state["ownB"] is False


@pytest.mark.skipif(NODE is None, reason="no node on this machine")
def test_an_older_daemon_without_identities_still_closes_its_turns() -> None:
    """The filter must not strand a client talking to a server that predates it.

    Neither side offering any identity is the pre-identity contract, and
    treating that as stale would leave every turn open forever. One side silent
    is the mixed case -- also not evidence of staleness.
    """
    state = _drive("""
        const out = {};
        closeTurnTicket();
        out.bothSilent = isStaleTurnEvent({});
        activateTurnTicket("req-A", undefined);          // daemon sends no exec id
        out.reqOnlyMatch = isStaleTurnEvent({ request_id: "req-A" });
        out.reqOnlyOther = isStaleTurnEvent({ request_id: "req-Z" });
        out.execArrivesLater = isStaleTurnEvent({ execution_id: "exec-A" });
        console.log(JSON.stringify(out));
        """)
    assert state["bothSilent"] is False
    assert state["reqOnlyMatch"] is False
    assert state["reqOnlyOther"] is True, "the request-id fallback stopped working"
    assert state["execArrivesLater"] is False, "one side silent is not staleness"


# --- plan turns take a ticket too ----------------------------------------------


def test_the_three_plan_turns_share_one_generation_owned_dispatch() -> None:
    """Each used to lock the UI after its own 202, or without a ticket at all."""
    for name in ("approvePlan", "resumePlan", "revisePlan"):
        assert "dispatchPlanTurn(" in _fn(name), name
    body = _fn("dispatchPlanTurn")
    # The ticket and the lock come BEFORE the POST, or a terminal event that
    # beats the 202 cannot invalidate anything.
    assert body.index("openTurnTicket()") < body.index("await api("), body
    assert body.index("S.running = true") < body.index("await api(")
    assert "if (!ownsTurnTicket(token)) return true;" in body
    assert "commitTurnTicket(token, accepted || {})" in body
    assert 'if (ownsTurnTicket(token)) turnDone("failed");' in body


def test_approve_still_leaves_plan_mode_on_a_failed_dispatch() -> None:
    """The toggle follows acceptance; a refused approve is still a draft."""
    body = _fn("approvePlan")
    assert "if (await dispatchPlanTurn(" in body, body
    assert "S.planMode = false" in body


# --- the plan dispatcher, actually executed -----------------------------------
#
# Lifting the ticket helpers and simulating what `dispatchPlanTurn` *would* do
# is not evidence about `dispatchPlanTurn`: those tests stay green while the
# shipped action is wrong. These run it, against a real pending promise.


def _drive_plan(program: str) -> dict:
    """Run the shipped `dispatchPlanTurn` under node with a deferred `api`."""
    lifted = "\n".join(
        _fn(name)
        for name in (
            "openTurnTicket",
            "commitTurnTicket",
            "closeTurnTicket",
            "activateTurnTicket",
            "ownsTurnTicket",
            "retireTurnTicket",
            "isStaleTurnEvent",
            "dispatchPlanTurn",
        )
    )
    harness = """
const S = { currentId: "frame-1", running: false, _openGen: 3 };
const calls = { api: 0, resume: 0, turnDone: 0, hint: 0 };
let settle = null;
const api = (_path, _opts) => {
  calls.api += 1;
  return new Promise((resolve, reject) => { settle = { resolve, reject }; });
};
const $ = () => ({ classList: { add() {}, remove() {} } });
const hint = () => { calls.hint += 1; };
const t = (key) => key;
const apiErrorText = (e) => String(e && e.message ? e.message : e);
const enableComposer = () => {};
const resumeWatch = () => { calls.resume += 1; };
const turnDone = (_status) => { calls.turnDone += 1; S.running = false; closeTurnTicket(); };
const tick = () => new Promise(r => setTimeout(r, 0));
"""
    out = subprocess.run(
        [NODE, "--input-type=module", "-e", harness + lifted + "\n" + program],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr[:1000]
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(NODE is None, reason="no node on this machine")
def test_a_plan_dispatch_while_a_turn_runs_is_refused_outright() -> None:
    """A double-click, or a plan control pressed during someone else's turn.

    A second ticket makes the newer request the owner: a 409 then tears down
    the turn that is actually running, and an acceptance replaces its identity
    so its own terminal event is judged stale and never closes it.
    """
    state = _drive_plan("""
        activateTurnTicket("req-A", "exec-A");
        S.running = true;
        const generation = S.turnTicket;
        const refused = await dispatchPlanTurn("/plan/approve", {}, "h", "k");
        console.log(JSON.stringify({
          refused, api: calls.api, turnDone: calls.turnDone,
          sameGeneration: S.turnTicket === generation,
          running: S.running,
          pending: S.pendingRequestId, execution: S.pendingExecutionId
        }));
        """)
    assert state["refused"] is False
    assert state["api"] == 0, "a second plan request was sent during a running turn"
    assert state["turnDone"] == 0
    assert state["sameGeneration"] is True
    assert state["running"] is True
    assert state["pending"] == "req-A" and state["execution"] == "exec-A"


@pytest.mark.skipif(NODE is None, reason="no node on this machine")
def test_a_second_plan_click_while_the_first_is_in_flight_sends_nothing() -> None:
    """The first POST is still pending; the button is pressed again."""
    state = _drive_plan("""
        const first = dispatchPlanTurn("/plan/approve", {}, "h", "k");
        await tick();
        const second = await dispatchPlanTurn("/plan/approve", {}, "h", "k");
        settle.resolve({ request_id: "req-1", execution_id: "exec-1" });
        const firstResult = await first;
        console.log(JSON.stringify({
          second, firstResult, api: calls.api,
          pending: S.pendingRequestId, execution: S.pendingExecutionId
        }));
        """)
    assert state["second"] is False
    assert state["api"] == 1, "the second click sent its own request"
    assert state["firstResult"] is True
    assert state["pending"] == "req-1"
    assert state["execution"] == "exec-1"


@pytest.mark.skipif(NODE is None, reason="no node on this machine")
def test_a_terminal_before_the_plan_202_does_not_relock_the_composer() -> None:
    """The stuck-session case, run rather than simulated."""
    state = _drive_plan("""
        const pending = dispatchPlanTurn("/plan/approve", {}, "h", "k");
        await tick();
        turnDone("failed");                       // the WS beat the 202
        settle.resolve({ request_id: "req-late", execution_id: "exec-late" });
        await pending;
        console.log(JSON.stringify({
          running: S.running, resume: calls.resume,
          pending: S.pendingRequestId, execution: S.pendingExecutionId
        }));
        """)
    assert state["running"] is False, "the late 202 locked the composer again"
    assert state["resume"] == 0, "the watchdog was re-armed for a finished turn"
    assert state.get("pending") in (None, "")
    assert state.get("execution") in (None, "")


@pytest.mark.skipif(NODE is None, reason="no node on this machine")
def test_a_rejected_plan_post_does_not_end_the_turn_that_took_over() -> None:
    state = _drive_plan("""
        const pending = dispatchPlanTurn("/plan/approve", {}, "h", "k");
        await tick();
        activateTurnTicket("req-B", "exec-B");     // B took over mid-flight
        settle.reject(new Error("409 plan_not_paused"));
        await pending;
        console.log(JSON.stringify({
          turnDone: calls.turnDone, running: S.running,
          pending: S.pendingRequestId, execution: S.pendingExecutionId
        }));
        """)
    assert state["turnDone"] == 0, "a rejected plan POST ended another turn"
    assert state["running"] is True
    assert state["pending"] == "req-B" and state["execution"] == "exec-B"


@pytest.mark.skipif(NODE is None, reason="no node on this machine")
def test_a_legacy_202_without_ids_still_leaves_the_turn_running() -> None:
    """An older daemon answers with neither id, and its turn must still work."""
    state = _drive_plan("""
        const pending = dispatchPlanTurn("/plan/approve", {}, "h", "k");
        await tick();
        settle.resolve({ status: "accepted", job_id: "j-1" });
        await pending;
        const before = { running: S.running, resume: calls.resume };
        const stale = isStaleTurnEvent({ type: "frame_update", status: "failed" });
        if (!stale) turnDone("failed");
        console.log(JSON.stringify({ before, stale, running: S.running }));
        """)
    assert state["before"] == {"running": True, "resume": 1}
    assert state["stale"] is False, "an idless terminal could never close the turn"
    assert state["running"] is False


def test_action_timeline_ledger_row_reports_state_it_cannot_fabricate() -> None:
    """The row must not present unknown state as a known value.

    Three of these were regressions against the card the ledger replaced:
    status and the attempt error were reduced to an icon colour, an absent
    usage row printed a fabricated ``0``, and a time brush resolved through the
    paint model so an action with no execution attempt vanished from the list.
    """

    row = _extract_js_function(APP_JS, "actionTimelineLedgerRow")
    overlap = _extract_js_function(APP_JS, "actionTimelineSelectionOverlaps")
    filtered = _extract_js_function(APP_JS, "filteredActionTimelineGroups")
    epoch = _extract_js_function(APP_JS, "timelineEpochMs")
    duration = _extract_js_function(APP_JS, "timelineDurationMs")
    search_doc = _extract_js_function(APP_JS, "actionTimelineSearchDocument")
    creator = _extract_js_function(APP_JS, "createActionTimelineView")
    renderer = _extract_js_function(APP_JS, "renderActionTimeline")
    history = _extract_js_function(APP_JS, "syncActionTimelineHistoryState")

    # Unknown token usage stays unknown; a real 0 and an absent row differ.
    assert 'group.usage ? String(timelineTokenTotal(group.usage)) : "—"' in row
    # Status and the attempt error reach text, not just a colour class.
    assert "timeline-ledger-status" in row and "statusNoteworthy" in row
    assert "latest.error" in row or "rowError" in row
    assert ".timeline-ledger-status" in STYLE_CSS

    # A time filter asks when an action happened, not whether it was painted.
    assert "group.created_at" in overlap and "if (!item) return false" not in overlap
    assert "selection, group)" in filtered

    # One timestamp parser, shared, and never one Date cannot represent.
    assert "Date.parse" in epoch and "TIMELINE_MAX_EPOCH_MS" in epoch
    assert "const parse = timelineEpochMs" in duration

    # Search covers the Kind label the placeholder advertises.
    assert "timelineKind(group" in search_doc
    assert 't("timeline.kind." + kind)' in search_doc
    assert "cached.lang === LANG" in _extract_js_function(
        APP_JS, "syncActionTimelineSearchIndex"
    )

    # The CSS strips implicit table roles, so they are declared explicitly.
    for role in ('"role", "table"', '"role", "rowgroup"', '"role", "columnheader"'):
        assert role in creator
    assert '"role", "row"' in row and '"role", "cell"' in row

    # Any render that does not re-append the region must destroy the view.
    assert "else {" in renderer and "destroyActionTimelineView();" in renderer
    # The history slot is measured with its previous reservation still applied.
    # Clearing it first reads 0 on an already-reserved slot, collapsing the band
    # during a prepend and moving the compensated row -- browser_smoke's
    # "history prepend N moved the visible anchor" case.
    assert "const previousHeight = target.getBoundingClientRect().height" in history
    assert 'target.replaceChildren(); target.style.minHeight = ""' in history
