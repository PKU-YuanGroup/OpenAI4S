"""What a tool's `dangerous` declaration actually did.

Ten control tools declare `dangerous = True` — artifact restore, network
access, remote-capability registration, the three compute tools, and the four
dynamic-tool lifecycle tools. Two tests assert the declaration. Nothing else in
the repository read it: not the dispatcher, not the permission broker, not the
audit record, not the approval card. `Tool.dangerous` was a field that
type-checked and meant nothing.

The consequence was in the prompt. Every approval card looked the same, and its
remember-scope defaulted to "conversation" with a pre-filled pattern — so one
click of Allow on a `restore_artifact_version` prompt granted artifact restore
for the rest of the session, exactly as it would for reading a file, and the
card gave no sign the two differed.

Two things changed. The declaration now reaches the broker and is persisted in
the durable request payload, and the card reads it: a risk badge, and a default
grant that covers only this call. Every scope is still offered — the point is
that a broader one is now chosen rather than defaulted into.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.permissions import broker
from openai4s.server import gateway as gateway_mod
from openai4s.tools.registry import TOOL_TYPES

APP_JS = Path("openai4s/server/webui/app.js").read_text(encoding="utf-8")


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


@pytest.fixture
def session(tmp_path):
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=1,
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    project = runner.store.create_project(name="p", description="", context="")
    if isinstance(project, dict):
        project = project["project_id"]
    frame = runner.create_session(project)
    return runner, frame


def _prompt_for(runner, frame, **gate_kwargs) -> dict:
    """Drive the real broker and return the card payload it emitted."""
    captured: dict = {}
    answered = threading.Event()

    def channel(payload):
        if payload.get("type") != "await_permission":
            return  # the resolution follows on the same channel; only the ask matters
        captured.update(payload)

        def _answer():
            broker().resolve(payload["decision_id"], allow=True, scope="once")
            answered.set()

        threading.Timer(0.02, _answer).start()

    broker().register_channel(frame, channel, store=runner.store)
    try:
        broker().gate(
            store=runner.store,
            frame_id=frame,
            method=gate_kwargs.pop("method", "restore_artifact_version"),
            target="a-1",
            timeout=10,
            **gate_kwargs,
        )
    finally:
        answered.wait(5)
        broker().unregister_channel(frame)
    return captured


# --------------------------------------------------------------------------
# the declaration reaches the prompt
# --------------------------------------------------------------------------


def test_the_declaration_reaches_the_approval_prompt(session):
    """The defect, at the point where the field either means something or does
    not. Before this the payload had no such key at all."""
    runner, frame = session
    payload = _prompt_for(runner, frame, dangerous=True)
    assert payload.get("dangerous") is True


def test_an_ordinary_tool_is_not_marked(session):
    """A fix that marked everything dangerous would satisfy the test above and
    make the badge meaningless."""
    runner, frame = session
    payload = _prompt_for(runner, frame, method="read_file", dangerous=False)
    assert payload.get("dangerous") is False


def test_the_flag_is_persisted_with_the_durable_request(session):
    """The request row outlives the live channel — it is what a reconnecting
    client re-renders and what an audit reads. A flag that lived only in the
    in-flight message would vanish on reload, and the card would come back
    without its badge."""
    runner, frame = session
    payload = _prompt_for(runner, frame, dangerous=True)
    stored = runner.store.get_permission_request(payload["decision_id"])
    assert stored is not None
    body = stored.get("payload")
    if isinstance(body, str):
        body = json.loads(body)
    assert body.get("dangerous") is True


def test_a_real_dangerous_tool_call_arrives_marked(session, tmp_path):
    """The wiring, driven rather than read.

    The tests above hand `dangerous=True` to the broker themselves, so they
    would all still pass if the dispatcher never passed it — which is the whole
    defect. This one calls a tool that declares it and reads what the prompt
    got, so removing the argument at the call site fails here.
    """
    from openai4s.host_dispatch import build_dispatcher

    runner, frame = session
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    dispatcher = build_dispatcher(runner.cfg, frame_id=frame, workspace=workspace)

    captured: dict = {}
    answered = threading.Event()

    def channel(payload):
        if payload.get("type") != "await_permission":
            return  # the resolution follows on the same channel; only the ask matters
        captured.update(payload)

        def _answer():
            # Deny: the tool's own behaviour is not what is under test, and a
            # denial exercises the same prompt without restoring anything.
            broker().resolve(payload["decision_id"], allow=False, scope="once")
            answered.set()

        threading.Timer(0.02, _answer).start()

    broker().register_channel(frame, channel, store=dispatcher.store)
    try:
        dispatcher("restore_artifact_version", [{"version_id": "v-does-not-exist"}])
    except (
        Exception
    ):  # noqa: BLE001 — a refused restore is fine; the prompt is the subject
        pass
    finally:
        answered.wait(5)
        broker().unregister_channel(frame)

    assert captured, "the dangerous tool never prompted at all"
    assert captured.get("dangerous") is True


def test_an_ordinary_tool_call_arrives_unmarked(session, tmp_path):
    """Same path, a tool that declares nothing — so the marking comes from the
    declaration rather than from the dispatcher marking everything."""
    from openai4s.host_dispatch import build_dispatcher

    runner, frame = session
    workspace = tmp_path / "ws2"
    workspace.mkdir(exist_ok=True)
    (workspace / "note.txt").write_text("hello\n", encoding="utf-8")
    dispatcher = build_dispatcher(runner.cfg, frame_id=frame, workspace=workspace)

    captured: dict = {}
    answered = threading.Event()

    def channel(payload):
        if payload.get("type") != "await_permission":
            return  # the resolution follows on the same channel; only the ask matters
        captured.update(payload)

        def _answer():
            broker().resolve(payload["decision_id"], allow=False, scope="once")
            answered.set()

        threading.Timer(0.02, _answer).start()

    broker().register_channel(frame, channel, store=dispatcher.store)
    try:
        dispatcher("read_file", [{"path": "note.txt"}])
    except Exception:  # noqa: BLE001
        pass
    finally:
        answered.wait(5)
        broker().unregister_channel(frame)

    if captured:
        assert captured.get("dangerous") is False


def test_every_dangerous_tool_still_asks(session):
    """The badge is worth nothing on a tool that never prompts. Three of the
    ten do not require approval — `compute_cancel` and `compute_close` stop
    work rather than start it — so this pins which ones the declaration can
    actually reach, and fails if a prompting tool quietly stops prompting.
    """
    dangerous = {
        tool.name: tool
        for tool in (cls() for cls in TOOL_TYPES)
        if getattr(tool, "dangerous", False)
    }
    assert dangerous, "no tool declares dangerous=True; this suite is vacuous"
    prompting = {name for name, t in dangerous.items() if t.requires_approval}
    assert "restore_artifact_version" in prompting
    assert "compute_submit" in prompting
    assert "request_network_access" in prompting
    # Stopping remote work is not the risk the declaration is about.
    assert not dangerous["compute_cancel"].requires_approval
    assert not dangerous["compute_close"].requires_approval


# --------------------------------------------------------------------------
# the card reads it
# --------------------------------------------------------------------------

_HARNESS = """
'use strict';
__SNIPPET__
process.stdout.write(JSON.stringify({
  dangerous: defaultRememberScope(JSON.parse(process.argv[1])),
  ordinary: defaultRememberScope({}),
  missing: defaultRememberScope(null),
}));
"""


def _scope_for(payload: dict) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    start = APP_JS.index("function defaultRememberScope(m) {")
    end = APP_JS.index("\n}", start) + 2
    script = _HARNESS.replace("__SNIPPET__", APP_JS[start:end])
    result = subprocess.run(
        [node, "-e", script, json.dumps(payload)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_a_dangerous_prompt_does_not_default_to_remembering(session):
    """The user-visible half of the defect: Allow, on a card whose scope
    selector you did not read, used to grant a dangerous capability for the
    whole conversation."""
    out = _scope_for({"dangerous": True})
    assert out["dangerous"] == "once"


def test_an_ordinary_prompt_keeps_the_scope_it_had():
    """Approving every ordinary file read one at a time is how a permission
    system gets turned off. This behaviour was not the problem and stays."""
    out = _scope_for({"dangerous": True})
    assert out["ordinary"] == "conversation"


def test_a_payload_from_an_older_daemon_is_not_treated_as_dangerous():
    """A reconnecting client can be handed a request recorded before the field
    existed. Undefined has to mean "ordinary", not crash and not "risky"."""
    out = _scope_for({"dangerous": True})
    assert out["missing"] == "conversation"


def test_the_card_shows_the_badge_and_uses_the_rule():
    """The renderer needs a DOM, so this is the one place the assertion is
    structural. Both halves have to be in the card: a fix that computed the
    scope and never installed it would pass every test above."""
    start = APP_JS.index("function renderPermissionCard(m) {")
    body = APP_JS[start : APP_JS.index("\n}\n", start)]
    assert "let scope = defaultRememberScope(m);" in body
    assert "perm-badge danger" in body
    assert "perm.badge.dangerous" in body


def test_the_rule_input_is_hidden_when_the_grant_is_once():
    """It was only ever hidden by the scope buttons' click handler. A card that
    now *starts* at "once" would otherwise open showing a "remember this rule"
    input that the submitted decision ignores."""
    start = APP_JS.index("function renderPermissionCard(m) {")
    body = APP_JS[start : APP_JS.index("\n}\n", start)]
    initial = body.index('patWrap.style.display = (scope === "once")')
    assert initial < body.index("perm.lbl.rememberRule")


def test_both_languages_have_the_badge():
    assert APP_JS.count('"perm.badge.dangerous":') == 2
