"""D2: a session names the model configuration it ran under.

A frame stored a model *string*. That answers "which model name", not "which
configuration", and the two come apart in exactly the case that matters: two
profiles can name the same model against different providers or endpoints, and
editing a profile rewrote it in place. So a replayed session reported whatever
its profile says today -- not what it actually used -- and nothing recorded the
difference or could detect it afterwards.

A session now binds `profile_id + revision`, and never silently follows the
latest. What a revision is *not* keyed on is as load-bearing as what it is:
`make_ref` derives the credential reference from `(scope, profile_id)` alone, so
forking the id per revision would fork the key with it -- rotating a key would
strand earlier revisions on a secret nobody can read, and deleting any revision
would destroy the key the others still point at.
"""

from __future__ import annotations

import sqlite3

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server.model_profiles import ModelProfileService
from openai4s.store import get_store


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        pass


def _setup(tmp_path):
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    store = get_store(cfg.db_path)
    service = ModelProfileService(store, cfg, providers=lambda: {})
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    return cfg, store, service, runner


def _profile(store, service, name, provider, base_url, model, *, activate=False):
    # With a key. Every protocol in `PROFILE_PROTOCOLS` is a hosted provider that
    # needs one -- `readiness` already reports a keyless profile as `needs_key` --
    # and `bind_model_revision` now refuses to pin a session to a configuration
    # whose credential does not resolve, rather than discovering that at dispatch
    # and silently using the active profile instead. Omitting it here was a
    # fixture shortcut, not a keyless case: these tests are about revisions.
    service.create(
        {
            "name": name,
            "provider": provider,
            "base_url": base_url,
            "model": model,
            "api_key": "test-key",
        }
    )
    row = next(item for item in store.list_model_profiles() if item.get("name") == name)
    if activate:
        store.set_setting("active_model_profile", row["id"])
    return row


# --------------------------------------------------------------------------
# what a revision is, and is not
# --------------------------------------------------------------------------


def test_a_rename_or_a_key_rotation_is_not_a_new_configuration(tmp_path):
    """Only `(provider, base_url, model)` identifies a configuration.

    A rename is a label change: a replayed session must not claim it used a
    different model because someone tidied the list. A key rotation is not a
    configuration change either, and it *cannot* be one -- see the module
    docstring: the credential is keyed on the profile id, so revisions have to
    share it.
    """
    _cfg, store, service, runner = _setup(tmp_path)
    try:
        profile = _profile(store, service, "A", "claude", "https://a", "m1")
        assert profile["revision"] == 1

        service.edit(profile["id"], {"name": "A, renamed"})
        # Not a credential-shaped literal. `source_secret_scan.py` flags those
        # in source, and it is right to: "it is only a test" is the reasoning
        # that lets a real one through. Any non-placeholder string exercises
        # the rotation path, which is all this needs.
        service.edit(profile["id"], {"api_key": "rotated-key-for-this-test"})
        current = store.list_model_profiles()[0]
        assert current["revision"] == 1, "a rename or rotation minted a revision"
        assert len(current["revisions"]) == 1

        service.edit(profile["id"], {"model": "m2"})
        current = store.list_model_profiles()[0]
        assert current["revision"] == 2
        # Append-only: revision 1 still says what revision 1 was.
        assert ModelProfileService.revision_config(current, 1)["model"] == "m1"
        assert ModelProfileService.revision_config(current, 2)["model"] == "m2"
    finally:
        runner.close()


def test_an_unknown_revision_is_reported_rather_than_rounded(tmp_path):
    """Resolving to the nearest revision would be the silently-follow-latest
    behaviour this replaces, wearing a number."""
    _cfg, store, service, runner = _setup(tmp_path)
    try:
        profile = _profile(store, service, "A", "claude", "https://a", "m1")
        current = store.list_model_profiles()[0]
        assert ModelProfileService.revision_config(current, 7) is None
        assert ModelProfileService.revision_config(current, 0) is None
        del profile
    finally:
        runner.close()


# --------------------------------------------------------------------------
# the binding, and when it happens
# --------------------------------------------------------------------------


def test_a_bound_session_does_not_follow_the_profile_forward(tmp_path):
    """The whole point. Editing the profile after a session bound must not
    change what that session says it used."""
    _cfg, store, service, runner = _setup(tmp_path)
    try:
        profile = _profile(
            store, service, "A", "claude", "https://a", "m1", activate=True
        )
        frame_id = store.new_frame(kind="turn", project_id="p")

        first = runner.bind_model_revision(frame_id)
        assert first["bound"] is True and first["model_profile_revision"] == 1

        service.edit(profile["id"], {"model": "m2"})
        assert store.list_model_profiles()[0]["revision"] == 2

        again = runner.bind_model_revision(frame_id)
        assert again["model_profile_revision"] == 1, "the session followed latest"
        assert again["bound"] is False
    finally:
        runner.close()


def test_a_dangling_pin_asks_for_a_rebind_instead_of_guessing(tmp_path):
    _cfg, store, service, runner = _setup(tmp_path)
    try:
        _profile(store, service, "A", "claude", "https://a", "m1", activate=True)
        frame_id = store.new_frame(kind="turn", project_id="p")
        runner.bind_model_revision(frame_id)
        store.update_frame(frame_id, model_profile_revision=99)

        with pytest.raises(gateway_mod.GatewayError) as refused:
            runner.bind_model_revision(frame_id)
        assert refused.value.code == 409
        assert refused.value.error_code == "model_revision_unavailable"
    finally:
        runner.close()


