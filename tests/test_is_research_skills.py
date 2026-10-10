"""Offline discovery, resource and attribution contracts for the IS additions."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

import pytest

from openai4s.skills_loader import SkillLoader

pytestmark = pytest.mark.skills

NAMES = ("is-research-design", "is-survey-measurement", "is-qualitative-analysis")


@pytest.mark.parametrize(
    ("name", "research_request"),
    [
        (
            "is-research-design",
            "Develop an information systems theory and design science evaluation "
            "for a research assistant, with constructs and rival explanations.",
        ),
        (
            "is-survey-measurement",
            "Evaluate ordinal survey items measuring trust: CFA, SEM, "
            "psychometrics and measurement invariance across groups.",
        ),
        (
            "is-qualitative-analysis",
            "Analyze interview transcripts using a qualitative codebook or "
            "reflexive thematic analysis and assess coding agreement.",
        ),
    ],
)
def test_research_requests_find_the_relevant_bundled_recipe(name, research_request):
    loader = SkillLoader()
    hits = loader.search(research_request, limit=5)
    assert name in [hit["name"] for hit in hits]
    skill = loader.skills()[name]
    assert skill.read_only
    assert skill.network.mode == "none"
    assert skill.sidecar_gate()["ok"]


@pytest.mark.parametrize("name", NAMES)
def test_runtime_references_are_readable_inside_the_installed_skill(name):
    loader = SkillLoader()
    skill = loader.skills()[name]
    documents = [skill.root / "SKILL.md"]
    documents.extend(
        path
        for path in skill.root.rglob("*.md")
        if path.name not in {"SKILL.md", "README.md", "README_zh.md"}
    )
    for document in documents:
        text = document.read_text("utf-8")
        for target in re.findall(r"\[[^\]]*\]\(([^\s)]+)\)", text):
            if "://" in target or target.startswith("#"):
                continue
            relative = target.split("#", 1)[0]
            resolved = (document.parent / relative).resolve()
            resource = resolved.relative_to(skill.root.resolve())
            assert loader.read(name, resource.as_posix())
        for code in re.findall(r"```python\n(.*?)\n```", text, flags=re.S):
            ast.parse(code, filename=str(document))


@pytest.mark.parametrize("name", NAMES)
def test_third_party_adaptations_retain_pinned_sources_and_exact_license(name):
    loader = SkillLoader()
    record = json.loads(loader.read(name, "UPSTREAM.json"))
    assert record["license"] == "MIT"
    assert record["repository"].startswith("https://github.com/")
    assert re.fullmatch(r"[a-f0-9]{40}", record["commit"])
    assert record["adaptations"]
    license_bytes = loader.read(name, "LICENSE").encode("utf-8")
    assert b"Copyright" in license_bytes
    license_sources = []
    root = loader.skills()[name].root
    for source in record["sources"]:
        assert re.fullmatch(r"[a-f0-9]{64}", source["sha256"])
        assert source["path"]
        for target in source["adapted_paths"]:
            path = (root / target).resolve()
            path.relative_to(root.resolve())
            assert path.is_file()
        if "LICENSE" in source["adapted_paths"]:
            license_sources.append(source)
    assert len(license_sources) == 1
    assert hashlib.sha256(license_bytes).hexdigest() == license_sources[0]["sha256"]
