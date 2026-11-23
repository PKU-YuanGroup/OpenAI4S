"""Switching models inside an open conversation switches that conversation.

The composer selector answered only `PUT /models/default`, which moves the
instance's active profile. A session is pinned to the configuration of its
first send (D2), and `_pinned_llm_config` dispatches that pin whatever is
active -- so choosing another model inside a conversation changed what *new*
sessions would bind and did nothing at all to the one on screen.

Choosing an entry while a session is open is now the explicit request D2 asks
for: `POST /frames/{id}/model-binding {model_id}` re-pins that session to the
configuration named. The bodiless form keeps its meaning (re-pin to the active
profile, the 409 prompt's answer), a `model` on a message is still never
consent, and the target's credential is still checked before the old pin goes.
"""

from __future__ import annotations

import io
import json

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth
from tests.test_team_auth_routes import (  # noqa: F401  (fixture reuse)
    _fast_pbkdf2,
    _get,
    _login,
    _post,
    _TeamDaemon,
)


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
        raw = json.dumps(body).encode("utf-8") if body is not None else b""
        handler.headers = {
            "Content-Length": str(len(raw)),
            "Content-Type": "application/json",
            local_auth.TOKEN_HEADER: token,
        }
        handler.rfile = io.BytesIO(raw)
        handler._route(method)
        return sent

    try:
        yield runner, call
    finally:
        runner.close()


def _profile(call, name, provider, model, api_key=None):
    body = {"name": name, "provider": provider, "model": model}
    if api_key is not None:
        body["api_key"] = api_key
    created = call("POST", "/model-profiles", body)
    assert created["code"] == 201, created
    return created["body"]["id"]


def _sent_session(runner, call, model="gpt-4o"):
    """A session created the way the workbench creates one, then sent to once."""
    project = runner.store.create_project(name="p", description="", context="")
    if isinstance(project, dict):
        project = project["project_id"]
    created = call("POST", "/frames", {"project_id": project, "model": model})
    assert created["code"] == 200, created
    frame = created["body"]["id"]
    runner.bind_model_revision(frame)
    runner.store.add_message(root_frame_id=frame, role="user", content="hello")
    return frame


def _pin(runner, frame):
    row = runner.store.get_frame(frame) or {}
    return row.get("model_profile_id"), row.get("model_profile_revision")


def _two_profiles(runner, call):
    """A session pinned to A, with B saved beside it. A stays active."""
    a = _profile(call, "A", "openai_responses", "gpt-4o", "sk-a")
    b = _profile(call, "B", "chatgpt", "gpt-4.1", "sk-b")
    assert call("POST", f"/model-profiles/{a}/activate")["code"] == 200
    frame = _sent_session(runner, call)
    assert _pin(runner, frame)[0] == a
    return frame, a, b


def test_choosing_a_model_in_an_open_session_pins_that_session(api):
    """The user's report: switching inside a conversation had no effect."""
    runner, call = api
    frame, a, b = _two_profiles(runner, call)

    switched = call("POST", f"/frames/{frame}/model-binding", {"model_id": b})

    assert switched["code"] == 200, switched
    binding = switched["body"]["binding"]
    assert binding["model_profile_id"] == b and binding["bound"] is True
    assert _pin(runner, frame) == (b, binding["model_profile_revision"])
    # What the next turn is dispatched under -- the thing that did not move.
    state = runner._state(frame, "")
    cfg = runner._llm_cfg(state)
    assert (cfg.provider, cfg.model, cfg.api_key) == ("chatgpt", "gpt-4.1", "sk-b")
    # The record that names the session's model follows the switch.
    assert (runner.store.get_frame(frame) or {}).get("model") == "gpt-4.1"
    # Session-scoped: the instance's active profile is the composer's separate
    # `PUT /models/default`, not a side effect of re-pinning one session.
    assert runner.store.get_setting("active_model_profile") == a


