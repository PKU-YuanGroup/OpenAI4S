"""A non-integer `limit` is a client mistake, not a server fault.

`GET /projects/{pid}/action-timeline` and `GET /projects/{pid}/lineage`
parsed `?limit=` with a bare ``int(...)``: `?limit=abc` raised ValueError,
the catch-all mapped it to 500 "internal error", and a client probing the
boundary could not tell its own typo from a broken server. The messages
route carries the 400 guard for the same parameter with that exact
reasoning in its comment; these two routes now mirror it, including the
`invalid_limit` code.

Each 400 test fails if the guard is removed: the route goes back to a 500.
"""

from __future__ import annotations

import json

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth


class _Hub:
    def __init__(self):
        self.events = []

    def emitter(self, root_frame_id):
        def emit(event):
            event.setdefault("root_frame_id", root_frame_id)
            self.events.append(event)

        return emit

    def broadcast(self, root_frame_id, event):
        event.setdefault("root_frame_id", root_frame_id)
        self.events.append(event)


def _cfg(tmp_path):
    return Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=3,
    )


def _drive(cfg, runner, path):
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    handler = object.__new__(handler_cls)
    seen: list[tuple[dict, int]] = []

    def capture(code, payload, content_type, extra=None, *, security=None):
        assert content_type == "application/json; charset=utf-8"
        seen.append((json.loads(payload), code))

    # Keep the production error enrichment and JSON serialization in the path.
    handler._send = capture
    handler.headers = {
        local_auth.TOKEN_HEADER: local_auth.load_or_mint(cfg.data_dir),
        "X-Request-Id": "project-view-route-test",
    }
    handler.path = path
    handler._route("GET")
    return seen[-1]


def test_action_timeline_rejects_a_non_integer_limit(tmp_path):
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    try:
        body, code = _drive(
            cfg, runner, "/api/v1/projects/default/action-timeline?limit=abc"
        )
        assert code == 400
        assert body == {
            "error": "limit must be an integer",
            "code": "invalid_limit",
            "status": 400,
            "request_id": "project-view-route-test",
        }
    finally:
        runner.close()


def test_lineage_rejects_a_non_integer_limit(tmp_path):
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    try:
        body, code = _drive(cfg, runner, "/api/v1/projects/default/lineage?limit=xyz")
        assert code == 400
        assert body == {
            "error": "limit must be an integer",
            "code": "invalid_limit",
            "status": 400,
            "request_id": "project-view-route-test",
        }
    finally:
        runner.close()


def test_action_timeline_serves_a_valid_limit(tmp_path):
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    try:
        body, code = _drive(
            cfg, runner, "/api/v1/projects/default/action-timeline?limit=5"
        )
        assert code == 200
        assert body["project_id"] == "default"
    finally:
        runner.close()


def test_lineage_serves_a_valid_limit(tmp_path):
    cfg = _cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    try:
        body, code = _drive(cfg, runner, "/api/v1/projects/default/lineage?limit=5")
        assert code == 200
        assert body["project_id"] == "default"
    finally:
        runner.close()
