"""`emit` sits on hot paths, so its first duty is to do no harm.

It is called at daemon start and at the end of every turn. A telemetry bug on
those paths must not surface as a failed turn or a daemon that will not bind, so
the tests here are mostly about what `emit` refuses to let escape: a store that
raises, a consent read that throws, a send that crashes. None of it may reach
the caller, and none of it may happen on the caller's thread.

The one behaviour that is not a refusal is the stop-reason mapping, and it is
pinned against the engine's real vocabulary rather than an invented one -- the
first version of the call site mapped `finalized`, a reason the engine never
produces, and dropped `stopped`, `done` and `failed`, which it does.
"""
from __future__ import annotations

import threading

import pytest

from openai4s.config import Config
from openai4s.store import get_store
from openai4s.telemetry import consent as consent_mod
from openai4s.telemetry import emit as emit_mod
from openai4s.telemetry.emit import emit, emit_session_start, turn_outcome


@pytest.fixture(autouse=True)
def _clean():
    emit_mod._reset_for_tests()
    yield
    emit_mod._reset_for_tests()


@pytest.fixture
def store(tmp_path):
    return get_store(Config(data_dir=tmp_path).db_path)


@pytest.fixture
def captured(monkeypatch):
    """Capture what would be sent, without a thread or a socket."""
    sent: list = []

    def fake_dispatch(store, install_id, batch):
        sent.append((install_id, batch))

    monkeypatch.setattr(emit_mod, "_dispatch", fake_dispatch)
    return sent


# --------------------------------------------------------------------------
# off by default
# --------------------------------------------------------------------------


def test_emit_without_consent_records_nothing(store, captured):
    emit("daemon_start", store=store, surface="web")
    assert captured == []


def test_emit_with_consent_records_the_event(store, captured):
    consent_mod.grant(store)
    emit("daemon_start", store=store, surface="web")

    assert len(captured) == 1
    install_id, batch = captured[0]
    assert len(install_id) == 32
    assert batch == [{"event": "daemon_start", "surface": "web"}]


def test_emit_drops_fields_outside_the_declaration(store, captured):
    consent_mod.grant(store)
    emit("turn_complete", store=store, surface="web", secret="/home/y/cohort.csv")

    _install, batch = captured[0]
    assert batch == [{"event": "turn_complete", "surface": "web"}]


def test_emit_drops_an_undeclared_event_name(store, captured):
    consent_mod.grant(store)
    emit("exfiltrate", store=store)
    assert captured == []


# --------------------------------------------------------------------------
# it never breaks the caller
# --------------------------------------------------------------------------


def test_a_store_that_raises_does_not_propagate(captured):
    class Exploding:
        def get_setting(self, *a, **k):
            raise RuntimeError("db is unhappy")

    emit("daemon_start", store=Exploding())  # must not raise
    assert captured == []


def test_a_dispatch_that_crashes_does_not_propagate(store, monkeypatch):
    consent_mod.grant(store)

    def boom(*a, **k):
        raise RuntimeError("collector on fire")

    monkeypatch.setattr(emit_mod, "_dispatch", boom)
    emit("daemon_start", store=store)  # must not raise


def test_the_send_happens_off_the_calling_thread(store, monkeypatch):
    """A slow collector must not add latency to a turn, so the actual send runs
    on a background thread, never inline."""
    consent_mod.grant(store)
    threads: list = []
    real_thread = threading.Thread

    def spy(*args, **kwargs):
        t = real_thread(*args, **kwargs)
        threads.append(t)
        return t

    monkeypatch.setattr(threading, "Thread", spy)

    sent_on: list = []
    from openai4s.telemetry import sender as sender_mod

    monkeypatch.setattr(
        sender_mod,
        "send",
        lambda store, payload: sent_on.append(threading.current_thread().name),
    )

    emit("daemon_start", store=store, surface="web")
    for t in threads:
        t.join(timeout=2)

    assert sent_on and sent_on[0] != threading.current_thread().name
    assert any(t.name == "openai4s-telemetry" for t in threads)


# --------------------------------------------------------------------------
# session_start fires once
# --------------------------------------------------------------------------


def test_session_start_fires_once_per_session(store, captured):
    consent_mod.grant(store)
    emit_session_start("frame-a", store=store, surface="web")
    emit_session_start("frame-a", store=store, surface="web")
    emit_session_start("frame-b", store=store, surface="web")

    events = [b[0]["event"] for _i, b in captured]
    assert events == ["session_start", "session_start"]  # a, then b, not a twice


# --------------------------------------------------------------------------
# the stop-reason mapping, against the engine's real vocabulary
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stop_reason,expected",
    [
        ("completed", "ok"),
        ("done", "ok"),
        ("stopped", "ok"),
        ("cancelled", "cancelled"),
        ("max_turns", "timeout"),
        ("failed", "error"),
    ],
)
def test_every_engine_stop_reason_maps_to_a_declared_outcome(stop_reason, expected):
    """These six strings are what openai4s/agent/engine.py and loop.py actually
    return. If the engine gains a seventh, this test is where it should be
    noticed."""
    from openai4s.telemetry.schema import RECORD

    assert turn_outcome(stop_reason) == expected
    assert expected in RECORD["outcome"].members


def test_an_unrecognised_stop_reason_is_error_not_ok():
    """The safe default. A stop this mapping has not seen is likelier a failure
    than a success worth reporting as one."""
    assert turn_outcome("some_new_reason") == "error"
    assert turn_outcome("") == "error"
    assert turn_outcome(None) == "error"
