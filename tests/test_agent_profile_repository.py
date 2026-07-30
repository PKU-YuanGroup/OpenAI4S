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
    # `2` is not a state this column has — it is written here to prove the
    # decode is a decode and not a passthrough. It used to assert `== 2`, which
    # pinned the raw SQLite int as the contract; that is what reached
    # `child_execution_policy`'s `type(x) is not bool` check and made every
    # stored specialist fail to delegate. A boolean column reads back as a
    # boolean, and anything truthy in it means unrestricted.
    assert agents[0]["unrestricted"] is True
    assert agents[1]["unrestricted"] is False
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


def test_editing_a_specialist_does_not_wipe_its_resource_restrictions(tmp_path):
    """The privilege-escalation shape of a full-overwrite update.

    `PUT /specialists/<name>` called `upsert_agent`, which writes every column,
    while the editor in app.js posts only `{name, description, system_prompt}`.
    Every edit therefore wrote NULL over `skill_names` and `connectors` and
    reset `unrestricted` to True: a specialist confined to two skills came back
    unconfined, and nothing said so. Renaming a specialist's description
    widened what it could reach.

    The direction is what makes this urgent rather than untidy. A restriction
    that silently loosens looks exactly like one that was never set.
    """
    store = get_store(tmp_path / "agents.db")
    store.upsert_agent(
        name="confined",
        description="original",
        system_prompt="be careful",
        skill_names=["literature-review"],
        connectors=["example"],
        unrestricted=False,
    )

    # What the editor sends: three fields, nothing about resources.
    updated = store.update_agent(
        "confined", description="renamed", system_prompt="be careful"
    )

    assert updated["description"] == "renamed"
    assert updated["skill_names"] == ["literature-review"]
    assert updated["connectors"] == ["example"]
    assert updated["unrestricted"] in (0, False)
    store.close()


def test_a_specialist_allowlist_can_still_be_cleared_deliberately(tmp_path):
    """Absent and null have to stay tellable apart.

    A NULL allowlist is a real state -- "inherit" -- so "not supplied" cannot
    be spelled `None`. Only keys actually present are written.
    """
    store = get_store(tmp_path / "agents2.db")
    store.upsert_agent(
        name="confined",
        description="d",
        system_prompt="p",
        skill_names=["a"],
        connectors=["b"],
        unrestricted=False,
    )

    cleared = store.update_agent("confined", skill_names=None)
    assert cleared["skill_names"] is None
    assert cleared["connectors"] == ["b"]

    emptied = store.update_agent("confined", connectors=[])
    assert emptied["connectors"] == []

    assert store.update_agent("no-such-specialist", description="x") is None
    store.close()


def test_the_specialist_edit_route_updates_without_widening(tmp_path):
    """The route, not just the repository.

    Driven through `Handler._api` because the defect lived at the call site:
    the repository has always been able to store an allowlist, and the route
    was the thing that threw it away.

    This also keeps `PUT /specialists/<name> [ok]` covered. It used to be
    pinned only by accident -- the contract driver probes every route with a
    synthetic name, `upsert` created the specialist, and the create's 200 was
    filed as the edit's success shape. Now that a PUT to a nonexistent
    specialist is a 404, the success shape needs a scenario that actually
    edits something.
    """
    from openai4s.config import LLMConfig
    from openai4s.server import gateway as gateway_mod

    class _Hub:
        def emitter(self, root_frame_id):
            return lambda event: None

        def broadcast(self, root_frame_id, event):
            return None

    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=3,
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    runner.store.upsert_agent(
        name="confined",
        description="original",
        system_prompt="p",
        skill_names=["literature-review"],
        connectors=["example"],
        unrestricted=False,
    )

    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    handler = object.__new__(handler_cls)
    replies: list = []
    handler._query = lambda: {}
    handler._body = lambda: {"description": "renamed", "system_prompt": "p"}
    handler._json = lambda obj, code=200: replies.append((code, obj))

    handler._api("PUT", "/specialists/confined")

    code, body = replies[-1]
    assert code == 200
    assert body["description"] == "renamed"
    assert body["skill_names"] == ["literature-review"]
    assert body["connectors"] == ["example"]

    # PATCH is the same branch and must keep the same guarantee.
    replies.clear()
    handler._body = lambda: {"system_prompt": "revised"}
    handler._api("PATCH", "/specialists/confined")
    code, body = replies[-1]
    assert code == 200
    assert body["system_prompt"] == "revised"
    assert body["description"] == "renamed"
    assert body["skill_names"] == ["literature-review"]

    # And an edit at a name that does not exist no longer silently creates one.
    replies.clear()
    handler._body = lambda: {"description": "x"}
    with pytest.raises(gateway_mod.GatewayError) as raised:
        handler._api("PUT", "/specialists/never-created")
    assert raised.value.code == 404
    assert runner.store.get_agent("never-created") is None
