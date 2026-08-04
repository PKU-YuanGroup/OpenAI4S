"""Which memories a session gets, and which writes it refuses.

The defect that mattered was not a leak but a silence. The Memory pane sent no
scope, the route defaulted to the literal string ``"default"``, and nothing on
this installation creates a project by that name -- every Web session belongs to
a real ``proj_*``, and injection reads *the session's* project. So a memory
saved in the UI was stored, listed back, counted in the categories chips, and
injected into exactly nothing. Every surface said the feature worked.

That shape -- accepted, stored, never read -- is why the tests below assert
against the real prompt builder and the real HTTP handler rather than against
the repository. The repository was never wrong; the callers were.

The rest is the boundary the scope draws once it exists: an id-only delete
crossing projects, and writes that must be refused *before* the insert rather
than trimmed at injection time, where the user who saved the item is no longer
watching.
"""

from __future__ import annotations

import json
import time

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.host_dispatch import HostDispatcher
from openai4s.memory_budget import (
    MAX_MEMORIES_PER_SCOPE,
    MAX_MEMORY_CHARS,
    RETENTION_MS,
)
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth
from openai4s.storage.memories import GLOBAL_SCOPE


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


class _Client:
    """The real handler, the real dispatcher, the real Store.

    Statuses come back from `_route`, not from a service call: a GatewayError
    raised inside a route is only a 4xx if the dispatcher turns it into one,
    and asserting on the exception would not check that.
    """

    def __init__(self, tmp_path):
        self.cfg = Config(
            data_dir=tmp_path,
            llm=LLMConfig(provider="deepseek", api_key="test-key"),
            max_turns=1,
        )
        self.runner = gateway_mod.SessionRunner(self.cfg, _Hub())
        self.store = self.runner.store
        self.store.set_setting("memory_enabled", "1")
        self._handler_class = gateway_mod.make_handler(self.cfg, _Hub(), self.runner)
        self._token = local_auth.read_token(tmp_path) or ""

    def project(self, name):
        self.store.create_project(name=name, description="", context="")
        return {p["name"]: p["project_id"] for p in self.store.list_projects()}[name]

    def system_prompt(self, project_id):
        """What a session in this project would actually be seeded with."""
        state = self.runner._state(self.runner.create_session(project_id), project_id)
        self.runner._seed_messages(state)
        return str(
            next(m for m in state.messages if m.get("role") == "system")["content"]
        )

    def get(self, path):
        return self._call("GET", path, None)

    def post(self, path, body):
        return self._call("POST", path, body)

    def delete(self, path):
        return self._call("DELETE", path, None)

    def _call(self, method, path, body):
        handler = object.__new__(self._handler_class)
        handler._correlation_id = "req-1"
        sent: dict = {}

        def _send(code, payload, ctype, extra=None):
            sent["code"] = code
            sent["body"] = json.loads(payload.decode("utf-8"))

        handler._send = _send
        handler.command = method
        handler.path = f"/api/v1{path}"
        handler.headers = {
            "Content-Length": "0",
            local_auth.TOKEN_HEADER: self._token,
        }
        if body is not None:
            handler._body = lambda: body
        handler._route(method)
        return sent["code"], sent["body"]


@pytest.fixture
def client(tmp_path):
    return _Client(tmp_path)


# --------------------------------------------------------------------------
# (a) the silence: a save that reached no session
# --------------------------------------------------------------------------


def test_a_save_without_a_scope_is_refused_instead_of_landing_where_nothing_reads(
    client,
):
    """The exact mismatch the pane shipped: no project_id in, "default" out.

    The old route answered 200 and stored the row under a project id that no
    session, on any installation, has ever read. Refusing is the only outcome
    that cannot silently mean "saved nowhere" -- a different default would just
    move the problem to whichever project happened to be wrong.
    """
    alpha = client.project("alpha")

    code, body = client.post("/memory", {"content": "PANE-CANARY", "block": "general"})

    assert code == 400
    assert body["code"] == "memory_scope_required"
    assert client.store.list_memories(project_id="default") == []
    assert "PANE-CANARY" not in client.system_prompt(alpha)


def test_a_scoped_save_is_in_the_next_session_of_that_project(client):
    alpha = client.project("alpha")

    code, saved = client.post(
        "/memory",
        {"content": "ALPHA-CANARY", "block": "general", "project_id": alpha},
    )

    assert code == 200
    assert saved["project_id"] == alpha
    assert "ALPHA-CANARY" in client.system_prompt(alpha)