def test_the_switch_does_not_follow_later_activations(api):
    """An explicit choice is a pin like any other: activating a third profile
    afterwards moves new sessions, not this one."""
    runner, call = api
    frame, _a, b = _two_profiles(runner, call)
    c = _profile(call, "C", "openai_responses", "o3", "sk-c")
    assert (
        call("POST", f"/frames/{frame}/model-binding", {"model_id": b})["code"] == 200
    )
    assert call("POST", f"/model-profiles/{c}/activate")["code"] == 200

    assert runner.bind_model_revision(frame)["model_profile_id"] == b
    assert runner._llm_cfg(runner._state(frame, "")).model == "gpt-4.1"


@pytest.mark.stubbed_backend
def test_the_next_send_runs_on_the_chosen_model(api, monkeypatch):
    runner, call = api
    frame, _a, b = _two_profiles(runner, call)
    seen: list = []

    def _loop(st, emit, visible):
        del emit, visible
        seen.append(runner._llm_cfg(st))
        return "submitted"

    monkeypatch.setattr(runner, "_loop", _loop)
    monkeypatch.setattr(runner, "_spawn_title_summary", lambda *a, **k: None)
    assert (
        call("POST", f"/frames/{frame}/model-binding", {"model_id": b})["code"] == 200
    )

    accepted = call(
        "POST", f"/frames/{frame}/message", {"request": "continue", "wait": False}
    )
    assert accepted["code"] == 202, accepted
    result = runner._jobs[accepted["body"]["job_id"]].wait_result()
    assert result and result.get("status") == "completed", result
    assert seen and (seen[-1].model, seen[-1].api_key) == ("gpt-4.1", "sk-b")


def test_a_refused_switch_keeps_the_sessions_pin(api):
    """Checked before the old pin is dropped, exactly like the bodiless rebind,
    and the refusal names the way out that fits a choice: another model."""
    runner, call = api
    frame, a, _b = _two_profiles(runner, call)
    before = _pin(runner, frame)
    keyless = _profile(call, "no-key", "claude", "claude-sonnet-4-5")

    refused = call("POST", f"/frames/{frame}/model-binding", {"model_id": keyless})

    assert refused["code"] == 409, refused
    assert refused["body"].get("code") == "model_profile_needs_key", refused
    assert "'no-key'" in refused["body"]["error"], refused
    assert "choose another model" in refused["body"]["error"], refused
    assert _pin(runner, frame) == before and before[0] == a


def test_a_deleted_profile_cannot_be_chosen(api):
    """A selector list read before the delete still offers the tombstone."""
    runner, call = api
    frame, _a, b = _two_profiles(runner, call)
    before = _pin(runner, frame)
    assert call("DELETE", f"/model-profiles/{b}")["code"] in (200, 204)

    refused = call("POST", f"/frames/{frame}/model-binding", {"model_id": b})

    assert refused["code"] == 404, refused
    assert refused["body"].get("code") == "model_profile_not_found", refused
    assert _pin(runner, frame) == before


def test_the_live_entry_means_the_global_configuration(api):
    """The selector's first entry is the daemon's live model, a bare name and
    not a profile id. Choosing it is answered as the bodiless rebind is."""
    runner, call = api
    frame, a, b = _two_profiles(runner, call)
    assert (
        call("POST", f"/frames/{frame}/model-binding", {"model_id": b})["code"] == 200
    )
    assert _pin(runner, frame)[0] == b
    # M-7: with A active the selector lists A once, without a bare twin of it;
    # the bare live model name posted here still means the global configuration.
    entries = call("GET", "/models")["body"]["models"]["default"]
    assert [e["id"] for e in entries if not e.get("profile_id")] == []
    live = runner.store.get_setting("llm_model")
    assert live and live not in (a, b)

    back = call("POST", f"/frames/{frame}/model-binding", {"model_id": live})

    assert back["code"] == 200, back
    assert back["body"]["binding"]["model_profile_id"] == a
    assert _pin(runner, frame)[0] == a


def test_a_bodiless_rebind_still_means_the_active_profile(api):
    runner, call = api
    frame, a, b = _two_profiles(runner, call)
    assert (
        call("POST", f"/frames/{frame}/model-binding", {"model_id": b})["code"] == 200
    )

    for body in (None, {}, {"model_id": ""}, {"model_id": "   "}):
        assert (
            call("POST", f"/frames/{frame}/model-binding", {"model_id": b})["code"]
            == 200
        )
        assert _pin(runner, frame)[0] == b
        rebound = call("POST", f"/frames/{frame}/model-binding", body)
        assert rebound["code"] == 200, (body, rebound)
        assert rebound["body"]["binding"]["model_profile_id"] == a, body


