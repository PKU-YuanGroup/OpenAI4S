"""`host.query` must not read a colleague's prompts (external review #4).

The denylist covered identity and governance tables and the artifact family
was closed behind scoped views. `messages`, `execution_log` and `frames`
were left readable — which in a single-user install is the user's own work
and is documented as allowed, and in team mode is every colleague's
prompts, the model's replies to them, and the code they ran.

So the closure is conditional, and both halves are asserted here: team
mode refuses the direct read and answers through the scoped view, and
team mode off reads exactly what it always read (INV-1).
"""

from __future__ import annotations

import sqlite3

import pytest

from openai4s.store import get_store


def _seed(store):
    """Two sessions belonging to two different people."""
    mine = store.new_frame(kind="turn", project_id="p")
    theirs = store.new_frame(kind="turn", project_id="p")
    store.add_message(root_frame_id=mine, role="user", content="my own question")
    store.add_message(
        root_frame_id=theirs, role="user", content="SOMEBODY ELSE PRIVATE PROMPT"
    )
    return mine, theirs


@pytest.fixture()
def store(tmp_path):
    st = get_store(str(tmp_path / "state.db"))
    try:
        yield st
    finally:
        st.close()


def test_team_mode_refuses_a_direct_read_of_everyone_s_messages(store, monkeypatch):
    monkeypatch.setenv("OPENAI4S_TEAM_MODE", "1")
    mine, theirs = _seed(store)
    scope = {"root_frame_id": mine, "project_id": "p"}

    for table in ("messages", "execution_log", "frames"):
        # `PermissionError`, not `Exception`. A *missing* table raises
        # `sqlite3.OperationalError: no such table: …`, which is also an
        # Exception and also contains the table name -- so the loose spelling
        # could not tell "the denylist refused it" from "the migration never
        # created it", and would have gone on reporting green.
        with pytest.raises(PermissionError) as caught:
            store.query(f"SELECT * FROM {table}", scope=scope)
        assert table in str(caught.value).lower(), str(caught.value)


def test_the_scoped_view_answers_and_answers_only_this_session(store, monkeypatch):
    monkeypatch.setenv("OPENAI4S_TEAM_MODE", "1")
    mine, theirs = _seed(store)
    scope = {"root_frame_id": mine, "project_id": "p"}

    rows = store.query("SELECT * FROM my_messages", scope=scope)
    blob = " ".join(str(r) for r in rows)
    assert "my own question" in blob
    assert (
        "SOMEBODY ELSE PRIVATE PROMPT" not in blob
    ), "the scoped view returned another session's prompt"


def test_a_single_user_install_reads_what_it_always_read(store, monkeypatch):
    """INV-1. `tests/test_store.py` documents the direct read as allowed, and
    in a single-user database every session is the same person's work."""
    monkeypatch.delenv("OPENAI4S_TEAM_MODE", raising=False)
    mine, theirs = _seed(store)
    rows = store.query("SELECT * FROM messages")
    assert len(rows) >= 2


def test_the_credential_table_is_denied_outright(store, monkeypatch):
    """No scoped view for this one: there is no legitimate agent read of a
    table holding a broker reference and every user's id."""
    monkeypatch.setenv("OPENAI4S_TEAM_MODE", "1")
    # These three are exactly the tables the two newest migrations create, so
    # a bare `pytest.raises(Exception)` here would have reported green for a
    # database where those migrations never ran -- "no such table" and "not
    # readable" are both Exceptions naming the table.
    for table in ("user_llm_keys", "leases", "session_workloads"):
        row = store._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        assert row is not None, f"{table} does not exist; the denial proves nothing"
        with pytest.raises(PermissionError) as caught:
            store.query(f"SELECT * FROM {table}")
        assert table in str(caught.value).lower()


#: Base tables `host.query` may read directly, in team mode as well as
#: single-user. Adding a name here is a security decision, not a formality:
#: it says the whole content is the agent's own working data in every
#: deployment.
_DELIBERATELY_PUBLIC = {
    # Schema bookkeeping. No user content, and the support surfaces document
    # reading it.
    "schema_migrations",
}


