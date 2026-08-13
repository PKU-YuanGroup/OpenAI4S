"""The documented contract must cover the surface the code actually serves.

The proposal requires that every external route and event be covered by a
contract inventory. The load-bearing word is *checkable*: a list maintained by
hand is wrong the first time somebody adds a route in a hurry, and its being
wrong is invisible, which is precisely the failure a contract exists to
prevent. So the inventory is derived from the code and compared against the
document — by parsing the routing chain out of the source, and, for a module
that has migrated to declarative `RouteSpec` objects, by reading its live
`ROUTES` table as well. Both readings run over every route module; neither
replaces the other.

This found two real gaps on its first run — `branch_projection_restored` and
`branch_activation_state` were emitted to clients and handled by the frontend
but appeared nowhere in `docs/webapp-api.md`. That is the drift this test
exists to stop.

Scope, stated plainly: this answers "which paths and events exist", not "what
shape do they return". Response shapes are the next layer and live in
`docs/response-schemas.json`, captured from real responses.

The inventory now scans the route modules being carved out of `Handler._api`
as well as gateway.py. Reading gateway.py alone made the decomposition
self-defeating: moving the kernel group dropped 12 routes out of the inventory
and orphaned 11 frozen response shapes, and the obvious repair -- regenerate
the artifact until it is green -- re-files those shapes under the catch-all
`/frames/([^/]+)(?:/.*)?` and destroys the per-route contract.

A migrated module is scanned *and* read, for the same reason. Trusting its
`ROUTES` table instead of its source reopened the identical hole one level
down: a leftover `re.fullmatch` branch beside the declarations, or a table
named anything but `ROUTES`, silently left the inventory.
"""

import re
from pathlib import Path

import pytest

