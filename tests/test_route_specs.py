"""Contracts for the first declarative HTTP route registry slice.

These tests are intentionally about routing facts, not kernel behaviour. The
existing gateway-kernel tests already pin handler bodies, guard ordering and
wrong-method 404s end to end; this file pins the new shared declaration layer
that the runtime and contract inventory now consume together.
"""

from openai4s.server import contract, kernel_routes
from openai4s.server.routing import RouteSpec


EXPECTED_KERNEL_ROUTES = (
    ("kernel.execution", "GET", r"/frames/([^/]+)/execution"),
    ("kernel.execute", "POST", r"/frames/([^/]+)/kernel/execute"),
    ("kernel.restart", "POST", r"/frames/([^/]+)/kernel/restart"),
    ("kernel.stop", "POST", r"/frames/([^/]+)/kernel/stop"),
    ("kernel.interrupt", "POST", r"/frames/([^/]+)/kernel/interrupt"),
    ("kernel.start", "POST", r"/frames/([^/]+)/kernel/start"),
    ("kernel.variables", "GET", r"/frames/([^/]+)/kernel/variables"),
    ("kernel.status", "GET", r"/frames/([^/]+)/kernel"),
    ("session.status", "GET", r"/frames/([^/]+)/status"),
    ("kernel.install", "POST", r"/frames/([^/]+)/kernel/install"),
    ("kernel.environments", "GET", r"/frames/([^/]+)/environments"),
    ("kernel.env", "POST", r"/frames/([^/]+)/kernel/env"),
)


def test_kernel_registry_preserves_the_pre_refactor_order_and_surface():
    actual = tuple(
        (spec.name, spec.method, spec.pattern) for spec in kernel_routes.ROUTES
    )
    assert actual == EXPECTED_KERNEL_ROUTES


def test_route_specs_are_unique_by_name_and_method_path():
    specs = contract.declared_http_routes()
    names = [spec.name for spec in specs]
    method_paths = [(spec.method, spec.pattern) for spec in specs]
    assert len(names) == len(set(names)), "route names are protocol identities"
    assert len(method_paths) == len(set(method_paths)), "duplicate HTTP route declaration"


def test_route_spec_matching_is_method_aware():
    spec = next(s for s in kernel_routes.ROUTES if s.name == "kernel.execute")
    path = "/frames/f-1/kernel/execute"
    assert spec.match("POST", path) is not None
    assert spec.match("GET", path) is None


def test_inventory_reads_declarative_routes_even_without_route_source_scan(monkeypatch):
    """The point of the migration: registered runtime routes do not disappear
    merely because contract tooling stops parsing their handler source.
    """

    gateway_only = contract._source()
    monkeypatch.setattr(contract, "_route_sources", lambda: [gateway_only])
    inventoried = contract.http_routes()
    expected = {spec.pattern for spec in kernel_routes.ROUTES}
    assert expected <= inventoried


def test_source_fragment_extractor_understands_route_specs_for_contract_tests():
    source = 'RouteSpec("probe", "GET", r"/probe/([^/]+)")\n'
    assert contract.http_routes(source) == {r"/probe/([^/]+)"}


def test_route_spec_rejects_invalid_protocol_facts():
    for args in (
        ("", "GET", "/ok"),
        ("bad-method", "get", "/ok"),
        ("bad-path", "GET", "relative"),
        ("bad-regex", "GET", "/("),
    ):
        try:
            RouteSpec(*args)
        except ValueError:
            pass
        else:  # pragma: no cover - assertion branch
            raise AssertionError(f"invalid RouteSpec was accepted: {args!r}")
