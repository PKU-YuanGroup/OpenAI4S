"""What a loaded Skill's network limit looks like to the user and the model.

A ``host_only`` manifest holds every later Host request in the session to its
declared domains. That is a real change to what the session can do, so it has
to be said where people and the model look, not discovered one refused
request at a time.
"""

from __future__ import annotations

import pytest

from openai4s.config import Config
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


def test_loading_a_host_only_skill_puts_the_limit_on_the_timeline_once(tmp_path):
    disp, steps = _dispatcher(tmp_path, "frame-notice")
    with disp.bind_sandbox_status(_ENFORCED):
        loaded = disp("load_skill", [{"name": "literature-review"}])
    assert loaded.get("name") == "literature-review", loaded
    domains = list(SkillLoader().get("literature-review").network.domains)

    notices = [
        step
        for step in steps
        if step.get("phase") == "end"
        and (step.get("output") or {}).get("notice") == "skill_network"
    ]
    assert len(notices) == 1
    notice = notices[0]
    assert notice["status"] == "done"
    assert notice["output"]["skill"] == "literature-review"
    assert notice["output"]["skills"] == ["literature-review"]
    assert notice["output"]["domains"] == sorted(domains)
    assert notice["summary"] == f"limited to {len(domains)} domains"
    begin = next(step for step in steps if step.get("step_id") == notice["step_id"])
    assert begin["kind"] == "network"

    # The load card itself says what it did to the session.
    load_end = [
        step
        for step in steps
        if step.get("phase") == "end"
        and "session_network" in (step.get("output") or {})
    ]
    assert len(load_end) == 1
    assert load_end[0]["summary"] == (
        f"loaded · network limited to {len(domains)} domains"
    )
    assert loaded["session_network"] == {
        "skills": ["literature-review"],
        "domains": sorted(domains),
    }

    # A second load binds the same manifest again and announces nothing new.
    steps.clear()
    with disp.bind_sandbox_status(_ENFORCED):
        disp("load_skill", [{"name": "literature-review"}])
    assert not [
        step
        for step in steps
        if (step.get("output") or {}).get("notice") == "skill_network"
    ]
    assert len(bindings_for("frame-notice")) == 2
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
    assert head.startswith("[Tool: load_skill]\n[network] Loading this Skill limits")
    assert "eutils.ncbi.nlm.nih.gov" in head
    assert "approving it cannot widen the limit" in head
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


def _lit_session(tmp_path, monkeypatch, frame_id: str):
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


def test_a_command_the_skill_limit_refuses_never_asks_for_approval(
    tmp_path, monkeypatch
):
    """The approval card read "allowed" beside the refusal; now nobody is asked."""

    disp, steps, recorder = _lit_session(tmp_path, monkeypatch, "frame-bash")
    with disp.bind_sandbox_status(_ENFORCED):
        refused = disp(
            "authorize_bash",
            [_bash_proposal("curl -s https://www.nejm.org/doi/full/10.1056/x")],
        )
    assert recorder.asked == []
    assert set(refused) == {"error"}
    reason = refused["error"]
    assert "does not declare destination 'www.nejm.org'" in reason
    assert "literature-review Skill limits this session's network" in reason
    assert "eutils.ncbi.nlm.nih.gov" in reason
    assert "approving it cannot widen" in reason

    end = next(step for step in steps if step.get("phase") == "end")
    assert end["status"] == "error"
    blocked = end["output"]["blocked"]
    assert blocked["by"] == "skill_network"
    assert blocked["destination"] == "www.nejm.org"
    assert blocked["skills"] == ["literature-review"]
    assert "eutils.ncbi.nlm.nih.gov" in blocked["domains"]
    assert blocked["search"] is False

    # A declared destination still goes to the user for approval as before.
    with disp.bind_sandbox_status(_ENFORCED):
        asked = disp(
            "authorize_bash",
            [_bash_proposal("curl -s https://eutils.ncbi.nlm.nih.gov/entrez/x")],
        )
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
    refused = disp(method, [spec])
    assert recorder.asked == []
    assert set(refused) == {"error"}
    assert "does not declare destination 'www.nejm.org'" in refused["error"]
    disp.store.close()


def test_the_cell_hears_the_hosts_reason_not_authorization_unavailable():
    from openai4s.sdk.bash import BashExecutor

    reason = (
        "skill host_only manifest does not declare destination 'www.nejm.org': "
        "the loaded literature-review Skill limits this session's network"
    )

    def worker_host_call(method, _args):
        # How the worker's Host channel raises a soft-fail answer.
        raise RuntimeError(f"host.{method} error: {reason}")

    executor = BashExecutor(worker_host_call, generation="worker:test")
    with pytest.raises(RuntimeError) as refused:
        executor.run("curl -s https://www.nejm.org/x")
    assert str(refused.value) == f"bash: {reason}; command was not executed"

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
    with frame_scope("frame-search"):
        with pytest.raises(EgressBlocked) as refused:
            webtools.web_search("GLP-1 Alzheimer cognition", engines=("you", "builtin"))
    reason = str(refused.value)
    assert reason.startswith(
        "skill host_only manifest does not declare any enabled search engine "
        "(You.com, DuckDuckGo/Bing/Mojeek)"
    )
    assert (
        "the loaded text-features Skill limits this session's network to 1 domain"
        in reason
    )
    assert "retrying will not help" in reason


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
    with frame_scope("frame-search-lit"):
        result = webtools.web_search("GLP-1 Alzheimer", engines=("you", "builtin"))
    assert result["count"] == 1 and result["source"] == "duckduckgo"


def test_the_refused_search_card_is_marked_blocked(tmp_path, monkeypatch):
    disp, steps = _dispatcher(tmp_path, "frame-search-card")
    _no_network(monkeypatch)
    _bind_narrow_skill("frame-search-card", ["api.typesafe.ai"])
    result = disp("web_search", [{"query": "GLP-1 Alzheimer cognition"}])
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
