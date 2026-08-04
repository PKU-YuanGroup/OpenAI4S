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
    """Still the requirement: a session must not become permanently unsendable.

    What changed is *how*. Delete used to NULL the pin on every frame naming the
    profile, so the next send silently re-bound somewhere else -- which unbricks
    the session by destroying the audit answer to "what configuration did this
    session run under", and by making the substitution invisible. Delete is now a
    tombstone: the pin and the revision history stay, the next send refuses with
    `409 model_revision_unavailable`, and `POST /frames/{id}/model-binding` --
    which exists precisely for that 409 -- rebinds on request.

    The brick is what this test is about, so it asserts the whole path: refused,
    then explicitly rebindable, then sendable.
    """
    runner, call = api
    frame, profile_id = _pinned_session(runner, call)

    assert call("DELETE", f"/model-profiles/{profile_id}")["code"] in (200, 204)

    with pytest.raises(gateway_mod.GatewayError) as refused:
        runner.bind_model_revision(frame)
    assert refused.value.code == 409
    assert refused.value.error_code == "model_revision_unavailable"

    rebound = call("POST", f"/frames/{frame}/model-binding", {})
    assert rebound["code"] == 200
    binding = rebound["body"]["binding"]
    assert binding["model_profile_id"] != profile_id
    # And it stays bound, so the session is not merely unbricked once.
    assert runner.bind_model_revision(frame)["model_profile_id"] == (
        binding["model_profile_id"]
    )


def test_the_deleted_profile_keeps_the_sessions_record_until_it_is_rebound(api):
    """The audit half. A pin answers "what did this session run under"; clearing
    it on delete threw that answer away for every session at once, and the
    revisions live in the profile row's own JSON blob, so hard-deleting the row
    deleted the history too."""
    runner, call = api
    frame, profile_id = _pinned_session(runner, call)
    call("DELETE", f"/model-profiles/{profile_id}")

    row = runner.store.get_frame(frame) or {}
    assert row.get("model_profile_id") == profile_id
    stored = next(
        (p for p in runner.store.list_model_profiles() if p["id"] == profile_id), None
    )
    assert stored is not None and stored.get("deleted_at")
    assert stored.get("revisions"), "the revision history went with the row"


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
    # Delete no longer touches any frame's pin -- it tombstones the profile -- so
    # the property this test is about holds by construction rather than by a
    # correctly-scoped UPDATE. It is kept because the failure it guards against is
    # real: a blanket clear silently undid D2 for sessions that were fine.
    assert (runner.store.get_frame(frame_a) or {}).get("model_profile_id") == profile_a
    assert (runner.store.get_frame(frame_b) or {}).get("model_profile_id") == other
    # And session B, whose profile is untouched, is still sendable without a rebind.
    assert runner.bind_model_revision(frame_b)["model_profile_id"] == other


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


def test_the_route_refuses_on_a_read_only_session(api, monkeypatch):
    """It mutates a session, so it takes the same writability gate as every
    other session mutation — a quarantined import must not be re-pinned.

    Driven rather than read out of the source. The first version asserted on
    `inspect.getsource(make_handler)`, passed locally and failed in CI — the
    frozen-shape recorder wraps the handler, so the source it hands back is the
    wrapper's. Falsifying that version then showed the assertion was pointing
    at the wrong thing entirely: deleting the route's own
    `_require_session_writable` call changed nothing, because the blanket
    `frame_mutation` gate covers every non-GET under `/frames/{id}/...`
    already. The redundant call is gone and this checks the behaviour, which is
    what the claim was always about.
    """
    runner, call = api
    frame, _profile_id = _pinned_session(runner, call)
    monkeypatch.setattr(
        runner, "import_quarantine", lambda _frame_id: {"reason": "imported"}
    )
    result = call("POST", f"/frames/{frame}/model-binding")
    assert result["code"] == 423, result


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


def test_an_unresolvable_pin_refuses_rather_than_silently_using_another_profile(api):
    """This asserted the opposite, and the opposite was the defect.

    "A pin that cannot be honoured must not become a turn that cannot run" sounds
    like tolerance, and what it actually did was run the turn on whichever profile
    happens to be active while the frame went on recording the pinned one --
    recorded as A, executed as B, which is the single thing D2 exists to prevent.
    A revision that is not in the history is not a case a user should be spared;
    it is one they have to decide, and `POST /frames/{id}/model-binding` is how.
    """
    runner, call = api
    frame, _profile_id = _pinned_session(runner, call)
    runner.store.update_frame(frame, model_profile_revision=999)
    state = runner._state(frame, runner.store.list_projects()[0]["project_id"])

    with pytest.raises(gateway_mod.GatewayError) as refused:
        runner._llm_cfg(state)
    assert refused.value.code == 409
    assert refused.value.error_code == "model_revision_unavailable"


