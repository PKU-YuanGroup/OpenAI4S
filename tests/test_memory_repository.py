"""Direct contracts for long-term memory persistence."""

from __future__ import annotations

import pytest

from openai4s.config import Config
from openai4s.host_dispatch import HostDispatcher
from openai4s.store import get_store


def _store(tmp_path):
    return get_store(Config(data_dir=tmp_path).db_path)


def test_memory_repository_shares_store_boundary_and_preserves_filters(tmp_path):
    store = _store(tmp_path)
    assert store._memories._connection is store._conn
    assert store._memories._lock is store._lock
    timestamps = iter([100, 200, 300, 400])
    store._memories._clock_ms = lambda: next(timestamps)

    general = store.add_memory(content="general memory")
    first = store.add_memory(content="first a", block="research", project_id="a")
    second = store.add_memory(content="second a", block="research", project_id="a")
    other = store.add_memory(content="project b", block="research", project_id="b")

    assert general == {
        "memory_id": general["memory_id"],
        "project_id": "default",
        "block": "general",
        "content": "general memory",
        "created_at": 100,
    }
    assert [item["memory_id"] for item in store.list_memories()] == [
        other["memory_id"],
        second["memory_id"],
        first["memory_id"],
        general["memory_id"],
    ]
    assert store.list_memories(project_id=None) == store.list_memories(project_id="all")
    assert [item["content"] for item in store.list_memories(project_id="a")] == [
        "second a",
        "first a",
    ]
    assert len(store.list_memories(block="research")) == 3
    assert store.list_memories(project_id="a", block="missing") == []
    assert store.list_memories(project_id="a") == store._memories.list(project_id="a")


def test_memory_categories_legacy_default_delete_and_project_cascade(tmp_path):
    store = _store(tmp_path)
    store.add_memory(content="one", block="research", project_id="project-a")
    store.add_memory(content="two", block="research", project_id="project-a")
    with store._lock:
        store._conn.execute(
            "INSERT INTO memories(memory_id,project_id,block,content,created_at) "
            "VALUES(?,?,?,?,?)",
            ("legacy-memory", "project-a", None, "legacy", 1),
        )
        store._conn.commit()

    categories = store.memory_blocks("project-a")
    assert categories[0] == {"block": "research", "count": 2}
    assert {"block": "general", "count": 1} in categories
    assert store.memory_blocks(None) == store.memory_blocks("all")

    store.delete_memory("missing-memory")
    store.delete_memory("legacy-memory")
    assert all(
        item["memory_id"] != "legacy-memory"
        for item in store.list_memories(project_id="project-a")
    )

    store.create_project(name="Memory project", project_id="project-delete")
    store.add_memory(content="remove me", project_id="project-delete")
    store.delete_project("project-delete")
    assert store.list_memories(project_id="project-delete") == []
    with pytest.raises(PermissionError, match="memories"):
        store.query("SELECT * FROM memories")


def test_host_remember_uses_frame_project_and_repository(tmp_path):
    config = Config(data_dir=tmp_path)
    store = get_store(config.db_path)
    store.create_project(name="Science", project_id="science")
    frame_id = store.new_frame(project_id="science")
    dispatcher = HostDispatcher(config, frame_id=frame_id)

    assert dispatcher._m_remember({"content": "   "}) == {
        "error": "remember: empty content"
    }
    result = dispatcher._m_remember(
        {"content": "  preserve this result  ", "block": "facts"}
    )
    memories = store.list_memories(project_id="science")
    assert result == {"ok": True, "memory_id": memories[0]["memory_id"]}
    assert memories[0]["content"] == "preserve this result"
    assert memories[0]["block"] == "facts"
