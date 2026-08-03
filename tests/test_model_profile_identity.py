"""A pinned session must dispatch to the profile it recorded, and nothing else.

The `profile_id + immutable revision` machinery is real and wired: revisions are
append-only, the frame carries the pin, and `POST /frames/{id}/model-binding`
answers the 409. What is missing is that three separate paths can still send the
turn somewhere the pin does not name.

**A bare `model` field overrides the pinned model.** `_pinned_llm_config` builds
`model=str(st.model or recorded.get("model") or "")` and the comment justifies it
as "the composer's per-session choice ... still wins". But `st.model` is the
request's bare `model` string, which the browser sends on **every** message as
`S.defaultModel`. So the provider, endpoint and credential come from the pinned
revision and the model name comes from the header selector: a config that exists
in no profile, recorded as A and executed as a chimera.

**An unresolvable credential falls back to the globally active profile.**
`_pinned_llm_config` returns `None` when `resolve_key` yields nothing — and on any
exception at all — after which `_llm_cfg` calls `resolve_llm_config`, which is
whichever profile is active *now*. A revoked key therefore does not refuse; it
silently runs the turn on a different provider. Recorded as A, executed as B, with
the frame still saying A.

**Delete leaves nothing to rebind to.** The row and its whole revision history go,
and `release_model_binding` NULLs the pin on every frame, so the next send
re-pins by bare model string or to the active profile with no prompt — losing the
audit answer to "what did this session run under".

Every test drives the real `SessionRunner`/`ModelProfileService`. No test contacts
a provider: all three defects are decided before the request is built.
"""
from __future__ import annotations

import pytest

from openai4s.config import Config, LLMConfig


def _cfg(tmp_path) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="global-key", model="global-model"),
    )


class _Hub:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emitter(self, root_frame_id: str):
        def emit(event: dict) -> None:
            self.events.append(event)

        return emit

    def broadcast(self, root_frame_id: str, event: dict) -> None:
        self.events.append(event)


@pytest.fixture
def runner(tmp_path):
    from openai4s.server import gateway as gateway_mod

    return gateway_mod.SessionRunner(_cfg(tmp_path), _Hub())


def _service(runner):
    from openai4s.server.gateway import PROVIDERS
    from openai4s.server.model_profiles import ModelProfileService

    return ModelProfileService(runner.store, runner.cfg, providers=lambda: PROVIDERS)


def _revoke_key(runner, profile_id):
    """Clear a saved profile's credential, leaving the profile itself in place."""

    def clear(profiles):
        for item in profiles:
            if item.get("id") == profile_id:
                item["api_key"] = ""

    runner.store.mutate_model_profiles(clear)


def _profile(runner, *, name, provider, model, base_url, key="k"):
    service = _service(runner)
    return service.create(
        {
            "name": name,
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "api_key": key,
        }
    )


# --- identity in the selector ------------------------------------------------


def test_two_profiles_with_one_model_name_both_survive_to_the_selector(runner):
    """`models_payload` deduped on a `seen: set[str]` of bare model ids, so the
    second provider's entry vanished — and the option value the browser persisted
    was that same bare string, which cannot express which profile was meant."""
    _profile(
        runner,
        name="vendor A",
        provider="claude",
        model="shared-model",
        base_url="https://a.invalid",
    )
    _profile(
        runner,
        name="vendor B",
        provider="gemini",
        model="shared-model",
        base_url="https://b.invalid",
    )

    payload = _service(runner).models_payload("")
    entries = payload["models"]["default"]
    with_profiles = [e for e in entries if e.get("profile_id")]

    assert len(with_profiles) == 2, (
        f"two profiles sharing a model name collapsed to {len(with_profiles)}: "
        f"{entries}"
    )
    assert len({e["profile_id"] for e in with_profiles}) == 2
    # And each carries enough to be told apart by a human and by the backend.
    assert {e["provider"] for e in with_profiles} == {"claude", "gemini"}
    assert {e["base_url"] for e in with_profiles} == {
        "https://a.invalid",
        "https://b.invalid",
    }


def test_the_selector_option_value_is_a_profile_id_not_a_model_name(runner):
    created = _profile(
        runner,
        name="only",
        provider="claude",
        model="some-model",
        base_url="https://a.invalid",
    )
    entries = _service(runner).models_payload("")["models"]["default"]
    entry = next(e for e in entries if e.get("profile_id"))
    assert entry["profile_id"] == created["id"]


