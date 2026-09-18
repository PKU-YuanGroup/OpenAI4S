"""Web first-run onboarding: redacted GET, complete POST, zero outbound."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

from openai4s.config import Config, LLMConfig
from openai4s.onboarding import OnboardingService
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth, onboarding_routes, team_policy
from openai4s.server.model_discovery import LocalModelDiscoveryService


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


def _secret_hits(payload, secret: str) -> int:
    blob = json.dumps(payload) if not isinstance(payload, str) else payload
    return blob.count(secret)


def _api(tmp_path):
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=1,
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_class = gateway_mod.make_handler(cfg, _Hub(), runner)
    token = local_auth.read_token(tmp_path) or ""

    def call(method, path, body=None, *, identity=None):
        handler = object.__new__(handler_class)
        handler._correlation_id = "req-1"
        sent: dict = {}
        handler._send = (
            lambda code, payload, ctype, extra=None, security=None: sent.update(
                code=code, body=json.loads(payload.decode("utf-8"))
            )
        )
        handler.command = method
        handler.path = f"/api/v1{path}"
        raw = json.dumps(body or {}).encode("utf-8")
        handler.headers = {
            "Content-Length": str(len(raw)) if body is not None else "0",
            "Content-Type": "application/json",
            local_auth.TOKEN_HEADER: token,
        }
        handler.rfile = io.BytesIO(raw if body is not None else b"")
        if identity is not None:
            handler._team_identity = identity
        handler._route(method)
        return sent

    return runner, call


def test_get_onboarding_is_redacted_and_contacts_nobody(tmp_path, monkeypatch):
    secret = "sk-fake-onboarding-must-not-leak-9f3a"
    monkeypatch.setattr(
        "openai4s.llm.chat",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("GET contacted LLM")),
    )
    monkeypatch.setattr(
        LocalModelDiscoveryService,
        "discover",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("GET refreshed the local catalogue")
        ),
    )

    runner, call = _api(tmp_path)
    created = call(
        "POST",
        "/model-profiles",
        {
            "name": "first",
            "provider": "openai_responses",
            "api_key": secret,
            "model": "gpt-4o",
        },
    )
    assert created["code"] == 201, created

    result = call("GET", "/onboarding")
    assert result["code"] == 200, result
    body = result["body"]
    assert body["outbound"] == 0
    assert body["contacted"] is False
    assert body["network"]["contacted"] is False
    assert body["environment"]["network_contacted"] is False
    assert body["local_model_catalog"]["probed"] == 0
    assert body["local_model_catalog"]["contacted"] is False
    assert body["local_model_catalog"]["background_refresh"] is False
    assert body["has_api_key"] is True
    assert isinstance(body["has_api_key"], bool)
    assert "data_dir" not in body
    assert _secret_hits(body, secret) == 0
    assert secret not in json.dumps(body)


def test_get_onboarding_before_test_has_zero_outbound(tmp_path, monkeypatch):
    """The quantified gate: Test 前 outbound=0."""
    outbound = {"n": 0}

    def _count(*_a, **_k):
        outbound["n"] += 1
        raise AssertionError("onboarding GET made an outbound call")

    monkeypatch.setattr("openai4s.llm.chat", _count)
    monkeypatch.setattr("openai4s.llm.transport.post_json", _count)
    monkeypatch.setattr("openai4s.llm.transport.post_sse", _count)

    _runner, call = _api(tmp_path)
    body = call("GET", "/onboarding")["body"]
    assert outbound["n"] == 0
    assert body["outbound"] == 0


def test_complete_marks_first_run_without_a_provider_call(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "openai4s.llm.chat",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("complete contacted")),
    )
    runner, call = _api(tmp_path)
    result = call("POST", "/onboarding/complete", {"skip": True})
    assert result["code"] == 200, result
    assert result["body"]["complete"] is True
    assert runner.store.get_setting("onboarding_complete") == "1"
    assert result["body"]["outbound"] == 0


def test_complete_is_instance_config_and_members_are_refused():
    assert team_policy.is_instance_config("POST", "/onboarding/complete")
    assert not team_policy.is_instance_config("GET", "/onboarding")

    handler = SimpleNamespace(
        _team_identity=SimpleNamespace(is_admin=False, user_id="bob"),
        sent=None,
    )

    def _json(body, status=200):
        handler.sent = (status, body)

    handler._json = _json
    handler._body = lambda: {"skip": True}

    owned = onboarding_routes.handle(
        handler,
        "POST",
        "/onboarding/complete",
        {},
        store=SimpleNamespace(),
        cfg=SimpleNamespace(),
        model_profiles=SimpleNamespace(),
        model_discovery=SimpleNamespace(),
    )
    assert owned is True
    assert handler.sent[0] == 403
    assert handler.sent[1]["code"] == "admin_only"


def test_web_status_never_returns_the_key(tmp_path):
    from openai4s.llm.registry import provider_specs
    from openai4s.store import get_store

    secret = "sk-fake-web-status-canary-aabbcc"
    cfg = Config(data_dir=tmp_path / "data", llm=LLMConfig(provider="chatgpt"))
    store = get_store(cfg.db_path)
    service = OnboardingService(cfg, store, provider_specs())
    service.configure(provider="chatgpt", api_key=secret)
    payload = service.web_status()
    assert payload["has_api_key"] is True
    assert _secret_hits(payload, secret) == 0
    assert "api_key" not in payload


def test_diagnostics_bundle_does_not_include_the_key(tmp_path):
    """key 在 GET/DOM/日志/diagnostics 命中数 0 — diagnostics half."""
    from openai4s.diagnostics import redact_text
    from openai4s.llm.registry import provider_specs
    from openai4s.store import get_store

    secret = "sk-diag-canary-1122334455"
    cfg = Config(data_dir=tmp_path / "data", llm=LLMConfig(provider="chatgpt"))
    store = get_store(cfg.db_path)
    service = OnboardingService(cfg, store, provider_specs())
    service.configure(provider="chatgpt", api_key=secret)
    dumped = redact_text(json.dumps(service.web_status()))
    assert dumped.count(secret) == 0


def test_catalog_candidates_are_listed_without_opening_sockets():
    service = LocalModelDiscoveryService()
    payload = service.catalog()
    assert payload["probed"] == 0
    assert payload["contacted"] is False
    assert payload["background_refresh"] is False
    assert payload["mutated_settings"] is False
    assert len(payload["endpoints"]) == len(service.endpoints)


# --- an upgraded install is not a first run ---------------------------------
#
# The wizard decides whether to open from one stored flag, `onboarding_complete`.
# 0.2.0 had the same flag but only `openai4s init` wrote it: its Web UI had no
# wizard and no `/onboarding` route. So every 0.2.0 install configured through
# `.env` or Customize -> Models -- and used for real work -- reopened after the
# upgrade behind a modal saying "No profile yet". The flag is not the only
# evidence of an install that is already set up; the database it opens is.


def _first_boot(tmp_path):
    """A daemon's first boot on this data dir, as `build_app_server` seeds it."""
    runner, call = _api(tmp_path)
    gateway_mod._seed_example_project(runner.cfg)
    gateway_mod._seed_example_connector(runner.cfg)
    return runner, call


