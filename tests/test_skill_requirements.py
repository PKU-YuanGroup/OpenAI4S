"""What a Skill needs before it can run, and whether this machine has it.

Fourteen bundled Skills have declared `requirements: [gpu]` in their frontmatter
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
    _REQUIREMENT_CHECKS,
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


def test_a_known_missing_requirement_outranks_an_unknowable_one(monkeypatch):
    """`needs_setup` is the more actionable answer, so it wins."""
    monkeypatch.setitem(_REQUIREMENT_CHECKS, "gpu", lambda: False)
    mixed = skill_readiness(("gpu", "quantum-annealer"))
    assert mixed["state"] == NEEDS_SETUP
    assert mixed["missing"] == ["gpu"]
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
        assert row["readiness"]["probed"] is False
        assert "blocked_on" in row["readiness"]
        assert "ready" in row
        assert row["capabilities"]["network"]["mode"] in {
            "none",
            "host_only",
            "raw_required",
            "unknown",
        }


def test_readiness_does_not_spawn_a_process_per_skill(monkeypatch):
    """`nvidia-smi` is looked for on PATH, not executed.

    Running it would make rendering a catalogue spawn one subprocess per Skill
    — one per bundled Skill — which is the kind of cost that only
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


def test_strict_import_validator_rejects_what_the_historical_reader_degrades():
    """parse_network_frontmatter must keep degrading illegal modes so a
    third-party Skill still loads. The import gate is a new entry."""
    from openai4s.skills_loader.capabilities import (
        InvalidSkillCapability,
        parse_network_frontmatter,
        validate_network_frontmatter_strict,
    )

    raw = (
        "---\nname: x\ncapabilities:\n  network:\n    mode: warp\n"
        "    domains:\n      - example.org\n---\nbody\n"
    )
    degraded = parse_network_frontmatter(raw)
    assert degraded is not None
    assert degraded.mode == "unknown"
    assert degraded.declaration == "unknown"
    with pytest.raises(InvalidSkillCapability, match="mode"):
        validate_network_frontmatter_strict(raw)
    absent = "---\nname: x\ndescription: d\n---\nbody\n"
    assert validate_network_frontmatter_strict(absent) is None
    assert parse_network_frontmatter(absent) is None


def test_imported_gpu_skill_catalog_readiness_is_not_a_boolean(tmp_path):
    from openai4s.config import Config
    from openai4s.server.skills import SkillCustomizationService

    bundled = tmp_path / "bundled-skills"
    bundled.mkdir()
    config = Config(data_dir=tmp_path / "data", skills_dir=bundled)
    service = SkillCustomizationService(SkillLoader(cfg=config))
    imported = service.import_document(
        content=(
            "---\nname: needs-gpu\ndescription: gpu recipe\norigin: draft\n"
            "requirements: [gpu]\n"
            "capabilities:\n  network:\n    mode: none\n    domains: []\n"
            "license: MIT\ncategory: analysis\n"
            "# keep\n"
            "x-vendor-ext:\n  nested:\n    keep: me\n---\n\n# body\n"
        )
    )
    assert imported["ok"] is True
    row = next(item for item in service.catalog() if item["name"] == "needs-gpu")
    assert "gpu" in row["requirements"]
    assert row["capabilities"]["network"]["mode"] == "none"
    assert row["readiness"]["state"] in (READY, NEEDS_SETUP, UNKNOWN)
    assert row["readiness"]["checked_locally"] is True
    assert row["readiness"]["probed"] is False
    assert imported["review"]["readiness"]["state"] == row["readiness"]["state"]


def test_flow_style_network_mapping_keeps_every_domain():
    """`network: {mode: host_only, domains: [a, b]}` is one declaration.

    The inline parser split the mapping on every comma before it looked at
    the bracketed list, so the second domain vanished and the first kept its
    `[` -- and the strict import gate certified that allowlist. The block
    spelling of the same declaration parsed correctly, so two YAML spellings
    of one declaration produced different allowlists.
    """
    from openai4s.skills_loader.capabilities import (
        parse_network_frontmatter,
        validate_network_frontmatter_strict,
    )

    raw = (
        "---\n"
        "name: flow-net\n"
        "description: d\n"
        "capabilities:\n"
        "  network: {mode: host_only, domains: [a.example, b.example]}\n"
        "---\n"
        "body\n"
    )
    strict = validate_network_frontmatter_strict(raw)
    tolerant = parse_network_frontmatter(raw)
    assert strict is not None and tolerant is not None
    assert strict.mode == "host_only"
    assert list(strict.domains) == ["a.example", "b.example"]
    assert list(tolerant.domains) == ["a.example", "b.example"]