def test_an_unpinned_session_still_falls_back_to_the_active_profile(api):
    """The fallback is right when there is no pin. Removing it outright would
    break every session that never chose a profile, so `None` from
    `_pinned_llm_config` now means exactly that and nothing else."""
    runner, call = api
    # A session that never bound anything. `_pinned_session` first, only to make a
    # project exist; the frame under test is a fresh one with no pin written.
    _seed, _profile_id = _pinned_session(runner, call)
    project = runner.store.list_projects()[0]["project_id"]
    frame = runner.create_session(project)
    state = runner._state(frame, project)
    assert not (runner.store.get_frame(frame) or {}).get("model_profile_id")
    assert runner._llm_cfg(state) is not None


def test_the_composer_choice_does_not_overrule_the_pinned_model(api):
    """This asserted the chimera, with the same reasoning the code carried.

    `st.model` is the request's bare `model` field, which the browser sent on
    every single message -- so preferring it meant provider, endpoint and
    credential from the pinned revision and the model name from the header
    selector: a configuration that exists in no profile. Choosing a model is
    activating a profile, and the session then binds it.
    """
    runner, call = api
    frame, _profile_id = _pinned_session(runner, call)
    state = runner._state(frame, runner.store.list_projects()[0]["project_id"])
    pinned = runner._llm_cfg(state).model
    state.model = "gpt-4o-mini"
    assert runner._llm_cfg(state).model == pinned


# --- the legacy backfill, and the second 409 nothing could answer ----------


def test_a_legacy_session_matching_no_profile_stays_unbound(api):
    """Zero matches fell through to whatever profile happens to be active.

    The comment directly above that block states the rule -- "a session that
    already has history is a legacy one: it ran under some configuration, and
    D2 says to recover that rather than to adopt whatever happens to be active
    now" -- and the one-match and many-match branches both honour it. Zero
    matches did not: it wrote the active profile's id and sealed a revision on
    it, so the session's own record then claimed a configuration it never ran
    under. That is the silent drift D2 exists to remove, arriving through the
    path meant to prevent it.

    Unbound is the honest answer and an already-supported state: it is what an
    install driven entirely by `.env` gets.
    """
    runner, call = api
    created = call(
        "POST",
        "/model-profiles",
        {
            "name": "active",
            "provider": "openai_responses",
            "api_key": "sk-test",
            "model": "active-1",
        },
    )
    call("POST", f"/model-profiles/{created['body']['id']}/activate")

    project = runner.store.create_project(name="p", description="", context="")
    if isinstance(project, dict):
        project = project["project_id"]
    frame_id = runner.create_session(project)
    # A legacy session: history, and a recorded model no profile names.
    runner.store.update_frame(frame_id, model="retired-model-9")
    runner.store.add_message(root_frame_id=frame_id, role="user", content="hello")

    result = runner.bind_model_revision(frame_id)

    assert result["bound"] is False
    assert result["model_profile_id"] == ""
    assert result["model_profile_revision"] == 0

    frame = runner.store.get_frame(frame_id)
    assert not (
        frame.get("model_profile_id") or ""
    ), "the session was pinned to a profile it never ran under"


def test_a_legacy_session_with_one_match_is_still_backfilled(api):
    """The refusal must not be an outage: a unique match is the case the
    backfill exists for."""
    runner, call = api
    created = call(
        "POST",
        "/model-profiles",
        {
            "name": "legacy",
            "provider": "openai_responses",
            "api_key": "sk-test",
            "model": "legacy-1",
        },
    )
    target_id = created["body"]["id"]
    call("POST", f"/model-profiles/{target_id}/activate")

    project = runner.store.create_project(name="p", description="", context="")
    if isinstance(project, dict):
        project = project["project_id"]
    frame_id = runner.create_session(project)
    runner.store.update_frame(frame_id, model="legacy-1")
    runner.store.add_message(root_frame_id=frame_id, role="user", content="hello")

    result = runner.bind_model_revision(frame_id)

    assert result["bound"] is True
    assert result.get("backfilled") is True
    assert result["model_profile_id"] == target_id


def test_the_client_can_act_on_the_ambiguous_refusal_too():
    """`model_revision_ambiguous` is the same predicament as
    `model_revision_unavailable` -- "choose one to continue" -- answered by the
    same rebind route, and the branch that offers it named only one of the two
    codes."""
    from pathlib import Path as _Path

    app_js = _Path("openai4s/server/webui/app.js").read_text(encoding="utf-8")
    # In the branch CONDITION, not merely somewhere in the file. An earlier
    # version of this test matched the bare string and stayed green when the
    # code was removed from the `if` -- the comment above it still named it.
    import re

    guard = re.search(r"e\.code\s*===\s*[\"']model_revision_ambiguous[\"']", app_js)
    assert guard, "the code is mentioned but nothing branches on it"

    # And that branch is the rebind one.
    window = app_js[guard.end() : guard.end() + 700]
    assert "/model-binding" in window
    assert "confirm(" in window