def test_a_fresh_install_still_gets_the_first_run_wizard(tmp_path):
    """The negative control. The suite's fake provider key is in the
    environment here, exactly as an `.env` key is on a real fresh install: an
    environment key with no history and no stored configuration is what a
    first run looks like, so it must not count."""
    runner, call = _first_boot(tmp_path)
    body = call("GET", "/onboarding")["body"]
    assert body["complete"] is False, body
    assert runner.store.get_setting("onboarding_complete") in (None, "")


def test_an_upgraded_install_with_session_history_is_not_a_first_run(tmp_path):
    """0.2.0, configured through the environment, used through the Web UI."""
    runner, call = _first_boot(tmp_path)
    project = runner.store.create_project(name="Work", description="", context="")
    if isinstance(project, dict):
        project = project["project_id"]
    frame = runner.store.new_frame(kind="turn", project_id=project, status="done")
    runner.store.add_message(root_frame_id=frame, role="user", content="17 * 23?")
    runner.store.add_message(root_frame_id=frame, role="assistant", content="391")
    assert runner.store.get_setting("onboarding_complete") in (None, "")

    body = call("GET", "/onboarding")["body"]
    assert body["complete"] is True, body
    # Answered from what is there. A GET that wrote the flag would be a read
    # path mutating instance configuration, which team mode reserves to admins.
    assert runner.store.get_setting("onboarding_complete") in (None, "")


