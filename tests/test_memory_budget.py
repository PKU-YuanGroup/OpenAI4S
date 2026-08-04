"""How much remembered context may enter a system prompt.

The injection was `mems[:50]` — a count, and nothing else. Fifty memories of a
pasted protocol is about 600,000 characters, roughly 150k tokens against a
262,144-token window: over half the context spent on background before the user
has typed anything, on every turn, growing quietly as they save more. A count
cannot bound that, because length is the thing that varies.
"""

from __future__ import annotations

import time

from openai4s import memory_budget


def _memories(count, chars):
    """`chars` is the exact final length, suffix included.

    An earlier version appended ` {index}` on top of `chars`, which pushed
    every item a few characters over the per-item cap — so the total-budget
    test never reached the total at all and was really re-testing the per-item
    one. The same slip as the image-attachment budgets, caught the same way.
    """
    out = []
    for index in range(count):
        suffix = f" {index}"
        body = "x" * max(1, chars - len(suffix))
        out.append(
            {"content": (body + suffix)[:chars] if chars > len(suffix) else body}
        )
    return out


def test_a_count_alone_does_not_bound_the_prompt():
    """The defect, as arithmetic. Fifty items under the old rule; a fraction of
    the characters under the new one."""
    memories = _memories(60, 12_000)
    old_style = "\n".join(f"- {m['content']}" for m in memories[:50])
    assert len(old_style) > 500_000

    kept, dropped = memory_budget.select(memories)
    rendered = memory_budget.render(kept, dropped)
    assert len(rendered) < memory_budget.MAX_TOTAL_CHARS + 1000
    assert dropped


def test_the_three_budgets_each_do_something():
    # count
    kept, dropped = memory_budget.select(_memories(60, 10))
    assert len(kept) == memory_budget.MAX_MEMORIES
    assert {d["reason"] for d in dropped} == {"too_many"}

    # per item
    kept, dropped = memory_budget.select(
        _memories(3, memory_budget.MAX_MEMORY_CHARS + 1)
    )
    assert kept == []
    assert {d["reason"] for d in dropped} == {"too_long"}

    # total
    each = memory_budget.MAX_MEMORY_CHARS // 2
    assert each < memory_budget.MAX_MEMORY_CHARS, "per-item cap would fire first"
    needed = memory_budget.MAX_TOTAL_CHARS // each + 3
    assert needed < memory_budget.MAX_MEMORIES, "count cap would fire first"
    kept, dropped = memory_budget.select(_memories(needed, each))
    assert sum(len(text) for text in kept) <= memory_budget.MAX_TOTAL_CHARS
    assert any(d["reason"] == "budget_exhausted" for d in dropped)


def test_an_over_long_memory_is_skipped_rather_than_clipped():
    """Half a protocol is not a shorter protocol — it is a different and
    possibly wrong one, and an agent cannot tell that the instruction it is
    following was cut in half."""
    long_one = "A" * 5000 + " THEN STOP"
    kept, dropped = memory_budget.select([{"content": long_one}])
    assert kept == []
    assert dropped[0]["reason"] == "too_long"
    assert dropped[0]["chars"] == len(long_one)


def test_order_is_preserved_so_the_oldest_is_what_gets_dropped():
    """The store returns newest-first, so truncating from the end drops the
    oldest — the choice a user would make. Re-ranking by length would silently
    prefer terse memories over important ones."""
    memories = [{"content": f"memory {index}"} for index in range(60)]
    kept, _dropped = memory_budget.select(memories)
    assert kept[0] == "memory 0"
    assert kept[-1] == f"memory {memory_budget.MAX_MEMORIES - 1}"


def test_the_model_is_told_when_something_was_withheld():
    """For the model, not the user. An agent told its remembered context is
    incomplete can say so when it matters, instead of answering as though it
    had everything — which is what turns a budget into a correctness problem.
    """
    kept, dropped = memory_budget.select(_memories(60, 10))
    rendered = memory_budget.render(kept, dropped)
    assert "not included here" in rendered
    assert "complete remembered context" in rendered

    # ...and stays quiet when nothing was dropped, because a note that always
    # fires trains people to ignore it.
    kept, dropped = memory_budget.select(_memories(2, 10))
    assert dropped == []
    assert "not included here" not in memory_budget.render(kept, dropped)


def test_nothing_remembered_renders_nothing():
    assert memory_budget.render(*memory_budget.select([])) == ""


def test_blank_memories_are_ignored_without_being_reported_as_dropped():
    """An empty row is not a withheld memory, and counting it as one would
    make the note claim context exists that never did."""
    kept, dropped = memory_budget.select([{"content": "  "}, {"content": "real"}])
    assert kept == ["real"]
    assert dropped == []


# --------------------------------------------------------------------------
# the other half of B.6: what the Context projection admits to
# --------------------------------------------------------------------------


def test_the_system_prompt_is_not_counted_as_conversation():
    """The panel said "Text: N tokens" and meant two different things.

    Standing context -- memory, skills, specialists, connectors, environments
    -- is rebuilt from scratch every turn and compaction never touches it.
    Counted inside `text`, a large system prompt read as "your conversation is
    long", and the user reached for the one remedy that cannot help.
    """
    from openai4s.agent.compaction import estimate_context

    estimate = estimate_context(
        [
            {"role": "system", "content": "S" * 40_000},
            {"role": "user", "content": "hi"},
        ]
    )
    assert estimate.system_prompt > 9_000
    assert estimate.text < 100, "the system prompt is still inside conversation text"


