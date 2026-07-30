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
    assert [item["memory_id"] for item in store.list_memories(project_id="all")] == [
        other["memory_id"],
        second["memory_id"],
        first["memory_id"],
        general["memory_id"],
    ]
    assert [item["content"] for item in store.list_memories(project_id="a")] == [
        "second a",
        "first a",
    ]
    assert len(store.list_memories(project_id="all", block="research")) == 3
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
    with pytest.raises(ValueError):
        store.memory_blocks(None)

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


def test_an_unscoped_memory_read_is_refused_rather_than_answered_with_everything(
    tmp_path,
):
    """The cross-project view has to be asked for by name.

    `project_id=None` used to mean "no WHERE clause". The gateway seeded system
    prompts with `list_memories(project_id=st.project_id or "all")`, so a
    session whose project_id was falsy silently carried the whole
    installation's remembered context -- other projects included -- into the
    model. Nothing failed; the prompt just quietly got bigger and wronger.

    Falling closed is the right direction here: a missing memory is visible and
    gets reported, a leaked one is neither.
    """
    store = get_store(tmp_path / "mem.db")
    store.add_memory(content="alpha secret", block="general", project_id="alpha")
    store.add_memory(content="beta secret", block="general", project_id="beta")

    with pytest.raises(ValueError):
        store.list_memories()
    with pytest.raises(ValueError):
        store.list_memories(project_id="")

    scoped = [m["content"] for m in store.list_memories(project_id="alpha")]
    assert scoped == ["alpha secret"]

    everything = {m["content"] for m in store.list_memories(project_id="all")}
    assert everything == {"alpha secret", "beta secret"}
    store.close()


def test_a_session_prompt_carries_only_its_own_projects_memories(tmp_path):
    """The seam that leaked: `list_memories(project_id=st.project_id or "all")`.

    Asserted against the real prompt builder, because the repository was never
    the thing that was wrong -- the call site was.

    Scope of the claim, stated plainly: the degenerate state is forced here
    rather than reproduced. `resolve_frame_scope` chains
    `root.project_id or frame.project_id or fallback_project` and every
    `_state` caller supplies a non-empty fallback, so no public entry point
    currently reaches a `SessionState` with a falsy `project_id`. What was
    wrong is the *direction*: a scope boundary that falls open answers "every
    project" for a question it could not resolve, and the answer goes into a
    prompt where nothing will ever flag it. Unreachable today is one refactor
    from reachable, and pinning it costs one line.

    Without the fix this fails on the forced state; with `st.project_id`
    populated it passes either way, which is why the forcing is the test.
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
    store = runner.store
    store.set_setting("memory_enabled", "1")
    store.create_project(name="alpha", description="", context="")
    projects = {p["name"]: p["project_id"] for p in store.list_projects()}
    alpha = projects["alpha"]
    store.add_memory(content="ALPHA-CANARY", block="general", project_id=alpha)
    store.add_memory(content="BETA-CANARY", block="general", project_id="beta")

    frame_id = runner.create_session(alpha)

    scoped = runner._state(frame_id, alpha)
    runner._seed_messages(scoped)
    seeded = "\n".join(str(m.get("content") or "") for m in scoped.messages)
    assert "ALPHA-CANARY" in seeded
    assert "BETA-CANARY" not in seeded

    # The forced degenerate state: a session that cannot name its project must
    # not be handed every project's memories.
    unscoped = runner._state(frame_id, alpha)
    unscoped.project_id = ""
    unscoped.messages = []
    runner._seed_messages(unscoped)
    degenerate = "\n".join(str(m.get("content") or "") for m in unscoped.messages)
    assert "BETA-CANARY" not in degenerate
    assert "ALPHA-CANARY" not in degenerate