# --- the pin decides the dispatch -------------------------------------------


def _pin(runner, frame_id, profile):
    """Write the pin the way `bind_model_revision` does: through `update_frame`."""
    revisions = profile.get("revisions") or []
    revision = int(revisions[-1].get("revision") or 1) if revisions else 1
    runner.store.update_frame(
        frame_id,
        model_profile_id=profile["id"],
        model_profile_revision=revision,
    )
    return revision


def test_a_bare_model_in_the_request_does_not_override_the_pin(runner):
    """The chimera. Provider, endpoint and credential from the pin; model name
    from the header selector — a configuration that exists in no profile.

    The browser sends this on every single message, so it is the normal case
    rather than an edge one.
    """
    profile = _profile(
        runner,
        name="pinned",
        provider="claude",
        model="pinned-model",
        base_url="https://pinned.invalid",
    )
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    _pin(runner, frame_id, profile)

    state = runner._state(frame_id, "default")
    state.model = "something-the-selector-said"

    config = runner._pinned_llm_config(state)
    assert config is not None
    assert (
        config.model == "pinned-model"
    ), f"the request's bare model overrode the pinned revision: {config.model!r}"
    assert config.base_url == "https://pinned.invalid"
    assert config.provider == "claude"


def test_a_pinned_session_with_an_unresolvable_key_refuses_instead_of_falling_back(
    runner,
):
    """`return None` here means `_llm_cfg` uses the globally active profile, so a
    revoked key silently sends the turn to a different provider while the frame
    still records the pinned one."""
    from openai4s.server.gateway import GatewayError

    profile = _profile(
        runner,
        name="pinned",
        provider="claude",
        model="pinned-model",
        base_url="https://pinned.invalid",
    )
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    _pin(runner, frame_id, profile)
    state = runner._state(frame_id, "default")

    # The key is revoked out from under the pin, as clearing it in the UI does.
    _revoke_key(runner, profile["id"])

    with pytest.raises(GatewayError) as error:
        runner._llm_cfg(state)
    assert error.value.code == 409
    assert error.value.error_code == "model_revision_unavailable"


def test_an_unpinned_session_still_uses_the_active_profile(runner):
    """The fallback is correct when there is no pin — removing it entirely would
    break every session that never chose a profile."""
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    config = runner._llm_cfg(state)
    assert config is not None
    assert config.provider == "deepseek"


def test_binding_refuses_a_profile_whose_credential_does_not_resolve(runner):
    """Caught at bind time, before the frame records a pin it cannot honour."""
    from openai4s.server.gateway import GatewayError

    profile = _profile(
        runner,
        name="keyless",
        provider="claude",
        model="m",
        base_url="https://k.invalid",
    )
    _revoke_key(runner, profile["id"])
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")

    # Bound first (as the send path does), then the credential is checked.
    _pin(runner, frame_id, profile)
    with pytest.raises(GatewayError) as error:
        runner.bind_model_revision(frame_id)
    assert error.value.code == 409


# --- delete is a tombstone --------------------------------------------------


def test_deleting_a_profile_keeps_its_revisions_for_the_sessions_that_ran_on_it(
    runner,
):
    """A pin is the audit answer to "what did this session run under". Hard
    deleting the row and NULLing every frame's pin destroys that answer and lets
    the next send silently re-pin somewhere else."""
    profile = _profile(
        runner,
        name="gone",
        provider="claude",
        model="retired-model",
        base_url="https://gone.invalid",
    )
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    revision = _pin(runner, frame_id, profile)

    _service(runner).delete(profile["id"])

    frame = runner.store.get_frame(frame_id)
    assert (
        frame["model_profile_id"] == profile["id"]
    ), "deleting the profile erased the session's record of what it ran under"
    assert int(frame["model_profile_revision"]) == revision

    # The tombstoned profile is still resolvable for history...
    stored = next(
        (p for p in runner.store.list_model_profiles() if p["id"] == profile["id"]),
        None,
    )
    assert stored is not None, "the profile row was hard-deleted"
    assert stored.get("deleted_at")
    assert stored.get("revisions"), "the revision history went with the row"


