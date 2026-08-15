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
        with pytest.raises(Exception) as caught:
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
    for table in ("user_llm_keys", "leases", "session_workloads"):
        with pytest.raises(Exception) as caught:
            store.query(f"SELECT * FROM {table}")
        assert table in str(caught.value).lower()
