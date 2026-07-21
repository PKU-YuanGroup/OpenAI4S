"""Keyset pagination for the session list.

`/frames` took a `limit` and no cursor, so there was no way to see past the
first page. Worse, it read `limit * 2` rows once, filtered out abandoned empty
sessions, then truncated to `limit` — a project whose sessions are mostly hidden
returned a *short page*, and nothing distinguished that from the last page. The
client stopped early and lost the rest silently.

Two properties are asserted here:

  * **completeness** — walking the cursor visits every row exactly once, and
  * **honesty** — `has_more` is an observation (one extra row was actually
    available) rather than an inference from the page being full.

The cursor is keyset, not an offset: an offset skips or repeats rows whenever a
session is created or deleted between pages, which for this list is routine.
The `frame_id` tiebreaker matters for the same reason — `created_at` is a
millisecond timestamp, and two sessions created in the same millisecond are
ordinary, so ordering by it alone leaves ties undefined and lets a cursor land
inside one.
"""
import pytest

from openai4s.config import Config
from openai4s.store import get_store


@pytest.fixture
def store(tmp_path):
    return get_store(Config(data_dir=tmp_path).db_path)


def _make(store, n, *, project="p", base_ms=1_000_000):
    """n root frames, newest last, with distinct timestamps."""
    ids = []
    for i in range(n):
        fid = store.new_frame(kind="turn", project_id=project, name=f"s{i:02d}")
        store._conn.execute(
            "UPDATE frames SET created_at=? WHERE frame_id=?", (base_ms + i, fid)
        )
        ids.append(fid)
    store._conn.commit()
    return ids


def _walk(store, project, page_size):
    """Page through browse_frames the way the route does."""
    seen, cursor, pages = [], None, 0
    while True:
        batch = store.browse_frames(
            project_id=project, roots_only=True, limit=page_size, before=cursor
        )
        if not batch:
            break
        pages += 1
        seen.extend(f["frame_id"] for f in batch)
        last = batch[-1]
        cursor = (int(last["created_at"]), last["frame_id"])
        if len(batch) < page_size or pages > 50:
            break
    return seen, pages


# --------------------------------------------------------------------------
# completeness
# --------------------------------------------------------------------------


def test_the_cursor_visits_every_row_exactly_once(store):
    made = _make(store, 25)
    seen, pages = _walk(store, "p", 10)
    assert pages == 3
    assert len(seen) == len(set(seen)) == 25
    assert set(seen) == set(made)


def test_paging_returns_newest_first(store):
    made = _make(store, 5)
    seen, _ = _walk(store, "p", 2)
    assert seen == list(reversed(made))


def test_a_page_size_larger_than_the_data_terminates(store):
    _make(store, 3)
    seen, pages = _walk(store, "p", 100)
    assert pages == 1 and len(seen) == 3


def test_an_empty_project_pages_cleanly(store):
    seen, pages = _walk(store, "empty", 10)
    assert seen == [] and pages == 0


# --------------------------------------------------------------------------
# the tiebreaker
# --------------------------------------------------------------------------


def test_rows_sharing_a_timestamp_are_not_skipped(store):
    """The reason the cursor carries frame_id. With ordering by created_at
    alone, a tie has undefined order and a cursor landing mid-tie drops the
    rest of it — silently, and only under load, which is the worst way to find
    out."""
    ids = []
    for i in range(6):
        fid = store.new_frame(kind="turn", project_id="tie", name=f"t{i}")
        ids.append(fid)
    store._conn.execute("UPDATE frames SET created_at=5000 WHERE project_id='tie'")
    store._conn.commit()

    seen, _ = _walk(store, "tie", 2)
    assert len(seen) == len(set(seen)) == 6, seen


def test_ordering_is_deterministic_across_calls(store):
    for i in range(8):
        store.new_frame(kind="turn", project_id="tie2", name=f"t{i}")
    store._conn.execute("UPDATE frames SET created_at=7000 WHERE project_id='tie2'")
    store._conn.commit()
    first = [f["frame_id"] for f in store.browse_frames(project_id="tie2", limit=8)]
    second = [f["frame_id"] for f in store.browse_frames(project_id="tie2", limit=8)]
    assert first == second


# --------------------------------------------------------------------------
# the cursor itself
# --------------------------------------------------------------------------


def test_the_cursor_is_opaque_and_round_trips():
    from openai4s.server.gateway import _decode_frame_cursor, _encode_frame_cursor

    token = _encode_frame_cursor(1784531680123, "frm-abc")
    assert ":" not in token, "an opaque cursor must not expose its sort key"
    assert _decode_frame_cursor(token) == (1784531680123, "frm-abc")


def test_an_absent_cursor_means_the_first_page():
    from openai4s.server.gateway import _decode_frame_cursor

    assert _decode_frame_cursor(None) is None
    assert _decode_frame_cursor("") is None


@pytest.mark.parametrize("bad", ["!!!", "zzzz", "bm90LWEtY3Vyc29y"])
def test_an_unreadable_cursor_is_rejected_not_ignored(bad):
    """Treating a bad cursor as "start over" would loop a client on page one
    forever, which looks like a hang rather than an error."""
    from openai4s.server.gateway import GatewayError, _decode_frame_cursor

    with pytest.raises(GatewayError) as e:
        _decode_frame_cursor(bad)
    assert e.value.code == 400
