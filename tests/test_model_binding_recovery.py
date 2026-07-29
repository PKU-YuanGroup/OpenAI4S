"""Whether a session pinned to a model configuration can ever be sent to again.

D2 makes a session name the configuration it ran under instead of silently
following whatever is active now, and `bind_model_revision` refuses with 409
`model_revision_unavailable` — "choose one to continue" — when that
configuration is gone. The refusal is right. Nothing could answer it.

The two statements that write `model_profile_id` both sit past the raise, so
the binding could not be changed; `PATCH /frames/{id}` allowlists `name` and
`task_summary`; forking inherits the pin; profile ids are random `mp-<hex>`, so
re-creating the profile under the same name does not match. `app.js` had zero
references to the error code. Deleting a model profile therefore bricked every
session bound to it, permanently — history and artifacts still readable, the
session never sendable again.

Two triggers, and only one of them involves a delete click. The other is a
profile that still exists whose bound *revision* does not: a database predating
the revision history, a rebuilt profile, or seeded builtin profiles dropped the
first time an upgraded database opens Customize → Models.

So: deleting a profile releases what pointed at it, and
`POST /frames/{id}/model-binding` answers the 409 for everything else.

That route exists rather than a flag on send for a specific reason. The client
sends `model: S.defaultModel` on *every* message, so treating a supplied model
as consent to re-pin would rebind silently on every turn — which is exactly the
drift D2 was written to remove. Re-pinning is something a person asks for.
"""

from __future__ import annotations

import io
import json

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth
from openai4s.server.errors import GatewayError


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


@pytest.fixture
def api(tmp_path):
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=1,
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_class = gateway_mod.make_handler(cfg, _Hub(), runner)
    token = local_auth.read_token(tmp_path) or ""

    def call(method, path, body=None):
        handler = object.__new__(handler_class)
        handler._correlation_id = "req-1"
        sent: dict = {}
        handler._send = lambda code, payload, ctype, extra=None: sent.update(
            code=code, body=json.loads(payload.decode("utf-8"))
        )
        handler.command = method
        handler.path = f"/api/v1{path}"
        raw = json.dumps(body or {}).encode("utf-8")
        handler.headers = {
            "Content-Length": str(len(raw)) if body is not None else "0",
            "Content-Type": "application/json",
            local_auth.TOKEN_HEADER: token,
        }
        handler.rfile = io.BytesIO(raw if body is not None else b"")
        handler._route(method)
        return sent

    return runner, call


def _pinned_session(runner, call):
    """A session bound to a profile, the way a real first send binds it."""
    created = call(
        "POST",
        "/model-profiles",
        {
            "name": "prod",
            "provider": "openai_responses",
            "api_key": "sk-test",
            "model": "gpt-4o",
        },
    )
    profile_id = created["body"]["id"]
    call("POST", f"/model-profiles/{profile_id}/activate")

    project = runner.store.create_project(name="p", description="", context="")
    if isinstance(project, dict):
        project = project["project_id"]
    frame = runner.create_session(project)
    binding = runner.bind_model_revision(frame)
    assert binding["model_profile_id"] == profile_id, binding
    return frame, profile_id


def test_deleting_a_profile_does_not_brick_its_sessions(api):
    """The defect. Before this, every send after the delete was a 409 forever."""
    runner, call = api
    frame, profile_id = _pinned_session(runner, call)

    assert call("DELETE", f"/model-profiles/{profile_id}")["code"] in (200, 204)

    # The next send re-binds instead of refusing.
    binding = runner.bind_model_revision(frame)
    assert binding["model_profile_id"] != profile_id


def test_the_brick_is_gone_from_the_stored_row_too(api):
    """Clearing it only in memory would come back on the next daemon start."""
    runner, call = api
    frame, profile_id = _pinned_session(runner, call)
    call("DELETE", f"/model-profiles/{profile_id}")
    row = runner.store.get_frame(frame) or {}
    assert not row.get("model_profile_id")


def test_only_the_sessions_of_the_deleted_profile_are_released(api):
    """A blanket clear would unpin every session in the database — silently
    undoing D2 for sessions that were perfectly fine."""
    runner, call = api
    frame_a, profile_a = _pinned_session(runner, call)

    other = call(
        "POST",
        "/model-profiles",
        {
            "name": "other",
            "provider": "claude",
            "api_key": "sk-other",
            "model": "claude-sonnet-4-5",
        },
    )["body"]["id"]
    call("POST", f"/model-profiles/{other}/activate")
    project = runner.store.list_projects()[0]["project_id"]
    frame_b = runner.create_session(project)
    runner.bind_model_revision(frame_b)
    assert (runner.store.get_frame(frame_b) or {}).get("model_profile_id") == other

    call("DELETE", f"/model-profiles/{profile_a}")
    assert not (runner.store.get_frame(frame_a) or {}).get("model_profile_id")
    assert (runner.store.get_frame(frame_b) or {}).get("model_profile_id") == other


# --------------------------------------------------------------------------
# the trigger that needs no delete click
# --------------------------------------------------------------------------


def test_a_dangling_revision_still_refuses(api):
    """The 409 is correct and stays. A session must not quietly change model."""
    runner, call = api
    frame, _profile_id = _pinned_session(runner, call)
    runner.store.update_frame(frame, model_profile_revision=999)

    with pytest.raises(GatewayError) as caught:
        runner.bind_model_revision(frame)
    assert caught.value.code == 409
    assert caught.value.error_code == "model_revision_unavailable"


