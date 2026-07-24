"""The receiving end, exercised on loopback.

The collector is a reference implementation -- it is here so the wire format has
a second reader, not so this repo runs a public server. The tests bind
127.0.0.1:0 (an ephemeral loopback port, never a public one) and drive it with
the same `wire.seal` the client uses.

The property worth proving is the one a relay would not have: the server does
not trust the sender. A record carrying a value outside its declared domain --
the kind a well-behaved client never emits, but a future or hostile one might --
is dropped on arrival, so "counts and enumerations only" holds even against a
client that ignores it. Everything else here is the boring, necessary refusal:
body caps before the read, a Host allowlist, a rate limit, and storing counts
rather than a per-install log.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from openai4s.telemetry import wire
from openai4s.telemetry.collector import (
    MAX_BODY_BYTES,
    Aggregate,
    CollectorServer,
    validate_envelope,
)

_HOST = "log.openai4s.org"


@pytest.fixture
def server():
    """A collector on an ephemeral loopback port. Never a public bind."""
    srv = CollectorServer(("127.0.0.1", 0), allowed_hosts=frozenset({_HOST}))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv, srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()


def _post(port, body, *, host=_HOST, path="/v1/events"):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        method="POST",
        headers={"Host": host, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _sealed(records):
    payload = wire.seal("a" * 32, records)
    return payload.body


# --------------------------------------------------------------------------
# the happy path, on a real socket
# --------------------------------------------------------------------------


def test_a_sealed_payload_is_accepted_and_counted(server):
    srv, port = server
    status, _ = _post(
        port, _sealed([{"event": "turn_complete", "outcome": "ok", "count": 3}])
    )

    assert status == 204
    snap = srv.aggregate.snapshot()
    assert snap["installs"] == 1
    assert snap["events"]["turn_complete/ok"] == 3


def test_the_same_install_is_counted_once(server):
    srv, port = server
    for _ in range(3):
        _post(port, _sealed([{"event": "daemon_start", "outcome": "ok"}]))
    assert srv.aggregate.snapshot()["installs"] == 1


# --------------------------------------------------------------------------
# the server does not trust the sender
# --------------------------------------------------------------------------


def test_a_field_outside_its_domain_is_dropped_on_arrival():
    """The guarantee that holds even against a client that ignores it. A hand
    -built envelope smuggling a path into error_type must not survive."""
    envelope = json.dumps(
        {
            "schema": 1,
            "install_id": "a" * 32,
            "app_version": "0.1.0",
            "os": "linux",
            "arch": "x86_64",
            "python": "3.12",
            "events": [
                {
                    "event": "turn_complete",
                    "outcome": "error",
                    "error_type": "/home/y/unpublished/cohort.csv",
                }
            ],
        }
    ).encode()

    clean = validate_envelope(envelope)
    assert clean is not None
    assert "error_type" not in clean["events"][0]
    assert clean["events"][0] == {"event": "turn_complete", "outcome": "error"}


def test_an_envelope_with_an_undeclared_event_name_is_rejected():
    envelope = json.dumps(
        {"schema": 1, "install_id": "a" * 32, "events": [{"event": "exfiltrate"}]}
    ).encode()
    assert validate_envelope(envelope) is None


def test_a_smuggled_envelope_field_does_not_survive():
    envelope = json.dumps(
        {
            "schema": 1,
            "install_id": "a" * 32,
            "hostname": "lab-workstation-3",
            "events": [{"event": "daemon_start"}],
        }
    ).encode()
    clean = validate_envelope(envelope)
    assert clean is not None and "hostname" not in clean


def test_an_envelope_without_an_install_id_is_rejected():
    envelope = json.dumps({"schema": 1, "events": [{"event": "daemon_start"}]}).encode()
    assert validate_envelope(envelope) is None


# --------------------------------------------------------------------------
# the boring, necessary refusals
# --------------------------------------------------------------------------


def test_a_wrong_host_is_refused(server):
    """A Host allowlist, not Origin==Host: the relay's DNS-rebinding history is
    exactly what a naive collector would inherit."""
    _srv, port = server
    status, _ = _post(port, _sealed([{"event": "daemon_start"}]), host="evil.example")
    assert status == 421


def test_a_get_is_not_a_way_in(server):
    _srv, port = server
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/events", headers={"Host": _HOST}
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            code = resp.status
    except urllib.error.HTTPError as exc:
        code = exc.code
    assert code == 404


def test_an_unknown_path_is_refused(server):
    _srv, port = server
    status, _ = _post(port, _sealed([{"event": "daemon_start"}]), path="/admin")
    assert status == 404


def test_an_oversized_body_is_refused_by_the_declared_length(server):
    """The cap is on Content-Length, checked before the body is read: reading a
    huge body only to reject it is the abuse the cap exists to stop."""
    _srv, port = server
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/events",
        data=b"x",
        method="POST",
        headers={"Host": _HOST, "Content-Length": str(MAX_BODY_BYTES + 1)},
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            code = resp.status
    except urllib.error.HTTPError as exc:
        code = exc.code
    assert code == 413


def test_a_malformed_body_is_rejected_and_counted_as_such(server):
    srv, port = server
    status, _ = _post(port, b"not json at all")
    assert status == 400
    assert srv.aggregate.snapshot()["rejected"] == 1


# --------------------------------------------------------------------------
# it stores counts, not a surveillance log
# --------------------------------------------------------------------------


def test_the_aggregate_keeps_no_per_event_timestamp_or_id_pairing():
    """A file of "install X did Y at time T" is the surveillance the design
    exists to avoid. The snapshot is counts keyed by event, and a single set of
    ids only so the total is not double-counted."""
    agg = Aggregate()
    agg.record("a" * 32, [{"event": "turn_complete", "outcome": "ok", "count": 1}])
    agg.record("b" * 32, [{"event": "turn_complete", "outcome": "ok", "count": 1}])

    snap = agg.snapshot()
    assert snap == {
        "installs": 2,
        "events": {"turn_complete/ok": 2},
        "rejected": 0,
    }
    # No structure anywhere maps an id to what it did.
    assert not any(isinstance(v, dict) and "install" in str(v) for v in snap.values())


def test_the_install_set_is_capped():
    """An unbounded id set turns an anonymous counter into a census."""
    from openai4s.telemetry import collector

    agg = Aggregate()
    original = collector.MAX_INSTALLS
    collector.MAX_INSTALLS = 3
    try:
        for i in range(10):
            agg.record(f"{i:032x}", [{"event": "daemon_start"}])
        assert len(agg.installs) == 3
    finally:
        collector.MAX_INSTALLS = original