from openai4s.server import contract
from openai4s.server.contract import (
    _NON_GATEWAY_ROUTE_MODULES,
    _SERVER_PKG,
    _route_modules,
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


def test_kernel_routes_declare_state_mutation_semantics():
    from openai4s.server import kernel_routes

    expected = {
        "kernel.execution": False,
        "kernel.execute": True,
        "kernel.restart": True,
        "kernel.stop": True,
        "kernel.interrupt": True,
        "kernel.start": True,
        "kernel.variables": False,
        "kernel.status": False,
        "session.status": False,
        "kernel.install": True,
        "kernel.environments": False,
        "kernel.env": True,
    }
    actual = {spec.name: spec.mutates for spec in kernel_routes.ROUTES}
    assert actual == expected


@pytest.mark.parametrize(
    "kwargs, exc",
    [
        ({"name": ""}, ValueError),
        ({"name": "   "}, ValueError),
        # Spelling is not membership: `str.isalpha()` is Unicode-wide and all
        # of these are `.upper()`-stable, so an "uppercase alphabetic" test
        # accepted every one. A route with a typo'd verb never matches a real
        # request, yet its pattern still enters the inventory and earns the
        # dispatcher's 404 from every probe verb -- published as covered.
        ({"method": "get"}, ValueError),
        ({"method": "GTE"}, ValueError),
        ({"method": "BANANA"}, ValueError),
        ({"method": "ＧＥＴ"}, ValueError),
        ({"method": ""}, ValueError),
        ({"pattern": "no-leading-slash"}, ValueError),
        ({"pattern": "/unclosed(["}, ValueError),
        ({"mutates": 1}, TypeError),
        ({"mutates": "yes"}, TypeError),
    ],
)
def test_route_spec_rejects_a_declaration_it_cannot_serve(kwargs, exc):
    """Every arm of `__post_init__`, which had no test at all.

    Deleting the whole validation body left all 58 tests in this file and
    `test_gateway_kernel_routes.py` green, so the constructor contract the
    registry rests on could be removed without turning anything red.
    """
    fields = {
        "name": "probe.one",
        "method": "GET",
        "pattern": r"/probe/([^/]+)",
        "mutates": False,
    }
    fields.update(kwargs)
    with pytest.raises(exc):
        contract.RouteSpec(**fields)


def test_a_valid_route_spec_keeps_its_compiled_pattern():
    """`__post_init__` compiled the pattern to validate it and threw it away,
    so `match` re-resolved the string through `re`'s 512-entry module cache on
    every request. Holding it is also what makes the validation compile pay."""
    spec = contract.RouteSpec("probe.one", "GET", r"/probe/([^/]+)", mutates=False)

    assert spec.match("GET", "/probe/abc").group(1) == "abc"
    assert spec.match("POST", "/probe/abc") is None
    assert spec._compiled.pattern == r"/probe/([^/]+)"


def test_route_specs_reject_a_route_an_earlier_one_already_answers():
    """Byte-equal `(method, pattern)` pairs are not the ambiguity that matters.

    Two *different* regexes matching the same path validated cleanly, and the
    later one was unreachable at runtime while still entering `http_routes()`
    -- `response_capture` then attributed a response to it, so a route that can
    only ever 404 counted toward the published coverage.
    """
    shadowed = (
        contract.RouteSpec("kernel.status", "GET", r"/frames/([^/]+)/kernel", False),
        contract.RouteSpec("kernel.shadow", "GET", r"/frames/(.+)/kernel", False),
    )
    with pytest.raises(ValueError, match="shadowed by"):
        contract._validate_route_specs(shadowed)

    # The real table must stay clean, or the check is unusable.
    from openai4s.server import kernel_routes

    assert contract._validate_route_specs(kernel_routes.ROUTES) == kernel_routes.ROUTES


def test_a_route_module_validates_its_table_when_it_is_imported():
    """The validator was reachable only from `declared_http_routes()`, which
    `gateway.py` never calls -- so a duplicated registry imported cleanly and
    the daemon served it, and deleting the call left every test green."""
    from openai4s.server import kernel_routes

    source = (contract._SERVER_PKG / "kernel_routes.py").read_text("utf-8")
    assert "contract.validate_routes(" in source, (
        "ROUTES must be validated where it is declared, not only when some "
        "contract test happens to build the inventory"
    )
    with pytest.raises(ValueError, match="duplicate route name"):
        contract.validate_routes(kernel_routes.ROUTES + (kernel_routes.ROUTES[0],))


def test_route_specs_reject_ambiguous_protocol_identities():
    first = contract.RouteSpec("probe.one", "GET", r"/probe/one", mutates=False)
    duplicate_name = contract.RouteSpec(
        "probe.one", "GET", r"/probe/two", mutates=False
    )
    duplicate_http = contract.RouteSpec(
        "probe.two", "GET", r"/probe/one", mutates=False
    )

    with pytest.raises(ValueError, match="duplicate route name"):
        contract._validate_route_specs((first, duplicate_name))
    with pytest.raises(ValueError, match="duplicate HTTP route declaration"):
        contract._validate_route_specs((first, duplicate_http))


def test_route_spec_source_fragment_remains_inventoried():
    source = 'RouteSpec("probe", "GET", r"/probe/([^/]+)", mutates=False)\n'
    assert contract.http_routes(source) == {r"/probe/([^/]+)"}


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


# --------------------------------------------------------------------------
# the inventory has to follow routes out of gateway.py
# --------------------------------------------------------------------------


def test_a_route_defined_in_a_route_module_is_still_inventory(tmp_path):
    """The property the decomposition depends on. Without it, every extraction
    silently shrinks the documented surface."""
    source = """
            m = re.fullmatch(r"/made-up/([^/]+)/probe", sub)
            if m and method == "GET":
                pass
    """
    assert "/made-up/([^/]+)/probe" in http_routes(source)


def test_every_discovered_route_module_is_scanned_or_absent(tmp_path):
    """A discovered module must be readable -- a name that silently scans
    nothing is exactly the failure mode this guards."""
    for name in _route_modules():
        path = _SERVER_PKG / name
        if path.exists():
            assert path.read_text("utf-8"), f"{name} is discovered but empty"


def test_a_module_carrying_routes_must_follow_the_naming_convention():
    """The inventory's membership is derived from `*_routes.py`, so a module
    that owns route branches under any other name is invisible to it.

    That failure is silent in the worst possible way: the routes are missing
    from the inventory *and* from the captured contract, so both
    `capture_response_contract.py --check` and
    `capture_response_schemas.py --check` compare two sides that agree with
    each other and disagree with reality -- full coverage of an incomplete
    inventory, which is the exact false confidence this module exists to
    prevent. A naming convention nobody can forget is one the build checks.

    If a module legitimately uses the routing idioms without being reachable
    through `Handler._route` (as `share_router.py` does, serving the outbound
    tunnel), name it in `_NON_GATEWAY_ROUTE_MODULES` with a reason rather than
    widening this test.
    """
    baseline = set(http_routes())
    discovered = set(_route_modules())
    offenders: dict[str, list[str]] = {}

    for path in sorted(_SERVER_PKG.glob("*.py")):
        name = path.name
        if name in {"gateway.py", "contract.py", "__init__.py"}:
            continue
        if name in discovered or name in _NON_GATEWAY_ROUTE_MODULES:
            continue
        # What would the extractor find if this module were scanned?
        found = set(http_routes(path.read_text("utf-8")))
        extra = sorted(found - baseline)
        if extra:
            offenders[name] = extra

    assert not offenders, (
        "these modules carry gateway routing idioms but are neither named "
        f"'{contract._ROUTE_MODULE_GLOB}' nor declared non-gateway: {offenders}"
    )


def test_widening_the_scan_adds_only_what_the_route_modules_declare():
    """The widening must not invent surface.

    This was pinned as ``len(http_routes()) == 144``, which was the right check
    on the day the scan was widened -- an unchanged tree, so any change in the
    count meant the wider scan was reading something that is not surface. It is
    the wrong check afterwards: it fails for the *intended* reason (a route was
    added) as loudly as for the unintended one, and a test whose only failure
    mode is "bump the number" is not read before it is bumped.

    So assert the property instead of the total. Every route the widened scan
    reports comes from gateway.py or from a module named by the route-module
    convention -- nothing appears from a source neither of those covers. That
    holds however many routes exist, and still fails if the extractor starts
    picking up a validator pattern or a fixture.
    """
    from openai4s.server import contract

    widened = set(http_routes())
    declared = set(http_routes(contract._source()))
    for name in contract._route_modules():
        path = contract._SERVER_PKG / name
        if path.is_file():
            declared |= set(http_routes(path.read_text("utf-8")))
    # A declarative module's live table is the more truthful source: the AST
    # reader sees only *constant* patterns, so a legitimate `RouteSpec` built
    # from a computed string would appear in `widened` alone and fail the first
    # assertion with the opposite accusation -- "the widened scan reports routes
    # that no declared source contains" -- about a route the module declares
    # correctly.
    declared |= {spec.pattern for spec in contract.declared_http_routes()}

    assert not (widened - declared), (
        "the widened scan reports routes that no declared source contains: "
        f"{sorted(widened - declared)}"
    )
    # And the reverse, which is the cheaper half: a declared source whose routes
    # go missing means `_route_sources` stopped reading it.
    assert not (declared - widened), (
        "a declared route source is no longer being read: "
        f"{sorted(declared - widened)}"
    )


# --------------------------------------------------------------------------
# the inventory is the contract, so it has to see the whole surface
# --------------------------------------------------------------------------


def test_the_inventory_covers_every_route_the_router_can_match():
    """The regression.

    `_route_sources` was widened to read the modules route branches were
    extracted into, and `inventory()` then handed `http_routes` the gateway
    text alone — defeating the widening at the one call site that produces the
    machine-readable artifact. `http_routes()` reported 144 routes while the
    inventory reported 132, and the 12 endpoints in kernel_routes.py were
    absent from the thing that is supposed to *be* the contract.
    """
    from openai4s.server.contract import http_routes, inventory

    assert set(inventory()["http_routes"]) == set(http_routes())


def test_an_extracted_route_module_is_in_the_inventory():
    """Named concretely so the next extraction cannot quietly shrink it."""
    from pathlib import Path

    from openai4s.server.contract import inventory

    module = Path("openai4s/server/kernel_routes.py")
    if not module.is_file():
        import pytest

        pytest.skip("kernel_routes.py has been renamed; update this test with it")
    listed = set(inventory()["http_routes"])
    # Any exact route string the extracted module owns must be present.
    text = module.read_text("utf-8")
    owned = {r for r in listed if f'"{r}"' in text}
    assert owned, "no route from the extracted module appears in the inventory"


# --------------------------------------------------------------------------
# an entry that cannot match anything is not a route
# --------------------------------------------------------------------------


def test_no_inventory_entry_is_an_incomplete_matcher():
    """The reproduction: a `re.fullmatch` assembled from adjacent raw literals.

    A regex scan takes the first literal it sees, so the gateway's multi-line
    pattern entered the inventory as `/frames/([^/]+)/(?:` — a fragment that
    cannot match anything. The contract driver then concretised it, recorded
    the 404 that a non-route naturally earns, and counted that toward the
    published coverage.
    """
    for route in contract.http_routes():
        assert contract.is_complete_matcher(route), (
            f"{route!r} cannot form a complete matcher, so it describes no "
            f"surface and must not be counted as one"
        )
        assert route.count("(") == route.count(")"), route


def test_a_pattern_split_across_string_literals_is_read_whole():
    source = (
        "def _api(self, sub):\n"
        "    if re.fullmatch(\n"
        '        r"/frames/([^/]+)/(?:"\n'
        '        r"alpha|beta"\n'
        '        r")", sub):\n'
        "        return 1\n"
    )
    routes = contract.http_routes(source)
    assert routes == {"/frames/([^/]+)/(?:alpha|beta)"}


def test_a_non_constant_pattern_is_left_out_rather_than_half_read():
    source = (
        "def _api(self, sub):\n"
        "    if re.fullmatch(build_pattern(), sub):\n"
        "        return 1\n"
    )
    assert contract.http_routes(source) == set()


def test_every_route_concretises_to_a_path_that_reaches_it():
    """A route whose own probe does not match it drives nothing, so its 404 is
    a fact about the probe rather than about the surface."""
    from openai4s.server import response_capture

    unroutable = sorted(
        route for route in contract.http_routes() if response_capture.unroutable(route)
    )
    assert unroutable == [], (
        f"these entries cannot be driven and must not count as covered: "
        f"{unroutable}"
    )


def test_an_alternation_is_sampled_rather_than_stripped():
    from openai4s.server.response_capture import concrete_path

    route = "/frames/([^/]+)/(?:action-timeline|context|recovery(?:/actions)?)"
    assert concrete_path(route) == "/frames/probe-id/action-timeline"
    assert re.fullmatch(route, concrete_path(route))
