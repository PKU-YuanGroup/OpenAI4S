"""SecretBroker: credentials behind an opaque reference.

Business tables held the secret itself — model-profile API keys and
llm_api_key/tavily_api_key as plaintext in `settings`. The data dir is now
owner-only, but a file mode is not encryption and does nothing for a backup, an
rsync, a container layer, or a support bundle.

Two properties carry the whole design, and both are asserted below:

  * the reference leaks nothing and is safe to log or store, and
  * migration is ordered write -> verify -> replace, so every prefix of it is
    safe to be interrupted at. The one ordering that must never happen is
    dropping the plaintext before proving the new copy is readable — that locks
    a user out of their own model configuration in the name of security.

The backends are driven through the system CLIs (no `keyring`: the core is
stdlib-only). Tests use injected backends rather than the real keychain — see
conftest, which pins OPENAI4S_SECRET_STORE=plaintext so the suite cannot write
to the developer's login keychain.
"""

import json
from types import SimpleNamespace

import pytest

from openai4s.config import Config
from openai4s.security.secret_broker import (
    MemoryBackend,
    PlaintextBackend,
    SecretBroker,
    SecretBrokerError,
    SecretStoreUnavailable,
    is_ref,
    make_ref,
    parse_ref,
    split_ref,
    store_namespace,
)
from openai4s.security.secret_migration import (
    fingerprint,
    migrate_settings_secrets,
    resolve_setting,
)
from openai4s.store import get_store

_CANARY = "canary-broker-canary-3f9a1c-MUST-NOT-PERSIST-IN-THE-DB"


@pytest.fixture
def store(tmp_path):
    return get_store(Config(data_dir=tmp_path).db_path)


@pytest.fixture
def broker(store):
    return SecretBroker(store, mode="auto", backends=[MemoryBackend()])


class _Unavailable(MemoryBackend):
    name = "unavailable"

    def available(self) -> bool:
        return False


class _BrokenRoundTrip(MemoryBackend):
    """Accepts a write and returns nothing — a locked keychain, a denied
    prompt, a wrong collection. The failure mode a naive 'did put() raise?'
    check cannot see."""

    name = "broken"

    def get(self, namespace, scope, name):
        return None


# --------------------------------------------------------------------------
# references
# --------------------------------------------------------------------------


def test_a_reference_leaks_nothing_about_the_secret():
    """It identifies *which* secret, not anything about its value — which is
    what makes it safe to store in a row and print in a log."""
    ref = make_ref("llm", "llm_api_key")
    assert _CANARY not in ref
    assert ref == "secret://v1/llm/llm_api_key"
    assert is_ref(ref)


def test_a_namespaced_reference_round_trips_without_a_path(store):
    namespace = store_namespace(store)
    ref = make_ref("llm", "llm_api_key", namespace)
    assert parse_ref(ref) == (2, namespace, "llm", "llm_api_key")
    assert str(store.db_path) not in ref