def test_a_deleted_profile_is_not_selectable(runner):
    """...and must not come back in any chooser, or delete means nothing."""
    profile = _profile(
        runner,
        name="gone",
        provider="claude",
        model="retired-model",
        base_url="https://gone.invalid",
    )
    _service(runner).delete(profile["id"])

    entries = _service(runner).models_payload("")["models"]["default"]
    assert profile["id"] not in {e.get("profile_id") for e in entries}

    payload, _active = _service(runner).profiles_payload()
    assert profile["id"] not in {p.get("id") for p in payload.get("profiles", [])}


def test_a_session_pinned_to_a_deleted_profile_refuses_with_a_rebind(runner):
    """The refusal has to be actionable: 409 plus the existing
    `POST /frames/{id}/model-binding` route, not a silent substitution."""
    from openai4s.server.gateway import GatewayError

    profile = _profile(
        runner,
        name="gone",
        provider="claude",
        model="retired-model",
        base_url="https://gone.invalid",
    )
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    _pin(runner, frame_id, profile)
    _service(runner).delete(profile["id"])

    state = runner._state(frame_id, "default")
    with pytest.raises(GatewayError) as error:
        runner._llm_cfg(state)
    assert error.value.code == 409


# --- item 13 sub-defect (5): the queued follow-up ----------------------------
#
# `submit_message` freezes the identity at send, and its comment says so. But it
# freezes it onto the *frame*, and the frame's pin is mutable: `POST
# /frames/{id}/model-binding` rewrites it by design, because that route is the
# answer to a dangling pin. So a follow-up accepted under profile P, still sitting
# in the FIFO, is re-resolved from the frame when `run_message` calls
# `bind_model_revision` again at dequeue -- and by then the frame may say Q.
#
# The client was told 202 under P. Nothing tells it the work ran on Q.


def test_a_queued_follow_up_records_the_binding_it_was_accepted_under(runner):
    """The freeze has to be on the ticket, not only on the frame.

    A `MessageJob` that carries no binding cannot detect that the frame moved
    underneath it, which is the whole difference between "frozen at send" and
    "read again at dequeue".
    """
    profile = _profile(
        runner,
        name="P",
        provider="claude",
        model="p-model",
        base_url="https://p.invalid",
        key="pk",
    )
    runner.store.set_setting("active_model_profile", profile["id"])
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")

    job = runner.submit_message(frame_id, "default", "first")
    try:
        assert job.model_profile_id == profile["id"], (
            "the queued job does not record which configuration it was accepted "
            "under, so it cannot tell that the frame moved"
        )
        assert int(job.model_profile_revision) > 0
    finally:
        # The turn itself will fail (no provider is reachable); this test is about
        # what the ticket recorded at admission, which happens before the thread.
        job.done.wait(timeout=60)


def test_a_rebind_while_an_item_is_queued_does_not_move_that_item(runner):
    """The defect, as the sequence a user can actually produce.

    Accept a follow-up under P, then rebind the session to Q -- which is a
    supported, documented action -- and the queued item dispatched to Q. It was
    accepted under P and the client was told so.
    """
    p = _profile(
        runner,
        name="P",
        provider="claude",
        model="p-model",
        base_url="https://p.invalid",
        key="pk",
    )
    q = _profile(
        runner,
        name="Q",
        provider="gemini",
        model="q-model",
        base_url="https://q.invalid",
        key="qk",
    )
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    _pin(runner, frame_id, p)

    frozen = runner.freeze_model_binding(frame_id)
    assert frozen["model_profile_id"] == p["id"]

    # The user rebinds the session to Q while the item is still queued.
    runner.store.unpin_model(frame_id)
    runner.store.set_setting("active_model_profile", q["id"])
    runner.bind_model_revision(frame_id)
    assert (runner.store.get_frame(frame_id) or {}).get("model_profile_id") == q["id"]

    # The queued item still resolves to what it was accepted under.
    state = runner._state(frame_id, "default")
    state.frozen_model_binding = (
        frozen["model_profile_id"],
        int(frozen["model_profile_revision"]),
    )
    config = runner._pinned_llm_config(state)
    assert (
        config.model == "p-model"
    ), f"the queued item followed the frame's new pin: {config.model!r}"
    assert config.base_url == "https://p.invalid"