def test_splitting_the_system_prompt_out_does_not_change_the_total():
    """Compaction decides on `.total`. Moving tokens between buckets must not
    move the threshold, or a display change would silently retune when the
    conversation gets summarised."""
    from openai4s.agent.compaction import estimate_context

    messages = [
        {"role": "system", "content": "S" * 8000},
        {"role": "user", "content": "u" * 4000},
        {"role": "tool", "content": "t" * 2000},
    ]
    estimate = estimate_context(messages)
    assert estimate.total == (
        estimate.text
        + estimate.images
        + estimate.tool_schemas
        + estimate.tool_calls
        + estimate.tool_results
        + estimate.artifact_refs
        + estimate.wire_state
        + estimate.system_prompt
    )
    # and the sum still reflects every character that was passed in
    assert estimate.total > (8000 + 4000 + 2000) // 4


def test_the_projection_reports_what_the_budgets_left_out():
    """A projection that lists only what is present reads as complete. The one
    thing a user needs to know about a budget is when it fired."""
    from openai4s.server.workbench_state import SessionWorkbenchStateService

    class _State:
        context_omissions = {
            "memory": [
                {"reason": "too_long", "preview": "secret protocol"},
                {"reason": "too_long", "preview": "another"},
                {"reason": "too_many", "preview": "third"},
            ]
        }

    omitted = SessionWorkbenchStateService._omissions(_State())
    assert omitted == [
        {
            "kind": "memory",
            "count": 3,
            "reasons": [
                {"reason": "too_long", "count": 2},
                {"reason": "too_many", "count": 1},
            ],
        }
    ]


def test_the_projection_does_not_re_render_withheld_memory_text():
    """Aggregate counts, not previews. These are memories deliberately kept out
    of the model's context; a panel is not the place to put them back on
    screen."""
    from openai4s.server.workbench_state import SessionWorkbenchStateService

    class _State:
        context_omissions = {
            "memory": [{"reason": "too_long", "preview": "PATIENT-42"}]
        }

    rendered = repr(SessionWorkbenchStateService._omissions(_State()))
    assert "PATIENT-42" not in rendered


def test_a_session_with_nothing_dropped_reports_nothing():
    from openai4s.server.workbench_state import SessionWorkbenchStateService

    class _Empty:
        context_omissions: dict = {}

    assert SessionWorkbenchStateService._omissions(_Empty()) == []
    assert SessionWorkbenchStateService._omissions(object()) == []


# --------------------------------------------------------------------------
# against the real seeder, not the module in isolation
# --------------------------------------------------------------------------


def _runner(tmp_path):
    from openai4s.config import Config, LLMConfig
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
    return gateway_mod.SessionRunner(cfg, _Hub())


def test_a_pasted_protocol_no_longer_fills_the_window(tmp_path):
    """The defect at the size it actually occurred.

    Sixty saved memories of a pasted protocol put 600,647 characters into every
    system prompt — about 150k tokens against a 262,144-token window, before
    the user had typed anything. The assertion is a fixed character count, not
    a multiple of the constants under test: expressing it in terms of the
    budget is how the image-attachment tests ended up unable to fail.

    Seeded through SQL rather than `add_memory`, because the write path now
    refuses a 10,000-character memory outright. These rows are still reachable
    -- they are what an install that predates the limit already holds, and what
    a session import carries -- and the injection budget is what has to hold
    the line for them.
    """
    runner = _runner(tmp_path)
    store = runner.store
    store.set_setting("memory_enabled", "1")
    store.create_project(name="alpha", description="", context="")
    alpha = {p["name"]: p["project_id"] for p in store.list_projects()}["alpha"]
    now = int(time.time() * 1000)
    with store._lock:
        for index in range(60):
            store._conn.execute(
                "INSERT INTO memories(memory_id,project_id,block,content,"
                "created_at) VALUES(?,?,?,?,?)",
                (
                    f"legacy-{index}",
                    alpha,
                    "general",
                    f"protocol {index}: " + "x" * 10_000,
                    # Recent, so this stays a test of the *size* budget rather
                    # than accidentally becoming one of the retention window.
                    now - index,
                ),
            )
        store._conn.commit()

    state = runner._state(runner.create_session(alpha), alpha)
    runner._seed_messages(state)
    system = next(m for m in state.messages if m.get("role") == "system")
    assert len(str(system["content"])) < 60_000, "the old join was ~600,000 characters"


def test_the_agent_is_told_its_remembered_context_is_incomplete(tmp_path):
    runner = _runner(tmp_path)
    store = runner.store
    store.set_setting("memory_enabled", "1")
    store.create_project(name="alpha", description="", context="")
    alpha = {p["name"]: p["project_id"] for p in store.list_projects()}["alpha"]
    for index in range(60):
        store.add_memory(content=f"note {index}", block="general", project_id=alpha)

    state = runner._state(runner.create_session(alpha), alpha)
    runner._seed_messages(state)
    system = str(
        next(m for m in state.messages if m.get("role") == "system")["content"]
    )
    assert "not included here" in system
    # ...and the Context panel can say so too.
    assert state.context_omissions["memory"]


def test_the_feature_off_means_not_one_character(tmp_path):
    """`memory_enabled` defaults to "0". Off has to mean nothing at all in the
    prompt — not a heading, not an empty section, not a note about what was
    withheld."""
    runner = _runner(tmp_path)
    store = runner.store
    store.create_project(name="alpha", description="", context="")
    alpha = {p["name"]: p["project_id"] for p in store.list_projects()}["alpha"]
    store.add_memory(content="OFF-CANARY", block="general", project_id=alpha)

    state = runner._state(runner.create_session(alpha), alpha)
    runner._seed_messages(state)
    system = str(
        next(m for m in state.messages if m.get("role") == "system")["content"]
    )
    assert "OFF-CANARY" not in system
    assert "Remembered context" not in system
    assert "not included here" not in system
    assert state.context_omissions == {}
