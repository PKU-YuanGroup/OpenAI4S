"""HTTP adapter tests for the Stage 2 Auto Mode read/configuration surface."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openai4s.server import auto_mode_routes, contract
from openai4s.server.auto_mode import AutoModeError
from openai4s.server.gateway import WSHub

pytestmark = pytest.mark.stubbed_backend


class _Handler:
    def __init__(self, body=None) -> None:
        self.body = body or {}
        self.responses: list[tuple[dict, int]] = []

    def _body(self):
        return self.body

    def _json(self, payload, status=200):
        self.responses.append((payload, status))


class _Service:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.error: AutoModeError | None = None

    def _result(self, call, value):
        self.calls.append(call)
        if self.error is not None:
            raise self.error
        return value

    def get(self, frame_id):
        return self._result(("get", frame_id), {"feature_enabled": True})

    def patch(self, frame_id, body):
        return self._result(("patch", frame_id, body), {"selection": body})

    def list_audits(self, frame_id, *, subject_kind, before, limit):
        return self._result(
            ("audits", frame_id, subject_kind, before, limit), {"audits": []}
        )


def _runner(service):
    return SimpleNamespace(auto_mode=service)


def test_get_patch_and_audit_routes_are_exact_and_no_transition_route_exists():
    service = _Service()
    handler = _Handler({"revision": 0, "preset": "off"})

    assert auto_mode_routes.handle(
        handler, "GET", "/frames/frame/auto-mode", {}, _runner(service)
    )
    assert auto_mode_routes.handle(
        handler, "PATCH", "/frames/frame/auto-mode", {}, _runner(service)
    )
    assert auto_mode_routes.handle(
        handler,
        "GET",
        "/frames/frame/auto-audits",
        {"subject_kind": ["permission_review"], "before": ["17"], "limit": ["25"]},
        _runner(service),
    )
    assert service.calls == [
        ("get", "frame"),
        ("patch", "frame", {"revision": 0, "preset": "off"}),
        ("audits", "frame", "permission_review", "17", 25),
    ]
    assert not auto_mode_routes.handle(
        handler, "POST", "/frames/frame/auto-mode", {}, _runner(service)
    )
    assert not auto_mode_routes.handle(
        handler, "POST", "/frames/frame/auto-audits", {}, _runner(service)
    )


def test_service_errors_keep_status_and_stable_code():
    service = _Service()
    service.error = AutoModeError(409, "disabled", "auto_mode_storage_disabled")
    handler = _Handler()

    assert auto_mode_routes.handle(
        handler, "GET", "/frames/frame/auto-mode", {}, _runner(service)
    )
    assert handler.responses == [
        ({"error": "disabled", "code": "auto_mode_storage_disabled"}, 409)
    ]


def test_query_validation_answers_400_before_service_call():
    service = _Service()
    handler = _Handler()

    assert auto_mode_routes.handle(
        handler,
        "GET",
        "/frames/frame/auto-audits",
        {"limit": ["zero"]},
        _runner(service),
    )
    payload, status = handler.responses[-1]
    assert status == 400
    assert payload["code"] == "invalid_limit"
    assert service.calls == []


def test_routes_are_in_the_machine_readable_inventory():
    declared = {(spec.method, spec.pattern) for spec in auto_mode_routes.ROUTES}
    assert declared == {
        ("GET", r"/frames/([^/]+)/auto-mode"),
        ("PATCH", r"/frames/([^/]+)/auto-mode"),
        ("GET", r"/frames/([^/]+)/auto-audits"),
    }
    inventory = contract.inventory()
    assert r"/frames/([^/]+)/auto-mode" in inventory["http_routes"]
    assert r"/frames/([^/]+)/auto-audits" in inventory["http_routes"]


def test_canonical_auto_events_are_in_websocket_inventory_without_aliases():
    outbound = contract.inventory()["ws_outbound"]
    assert {
        "auto_run_started",
        "candidate_ready",
        "auto_audit_started",
        "auto_audit_completed",
        "repair_started",
        "repair_completed",
        "auto_run_terminal",
    } <= set(outbound)
    assert "review_started" not in outbound
    assert "guardian_completed" not in outbound


def test_auto_events_from_an_older_execution_cannot_overwrite_the_live_run():
    class _Connection:
        alive = True

        def __init__(self):
            self.subs = {"root"}
            self.events = []

        def send_json(self, event):
            self.events.append(dict(event))

        def refresh_visibility(self, root_frame_id):
            del root_frame_id
            return True

    hub = WSHub()
    conn = _Connection()
    hub.add(conn)  # type: ignore[arg-type]
    hub.broadcast(
        "root",
        {
            "type": "frame_update",
            "root_frame_id": "root",
            "status": "processing",
            "execution_id": "execution-new",
        },
    )
    conn.events.clear()

    hub.broadcast(
        "root",
        {
            "type": "candidate_ready",
            "root_frame_id": "root",
            "execution_id": "execution-old",
        },
    )
    assert conn.events == []

    hub.broadcast(
        "root",
        {
            "type": "candidate_ready",
            "root_frame_id": "root",
            "execution_id": "execution-new",
        },
    )
    assert [event["type"] for event in conn.events] == ["candidate_ready"]
