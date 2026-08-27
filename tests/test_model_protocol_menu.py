"""The Models pane offers what the daemon accepts, and opening it costs nothing.

Two defects in one pane.

The protocol menu was a fixed three-entry list in ``app.js`` while the daemon
had accepted five: ``gemini`` and ``openai_responses`` are in
``PROFILE_PROTOCOLS``, are accepted by POST/PATCH, are dispatched by the LLM
layer, and are already served in the ``protocols`` field of the payload the
pane fetches -- and were unreachable from the only screen that creates a
profile. The client ignored a catalogue it was being handed.

And opening the pane ran the loopback endpoint scan itself, on first render and
again after every save, activate and delete, because ``custTab("models")``
re-renders. Readiness is derived from local state precisely so that opening
Customize contacts nobody; a probe that fires on render is the implicit
outbound call that design exists to remove.

These tests run the shipped JavaScript under node instead of reading it. A
string assertion against ``app.js`` would pass on code that computes an option
list and never installs it, and would pass on a scan that was moved rather than
removed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from openai4s.server.model_profiles import PROFILE_PROTOCOLS

APP_JS = (
    Path(__file__).resolve().parents[1] / "openai4s" / "server" / "webui" / "app.js"
).read_text(encoding="utf-8")


def _extract_js_function(source: str, name: str, *, required: bool = True) -> str:
    """Return a named classic JS function, balancing braces outside strings.

    The web UI has no build step and no JavaScript parser dependency, so this
    is the same small scanner ``test_webui_static_contract.py`` uses. Optional
    extraction matters here: the network test must fail because a scan ran, not
    because a helper it does not need is missing.
    """
    match = re.search(
        rf"\b(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", source
    )
    if not match:
        if required:
            raise AssertionError(f"app.js must define function {name}()")
        return ""
    start = match.end() - 1
    depth = 0
    quote: str | None = None
    escaped = line_comment = block_comment = False
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


def _extract_i18n(lang: str) -> str:
    """The real translation table, so a label can be checked as prose."""
    head = f"Object.assign(I18N.{lang}, {{"
    start = APP_JS.index(head)
    end = APP_JS.index("\n});", start) + len("\n});")
    return APP_JS[start:end]


def _node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    return node


def _run(script: str, *args: str) -> dict:
    # The harnesses inline both I18N dictionaries, so the script grows with
    # every translated string. `node -e` carries it as one argv entry, and
    # Linux caps a single argument at MAX_ARG_STRLEN (128 KiB): crossing that
    # turns every test here into `OSError: [Errno 7] Argument list too long`,
    # which reads as a broken harness rather than "one more phrase was
    # translated". A file has no such ceiling. `-e` still runs the require, so
    # process.argv keeps starting at the first test argument.
    with tempfile.TemporaryDirectory() as directory:
        harness = Path(directory) / "harness.js"
        harness.write_text(script, encoding="utf-8")
        result = subprocess.run(
            [_node(), "-e", f"require({json.dumps(str(harness))})", *args],
            capture_output=True,
            text=True,
            timeout=60,
        )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


# --------------------------------------------------------------------------
# the menu is generated from the served catalogue
# --------------------------------------------------------------------------

_MENU_HARNESS = """
'use strict';
const I18N = { zh: {}, en: {} };
const LANG = process.argv[1];
__I18N_ZH__
__I18N_EN__
__T_OPTIONAL__
__OPTIONS__
process.stdout.write(JSON.stringify(modelProtocolOptions(JSON.parse(process.argv[2]))));
"""


def _menu(lang: str, served) -> list[dict]:
    script = (
        _MENU_HARNESS.replace("__I18N_ZH__", _extract_i18n("zh"))
        .replace("__I18N_EN__", _extract_i18n("en"))
        .replace("__T_OPTIONAL__", _extract_js_function(APP_JS, "tOptional"))
        .replace("__OPTIONS__", _extract_js_function(APP_JS, "modelProtocolOptions"))
    )
    return _run(script, lang, json.dumps(served))


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_every_protocol_the_daemon_accepts_can_be_chosen(lang):
    """The defect, stated as the user meets it: a Gemini key and no way to say
    so. The menu is now whatever the payload listed, in that order."""
    options = _menu(lang, list(PROFILE_PROTOCOLS))
    assert [item["value"] for item in options] == list(PROFILE_PROTOCOLS)
    assert {"gemini", "openai_responses"} <= {item["value"] for item in options}


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_each_offered_protocol_reads_as_prose_in_both_languages(lang):
    """A generated menu makes it possible to ship an option whose label is the
    untranslated dot-key. Every protocol on offer has to have a translation in
    the language being displayed, not only in the one the author speaks."""
    for item in _menu(lang, list(PROFILE_PROTOCOLS)):
        assert item["label"], item
        assert not item["label"].startswith("cust."), item


def test_a_protocol_with_no_translation_yet_is_still_selectable():
    """The point of generating the list: a protocol added server-side must be
    reachable before anyone writes its label. Showing its id is a usable
    option; dropping it would rebuild the defect one release later."""
    options = _menu("en", ["chatgpt", "vertex_experiment"])
    assert options[1] == {"value": "vertex_experiment", "label": "vertex_experiment"}


def test_a_daemon_too_old_to_send_the_catalogue_leaves_the_form_usable():
    """`protocols` has not always been in the payload. An empty menu would make
    the pane unable to create any profile at all."""
    assert [item["value"] for item in _menu("en", [])] == ["chatgpt", "claude", "ark"]
    assert [item["value"] for item in _menu("en", None)] == ["chatgpt", "claude", "ark"]


def test_the_menu_ignores_values_that_are_not_protocol_ids():
    """The catalogue is server data rendered into a control, so non-strings and
    repeats are dropped rather than turned into blank menu entries."""
    options = _menu("en", ["claude", None, 7, "", "claude", "  ark  "])
    assert [item["value"] for item in options] == ["claude", "ark"]


# --------------------------------------------------------------------------
# opening the pane does not scan local model servers
# --------------------------------------------------------------------------

_PANE_HARNESS = """
'use strict';
function makeNode(tag) {
  return {
    tagName: tag, className: "", textContent: "", value: "", innerHTML: "",
    style: {}, dataset: {}, children: [], disabled: false, scrollTop: 0,
    appendChild(child) { this.children.push(child); return child; },
    querySelectorAll() { return []; },
    focus() {},
  };
}
const document = { createElement: makeNode, createTextNode: () => makeNode("#text") };
const el = (t, c, x) => { const e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e; };
const hdr = () => el("div");
const t = key => key;
const tOptional = () => null;
const hint = () => {};
const custTab = () => {};
const refreshKeyBanner = () => {};
const loadModels = async () => {};
const apiErrorText = e => String((e && e.message) || e);
const publicText = value => String(value == null ? "" : value);
const loopbackModelBase = () => "";
const iconEl = () => el("span");
const sanitizeLocalModelDiscovery = () => ({ endpoints: [], probed: 0, mutated_settings: false });
const renderLocalModelEndpoints = () => {};
const S = {};
const PAYLOAD = JSON.parse(process.argv[1]);
const calls = [];
async function api(path) {
  calls.push(path);
  return path === "/model-profiles" ? PAYLOAD : { endpoints: [], probed: 0 };
}
__OPTIONS__
__CUST_MODELS__
function walk(node, out) { (node.children || []).forEach(child => { out.push(child); walk(child, out); }); return out; }
(async () => {
  const root = el("div");
  await custModels(root);
  const nodes = walk(root, []);
  const onOpen = calls.slice();
  const select = nodes.find(n => n.tagName === "select" && n.className === "cust-input");
  const scan = nodes.find(n => n.tagName === "button" && n.textContent === "cust.models.local.scan" && typeof n.onclick === "function");
  if (scan) await scan.onclick();
  process.stdout.write(JSON.stringify({
    onOpen,
    afterClick: calls.slice(),
    options: select ? select.children.map(option => option.value) : null,
    placeholder: nodes.some(n => n.textContent === "cust.models.local.idle"),
  }));
})();
"""


def _open_pane() -> dict:
    script = _PANE_HARNESS.replace(
        "__OPTIONS__",
        _extract_js_function(APP_JS, "modelProtocolOptions", required=False),
    ).replace("__CUST_MODELS__", _extract_js_function(APP_JS, "custModels"))
    payload = {
        "profiles": [],
        "active_id": "",
        "protocols": list(PROFILE_PROTOCOLS),
    }
    return _run(script, json.dumps(payload))


def test_opening_the_models_pane_does_not_scan_local_servers():
    """The pane may load account metadata, but local model discovery remains
    an explicit action instead of probing four loopback ports on every render."""
    result = _open_pane()
    assert result["onOpen"] == ["/model-profiles", "/volcengine/connection"]
    assert "/model-endpoints/discover" not in result["onOpen"]
    # And it says so, rather than reusing "nothing was detected" for something
    # that was never looked for.
    assert result["placeholder"] is True


def test_the_scan_still_happens_when_the_button_is_pressed():
    """Removing the render-time call must not remove the feature: local
    discovery is useful, it just has to be asked for."""
    result = _open_pane()
    assert any(
        path.startswith("/model-endpoints/discover") for path in result["afterClick"]
    )


def test_the_generated_menu_is_the_control_the_user_sees():
    """The half a pure-function test cannot reach: an option list computed and
    never installed would satisfy every assertion above."""
    result = _open_pane()
    assert result["options"] == list(PROFILE_PROTOCOLS)
