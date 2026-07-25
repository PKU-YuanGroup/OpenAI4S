"""With no consent, nothing leaves the machine. This is the proof.

The claim is absolute, so the test has to be too: any attempt to open a
connection or resolve a name fails loudly rather than being absorbed by a mock
that returns a plausible response. A stub that quietly answers 200 would let a
telemetry send *succeed* in the suite and prove nothing.

The DNS lookup counts. Resolving log.openai4s.org tells a resolver that this
install exists and is running now, which is precisely the fact consent is asked
for. So the guard here refuses `getaddrinfo` as well as `connect`.

Scope, stated plainly: this proves the in-process claim. A subprocess -- a
kernel cell, an `ssh` from the compute manager -- is outside any in-process
guard by construction, which is what the sandbox is for, and no telemetry code
runs there (asserted below).
"""
from __future__ import annotations

import socket

import pytest

from openai4s.config import Config
from openai4s.store import get_store
from openai4s.telemetry import consent as consent_mod
from openai4s.telemetry import sender as sender_mod
from openai4s.telemetry import wire


class EgressAttempted(AssertionError):
    """Raised the moment anything tries to leave the machine."""


@pytest.fixture
def store(tmp_path):
    return get_store(Config(data_dir=tmp_path).db_path)


@pytest.fixture
def no_egress(monkeypatch):
    """Make every outbound primitive a loud failure, DNS included."""
    attempts: list[str] = []

    def refuse(what):
        def guard(*args, **kwargs):
            attempts.append(what)
            raise EgressAttempted(f"telemetry attempted {what}: {args!r}")

        return guard

    monkeypatch.setattr(socket, "getaddrinfo", refuse("getaddrinfo"))
    monkeypatch.setattr(socket, "create_connection", refuse("create_connection"))
    monkeypatch.setattr(socket.socket, "connect", refuse("connect"))
    monkeypatch.setattr(socket.socket, "connect_ex", refuse("connect_ex"))
    return attempts


def _payload(install_id: str = "0" * 32):
    return wire.seal(install_id, [{"event": "daemon_start", "outcome": "ok"}])


# --------------------------------------------------------------------------
# the claim
# --------------------------------------------------------------------------


def test_sending_without_consent_does_not_touch_the_network(store, no_egress):
    """The headline. Not "returns False" -- never reaches a socket at all."""
    assert sender_mod.send(store, _payload()) is False
    assert no_egress == []


def test_a_revoked_install_stops_sending(store, no_egress):
    consent_mod.grant(store)
    consent_mod.revoke(store)

    assert sender_mod.send(store, _payload()) is False
    assert no_egress == []


def test_an_environment_veto_stops_sending_even_with_consent_recorded(
    store, no_egress, monkeypatch
):
    consent_mod.grant(store)
    monkeypatch.setenv(consent_mod.ENV_VAR, "0")

    assert sender_mod.send(store, _payload()) is False
    assert no_egress == []


def test_the_consent_check_happens_before_any_name_is_resolved(store, no_egress):
    """A DNS query is itself a signal, so the order of the checks is the
    property, not an optimisation."""
    for _ in range(5):
        sender_mod.send(store, _payload())
    assert no_egress == [], "no lookup may happen before consent is confirmed"


def test_importing_the_package_sends_nothing(no_egress):
    """Import-time side effects are the classic way "off by default" is not."""
    import importlib

    for name in (
        "openai4s.telemetry",
        "openai4s.telemetry.schema",
        "openai4s.telemetry.consent",
        "openai4s.telemetry.wire",
        "openai4s.telemetry.sender",
    ):
        importlib.reload(importlib.import_module(name))
    assert no_egress == []


def test_building_the_daemon_starts_no_telemetry_thread(tmp_path, no_egress):
    """Consent is read per send, so nothing may be started at boot."""
    import threading

    before = {t.name for t in threading.enumerate()}
    from openai4s.server import gateway as gateway_mod

    config = Config(data_dir=tmp_path)
    gateway_mod.SessionRunner(config, _SilentHub(), start_idle_sweeper=False)
    after = {t.name for t in threading.enumerate()}

    assert not any("telemetry" in name.lower() for name in after - before)
    assert no_egress == []