def test_a_queued_item_whose_frozen_profile_died_fails_visibly(runner):
    """It cannot run -- the credential is gone -- but it must not silently run
    somewhere else either. The same stable code, raised where the job's error
    surfaces rather than swallowed into a fallback."""
    from openai4s.server.gateway import GatewayError

    p = _profile(
        runner,
        name="P",
        provider="claude",
        model="p-model",
        base_url="https://p.invalid",
        key="pk",
    )
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    _pin(runner, frame_id, p)
    frozen = runner.freeze_model_binding(frame_id)

    _service(runner).delete(p["id"])

    state = runner._state(frame_id, "default")
    state.frozen_model_binding = (
        frozen["model_profile_id"],
        int(frozen["model_profile_revision"]),
    )
    with pytest.raises(GatewayError) as error:
        runner._llm_cfg(state)
    assert error.value.code == 409
    assert error.value.error_code == "model_revision_unavailable"


# --- three mechanisms that existed, each wired to one call site ------------


def test_an_endpoint_with_credentials_is_never_stored_published_or_sealed(runner):
    """7.2 says secrets do not enter the snapshot. The key obeyed; the URL did not.

    `base_url` was stored as typed, returned verbatim by `GET /model-profiles`,
    and frozen verbatim into an immutable revision. Measured before this change:
    `https://user:s3cr3t@api.internal.corp/v1?key=abc` came back from the public
    projection with the password in it, and the sealed entry kept it for good --
    the one field of a revision that is designed never to change.

    `doctor._sanitize_endpoint` already dropped userinfo and query, and said so
    in its docstring. It had one call site, in the diagnostics report.
    """
    service = _service(runner)
    created = service.create(
        {
            "name": "leaky",
            "provider": "chatgpt",
            "api_key": "sk-test",
            "model": "gpt-4o",
            "base_url": "https://user:s3cr3t@api.internal.corp/v1/?key=abc",
        }
    )

    row = next(
        item
        for item in runner.store.list_model_profiles()
        if item["id"] == created["id"]
    )
    sealed = (row.get("revisions") or [])[-1]

    for surface, value in (
        ("stored", row["base_url"]),
        ("published", service.public_profile(row)["base_url"]),
        ("sealed", sealed["base_url"]),
    ):
        assert "s3cr3t" not in value, surface
        assert "key=abc" not in value, surface
        assert value == "https://api.internal.corp/v1", surface


def test_two_spellings_of_one_endpoint_are_one_configuration(runner):
    """A trailing slash made `https://h/v1` and `https://h/v1/` two revisions of
    the same thing, which is the opposite of what an immutable revision is for."""
    service = _service(runner)
    created = service.create(
        {
            "name": "slash",
            "provider": "chatgpt",
            "api_key": "sk-test",
            "model": "gpt-4o",
            "base_url": "https://api.example.com/v1",
        }
    )
    service.edit(created["id"], {"base_url": "https://api.example.com/v1/"})

    row = next(
        item
        for item in runner.store.list_model_profiles()
        if item["id"] == created["id"]
    )
    assert len(row.get("revisions") or []) == 1, "a trailing slash sealed a revision"


def test_a_loopback_profile_is_ready_without_a_key(runner):
    """`resolve.is_loopback_endpoint` says demanding a key from a local endpoint
    "is demanding a credential that does not exist", `chat()` honours it and
    `doctor` honours it. `readiness` did not, so a working Ollama profile read
    `needs_key` and could not be probed -- and the UI hand-rolled its own
    loopback check for the badge while still rendering the warning above it.
    """
    service = _service(runner)
    local = service.create(
        {
            "name": "local",
            "provider": "chatgpt",
            "api_key": "",
            "model": "llama3",
            "base_url": "http://127.0.0.1:11434/v1",
        }
    )
    remote = service.create(
        {
            "name": "remote",
            "provider": "chatgpt",
            "api_key": "",
            "model": "gpt-4o",
            "base_url": "https://api.example.com/v1",
        }
    )
    rows = {item["id"]: item for item in runner.store.list_model_profiles()}

    assert service.readiness(rows[local["id"]])["state"] == "ready"
    # The exception is loopback, not "no key required anywhere".
    assert service.readiness(rows[remote["id"]])["state"] == "needs_key"
