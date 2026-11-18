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