def test_an_install_with_no_profiles_still_runs(tmp_path):
    """An install driven entirely by `.env` has no profiles at all. Refusing to
    run there would break a configuration this project documents as supported,
    so an absent profile is not an error -- it is an absent binding."""
    _cfg, store, _service, runner = _setup(tmp_path)
    try:
        frame_id = store.new_frame(kind="turn", project_id="p")
        result = runner.bind_model_revision(frame_id)
        assert result["model_profile_id"] == "" and result["bound"] is False
    finally:
        runner.close()


# --------------------------------------------------------------------------
# legacy sessions
# --------------------------------------------------------------------------


def test_a_legacy_session_backfills_only_on_a_unique_match(tmp_path):
    _cfg, store, service, runner = _setup(tmp_path)
    try:
        _profile(store, service, "Anthropic", "claude", "https://a", "shared")
        legacy = store.new_frame(kind="turn", project_id="p", model="shared")
        store.add_message(
            root_frame_id=legacy, role="user", frame_id=legacy, content="old work"
        )

        result = runner.bind_model_revision(legacy)
        assert result["backfilled"] is True and result["model_profile_revision"] == 1
    finally:
        runner.close()


def test_an_ambiguous_legacy_session_stays_unbound_and_asks(tmp_path):
    """Two profiles naming one model against different endpoints. "Which one
    did it use" has no answer in the data, so picking either would be a guess
    presented as a fact -- which is the failure D2 removes, not a case of it.
    """
    _cfg, store, service, runner = _setup(tmp_path)
    try:
        _profile(store, service, "Anthropic", "claude", "https://a", "shared")
        _profile(store, service, "Ark", "ark", "https://b", "shared")
        legacy = store.new_frame(kind="turn", project_id="p", model="shared")
        store.add_message(
            root_frame_id=legacy, role="user", frame_id=legacy, content="old work"
        )

        with pytest.raises(gateway_mod.GatewayError) as refused:
            runner.bind_model_revision(legacy)
        assert refused.value.code == 409
        assert refused.value.error_code == "model_revision_ambiguous"
        # Unbound, not half-bound: nothing was written by the refusal.
        assert store.get_frame(legacy)["model_profile_id"] is None
    finally:
        runner.close()


def test_reading_an_unbound_session_never_binds_it(tmp_path):
    """The user's call: an unbound legacy session stays fully readable --
    history, artifacts, Notebook -- and only *continuing* it asks for a
    decision. So nothing on a read path may bind, and this asserts the shape of
    that: the binding happens in `run_message`, not in state resolution."""
    import inspect

    _cfg, store, service, runner = _setup(tmp_path)
    try:
        _profile(store, service, "A", "claude", "https://a", "m1", activate=True)
        legacy = store.new_frame(kind="turn", project_id="p")

        store.get_frame(legacy)
        store.list_artifacts({"root_frame_id": legacy})
        store.message_count(legacy)
        runner.get_plan_state(legacy)
        assert store.get_frame(legacy)["model_profile_id"] is None

        source = inspect.getsource(gateway_mod.SessionRunner.run_message)
        assert (
            "bind_model_revision" in source
        ), "the send path no longer binds; a session could run unpinned"
    finally:
        runner.close()


# --------------------------------------------------------------------------
# the migration
# --------------------------------------------------------------------------


def test_migration_ten_upgrades_a_populated_database_without_defaulting(tmp_path):
    """Historical rows come back *unbound*, which is not the same as "used the
    default". The distinction is the whole reason the columns are nullable: an
    unbound session is one nobody has told us about, and inventing a binding
    for it would be exactly the fabricated provenance being removed.
    """
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    store = get_store(cfg.db_path)
    frame_id = store.new_frame(kind="turn", project_id="p", model="claude-x")
    store.close()

    # Rewind to a v9-shaped database that already holds real rows.
    connection = sqlite3.connect(cfg.db_path)
    connection.execute("ALTER TABLE frames DROP COLUMN model_profile_id")
    connection.execute("ALTER TABLE frames DROP COLUMN model_profile_revision")
    connection.execute("DELETE FROM schema_migrations WHERE version=10")
    connection.execute("PRAGMA user_version = 9")
    connection.commit()
    connection.close()

    upgraded = get_store(cfg.db_path)
    try:
        columns = {
            row[1] for row in upgraded._conn.execute("PRAGMA table_info(frames)")
        }
        assert {"model_profile_id", "model_profile_revision"} <= columns
        row = upgraded.get_frame(frame_id)
        assert row["frame_id"] == frame_id and row["model"] == "claude-x"
        assert row["model_profile_id"] is None
        assert row["model_profile_revision"] is None
        # Against the constant, not a literal: what this asserts is "the upgrade
        # reached the current version". A hardcoded 10 turns every future
        # migration into a failure of this test rather than of the migration.
        from openai4s.storage.migrations import SCHEMA_VERSION

        assert upgraded.schema_state()["version"] == SCHEMA_VERSION
    finally:
        upgraded.close()


def test_the_schema_version_matches_the_registered_migrations(tmp_path):
    """A migration added without bumping the constant never runs; a constant
    bumped without a migration makes every database report itself as behind."""
    from openai4s.storage.migrations import SCHEMA_VERSION

    cfg = Config(data_dir=tmp_path / "data")
    store = get_store(cfg.db_path)
    try:
        applied = {entry["version"] for entry in store.schema_state()["applied"]}
        assert applied == set(
            range(1, SCHEMA_VERSION + 1)
        ), f"SCHEMA_VERSION is {SCHEMA_VERSION} but the applied set is {sorted(applied)}"
    finally:
        store.close()
