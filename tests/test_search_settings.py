"""Backup web-search engines: the stored order, the setup choice, the
Doubao-quota fallback confirmation, and the settings route.

Everything here is offline. The search engines themselves are stubbed at
`webtools.web_search`; the Doubao provider at its service object.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from openai4s import search_settings, webtools
from openai4s.config import Config, LLMConfig
from openai4s.doubao_search import DoubaoSearchQuotaError
from openai4s.host.search import FALLBACK_PERMISSION, confirm_quota_fallback
from openai4s.permissions import PermissionBroker
from openai4s.server.errors import GatewayError
from openai4s.store import get_store

_KEY_ENVS = ("OPENAI4S_TAVILY_API_KEY", "YDC_API_KEY")


@pytest.fixture(autouse=True)
def _clean_search_env(monkeypatch):
    # `set_key` writes os.environ itself. setenv first so teardown removes a
    # value the code under test set, then delenv for an empty starting point.
    for name in _KEY_ENVS:
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)


def _store(tmp_path):
    cfg = Config(data_dir=tmp_path, llm=LLMConfig(provider="deepseek", api_key="k"))
    st = get_store(cfg.db_path)
    st.seed_default_permission_rules()
    return st


# --------------------------------------------------------------------------- #
#  stored order and switches
# --------------------------------------------------------------------------- #
def test_a_fresh_install_keeps_the_pre_setting_chain(tmp_path):
    st = _store(tmp_path)
    assert search_settings.load(st) == {
        "order": ["tavily", "you", "builtin"],
        "enabled": {"tavily": True, "you": True, "builtin": True},
    }
    assert search_settings.engine_chain(st) == webtools.SEARCH_ENGINES


def test_order_and_switches_round_trip(tmp_path):
    st = _store(tmp_path)
    search_settings.save(
        st,
        order=["builtin", "you", "tavily"],
        enabled={"tavily": True, "you": False, "builtin": True},
    )
    assert search_settings.engine_chain(st) == ("builtin", "tavily")


def test_a_row_from_another_version_is_repaired(tmp_path):
    st = _store(tmp_path)
    st.set_setting(
        search_settings.SETTING_KEY,
        json.dumps({"order": ["gone", "you", "you"], "enabled": {"you": False}}),
    )
    assert search_settings.load(st) == {
        "order": ["you", "tavily", "builtin"],
        "enabled": {"tavily": True, "you": False, "builtin": True},
    }


@pytest.mark.parametrize(
    "order, enabled",
    [
        (["tavily", "you"], {"tavily": True, "you": True, "builtin": True}),
        (["tavily", 1, "you"], {"tavily": True, "you": True, "builtin": True}),
        (["tavily", "you", "you"], {"tavily": True, "you": True, "builtin": True}),
        (["tavily", "you", "builtin"], {"tavily": True, "you": True}),
        (["tavily", "you", "builtin"], {"tavily": 1, "you": True, "builtin": True}),
    ],
)
def test_a_malformed_configuration_is_refused(tmp_path, order, enabled):
    st = _store(tmp_path)
    with pytest.raises(search_settings.SearchSettingsError):
        search_settings.save(st, order=order, enabled=enabled)


def test_nothing_usable_is_refused_and_keyless_tavily_does_not_count(tmp_path):
    st = _store(tmp_path)
    only_tavily = {"tavily": True, "you": False, "builtin": False}
    with pytest.raises(search_settings.SearchSettingsError, match="usable"):
        search_settings.save(
            st, order=list(webtools.SEARCH_ENGINES), enabled=only_tavily
        )
    search_settings.set_key(st, "tavily", "tvly-test")
    search_settings.save(st, order=list(webtools.SEARCH_ENGINES), enabled=only_tavily)
    assert search_settings.engine_chain(st) == ("tavily",)


def test_setup_choice_moves_an_engine_first(tmp_path):
    st = _store(tmp_path)
    search_settings.apply_setup_choice(st, "you")
    assert search_settings.load(st)["order"][0] == "you"
    search_settings.apply_setup_choice(st, "builtin")
    assert search_settings.engine_chain(st) == ("builtin",)


def test_setup_choice_for_tavily_needs_a_key_and_mirrors_it(tmp_path):
    import os

    st = _store(tmp_path)
    with pytest.raises(search_settings.SearchSettingsError, match="Tavily"):
        search_settings.apply_setup_choice(st, "tavily")
    view = search_settings.apply_setup_choice(st, "tavily", "tvly-secret-value")
    assert os.environ["OPENAI4S_TAVILY_API_KEY"] == "tvly-secret-value"
    assert view["order"][0] == "tavily"
    assert "tvly-secret-value" not in json.dumps(view)


def test_setup_choice_for_doubao_needs_the_agent_plan_key(tmp_path):
    st = _store(tmp_path)
    with pytest.raises(search_settings.SearchSettingsError, match="Agent Plan"):
        search_settings.apply_setup_choice(st, "doubao")
    with pytest.raises(search_settings.SearchSettingsError):
        search_settings.apply_setup_choice(st, "bing")


def test_you_key_switches_the_reported_endpoint(tmp_path):
    st = _store(tmp_path)

    def you():
        return next(
            e for e in search_settings.project(st)["engines"] if e["id"] == "you"
        )

    assert you()["endpoint"] == "https://api.you.com/v1/agents/search"
    assert you()["api_key_configured"] is False
    search_settings.set_key(st, "you", "ydc-test")
    assert you()["endpoint"] == "https://ydc-index.io/v1/search"
    assert you()["api_key_configured"] is True


# --------------------------------------------------------------------------- #
#  the fallback confirmation
# --------------------------------------------------------------------------- #
class _Broker:
    """Records gate calls; answers with a scripted choice."""

    def __init__(self, *, reachable=True, answer=None):
        self.reachable = reachable
        self.answer = answer
        self.gates = []

    def human_reachable(self, **_kwargs):
        return self.reachable

    def gate(self, **kwargs):
        self.gates.append(kwargs)
        return self.answer or {"allow": False, "message": "denied by user"}


def _confirm(st, broker, *, engines=("tavily", "you", "builtin"), delegated=False):
    return confirm_quota_fallback(
        store=st,
        frame_id="root-search",
        engines=engines,
        query="crispr",
        delegated=delegated,
        action_context={},
        permission_broker=broker,
    )


def _rule(st, engine, decision):
    st.set_permission_rule(
        scope="global",
        scope_id="",
        tool=FALLBACK_PERMISSION,
        pattern=engine,
        decision=decision,
    )


def test_the_user_picks_from_the_usable_switched_on_engines(tmp_path):
    st = _store(tmp_path)
    broker = _Broker(answer={"allow": True, "decision_id": "d", "choice": "builtin"})
    assert _confirm(st, broker) == "builtin"
    (gate,) = broker.gates
    assert gate["method"] == FALLBACK_PERMISSION
    assert gate["approvals_reviewer"] == "user"
    # Tavily has no key, so it cannot answer and is not offered.
    assert [c["id"] for c in gate["choices"]] == ["you", "builtin"]


def test_a_denied_prompt_means_no_fallback(tmp_path):
    st = _store(tmp_path)
    assert _confirm(st, _Broker()) is None


def test_a_remembered_allow_decides_without_asking_even_unattended(tmp_path):
    st = _store(tmp_path)
    _rule(st, "builtin", "allow")
    broker = _Broker(reachable=False)
    assert _confirm(st, broker, delegated=True) == "builtin"
    assert broker.gates == []


def test_a_remembered_deny_removes_that_engine_from_the_offer(tmp_path):
    st = _store(tmp_path)
    _rule(st, "you", "deny")
    broker = _Broker()
    _confirm(st, broker)
    assert [c["id"] for c in broker.gates[0]["choices"]] == ["builtin"]


@pytest.mark.parametrize("delegated, reachable", [(True, True), (False, False)])
def test_nobody_to_ask_means_no_fallback_and_no_prompt(tmp_path, delegated, reachable):
    st = _store(tmp_path)
    broker = _Broker(reachable=reachable)
    assert _confirm(st, broker, delegated=delegated) is None
    assert broker.gates == []


def test_nothing_switched_on_means_nothing_to_offer(tmp_path):
    st = _store(tmp_path)
    broker = _Broker()
    assert _confirm(st, broker, engines=("tavily",)) is None
    assert broker.gates == []


def test_the_card_carries_choices_and_returns_the_pick(tmp_path):
    st = _store(tmp_path)
    broker = PermissionBroker()
    events = []
    broker.register_channel("root-choice", events.append)
    out = {}

    def run():
        out["res"] = broker.gate(
            store=st,
            frame_id="root-choice",
            method=FALLBACK_PERMISSION,
            target="you",
            approvals_reviewer="user",
            choices=[{"id": "you", "label": "You.com"}, {"id": "builtin"}],
        )

    thread = threading.Thread(target=run)
    thread.start()
    for _ in range(200):
        if any(e.get("type") == "await_permission" for e in events):
            break
        time.sleep(0.01)
    ask = next(e for e in events if e.get("type") == "await_permission")
    assert ask["choices"] == [
        {"id": "you", "label": "You.com"},
        {"id": "builtin", "label": "builtin"},
    ]
    broker.resolve_result(
        ask["decision_id"],
        allow=True,
        scope="project",
        pattern="builtin",
        store=st,
        root_frame_id="root-choice",
    )
    thread.join(timeout=5)
    assert out["res"]["allow"] is True and out["res"]["choice"] == "builtin"
    # The pick is the remembered rule's pattern.
    assert (
        st.resolve_permission(
            root_frame_id="root-choice",
            project_id="default",
            tool=FALLBACK_PERMISSION,
            pattern_input="builtin",
        )
        == "allow"
    )


def test_an_allow_naming_no_offered_option_is_a_denial(tmp_path):
    st = _store(tmp_path)
    broker = PermissionBroker()
    events = []
    broker.register_channel("root-bad", events.append)
    out = {}

    def run():
        out["res"] = broker.gate(
            store=st,
            frame_id="root-bad",
            method=FALLBACK_PERMISSION,
            target="you",
            approvals_reviewer="user",
            choices=[{"id": "you"}],
        )

    thread = threading.Thread(target=run)
    thread.start()
    for _ in range(200):
        if any(e.get("type") == "await_permission" for e in events):
            break
        time.sleep(0.01)
    ask = next(e for e in events if e.get("type") == "await_permission")
    broker.resolve_result(
        ask["decision_id"],
        allow=True,
        scope="global",
        pattern="*",
        store=st,
        root_frame_id="root-bad",
    )
    thread.join(timeout=5)
    assert out["res"]["allow"] is False
    # No rule was left behind by the refused pick.
    assert st.resolve_permission(tool=FALLBACK_PERMISSION, pattern_input="you") == "ask"


# --------------------------------------------------------------------------- #
#  the dispatcher: settings in, fallback only on approval
# --------------------------------------------------------------------------- #
class _QuotaDoubao:
    def search_primary(self, query, *, num_results, timeout, fallback):
        raise DoubaoSearchQuotaError("Doubao search quota or rate limit is spent")


class _NoDoubao:
    def search_primary(self, query, *, num_results, timeout, fallback):
        return fallback(query, num_results=num_results, timeout=timeout)


def _dispatcher(tmp_path):
    from openai4s.host_dispatch import HostDispatcher

    return HostDispatcher(Config(data_dir=tmp_path), frame_id="search-frame")


def _record_searches(monkeypatch):
    calls = []

    def fake(query, num_results=8, timeout=20.0, engines=webtools.SEARCH_ENGINES):
        calls.append(tuple(engines))
        return {"query": query, "count": 0, "results": []}

    monkeypatch.setattr(webtools, "web_search", fake)
    return calls


def test_the_backups_follow_the_stored_order(tmp_path, monkeypatch):
    calls = _record_searches(monkeypatch)
    dispatcher = _dispatcher(tmp_path)
    try:
        search_settings.save(
            dispatcher.store,
            order=["builtin", "you", "tavily"],
            enabled={"tavily": True, "you": False, "builtin": True},
        )
        dispatcher._doubao_search_service = _NoDoubao()
        dispatcher._search_web("q")
    finally:
        dispatcher.store.close()
    assert calls == [("builtin", "tavily")]


def test_an_approved_fallback_starts_at_the_picked_engine(tmp_path, monkeypatch):
    from openai4s.host import search as host_search

    calls = _record_searches(monkeypatch)
    seen = {}

    def confirm(**kwargs):
        seen.update(kwargs)
        return "you"

    monkeypatch.setattr(host_search, "confirm_quota_fallback", confirm)
    dispatcher = _dispatcher(tmp_path)
    try:
        dispatcher._doubao_search_service = _QuotaDoubao()
        dispatcher._search_web("q")
    finally:
        dispatcher.store.close()
    assert calls == [("you", "builtin")]
    assert seen["delegated"] is False and seen["engines"] == webtools.SEARCH_ENGINES


def test_without_approval_the_quota_error_reaches_the_agent(tmp_path, monkeypatch):
    calls = _record_searches(monkeypatch)
    dispatcher = _dispatcher(tmp_path)
    try:
        dispatcher._doubao_search_service = _QuotaDoubao()
        # No UI channel is attached, so nobody can be asked.
        result = dispatcher("web_search", [{"query": "quota", "num_results": 1}])
    finally:
        dispatcher.store.close()
    assert calls == []
    assert "quota" in result["error"]


# --------------------------------------------------------------------------- #
#  the settings route
# --------------------------------------------------------------------------- #
class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


@pytest.fixture
def route(tmp_path):
    from openai4s.server import gateway as gateway_mod

    cfg = Config(data_dir=tmp_path, llm=LLMConfig(provider="deepseek", api_key="k"))
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    replies = []
    state = {"body": {}}
    handler = object.__new__(gateway_mod.make_handler(cfg, _Hub(), runner))
    handler._query = lambda: {}
    handler._body = lambda: state["body"]
    handler._json = lambda obj, code=200: replies.append((code, obj))

    def call(method, body=None):
        state["body"] = body or {}
        try:
            handler._api(method, "/search/config")
        except GatewayError as error:
            return error.code, {"error": str(error)}
        return replies[-1]

    try:
        yield call
    finally:
        runner.close()


def test_route_reports_the_list_and_keeps_the_tavily_fields(route):
    status, body = route("GET")
    assert status == 200
    assert body["endpoint"] == "https://api.tavily.com/search"
    assert body["api_key_configured"] is False
    assert body["order"] == ["tavily", "you", "builtin"]
    assert [e["id"] for e in body["engines"]] == ["tavily", "you", "builtin"]
    assert body["doubao_configured"] is False


def test_route_saves_order_switches_and_a_you_key(route):
    status, body = route(
        "PUT",
        {
            "order": ["you", "builtin", "tavily"],
            "enabled": {"tavily": False, "you": True, "builtin": True},
        },
    )
    assert status == 200 and body["order"] == ["you", "builtin", "tavily"]
    status, body = route("POST", {"engine": "you", "api_key": "ydc-route-secret"})
    assert status == 200
    you = next(e for e in body["engines"] if e["id"] == "you")
    assert you["api_key_configured"] is True
    assert "ydc-route-secret" not in json.dumps(body)


def test_route_refuses_switching_everything_off(route):
    status, body = route(
        "PUT",
        {
            "order": ["tavily", "you", "builtin"],
            "enabled": {"tavily": False, "you": False, "builtin": False},
        },
    )
    assert status == 400 and "usable" in body["error"]
    status, body = route("POST", {"engine": "bing", "api_key": "x"})
    assert status == 400


def test_route_primary_is_the_setup_choice(route):
    status, body = route("POST", {"primary": "builtin"})
    assert status == 200
    assert [e["id"] for e in body["engines"] if e["enabled"]] == ["builtin"]


# --------------------------------------------------------------------------- #
#  openai4s init
# --------------------------------------------------------------------------- #
def _init(tmp_path, monkeypatch, stdin, **flags):
    import importlib
    import io
    from types import SimpleNamespace

    from openai4s.llm import PROVIDERS
    from openai4s.onboarding import OnboardingService

    main = importlib.import_module("openai4s.cli.main")
    cfg = Config(
        data_dir=tmp_path, llm=LLMConfig(provider="openai_responses", api_key="k")
    )
    st = get_store(cfg.db_path)
    monkeypatch.setattr(
        main, "_onboarding_service", lambda: (OnboardingService(cfg, st, PROVIDERS), st)
    )
    monkeypatch.setattr(main.sys, "stdin", io.StringIO(stdin))
    args = SimpleNamespace(
        provider="openai_responses",
        model=None,
        base_url=None,
        non_interactive=True,
        api_key_stdin=False,
        clear_api_key=False,
        search=None,
        search_key_stdin=False,
        json=True,
    )
    for name, value in flags.items():
        setattr(args, name, value)
    return main.cmd_init(args)


def test_init_makes_the_search_choice_without_echoing_the_key(
    tmp_path, monkeypatch, capsys
):
    code = _init(
        tmp_path,
        monkeypatch,
        "tvly-cli-secret\n",
        search="tavily",
        search_key_stdin=True,
    )
    captured = capsys.readouterr()
    assert code == 0, captured.err
    payload = json.loads(captured.out)
    assert payload["search"]["order"][0] == "tavily"
    assert payload["search"]["engines"][0]["usable"] is True
    assert "tvly-cli-secret" not in captured.out + captured.err


def test_init_refuses_two_keys_on_one_stdin(tmp_path, monkeypatch, capsys):
    code = _init(
        tmp_path,
        monkeypatch,
        "a\nb\n",
        api_key_stdin=True,
        search="you",
        search_key_stdin=True,
    )
    assert code == 1
    assert "one key per run" in capsys.readouterr().err
