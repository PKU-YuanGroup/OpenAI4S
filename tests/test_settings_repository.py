"""Direct contracts for settings, model profiles, and feedback."""

from __future__ import annotations

import itertools
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from openai4s.config import Config
from openai4s.storage.settings import SettingsRepository
from openai4s.store import get_store


def _repository(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    ticks = itertools.count(1000)
    repository = SettingsRepository(
        store._conn,
        store._lock,
        clock_ms=lambda: next(ticks),
    )
    return store, repository


def test_key_value_upsert_defaults_and_commit(tmp_path):
    store, repository = _repository(tmp_path)
    assert repository.get("missing") is None
    assert repository.get("missing", "fallback") == "fallback"

    repository.set("mode", "first")
    repository.set("mode", "second")
    assert repository.get("mode") == "second"

    with sqlite3.connect(store.db_path) as independent:
        row = independent.execute(
            "SELECT value,updated_at FROM settings WHERE key='mode'"
        ).fetchone()
    assert row == ("second", 1001)


@pytest.mark.parametrize("raw", ["", "not-json", "null", "{}", "1"])
def test_model_profiles_invalid_or_non_list_values_normalize_empty(tmp_path, raw):
    _store, repository = _repository(tmp_path)
    repository.set("model_profiles", raw)
    assert repository.list_model_profiles() == []


def test_model_profile_round_trip_mutation_return_and_failure_boundary(tmp_path):
    _store, repository = _repository(tmp_path)
    profiles = [
        {"id": "one", "provider": "ark"},
        {"id": "two", "provider": "gemini"},
    ]
    repository.set_model_profiles(profiles)
    assert repository.list_model_profiles() == profiles

    result = repository.mutate_model_profiles(
        lambda values: values.insert(0, {"id": "zero"}) or "inserted"
    )
    assert result == "inserted"
    assert [item["id"] for item in repository.list_model_profiles()] == [
        "zero",
        "one",
        "two",
    ]

    def fail(values):
        values.append({"id": "must-not-commit"})
        raise RuntimeError("mutation failed")

    with pytest.raises(RuntimeError, match="mutation failed"):
        repository.mutate_model_profiles(fail)
    assert "must-not-commit" not in {
        item["id"] for item in repository.list_model_profiles()
    }


def test_model_profile_mutations_are_serialized_by_shared_rlock(tmp_path):
    _store, repository = _repository(tmp_path)

    def append(index):
        repository.mutate_model_profiles(
            lambda profiles: profiles.append({"id": f"profile-{index}"})
        )

    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(append, range(40)))

    assert {profile["id"] for profile in repository.list_model_profiles()} == {
        f"profile-{index}" for index in range(40)
    }


def test_feedback_projection_and_falsy_delete(tmp_path):
    store, repository = _repository(tmp_path)
    repository.set_feedback("frame-a", "message:1", "up")
    repository.set_feedback("frame-a", "2", "down")
    repository.set_feedback("frame-b", "1", "up")
    assert repository.list_feedback("frame-a") == {
        "message:1": "up",
        "2": "down",
    }

    repository.set_feedback("frame-a", "2", None)
    repository.set_feedback("frame-a", "message:1", "")
    assert repository.list_feedback("frame-a") == {}
    assert repository.list_feedback("frame-b") == {"1": "up"}

    with sqlite3.connect(store.db_path) as independent:
        rows = independent.execute(
            "SELECT key,value FROM settings WHERE key LIKE 'fb:%'"
        ).fetchall()
    assert rows == [("fb:frame-b:1", "up")]


def test_store_facade_and_permission_seed_share_settings_repository(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    assert isinstance(store._settings, SettingsRepository)

    store.set_setting("feature", "on")
    assert store.get_setting("feature") == "on"
    store.set_model_profiles([{"id": "model"}])
    assert store.list_model_profiles() == [{"id": "model"}]
    store.set_feedback("frame", "1", "up")
    assert store.list_feedback("frame") == {"1": "up"}

    store.seed_default_permission_rules()
    assert store.get_setting("perm_seeded") == "1"