class _SilentHub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None

    def has_subscriber(self, root_frame_id):
        return False

    def drop_frame(self, root_frame_id):
        return None


# --------------------------------------------------------------------------
# what the sender refuses even once consent exists
# --------------------------------------------------------------------------


def test_a_payload_the_sender_did_not_get_from_seal_is_refused(store, no_egress):
    """The sealed type is the reason `send` cannot be handed raw bytes."""
    consent_mod.grant(store)

    class Impostor:
        body = b'{"anything": "at all"}'
        record_count = 1

    assert sender_mod.send(store, Impostor()) is False
    assert no_egress == []


def test_a_sealed_payload_cannot_be_built_outside_wire():
    with pytest.raises(TypeError):
        wire.SealedPayload(object(), b"{}", 1)


def test_a_plain_http_endpoint_is_refused(monkeypatch):
    """A downgrade would put the payload and the install id in clear text."""
    monkeypatch.setenv(sender_mod.ENDPOINT_VAR, "http://log.openai4s.org/v1/events")
    assert sender_mod.endpoint() is None


def test_an_endpoint_carrying_credentials_is_refused(monkeypatch):
    monkeypatch.setenv(sender_mod.ENDPOINT_VAR, "https://user:pw@example.com/v1")
    assert sender_mod.endpoint() is None


def test_the_default_endpoint_is_the_one_that_was_agreed(monkeypatch):
    monkeypatch.delenv(sender_mod.ENDPOINT_VAR, raising=False)
    assert sender_mod.endpoint() == sender_mod.DEFAULT_ENDPOINT
    assert sender_mod.DEFAULT_ENDPOINT.startswith("https://log.openai4s.org/")


def test_an_oversized_payload_is_refused_before_consent_is_even_checked(
    store, no_egress
):
    consent_mod.grant(store)
    payload = _payload()
    payload.body = b"x" * (sender_mod.MAX_BODY_BYTES + 1)

    assert sender_mod.send(store, payload) is False
    assert no_egress == []


def test_the_no_redirect_handler_refuses_every_redirect():
    handler = sender_mod._NoRedirects()
    for code in (301, 302, 303, 307, 308):
        assert (
            handler.redirect_request(None, None, code, "x", {}, "https://elsewhere/")
            is None
        )


def test_the_sender_actually_installs_that_handler(store, monkeypatch):
    """Testing the class alone proves nothing about the wiring: a `send` that
    built a default opener would still pass, and a redirect is a third party
    choosing where research telemetry goes. So observe the construction."""
    import urllib.request

    granted = consent_mod.grant(store)
    assert granted is not None
    handlers: list[object] = []

    class _Stub:
        def open(self, request, timeout=None):
            raise AssertionError("must not be reached in this test")

    def fake_build_opener(*args):
        handlers.extend(args)
        return _Stub()

    monkeypatch.setattr(urllib.request, "build_opener", fake_build_opener)
    # Sealed under the identity that is actually authorised, or the sender
    # refuses before it ever builds an opener.
    sender_mod.send(store, _payload(granted.install_id))

    assert any(
        h is sender_mod._NoRedirects for h in handlers
    ), "send() must build its opener with the no-redirect handler"


# --------------------------------------------------------------------------
# nothing is buffered across a revoke, and nothing flushes at exit
# --------------------------------------------------------------------------


def test_there_is_no_exit_or_crash_hook_anywhere_in_the_package():
    """A flush at exit would send events recorded before consent, and a crash
    hook would send them at the least examinable moment."""
    import ast
    import pathlib

    package = pathlib.Path(sender_mod.__file__).parent
    forbidden = {"atexit", "faulthandler", "excepthook", "__del__", "finalize"}
    for path in package.glob("*.py"):
        source = path.read_text("utf-8")
        for name in forbidden:
            assert name not in source, f"{path.name} references {name}"
        ast.parse(source)


def test_seal_refuses_to_produce_an_empty_report(store):
    """Sending "I have nothing to say" is still a packet, and still tells a
    listener this install is running right now."""
    assert wire.seal("0" * 32, []) is None
    assert wire.seal("0" * 32, [{"not_a_declared_field": "x"}]) is None


def test_seal_refuses_without_a_well_formed_identity():
    assert wire.seal("not-32-hex", [{"event": "daemon_start"}]) is None


