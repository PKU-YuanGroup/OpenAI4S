"""Direct contracts for the class that owns host skill behaviour."""

from __future__ import annotations

from pathlib import Path

import pytest

from openai4s.config import Config
from openai4s.host.skills import SkillService


def _service(tmp_path: Path) -> SkillService:
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "spectral").mkdir()
    (skills / "spectral" / "SKILL.md").write_text(
        "---\n"
        "name: spectral\n"
        "description: spectral signal analysis\n"
        "origin: draft\n"
        "---\n"
        "# Spectral\nUse Fourier analysis.\n",
        "utf-8",
    )
    (skills / "vendor").mkdir()
    (skills / "vendor" / "SKILL.md").write_text(
        "---\nname: vendor\ndescription: bundled\norigin: openai4s\n---\n",
        "utf-8",
    )
    return SkillService(Config(data_dir=tmp_path / "data", skills_dir=skills))


def test_skill_service_keeps_load_and_lookup_failure_contracts(tmp_path):
    service = _service(tmp_path)

    loaded = service.load("Fourier signal")
    assert loaded["name"] == "spectral"
    assert "Fourier analysis" in loaded["content"]
    assert service.load("no matching quantum lattice skill") == {
        "error": "no such skill: 'no matching quantum lattice skill'"
    }
    with pytest.raises(KeyError, match="no such skill"):
        service.get("missing")


def test_skill_service_owns_path_confinement_read_only_and_sidecar_gate(tmp_path):
    service = _service(tmp_path)

    with pytest.raises(ValueError, match="escapes skill dir"):
        service.edit(
            {
                "name": "spectral",
                "path": "../escape.txt",
                "content": "escaped",
            }
        )
    assert not (tmp_path / "skills" / "escape.txt").exists()

    for operation in (
        lambda: service.edit(
            {"name": "vendor", "path": "SKILL.md", "content": "changed"}
        ),
        lambda: service.publish("vendor"),
        lambda: service.delete("vendor"),
    ):
        with pytest.raises(PermissionError, match="read-only"):
            operation()

    broken = service.edit(
        {
            "name": "demo",
            "path": "kernel.py",
            "content": "def broken(x)\n    return x\n",
        }
    )
    assert broken["sidecar_gate"]["ok"] is False
    fixed = service.edit(
        {
            "name": "demo",
            "path": "kernel.py",
            "content": "def broken(x):\n    return x\n",
        }
    )
    assert fixed["sidecar_gate"] == {"ok": True, "error": None}


def test_skill_service_refreshes_catalog_after_publish_and_delete(tmp_path):
    service = _service(tmp_path)
    service.edit(
        {
            "name": "demo",
            "path": "SKILL.md",
            "content": (
                "---\nname: demo\ndescription: demo skill\n"
                "origin: draft\n---\n# Demo\n"
            ),
        }
    )

    assert service.publish("demo") == {"ok": True, "origin": "personal"}
    assert service.get("demo")["origin"] == "personal"
    assert "demo" in {item["name"] for item in service.list()}
    assert service.delete("demo") == {"ok": True, "deleted": "demo"}
    assert "demo" not in {item["name"] for item in service.list()}