@pytest.mark.parametrize("bad", [5, ["mp-x"], {"id": "mp-x"}, True])
def test_a_model_id_that_is_not_a_string_is_refused(api, bad):
    runner, call = api
    frame, a, _b = _two_profiles(runner, call)

    refused = call("POST", f"/frames/{frame}/model-binding", {"model_id": bad})

    assert refused["code"] == 400, refused
    assert refused["body"].get("code") == "invalid_model_id", refused
    assert _pin(runner, frame)[0] == a


def test_a_queued_turn_keeps_the_binding_it_was_admitted_under(api):
    """The switch applies from the next turn: an item already accepted carries
    its own frozen pair, which a re-pin of the frame must not move."""
    runner, call = api
    frame, a, b = _two_profiles(runner, call)
    frozen = runner.freeze_model_binding(frame)
    assert frozen["model_profile_id"] == a

    assert (
        call("POST", f"/frames/{frame}/model-binding", {"model_id": b})["code"] == 200
    )

    state = runner._state(frame, "")
    state.frozen_model_binding = (a, frozen["model_profile_revision"])
    assert runner._pinned_llm_config(state).model == "gpt-4o"
    state.frozen_model_binding = None
    assert runner._pinned_llm_config(state).model == "gpt-4.1"


def test_the_session_reports_its_own_pin(api):
    """The composer shows the open session's model, so the frame says which
    configuration it is pinned to -- in the list row as in the detail."""
    runner, call = api
    frame, a, b = _two_profiles(runner, call)

    shown = call("GET", f"/frames/{frame}")["body"]
    assert shown["model_profile_id"] == a
    assert shown["model_profile_revision"] == _pin(runner, frame)[1]

    call("POST", f"/frames/{frame}/model-binding", {"model_id": b})
    assert call("GET", f"/frames/{frame}")["body"]["model_profile_id"] == b
    # Every single-frame answer carries it, a rename's included.
    renamed = call("PATCH", f"/frames/{frame}", {"name": "renamed"})["body"]
    assert (renamed["model_profile_id"], renamed["model_profile_revision"]) == (
        b,
        _pin(runner, frame)[1],
    )

    project = (runner.store.get_frame(frame) or {}).get("project_id")
    rows = call("GET", f"/frames?project_id={project}")["body"]
    listed = rows["frames"] if isinstance(rows, dict) else rows
    row = next(item for item in listed if item["id"] == frame)
    assert (row["model_profile_id"], row["model_profile_revision"]) == (
        b,
        _pin(runner, frame)[1],
    )


def test_an_unsent_session_reports_no_pin(api):
    runner, call = api
    project = runner.store.create_project(name="p", description="", context="")
    if isinstance(project, dict):
        project = project["project_id"]
    frame = call("POST", "/frames", {"project_id": project})["body"]["id"]

    shown = call("GET", f"/frames/{frame}")["body"]
    assert shown["model_profile_id"] is None
    assert shown["model_profile_revision"] is None


def test_a_choice_from_a_changed_list_is_refused_not_redirected(api):
    """A non-profile value is the live entry only while it *is* the live model.
    Anything else -- a stale list, a since-removed id, a name typed for an id --
    was answered with whatever happened to be active, a pin the user never
    chose."""
    runner, call = api
    frame, a, _b = _two_profiles(runner, call)
    before = _pin(runner, frame)

    refused = call(
        "POST", f"/frames/{frame}/model-binding", {"model_id": "deepseek-chat-old"}
    )

    assert refused["code"] == 409, refused
    assert refused["body"].get("code") == "model_selection_stale", refused
    assert _pin(runner, frame) == before and before[0] == a


