"""Revocation is a boundary, not a request to stop soon.

Two defects, both invisible to a functional test and both about *when*:

  * ``sender.send`` read consent, and then — on another thread, at another
    moment — opened a socket. A revoke landing in that window returned to the
    caller while a payload sealed under the destroyed identity was still on its
    way to the opener. "With no consent, not a single packet leaves the
    machine" was true only if nobody was mid-send when consent went away.
  * ``emit`` started a fresh daemon thread per flush. ``_MAX_BUFFER`` bounded
    the *records* and nothing bounded the threads, sockets or payloads in
    flight, so a stalled collector turned an event rate into a thread rate.

The first test forces the interleaving with a barrier rather than hoping for
it; the second holds a collector open and counts what the daemon actually
spends on it.
"""
from __future__ import annotations

import threading
import time

import pytest

from openai4s.config import Config
from openai4s.store import get_store
from openai4s.telemetry import consent as consent_mod
from openai4s.telemetry import emit as emit_mod
from openai4s.telemetry import gate as gate_mod
from openai4s.telemetry import sender as sender_mod
from openai4s.telemetry import wire


@pytest.fixture(autouse=True)
def _clean():
    emit_mod._reset_for_tests()
    gate_mod._reset_for_tests()
    yield
    emit_mod._reset_for_tests()
    gate_mod._reset_for_tests()


@pytest.fixture
def store(tmp_path):
    return get_store(Config(data_dir=tmp_path).db_path)


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _RecordingOpener:
    """Stands in for the network, and records when it was reached."""

    def __init__(self):
        self.opened_at: list[float] = []
        self.hold = threading.Event()
        self.hold.set()

    def open(self, request, timeout=None):
        self.opened_at.append(time.monotonic())
        self.hold.wait(timeout=10)
        return _Response()


# --------------------------------------------------------------------------
# the revoke boundary
# --------------------------------------------------------------------------


def test_no_request_may_begin_after_revoke_has_returned(store, monkeypatch):
    """The barrier reproduction.

    A sender is stepped to the exact point review described: consent has been
    read and found present, and the socket has not been opened yet. Revoke is
    then called from another thread. Whatever the ordering, the invariant is
    one comparison: nothing reached the opener after ``revoke`` returned.
    """
    granted = consent_mod.grant(store)
    payload = wire.seal(granted.install_id, [{"event": "daemon_start"}])
    assert payload is not None

    opener = _RecordingOpener()
    monkeypatch.setattr(sender_mod.urllib.request, "build_opener", lambda *a: opener)

    read_reached = threading.Event()
    let_read_finish = threading.Event()
    real_read = consent_mod.read

    def stepped_read(target):
        result = real_read(target)
        read_reached.set()
        let_read_finish.wait(timeout=10)
        return result

    monkeypatch.setattr(sender_mod.consent_mod, "read", stepped_read)

    sent: list[bool] = []
    sender = threading.Thread(
        target=lambda: sent.append(sender_mod.send(store, payload))
    )
    sender.start()
    assert read_reached.wait(timeout=10), "the sender never reached the consent read"

    revoked_at: list[float] = []

    def do_revoke():
        consent_mod.revoke(store)
        revoked_at.append(time.monotonic())

    revoker = threading.Thread(target=do_revoke)
    revoker.start()
    # Give the revoke every chance to slip past the in-flight send.
    time.sleep(0.2)
    let_read_finish.set()
    sender.join(timeout=15)
    revoker.join(timeout=15)

    assert revoked_at, "revoke never returned"
    for opened in opener.opened_at:
        assert opened < revoked_at[0], (
            "a payload sealed under the revoked identity began its request "
            "after revoke had already returned to the caller"
        )


def test_a_payload_queued_before_a_revoke_is_never_transmitted(store, monkeypatch):
    """The other half: what is already waiting must not go out either."""
    granted = consent_mod.grant(store)
    payload = wire.seal(granted.install_id, [{"event": "daemon_start"}])
    opener = _RecordingOpener()
    monkeypatch.setattr(sender_mod.urllib.request, "build_opener", lambda *a: opener)

    gate_mod.pause_worker()
    assert gate_mod.submit(store, payload) is True
    assert gate_mod.pending() == 1

    consent_mod.revoke(store)

    assert gate_mod.pending() == 0, "revoke must drop what is still waiting"
    gate_mod.resume_worker()
    assert gate_mod.wait_idle(timeout=5)
    assert opener.opened_at == []


def test_a_re_grant_does_not_resurrect_the_old_identity(store, monkeypatch):
    """Two participation periods must stay unlinkable.

    A payload sealed under the first id, sent after a revoke and a fresh grant,
    would carry the old identity under the new permission.
    """
    first = consent_mod.grant(store)
    payload = wire.seal(first.install_id, [{"event": "daemon_start"}])
    opener = _RecordingOpener()
    monkeypatch.setattr(sender_mod.urllib.request, "build_opener", lambda *a: opener)

    consent_mod.revoke(store)
    second = consent_mod.grant(store)
    assert second.install_id != first.install_id

    assert sender_mod.send(store, payload) is False
    assert opener.opened_at == []


# --------------------------------------------------------------------------
# bounded delivery
# --------------------------------------------------------------------------


def test_a_stalled_collector_does_not_turn_events_into_threads(store, monkeypatch):
    """The resource-spike reproduction: many events, one slow endpoint."""
    consent_mod.grant(store)

    opener = _RecordingOpener()
    opener.hold.clear()  # every send blocks until released
    monkeypatch.setattr(sender_mod.urllib.request, "build_opener", lambda *a: opener)

    before = threading.active_count()
    for index in range(200):
        emit_mod.emit("turn_complete", store=store, outcome="ok", turns=index)

    peak = threading.active_count() - before
    assert peak <= 2, (
        f"{peak} extra threads for 200 events — delivery must be a fixed "
        f"worker, not a thread per flush"
    )
    assert (
        gate_mod.pending() <= gate_mod.MAX_PENDING
    ), "the queue must be bounded, and its bound must be the declared one"
    assert (
        gate_mod.dropped() > 0
    ), "overflow must be recorded rather than silently absorbed"

    opener.hold.set()
    assert gate_mod.wait_idle(timeout=10)


def test_overflow_drops_the_newest_and_keeps_the_queue_at_its_bound(store, monkeypatch):
    """The declared overflow behaviour, stated as a test rather than a hope."""
    granted = consent_mod.grant(store)
    payload = wire.seal(granted.install_id, [{"event": "daemon_start"}])

    gate_mod.pause_worker()
    accepted = [
        gate_mod.submit(store, payload) for _ in range(gate_mod.MAX_PENDING + 5)
    ]

    assert accepted[: gate_mod.MAX_PENDING] == [True] * gate_mod.MAX_PENDING
    assert accepted[gate_mod.MAX_PENDING :] == [False] * 5
    assert gate_mod.pending() == gate_mod.MAX_PENDING
    assert gate_mod.dropped() == 5
    gate_mod.resume_worker()


def test_emit_still_does_not_block_the_caller(store, monkeypatch):
    """The property the per-event thread was there for must survive."""
    consent_mod.grant(store)
    opener = _RecordingOpener()
    opener.hold.clear()
    monkeypatch.setattr(sender_mod.urllib.request, "build_opener", lambda *a: opener)

    started = time.monotonic()
    emit_mod.emit("daemon_start", store=store, surface="web")
    assert time.monotonic() - started < 1.0

    opener.hold.set()
    gate_mod.wait_idle(timeout=10)
