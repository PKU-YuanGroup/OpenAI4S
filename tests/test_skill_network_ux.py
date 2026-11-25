"""What a loaded Skill's network limit looks like to the user and the model.

A ``host_only`` manifest holds the requests that Skill's OWN helper code (its
``kernel.py`` sidecar) makes to its declared domains (owner decision 34). The
rest of the session -- native tools, the agent's own Cell code -- keeps its
network policy. Both halves have to be said where people and the model look:
a model told only "limited" spent twenty minutes working around a limit that
never applied to its own requests.
"""

from __future__ import annotations

import re

import pytest

from openai4s.config import Config
from openai4s.sdk.host import CALLER_SKILL_MARKER
from openai4s.server.skill_network_admission import (
    bindings_for,
    domain_in_declared,
    reset_bindings,
)
from openai4s.skills_loader import SkillLoader

_ENFORCED = {
    "enforced": True,
    "self_test_passed": True,
    "network_policy": "blocked",
    "backend": "seatbelt",
    "state": "enforced",
    "mode": "auto",
}


def _hosts(text: str) -> set[str]:
    """The host names a message lists, each an exact token (not a substring)."""
    return set(re.findall(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", text))


@pytest.fixture(autouse=True)
def _clean_bindings():
    reset_bindings()
    yield
    reset_bindings()


def _dispatcher(tmp_path, frame_id: str):
    from openai4s.host_dispatch import build_dispatcher

    disp = build_dispatcher(Config(data_dir=tmp_path / "data"))
    disp.frame_id = frame_id
    steps: list[dict] = []
    disp.on_step = steps.append
    return disp, steps


def test_literature_review_declares_the_services_its_recipe_sends_the_model_to():
    """SKILL.md says "a PubMed query, web_search"; the manifest has to agree.

    It declared six hosts and none of them was the PubMed API or a search
    engine, so loading the Skill switched both off for the session.
    """

    from openai4s.egress import EGRESS_GROUPS

    lit = SkillLoader().get("literature-review")
    assert lit is not None and lit.network.mode == "host_only"
    declared = lit.network.domains
    for host in (
        "eutils.ncbi.nlm.nih.gov",
        "clinicaltrials.gov",
        "www.clinicaltrials.gov",
        "api.crossref.org",
        "api.openalex.org",
    ):
        assert domain_in_declared(host, declared), host
    search = next(group for group in EGRESS_GROUPS if group["name"] == "Web search")
    engines = {*search.get("domains", ()), *search.get("exact_domains", ())}
    missing = sorted(host for host in engines if not domain_in_declared(host, declared))
    assert missing == []
    # The concrete hosts the built-in chain and the arXiv-id route connect to.
    for host in ("www.bing.com", "html.duckduckgo.com", "www.mojeek.com"):
        assert domain_in_declared(host, declared), host
    assert domain_in_declared("export.arxiv.org", declared)


def _notices(steps: list[dict]) -> list[dict]:
    return [
        step
        for step in steps
        if step.get("phase") == "end"
        and (step.get("output") or {}).get("notice") == "skill_network"
    ]


def test_loading_a_host_only_skill_says_so_on_one_card(tmp_path):
    """SKL-4: the load card and a notice card used to say the same thing."""

    disp, steps = _dispatcher(tmp_path, "frame-notice")
    with disp.bind_sandbox_status(_ENFORCED):
        loaded = disp("load_skill", [{"name": "literature-review"}])
    assert loaded.get("name") == "literature-review", loaded
    domains = list(SkillLoader().get("literature-review").network.domains)

    # The load card itself says what the Skill's helpers may reach ...
    load_end = [
        step
        for step in steps
        if step.get("phase") == "end"
        and "session_network" in (step.get("output") or {})
    ]
    assert len(load_end) == 1
    assert loaded["session_network"] == {
        "skills": ["literature-review"],
        "domains": sorted(domains),
    }
    # ... so no second card repeats it.
    assert _notices(steps) == []
    assert not [step for step in steps if step.get("kind") == "network"]

    # A second load binds the same manifest again and announces nothing new.
    steps.clear()
    with disp.bind_sandbox_status(_ENFORCED):
        disp("load_skill", [{"name": "literature-review"}])
    assert _notices(steps) == []
    assert len(bindings_for("frame-notice")) == 2
    disp.store.close()


def test_a_binding_without_a_load_card_puts_the_limit_on_the_timeline_once(
    tmp_path,
):
    """A Skill file read binds the manifest; its card says nothing about it."""

    disp, steps = _dispatcher(tmp_path, "frame-read-notice")
    with disp.bind_sandbox_status(_ENFORCED):
        read = disp("skills_read", [{"name": "literature-review"}])
    assert isinstance(read, str) and "# Literature review" in read
    domains = list(SkillLoader().get("literature-review").network.domains)

    notices = _notices(steps)
    assert len(notices) == 1
    notice = notices[0]
    assert notice["status"] == "done"
    assert notice["output"]["skill"] == "literature-review"
    assert notice["output"]["skills"] == ["literature-review"]
    assert notice["output"]["domains"] == sorted(domains)
    assert notice["summary"] == f"helpers limited to {len(domains)} domains"
    # No built-in connector is "lost": the limit is on the Skill's helpers.
    assert "unavailable_connectors" not in notice["output"]
    begin = next(step for step in steps if step.get("step_id") == notice["step_id"])
    assert begin["kind"] == "network"
    assert begin["title"] == "literature-review limits its own helpers' network"

    steps.clear()
    with disp.bind_sandbox_status(_ENFORCED):
        disp("skills_read", [{"name": "literature-review"}])
    assert _notices(steps) == []
    disp.store.close()


def test_a_skill_without_a_network_limit_loads_without_a_notice(tmp_path):
    disp, steps = _dispatcher(tmp_path, "frame-plain")
    loaded = disp("load_skill", [{"name": "example_stats"}])
    assert loaded.get("name") == "example_stats", loaded
    assert "session_network" not in loaded
    assert not [step for step in steps if step.get("kind") == "network"]
    ends = [step for step in steps if step.get("phase") == "end"]
    assert [step["summary"] for step in ends] == ["loaded"]
    disp.store.close()


def test_the_model_reads_the_limit_before_the_recipe(tmp_path):
    from openai4s.tools.registry import format_tool_result, get_tool

    disp, _steps = _dispatcher(tmp_path, "frame-model")
    with disp.bind_sandbox_status(_ENFORCED):
        loaded = disp("load_skill", [{"name": "literature-review"}])
    text = format_tool_result(get_tool("load_skill"), loaded)
    head = text.split("\n\n", 1)[0]
    assert head.startswith("[Tool: load_skill]\n[network] This Skill's own helpers")
    assert {"eutils.ncbi.nlm.nih.gov"} <= _hosts(head)
    assert "approving it cannot widen that limit" in head
    # And what is NOT limited, so the model does not work around a limit
    # that never applied to its own requests.
    assert "The rest of this session's network is unchanged" in head
    assert "science_search" in head
    assert "# Literature review" in text
    disp.store.close()


# --- SEC-6: refused before an approval card, with the real reason -----------


class _RecordingBroker:
    """A permission broker that records whether anyone was asked."""

    def __init__(self) -> None:
        self.asked: list[dict] = []

    def gate(self, **kwargs):
        self.asked.append(kwargs)
        return {"allow": False, "message": "denied in test"}


def _lit_session(tmp_path, monkeypatch, frame_id: str, *, record: bool = True):
    """A session with literature-review loaded; ``record`` swaps in a broker
    that records and refuses every approval (the seeded rules decide when
    it is off)."""

    from openai4s.host_dispatch import build_dispatcher
    from openai4s.store import get_store

    cfg = Config(data_dir=tmp_path / "data")
    store = get_store(cfg.db_path)
    store.seed_default_permission_rules()
    frame = store.new_frame(kind="turn")
    disp = build_dispatcher(cfg, frame_id=frame)
    steps: list[dict] = []
    disp.on_step = steps.append
    recorder = _RecordingBroker()
    if record:
        monkeypatch.setattr("openai4s.permissions.broker", lambda: recorder)
    with disp.bind_sandbox_status(_ENFORCED):
        loaded = disp("load_skill", [{"name": "literature-review"}])
    assert loaded.get("name") == "literature-review", loaded
    steps.clear()
    return disp, steps, recorder


def _bash_proposal(command: str) -> dict:
    return {
        "command": command,
        "cwd": "/",
        "challenge": "c" * 24,
        "generation": "worker:test",
        "timeout": 60,
    }


#: How a call from literature-review's kernel.py reaches the dispatcher.
_FROM_LIT_HELPER = {CALLER_SKILL_MARKER: "literature-review"}


def test_a_command_the_skill_limit_refuses_never_asks_for_approval(
    tmp_path, monkeypatch
):
    """The approval card read "allowed" beside the refusal; now nobody is asked."""

    disp, steps, recorder = _lit_session(tmp_path, monkeypatch, "frame-bash")
    with disp.bind_sandbox_status(_ENFORCED):
        refused = disp(
            "authorize_bash",
            [
                _bash_proposal("curl -s https://www.nejm.org/doi/full/10.1056/x"),
                _FROM_LIT_HELPER,
            ],
        )
    assert recorder.asked == []
    assert set(refused) == {"error"}
    reason = refused["error"]
    assert "does not declare destination 'www.nejm.org'" in reason
    assert "the literature-review Skill's own helper code may only reach" in reason
    assert {"eutils.ncbi.nlm.nih.gov"} <= _hosts(reason)
    assert "approving it cannot widen" in reason
    assert "the rest of this session's network is unchanged" in reason

    end = next(step for step in steps if step.get("phase") == "end")
    assert end["status"] == "error"
    blocked = end["output"]["blocked"]
    assert blocked["by"] == "skill_network"
    assert blocked["destination"] == "www.nejm.org"
    assert blocked["skills"] == ["literature-review"]
    assert {"eutils.ncbi.nlm.nih.gov"} <= set(blocked["domains"])
    assert blocked["search"] is False

    # A declared destination still goes to the user for approval as before.
    with disp.bind_sandbox_status(_ENFORCED):
        asked = disp(
            "authorize_bash",
            [
                _bash_proposal("curl -s https://eutils.ncbi.nlm.nih.gov/entrez/x"),
                _FROM_LIT_HELPER,
            ],
        )
    assert len(recorder.asked) == 1
    assert asked == {"error": "Permission denied: denied in test"}
    disp.store.close()


def test_the_agents_own_command_keeps_the_sessions_network(tmp_path, monkeypatch):
    """Owner decision 34: the Skill's limit is not the session's."""

    disp, _steps, recorder = _lit_session(tmp_path, monkeypatch, "frame-own-bash")
    with disp.bind_sandbox_status(_ENFORCED):
        asked = disp(
            "authorize_bash",
            [_bash_proposal("curl -s https://data.rcsb.org/rest/v1/core/entry/6EQE")],
        )
    # Put to the user as any shell command is, not refused by the Skill.
    assert len(recorder.asked) == 1
    assert asked == {"error": "Permission denied: denied in test"}
    disp.store.close()


@pytest.mark.parametrize(
    ("method", "spec"),
    [
        ("web_download", {"url": "https://www.nejm.org/a.pdf", "path": "a.pdf"}),
        ("request_network_access", {"domain": "www.nejm.org", "reason": "paper"}),
    ],
)
def test_a_network_request_the_skill_limit_refuses_is_not_put_to_the_user(
    tmp_path, monkeypatch, method, spec
):
    disp, _steps, recorder = _lit_session(tmp_path, monkeypatch, f"frame-{method}")
    refused = disp(method, [spec, _FROM_LIT_HELPER])
    assert recorder.asked == []
    assert set(refused) == {"error"}
    assert "does not declare destination 'www.nejm.org'" in refused["error"]
    # The same request from the agent's own code goes to the user as usual.
    asked = disp(method, [spec])
    assert len(recorder.asked) == 1
    assert asked == {"error": "Permission denied: denied in test"}
    disp.store.close()


def _fetch_through_egress(monkeypatch) -> list[str]:
    """Stand in for the network: run the egress check, record what passed."""

    from openai4s import egress, webtools

    passed: list[str] = []

    def fetch(url, **_kwargs):
        egress.check_url(url)
        passed.append(url)
        return {"url": url, "content": "{}", "format": "json"}

    monkeypatch.setattr(webtools, "web_fetch", fetch)
    return passed


def test_native_requests_after_loading_the_skill_keep_the_sessions_network(
    tmp_path, monkeypatch
):
    """The review session: literature AND PDB 6EQE in one request (SKL-2)."""

    disp, _steps, _recorder = _lit_session(
        tmp_path, monkeypatch, "frame-native", record=False
    )
    passed = _fetch_through_egress(monkeypatch)
    entry = "https://data.rcsb.org/rest/v1/core/entry/6EQE"
    fetched = disp("web_fetch", [{"url": entry, "format": "json"}])
    assert "does not declare" not in str(fetched)
    assert entry in passed
    searched = disp("science_search", [{"database": "pdb", "query": "6EQE"}])
    assert "does not declare" not in str(searched)
    assert any("rcsb.org" in url for url in passed if url != entry)
    disp.store.close()


def test_a_helper_call_from_the_skills_kernel_module_is_held_to_its_manifest(
    tmp_path, monkeypatch
):
    """Through the real SDK: the sidecar on the stack is what attributes."""

    import types

    from openai4s.sdk.host import build_host

    disp, _steps, _recorder = _lit_session(
        tmp_path, monkeypatch, "frame-sidecar", record=False
    )
    passed = _fetch_through_egress(monkeypatch)
    host = build_host(lambda method, args: disp(method, args), mode="python")
    sidecar = types.ModuleType("literature-review.kernel")
    exec(  # noqa: S102 - a stand-in for the Skill's kernel.py
        "def fetch(host, url):\n    return host.web_fetch(url, format='json')\n",
        sidecar.__dict__,
    )
    undeclared = sidecar.fetch(host, "https://data.rcsb.org/rest/v1/core/entry/6EQE")
    assert set(undeclared) == {"error"}
    assert "does not declare destination 'data.rcsb.org'" in undeclared["error"]
    declared = sidecar.fetch(host, "https://api.openalex.org/works/W1")
    assert "error" not in declared
    # The agent's own code, through the same host object, is not limited.
    own = host.web_fetch("https://data.rcsb.org/rest/v1/core/entry/6EQE")
    assert "error" not in own
    assert passed == [
        "https://api.openalex.org/works/W1",
        "https://data.rcsb.org/rest/v1/core/entry/6EQE",
    ]
    disp.store.close()


def test_load_still_refuses_a_host_only_skill_on_an_unconfined_kernel(tmp_path):
    """Owner decision 34 keeps the load-time posture admission as it was."""

    disp, steps = _dispatcher(tmp_path, "frame-unconfined")
    unconfined = {
        "enforced": False,
        "self_test_passed": False,
        "network_policy": "not_enforced",
        "backend": None,
    }
    with disp.bind_sandbox_status(unconfined):
        refused = disp("load_skill", [{"name": "literature-review"}])
    assert set(refused) == {"error"}
    assert "does not confine the kernel to the Host path" in refused["error"]
    assert bindings_for("frame-unconfined") == ()
    disp.store.close()


def test_the_cell_hears_the_hosts_reason_not_authorization_unavailable():
    from openai4s.sdk.bash import BashExecutor

    reason = (
        "skill host_only manifest does not declare destination 'www.nejm.org': "
        "the literature-review Skill's own helper code may only reach 18 domains"
    )

    def worker_host_call(method, _args):
        # How the worker's Host channel raises a soft-fail answer.
        raise RuntimeError(f"host.{method} error: {reason}")

    executor = BashExecutor(worker_host_call, generation="worker:test")
    with pytest.raises(RuntimeError) as refused:
        executor.run("curl -s https://www.nejm.org/x")
    assert str(refused.value) == f"bash: {reason}; command was not executed"

    def sentence(method, _args):
        raise RuntimeError(f"host.{method} error: {reason}. Use one of those domains.")

    with pytest.raises(RuntimeError) as worded:
        BashExecutor(sentence, generation="worker:test").run("ls")
    assert str(worded.value).endswith(
        "Use one of those domains; command was not executed"
    )

    def denied_by_user(method, _args):
        raise RuntimeError(f"host.{method} error: Permission denied: denied by user")

    with pytest.raises(RuntimeError) as denied:
        BashExecutor(denied_by_user, generation="worker:test").run("ls")
    assert "Permission denied: denied by user" in str(denied.value)

    def legacy(method, _args):
        raise RuntimeError(f"host.{method} error: unknown host method: {method!r}")

    with pytest.raises(RuntimeError) as unavailable:
        BashExecutor(legacy, generation="worker:test").run("ls")
    assert str(unavailable.value) == (
        "bash: host authorization unavailable; command was not executed"
    )


# --- SKL-3: a search the limit refuses is a refusal, not "0 results" --------


def _bind_narrow_skill(frame_id: str, domains: list[str]) -> None:
    from openai4s.server.skill_network_admission import bind_skill_load
    from openai4s.skills_loader.capabilities import declared_capability

    bind_skill_load(
        frame_id=frame_id,
        action_group_id=None,
        skill_id="text-features",
        version="v",
        document_digest="d",
        capability=declared_capability("host_only", domains, source="frontmatter"),
        source="load_skill",
    )


def _no_network(monkeypatch):
    from openai4s import webtools

    def refuse(*_args, **_kwargs):
        raise AssertionError("a refused search must not open a connection")

    monkeypatch.setattr(webtools, "_http_get", refuse)
    monkeypatch.setattr(webtools, "_post_json", refuse)


def test_a_search_no_engine_can_serve_is_refused_not_empty(monkeypatch):
    from openai4s import webtools
    from openai4s.egress import EgressBlocked
    from openai4s.server.skill_network_admission import frame_scope

    _no_network(monkeypatch)
    _bind_narrow_skill("frame-search", ["api.typesafe.ai"])
    with frame_scope("frame-search", caller_skill="text-features"):
        with pytest.raises(EgressBlocked) as refused:
            webtools.web_search("GLP-1 Alzheimer cognition", engines=("you", "builtin"))
    reason = str(refused.value)
    assert reason.startswith(
        "skill host_only manifest does not declare any enabled search engine "
        "(You.com, DuckDuckGo/Bing/Mojeek)"
    )
    assert "the text-features Skill's own helper code may only reach 1 domain" in reason
    assert "will not help" in reason
    assert "the web_search tool instead" in reason


def test_a_search_from_the_agents_own_code_is_not_limited(monkeypatch):
    from openai4s import webtools
    from openai4s.server.skill_network_admission import frame_scope

    _no_network(monkeypatch)
    _bind_narrow_skill("frame-search-own", ["api.typesafe.ai"])
    hit = [{"title": "t", "url": "https://example.org/p", "snippet": "s"}]
    monkeypatch.setattr(
        webtools, "_builtin_search", lambda *_a, **_k: (hit, "duckduckgo", None)
    )
    monkeypatch.setattr(webtools, "_you_search", lambda *_a, **_k: [])
    with frame_scope("frame-search-own"):
        result = webtools.web_search("GLP-1 Alzheimer", engines=("you", "builtin"))
    assert result["count"] == 1


def test_engines_a_skill_declares_still_search(monkeypatch):
    """literature-review now declares the engine hosts, so search keeps working."""

    from openai4s import webtools
    from openai4s.server.skill_network_admission import frame_scope

    _no_network(monkeypatch)
    lit = SkillLoader().get("literature-review")
    _bind_narrow_skill("frame-search-lit", list(lit.network.domains))
    hit = [{"title": "t", "url": "https://example.org/p", "snippet": "s"}]
    monkeypatch.setattr(
        webtools, "_builtin_search", lambda *_a, **_k: (hit, "duckduckgo", None)
    )
    monkeypatch.setattr(webtools, "_you_search", lambda *_a, **_k: [])
    with frame_scope("frame-search-lit", caller_skill="text-features"):
        result = webtools.web_search("GLP-1 Alzheimer", engines=("you", "builtin"))
    assert result["count"] == 1 and result["source"] == "duckduckgo"


def test_the_refused_search_card_is_marked_blocked(tmp_path, monkeypatch):
    disp, steps = _dispatcher(tmp_path, "frame-search-card")
    _no_network(monkeypatch)
    _bind_narrow_skill("frame-search-card", ["api.typesafe.ai"])
    result = disp(
        "web_search",
        [
            {"query": "GLP-1 Alzheimer cognition"},
            {CALLER_SKILL_MARKER: "text-features"},
        ],
    )
    assert set(result) == {"error"}
    assert "does not declare any enabled search engine" in result["error"]
    end = next(step for step in steps if step.get("phase") == "end")
    assert end["status"] == "error"
    assert end["summary"].startswith(
        "failed: skill host_only manifest does not declare any enabled search engine"
    )
    blocked = end["output"]["blocked"]
    assert blocked == {
        "by": "skill_network",
        "skills": ["text-features"],
        "domains": ["api.typesafe.ai"],
        "destination": "",
        "search": True,
    }
    disp.store.close()


def test_attribution_only_ever_narrows():
    """A forged or missing marker cannot widen past the session's policy."""

    from openai4s.sdk.host import attribute_caller, split_caller_skill

    # No sidecar on the stack: nothing is attached.
    assert attribute_caller([{"url": "x"}]) == [{"url": "x"}]
    assert split_caller_skill([{"url": "x"}]) == ([{"url": "x"}], None)
    # A marker is stripped wherever it came from; a malformed one names nothing.
    assert split_caller_skill([{"url": "x"}, {CALLER_SKILL_MARKER: 3}]) == (
        [{"url": "x"}],
        None,
    )
    # A Skill nobody bound limits nothing.
    _bind_narrow_skill("frame-forged", ["api.typesafe.ai"])
    from openai4s.server.skill_network_admission import (
        constrain_check_url,
        frame_scope,
    )

    with frame_scope("frame-forged", caller_skill="no-such-skill"):
        assert constrain_check_url("https://data.rcsb.org/x") is None
    with frame_scope("frame-forged", caller_skill="text-features"):
        assert constrain_check_url("https://data.rcsb.org/x") == "data.rcsb.org"
        # A nested request with no caller is the session's again.
        with frame_scope("frame-forged"):
            assert constrain_check_url("https://data.rcsb.org/x") is None
