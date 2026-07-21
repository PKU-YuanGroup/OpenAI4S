"""Monotonic sequence numbers and the WebSocket resume cursor.

A turn keeps running server-side after every client disconnects, so a browser
that drops mid-turn has to catch up when it comes back. The hub already kept a
capped replay buffer, but replay was all-or-nothing: a reconnecting client
received the whole turn again and had no way to tell which of those events it
had already rendered. Duplicated deltas are not a cosmetic problem — the stream
is append-only text, so a re-delivered chunk is indistinguishable from new
output.

Contract v1 (proposal §4.6) asks for a monotonic sequence and a resume cursor.
Each event now carries `seq`; a client passes the highest one it actually
applied as `since_seq` and gets only what came after.

The buffer is capped, so a client away longer than the window cannot be served
by a cursor at all. That case is reported (`gap: true`) rather than papered
over, because silently starting mid-stream would leave a hole the client cannot
see.
"""
import pytest

from openai4s.server.gateway import WSHub


class _Conn:
    def __init__(self):
        self.subs = set()
        self.alive = True
        self.sent = []

    def send_json(self, obj):
        self.sent.append(obj)


def _chunks(conn):
    return [e["d"] for e in conn.sent if e.get("type") == "text_chunk"]


def _seqs(conn):
    return [e["seq"] for e in conn.sent if e.get("seq")]


@pytest.fixture
def hub_with_turn():
    """A hub mid-turn: text_reset opened the buffer, three chunks followed."""
    hub = WSHub()
    live = _Conn()
    hub.add(live)
    hub.subscribe("f1", live)
    for event in (
        {"type": "text_reset"},
        {"type": "text_chunk", "d": "A"},
        {"type": "text_chunk", "d": "B"},
        {"type": "text_chunk", "d": "C"},
    ):
        hub.broadcast("f1", dict(event))
    return hub, live


# --------------------------------------------------------------------------
# sequence
# --------------------------------------------------------------------------


def test_every_broadcast_event_is_numbered(hub_with_turn):
    hub, live = hub_with_turn
    assert _seqs(live) == [1, 2, 3, 4]


def test_the_sequence_does_not_reset_between_turns(hub_with_turn):
    """A per-turn counter would restart at 1, so a client holding cursor=4 from
    the previous turn would look like it already had the new turn's first four
    events and skip them."""
    hub, live = hub_with_turn
    hub.broadcast("f1", {"type": "text_reset"})
    hub.broadcast("f1", {"type": "text_chunk", "d": "D"})
    assert _seqs(live) == [1, 2, 3, 4, 5, 6]


def test_frames_are_numbered_independently():
    hub = WSHub()
    a, b = _Conn(), _Conn()
    hub.add(a)
    hub.add(b)
    hub.subscribe("f1", a)
    hub.subscribe("f2", b)
    hub.broadcast("f1", {"type": "text_reset"})
    hub.broadcast("f2", {"type": "text_reset"})
    assert _seqs(a) == [1]
    assert _seqs(b) == [1]


# --------------------------------------------------------------------------
# resume
# --------------------------------------------------------------------------


def test_a_cursor_replays_only_what_was_missed(hub_with_turn):
    """The headline behaviour. Without it the client re-receives "A" and, since
    the stream is append-only text, cannot tell it from new output."""
    hub, _ = hub_with_turn
    back = _Conn()
    hub.add(back)
    hub.subscribe("f1", back, since_seq=2)
    assert _chunks(back) == ["B", "C"]


def test_no_cursor_replays_the_whole_buffer(hub_with_turn):
    """A first-time viewer has nothing to resume from; the old behaviour is
    still the right one for them."""
    hub, _ = hub_with_turn
    fresh = _Conn()
    hub.add(fresh)
    hub.subscribe("f1", fresh, since_seq=0)
    assert _chunks(fresh) == ["A", "B", "C"]


def test_a_cursor_at_the_head_replays_nothing(hub_with_turn):
    hub, _ = hub_with_turn
    caught_up = _Conn()
    hub.add(caught_up)
    hub.subscribe("f1", caught_up, since_seq=4)
    assert _chunks(caught_up) == []


def test_a_cursor_beyond_the_head_replays_nothing(hub_with_turn):
    """A stale or fabricated cursor must not wrap around into a full replay."""
    hub, _ = hub_with_turn
    ahead = _Conn()
    hub.add(ahead)
    hub.subscribe("f1", ahead, since_seq=9999)
    assert _chunks(ahead) == []


def test_replay_bounds_are_reported(hub_with_turn):
    hub, _ = hub_with_turn
    back = _Conn()
    hub.add(back)
    hub.subscribe("f1", back, since_seq=2)
    begin = back.sent[0]
    assert begin["type"] == "replay_begin"
    assert begin["from_seq"] == 3
    assert begin["to_seq"] == 4
    assert back.sent[-1] == {
        "type": "replay_end",
        "root_frame_id": "f1",
        "to_seq": 4,
    }


def test_a_gap_is_reported_rather_than_hidden():
    """The buffer is capped. A client away longer than the window cannot be
    served by its cursor, and resuming mid-stream anyway would leave a hole it
    has no way to detect."""
    hub = WSHub()
    live = _Conn()
    hub.add(live)
    hub.subscribe("f1", live)
    hub.broadcast("f1", {"type": "text_reset"})
    for i in range(5):
        hub.broadcast("f1", {"type": "text_chunk", "d": str(i)})

    # Simulate eviction: drop the oldest events the way the cap would.
    buf = hub._live["f1"]
    buf["events"] = buf["events"][-2:]

    back = _Conn()
    hub.add(back)
    hub.subscribe("f1", back, since_seq=1)
    assert back.sent[0]["gap"] is True


def test_no_gap_is_reported_for_a_contiguous_resume(hub_with_turn):
    hub, _ = hub_with_turn
    back = _Conn()
    hub.add(back)
    hub.subscribe("f1", back, since_seq=2)
    assert back.sent[0]["gap"] is False


def test_a_fresh_subscriber_is_not_told_there_is_a_gap(hub_with_turn):
    """since_seq=0 means "I have nothing", not "I lost something"."""
    hub, _ = hub_with_turn
    fresh = _Conn()
    hub.add(fresh)
    hub.subscribe("f1", fresh, since_seq=0)
    assert fresh.sent[0]["gap"] is False


# --------------------------------------------------------------------------
# ordering
# --------------------------------------------------------------------------


def test_live_events_continue_the_same_sequence_after_a_resume(hub_with_turn):
    """The number a resumed client sees next must follow the replay, or its
    cursor would go backwards on the first live event."""
    hub, _ = hub_with_turn
    back = _Conn()
    hub.add(back)
    hub.subscribe("f1", back, since_seq=2)
    hub.broadcast("f1", {"type": "text_chunk", "d": "D"})
    assert _seqs(back) == [3, 4, 5]
