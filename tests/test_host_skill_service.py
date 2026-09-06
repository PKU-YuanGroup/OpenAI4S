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
    assert loaded["capabilities"]["network"]["mode"] == "unknown"
    assert loaded["network_manifest_digest"]
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
                "name": "demo",
                "path": "../escape.txt",
                "content": "escaped",
            }
        )
    assert not (tmp_path / "skills" / "escape.txt").exists()

    # Bundled-root ownership, not a forgeable frontmatter origin, controls
    # mutation. Both the openai4s and draft declarations remain immutable.
    for name in ("vendor", "spectral"):
        for operation in (
            lambda name=name: service.edit(
                {"name": name, "path": "SKILL.md", "content": "changed"}
            ),
            lambda name=name: service.publish(name),
            lambda name=name: service.delete(name),
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


def test_host_edit_rejects_declared_name_collision_with_bundled_skill(tmp_path):
    service = _service(tmp_path)

    with pytest.raises(PermissionError, match="collides with read-only bundled"):
        service.edit(
            {
                "name": "innocent-directory",
                "path": "SKILL.md",
                "content": (
                    "---\nname:  VENDOR \ndescription: forged alias\n"
                    "origin: draft\n---\n# Not vendor\n"
                ),
            }
        )

    assert not (
        service.loader.user_skills_dir() / "innocent-directory" / "SKILL.md"
    ).exists()


@pytest.mark.parametrize("first_path", ["SKILL.md", "kernel.py"])
def test_host_edit_reserves_collection_root_import_names(tmp_path, first_path):
    service = _service(tmp_path)
    collection = service.cfg.skills_dir / "collection_root"
    member = collection / "member"
    member.mkdir(parents=True)
    (collection / "COLLECTION.json").write_text(
        '{"id":"collection","prompt_line":"collection: {count}"}\n',
        "utf-8",
    )
    (member / "SKILL.md").write_text(
        "---\nname: member\ndescription: member\n---\nbody\n",
        "utf-8",
    )

    with pytest.raises(PermissionError, match="collides with read-only bundled"):
        service.edit(
            {
                "name": "collection_root",
                "path": first_path,
                "content": (
                    "---\nname: collection_root\ndescription: collision\n---\nbody\n"
                    if first_path == "SKILL.md"
                    else "VALUE = 1\n"
                ),
            }
        )

    assert not (service.loader.user_skills_dir() / "collection_root").exists()

    # Direct filesystem installation is outside the Host authoring path, but
    # discovery must still give the importable collection root precedence.
    shadow = service.loader.user_skills_dir() / "collection_root"
    shadow.mkdir(parents=True)
    (shadow / "SKILL.md").write_text(
        "---\nname: shadow\ndescription: shadow\n---\nbody\n", "utf-8"
    )
    (shadow / "kernel.py").write_text("VALUE = 1\n", "utf-8")
    assert "collection_root" not in service.loader.discover()


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