def test_an_upgraded_install_used_only_through_the_notebook_is_not_a_first_run(
    tmp_path,
):
    """UPG3-02. 0.2.0, configured through the environment, used only through
    the Notebook REPL (`OPENAI4S_NOTEBOOK_REPL=1`).

    A REPL cell writes a frame, an execution record and its artifacts, and
    never a message -- so this install matched none of the evidence and got
    the blocking wizard over its own cells. The execution record is written
    through the same Store call the REPL execute route uses."""
    runner, call = _first_boot(tmp_path)
    project = runner.store.create_project(name="Work", description="", context="")
    if isinstance(project, dict):
        project = project["project_id"]
    frame = runner.store.new_frame(kind="turn", project_id=project, status="ready")
    runner.store.log_cell(
        frame_id=frame,
        root_frame_id=frame,
        project_id=project,
        code="with open('mine.txt', 'w') as f:\n    f.write('upg3-02')",
        result={"id": "cell-repl-only", "stdout": "", "files_written": ["mine.txt"]},
        language="python",
        origin="user",
        cell_index=1,
    )
    assert not runner.store.has_message_history()
    for key in OnboardingService._CONFIGURATION_SETTINGS:
        assert runner.store.get_setting(key) in (None, ""), key
    assert runner.store.get_setting("onboarding_complete") in (None, "")

    body = call("GET", "/onboarding")["body"]
    assert body["complete"] is True, body
    assert runner.store.get_setting("onboarding_complete") in (None, "")


def test_a_session_created_but_never_run_is_still_a_first_run(tmp_path):
    """The negative control for the notebook evidence: opening a session is not
    using the install. Only an executed cell counts, so a fresh install whose
    user clicked "new session" before configuring a model keeps the wizard."""
    runner, call = _first_boot(tmp_path)
    project = runner.store.create_project(name="Work", description="", context="")
    if isinstance(project, dict):
        project = project["project_id"]
    runner.store.new_frame(kind="turn", project_id=project, status="ready")

    body = call("GET", "/onboarding")["body"]
    assert body["complete"] is False, body


def test_an_upgraded_install_configured_in_customize_is_not_a_first_run(tmp_path):
    """0.2.0, configured through Customize -> Models, before any session ran."""
    runner, call = _first_boot(tmp_path)
    saved = call(
        "PUT",
        "/config/llm",
        {"provider": "chatgpt", "model": "gpt-4o", "api_key": "sk-configured-in-ui"},
    )
    assert saved["code"] == 200, saved

    body = call("GET", "/onboarding")["body"]
    assert body["complete"] is True, body


def test_an_install_with_only_a_saved_profile_is_not_a_first_run(tmp_path):
    """0.2.0, with a profile saved in Customize -> Models and never activated:
    no `llm_*` row, no active profile and no message, so the profile is the only
    evidence -- and it is enough. A deleted profile is not: it configures
    nothing."""
    runner, call = _first_boot(tmp_path)
    created = call(
        "POST",
        "/model-profiles",
        {"name": "saved", "provider": "claude", "model": "claude-sonnet-4-5"},
    )
    assert created["code"] == 201, created
    for key in OnboardingService._CONFIGURATION_SETTINGS:
        assert runner.store.get_setting(key) in (None, ""), key
    assert not runner.store.has_message_history()
    assert call("GET", "/onboarding")["body"]["complete"] is True

    assert call("DELETE", f"/model-profiles/{created['body']['id']}")["code"] in (
        200,
        204,
    )
    for key in OnboardingService._CONFIGURATION_SETTINGS:
        assert runner.store.get_setting(key) in (None, ""), key
    assert call("GET", "/onboarding")["body"]["complete"] is False