def test_an_unknown_session_or_a_child_frame_is_refused(api):
    runner, call = api
    frame, _a, b = _two_profiles(runner, call)

    for body in (None, {"model_id": b}):
        missing = call("POST", "/frames/no-such-frame/model-binding", body)
        assert missing["code"] == 404, missing
        assert missing["body"]["error"] == "session not found"

    child = runner.store.new_frame(
        parent_id=frame,
        project_id=(runner.store.get_frame(frame) or {}).get("project_id") or "default",
        kind="delegate",
    )
    assert (runner.store.get_frame(child) or {}).get("root_frame_id") == frame
    refused = call("POST", f"/frames/{child}/model-binding", {"model_id": b})
    assert refused["code"] == 400, refused
    assert refused["body"].get("code") == "not_a_session_root", refused
    assert not (runner.store.get_frame(child) or {}).get("model_profile_id")


@pytest.mark.stubbed_backend
@pytest.mark.parametrize("delete_when", ["before", "after"])
@pytest.mark.parametrize("selection", ["profile", "live", "bodiless"])
def test_a_session_deleted_during_a_model_switch_cannot_report_success(
    api, monkeypatch, delete_when, selection
):
    runner, call = api
    frame, _a, b = _two_profiles(runner, call)
    # The bare live model name (the selector lists the active profile instead, M-7).
    live = runner.store.get_setting("llm_model")
    body = (
        {"model_id": b if selection == "profile" else live}
        if selection != "bodiless"
        else None
    )
    update_frame = runner.store.update_frame

    def delete_during_write(frame_id, **kwargs):
        if frame_id == frame and delete_when == "before":
            runner.store.delete_frame(frame)
        result = update_frame(frame_id, **kwargs)
        if frame_id == frame and delete_when == "after":
            runner.store.delete_frame(frame)
        return result

    monkeypatch.setattr(runner.store, "update_frame", delete_during_write)
    refused = call("POST", f"/frames/{frame}/model-binding", body)

    assert refused["code"] == 404, refused
    assert refused["body"]["error"] == "session not found"
    assert refused["body"]["code"] == "not_found"
    assert runner.store.get_frame(frame) is None


def test_with_no_profile_active_the_live_entry_leaves_the_session_unpinned(api):
    """The global configuration is then the `llm_*` settings themselves. The
    switch away from it recorded the chosen profile's model in `frames.model`,
    which the legacy backfill matches -- so without care, choosing the live
    entry backfilled straight back to the profile just left."""
    runner, call = api
    b = _profile(call, "B", "chatgpt", "gpt-4.1", "sk-b")
    frame = _sent_session(runner, call)
    assert runner.store.get_setting("active_model_profile") in (None, "")
    assert (
        call("POST", f"/frames/{frame}/model-binding", {"model_id": b})["code"] == 200
    )
    assert _pin(runner, frame)[0] == b
    live = call("GET", "/models")["body"]["models"]["default"][0]["id"]

    back = call("POST", f"/frames/{frame}/model-binding", {"model_id": live})

    assert back["code"] == 200, back
    assert back["body"]["binding"]["bound"] is False
    assert not _pin(runner, frame)[0]
    assert (runner.store.get_frame(frame) or {}).get("model") == live
    # And the next send does not re-pin B behind the user's back.
    assert runner.bind_model_revision(frame)["model_profile_id"] == ""
    assert not _pin(runner, frame)[0]


def _no_active_session_on(runner, call, target):
    """A sent session pinned to `target`, with no profile active."""
    frame = _sent_session(runner, call)
    assert runner.store.get_setting("active_model_profile") in (None, "")
    assert (
        call("POST", f"/frames/{frame}/model-binding", {"model_id": target})["code"]
        == 200
    )
    return frame


def test_with_no_profile_active_the_live_entry_takes_the_next_sends_decision_now(
    api,
):
    """With exactly one live profile naming the live model, the next send's
    legacy backfill would pin it -- so choosing the live entry pins it now,
    rather than reporting "unpinned" and being contradicted by that send."""
    runner, call = api
    live = call("GET", "/models")["body"]["models"]["default"][0]["id"]
    same = _profile(call, "same-model", "chatgpt", live, "sk-same")
    other = _profile(call, "other", "chatgpt", "gpt-4.1", "sk-other")
    frame = _no_active_session_on(runner, call, other)

    back = call("POST", f"/frames/{frame}/model-binding", {"model_id": live})

    assert back["code"] == 200, back
    assert back["body"]["binding"]["model_profile_id"] == same
    assert runner.bind_model_revision(frame)["model_profile_id"] == same


