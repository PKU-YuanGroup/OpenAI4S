"""Two halves of retention that disagreed, and the operation that was missing.

**Expiry withheld a memory and kept its slot.** `RETENTION_DAYS` stops injecting
a memory nobody has touched in a year, deliberately without deleting it -- an
undoable delete on a schedule the user never saw would be worse. But admission
counted every row in the scope, so two hundred expired memories filled the quota
with entries that are never injected and never will be, and every new write was
refused with "delete one first" about memories the pane already lists as
omitted. Withholding has to release the slot too, or the rule means one thing
when reading and another when writing.

**A memory could be written and deleted, never corrected.** Fixing a typo in
standing context meant delete-and-rewrite: two round trips through a scope that
may be at its cap, so the second can fail and leave the user with neither
version, and the row loses its place in the newest-first order that both the
pane and the injection use. And because retention keys off the last touch, an
edit that did not record one left the correction expiring on the original's
clock.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from openai4s import memory_budget
from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth
from openai4s.storage.memories import MemoryLimitError
from openai4s.store import get_store

DAY_MS = 86_400_000


def _store(tmp_path):
    return get_store(Config(data_dir=tmp_path).db_path)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


# --- the quota ---------------------------------------------------------------


def test_expired_memories_do_not_hold_the_scope_at_its_cap(tmp_path):
    """A scope of nothing but expired rows must still accept a new memory."""
    store = _store(tmp_path)
    try:
        long_ago = int(time.time() * 1000) - (memory_budget.RETENTION_MS + DAY_MS)
        store._memories._clock_ms = lambda: long_ago
        for index in range(memory_budget.MAX_MEMORIES_PER_SCOPE):
            store.add_memory(content=f"stale {index}", project_id="p")

        # Back to now: every row above is outside the retention window.
        store._memories._clock_ms = lambda: int(time.time() * 1000)
        saved = store.add_memory(content="a fact from today", project_id="p")

        assert saved["memory_id"]
        # And it is the one that gets injected -- the others are withheld, so
        # the quota they were holding bought nothing.
        resolved = store.resolve_memories("p")
        kept, dropped = memory_budget.select(resolved["memories"])
        assert kept == ["a fact from today"]
        assert all(item["reason"] == "expired" for item in dropped)
    finally:
        store.close()


def test_a_scope_of_live_memories_is_still_capped(tmp_path):
    """The change must not remove the limit, only stop expired rows holding it."""
    store = _store(tmp_path)
    try:
        for index in range(memory_budget.MAX_MEMORIES_PER_SCOPE):
            store.add_memory(content=f"live {index}", project_id="p")

        with pytest.raises(MemoryLimitError) as caught:
            store.add_memory(content="one too many", project_id="p")

        assert caught.value.code == "memory_scope_full"
    finally:
        store.close()


def test_the_stored_ceiling_is_its_own_refusal_with_its_own_remedy(tmp_path):
    """Otherwise releasing the live slot would leave the table unbounded.

    The two refusals are distinct because the fixes are: one says delete a
    memory you are still using, the other says delete rows that are not being
    injected at all.
    """
    store = _store(tmp_path)
    try:
        # How a scope actually reaches the ceiling: fill it, let a retention
        # window pass so those rows stop counting, fill it again. Seeding all
        # four hundred at one instant is impossible by construction -- the live
        # cap is enforced against the clock the write itself sees.
        base = int(time.time() * 1000) - 3 * memory_budget.RETENTION_MS
        clock = {"now": base}
        store._memories._clock_ms = lambda: clock["now"]
        for era in range(2):
            clock["now"] = base + era * (memory_budget.RETENTION_MS + DAY_MS)
            for index in range(memory_budget.MAX_MEMORIES_PER_SCOPE):
                store.add_memory(content=f"era {era} item {index}", project_id="p")

        clock["now"] = int(time.time() * 1000)
        with pytest.raises(MemoryLimitError) as caught:
            store.add_memory(content="no room at all", project_id="p")

        assert caught.value.code == "memory_scope_full_expired"
        assert "expired" in str(caught.value)
    finally:
        store.close()


# --- the edit ----------------------------------------------------------------


def test_an_edit_keeps_the_row_and_resets_its_retention_clock(tmp_path):
    store = _store(tmp_path)
    try:
        long_ago = int(time.time() * 1000) - (memory_budget.RETENTION_MS + DAY_MS)
        store._memories._clock_ms = lambda: long_ago
        saved = store.add_memory(content="use the old protocol", project_id="p")

        kept, dropped = memory_budget.select(store.resolve_memories("p")["memories"])
        assert kept == [] and dropped[0]["reason"] == "expired"

        store._memories._clock_ms = lambda: int(time.time() * 1000)
        edited = store.update_memory(
            saved["memory_id"], content="use the 2026 protocol", project_id="p"
        )

        assert edited["memory_id"] == saved["memory_id"], "the row was replaced"
        assert edited["created_at"] == long_ago, "the original write time was lost"
        assert edited["updated_at"] > long_ago
        kept, dropped = memory_budget.select(store.resolve_memories("p")["memories"])
        assert kept == ["use the 2026 protocol"], dropped
    finally:
        store.close()


def test_an_edit_cannot_reach_across_a_project_boundary(tmp_path):
    """Scoped exactly like delete: an id is not authority over a project."""
    store = _store(tmp_path)
    try:
        mine = store.add_memory(content="project a fact", project_id="a")

        assert (
            store.update_memory(mine["memory_id"], content="x", project_id="b") is None
        )
        assert store.list_memories(project_id="a")[0]["content"] == "project a fact"

        with pytest.raises(MemoryLimitError) as caught:
            store.update_memory(mine["memory_id"], content="x", project_id="all")
        assert caught.value.code == "memory_scope_invalid"
    finally:
        store.close()


def test_an_edit_refuses_the_same_things_a_write_does(tmp_path):
    store = _store(tmp_path)
    try:
        saved = store.add_memory(content="keep me", project_id="p")

        for content, code in (
            ("   ", "memory_empty"),
            ("x" * (memory_budget.MAX_MEMORY_CHARS + 1), "memory_too_long"),
        ):
            with pytest.raises(MemoryLimitError) as caught:
                store.update_memory(saved["memory_id"], content=content, project_id="p")
            assert caught.value.code == code
        with pytest.raises(MemoryLimitError) as caught:
            store.update_memory(saved["memory_id"], project_id="p")
        assert caught.value.code == "memory_no_change"

        # Refused before the UPDATE: the row is untouched.
        assert store.list_memories(project_id="p")[0]["content"] == "keep me"
        assert store.list_memories(project_id="p")[0]["updated_at"] is None
    finally:
        store.close()


def test_an_edited_memory_moves_to_the_front_of_the_injection_order(tmp_path):
    """The order is priority: `select` truncates from the end."""
    store = _store(tmp_path)
    try:
        base = int(time.time() * 1000)
        clock = {"now": base}
        store._memories._clock_ms = lambda: clock["now"]
        first = store.add_memory(content="first", project_id="p")
        clock["now"] = base + 1000
        store.add_memory(content="second", project_id="p")

        clock["now"] = base + 2000
        store.update_memory(
            first["memory_id"], content="first, corrected", project_id="p"
        )

        assert [row["content"] for row in store.list_memories(project_id="p")] == [
            "first, corrected",
            "second",
        ]
    finally:
        store.close()


# --- over the wire -----------------------------------------------------------


def test_the_edit_route_is_scoped_and_answers_the_edited_row(tmp_path):
    """Driven through the real handler, so the 400/404 are the ones HTTP sees."""
    port = _free_port()
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        host="127.0.0.1",
        port=port,
    )
    runner = gateway_mod.SessionRunner(cfg, _NullHub(), start_idle_sweeper=False)
    saved = runner.store.add_memory(content="before", project_id="p")
    handler_cls = gateway_mod.make_handler(cfg, runner.hub, runner)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    token = local_auth.load_or_mint(cfg.data_dir)

    def _patch(path, body):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        try:
            conn.request(
                "PATCH",
                path,
                body=json.dumps(body).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    local_auth.TOKEN_HEADER: token,
                },
            )
            response = conn.getresponse()
            return response.status, json.loads(response.read() or b"{}")
        finally:
            conn.close()

    try:
        mid = saved["memory_id"]
        status, body = _patch(f"/api/v1/memory/{mid}", {"content": "after"})
        assert status == 400, body
        assert body["code"] == "memory_scope_required"

        status, body = _patch(
            f"/api/v1/memory/{mid}?project_id=p", {"content": "after"}
        )
        assert status == 200, body
        assert body["content"] == "after"
        assert body["updated_at"]

        status, body = _patch(
            f"/api/v1/memory/{mid}?project_id=other", {"content": "x"}
        )
        assert status == 404, body
        assert body["code"] == "memory_not_found"

        status, body = _patch(f"/api/v1/memory/{mid}?project_id=p", {"content": " "})
        assert status == 400, body
        assert body["code"] == "memory_empty"
        assert runner.store.list_memories(project_id="p")[0]["content"] == "after"
    finally:
        httpd.shutdown()
        httpd.server_close()
        runner.close()


class _NullHub:
    def emitter(self, root_frame_id):
        def emit(event):
            del event

        return emit

    def broadcast(self, root_frame_id, event):
        del root_frame_id, event

    def has_subscriber(self, root_frame_id):
        del root_frame_id
        return False

    def drop_frame(self, root_frame_id):
        del root_frame_id
