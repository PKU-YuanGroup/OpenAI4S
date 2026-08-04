"""End-to-end integration test for the Code-as-Action loop.

Unlike test_agent.py (which mocks the kernel and runs with use_skills=False) and
test_kernel.py (which stubs the dispatcher), this exercises the WHOLE real stack
in one flow, offline:

    Agent(use_skills=True)
      -> real SkillLoader (system_context + bootstrap_code)
      -> real Kernel subprocess (JSON-per-line protocol, host_call RPC)
      -> real HostDispatcher (search_skills, submit_output, SQLite store)
      -> skill sidecar imported inside the kernel and called across cells

Only the LLM is scripted, so the run is fully offline. This is the path where a
destructive find/replace hid: the two `self._skill_loader.system_context` /
`.bootstrap_code` method-call parens were dropped, and NOTHING in the old suite
caught it because every agent test used use_skills=False. This test does.
"""

from pathlib import Path

import pytest

import openai4s.agent.loop as loop_mod
from openai4s.agent import Agent
from openai4s.config import Config


class ScriptedLLM:
    """Returns queued replies in order; records the messages it was handed so a
    test can assert what actually reached the model (e.g. the skills block)."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = []

    def __call__(self, messages, cfg, **kw):
        self.calls.append([dict(m) for m in messages])
        content = (
            self._replies.pop(0)
            if self._replies
            else ("```python\nhost.submit_output({}, ['Finished the task'])\n```")
        )
        return {
            "content": content,
            "reasoning": None,
            "usage": {},
            "finish_reason": "stop",
            "raw": {},
        }


_SKILL_MD = """\
---
name: teststats
description: descriptive statistics helpers (mean of a numeric series)
origin: personal
---
# teststats

Import the sidecar and call `mean`:

    from teststats.kernel import mean
    mean([1, 2, 3])
"""

_SKILL_KERNEL = '''\
"""teststats.kernel — importable sidecar for the e2e test."""
from __future__ import annotations


def mean(xs):
    if not xs:
        raise ValueError("empty series")
    return sum(xs) / len(xs)
'''


@pytest.fixture
def e2e_cfg(tmp_path: Path) -> Config:
    """A fully isolated config: private data_dir (SQLite/artifacts) + a private
    skills_dir holding one importable stats skill. Nothing touches ~/.openai4s."""
    data_dir = tmp_path / "data"
    skills_dir = tmp_path / "skills"
    skill = skills_dir / "teststats"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(_SKILL_MD, "utf-8")
    (skill / "kernel.py").write_text(_SKILL_KERNEL, "utf-8")

    cfg = Config(data_dir=data_dir, skills_dir=skills_dir)
    cfg.ensure_dirs()
    return cfg


def test_end_to_end_skill_flow(e2e_cfg, monkeypatch):
    """Full Code-as-Action cycle with skills enabled, real kernel + dispatcher.

    Turn 1: retrieve the skill, import its sidecar, compute (persists in ns).
    Turn 2: submit the structured output using the persisted function.
    """
    scripted = ScriptedLLM(
        [
            # Turn 1 — retrieve skill, import sidecar, compute. Exercises
            # host.search_skills (real dispatcher + loader + store) AND the kernel's
            # skills_dir bootstrap (the dropped `bootstrap_code()` paren).
            "Let me pull the stats skill and compute.\n"
            "```python\n"
            "hits = host.search_skills('descriptive statistics mean')\n"
            "print('skills:', [h['name'] for h in hits])\n"
            "from teststats.kernel import mean\n"
            "series = [2, 4, 6, 8]\n"
            "m = mean(series)\n"
            "print('mean=', m)\n"
            "```",
            # Turn 2 — submit using the namespace persisted from turn 1.
            "```python\n"
            "host.submit_output({'mean': m, 'n': len(series)}, "
            "['Computed the mean via the teststats skill'])\n"
            "```",
        ]
    )
    monkeypatch.setattr(loop_mod, "chat", scripted)

    agent = Agent(cfg=e2e_cfg, use_skills=True, allow_delegate=False)
    result = agent.run("Compute the mean of [2,4,6,8] and submit it.")

    # 1) The run completed via the structured host channel.
    assert result["stop_reason"] == "submitted"
    assert result["submitted_output"]["output"] == {"mean": 5.0, "n": 4}
    assert result["submitted_output"]["completion_bullets"] == [
        "Computed the mean via the teststats skill"
    ]

    # 2) Regression guard for the paren-deletion bug: system_context() must have
    #    been CALLED (not left as a bound method) so the skills block reached the
    #    model. With the bug, run() raised before the first chat() call.
    first_system = scripted.calls[0][0]
    assert first_system["role"] == "system"
    assert "Available skills" in first_system["content"]
    assert "teststats" in first_system["content"]

    # 3) The skill retrieval + sidecar import actually ran inside the kernel:
    #    the turn-1 observation carries the printed stdout.
    obs_turn1 = result["transcript"][1]["content"]
    assert "skills: ['teststats']" in obs_turn1
    assert "mean= 5.0" in obs_turn1

    # 4) The host_call round-trips were persisted to the real SQLite store.
    #    (host_call_log is denylisted for host.query, so read it directly.)
    import sqlite3

    conn = sqlite3.connect(str(e2e_cfg.db_path))
    try:
        methods = {row[0] for row in conn.execute("SELECT method FROM host_call_log")}
    finally:
        conn.close()
    assert {"search_skills", "submit_output"} <= methods


def test_end_to_end_kernel_error_is_observed(e2e_cfg, monkeypatch):
    """A cell that errors must be captured and fed back as an observation, then
    the agent recovers and submits — proving error attribution survives the full
    real-kernel path (not just the stubbed one in test_kernel.py)."""
    scripted = ScriptedLLM(
        [
            "```python\nraise ValueError('boom in cell')\n```",
            "```python\nhost.submit_output({'ok': True}, ['Recovered from the error'])\n```",
        ]
    )
    monkeypatch.setattr(loop_mod, "chat", scripted)

    agent = Agent(cfg=e2e_cfg, use_skills=True, allow_delegate=False)
    result = agent.run("trigger an error, then recover")

    assert result["stop_reason"] == "submitted"
    obs_turn1 = result["transcript"][1]["content"]
    assert "ValueError" in obs_turn1
    assert "boom in cell" in obs_turn1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
