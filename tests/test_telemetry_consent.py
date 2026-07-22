"""Consent is the thing telemetry is not allowed to work without.

Two properties carry most of the weight here, and both have a reasonable-looking
alternative that fails quietly:

  * **revoke destroys the identity too.** If consent and install id were
    separate rows, "revoke" could plausibly clear one and leave the other, and
    an identifier that outlives the consent it was minted under is not
    anonymous -- it is pseudonymous with a longer memory than the user agreed
    to. Nothing would look broken.
  * **an environment variable cannot grant.** `OPENAI4S_*` is how CI,
    containers and scripts configure this program. Honouring one here would let
    a machine start reporting because of a line in a Dockerfile that nobody
    read as a privacy decision. Turning telemetry *off* needs no permission, so
    that direction is allowed.
"""
from __future__ import annotations

import json

import pytest

from openai4s.config import Config
from openai4s.store import get_store
from openai4s.telemetry import consent as consent_mod
from openai4s.telemetry.consent import (
    CONSENT_KEY,
    ENV_VAR,
    enabled,
    grant,
    read,
    revoke,
)


@pytest.fixture
def store(tmp_path):
    return get_store(Config(data_dir=tmp_path).db_path)


# --------------------------------------------------------------------------
# the default
# --------------------------------------------------------------------------


def test_a_fresh_install_has_not_consented(store):
    assert read(store) is None
    assert enabled(store) is False


def test_granting_records_consent_and_mints_an_identity(store):
    consent = grant(store)

    assert enabled(store) is True
    assert len(consent.install_id) == 32
    assert consent.granted_at > 0


def test_granting_twice_does_not_mint_a_second_identity(store):
    """One continuous participation, one id. A second grant that re-minted
    would make a restart look like a new install."""
    first = grant(store)
    second = grant(store)
    assert first.install_id == second.install_id


def test_the_identity_is_not_derived_from_anything_about_the_machine(store, tmp_path):
    """Two installs must not collide or correlate. A hostname- or path-derived
    id would do both, and would survive a revoke-and-regrant."""
    other = get_store(Config(data_dir=tmp_path / "other").db_path)
    assert grant(store).install_id != grant(other).install_id


# --------------------------------------------------------------------------
# revocation
# --------------------------------------------------------------------------


def test_revoking_destroys_the_identity_along_with_the_permission(store):
    """The load-bearing one. There is no way to revoke and keep the id."""
    grant(store)
    revoke(store)

    assert read(store) is None
    assert store.get_setting(CONSENT_KEY) is None


def test_re_consenting_after_a_revoke_is_a_new_identity(store):
    """Otherwise the two periods are linkable, and "anonymous" quietly means
    "the same person, before and after they changed their mind"."""
    before = grant(store).install_id
    revoke(store)
    after = grant(store).install_id

    assert before != after


def test_revoking_leaves_no_tombstone(store):
    """A record that a grant once existed is itself a fact about the user that
    outlives their withdrawal of it."""
    grant(store)
    revoke(store)

    remaining = [
        key
        for key in ("telemetry_consent", "telemetry_install_id", "telemetry_revoked")
        if store.get_setting(key) is not None
    ]
    assert remaining == []


def test_revoking_when_nothing_was_granted_is_not_an_error(store):
    revoke(store)
    assert read(store) is None


# --------------------------------------------------------------------------
# the environment can refuse, and cannot agree
# --------------------------------------------------------------------------


def test_an_environment_variable_cannot_turn_telemetry_on(store, monkeypatch):
    """A line in a Dockerfile is not consent."""
    for value in ("1", "on", "true", "yes", "enabled"):
        monkeypatch.setenv(ENV_VAR, value)
        assert enabled(store) is False, f"{ENV_VAR}={value} must not grant"


def test_an_environment_variable_can_turn_telemetry_off(store, monkeypatch):
    """Refusing needs no permission, so this direction is honoured."""
    grant(store)
    assert enabled(store) is True

    for value in ("0", "off", "false", "no", "disabled", "OFF"):
        monkeypatch.setenv(ENV_VAR, value)
        assert enabled(store) is False, f"{ENV_VAR}={value} must veto"


def test_the_veto_does_not_delete_the_recorded_consent(store, monkeypatch):
    """A container-level opt-out is not the user changing their mind, so
    unsetting the variable restores what the person actually chose."""
    grant(store)
    monkeypatch.setenv(ENV_VAR, "0")
    assert enabled(store) is False

    monkeypatch.delenv(ENV_VAR)
    assert enabled(store) is True


def test_granting_under_a_veto_does_not_record_consent(store, monkeypatch):
    """Otherwise a scripted grant lands silently and takes effect the moment
    the variable goes away."""
    monkeypatch.setenv(ENV_VAR, "0")
    assert grant(store) is None, "a vetoed grant must report that it did nothing"

    monkeypatch.delenv(ENV_VAR)
    assert enabled(store) is False


# --------------------------------------------------------------------------
# a record that cannot be trusted is not consent
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not json",
        "[]",
        '"a string"',
        json.dumps({"granted_at": 1}),
        json.dumps({"install_id": "short", "granted_at": 1}),
        json.dumps({"install_id": "Z" * 32, "granted_at": 1}),
        json.dumps({"install_id": "a" * 32}),
        json.dumps({"install_id": "a" * 32, "granted_at": "yesterday"}),
    ],
)
def test_an_unreadable_record_is_read_as_no_consent(store, raw):
    """The safe reading of "I cannot tell whether they agreed" is that they
    did not."""
    store.set_setting(CONSENT_KEY, raw)
    assert read(store) is None
    assert enabled(store) is False


def test_the_record_is_the_only_place_the_identity_is_written(store):
    """An id copied into a second row is an id a revoke can miss."""
    consent = grant(store)
    raw = store.get_setting(CONSENT_KEY)

    assert consent.install_id in raw
    for other in ("install_id", "telemetry_id", "anonymous_id"):
        assert store.get_setting(other) is None


# --------------------------------------------------------------------------
# nothing here may reach out
# --------------------------------------------------------------------------


def test_the_consent_module_cannot_reach_the_network():
    """Reading consent happens on paths that run with telemetry disabled, so
    this module in particular must be inert."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(consent_mod.__file__).read_text("utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in {"socket", "http.client", "urllib.request"}
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "") not in {
                "socket",
                "http.client",
                "urllib.request",
            }