def test_a_save_addressed_to_a_project_that_does_not_exist_is_refused(client):
    """The same defect with a different spelling. A write to an id nothing owns
    is stored and unreachable, which is what "default" was."""
    client.project("alpha")

    code, body = client.post(
        "/memory", {"content": "GHOST", "project_id": "proj_does_not_exist"}
    )

    assert code == 400
    assert body["code"] == "memory_scope_unknown"


# --------------------------------------------------------------------------
# (a) the tiers: inherit, override, and still not leak
# --------------------------------------------------------------------------


def test_a_project_inherits_global_memories_but_its_own_block_wins(client):
    """Global is the tier "remember this everywhere" needs; the per-block
    override is how a project says "here, not that". Asserted on the prompt,
    because the merge only matters where it is read."""
    alpha = client.project("alpha")
    client.post(
        "/memory",
        {"content": "GLOBAL-FACT", "block": "facts", "project_id": GLOBAL_SCOPE},
    )
    client.post(
        "/memory",
        {"content": "GLOBAL-STYLE", "block": "style", "project_id": GLOBAL_SCOPE},
    )
    client.post(
        "/memory",
        {"content": "ALPHA-STYLE", "block": "style", "project_id": alpha},
    )

    prompt = client.system_prompt(alpha)

    assert "GLOBAL-FACT" in prompt, "an untouched global block is inherited"
    assert "ALPHA-STYLE" in prompt
    assert "GLOBAL-STYLE" not in prompt, "the project's own block replaces it"


def test_inheritance_does_not_reopen_the_cross_project_leak(client):
    """The merge widens a scope by exactly one tier and no further."""
    alpha = client.project("alpha")
    beta = client.project("beta")
    client.post("/memory", {"content": "BETA-CANARY", "project_id": beta})
    client.post("/memory", {"content": "GLOBAL-CANARY", "project_id": GLOBAL_SCOPE})

    prompt = client.system_prompt(alpha)

    assert "GLOBAL-CANARY" in prompt
    assert "BETA-CANARY" not in prompt


def test_a_projects_own_memories_outrank_what_it_inherits(client):
    """Order is priority: `select` truncates from the end, so a project full of
    its own context must not lose it to the global background."""
    alpha = client.project("alpha")
    client.post("/memory", {"content": "GLOBAL-FIRST", "project_id": GLOBAL_SCOPE})
    client.post(
        "/memory", {"content": "ALPHA-SECOND", "block": "facts", "project_id": alpha}
    )

    ordered = [m["content"] for m in client.store.list_memories(project_id=alpha)]

    assert ordered == ["ALPHA-SECOND", "GLOBAL-FIRST"]


# --------------------------------------------------------------------------
# (c) delete is scoped
# --------------------------------------------------------------------------


def test_a_delete_does_not_cross_a_project_boundary(client):
    """The classic id-only delete. `DELETE /memory/<id>` took no scope, so any
    client holding an id could remove another project's memory and be answered
    the same `{"ok": true}` either way."""
    alpha = client.project("alpha")
    beta = client.project("beta")
    _, saved = client.post("/memory", {"content": "ALPHA-ONLY", "project_id": alpha})

    code, body = client.delete(f"/memory/{saved['memory_id']}?project_id={beta}")

    assert code == 404
    assert body["code"] == "memory_not_found"
    assert [m["content"] for m in client.store.list_memories(project_id=alpha)] == [
        "ALPHA-ONLY"
    ]

    code, _ = client.delete(f"/memory/{saved['memory_id']}?project_id={alpha}")

    assert code == 200
    assert client.store.list_memories(project_id=alpha) == []


def test_a_delete_that_names_no_scope_is_refused_rather_than_guessed(client):
    alpha = client.project("alpha")
    _, saved = client.post("/memory", {"content": "ALPHA-ONLY", "project_id": alpha})

    code, body = client.delete(f"/memory/{saved['memory_id']}")

    assert code == 400
    assert body["code"] == "memory_scope_required"
    assert len(client.store.list_memories(project_id=alpha)) == 1


def test_a_global_memory_is_not_deletable_through_a_project_that_inherits_it(client):
    """Inheritance is a read. A project that can see a global memory must not
    be able to remove it for every other project."""
    alpha = client.project("alpha")
    _, saved = client.post(
        "/memory", {"content": "GLOBAL-ONLY", "project_id": GLOBAL_SCOPE}
    )

    code, _ = client.delete(f"/memory/{saved['memory_id']}?project_id={alpha}")

    assert code == 404
    assert len(client.store.list_memories(project_id=GLOBAL_SCOPE)) == 1