def test_the_rebind_route_answers_it(api):
    """The half that did not exist. Without a way to answer, a correct refusal
    is still a dead session."""
    runner, call = api
    frame, _profile_id = _pinned_session(runner, call)
    runner.store.update_frame(frame, model_profile_revision=999)

    result = call("POST", f"/frames/{frame}/model-binding")
    assert result["code"] == 200, result
    assert result["body"]["ok"] is True
    # And the session sends again.
    assert runner.bind_model_revision(frame)["model_profile_id"]


def test_rebinding_is_not_something_send_does_on_its_own(api):
    """The client sends `model` on every message. If that counted as consent,
    every turn would silently re-pin and D2 would be undone by the fix meant to
    make it usable — so the capability lives on its own route."""
    import inspect

    source = inspect.getsource(gateway_mod.SessionRunner.run_message)
    assert "unpin_model" not in source
    assert "bind_model_revision" in source


def test_the_route_refuses_on_a_read_only_session(api):
    """It mutates a session, so it takes the same writability gate as every
    other session mutation — a quarantined import must not be re-pinned."""
    source = __import__("inspect").getsource(gateway_mod.make_handler)
    index = source.index("/frames/([^/]+)/model-binding")
    window = source[index : index + 900]
    assert "_require_session_writable" in window


# --------------------------------------------------------------------------
# the client
# --------------------------------------------------------------------------


def test_the_client_can_act_on_the_refusal():
    """`app.js` had zero references to the error code, so the 409 reached a
    user as a generic "send failed" toast with no way forward."""
    from pathlib import Path as _Path

    app_js = _Path("openai4s/server/webui/app.js").read_text(encoding="utf-8")
    assert "model_revision_unavailable" in app_js
    assert "/model-binding" in app_js


def test_the_client_asks_before_rebinding():
    """Re-pinning changes which configuration a session claims to have run
    under. Doing it without asking is the silent drift D2 removed."""
    from pathlib import Path as _Path

    app_js = _Path("openai4s/server/webui/app.js").read_text(encoding="utf-8")
    index = app_js.index("model_revision_unavailable")
    window = app_js[index : index + 700]
    assert "confirm(" in window
    assert window.index("confirm(") < window.index("/model-binding")


def test_both_languages_have_the_rebind_strings():
    from pathlib import Path as _Path

    app_js = _Path("openai4s/server/webui/app.js").read_text(encoding="utf-8")
    for key in ("model.rebind.confirm", "model.rebind.done"):
        assert app_js.count(f'"{key}":') == 2, key


# --------------------------------------------------------------------------
# the pin was write-only
# --------------------------------------------------------------------------


def test_a_pinned_session_dispatches_to_what_it_named(api):
    """The other half, and the sharper one. The pin was written on every
    session and read by nothing: `revision_config` was used only as an
    existence test, so the turn went to the globally active profile's provider,
    endpoint, model AND credential while the row recorded a different profile.
    A session pinned to A and continued after B was activated ran on B and said
    it ran on A.
    """
    runner, call = api
    frame, profile_id = _pinned_session(runner, call)

    other = call(
        "POST",
        "/model-profiles",
        {
            "name": "switched-to",
            "provider": "claude",
            "api_key": "sk-the-other-key",
            "model": "claude-sonnet-4-5",
        },
    )["body"]["id"]
    call("POST", f"/model-profiles/{other}/activate")

    state = runner._state(frame, runner.store.list_projects()[0]["project_id"])
    resolved = runner._llm_cfg(state)
    assert resolved.provider == "openai_responses", "the pin was ignored"
    assert resolved.model == "gpt-4o"
    assert resolved.api_key == "sk-test", "it dispatched under the other key"


def test_an_unpinned_session_still_follows_the_active_profile(api):
    """The fallback has to stay: every session that predates the pin, and every
    one whose pin was released, depends on it."""
    runner, call = api
    call(
        "POST",
        "/model-profiles",
        {
            "name": "active",
            "provider": "claude",
            "api_key": "sk-active",
            "model": "claude-sonnet-4-5",
        },
    )
    profiles = call("GET", "/model-profiles")["body"]["profiles"]
    active = next(p for p in profiles if p["name"] == "active")["id"]
    call("POST", f"/model-profiles/{active}/activate")

    project = runner.store.create_project(name="q", description="", context="")
    if isinstance(project, dict):
        project = project["project_id"]
    frame = runner.create_session(project)
    state = runner._state(frame, project)
    assert runner._llm_cfg(state).provider == "claude"


def test_an_unresolvable_pin_falls_back_rather_than_failing(api):
    """A pin that cannot be honoured must not become a turn that cannot run.
    `bind_model_revision` already refuses the cases a user should be asked
    about; this path is the ones they should not be."""
    runner, call = api
    frame, _profile_id = _pinned_session(runner, call)
    runner.store.update_frame(frame, model_profile_revision=999)
    state = runner._state(frame, runner.store.list_projects()[0]["project_id"])
    assert runner._llm_cfg(state) is not None


def test_the_composer_choice_still_wins_over_the_recorded_model(api):
    """Picking a model for this session is an explicit act. The pin records
    what it ran under; it does not overrule what the user just asked for."""
    runner, call = api
    frame, _profile_id = _pinned_session(runner, call)
    state = runner._state(frame, runner.store.list_projects()[0]["project_id"])
    state.model = "gpt-4o-mini"
    assert runner._llm_cfg(state).model == "gpt-4o-mini"
