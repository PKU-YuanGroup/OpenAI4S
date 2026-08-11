"""What a Skill needs before it can run, and whether this machine has it.

Thirteen bundled Skills have declared `requirements: [gpu]` in their frontmatter
since they were written, and nothing read it — not the loader, not the Skill
object, not the catalogue row. So a GPU-only Skill looked exactly like one that
runs anywhere, and the agent found out the difference at execution time, deep
into a task, after the user had already waited.

Readiness is answered from local state alone. Browsing a catalogue is not a
request to contact anything, and the report says so: `浏览不联网`.
"""

from __future__ import annotations

import pytest

from openai4s.config import get_config
from openai4s.skills_loader.loader import (
    NEEDS_SETUP,
    READY,
    UNKNOWN,
    SkillLoader,
    _requirements,
    skill_readiness,
)


def test_requirements_survive_the_frontmatter(tmp_path):
    """They were parsed into a dict and then dropped on the floor."""
    loader = SkillLoader(cfg=get_config())
    loader.discover()
    gpu_skill = loader.get("boltz")
    if gpu_skill is None:
        pytest.skip("bundled boltz skill not present")
    assert gpu_skill.requirements == ("gpu",)
    assert loader.get("literature-review").requirements == ()


def test_the_three_spellings_that_actually_appear_all_parse():
    """Rejecting a Skill over its punctuation would hide a real Skill for a
    cosmetic reason, so a YAML list, a comma string and a bare word all work.
    """
    assert _requirements("[gpu]") == ("gpu",)
    assert _requirements(["gpu"]) == ("gpu",)
    assert _requirements("gpu, r") == ("gpu", "r")
    assert _requirements("GPU") == ("gpu",)  # normalised


def test_an_unparseable_requirement_claims_nothing():
    """Inventing a requirement is worse than missing one: it sends a user to
    install something they may not need, and there is no way for them to tell
    the claim is fabricated."""
    assert _requirements(42) == ()
    assert _requirements(None) == ()
    assert _requirements({}) == ()


def test_readiness_distinguishes_missing_from_unknowable():
    """Three states, and the third one matters.

    A requirement nobody knows how to check must not be guessed in either
    direction: `ready` invites a failure deep into a task, and `needs_setup`
    sends a user to install something that may already be there. `unknown`
    says which it is.
    """
    assert skill_readiness(())["state"] == READY

    unknowable = skill_readiness(("quantum-annealer",))
    assert unknowable["state"] == UNKNOWN
    assert unknowable["unverifiable"] == ["quantum-annealer"]
    assert unknowable["missing"] == []


def test_a_known_missing_requirement_outranks_an_unknowable_one():
    """`needs_setup` is the more actionable answer, so it wins."""
    mixed = skill_readiness(("gpu", "quantum-annealer"))
    assert mixed["state"] in (NEEDS_SETUP, READY)  # depends on this host's GPU
    if mixed["missing"]:
        assert mixed["state"] == NEEDS_SETUP
        assert mixed["unverifiable"] == ["quantum-annealer"]


def test_browsing_the_catalogue_never_reaches_the_network(monkeypatch):
    """The load-bearing property, asserted by making egress an error.

    A user scrolling a Skill list is not asking to contact anything, and a
    readiness check that grew a probe would turn rendering a catalogue into
    outbound traffic — the same implicit call P0-1 removed, and the same
    mistake the model readiness card avoids.
    """
    import urllib.request

    def _explode(*args, **kwargs):
        raise AssertionError("browsing the skill catalogue made a network call")

    monkeypatch.setattr(urllib.request, "urlopen", _explode)
    loader = SkillLoader(cfg=get_config())
    loader.discover()
    rows = loader.catalog()
    assert rows, "no skills discovered"
    for row in rows:
        assert row["readiness"]["checked_locally"] is True


def test_readiness_does_not_spawn_a_process_per_skill(monkeypatch):
    """`nvidia-smi` is looked for on PATH, not executed.

    Running it would make rendering a catalogue spawn one subprocess per Skill
    — thirty-four of them on this repo — which is the kind of cost that only
    shows up on a slow machine and is then blamed on something else.
    """
    import subprocess

    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran"))
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")),
    )
    for _ in range(3):
        skill_readiness(("gpu",))


def test_readiness_is_not_enabledness():
    """A disabled Skill can be perfectly ready and an enabled one can be
    missing its hardware. Folding the two together means a user who enables a
    Skill believes they have made it work."""
    loader = SkillLoader(cfg=get_config())
    loader.discover()
    rows = {row["name"]: row for row in loader.catalog()}
    gpu_row = rows.get("boltz")
    if gpu_row is None:
        pytest.skip("bundled boltz skill not present")
    assert "enabled" in gpu_row and "readiness" in gpu_row
    assert gpu_row["readiness"]["state"] in (READY, NEEDS_SETUP, UNKNOWN)
    # Enabled says nothing about whether it can run.
    assert gpu_row["enabled"] is True