def test_a_refused_live_entry_choice_changes_nothing(api):
    """Two live profiles name the live model and none is active: the next send
    could not choose between them. That refusal used to land *after* the unpin,
    so a session that sent fine was left unpinned and unable to send, while the
    409 said nothing had changed."""
    runner, call = api
    live = call("GET", "/models")["body"]["models"]["default"][0]["id"]
    _profile(call, "east", "chatgpt", live, "sk-east")
    _profile(call, "west", "openai_responses", live, "sk-west")
    other = _profile(call, "other", "chatgpt", "gpt-4.1", "sk-other")
    frame = _no_active_session_on(runner, call, other)
    before = (_pin(runner, frame), (runner.store.get_frame(frame) or {}).get("model"))

    refused = call("POST", f"/frames/{frame}/model-binding", {"model_id": live})

    assert refused["code"] == 409, refused
    assert refused["body"].get("code") == "model_profile_needs_active", refused
    after = (_pin(runner, frame), (runner.store.get_frame(frame) or {}).get("model"))
    assert after == before
    assert runner.bind_model_revision(frame)["model_profile_id"] == other


@pytest.mark.stubbed_backend
def test_an_item_admitted_unpinned_is_not_moved_by_a_later_pin(api, monkeypatch):
    """The ticket froze `("", 0)` but handed the turn None, which meant "bind at
    dequeue": a pin the composer set after the 202 was adopted by an item that
    was accepted before it."""
    runner, call = api
    b = _profile(call, "B", "chatgpt", "gpt-4.1", "sk-b")
    project = runner.store.create_project(name="p", description="", context="")
    if isinstance(project, dict):
        project = project["project_id"]
    frame = call("POST", "/frames", {"project_id": project})["body"]["id"]
    seen: list = []
    entered = __import__("threading").Event()
    release = __import__("threading").Event()

    def _loop(st, emit, visible):
        del emit, visible
        if not entered.is_set():
            entered.set()
            release.wait(10)
        seen.append(runner._llm_cfg(st).model)
        return "submitted"

    monkeypatch.setattr(runner, "_loop", _loop)
    monkeypatch.setattr(runner, "_spawn_title_summary", lambda *a, **k: None)
    first = call("POST", f"/frames/{frame}/message", {"request": "1", "wait": False})
    assert first["code"] == 202, first
    assert entered.wait(10)
    queued = call("POST", f"/frames/{frame}/message", {"request": "2", "wait": False})
    assert queued["code"] == 202, queued
    assert queued["body"]["model_binding"] == {
        "model_profile_id": "",
        "model_profile_revision": 0,
    }
    assert (
        call("POST", f"/frames/{frame}/model-binding", {"model_id": b})["code"] == 200
    )
    release.set()
    runner._jobs[queued["body"]["job_id"]].wait_result()

    assert seen[1] != "gpt-4.1", seen
    assert _pin(runner, frame)[0] == b


@pytest.mark.stubbed_backend
def test_a_direct_turn_keeps_its_binding_when_the_session_is_re_pinned_mid_turn(
    api, monkeypatch
):
    """Plan approve/resume/revise run `run_message` with no frozen pair. They
    used to re-read the frame on every `_llm_cfg`, so a switch landing mid-turn
    moved screening, kernel wiring and the reviewer while the ledger and the
    loop stayed on the old pin: recorded as A, run partly as B."""
    runner, call = api
    frame, _a, b = _two_profiles(runner, call)
    seen: list = []

    def _loop(st, emit, visible):
        del emit, visible
        seen.append(runner._llm_cfg(st).model)
        runner.choose_session_model(frame, b)
        seen.append(runner._llm_cfg(st).model)
        return "submitted"

    monkeypatch.setattr(runner, "_loop", _loop)
    monkeypatch.setattr(runner, "_spawn_title_summary", lambda *a, **k: None)
    project = (runner.store.get_frame(frame) or {}).get("project_id") or "default"

    runner.run_message(frame, project, "go")

    assert seen == ["gpt-4o", "gpt-4o"]
    assert _pin(runner, frame)[0] == b