def test_every_table_is_classified(store):
    """A denylist with no completeness check is a list somebody has to
    remember to extend, and this one has been forgotten twice already: the
    commit adding `user_llm_keys`, `leases` and `session_workloads` records
    that they "were written and committed once -- and lost" to a stash/pop
    with nothing detecting it, and a later review found sixteen older tables
    readable by any agent cell, one of them holding a plaintext credential.

    So the rule is mechanical now: every base table is denied, view-only, or
    deliberately public. A migration that adds a table fails here until
    somebody decides which -- and that decision is exactly what was being
    skipped.
    """
    from openai4s.store import (
        _TEAM_VIEW_ONLY_TABLES,
        _VIEW_ONLY_TABLES,
        QUERY_DENYLIST,
    )

    with sqlite3.connect(store.db_path) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }
    classified = (
        set(QUERY_DENYLIST)
        | set(_VIEW_ONLY_TABLES)
        | set(_TEAM_VIEW_ONLY_TABLES)
        | _DELIBERATELY_PUBLIC
    )
    unclassified = sorted(tables - classified)
    assert not unclassified, (
        "these tables are readable by any agent cell and nobody has decided "
        f"they should be: {unclassified}. Put each in QUERY_DENYLIST (secrets, "
        "identity, another tenant's state), in _TEAM_VIEW_ONLY_TABLES "
        "(per-session or per-project content), or in _DELIBERATELY_PUBLIC "
        "above if it is genuinely the agent's own working data."
    )


def test_a_cte_cannot_impersonate_a_scoped_view(store, monkeypatch):
    """The authorizer's fifth argument is not caller-proof.

    SQLite names "the view responsible for this read" in that argument, and it
    fills it in for a CTE exactly as it does for a view -- so a caller who
    names a CTE `my_messages` produced the byte-identical callback the real
    temp view produces, and the escape hatch let it through. In team mode that
    one string was the entire tenant boundary for `host.query`: this query used
    to return the other person's prompt.
    """
    monkeypatch.setenv("OPENAI4S_TEAM_MODE", "1")
    mine, theirs = _seed(store)
    scope = store.resolve_frame_scope(mine)

    for view, table in (
        ("my_messages", "messages"),
        ("my_frames", "frames"),
        ("my_execution_log", "execution_log"),
        ("my_artifacts", "artifacts"),
    ):
        for quoted in (
            view,
            f'"{view}"',
            f"[{view}]",
            f"`{view}`",
            f"'{view}'",
        ):
            sql = (
                f"WITH {quoted} AS (SELECT * FROM main.{table}) "
                f"SELECT * FROM {quoted}"
            )
            with pytest.raises(PermissionError, match=view):
                store.query(sql, scope=scope)

    # SQLite treats every non-ASCII code point as part of a bare identifier.
    # A combining mark therefore used to stop the small lexer at the first,
    # harmless CTE and hide the scoped shadow after the comma.
    with pytest.raises(PermissionError, match="my_messages"):
        store.query(
            "WITH e\u0301 AS (SELECT 1), "
            "my_messages AS (SELECT * FROM main.messages) "
            "SELECT content FROM my_messages",
            scope=scope,
        )

    # The real view still answers, and still answers only for this session.
    rows = store.query("SELECT content FROM my_messages", scope=scope)
    assert [r["content"] for r in rows] == ["my own question"]

    # A named WINDOW uses the same ``name AS (...)`` spelling but is not a
    # CTE and cannot replace a scoped view. The guard must not reject valid
    # SQLite syntax merely because the window happens to share a view name.
    ranked = store.query(
        "SELECT row_number() OVER my_messages AS n FROM my_frames "
        "WINDOW my_messages AS (ORDER BY frame_id)",
        scope=scope,
    )
    assert [row["n"] for row in ranked] == [1]


def test_the_authorizer_refuses_a_view_name_nothing_published(monkeypatch):
    """Second layer, independent of the text check above.

    The escape hatch used to accept any name in `_SCOPED_VIEWS` whether or not
    a view answered to it, so it was a decision about a string rather than
    about the statement. It now asks what this statement actually created:
    `query()` passes the scoped set when it published the views and an empty
    one when it did not.
    """
    from openai4s.store import _SCOPED_VIEWS, _QueryAuthorizer

    published = _QueryAuthorizer(published_views=_SCOPED_VIEWS)
    assert (
        published(sqlite3.SQLITE_READ, "artifacts", "name", "main", "my_artifacts")
        == sqlite3.SQLITE_OK
    )
    unpublished = _QueryAuthorizer()
    assert (
        unpublished(sqlite3.SQLITE_READ, "artifacts", "name", "main", "my_artifacts")
        == sqlite3.SQLITE_DENY
    )
