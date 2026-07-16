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
    split_ref,
)
from openai4s.security.secret_migration import (
    fingerprint,
    migrate_settings_secrets,
    resolve_setting,
)
from openai4s.store import get_store

_CANARY = "sk-broker-canary-3f9a1c-MUST-NOT-PERSIST-IN-THE-DB"


@pytest.fixture
def store(tmp_path):
    return get_store(Config(data_dir=tmp_path).db_path)


@pytest.fixture
def broker():
    return SecretBroker(mode="auto", backends=[MemoryBackend()])


class _Unavailable(MemoryBackend):
    name = "unavailable"

    def available(self) -> bool:
        return False


class _BrokenRoundTrip(MemoryBackend):
    """Accepts a write and returns nothing — a locked keychain, a denied
    prompt, a wrong collection. The failure mode a naive 'did put() raise?'
    check cannot see."""

    name = "broken"

    def get(self, scope, name):
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


# --------------------------------------------------------------------------
# backend resolution
# --------------------------------------------------------------------------


def test_a_backend_must_pass_a_real_round_trip_to_be_used(store):
    """Presence of a CLI is not availability of a keychain. A backend that
    accepts a write and returns nothing must be rejected at resolution — not
    discovered later, when the user's key silently fails to save."""
    resolved = SecretBroker(store, mode="auto", backends=[_BrokenRoundTrip()])
    assert resolved.posture()["backend"] == "plaintext-db"
    assert "unusable" in resolved.posture()["detail"]


def test_auto_degrades_visibly_rather_than_silently(store, capsys):
    SecretBroker(store, mode="auto", backends=[_Unavailable()])
    warning = capsys.readouterr().err
    assert "SECURITY WARNING" in warning
    assert "PLAINTEXT" in warning


def test_auto_posture_admits_it_is_not_secure(store):
    posture = SecretBroker(store, mode="auto", backends=[_Unavailable()]).posture()
    assert posture["secure"] is False
    assert posture["backend"] == "plaintext-db"


def test_keychain_mode_fails_closed(store):
    """Refuse to store a secret at all rather than store it in the clear."""
    with pytest.raises(SecretStoreUnavailable, match="Refusing"):
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
    assert backend.get("__selftest__", "probe") is None


# --------------------------------------------------------------------------
# migration
# --------------------------------------------------------------------------


def test_migration_replaces_plaintext_with_a_reference(store, broker):
    store.set_setting("llm_api_key", _CANARY)
    report = migrate_settings_secrets(store, broker)

    assert "llm_api_key" in report.migrated
    assert store.get_setting("llm_api_key") == "secret://v1/llm/llm_api_key"
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
        def get(self, scope, name):
            return None if scope == "llm" else super().get(scope, name)

    broker = SecretBroker(store, mode="auto", backends=[MemoryBackend()])
    broker._backend = _OnlyLlmBreaks()
    store.set_setting("llm_api_key", _CANARY)
    store.set_setting("tavily_api_key", "tvly-fine")

    report = migrate_settings_secrets(store, broker)
    assert report.migrated == ["tavily_api_key"]
    assert [f["key"] for f in report.failed] == ["llm_api_key"]


def test_empty_settings_are_skipped(store, broker):
    report = migrate_settings_secrets(store, broker)
    assert set(report.empty) == {"llm_api_key", "tavily_api_key"}
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
