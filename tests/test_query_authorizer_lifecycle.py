"""Taking the guard off has to actually take it off, on every interpreter.

`Store.query` brackets the caller's SQL with an authorizer and clears it twice:
once so the privileged scoped-view creation is not subject to the caller's
rules, and once in its `finally` so the daemon gets its connection back.

Both clears were `conn.set_authorizer(None)`, which removes the authorizer from
Python 3.11 onwards and **does not** on 3.10 -- this project's declared floor
(`requires-python = ">=3.10"`). There the C trampoline stays installed with no
Python callable behind it and SQLite reads the failed callback as
`SQLITE_DENY`, so "no restrictions" silently meant "deny everything".

The consequence was not confined to `host.query`. The Store holds ONE
connection, so the `finally` left it deny-all for the rest of the process:
after a single agent SQL statement, an ordinary `new_frame` raised
`not authorized`. Nineteen tests reported this accurately on the py3.10 CI job
and were read as a py3.10 quirk for as long as they were red.

READ THIS BEFORE FALSIFYING. Restoring `set_authorizer(None)` leaves every test
in this file GREEN on 3.11+, because there it is correct. The mutation is only
visible on 3.10:

    uv run --python 3.10 --extra science pytest -q tests/test_query_authorizer_lifecycle.py

which is why the CI matrix carries 3.10 at all.
"""

from __future__ import annotations

import sqlite3
import sys

import pytest

from openai4s.store import get_store


@pytest.fixture()
def store(tmp_path):
    node = get_store(str(tmp_path / "store.db"))
    try:
        yield node
    finally:
        node.close()


def _scope(store):
    frame_id = store.new_frame(kind="turn", project_id="proj")
    return frame_id, {"root_frame_id": frame_id, "project_id": "proj"}


def test_clearing_the_authorizer_really_clears_it():
    """The primitive, isolated from the Store.

    `set_authorizer(None)` is a no-op-shaped call that means two different
    things on either side of 3.11, and nothing in this repository asserted
    which one it got.
    """
    from openai4s.store import _clear_authorizer

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE t(x)")
        conn.set_authorizer(lambda *_event: sqlite3.SQLITE_DENY)
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("SELECT * FROM t")

        _clear_authorizer(conn)

        assert conn.execute("SELECT * FROM t").fetchall() == []
    finally:
        conn.close()


def test_the_scoped_views_can_be_built_while_the_guard_is_off(store):
    """The first thing that broke. Creating the views is a privileged setup
    step, and it runs inside the window the guard is supposed to be off for."""
    frame_id, scope = _scope(store)
    del frame_id

    rows = store.query("SELECT COUNT(*) AS n FROM my_artifacts", scope=scope)

    assert rows == [{"n": 0}]


def test_one_agent_query_does_not_brick_the_daemons_connection(store):
    """The consequence, and the reason this is a product defect rather than a
    test artefact: the Store has one connection and every later caller shares
    it. Measured on 3.10 before the fix -- `new_frame` raised
    `not authorized` for the life of the process."""
    _frame_id, scope = _scope(store)

    store.query("SELECT 1 AS one", scope=scope)

    after = store.new_frame(kind="turn", project_id="proj")
    assert after
    assert store.get_frame(after)


def test_the_guard_is_still_a_guard(store):
    """The other half, without which "clear the authorizer" could be fixed by
    never installing it. A permissive callback in the wrong place looks exactly
    like a working one until something reads a table it should not."""
    _frame_id, scope = _scope(store)

    with pytest.raises(PermissionError) as caught:
        store.query("SELECT * FROM artifacts", scope=scope)
    assert "artifacts" in str(caught.value)

    with pytest.raises(PermissionError):
        store.query("SELECT * FROM sqlite_master", scope=scope)


def test_the_guard_is_reinstalled_for_the_next_caller(store):
    """Clearing in the `finally` must not leave the *next* query ungated. The
    permissive callback is installed on the shared connection, so a second
    query has to re-arm the real one rather than inherit the permissive one."""
    _frame_id, scope = _scope(store)

    store.query("SELECT 1 AS one", scope=scope)

    with pytest.raises(PermissionError):
        store.query("SELECT * FROM artifacts", scope=scope)


def test_the_interpreter_boundary_this_is_about_is_stated_not_guessed():
    """`None` gained its meaning in 3.11. Pinning the boundary here means a
    future reader does not have to rediscover which side they are on."""
    from openai4s.store import _AUTHORIZER_ACCEPTS_NONE

    assert _AUTHORIZER_ACCEPTS_NONE == (sys.version_info >= (3, 11))
