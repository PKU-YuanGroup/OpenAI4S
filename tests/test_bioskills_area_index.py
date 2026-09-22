"""The generated bioSkills area index must cover the current collection exactly."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INDEX_PATH = REPO / "openai4s" / "judgment" / "templates" / "bioskills_areas.json"
MANIFEST_PATH = REPO / "skills" / "bioskills" / "MANIFEST.json"
BIOSKILLS_ROOT = REPO / "skills" / "bioskills"
MAX_AREA_MEMBERS = 254
REGEN = "run `uv run python scripts/build_bioskills_area_index.py` to regenerate"


def _declared_name(skill_md: Path, directory: str) -> str:
    text = skill_md.read_text("utf-8") if skill_md.is_file() else ""
    if not text.startswith("---"):
        return directory
    end = text.find("\n---", 3)
    if end < 0:
        return directory
    for line in text[3:end].splitlines():
        if line.startswith("name:"):
            name = line.split(":", 1)[1].strip().strip('"').strip("'")
            return name or directory
    return directory


def current_bioskills_names() -> set[str]:
    names: set[str] = set()
    for child in BIOSKILLS_ROOT.iterdir():
        if not child.is_dir() or not child.name.startswith("bio-"):
            continue
        names.add(_declared_name(child / "SKILL.md", child.name))
    return names


def test_index_covers_every_bioskills_member_exactly() -> None:
    assert INDEX_PATH.is_file(), REGEN
    payload = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    areas = payload.get("areas") or {}
    indexed: list[str] = []
    for area_id, info in areas.items():
        members = list(info.get("members") or [])
        assert (
            len(members) <= MAX_AREA_MEMBERS
        ), f"area {area_id!r} has {len(members)} members (>{MAX_AREA_MEMBERS}); {REGEN}"
        indexed.extend(str(name) for name in members)
    indexed_set = set(indexed)
    current = current_bioskills_names()
    extra = sorted(indexed_set - current)
    missing = sorted(current - indexed_set)
    assert not extra and not missing, (
        f"area index members drifted from skills/bioskills/; extra={extra[:8]} "
        f"missing={missing[:8]}; {REGEN}"
    )
    assert len(indexed) == len(indexed_set), f"duplicate members in area index; {REGEN}"


def test_source_manifest_sha256_matches_current_manifest() -> None:
    payload = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    expected = hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest()
    actual = payload.get("source_manifest_sha256")
    assert actual == expected, (
        f"source_manifest_sha256 {actual!r} does not match {MANIFEST_PATH} "
        f"({expected}); {REGEN}"
    )


def test_index_is_deterministic() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_bioskills_area_index",
        REPO / "scripts" / "build_bioskills_area_index.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rebuilt = module.dumps_index(module.build_index())
    committed = INDEX_PATH.read_text(encoding="utf-8")
    assert rebuilt == committed, (
        "rebuilding the area index produced different JSON; " + REGEN
    )