def test_store_namespace_is_realpath_stable_and_copy_path_specific(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    first = SimpleNamespace(db_path=real / "openai4s.db")
    same = SimpleNamespace(db_path=alias / "openai4s.db")
    copied = SimpleNamespace(db_path=tmp_path / "copy" / "openai4s.db")

    assert store_namespace(first) == store_namespace(same)
    assert store_namespace(first) != store_namespace(copied)


def test_the_reference_does_not_depend_on_the_value(broker):
    a = broker.put("llm", "k", "value-one")
    b = broker.put("llm", "k", "totally-different")
    assert a == b


def test_split_ref_round_trips():
    assert split_ref(make_ref("search", "tavily_api_key")) == (
        "search",
        "tavily_api_key",
    )


@pytest.mark.parametrize("bad", ["", "nope", "secret://", "secret://v1/onlyscope"])
def test_malformed_references_are_rejected(bad):
    with pytest.raises(SecretBrokerError):
        split_ref(bad)


def test_plaintext_is_not_mistaken_for_a_reference():
    """resolve_setting branches on this; if a raw key were treated as a ref the
    un-migrated install would break."""
    assert not is_ref("sk-abc123")
    assert not is_ref(None)


@pytest.mark.parametrize("bad", ["has space", "semi;colon", "sl/ash", ""])
def test_scope_and_name_are_constrained(bad):
    """They become a keychain account and ride in logs."""
    with pytest.raises(SecretBrokerError):
        make_ref(bad, "x")


def test_a_multiline_secret_is_refused(broker):
    """The keychain CLIs read one line. Storing a truncated value would 'work'
    and then fail at the provider as an unexplained auth error."""
    with pytest.raises(SecretBrokerError, match="newline"):
        broker.put("llm", "k", "line-one\nline-two")


# --------------------------------------------------------------------------
# put / get / delete
# --------------------------------------------------------------------------


def test_round_trip(broker):
    ref = broker.put("llm", "llm_api_key", _CANARY)
    assert broker.get(ref) == _CANARY


def test_delete_removes_it(broker):
    ref = broker.put("llm", "k", _CANARY)
    broker.delete(ref)
    assert broker.get(ref) is None


def test_describe_reports_configured_without_the_value(broker):
    ref = broker.put("llm", "k", _CANARY)
    described = broker.describe(ref)
    assert _CANARY not in json.dumps(described)
    assert described["configured"] is True
    assert described["scope"] == "llm"


def test_describe_reports_absence(broker):
    assert broker.describe(make_ref("llm", "never-set"))["configured"] is False


@pytest.mark.stubbed_backend
def test_shared_system_backend_isolates_same_named_secrets_by_store(tmp_path):
    """Two data dirs sharing one system backend resolve only their own key."""

    from openai4s.store import Store

    backend = MemoryBackend()
    first = Store(tmp_path / "namespace-a" / "openai4s.db")
    second = Store(tmp_path / "namespace-b" / "openai4s.db")
    first._secret_broker = SecretBroker(first, mode="auto", backends=[backend])
    second._secret_broker = SecretBroker(second, mode="auto", backends=[backend])
    try:
        first.set_secret_setting("agent_plan_key", "plan-key-a", scope="agent_plan")
        second.set_secret_setting("agent_plan_key", "plan-key-b", scope="agent_plan")
        first_ref = first.get_setting("agent_plan_key")
        second_ref = second.get_setting("agent_plan_key")

        assert first_ref != second_ref
        assert first.get_secret_setting("agent_plan_key") == "plan-key-a"
        assert second.get_secret_setting("agent_plan_key") == "plan-key-b"
        # JIT reads remain isolated even after the other Store overwrites the
        # same logical setting name.
        assert first.get_secret_setting("agent_plan_key") == "plan-key-a"
    finally:
        first.close()
        second.close()


@pytest.mark.stubbed_backend
def test_foreign_v2_and_ownerless_v1_refs_never_touch_system_backend(tmp_path):
    """Copied DB refs and legacy global slots both fail before lookup/delete."""

    from openai4s.store import Store

    class SpyBackend(MemoryBackend):
        def __init__(self):
            super().__init__()
            self.calls = []

        def get(self, namespace, scope, name):
            self.calls.append(("get", namespace, scope, name))
            return super().get(namespace, scope, name)

        def delete(self, namespace, scope, name):
            self.calls.append(("delete", namespace, scope, name))
            return super().delete(namespace, scope, name)

    backend = SpyBackend()
    first = Store(tmp_path / "owner-a" / "openai4s.db")
    copied = Store(tmp_path / "copied-b" / "openai4s.db")
    first_broker = SecretBroker(first, mode="auto", backends=[backend])
    copied_broker = SecretBroker(copied, mode="auto", backends=[backend])
    try:
        first_ref = first_broker.put("llm", "llm_api_key", "owner-a-key")
        legacy_ref = make_ref("llm", "llm_api_key")
        backend.put("", "llm", "llm_api_key", "ambiguous-legacy-key")
        backend.calls.clear()

        assert copied_broker.get(first_ref) is None
        copied_broker.delete(first_ref)
        assert copied_broker.get(legacy_ref) is None
        copied_broker.delete(legacy_ref)
        assert copied_broker.describe(legacy_ref)["reentry_required"] is True
        assert backend.calls == []

        # Re-entry writes only the copied Store's v2 slot and leaves the
        # ownerless v1 slot untouched for an installation that may still use it.
        copied_ref = copied_broker.put("llm", "llm_api_key", "owner-b-key")
        assert copied_ref != first_ref
        assert backend.get("", "llm", "llm_api_key") == "ambiguous-legacy-key"
        assert first_broker.get(first_ref) == "owner-a-key"
    finally:
        first.close()
        copied.close()


@pytest.mark.stubbed_backend
def test_nested_profile_and_connector_foreign_refs_fail_closed(tmp_path):
    """Profile and connector consumers cannot bypass Broker namespace checks."""

    from openai4s.config import Config
    from openai4s.server.model_profiles import ModelProfileService
    from openai4s.store import Store

    backend = MemoryBackend()
    first = Store(tmp_path / "nested-a" / "openai4s.db")
    copied = Store(tmp_path / "nested-b" / "openai4s.db")
    first._secret_broker = SecretBroker(first, mode="auto", backends=[backend])
    copied._secret_broker = SecretBroker(copied, mode="auto", backends=[backend])
    first_service = ModelProfileService(
        first, Config(data_dir=tmp_path / "nested-a"), providers=lambda: {}
    )
    copied_service = ModelProfileService(
        copied, Config(data_dir=tmp_path / "nested-b"), providers=lambda: {}
    )
    try:
        profile = first_service.create(
            {"name": "foreign", "provider": "ark", "api_key": "profile-a-key"}
        )
        raw_profile = next(
            item for item in first.list_model_profiles() if item["id"] == profile["id"]
        )
        copied.set_model_profiles([dict(raw_profile)])
        assert copied_service.resolve_key(copied.list_model_profiles()[0]) == ""

        connector_ref = first.secrets.put(
            "connector_env", "lab.LAB_TOKEN", "connector-a-key"
        )
        copied._connectors.upsert(
            connector_id="lab",
            name="Lab",
            command=["x"],
            env={"LAB_TOKEN": connector_ref},
            enabled=True,
        )
        assert copied.connector_env(copied.get_connector("lab")) == {"LAB_TOKEN": ""}
        copied.delete_connector("lab")
        assert first.secrets.get(connector_ref) == "connector-a-key"
    finally:
        first.close()
        copied.close()


# --------------------------------------------------------------------------
# backend resolution
# --------------------------------------------------------------------------


def test_a_backend_that_cannot_round_trip_is_not_used(store):
    """Presence of a CLI is not availability of a keychain. A backend that
    accepts a write and returns nothing must be rejected at resolution — not
    discovered later, when the user's key silently fails to save. With nothing
    else available that now means failing closed."""
    with pytest.raises(SecretStoreUnavailable, match="unusable"):
        SecretBroker(store, mode="auto", backends=[_BrokenRoundTrip()])


def test_auto_fails_closed_rather_than_degrading_to_plaintext(store):
    """The inversion this fixes: `auto` used to fall through to plaintext with
    a warning, so the deployment least able to protect a secret — a Linux
    server with neither a keychain nor a session bus — was exactly the one that
    silently got no protection. A boot warning is not a control; it scrolls
    away and the credential stays in the clear."""
    with pytest.raises(SecretStoreUnavailable) as e:
        SecretBroker(store, mode="auto", backends=[_Unavailable()])
    message = str(e.value)
    # The error has to say how to fix it on each kind of host, or it just
    # relocates the problem to a support thread.
    assert "OPENAI4S_SECRET_" in message
    assert "plaintext" in message


def test_failing_closed_does_not_leak_a_secret_into_the_message(store):
    with pytest.raises(SecretStoreUnavailable) as e:
        SecretBroker(store, mode="auto", backends=[_Unavailable()])
    assert _CANARY not in str(e.value)


def test_plaintext_remains_reachable_but_only_by_name(store):
    """An operator who accepts the risk can still say so; nobody inherits it."""
    posture = SecretBroker(store, mode="plaintext").posture()
    assert posture["backend"] == "plaintext-db"
    assert posture["secure"] is False


def test_keychain_mode_fails_closed(store):
    """Refuse to store a secret at all rather than store it in the clear."""
    with pytest.raises(SecretStoreUnavailable, match="refusing"):
        SecretBroker(store, mode="keychain", backends=[_Unavailable()])


def test_keychain_mode_accepts_a_working_backend(store):
    posture = SecretBroker(store, mode="keychain", backends=[MemoryBackend()]).posture()
    assert posture["secure"] is True


def test_plaintext_mode_is_explicit_and_says_so(store):
    posture = SecretBroker(store, mode="plaintext").posture()
    assert posture["backend"] == "plaintext-db"
    assert posture["secure"] is False
    assert "explicitly selected" in posture["detail"]


def test_a_working_backend_is_preferred_over_plaintext(store):
    posture = SecretBroker(
        store, mode="auto", backends=[_Unavailable(), MemoryBackend()]
    ).posture()
    assert posture["backend"] == "memory"


def test_mode_rejects_garbage(store):
    with pytest.raises(SecretBrokerError):
        SecretBroker(store, mode="sorta-secure")


def test_the_self_test_cleans_up_after_itself():
    backend = MemoryBackend()
    SecretBroker(mode="auto", backends=[backend])
    assert backend._values == {}


@pytest.mark.stubbed_backend
def test_macos_keychain_account_contains_store_namespace(monkeypatch):
    import openai4s.security.secret_broker as module

    namespace = "a" * 32
    calls = []

    def fake_run(argv, stdin=None):
        calls.append((argv, stdin))
        return SimpleNamespace(returncode=0, stdout=b"saved-value\n", stderr=b"")

    monkeypatch.setattr(module, "_run", fake_run)
    backend = module.KeychainBackend()
    backend.put(namespace, "llm", "llm_api_key", "saved-value")
    assert backend.get(namespace, "llm", "llm_api_key") == "saved-value"
    backend.delete(namespace, "llm", "llm_api_key")

    expected_account = f"v2/{namespace}/llm/llm_api_key"
    for argv, _stdin in calls:
        assert argv[argv.index("-a") + 1] == expected_account
        assert "saved-value" not in argv


@pytest.mark.stubbed_backend
def test_secret_service_attributes_contain_store_namespace(monkeypatch):
    import openai4s.security.secret_broker as module

    namespace = "b" * 32
    calls = []

    def fake_run(argv, stdin=None):
        calls.append((argv, stdin))
        return SimpleNamespace(returncode=0, stdout=b"saved-value", stderr=b"")

    monkeypatch.setattr(module, "_run", fake_run)
    backend = module.SecretServiceBackend()
    backend.put(namespace, "agent_plan", "agent_plan_key", "saved-value")
    assert backend.get(namespace, "agent_plan", "agent_plan_key") == "saved-value"
    backend.delete(namespace, "agent_plan", "agent_plan_key")

    for argv, _stdin in calls:
        attributes = argv[argv.index("service") :]
        assert ["version", "v2"] == attributes[2:4]
        ns_index = attributes.index("namespace")
        assert attributes[ns_index + 1] == namespace
        assert "saved-value" not in argv


# --------------------------------------------------------------------------
# migration
# --------------------------------------------------------------------------


def test_migration_replaces_plaintext_with_a_reference(store, broker):
    store.set_setting("llm_api_key", _CANARY)
    report = migrate_settings_secrets(store, broker)

    assert "llm_api_key" in report.migrated
    assert store.get_setting("llm_api_key").startswith("secret://v2/")
    assert _CANARY not in str(store.get_setting("llm_api_key"))


def test_the_value_survives_migration(store, broker):
    store.set_setting("llm_api_key", _CANARY)
    migrate_settings_secrets(store, broker)
    assert resolve_setting(store, broker, "llm_api_key") == _CANARY


def test_migration_is_idempotent(store, broker):
    store.set_setting("llm_api_key", _CANARY)
    migrate_settings_secrets(store, broker)
    second = migrate_settings_secrets(store, broker)
    assert second.migrated == []
    assert "llm_api_key" in second.already
    assert resolve_setting(store, broker, "llm_api_key") == _CANARY


@pytest.mark.stubbed_backend
def test_v1_system_reference_reports_reentry_without_backend_lookup(store):
    class SpyBackend(MemoryBackend):
        def __init__(self):
            super().__init__()
            self.gets = []

        def get(self, namespace, scope, name):
            self.gets.append((namespace, scope, name))
            return super().get(namespace, scope, name)

    backend = SpyBackend()
    broker = SecretBroker(store, mode="auto", backends=[backend])
    backend.gets.clear()
    store.set_setting("llm_api_key", make_ref("llm", "llm_api_key"))

    report = migrate_settings_secrets(store, broker)

    assert report.reentry_required == ["llm_api_key"]
    assert backend.gets == []
    assert resolve_setting(store, broker, "llm_api_key") == ""


@pytest.mark.stubbed_backend
def test_revoked_v2_setting_is_reentry_required_not_already(store):
    backend = MemoryBackend()
    broker = SecretBroker(store, mode="auto", backends=[backend])
    ref = broker.put("llm", "llm_api_key", _CANARY)
    store.set_setting("llm_api_key", ref)
    broker.delete(ref)

    report = migrate_settings_secrets(store, broker)

    assert report.already == []
    assert report.reentry_required == ["llm_api_key"]


def test_an_unverifiable_write_leaves_the_plaintext_alone(store):
    """The ordering that matters. If the new copy cannot be read back, keeping
    the plaintext is strictly better than a reference that resolves to nothing
    — the latter locks the user out of their own configuration.
    """
    broken = SecretBroker(store, mode="auto", backends=[MemoryBackend()])
    broken._backend = _BrokenRoundTrip()
    store.set_setting("llm_api_key", _CANARY)

    report = migrate_settings_secrets(store, broken)
    assert report.migrated == []
    assert [f["key"] for f in report.failed] == ["llm_api_key"]
    assert store.get_setting("llm_api_key") == _CANARY, "plaintext must survive"


def test_one_bad_key_does_not_strand_the_others(store):
    class _OnlyLlmBreaks(MemoryBackend):
        def get(self, namespace, scope, name):
            return None if scope == "llm" else super().get(namespace, scope, name)

    broker = SecretBroker(store, mode="auto", backends=[MemoryBackend()])
    broker._backend = _OnlyLlmBreaks()
    store.set_setting("llm_api_key", _CANARY)
    store.set_setting("tavily_api_key", "tvly-fine")

    report = migrate_settings_secrets(store, broker)
    assert report.migrated == ["tavily_api_key"]
    assert [f["key"] for f in report.failed] == ["llm_api_key"]


def test_empty_settings_are_skipped(store, broker):
    report = migrate_settings_secrets(store, broker)
    assert set(report.empty) == {
        "llm_api_key",
        "tavily_api_key",
        "agent_plan_key",
    }
    assert report.migrated == []


def test_a_fingerprint_is_not_the_secret():
    """Migration logs correlate by fingerprint, never by value."""
    fp = fingerprint(_CANARY)
    assert _CANARY not in fp
    assert len(fp) == 12
    assert fingerprint(_CANARY) == fp
    assert fingerprint("other") != fp


# --------------------------------------------------------------------------
# the Store facade
# --------------------------------------------------------------------------


def test_get_secret_setting_reads_legacy_plaintext(store):
    """An install that has not migrated yet must keep working."""
    store.set_setting("llm_api_key", _CANARY)
    assert store.get_secret_setting("llm_api_key") == _CANARY


def test_v1_reference_still_resolves_db_local_plaintext_slot(store):
    broker = SecretBroker(store, mode="plaintext")
    store.set_setting("secret::llm::llm_api_key", _CANARY)
    ref = make_ref("llm", "llm_api_key")

    assert broker.get(ref) == _CANARY


def test_get_secret_setting_resolves_a_reference(store):
    store.set_secret_setting("llm_api_key", _CANARY, scope="llm")
    assert is_ref(store.get_setting("llm_api_key"))
    assert store.get_secret_setting("llm_api_key") == _CANARY


def test_set_secret_setting_records_only_a_reference(store):
    store.set_secret_setting("llm_api_key", _CANARY, scope="llm")
    assert _CANARY not in str(store.get_setting("llm_api_key"))


def test_clearing_a_secret_removes_it_from_the_store_too(store):
    """A key the UI reports as gone must not linger in the backing store."""
    store.set_secret_setting("llm_api_key", _CANARY, scope="llm")
    ref = store.get_setting("llm_api_key")
    store.set_secret_setting("llm_api_key", "", scope="llm")
    assert store.get_setting("llm_api_key") == ""
    assert store.secrets.get(ref) is None


def test_set_secret_setting_refuses_to_record_an_unverifiable_write(store):
    store._secret_broker = SecretBroker(store, mode="auto", backends=[MemoryBackend()])
    store._secret_broker._backend = _BrokenRoundTrip()
    with pytest.raises(RuntimeError, match="could not read it back"):
        store.set_secret_setting("llm_api_key", _CANARY, scope="llm")
    assert store.get_setting("llm_api_key") in (None, "")


def test_missing_secret_reads_as_empty_not_as_the_reference(store):
    """If the keychain entry is gone (revoked by hand, different machine), the
    caller must get "" and re-prompt — never the ref as if it were a key."""
    store.set_secret_setting("llm_api_key", _CANARY, scope="llm")
    store.secrets.delete(store.get_setting("llm_api_key"))
    assert store.get_secret_setting("llm_api_key") == ""


def test_a_revoked_secret_is_not_reported_as_configured(store, tmp_path):
    """The trap a reference sets for any `if stored_key:` check: a ref is
    truthy whether or not the value behind it still exists. Onboarding's
    has_api_key must track the value, not the row.
    """
    from types import SimpleNamespace

    from openai4s.llm import PROVIDERS
    from openai4s.onboarding import OnboardingService

    cfg = Config(
        data_dir=tmp_path,
        llm=SimpleNamespace(
            provider="claude", base_url="https://x/v1", model="m", api_key=""
        ),
    )
    service = OnboardingService(cfg, store, PROVIDERS)

    store.set_secret_setting("llm_api_key", _CANARY, scope="llm")
    assert service.status().has_api_key is True

    # The row still holds a live-looking reference; the value is gone.
    store.secrets.delete(store.get_setting("llm_api_key"))
    assert store.get_setting("llm_api_key").startswith("secret://")
    assert service.status().has_api_key is False


# --------------------------------------------------------------------------
# model profiles: each carries its own key inside the blob
# --------------------------------------------------------------------------


def _profiles(store, tmp_path):
    from openai4s.llm import PROVIDERS
    from openai4s.server.model_profiles import ModelProfileService

    return ModelProfileService(
        store, Config(data_dir=tmp_path), providers=lambda: PROVIDERS
    )


def test_a_new_profile_stores_only_a_reference(store, tmp_path):
    service = _profiles(store, tmp_path)
    service.create({"name": "prod", "provider": "claude", "api_key": _CANARY})
    saved = store.list_model_profiles()[0]
    assert is_ref(saved["api_key"])
    assert _CANARY not in json.dumps(store.list_model_profiles())


def test_the_profile_key_is_still_usable(store, tmp_path):
    service = _profiles(store, tmp_path)
    service.create({"name": "prod", "provider": "claude", "api_key": _CANARY})
    assert service.resolve_key(store.list_model_profiles()[0]) == _CANARY


def test_activating_a_profile_copies_the_key_not_the_reference(store, tmp_path):
    """The trap: activate mirrors the profile's key into llm_api_key. Copying
    the reference instead would send it to the provider as an API key."""
    service = _profiles(store, tmp_path)
    created = service.create({"name": "prod", "provider": "claude", "api_key": _CANARY})
    service.activate(created["id"])
    assert store.get_secret_setting("llm_api_key") == _CANARY


def test_legacy_plaintext_profile_keys_migrate(store, tmp_path):
    service = _profiles(store, tmp_path)
    store.mutate_model_profiles(
        lambda profiles: profiles.append(
            {"id": "mp-old", "name": "legacy", "provider": "claude", "api_key": _CANARY}
        )
    )
    report = service.migrate_profile_keys()
    assert report["migrated"] == ["mp-old"]
    saved = store.list_model_profiles()[0]
    assert is_ref(saved["api_key"])
    assert service.resolve_key(saved) == _CANARY
    assert _CANARY not in json.dumps(store.list_model_profiles())


def test_profile_migration_is_idempotent(store, tmp_path):
    service = _profiles(store, tmp_path)
    store.mutate_model_profiles(
        lambda profiles: profiles.append(
            {"id": "mp-old", "name": "legacy", "api_key": _CANARY}
        )
    )
    service.migrate_profile_keys()
    assert service.migrate_profile_keys()["migrated"] == []


@pytest.mark.stubbed_backend
def test_legacy_profile_ref_is_reported_for_reentry_without_lookup(store, tmp_path):
    backend = MemoryBackend()
    store._secret_broker = SecretBroker(store, mode="auto", backends=[backend])
    store.mutate_model_profiles(
        lambda profiles: profiles.append(
            {
                "id": "mp-v1",
                "name": "legacy",
                "provider": "ark",
                "api_key": make_ref("model_profile", "mp-v1"),
            }
        )
    )

    report = _profiles(store, tmp_path).migrate_profile_keys()

    assert report["migrated"] == []
    assert report["reentry_required"] == ["mp-v1"]


@pytest.mark.stubbed_backend
def test_revoked_v2_profile_ref_is_reported_for_reentry(store, tmp_path):
    backend = MemoryBackend()
    store._secret_broker = SecretBroker(store, mode="auto", backends=[backend])
    service = _profiles(store, tmp_path)
    created = service.create({"name": "revoked", "provider": "ark", "api_key": _CANARY})
    raw = next(
        profile["api_key"]
        for profile in store.list_model_profiles()
        if profile["id"] == created["id"]
    )
    store.secrets.delete(raw)

    report = service.migrate_profile_keys()

    assert report["reentry_required"] == [created["id"]]


def test_a_legacy_profile_key_keeps_working_before_migration(store, tmp_path):
    """An install that has not migrated must not lose its endpoints."""
    service = _profiles(store, tmp_path)
    assert service.resolve_key({"id": "mp-x", "api_key": _CANARY}) == _CANARY


def test_deleting_a_profile_deletes_its_credential(store, tmp_path):
    """Otherwise the key outlives the row that referred to it, with nothing
    left in the app that knows it exists."""
    service = _profiles(store, tmp_path)
    created = service.create({"name": "prod", "provider": "claude", "api_key": _CANARY})
    ref = store.list_model_profiles()[0]["api_key"]
    service.delete(created["id"])
    assert store.secrets.get(ref) is None


def test_clearing_a_profile_key_removes_it_from_the_store(store, tmp_path):
    service = _profiles(store, tmp_path)
    created = service.create({"name": "prod", "provider": "claude", "api_key": _CANARY})
    ref = store.list_model_profiles()[0]["api_key"]
    service.edit(created["id"], {"clear_api_key": True})
    assert store.secrets.get(ref) is None
    assert store.list_model_profiles()[0]["api_key"] == ""


def test_replacing_a_profile_key_does_not_strand_the_old_one(store, tmp_path):
    service = _profiles(store, tmp_path)
    created = service.create({"name": "prod", "provider": "claude", "api_key": _CANARY})
    service.edit(created["id"], {"api_key": "sk-rotated"})
    assert service.resolve_key(store.list_model_profiles()[0]) == "sk-rotated"


# --------------------------------------------------------------------------
# connector env
# --------------------------------------------------------------------------


def _add_connector(store, env):
    return store.upsert_connector(
        connector_id="lab",
        name="Lab MCP",
        command=["python", "s.py"],
        env=env,
        enabled=True,
    )


def test_connector_env_values_are_brokered(store):
    _add_connector(store, {"LAB_TOKEN": _CANARY, "MODE": "test"})
    stored = store.get_connector("lab")["env"]
    assert stored["LAB_TOKEN"].startswith("secret://")
    assert _CANARY not in json.dumps(store.get_connector("lab"))


def test_every_env_value_is_brokered_not_just_the_credential_shaped_ones(store):
    """Deciding by variable name would be the same name-based heuristic the
    compute provider's README warns about — a secret under an unrecognised name
    is simply missed. A benign MODE=test in the keychain costs nothing."""
    _add_connector(store, {"LAB_TOKEN": _CANARY, "MODE": "test"})
    stored = store.get_connector("lab")["env"]
    assert stored["MODE"].startswith("secret://")


def test_the_launcher_gets_real_values(store):
    _add_connector(store, {"LAB_TOKEN": _CANARY, "MODE": "test"})
    assert store.connector_env(store.get_connector("lab")) == {
        "LAB_TOKEN": _CANARY,
        "MODE": "test",
    }


def test_legacy_plaintext_connector_env_still_launches(store):
    """An install that has not migrated must keep launching its servers."""
    assert store.connector_env({"env": {"LAB_TOKEN": _CANARY}}) == {
        "LAB_TOKEN": _CANARY
    }


def test_connector_env_migration(store):
    from openai4s.security.secret_migration import migrate_connector_env

    # A legacy row: plaintext written straight past the broker.
    store._connectors.upsert(
        connector_id="old",
        name="Old",
        command=["x"],
        env={"OLD_TOKEN": _CANARY},
        enabled=True,
    )
    assert store.get_connector("old")["env"] == {"OLD_TOKEN": _CANARY}

    report = migrate_connector_env(store)
    assert report["migrated"] == ["old"]
    assert store.get_connector("old")["env"]["OLD_TOKEN"].startswith("secret://")
    assert store.connector_env(store.get_connector("old")) == {"OLD_TOKEN": _CANARY}


def test_connector_env_migration_is_idempotent(store):
    from openai4s.security.secret_migration import migrate_connector_env

    _add_connector(store, {"LAB_TOKEN": _CANARY})
    assert migrate_connector_env(store)["migrated"] == []


@pytest.mark.stubbed_backend
def test_legacy_connector_ref_is_reported_for_reentry(store):
    from openai4s.security.secret_migration import migrate_connector_env

    backend = MemoryBackend()
    store._secret_broker = SecretBroker(store, mode="auto", backends=[backend])
    store._connectors.upsert(
        connector_id="v1-connector",
        name="V1",
        command=["x"],
        env={"TOKEN": make_ref("connector_env", "v1-connector.TOKEN")},
        enabled=True,
    )

    report = migrate_connector_env(store)

    assert report["migrated"] == []
    assert report["reentry_required"] == ["v1-connector"]


@pytest.mark.stubbed_backend
def test_revoked_v2_connector_ref_is_reported_for_reentry(store):
    from openai4s.security.secret_migration import migrate_connector_env

    backend = MemoryBackend()
    store._secret_broker = SecretBroker(store, mode="auto", backends=[backend])
    _add_connector(store, {"TOKEN": _CANARY})
    ref = store.get_connector("lab")["env"]["TOKEN"]
    store.secrets.delete(ref)

    report = migrate_connector_env(store)

    assert report["reentry_required"] == ["lab"]


def test_deleting_a_connector_deletes_its_env_secrets(store):
    _add_connector(store, {"LAB_TOKEN": _CANARY})
    ref = store.get_connector("lab")["env"]["LAB_TOKEN"]
    store.delete_connector("lab")
    assert store.secrets.get(ref) is None


def test_an_unresolvable_env_reference_is_not_passed_through(store):
    """The server must not receive the literal "secret://..." string as its
    credential — that fails as a broken server rather than a missing key."""
    _add_connector(store, {"LAB_TOKEN": _CANARY})
    connector = store.get_connector("lab")
    store.secrets.delete(connector["env"]["LAB_TOKEN"])
    assert store.connector_env(connector) == {"LAB_TOKEN": ""}


def test_empty_env_is_untouched(store):
    _add_connector(store, None)
    assert store.get_connector("lab")["env"] == {}
    assert store.connector_env(store.get_connector("lab")) == {}


# --------------------------------------------------------------------------
# the plaintext backend is honest about what it is
# --------------------------------------------------------------------------


def test_plaintext_backend_never_claims_to_be_secure(store):
    assert PlaintextBackend(store).secure is False


def test_no_obfuscation_backend_exists():
    """Base64 or a hand-rolled cipher over a key stored beside the ciphertext
    is not a boundary — it is a plaintext store described in words that suggest
    otherwise. If one is ever added, this should be the test that argues."""
    import openai4s.security.secret_broker as module

    names = [n.lower() for n in dir(module)]
    for banned in ("base64", "obfuscat", "xor", "encrypt"):
        assert not any(banned in n for n in names), banned


# --------------------------------------------------------------------------
# environment injection — how a server holds credentials
# --------------------------------------------------------------------------


def test_env_injection_reads_the_operators_variable(store, monkeypatch):
    from openai4s.security.secret_broker import EnvInjectionBackend

    monkeypatch.setenv("OPENAI4S_SECRET_LLM_LLM_API_KEY", _CANARY)
    broker = SecretBroker(store, mode="env", backends=[EnvInjectionBackend()])
    assert broker.get(make_ref("llm", "llm_api_key")) == _CANARY


def test_env_injection_prefers_namespaced_value_then_legacy_global(store, monkeypatch):
    from openai4s.security.secret_broker import EnvInjectionBackend

    backend = EnvInjectionBackend()
    namespace = store_namespace(store)
    namespaced_var = backend.namespaced_var_name(
        namespace, "agent_plan", "agent_plan_key"
    )
    legacy_var = backend.var_name("agent_plan", "agent_plan_key")
    monkeypatch.setenv(legacy_var, "operator-global-key")
    monkeypatch.setenv(namespaced_var, "store-specific-key")
    broker = SecretBroker(store, mode="env", backends=[backend])
    ref = make_ref("agent_plan", "agent_plan_key", namespace)

    assert broker.get(ref) == "store-specific-key"
    monkeypatch.delenv(namespaced_var)
    assert broker.get(ref) == "operator-global-key"


def test_env_injection_writes_nothing_to_disk(store, monkeypatch):
    """Stronger than the keychain case, not a fallback from it: a snapshot of
    the data directory carries no credential at all."""
    from openai4s.security.secret_broker import EnvInjectionBackend

    monkeypatch.setenv("OPENAI4S_SECRET_LLM_LLM_API_KEY", _CANARY)
    SecretBroker(store, mode="env", backends=[EnvInjectionBackend()])
    assert _CANARY not in json.dumps(
        [dict(r) for r in store._conn.execute("SELECT * FROM settings")]
    )


def test_env_injection_refuses_to_be_overwritten_by_the_app(store, monkeypatch):
    """If the environment owns the secret, the UI must not be able to change it
    behind the operator's back — and the error has to name the variable to set,
    or the operator is left guessing."""
    from openai4s.security.secret_broker import EnvInjectionBackend

    monkeypatch.setenv("OPENAI4S_SECRET_ENV", "1")
    broker = SecretBroker(store, mode="env", backends=[EnvInjectionBackend()])
    with pytest.raises(SecretBrokerError) as e:
        broker.put("llm", "llm_api_key", _CANARY)
    assert "OPENAI4S_SECRET_V2_" in str(e.value)
    assert str(store.db_path) not in str(e.value)


def test_a_fresh_server_can_opt_in_before_any_credential_exists(store, monkeypatch):
    """Without the enable flag a box with no variables set yet would look like
    "no backend" and fail closed before the operator could supply one."""
    from openai4s.security.secret_broker import EnvInjectionBackend

    monkeypatch.setenv("OPENAI4S_SECRET_ENV", "1")
    assert EnvInjectionBackend().available() is True


def test_variable_names_are_predictable(monkeypatch):
    from openai4s.security.secret_broker import EnvInjectionBackend

    assert (
        EnvInjectionBackend.var_name("llm", "llm_api_key")
        == "OPENAI4S_SECRET_LLM_LLM_API_KEY"
    )
    assert (
        EnvInjectionBackend.var_name("connector_env", "lab.LAB_TOKEN")
        == "OPENAI4S_SECRET_CONNECTOR_ENV_LAB_LAB_TOKEN"
    )


def test_separator_runs_cannot_name_two_different_variables():
    """ "a..b" and "a__b" must not resolve to different secrets."""
    from openai4s.security.secret_broker import EnvInjectionBackend

    assert EnvInjectionBackend.var_name("s", "a..b") == EnvInjectionBackend.var_name(
        "s", "a__b"
    )


def test_auto_prefers_a_keychain_over_the_environment(store, monkeypatch):
    """Both are secure; the keychain is durable and interactive, so it wins
    when present."""
    from openai4s.security.secret_broker import EnvInjectionBackend

    monkeypatch.setenv("OPENAI4S_SECRET_ENV", "1")
    posture = SecretBroker(
        store, mode="auto", backends=[MemoryBackend(), EnvInjectionBackend()]
    ).posture()
    assert posture["backend"] == "memory"


@pytest.mark.stubbed_backend
def test_one_undescribable_profile_ref_does_not_strand_the_others(store, tmp_path):
    """`describe` reaches the backend, so it must not run outside the guard.

    A locked keychain times out and a hand-edited ref fails to parse. Either
    one, raised from outside the per-profile `try`, aborted the whole
    conversion and left every later profile's key sitting in plaintext.
    """

    service = _profiles(store, tmp_path)
    store.mutate_model_profiles(
        lambda profiles: profiles.extend(
            [
                {"id": "mp-bad", "name": "bad ref", "api_key": "secret://v1/llm"},
                {"id": "mp-plain", "name": "plaintext", "api_key": _CANARY},
            ]
        )
    )

    report = service.migrate_profile_keys()

    assert report["migrated"] == ["mp-plain"], report
    assert [entry["id"] for entry in report["failed"]] == ["mp-bad"]
    saved = {p["id"]: p for p in store.list_model_profiles()}
    assert is_ref(saved["mp-plain"]["api_key"])
    assert _CANARY not in json.dumps(store.list_model_profiles())