@pytest.mark.stubbed_backend
def test_a_finished_turn_does_not_leave_its_pin_behind(api, monkeypatch):
    """The frozen pair lives as long as the turn. It was never cleared, so after
    a turn everything resolving the session's model between turns -- the
    context projection, the REPL, export -- kept the previous turn's pin even
    after the composer re-pinned the session."""
    runner, call = api
    frame, _a, b = _two_profiles(runner, call)
    monkeypatch.setattr(runner, "_loop", lambda st, emit, visible: "submitted")
    monkeypatch.setattr(runner, "_spawn_title_summary", lambda *a, **k: None)

    accepted = call("POST", f"/frames/{frame}/message", {"request": "x", "wait": False})
    assert accepted["code"] == 202, accepted
    runner._jobs[accepted["body"]["job_id"]].wait_result()
    state = runner._state(frame, "")
    assert state.frozen_model_binding is None

    assert (
        call("POST", f"/frames/{frame}/model-binding", {"model_id": b})["code"] == 200
    )
    assert runner._llm_cfg(state).model == "gpt-4.1"


@pytest.mark.stubbed_backend
def test_a_model_on_a_message_is_still_never_consent_to_re_pin(api, monkeypatch):
    """Behavioural, beside the source check on `run_message`: a client that
    sends `model` with a message gets the session's own pin, unchanged."""
    runner, call = api
    frame, a, b = _two_profiles(runner, call)
    seen: list = []

    def _loop(st, emit, visible):
        del emit, visible
        seen.append(runner._llm_cfg(st).model)
        return "submitted"

    monkeypatch.setattr(runner, "_loop", _loop)
    monkeypatch.setattr(runner, "_spawn_title_summary", lambda *a, **k: None)

    accepted = call(
        "POST",
        f"/frames/{frame}/message",
        {"request": "x", "model": b, "wait": False},
    )
    assert accepted["code"] == 202, accepted
    runner._jobs[accepted["body"]["job_id"]].wait_result()
    assert _pin(runner, frame)[0] == a
    assert seen == ["gpt-4o"]


@pytest.mark.stubbed_backend
def test_the_202_names_the_binding_the_turn_was_admitted_under(api, monkeypatch):
    """A first send is what pins a fresh session. The composer learns that pin
    from the 202 instead of showing the session as unpinned -- that is, as
    whatever the default becomes later."""
    runner, call = api
    a = _profile(call, "A", "openai_responses", "gpt-4o", "sk-a")
    assert call("POST", f"/model-profiles/{a}/activate")["code"] == 200
    monkeypatch.setattr(runner, "_loop", lambda st, emit, visible: "submitted")
    monkeypatch.setattr(runner, "_spawn_title_summary", lambda *a, **k: None)
    project = runner.store.create_project(name="p", description="", context="")
    if isinstance(project, dict):
        project = project["project_id"]
    frame = call("POST", "/frames", {"project_id": project})["body"]["id"]
    assert not _pin(runner, frame)[0]

    accepted = call("POST", f"/frames/{frame}/message", {"request": "x", "wait": False})

    assert accepted["code"] == 202, accepted
    assert accepted["body"]["model_binding"] == {
        "model_profile_id": a,
        "model_profile_revision": _pin(runner, frame)[1],
    }
    runner._jobs[accepted["body"]["job_id"]].wait_result()


def _hold_first_turn(runner, monkeypatch):
    """Stub the loop so the first turn blocks until released; record models."""
    import threading

    seen: list = []
    entered = threading.Event()
    release = threading.Event()

    def _loop(st, emit, visible):
        del emit, visible
        if not entered.is_set():
            entered.set()
            release.wait(10)
        seen.append(runner._llm_cfg(st).model)
        return "submitted"

    monkeypatch.setattr(runner, "_loop", _loop)
    monkeypatch.setattr(runner, "_spawn_title_summary", lambda *a, **k: None)
    return seen, entered, release