# --------------------------------------------------------------------------
# (b) budgets refuse before the insert
# --------------------------------------------------------------------------


def test_an_oversized_memory_is_refused_before_it_is_stored(client):
    """The injection budget already skipped these -- silently, one turn later,
    with the person who saved it long gone. The refusal has to happen while
    they are looking at it."""
    alpha = client.project("alpha")

    code, body = client.post(
        "/memory", {"content": "x" * (MAX_MEMORY_CHARS + 1), "project_id": alpha}
    )

    assert code == 400
    assert body["code"] == "memory_too_long"
    assert client.store.list_memories(project_id=alpha) == []


def test_a_scope_cannot_grow_past_its_limit(client):
    alpha = client.project("alpha")
    for index in range(MAX_MEMORIES_PER_SCOPE):
        client.store.add_memory(content=f"note {index}", project_id=alpha)

    code, body = client.post("/memory", {"content": "one more", "project_id": alpha})

    assert code == 400
    assert body["code"] == "memory_scope_full"
    assert len(client.store.list_memories(project_id=alpha)) == MAX_MEMORIES_PER_SCOPE


def test_an_empty_memory_is_refused_instead_of_stored_as_a_blank_row(client):
    alpha = client.project("alpha")

    code, body = client.post("/memory", {"content": "   ", "project_id": alpha})

    assert code == 400
    assert body["code"] == "memory_empty"
    assert client.store.list_memories(project_id=alpha) == []


def test_a_refused_write_reaches_a_cell_as_an_error_not_a_dead_kernel(client, tmp_path):
    """`host.remember` is a side effect inside someone's analysis. A raised
    limit would take the cell down with it."""
    alpha = client.project("alpha")
    frame_id = client.store.new_frame(project_id=alpha)
    dispatcher = HostDispatcher(client.cfg, frame_id=frame_id)

    result = dispatcher._m_remember({"content": "x" * (MAX_MEMORY_CHARS + 1)})

    assert set(result) == {"error"}
    assert "at most" in result["error"]
    assert client.store.list_memories(project_id=alpha) == []


def test_an_expired_memory_is_withheld_and_reported_but_not_destroyed(client):
    """Retention withholds; it does not delete. A year-old standing instruction
    is more likely stale than durable, and a stale instruction is followed
    without comment -- but deleting on a timer would destroy something a person
    wrote, on a schedule they never saw."""
    alpha = client.project("alpha")
    now = int(time.time() * 1000)
    client.store.add_memory(content="FRESH", project_id=alpha)
    with client.store._lock:
        client.store._conn.execute(
            "INSERT INTO memories(memory_id,project_id,block,content,created_at) "
            "VALUES(?,?,?,?,?)",
            ("mem_stale", alpha, "general", "STALE", now - RETENTION_MS - 1),
        )
        client.store._conn.commit()

    prompt = client.system_prompt(alpha)
    code, preview = client.get(f"/memory/context?project_id={alpha}")

    assert "FRESH" in prompt
    assert "STALE" not in prompt
    assert code == 200
    assert preview["included_count"] == 1
    assert [item["reason"] for item in preview["omitted"]] == ["expired"]
    # Still there, still listed, still one click from being saved again.
    assert "STALE" in [m["content"] for m in client.store.list_memories(alpha)]


# --------------------------------------------------------------------------
# (d) the pane can say which scope it is showing
# --------------------------------------------------------------------------


def test_the_context_preview_names_its_scope_and_what_inheritance_did(client):
    """The counts the Memory pane renders. Without them an inherited memory
    that a project's own block hid is indistinguishable from one that was never
    saved, and those call for opposite actions."""
    alpha = client.project("alpha")
    client.post(
        "/memory", {"content": "G-FACT", "block": "facts", "project_id": GLOBAL_SCOPE}
    )
    client.post(
        "/memory", {"content": "G-STYLE", "block": "style", "project_id": GLOBAL_SCOPE}
    )
    client.post(
        "/memory", {"content": "A-STYLE", "block": "style", "project_id": alpha}
    )

    code, preview = client.get(f"/memory/context?project_id={alpha}")

    assert code == 200
    assert preview["project_id"] == alpha
    assert preview["inherited_count"] == 1  # facts
    assert preview["overridden_count"] == 1  # style
    assert preview["included_count"] == 2
    assert "A-STYLE" in preview["context"] and "G-FACT" in preview["context"]
    assert "G-STYLE" not in preview["context"]
