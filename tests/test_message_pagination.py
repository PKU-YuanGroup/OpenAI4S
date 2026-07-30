"""Which end of a long conversation you see, and whether you can walk back.

Opening a 640-message session returned messages 0-299: the *oldest* page, with
the newest 340 simply not present. Ordering was `seq ASC` with a limit and no
direction, so the only page you could get was the beginning of history — which
is the wrong end of a conversation, and the longer the session the wronger.

Paging back is by keyset (`before_seq`), not offset. Ordering newest-first and
offsetting would skew on every arriving message, because a new message shifts
what "offset 50 from the newest" means. `seq` is monotonic and unique per root
frame, so it needs no tiebreaker — unlike the session list, which needs
`(created_at, frame_id)` because two sessions can share a millisecond.
"""

from __future__ import annotations

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.store import get_store

#: P1-A's stated exit criterion for this item.
LONG_SESSION = 640


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        pass


def _seeded(tmp_path, count=LONG_SESSION):
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    store = get_store(cfg.db_path)
    frame_id = store.new_frame(kind="turn", project_id="p")
    for index in range(count):
        store.add_message(
            root_frame_id=frame_id,
            role="user",
            frame_id=frame_id,
            content=f"message {index}",
        )
    return cfg, store, frame_id


def test_a_long_session_can_be_read_from_its_newest_end(tmp_path):
    """The defect: 640 messages in, 0-299 out."""
    _cfg, store, frame_id = _seeded(tmp_path)

    oldest_first = store.list_messages(frame_id, limit=50)
    assert oldest_first[0]["content"] == "message 0"

    newest_first = store.list_messages(frame_id, limit=50, newest_first=True)
    assert newest_first[0]["content"] == f"message {LONG_SESSION - 1}"
    assert newest_first[-1]["content"] == f"message {LONG_SESSION - 50}"


def test_walking_back_covers_the_whole_session_exactly_once(tmp_path):
    _cfg, store, frame_id = _seeded(tmp_path)
    seen: list[str] = []
    cursor = None
    pages = 0
    while pages < 40:
        rows = store.list_messages(
            frame_id, limit=50, newest_first=True, before_seq=cursor
        )
        if not rows:
            break
        seen.extend(row["content"] for row in rows)
        cursor = rows[-1]["seq"]
        pages += 1
    assert len(seen) == LONG_SESSION
    assert len(set(seen)) == LONG_SESSION, "a message was returned twice"


def test_a_page_does_not_shift_when_a_message_arrives(tmp_path):
    """The reason it is a cursor and not an offset.

    Newest-first plus OFFSET means every arriving message shifts what "offset
    50 from the newest" points at, so a user scrolling back through a live
    session would see rows repeat or vanish. A `seq` bound is stable because it
    names a position in history rather than a distance from a moving end.
    """
    _cfg, store, frame_id = _seeded(tmp_path, count=120)
    head = store.list_messages(frame_id, limit=50, newest_first=True)
    cursor = head[-1]["seq"]

    before = [
        m["content"]
        for m in store.list_messages(
            frame_id, limit=10, newest_first=True, before_seq=cursor
        )
    ]
    store.add_message(
        root_frame_id=frame_id, role="user", frame_id=frame_id, content="ARRIVES NOW"
    )
    after = [
        m["content"]
        for m in store.list_messages(
            frame_id, limit=10, newest_first=True, before_seq=cursor
        )
    ]
    assert before == after


def test_the_route_reports_the_next_cursor_rather_than_inferring_it(tmp_path):
    """`has_earlier` is observed, not derived from a short page.

    A page can be short because the branch projection hid rows, which a client
    cannot tell apart from the end of history — the same reasoning the session
    list uses when it asks for one row more than it needs.
    """
    cfg, store, frame_id = _seeded(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    handler_class = gateway_mod.make_handler(cfg, _Hub(), runner)

    def call(query):
        handler = object.__new__(handler_class)
        handler.headers = {}
        handler.path = f"/api/v1/frames/{frame_id}/messages{query}"
        seen: list[dict] = []
        handler._json = lambda obj, code=200: seen.append(obj)
        handler._body = lambda: {}
        handler._api("GET", f"/frames/{frame_id}/messages")
        return seen[-1]

    try:
        first = call("?limit=50&newest_first=1")
        assert first["messages"][0]["content"] == f"message {LONG_SESSION - 1}"
        assert first["has_earlier"] is True
        assert first["next_before_seq"] is not None

        collected = [m["content"] for m in first["messages"]]
        cursor = first["next_before_seq"]
        for _ in range(40):
            body = call(f"?limit=50&before_seq={cursor}")
            collected.extend(m["content"] for m in body["messages"])
            if not body["has_earlier"]:
                break
            cursor = body["next_before_seq"]
        assert len(set(collected)) == LONG_SESSION

        # Absent the flag, the response is exactly what it always was.
        legacy = call("?limit=50")
        assert legacy["messages"][0]["content"] == "message 0"
        assert "has_earlier" not in legacy
        assert "next_before_seq" not in legacy
    finally:
        runner.close()


def test_a_malformed_cursor_is_refused_rather_than_coerced(tmp_path):
    cfg, store, frame_id = _seeded(tmp_path, count=5)
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    handler_class = gateway_mod.make_handler(cfg, _Hub(), runner)
    try:
        handler = object.__new__(handler_class)
        handler.headers = {}
        handler.path = f"/api/v1/frames/{frame_id}/messages?before_seq=banana"
        handler._json = lambda obj, code=200: None
        handler._body = lambda: {}
        with pytest.raises(gateway_mod.GatewayError) as refused:
            handler._api("GET", f"/frames/{frame_id}/messages")
        assert refused.value.code == 400
        assert refused.value.error_code == "invalid_cursor"
    finally:
        runner.close()