@pytest.mark.stubbed_backend
def test_a_queued_item_runs_on_the_pin_it_was_admitted_under(api, monkeypatch):
    """Through the real FIFO: a follow-up accepted under A, then a switch to B
    while it waits, runs on A; the switch applies from the next message."""
    runner, call = api
    frame, a, b = _two_profiles(runner, call)
    seen, entered, release = _hold_first_turn(runner, monkeypatch)
    first = call("POST", f"/frames/{frame}/message", {"request": "1", "wait": False})
    assert first["code"] == 202, first
    assert entered.wait(10)
    queued = call("POST", f"/frames/{frame}/message", {"request": "2", "wait": False})
    assert queued["code"] == 202, queued
    assert queued["body"]["model_binding"]["model_profile_id"] == a

    assert (
        call("POST", f"/frames/{frame}/model-binding", {"model_id": b})["code"] == 200
    )
    release.set()
    runner._jobs[queued["body"]["job_id"]].wait_result()
    after = call("POST", f"/frames/{frame}/message", {"request": "3", "wait": False})
    runner._jobs[after["body"]["job_id"]].wait_result()

    assert seen == ["gpt-4o", "gpt-4o", "gpt-4.1"], seen


@pytest.mark.stubbed_backend
def test_a_plan_style_turn_drops_its_pin_when_it_ends(api, monkeypatch):
    """Plan approve/resume/revise run through `_spawn_job`, not the message
    queue; their frozen pair has to be dropped at the end of the lease too."""
    runner, call = api
    frame, _a, b = _two_profiles(runner, call)
    monkeypatch.setattr(runner, "_loop", lambda st, emit, visible: "submitted")
    monkeypatch.setattr(runner, "_spawn_title_summary", lambda *a, **k: None)
    project = (runner.store.get_frame(frame) or {}).get("project_id") or "default"
    frozen_during: list = []

    def _turn():
        result = runner.run_message(frame, project, "go")
        frozen_during.append(runner._state(frame, project).frozen_model_binding)
        return result

    job = runner._spawn_job(frame, _turn, project_id=project)
    job.wait_result()

    assert frozen_during and frozen_during[0] is not None
    state = runner._state(frame, project)
    assert state.frozen_model_binding is None
    assert (
        call("POST", f"/frames/{frame}/model-binding", {"model_id": b})["code"] == 200
    )
    assert runner._llm_cfg(state).model == "gpt-4.1"


def test_a_re_pin_waits_for_a_bind_already_in_progress(api):
    """The composer's re-pin and a send's first bind share one lock, so a send
    that read "unpinned" cannot overwrite the user's choice a moment later."""
    import threading

    runner, call = api
    frame, _a, b = _two_profiles(runner, call)
    done = threading.Event()
    runner._model_binding_lock.acquire()
    try:
        worker = threading.Thread(
            target=lambda: (runner.choose_session_model(frame, b), done.set())
        )
        worker.start()
        assert not done.wait(0.3), "the re-pin did not wait for the held bind"
    finally:
        runner._model_binding_lock.release()
    assert done.wait(10)
    worker.join(10)
    assert _pin(runner, frame)[0] == b


def test_the_selector_entries_say_which_revision_they_pin(api):
    """A session pinned to an earlier revision of a profile shows that, rather
    than the profile's entry -- re-choosing an already-selected option fires no
    change, so the newer revision could not otherwise be chosen."""
    runner, call = api
    frame, a, _b = _two_profiles(runner, call)
    pinned = _pin(runner, frame)[1]
    assert call("PATCH", f"/model-profiles/{a}", {"model": "gpt-4o-2"})["code"] == 200

    entries = call("GET", "/models")["body"]["models"]["default"]
    entry = next(item for item in entries if item["id"] == a)
    assert entry["revision"] > pinned
    assert call("GET", f"/frames/{frame}")["body"]["model_profile_revision"] == pinned

    moved = call("POST", f"/frames/{frame}/model-binding", {"model_id": a})
    assert moved["code"] == 200, moved
    assert moved["body"]["binding"]["model_profile_revision"] == entry["revision"]
    assert runner._llm_cfg(runner._state(frame, "")).model == "gpt-4o-2"


# --- team mode -------------------------------------------------------------
#
# The instance default is an admin's switch (`/models/default` is instance
# config). A member's own session is theirs, so the composer can still switch
# it -- and only its owner can.


