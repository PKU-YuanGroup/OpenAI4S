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
        handler._send = (
            lambda code, payload, ctype, extra=None, security=None: sent.update(
                code=code, body=json.loads(payload.decode("utf-8"))
            )
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


# --- one credential rule: readiness, bind and dispatch ---------------------
#
# Three call sites answered "does this profile have a usable credential" three
# different ways. Readiness accepted a loopback endpoint; the fresh bind checked
# nothing; the already-bound bind and dispatch accepted only the profile's own
# stored key. So a profile relying on `OPENAI4S_<PROVIDER>_API_KEY`, or a keyless
# local Ollama profile readiness called `ready`, was activated and pinned on the
# first send, and dispatch then refused it with `model_revision_unavailable` --
# which the rebind route "answered" by pinning the same profile again. The
# user's message was lost on the way: the refusal fired while naming the
# session, before the user row was written.
#
# The rule is now one: the profile's own key; else the SAME provider's key from
# the daemon's environment (never another provider's); else keyless for a local
# endpoint. A brokered key that no longer resolves is still a refusal.

#: Not credential-shaped on purpose -- `source_secret_scan.py` would flag one.
_ENV_KEY = "environment-key-for-this-provider"


def _session(runner):
    project = runner.store.create_project(name="p", description="", context="")
    if isinstance(project, dict):
        project = project["project_id"]
    return runner.create_session(project), project


def _dispatch_spy(runner, monkeypatch):
    """Record the configuration the turn loop was entered under.

    `_loop` is replaced, nothing else: binding, the title summary's config
    resolution, the user row and the pinned dispatch all run for real, and all of
    them sit in front of this point.
    """
    seen: list = []

    def _loop(st, emit, visible):
        del emit, visible
        seen.append(runner._llm_cfg(st))
        return "submitted"

    monkeypatch.setattr(runner, "_loop", _loop)
    monkeypatch.setattr(runner, "_spawn_title_summary", lambda *a, **k: None)
    return seen


def _send(runner, call, frame, text):
    accepted = call(
        "POST", f"/frames/{frame}/message", {"request": text, "wait": False}
    )
    result = None
    if accepted["code"] == 202:
        result = next(
            job for job in runner._jobs.values() if job.root_frame_id == frame
        ).wait_result()
    return accepted, result


@pytest.mark.stubbed_backend
def test_a_profile_keyed_by_the_environment_is_ready_bound_and_dispatched(
    api, monkeypatch
):
    runner, call = api
    monkeypatch.setenv("OPENAI4S_CLAUDE_API_KEY", _ENV_KEY)
    seen = _dispatch_spy(runner, monkeypatch)

    created = call(
        "POST",
        "/model-profiles",
        {"name": "env-keyed", "provider": "claude", "model": "claude-sonnet-4-5"},
    )
    assert created["code"] == 201, created
    # The profile holds no key of its own, and says so...
    assert created["body"]["has_api_key"] is False
    # ...but a credential resolves for it, so it is not `needs_key`.
    assert created["body"]["readiness"]["state"] == "ready", created["body"]
    profile_id = created["body"]["id"]
    activated = call("POST", f"/model-profiles/{profile_id}/activate")
    assert activated["code"] == 200 and activated["body"]["has_api_key"] is True

    frame, _project = _session(runner)
    accepted, result = _send(runner, call, frame, "name the capital of Italy")
    assert accepted["code"] == 202, accepted
    assert result and result.get("status") == "completed", result
    assert seen, "the turn never reached the model loop"
    assert seen[-1].provider == "claude"
    assert seen[-1].model == "claude-sonnet-4-5"
    assert seen[-1].api_key == _ENV_KEY
    assert (runner.store.get_frame(frame) or {}).get("model_profile_id") == profile_id


@pytest.mark.stubbed_backend
def test_a_keyless_local_profile_dispatches_without_borrowing_any_key(api, monkeypatch):
    """The profile Customize → Models' local-discovery "Add" button creates.

    Keyless, and it must stay keyless: `replace()` re-runs `LLMConfig`'s env
    resolution, which would otherwise hand the daemon's generic key -- set by
    the suite for another provider -- to whatever is listening locally. Nor
    does the SAME provider's key count: `OPENAI_API_KEY` is a cloud credential
    a developer machine usually exports, and this endpoint is plain http on a
    LAN address.
    """
    runner, call = api
    monkeypatch.setenv("OPENAI_API_KEY", _ENV_KEY)
    monkeypatch.setenv("OPENAI4S_CHATGPT_API_KEY", _ENV_KEY)
    seen = _dispatch_spy(runner, monkeypatch)
    created = call(
        "POST",
        "/model-profiles",
        {
            "name": "Ollama · llama3.2",
            "provider": "chatgpt",
            "base_url": "http://192.168.1.50:11434/v1",
            "model": "llama3.2",
        },
    )
    assert created["body"]["readiness"]["state"] == "ready", created["body"]
    call("POST", f"/model-profiles/{created['body']['id']}/activate")

    frame, _project = _session(runner)
    accepted, result = _send(runner, call, frame, "hello")
    assert accepted["code"] == 202, accepted
    assert result and result.get("status") == "completed", result
    assert seen and seen[-1].base_url == "http://192.168.1.50:11434/v1"
    assert seen[-1].api_key == "", "a key was sent to a keyless local endpoint"


@pytest.mark.stubbed_backend
def test_another_providers_key_is_never_inherited(api, monkeypatch):
    """The daemon's key belongs to its own provider (`deepseek` here).

    A Claude profile with no key of its own must not be dispatched under it:
    readiness says `needs_key`, activation does not claim a key, and send
    refuses before anything is admitted or pinned -- with a code that says what
    is wrong, rather than the rebind prompt that would re-pin the same profile.
    """
    runner, call = api
    seen = _dispatch_spy(runner, monkeypatch)
    created = call(
        "POST",
        "/model-profiles",
        {"name": "no-key", "provider": "claude", "model": "claude-sonnet-4-5"},
    )
    assert created["body"]["readiness"]["state"] == "needs_key"
    activated = call("POST", f"/model-profiles/{created['body']['id']}/activate")
    assert activated["body"]["has_api_key"] is False, activated

    frame, _project = _session(runner)
    accepted, _result = _send(runner, call, frame, "hello")
    assert accepted["code"] == 409, accepted
    assert accepted["body"].get("code") == "model_profile_needs_key", accepted
    assert not seen, "the turn ran under a credential that is not this provider's"
    assert not (runner.store.get_frame(frame) or {}).get("model_profile_id")


def test_a_revoked_brokered_key_still_refuses_even_with_an_environment_key(
    api, monkeypatch
):
    """Falling back is for a profile that never had a key. One that had a
    brokered key which no longer resolves asked for *that* credential."""
    runner, call = api
    created = call(
        "POST",
        "/model-profiles",
        {
            "name": "brokered",
            "provider": "claude",
            "api_key": "sk-brokered",
            "model": "claude-sonnet-4-5",
        },
    )
    profile_id = created["body"]["id"]
    call("POST", f"/model-profiles/{profile_id}/activate")
    frame, project = _session(runner)
    assert runner.bind_model_revision(frame)["model_profile_id"] == profile_id

    monkeypatch.setenv("OPENAI4S_CLAUDE_API_KEY", _ENV_KEY)
    row = next(p for p in runner.store.list_model_profiles() if p["id"] == profile_id)
    runner.store.secrets.delete(row["api_key"])

    listed = call("GET", "/model-profiles")["body"]["profiles"]
    card = next(p for p in listed if p["id"] == profile_id)["readiness"]
    assert card["state"] == "needs_key", card
    with pytest.raises(GatewayError) as bound:
        runner.bind_model_revision(frame)
    assert bound.value.error_code == "model_revision_unavailable"
    with pytest.raises(GatewayError) as dispatched:
        runner._llm_cfg(runner._state(frame, project))
    assert dispatched.value.error_code == "model_revision_unavailable"


def test_a_tombstoned_pin_refuses_even_when_the_environment_has_a_key(api, monkeypatch):
    """Delete destroys the profile's key, which used to be what made a pinned
    tombstone undispatchable. With an environment fallback that is no longer
    enough on its own, so the tombstone is refused for being one."""
    runner, call = api
    monkeypatch.setenv("OPENAI4S_CLAUDE_API_KEY", _ENV_KEY)
    created = call(
        "POST",
        "/model-profiles",
        {"name": "env-keyed", "provider": "claude", "model": "claude-sonnet-4-5"},
    )
    profile_id = created["body"]["id"]
    call("POST", f"/model-profiles/{profile_id}/activate")
    frame, project = _session(runner)
    frozen = runner.freeze_model_binding(frame)
    assert frozen["model_profile_id"] == profile_id

    call("DELETE", f"/model-profiles/{profile_id}")
    state = runner._state(frame, project)
    state.frozen_model_binding = (profile_id, frozen["model_profile_revision"])
    with pytest.raises(GatewayError) as refused:
        runner._llm_cfg(state)
    assert refused.value.error_code == "model_revision_unavailable"


@pytest.mark.stubbed_backend
def test_a_turn_refused_after_admission_keeps_the_users_message(api, monkeypatch):
    """A 202 tells the client its text was accepted, and the composer clears.

    A credential revoked between admission and dispatch is refused at dispatch --
    correctly -- but the refusal fired while resolving the configuration used to
    name the session, which ran before the user row was written. The failure row
    was stored and the request itself was not, anywhere but the session title.
    """
    runner, call = api
    created = call(
        "POST",
        "/model-profiles",
        {
            "name": "revoked-in-flight",
            "provider": "claude",
            "api_key": "sk-in-flight",
            "model": "claude-sonnet-4-5",
        },
    )
    profile_id = created["body"]["id"]
    call("POST", f"/model-profiles/{profile_id}/activate")
    monkeypatch.setattr(runner, "_spawn_title_summary", lambda *a, **k: None)
    frame, _project = _session(runner)

    admit = runner.freeze_model_binding

    def _admit_then_revoke(frame_id):
        frozen = admit(frame_id)
        row = next(
            p for p in runner.store.list_model_profiles() if p["id"] == profile_id
        )
        runner.store.secrets.delete(row["api_key"])
        return frozen

    monkeypatch.setattr(runner, "freeze_model_binding", _admit_then_revoke)
    text = "the request text that must survive a refusal"
    accepted, result = _send(runner, call, frame, text)
    assert accepted["code"] == 202, accepted
    assert result and result.get("status") == "failed", result

    listed = call("GET", f"/frames/{frame}/messages")["body"]["messages"]
    users = [m for m in listed if m.get("role") == "user"]
    assert [m.get("content") for m in users] == [text], listed
    assert any(m.get("failure") for m in listed), listed


def test_the_rebind_route_does_not_re_pin_a_profile_nothing_can_dispatch(api):
    """The dead-end loop, driven through the route that answered it.

    `model_revision_unavailable` says "rebind it to continue"; the rebind route
    unpinned and re-bound to the active profile -- the same keyless one -- and
    answered 200 `bound:true`, so the next send refused again. It now refuses
    with the code that names the actual problem, and pins nothing.
    """
    runner, call = api
    created = call(
        "POST",
        "/model-profiles",
        {"name": "no-key", "provider": "claude", "model": "claude-sonnet-4-5"},
    )
    call("POST", f"/model-profiles/{created['body']['id']}/activate")
    frame, _project = _session(runner)

    rebound = call("POST", f"/frames/{frame}/model-binding", {})
    assert rebound["code"] == 409, rebound
    assert rebound["body"].get("code") == "model_profile_needs_key", rebound
    assert not (runner.store.get_frame(frame) or {}).get("model_profile_id")


# --- a session the workbench created, whose profile was then deleted --------
#
# The workbench sends `model: <the composer's model name>` on `POST /frames`, so
# every session it creates records `frames.model`. Delete tombstones a profile
# and keeps the pin, the next send refuses with `model_revision_unavailable`,
# and the rebind route unpins and binds again. With `frames.model` set and
# history present that second bind took the legacy backfill branch, whose
# model-string match counted the tombstone: 409 `model_profile_needs_key` for a
# profile that can never be keyed, or `model_revision_ambiguous` for good when
# the replacement names the same model. The prompt that led there says "re-bind
# it to the active configuration", and nothing on that path looked at it.


def _profile(call, name, provider, model, api_key=None):
    body = {"name": name, "provider": provider, "model": model}
    if api_key is not None:
        body["api_key"] = api_key
    created = call("POST", "/model-profiles", body)
    assert created["code"] == 201, created
    return created["body"]["id"]


def _workbench_session(runner, call, model):
    """A session created the way the workbench creates one, then sent to once."""
    project = runner.store.create_project(name="p", description="", context="")
    if isinstance(project, dict):
        project = project["project_id"]
    created = call("POST", "/frames", {"project_id": project, "model": model})
    assert created["code"] == 200, created
    frame = created["body"]["id"]
    assert (runner.store.get_frame(frame) or {}).get("model") == model
    runner.bind_model_revision(frame)
    runner.store.add_message(root_frame_id=frame, role="user", content="hello")
    return frame


def _refused_send(call, frame):
    refused = call("POST", f"/frames/{frame}/message", {"request": "x", "wait": False})
    assert refused["code"] == 409, refused
    return refused["body"].get("code")


@pytest.mark.stubbed_backend
def test_a_workbench_session_whose_profile_was_deleted_rebinds_to_the_active_one(
    api, monkeypatch
):
    runner, call = api
    seen = _dispatch_spy(runner, monkeypatch)
    deleted = _profile(call, "A", "openai_responses", "gpt-4o", "sk-a")
    call("POST", f"/model-profiles/{deleted}/activate")
    frame = _workbench_session(runner, call, "gpt-4o")
    assert (runner.store.get_frame(frame) or {}).get("model_profile_id") == deleted

    assert call("DELETE", f"/model-profiles/{deleted}")["code"] in (200, 204)
    replacement = _profile(call, "B", "openai_responses", "gpt-4.1", "sk-b")
    call("POST", f"/model-profiles/{replacement}/activate")
    assert _refused_send(call, frame) == "model_revision_unavailable"

    rebound = call("POST", f"/frames/{frame}/model-binding", {})
    assert rebound["code"] == 200, rebound
    assert rebound["body"]["binding"]["model_profile_id"] == replacement

    accepted, result = _send(runner, call, frame, "continue")
    assert accepted["code"] == 202, accepted
    assert result and result.get("status") == "completed", result
    assert seen and seen[-1].model == "gpt-4.1" and seen[-1].api_key == "sk-b"


def test_a_replacement_naming_the_same_model_is_not_ambiguous_with_its_tombstone(
    api,
):
    """Recreating a deleted profile under the same model is the obvious repair,
    and it made the session ask "which one" forever: the tombstone still matched
    by model string, so there were always two."""
    runner, call = api
    deleted = _profile(call, "A", "openai_responses", "gpt-4o", "sk-a")
    call("POST", f"/model-profiles/{deleted}/activate")
    frame = _workbench_session(runner, call, "gpt-4o")
    call("DELETE", f"/model-profiles/{deleted}")
    replacement = _profile(call, "A again", "openai_responses", "gpt-4o", "sk-a2")
    call("POST", f"/model-profiles/{replacement}/activate")
    assert _refused_send(call, frame) == "model_revision_unavailable"

    rebound = call("POST", f"/frames/{frame}/model-binding", {})
    assert rebound["code"] == 200, rebound
    assert rebound["body"]["binding"]["model_profile_id"] == replacement


def test_a_refused_rebind_keeps_the_sessions_record(api):
    """The rebind checks the configuration it is about to pin before it drops
    the old pin. It used to unpin first, so a refusal still erased the audit
    answer to "what did this session run under"."""
    runner, call = api
    deleted = _profile(call, "A", "openai_responses", "gpt-4o", "sk-a")
    call("POST", f"/model-profiles/{deleted}/activate")
    frame = _workbench_session(runner, call, "gpt-4o")
    call("DELETE", f"/model-profiles/{deleted}")
    keyless = _profile(call, "no-key", "claude", "claude-sonnet-4-5")
    call("POST", f"/model-profiles/{keyless}/activate")

    rebound = call("POST", f"/frames/{frame}/model-binding", {})
    assert rebound["code"] == 409, rebound
    assert rebound["body"].get("code") == "model_profile_needs_key", rebound
    assert "'no-key'" in rebound["body"]["error"], rebound
    assert (runner.store.get_frame(frame) or {}).get("model_profile_id") == deleted


def test_the_rebind_route_answers_an_ambiguous_legacy_session(api):
    """`model_revision_ambiguous` is offered the same rebind, and the rebind ran
    the same ambiguous backfill again: asked, confirmed, asked again."""
    runner, call = api
    _profile(call, "east", "openai_responses", "gpt-4o", "sk-east")
    west = _profile(call, "west", "chatgpt", "gpt-4o", "sk-west")
    call("POST", f"/model-profiles/{west}/activate")
    project = runner.store.create_project(name="p", description="", context="")
    if isinstance(project, dict):
        project = project["project_id"]
    frame = runner.create_session(project)
    runner.store.update_frame(frame, model="gpt-4o")
    runner.store.add_message(root_frame_id=frame, role="user", content="hello")
    assert _refused_send(call, frame) == "model_revision_ambiguous"

    rebound = call("POST", f"/frames/{frame}/model-binding", {})
    assert rebound["code"] == 200, rebound
    assert rebound["body"]["binding"]["model_profile_id"] == west
    assert runner.bind_model_revision(frame)["model_profile_id"] == west


def test_a_legacy_session_matching_only_a_tombstone_stays_unbound(api):
    """A deleted profile is not a configuration a session can continue under,
    so it is not a backfill candidate: zero live matches is the honest unbound
    state, not a demand for a key nothing can accept."""
    runner, call = api
    deleted = _profile(call, "A", "openai_responses", "gpt-4o", "sk-a")
    call("DELETE", f"/model-profiles/{deleted}")
    project = runner.store.create_project(name="p", description="", context="")
    if isinstance(project, dict):
        project = project["project_id"]
    frame = runner.create_session(project)
    runner.store.update_frame(frame, model="gpt-4o")
    runner.store.add_message(root_frame_id=frame, role="user", content="hello")

    binding = runner.bind_model_revision(frame)
    assert binding["bound"] is False and binding["model_profile_id"] == "", binding


def test_a_legacy_backfill_is_not_ambiguous_between_a_tombstone_and_a_live_profile(
    api,
):
    """The send-path half of the same match: one live profile names the
    recorded model, so that is the backfill -- the deleted one beside it is
    history, not a second candidate."""
    runner, call = api
    deleted = _profile(call, "A", "openai_responses", "gpt-4o", "sk-a")
    call("DELETE", f"/model-profiles/{deleted}")
    live = _profile(call, "A again", "openai_responses", "gpt-4o", "sk-a2")
    project = runner.store.create_project(name="p", description="", context="")
    if isinstance(project, dict):
        project = project["project_id"]
    frame = runner.create_session(project)
    runner.store.update_frame(frame, model="gpt-4o")
    runner.store.add_message(root_frame_id=frame, role="user", content="hello")

    binding = runner.bind_model_revision(frame)
    assert binding.get("backfilled") is True, binding
    assert binding["model_profile_id"] == live, binding


def test_a_legacy_backfill_refuses_a_match_nothing_can_dispatch(api):
    """The backfill branch's own credential check. A legacy session's unique
    match with no usable key used to be pinned and then refused at dispatch; it
    is now refused before the pin, and the advice fits this branch -- the
    backfill follows the recorded model, not the active profile, so "activate
    another profile" would not help."""
    runner, call = api
    keyless = _profile(call, "legacy", "claude", "claude-sonnet-4-5")
    active = _profile(call, "other", "openai_responses", "gpt-4o", "sk-other")
    call("POST", f"/model-profiles/{active}/activate")
    project = runner.store.create_project(name="p", description="", context="")
    if isinstance(project, dict):
        project = project["project_id"]
    frame = runner.create_session(project)
    runner.store.update_frame(frame, model="claude-sonnet-4-5")
    runner.store.add_message(root_frame_id=frame, role="user", content="hello")

    refused = call("POST", f"/frames/{frame}/message", {"request": "x", "wait": False})
    assert refused["code"] == 409, refused
    assert refused["body"].get("code") == "model_profile_needs_key", refused
    assert "'legacy'" in refused["body"]["error"], refused
    assert "activate another profile" not in refused["body"]["error"], refused
    assert not (runner.store.get_frame(frame) or {}).get("model_profile_id")
    assert keyless


# --------------------------------------------------------------------------
# a pinned revision only gets the key the profile holds for its endpoint
# --------------------------------------------------------------------------
#
# The key is shared across revisions by design (`REVISIONED_FIELDS` excludes
# it), and `edit()` stores a replacement on the same profile while provider or
# base_url moves. The credential rule returned that key before it looked at the
# revision being dispatched, so a session pinned to endpoint A sent the key an
# admin had just entered for endpoint B -- or for another protocol -- to A on
# every later turn. A third-party proxy received the real OpenAI key.


def _send_this(runner, call, frame, text):
    """`_send`, waiting on the job this request was accepted as -- not the
    session's first one, which a second send would otherwise find first."""
    accepted = call(
        "POST", f"/frames/{frame}/message", {"request": text, "wait": False}
    )
    result = None
    if accepted["code"] == 202:
        result = runner._jobs[accepted["body"]["job_id"]].wait_result()
    return accepted, result


def _keyed_workbench_session(runner, call, monkeypatch, *, provider, base_url, key):
    seen = _dispatch_spy(runner, monkeypatch)
    body = {"name": "work", "provider": provider, "model": "gpt-x", "api_key": key}
    if base_url:
        body["base_url"] = base_url
    created = call("POST", "/model-profiles", body)
    assert created["code"] == 201, created
    profile_id = created["body"]["id"]
    call("POST", f"/model-profiles/{profile_id}/activate")
    frame, project = _session(runner)
    accepted, result = _send_this(runner, call, frame, "first")
    assert accepted["code"] == 202, accepted
    assert result and result.get("status") == "completed", result
    pinned = runner.store.get_frame(frame) or {}
    assert pinned.get("model_profile_id") == profile_id
    assert int(pinned.get("model_profile_revision") or 0) == 1
    seen.clear()
    return seen, frame, project, profile_id


@pytest.mark.stubbed_backend
def test_a_key_entered_for_a_new_endpoint_never_reaches_a_pinned_old_one(
    api, monkeypatch
):
    runner, call = api
    monkeypatch.setenv("OPENAI_API_KEY", _ENV_KEY)
    monkeypatch.setenv("OPENAI4S_CHATGPT_API_KEY", _ENV_KEY)
    seen, frame, project, profile_id = _keyed_workbench_session(
        runner,
        call,
        monkeypatch,
        provider="chatgpt",
        base_url="https://llm-proxy.example.org/v1",
        key="sk-proxy-OLD",
    )
    edited = call(
        "PATCH",
        f"/model-profiles/{profile_id}",
        {"base_url": "https://api.openai.com/v1", "api_key": "sk-REAL-NEW"},
    )
    assert edited["code"] == 200 and edited["body"]["revision"] == 2, edited

    refused = call(
        "POST", f"/frames/{frame}/message", {"request": "again", "wait": False}
    )
    assert refused["code"] == 409, refused
    assert refused["body"].get("code") == "model_revision_unavailable", refused
    assert "different provider or endpoint" in refused["body"]["error"], refused
    assert not seen, f"dispatched {seen[-1].base_url} with ...{seen[-1].api_key[-4:]}"
    # The dispatch half on its own -- what a turn queued before the edit reads --
    # and without borrowing the same provider's environment key instead.
    with pytest.raises(GatewayError) as dispatch:
        runner._llm_cfg(runner._state(frame, project))
    assert dispatch.value.code == 409
    assert dispatch.value.error_code == "model_revision_unavailable"
    assert (runner.store.get_frame(frame) or {}).get("model_profile_revision") == 1

    # Rebinding is the way forward, and it lands on the configuration the new
    # key was entered for.
    rebound = call("POST", f"/frames/{frame}/model-binding", {})
    assert rebound["code"] == 200, rebound
    assert rebound["body"]["binding"]["model_profile_revision"] == 2, rebound
    accepted, result = _send_this(runner, call, frame, "continue")
    assert accepted["code"] == 202, accepted
    assert result and result.get("status") == "completed", result
    assert seen[-1].base_url == "https://api.openai.com/v1"
    assert seen[-1].api_key == "sk-REAL-NEW"


def test_a_key_entered_for_another_protocol_never_reaches_a_pinned_session(api):
    runner, call = api
    created = call(
        "POST",
        "/model-profiles",
        {"name": "work", "provider": "chatgpt", "model": "gpt-x", "api_key": "sk-OLD"},
    )
    profile_id = created["body"]["id"]
    call("POST", f"/model-profiles/{profile_id}/activate")
    frame, project = _session(runner)
    assert runner.bind_model_revision(frame)["model_profile_revision"] == 1
    edited = call(
        "PATCH",
        f"/model-profiles/{profile_id}",
        {"provider": "claude", "model": "claude-x", "api_key": "sk-ant-NEW"},
    )
    assert edited["body"]["revision"] == 2, edited

    with pytest.raises(GatewayError) as bound:
        runner.bind_model_revision(frame)
    assert bound.value.error_code == "model_revision_unavailable"
    with pytest.raises(GatewayError) as dispatch:
        runner._llm_cfg(runner._state(frame, project))
    assert dispatch.value.error_code == "model_revision_unavailable"


@pytest.mark.stubbed_backend
def test_an_environment_key_is_not_sent_to_an_endpoint_the_profile_left(
    api, monkeypatch
):
    """The same disclosure through the inherited branch: a profile keyed by
    `OPENAI4S_CHATGPT_API_KEY` moves off a proxy, and a session pinned to the
    proxy revision must not go on sending the daemon's key there."""
    runner, call = api
    monkeypatch.setenv("OPENAI4S_CHATGPT_API_KEY", _ENV_KEY)
    seen = _dispatch_spy(runner, monkeypatch)
    created = call(
        "POST",
        "/model-profiles",
        {
            "name": "env-keyed",
            "provider": "chatgpt",
            "base_url": "https://llm-proxy.example.org/v1",
            "model": "gpt-x",
        },
    )
    assert created["body"]["credential_source"] == "environment", created
    profile_id = created["body"]["id"]
    call("POST", f"/model-profiles/{profile_id}/activate")
    frame, project = _session(runner)
    accepted, result = _send_this(runner, call, frame, "first")
    assert accepted["code"] == 202 and result.get("status") == "completed", result
    assert seen[-1].base_url == "https://llm-proxy.example.org/v1"
    seen.clear()

    call("PATCH", f"/model-profiles/{profile_id}", {"base_url": ""})
    refused = call("POST", f"/frames/{frame}/message", {"request": "x", "wait": False})
    assert refused["code"] == 409, refused
    assert refused["body"].get("code") == "model_revision_unavailable", refused
    assert not seen, f"dispatched {seen[-1].base_url} under the environment key"
    with pytest.raises(GatewayError):
        runner._llm_cfg(runner._state(frame, project))


@pytest.mark.stubbed_backend
def test_a_rotated_key_on_the_same_endpoint_still_reaches_the_pinned_session(
    api, monkeypatch
):
    """The guard against over-refusal: a rotation, a model-only edit, or a new
    spelling of the same default endpoint leaves the pinned revision's provider
    and endpoint where the key belongs."""
    runner, call = api
    seen, frame, _project, profile_id = _keyed_workbench_session(
        runner, call, monkeypatch, provider="chatgpt", base_url="", key="sk-OLD"
    )
    call("PATCH", f"/model-profiles/{profile_id}", {"api_key": "sk-ROTATED"})
    moved = call(
        "PATCH",
        f"/model-profiles/{profile_id}",
        {"model": "gpt-y", "base_url": "https://api.openai.com/v1/"},
    )
    assert moved["body"]["revision"] == 2, moved

    accepted, result = _send_this(runner, call, frame, "continue")
    assert accepted["code"] == 202, accepted
    assert result and result.get("status") == "completed", result
    assert seen[-1].model == "gpt-x", "the pinned revision's model"
    assert seen[-1].api_key == "sk-ROTATED"
    assert (runner.store.get_frame(frame) or {}).get("model_profile_revision") == 1


_ENV_PROXY = "https://llm-proxy.example.org/v1"


@pytest.mark.stubbed_backend
@pytest.mark.parametrize(
    "variable", ["OPENAI4S_CHATGPT_BASE_URL", "OPENAI4S_LLM_BASE_URL"]
)
def test_an_empty_base_url_is_the_endpoint_the_environment_names(
    api, monkeypatch, variable
):
    """`base_url: ""` is not "the protocol's default". Dispatch resolves it
    through `OPENAI4S_<P>_BASE_URL` -> `OPENAI4S_LLM_BASE_URL` -> default, the
    layer `LLMConfig` documents. Judged against the static default instead, a
    revision pinned to `""` -- really the proxy the environment names -- matched
    one that spelled out the official endpoint, and the key entered for that
    endpoint went to the proxy."""
    runner, call = api
    monkeypatch.setenv(variable, _ENV_PROXY)
    seen, frame, project, profile_id = _keyed_workbench_session(
        runner, call, monkeypatch, provider="chatgpt", base_url="", key="sk-proxy-OLD"
    )
    # The premise: the pinned revision really is dispatched to the proxy.
    assert runner._llm_cfg(runner._state(frame, project)).base_url == _ENV_PROXY
    edited = call(
        "PATCH",
        f"/model-profiles/{profile_id}",
        {"base_url": "https://api.openai.com/v1", "api_key": "sk-REAL-NEW"},
    )
    assert edited["code"] == 200 and edited["body"]["revision"] == 2, edited

    refused = call(
        "POST", f"/frames/{frame}/message", {"request": "again", "wait": False}
    )
    assert refused["code"] == 409, refused
    assert refused["body"].get("code") == "model_revision_unavailable", refused
    assert not seen, f"dispatched {seen[-1].base_url} with ...{seen[-1].api_key[-4:]}"
    with pytest.raises(GatewayError) as dispatch:
        runner._llm_cfg(runner._state(frame, project))
    assert dispatch.value.error_code == "model_revision_unavailable"

    rebound = call("POST", f"/frames/{frame}/model-binding", {})
    assert rebound["code"] == 200, rebound
    assert rebound["body"]["binding"]["model_profile_revision"] == 2, rebound
    accepted, result = _send_this(runner, call, frame, "continue")
    assert accepted["code"] == 202, accepted
    assert result and result.get("status") == "completed", result
    assert seen[-1].base_url == "https://api.openai.com/v1"
    assert seen[-1].api_key == "sk-REAL-NEW"


@pytest.mark.stubbed_backend
def test_a_rotation_under_an_environment_endpoint_still_reaches_the_pinned_session(
    api, monkeypatch
):
    """The over-refusal guard for the same layer: both revisions leave
    `base_url` empty, so both reach the endpoint the environment names, and a
    rotated key or a model-only edit keeps the pinned session sendable."""
    runner, call = api
    monkeypatch.setenv("OPENAI4S_CHATGPT_BASE_URL", _ENV_PROXY)
    seen, frame, _project, profile_id = _keyed_workbench_session(
        runner, call, monkeypatch, provider="chatgpt", base_url="", key="sk-OLD"
    )
    call("PATCH", f"/model-profiles/{profile_id}", {"api_key": "sk-ROTATED"})
    moved = call("PATCH", f"/model-profiles/{profile_id}", {"model": "gpt-y"})
    assert moved["body"]["revision"] == 2, moved

    accepted, result = _send_this(runner, call, frame, "continue")
    assert accepted["code"] == 202, accepted
    assert result and result.get("status") == "completed", result
    assert seen[-1].base_url == _ENV_PROXY
    assert seen[-1].model == "gpt-x", "the pinned revision's model"
    assert seen[-1].api_key == "sk-ROTATED"


# --------------------------------------------------------------------------
# a rebind with nothing active must not report a success the send refuses
# --------------------------------------------------------------------------


def test_a_rebind_with_no_active_profile_is_not_a_success_the_next_send_refuses(api):
    """Delete the active profile while two live profiles name the session's
    model: the rebind answered 200 `{bound: false}`, the client said
    "re-bound", and the next send was `model_revision_ambiguous` again --
    offering the same rebind, forever."""
    runner, call = api
    _profile(call, "east", "openai_responses", "gpt-4o", "sk-east")
    _profile(call, "west", "chatgpt", "gpt-4o", "sk-west")
    deleted = _profile(call, "A", "openai_responses", "gpt-4o", "sk-a")
    call("POST", f"/model-profiles/{deleted}/activate")
    frame = _workbench_session(runner, call, "gpt-4o")
    assert call("DELETE", f"/model-profiles/{deleted}")["code"] in (200, 204)
    assert not runner.store.get_setting("active_model_profile")
    assert _refused_send(call, frame) == "model_revision_unavailable"

    rebound = call("POST", f"/frames/{frame}/model-binding", {})
    assert rebound["code"] == 409, rebound
    assert rebound["body"].get("code") == "model_profile_needs_active", rebound
    assert "activate" in rebound["body"]["error"], rebound
    # Refused before the old pin was dropped.
    assert (runner.store.get_frame(frame) or {}).get("model_profile_id") == deleted

    # Activating one is the way out, and the rebind then binds it.
    west = next(
        item["id"]
        for item in call("GET", "/model-profiles")["body"]["profiles"]
        if item["name"] == "west"
    )
    call("POST", f"/model-profiles/{west}/activate")
    rebound = call("POST", f"/frames/{frame}/model-binding", {})
    assert rebound["code"] == 200, rebound
    assert rebound["body"]["binding"]["model_profile_id"] == west
    assert runner.bind_model_revision(frame)["model_profile_id"] == west


def test_a_rebind_with_no_active_profile_reports_the_binding_the_send_will_use(api):
    """When the unpinned session can proceed, the answer names what it proceeds
    under: unbound on the global configuration, or its unique live match."""
    runner, call = api
    deleted = _profile(call, "A", "openai_responses", "gpt-4o", "sk-a")
    call("POST", f"/model-profiles/{deleted}/activate")
    frame = _workbench_session(runner, call, "gpt-4o")
    call("DELETE", f"/model-profiles/{deleted}")

    rebound = call("POST", f"/frames/{frame}/model-binding", {})
    assert rebound["code"] == 200, rebound
    assert rebound["body"]["binding"]["bound"] is False, rebound
    assert runner.bind_model_revision(frame)["bound"] is False

    frame = _workbench_session(runner, call, "gpt-4.1")
    runner.store.update_frame(frame, model_profile_id=deleted, model_profile_revision=1)
    match = _profile(call, "B", "openai_responses", "gpt-4.1", "sk-b")
    rebound = call("POST", f"/frames/{frame}/model-binding", {})
    assert rebound["code"] == 200, rebound
    assert rebound["body"]["binding"]["bound"] is True, rebound
    assert rebound["body"]["binding"]["model_profile_id"] == match, rebound
    assert rebound["body"]["binding"].get("backfilled") is True, rebound

    frame = _workbench_session(runner, call, "claude-sonnet-4-5")
    runner.store.update_frame(frame, model_profile_id=deleted, model_profile_revision=1)
    _profile(call, "keyless", "claude", "claude-sonnet-4-5")
    rebound = call("POST", f"/frames/{frame}/model-binding", {})
    assert rebound["code"] == 409, rebound
    assert rebound["body"].get("code") == "model_profile_needs_active", rebound
    assert (runner.store.get_frame(frame) or {}).get("model_profile_id") == deleted
