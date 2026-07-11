"""Tests for the 6 methodology-only bundled skills.

These skills have no executable kernel (except paper-narrative's weak helper),
so replication == porting the prompt/recipe text. Two layers of proof:

 1. STATIC (always runs): SkillLoader discovers each one, marks it read-only
    (origin: openai4s), and the lexical search_skills route retrieves it.
 2. LIVE AGENT LOOP (opt-in via OPENAI4S_LIVE_LLM=1): the real Code-as-Action agent,
    given a task, autonomously calls host.search_skills, pulls the recipe into
    context, and submits a structured answer — the only meaningful "runs"
    signal for a text-only skill. Costs real LLM tokens, hence gated.
"""
import os
import sqlite3

import pytest

from openai4s.config import Config
from openai4s.skills_loader import SkillLoader

METHODOLOGY = [
    "paper-narrative",
    "indication-dossier",
    "remote-compute-ssh",
    "using-model-endpoint",
]

# (search query, expected skill retrieved in top-3)
PROBES = [
    ("reshape the story a paper's figures tell, hook verdict arc", "paper-narrative"),
    (
        "therapeutic indication dossier epidemiology standard of care",
        "indication-dossier",
    ),
    (
        "submit slurm ssh remote compute wait for notification harvest",
        "remote-compute-ssh",
    ),
    (
        "call a registered model endpoint native http BASE_URL inference",
        "using-model-endpoint",
    ),
]


@pytest.fixture
def loader():
    ld = SkillLoader(cfg=Config())
    ld.discover()
    return ld


@pytest.mark.parametrize("name", METHODOLOGY)
def test_methodology_skill_is_discovered_read_only(loader, name):
    s = loader.get(name)
    assert s is not None, f"{name} not discovered"
    assert s.origin == "openai4s" and s.read_only is True
    assert s.description
    # only paper-narrative ships a (weak) kernel helper; the rest are text-only
    assert s.has_kernel is (name == "paper-narrative")
    assert s.sidecar_gate()["ok"] is True


@pytest.mark.parametrize("query,expect", PROBES)
def test_methodology_skill_is_retrievable(loader, query, expect):
    res = loader.search(query, limit=3)
    assert any(
        r["name"] == expect for r in res
    ), f"{expect} not in top-3 for {query!r}: {[r['name'] for r in res]}"


# --- live agent-loop proof (opt-in) --------------------------------------

_LIVE = os.environ.get("OPENAI4S_LIVE_LLM") == "1"


def _searched(db_path) -> list[str]:
    raw = sqlite3.connect(str(db_path))
    rows = raw.execute(
        "SELECT args_preview FROM host_call_log " "WHERE method='search_skills'"
    ).fetchall()
    raw.close()
    return [r[0] for r in rows]


@pytest.mark.external
@pytest.mark.live_llm
@pytest.mark.skipif(
    not _LIVE, reason="set OPENAI4S_LIVE_LLM=1 to run the live agent loop"
)
@pytest.mark.parametrize(
    "task,expect",
    [
        (
            "You MUST NOT answer from memory. FIRST call "
            "host.search_skills('remote SLURM SSH submit wait harvest') in a code cell, "
            "read the returned skill recipe, THEN submit the ordered workflow steps it "
            "prescribes.",
            "remote-compute-ssh",
        ),
    ],
)
def test_methodology_skill_used_in_agent_loop(tmp_path, monkeypatch, task, expect):
    from openai4s.agent import Agent

    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path))
    cfg = Config(data_dir=tmp_path)
    cfg.ensure_dirs()
    result = Agent(cfg=cfg, max_turns=8).run(task)
    full = "\n".join(t["content"] for t in result["transcript"])
    assert result["stop_reason"] in ("submitted", "max_turns")
    assert _searched(cfg.db_path), "agent never called search_skills"
    assert expect in full, f"{expect} recipe was not retrieved into context"
    assert result["submitted_output"] is not None
