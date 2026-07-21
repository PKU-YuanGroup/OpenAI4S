"""The documented contract must cover the surface the code actually serves.

The proposal requires that every external route and event be covered by a
contract inventory. The load-bearing word is *checkable*: a list maintained by
hand is wrong the first time somebody adds a route in a hurry, and its being
wrong is invisible, which is precisely the failure a contract exists to
prevent. So the inventory is derived from the gateway source and compared
against the document.

This found two real gaps on its first run — `branch_projection_restored` and
`branch_activation_state` were emitted to clients and handled by the frontend
but appeared nowhere in `docs/webapp-api.md`. That is the drift this test
exists to stop.

Scope, stated plainly: this answers "which paths and events exist", not "what
shape do they return". Response schemas are the next layer of §4.6 and are not
inferable from a routing chain.
"""
from pathlib import Path

import pytest

from openai4s.server.contract import (
    http_routes,
    inventory,
    route_families,
    route_family,
    websocket_inbound,
    websocket_outbound,
)

_DOC = Path(__file__).resolve().parents[1] / "docs" / "webapp-api.md"


@pytest.fixture(scope="module")
def doc() -> str:
    return _DOC.read_text("utf-8")


# --------------------------------------------------------------------------
# the extractor is actually reading the surface
# --------------------------------------------------------------------------


def test_the_inventory_is_not_silently_empty():
    """The extractor parses source. If the routing style ever changes enough to
    break it, it must fail loudly here rather than quietly report full coverage
    of nothing."""
    assert len(http_routes()) > 80
    assert len(websocket_outbound()) > 10
    assert websocket_inbound() >= {"view_session", "ping"}


def test_known_routes_are_found():
    routes = http_routes()
    for expected in ("/projects", "/frames", "/config/llm", "/connectors"):
        assert expected in routes, expected


def test_validator_patterns_are_not_mistaken_for_routes():
    """The gateway also uses re.fullmatch to validate hashes and identifiers.
    Counting those as surface would inflate the inventory and make the coverage
    assertion meaningless."""
    for route in http_routes():
        assert route.startswith("/"), route


def test_route_family_reduces_a_parameterised_path():
    assert route_family("/frames/([^/]+)/kernel") == "frames"
    assert route_family("/projects") == "projects"
    assert route_family("/") == ""


# --------------------------------------------------------------------------
# coverage
# --------------------------------------------------------------------------


def test_every_route_family_is_documented(doc):
    """Families rather than exact paths: a document forced to enumerate every
    parameterised variant would be unmaintainable, and so would stop being
    maintained."""
    missing = sorted(f for f in route_families() if f not in doc)
    assert not missing, f"route families absent from docs/webapp-api.md: {missing}"


def test_every_websocket_event_the_server_emits_is_documented(doc):
    """The gap this test was written to catch: an event a client receives and
    acts on, that the contract never mentions."""
    missing = sorted(e for e in websocket_outbound() if e not in doc)
    assert not missing, f"WS events absent from docs/webapp-api.md: {missing}"


def test_every_websocket_message_a_client_may_send_is_documented(doc):
    missing = sorted(e for e in websocket_inbound() if e not in doc)
    assert not missing, f"WS inbound absent from docs/webapp-api.md: {missing}"


def test_the_document_records_the_versioned_root(doc):
    assert "/api/v1" in doc
    assert "no legacy alias" in doc or "legacy alias" in doc


def test_the_resume_cursor_is_documented(doc):
    """A client cannot implement resume from the code; it has to be written
    down or the contract is only nominally versioned."""
    for term in ("since_seq", "from_seq", "gap"):
        assert term in doc, term


def test_inventory_is_serialisable():
    inv = inventory()
    assert set(inv) == {"http_routes", "ws_inbound", "ws_outbound"}
    assert inv["http_routes"] == sorted(inv["http_routes"])


# --------------------------------------------------------------------------
# the inventory has to see the whole surface, and only the surface
# --------------------------------------------------------------------------


def test_events_emitted_outside_the_gateway_are_in_the_inventory():
    """The extractor read `gateway.py` alone while events are emitted from the
    focused services too, so fifteen live event types were invisible to it —
    and therefore exempt from the documentation gate above."""
    outbound = websocket_outbound()
    for event in (
        "notebook_cell_start",  # server/cell_run.py
        "notebook_cell_draft",  # server/agent_run.py
        "recovery_state",  # server/recovery_execution.py
        "recovery_log",  # server/recovery_control.py
        "branch_activated",  # server/session_domain.py
        "checkpoint_created",  # server/session_branching.py
        "execution_state",  # server/execution_coordinator.py
        "plan_ready",  # server/plans.py
        "delegation_child_event",  # agent/delegation.py
    ):
        assert event in outbound, f"{event} is emitted but not inventoried"


def test_the_inventory_does_not_invent_events():
    """As wrong as omitting one. `{"type": ...}` is a common shape — JSON
    schema fragments, ledger states, result payloads — and listing those as
    protocol would make the contract document a description of nothing."""
    outbound = websocket_outbound()
    for not_an_event in ("string", "number", "object", "array", "proposed"):
        assert not_an_event not in outbound, (
            f"{not_an_event!r} is a value in some other vocabulary, not a "
            f"WebSocket event"
        )
    # A sidecar warning rides inside a result payload, never over the socket.
    assert "skill_sidecar_recovery_capture_failed" not in outbound


def test_inbound_membership_dispatch_is_inventoried():
    """`t in {"cancel_execution", "cancel"}` is as much a dispatch as
    `t == "view_session"`; matching only equality hid two real client
    messages."""
    inbound = websocket_inbound()
    assert {"view_session", "unview_session", "ping"} <= inbound
    assert {"cancel_execution", "cancel"} <= inbound


def test_inbound_scanning_stops_at_the_handler():
    """Bounded at both ends: an unrelated truthiness check far below the
    socket handler reuses the same loop variable name, and scanning to
    end-of-file put its values in the inventory as client messages."""
    inbound = websocket_inbound()
    for stray in ("false", "no", "off", "0"):
        assert stray not in inbound
