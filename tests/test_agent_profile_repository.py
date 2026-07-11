"""Direct contracts for named agent-profile persistence."""

from __future__ import annotations

import itertools
import sqlite3
import threading

import pytest

from openai4s.config import Config
from openai4s.storage.agents import AgentProfileRepository
from openai4s.store import get_store


def _repository(tmp_path, *, lock=None):
    store = get_store(Config(data_dir=tmp_path).db_path)
    ticks = itertools.count(1000)
    repository = AgentProfileRepository(
        store._conn,
        lock or store._lock,
        clock_ms=lambda: next(ticks),
    )
    return store, repository


def test_insert_update_commit_and_preserve_created_at(tmp_path):
    store, repository = _repository(tmp_path)
    assert repository._connection is store._conn
    assert repository._lock is store._lock

    inserted = repository.upsert(
        name="PROTEIN_DESIGNER",
        description="design proteins",
        system_prompt="Use the science runtime.",
        skill_names=["fold", "sequence-design"],
        connectors=["metadata"],
        unrestricted=False,
    )
    assert inserted == {
        "name": "PROTEIN_DESIGNER",
        "description": "design proteins",
        "skill_names": ["fold", "sequence-design"],
        "connectors": ["metadata"],
        "unrestricted": 0,
        "system_prompt": "Use the science runtime.",
        "created_at": 1000,
        "updated_at": 1000,
    }

    updated = repository.upsert(
        name="PROTEIN_DESIGNER",
        description="refine proteins",
        system_prompt="Keep checkpoints.",
        skill_names=[],
        connectors=None,
        unrestricted=True,
    )
    assert updated == {
        "name": "PROTEIN_DESIGNER",
        "description": "refine proteins",
        "skill_names": [],
        "connectors": None,
        "unrestricted": 1,
        "system_prompt": "Keep checkpoints.",
        "created_at": 1000,
        "updated_at": 1001,
    }

    with sqlite3.connect(store.db_path) as independent:
        row = independent.execute(
            "SELECT description,skill_names,connectors,unrestricted,"
            "system_prompt,created_at,updated_at FROM agents WHERE name=?",
            ("PROTEIN_DESIGNER",),
        ).fetchone()
    assert row == (
        "refine proteins",
        "[]",
        None,
        1,
        "Keep checkpoints.",
        1000,
        1001,
    )


def test_list_orders_and_preserves_json_decoding_edges(tmp_path):
    store, repository = _repository(tmp_path)
    with store._lock:
        store._conn.executemany(
            "INSERT INTO agents(name,description,skill_names,connectors,"
            "unrestricted,system_prompt,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            [
                ("Z_AGENT", "z", "not-json", "", 0, "z prompt", 1, 1),
                ("A_AGENT", "a", "false", "null", 2, None, 2, 2),
            ],
        )
        store._conn.commit()

    agents = repository.list()
    assert [agent["name"] for agent in agents] == ["A_AGENT", "Z_AGENT"]
    assert agents[0]["skill_names"] is False
    assert agents[0]["connectors"] is None
    assert agents[0]["unrestricted"] == 2
    assert agents[1]["skill_names"] is None
    assert agents[1]["connectors"] == ""
    assert repository.get("a_agent") is None
    assert repository.get("A_AGENT") == agents[0]


def test_none_and_falsy_inputs_keep_legacy_serialization(tmp_path):
    store, repository = _repository(tmp_path)

    none_values = repository.upsert(
        name="NONE_VALUES",
        skill_names=None,
        connectors=None,
        unrestricted=0,
    )
    empty_values = repository.upsert(
        name="EMPTY_VALUES",
        skill_names=[],
        connectors=[],
        unrestricted="yes",
    )
    assert none_values["skill_names"] is None
    assert none_values["connectors"] is None
    assert none_values["unrestricted"] == 0
    assert empty_values["skill_names"] == []
    assert empty_values["connectors"] == []
    assert empty_values["unrestricted"] == 1

    with sqlite3.connect(store.db_path) as independent:
        rows = dict(
            independent.execute(
                "SELECT name,skill_names || '|' || connectors FROM agents "
                "WHERE name IN ('NONE_VALUES','EMPTY_VALUES')"
            ).fetchall()
        )
    assert rows == {"NONE_VALUES": None, "EMPTY_VALUES": "[]|[]"}


def test_serialization_failure_and_delete_are_committed(tmp_path):
    store, repository = _repository(tmp_path)

    with pytest.raises(TypeError):
        repository.upsert(name="BAD", skill_names=[object()])
    assert repository.get("BAD") is None

    repository.upsert(name="DELETE_ME")
    repository.delete("DELETE_ME")
    repository.delete("MISSING")
    with sqlite3.connect(store.db_path) as independent:
        assert independent.execute(
            "SELECT COUNT(*) FROM agents WHERE name='DELETE_ME'"
        ).fetchone() == (0,)


class _RecordingRLock:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.events: list[str] = []

    def __enter__(self):
        self._lock.acquire()
        self.events.append("enter")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.events.append("exit")
        self._lock.release()


def test_upsert_preserves_existing_read_then_write_lock_gap(tmp_path):
    lock = _RecordingRLock()
    _store, repository = _repository(tmp_path, lock=lock)

    repository.upsert(name="GAP")

    # Existence read, mutation, and result read remain three independent
    # critical sections.  In particular, no outer lock encloses all three.
    assert lock.events == ["enter", "exit", "enter", "exit", "enter", "exit"]