@pytest.fixture()
def team(tmp_path):
    node = _TeamDaemon(tmp_path)
    node.seed_user("root", "fake-pw-r", role="admin")
    node.seed_user("alice", "fake-pw-a")
    node.seed_user("bob", "fake-pw-b")
    node.seed_user("carol", "fake-pw-c")
    node.store.create_project(name="proj-one", description="", context="")
    try:
        yield node
    finally:
        node.close()


def _team_body(raw: bytes) -> dict:
    return json.loads(raw.split(b"\r\n\r\n", 1)[1].decode("utf-8"))


def _team_profile(node, cookie, name, provider, model, key):
    status, raw = _post(
        node.port,
        "/api/v1/model-profiles",
        {"name": name, "provider": provider, "model": model, "api_key": key},
        cookie=cookie,
    )
    assert status == 201, raw[:300]
    return _team_body(raw)["id"]


def test_a_member_switches_their_own_session_but_not_the_default(team):
    root = _login(team, "root", "fake-pw-r")
    alice = _login(team, "alice", "fake-pw-a")
    bob = _login(team, "bob", "fake-pw-b")
    a = _team_profile(team, root, "A", "openai_responses", "gpt-4o", "sk-a")
    b = _team_profile(team, root, "B", "chatgpt", "gpt-4.1", "sk-b")
    status, _ = _post(
        team.port, f"/api/v1/model-profiles/{a}/activate", {}, cookie=root
    )
    assert status == 200

    pid = str(team.store.list_projects()[0]["project_id"])
    status, raw = _post(team.port, "/api/v1/frames", {"project_id": pid}, cookie=alice)
    assert status == 200, raw[:300]
    frame = str(_team_body(raw).get("id"))
    team.runner.bind_model_revision(frame)
    team.store.governance.set_member(
        pid, team.store.team.get_user_by_username("bob")["id"], "member"
    )
    team.store.governance.set_member(
        pid, team.store.team.get_user_by_username("alice")["id"], "member"
    )

    # The default stays an admin's.
    status, raw = _post(
        team.port, "/api/v1/models/default", {"model_id": b}, cookie=alice
    )
    assert status == 403 and _team_body(raw).get("code") == "admin_only"

    # A project member who can read alice's session cannot re-pin it...
    assert _get(team.port, f"/api/v1/frames/{frame}", cookie=bob)[0] == 200
    status, raw = _post(
        team.port,
        f"/api/v1/frames/{frame}/model-binding",
        {"model_id": b},
        cookie=bob,
    )
    assert status == 403, raw[:300]
    assert _team_body(raw).get("code") == "owner_only"
    # ...and to someone who cannot see it, it does not exist.
    carol = _login(team, "carol", "fake-pw-c")
    status, raw = _post(
        team.port,
        f"/api/v1/frames/{frame}/model-binding",
        {"model_id": b},
        cookie=carol,
    )
    assert status == 404, raw[:300]
    assert (team.store.get_frame(frame) or {}).get("model_profile_id") == a

    # A profile with no key is refused in words a member can act on.
    keyless = _team_profile(team, root, "no-key", "claude", "claude-x", "")
    status, raw = _post(
        team.port,
        f"/api/v1/frames/{frame}/model-binding",
        {"model_id": keyless},
        cookie=alice,
    )
    assert status == 409, raw[:300]
    refusal = _team_body(raw)
    assert refusal.get("code") == "model_profile_needs_key"
    assert "ask an admin" in refusal["error"] and "Customize" not in refusal["error"]

    # Its owner can.
    status, raw = _post(
        team.port,
        f"/api/v1/frames/{frame}/model-binding",
        {"model_id": b},
        cookie=alice,
    )
    assert status == 200, raw[:300]
    assert (team.store.get_frame(frame) or {}).get("model_profile_id") == b
    assert team.store.get_setting("active_model_profile") == a
    status, raw = _get(team.port, f"/api/v1/frames/{frame}", cookie=alice)
    assert status == 200 and _team_body(raw)["model_profile_id"] == b

    # An admin may act on a member's session, as on every session control.
    status, raw = _post(
        team.port,
        f"/api/v1/frames/{frame}/model-binding",
        {"model_id": a},
        cookie=root,
    )
    assert status == 200, raw[:300]
    assert (team.store.get_frame(frame) or {}).get("model_profile_id") == a