# --------------------------------------------------------------------------
# a revoked identity does not come back under the next permission
# --------------------------------------------------------------------------


def test_a_payload_sealed_under_a_revoked_identity_is_not_sent(store, no_egress):
    """The regression, and the sharpest privacy claim in this subsystem.

    Sealing happens on the caller's thread; sending happens later on another.
    Between those two moments the user can revoke — which destroys the id — and
    grant again, which mints a *different* one. The sender only asked whether
    *some* consent existed, so the payload stamped with the old identity went
    out under the new permission, linking two participation periods that the
    id-inside-the-consent-record design exists specifically to keep unlinkable.

    The network guard is the assertion that matters: not merely "returns
    False", but that the old id never reaches a socket.
    """
    first = consent_mod.grant(store)
    assert first is not None
    stale = wire.seal(first.install_id, [{"event": "daemon_start", "outcome": "ok"}])

    consent_mod.revoke(store)
    second = consent_mod.grant(store)
    assert second is not None and second.install_id != first.install_id

    assert sender_mod.send(store, stale) is False
    assert no_egress == []


def test_a_payload_sealed_under_the_current_identity_is_accepted(store, monkeypatch):
    """The check must not become a blanket refusal: the ordinary path still
    sends. Stubbed at the opener so nothing actually leaves."""
    granted = consent_mod.grant(store)
    assert granted is not None
    payload = wire.seal(
        granted.install_id, [{"event": "daemon_start", "outcome": "ok"}]
    )

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    class _Opener:
        def open(self, _request, timeout=None):
            return _Response()

    monkeypatch.setattr(
        sender_mod.urllib.request, "build_opener", lambda *_h: _Opener()
    )
    assert sender_mod.send(store, payload) is True


def test_a_payload_with_no_identity_is_refused(store, no_egress):
    """A payload that cannot say who it belongs to cannot be matched against
    the current consent, so it is not sendable."""
    consent_mod.grant(store)
    payload = wire.seal("f" * 32, [{"event": "daemon_start", "outcome": "ok"}])
    payload.install_id = ""

    assert sender_mod.send(store, payload) is False
    assert no_egress == []


def test_the_sealed_payload_records_the_identity_it_was_sealed_under(store):
    payload = wire.seal("a" * 32, [{"event": "daemon_start", "outcome": "ok"}])
    assert payload is not None and payload.install_id == "a" * 32


def test_no_payload_ever_sends_under_an_identity_that_is_not_current(store, no_egress):
    """The invariant, under threads rather than in sequence.

    A consent toggle and an in-flight send are genuinely concurrent in the
    daemon: emit seals on the caller's thread and dispatches on another, while
    the Customize route can grant or revoke at any moment. Whatever the
    interleaving, a payload stamped with an identity that is not the one
    currently authorised must never leave — and the network guard, not a return
    value, is what proves it.
    """
    import threading

    granted = consent_mod.grant(store)
    assert granted is not None
    consent_mod.revoke(store)
    # Every payload here was sealed under an identity that has *already* been
    # destroyed, so any egress at all is a violation. Seeding the list with the
    # currently-authorised id instead would make a legitimate send indis-
    # tinguishable from the leak, and the assertion would be measuring the
    # scheduler rather than the invariant.
    stale = [_payload(granted.install_id)]
    stop = threading.Event()
    sent: list[bool] = []
    lock = threading.Lock()

    def churn():
        while not stop.is_set():
            fresh = consent_mod.grant(store)
            if fresh is None:
                continue
            consent_mod.revoke(store)
            with lock:
                stale.append(_payload(fresh.install_id))

    def send_loop():
        while not stop.is_set():
            with lock:
                candidates = list(stale)
            for payload in candidates:
                sent.append(sender_mod.send(store, payload))

    workers = [threading.Thread(target=churn), threading.Thread(target=send_loop)]
    for worker in workers:
        worker.start()
    threading.Event().wait(0.4)
    stop.set()
    for worker in workers:
        worker.join(timeout=5)

    # Every send was refused before it could resolve a name, because the
    # endpoint is unreachable in this fixture — the point is that nothing got
    # as far as the network under a stale identity.
    assert no_egress == []
