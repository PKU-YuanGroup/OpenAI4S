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
    hub.subscribe("f1", back, since_seq=2, epoch=hub.epoch)
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
    hub.subscribe("f1", caught_up, since_seq=4, epoch=hub.epoch)
    assert _chunks(caught_up) == []


def test_a_cursor_beyond_the_head_replays_nothing(hub_with_turn):
    """A stale or fabricated cursor must not wrap around into a full replay."""
    hub, _ = hub_with_turn
    ahead = _Conn()
    hub.add(ahead)
    hub.subscribe("f1", ahead, since_seq=9999, epoch=hub.epoch)
    assert _chunks(ahead) == []


def test_replay_bounds_are_reported(hub_with_turn):
    hub, _ = hub_with_turn
    back = _Conn()
    hub.add(back)
    hub.subscribe("f1", back, since_seq=2, epoch=hub.epoch)
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
    hub.subscribe("f1", back, since_seq=1, epoch=hub.epoch)
    assert back.sent[0]["gap"] is True


def test_no_gap_is_reported_for_a_contiguous_resume(hub_with_turn):
    hub, _ = hub_with_turn
    back = _Conn()
    hub.add(back)
    hub.subscribe("f1", back, since_seq=2, epoch=hub.epoch)
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
    hub.subscribe("f1", back, since_seq=2, epoch=hub.epoch)
    hub.broadcast("f1", {"type": "text_chunk", "d": "D"})
    assert _seqs(back) == [3, 4, 5]


# --------------------------------------------------------------------------
# every subscription carries the epoch, even an idle one
# --------------------------------------------------------------------------


def _epoch_of(conn):
    for event in conn.sent:
        if event.get("type") == "replay_begin":
            return event.get("epoch")
    return None


def test_an_idle_subscription_still_receives_the_epoch():
    """An idle frame — no running turn — used to send nothing, so the client
    recorded its next cursor with a null epoch. After a restart the numeric
    stale check then accepted that epoch-less cursor and skipped the new
    daemon's early events."""
    hub = WSHub()
    conn = _Conn()
    hub.add(conn)
    hub.subscribe("idle-frame", conn, since_seq=0)

    assert (
        _epoch_of(conn) == hub.epoch
    ), "an idle subscription must still hand the client this daemon's epoch"
    assert _chunks(conn) == [], "there is nothing to replay, only the epoch"


def test_a_cursor_from_a_previous_daemon_is_rejected_even_when_numerically_placeable():
    """The exact skipped-events scenario.

    A client subscribed to an idle frame under daemon A (epoch A), recorded a
    cursor, then daemon B (epoch B) restarted and emitted events reaching that
    cursor's number before the client reconnected. The cursor is numerically
    placeable, so only the epoch can catch it — which is why every subscription
    must carry one.
    """
    daemon_a = WSHub()
    first = _Conn()
    daemon_a.add(first)
    daemon_a.subscribe("f1", first, since_seq=0)
    epoch_a = _epoch_of(first)
    assert epoch_a == daemon_a.epoch

    # Daemon B restarts (a new epoch) and runs a turn on the same frame,
    # emitting events 1..4.
    daemon_b = WSHub()
    watcher = _Conn()
    daemon_b.add(watcher)
    daemon_b.subscribe("f1", watcher, since_seq=0)
    for event in (
        {"type": "text_reset"},
        {"type": "text_chunk", "d": "X"},
        {"type": "text_chunk", "d": "Y"},
        {"type": "text_chunk", "d": "Z"},
    ):
        daemon_b.broadcast("f1", dict(event))
    assert daemon_b.epoch != epoch_a

    # The client reconnects to daemon B presenting its old cursor AND old
    # epoch. Numerically seq(4) >= 2, but the epoch does not match, so the gap
    # must be declared rather than the early events silently skipped.
    reconnect = _Conn()
    daemon_b.add(reconnect)
    daemon_b.subscribe("f1", reconnect, since_seq=2, epoch=epoch_a)
    begin = next(e for e in reconnect.sent if e.get("type") == "replay_begin")
    assert (
        begin["gap"] is True
    ), "a cursor from a previous daemon must be declared a gap, not accepted"


def test_a_nonzero_cursor_without_an_epoch_is_a_gap():
    """Codex P1: the case the numeric check cannot see.

    An old tab, or a client that predates the epoch handshake, reconnects
    after a restart with `since_seq=2` and no epoch. The new daemon has by
    then emitted at least two events of its own, so its counter is *not* below
    the cursor — the numeric check declared the cursor fresh, and replay
    filtered the new daemon's events 1 and 2 out as already seen. The client
    was left believing it was caught up on a stream whose beginning it never
    received.

    An epoch-less cursor cannot be placed in either direction, so it is a gap.
    """
    hub = WSHub()
    watcher = _Conn()
    hub.add(watcher)
    hub.subscribe("f1", watcher, since_seq=0, epoch=hub.epoch)
    for event in (
        {"type": "text_reset"},
        {"type": "text_chunk", "d": "X"},
        {"type": "text_chunk", "d": "Y"},
    ):
        hub.broadcast("f1", dict(event))
    assert hub._seq["f1"] >= 2, "the premise: our counter is not below the cursor"

    legacy = _Conn()
    hub.add(legacy)
    hub.subscribe("f1", legacy, since_seq=2)  # no epoch — a legacy client
    begin = next(e for e in legacy.sent if e.get("type") == "replay_begin")
    assert begin["gap"] is True, (
        "an epoch-less cursor cannot be proven to belong to this stream; "
        "accepting it silently skips this daemon's early events"
    )
    assert _chunks(legacy) == [], "a declared gap replays nothing; the client refetches"


def test_a_zero_cursor_without_an_epoch_is_still_not_a_gap():
    """`since_seq=0` claims nothing, so there is nothing to misplace — a first
    subscribe must not be turned into a refetch."""
    hub = WSHub()
    conn = _Conn()
    hub.add(conn)
    hub.subscribe("f1", conn, since_seq=0)
    begin = next(e for e in conn.sent if e.get("type") == "replay_begin")
    assert begin["gap"] is False
