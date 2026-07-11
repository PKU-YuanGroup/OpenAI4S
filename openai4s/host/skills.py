"""Host-side skill lifecycle behaviour.

The dispatcher remains the policy boundary (permissions, audit, UI activity,
soft failures).  This service owns the skill domain itself so retrieval and
filesystem mutation are visible in one class instead of being scattered across
``HostDispatcher``.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from openai4s.config import Config
from openai4s.skills_loader import SkillLoader


class SkillService:
    """Retrieve and manage Code-as-Action skill directories."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.loader = SkillLoader(cfg=cfg)

    def load(self, name: str | dict) -> dict:
        """Load full guidance, with the historical fuzzy-name fallback."""
        if isinstance(name, dict):
            name = name.get("name", "")
        self.loader.discover()
        skill = self.loader.get(name)
        if skill is None:
            hits = self.loader.search(name, limit=1)
            if hits:
                skill = self.loader.get(hits[0]["name"])
        if skill is None:
            return {"error": f"no such skill: {name!r}"}
        try:
            content = (skill.root / "SKILL.md").read_text("utf-8")
        except Exception:  # noqa: BLE001 - loader doc is the compatibility fallback
            content = getattr(skill, "doc", "") or ""
        return {
            "name": skill.name,
            "origin": skill.origin,
            "description": skill.description,
            "content": content,
        }

    def search(self, spec: dict) -> list:
        self.loader.discover()
        return self.loader.search(
            spec.get("query", ""), limit=int(spec.get("limit", 5))
        )

    def list(self) -> list:
        self.loader.discover()
        return self.loader.catalog()

    def get(self, name: str) -> dict:
        self.loader.discover()
        skill = self.loader.get(name)
        if skill is None:
            raise KeyError(f"no such skill: {name!r}")
        return {
            "name": skill.name,
            "origin": skill.origin,
            "description": skill.description,
            "has_kernel": skill.has_kernel,
            "read_only": skill.read_only,
            "sidecar_gate": skill.sidecar_gate(),
        }

    def read(self, spec: dict) -> str:
        self.loader.discover()
        skill = self.loader.get(spec["name"])
        if skill is None:
            raise KeyError(f"no such skill: {spec['name']!r}")
        path = self._safe_path(skill.root, spec.get("path", "SKILL.md"))
        return path.read_text("utf-8")

    def edit(self, spec: dict) -> dict:
        name = spec["name"]
        relative = spec.get("path", "SKILL.md")
        content = spec.get("content", "")
        old_string = spec.get("old_string")
        self.loader.discover()
        existing = self.loader.get(name)
        if existing is not None and existing.read_only:
            raise PermissionError(
                f"skill {name!r} origin={existing.origin} is read-only"
            )

        if existing is not None:
            root = existing.root
        else:
            root = self.cfg.skills_dir / name
            root.mkdir(parents=True, exist_ok=True)
            skill_md = root / "SKILL.md"
            if not skill_md.exists() and relative != "SKILL.md":
                skill_md.write_text(
                    f"---\nname: {name}\ndescription: (draft)\norigin: draft\n---\n"
                    f"# Skill: {name}\n",
                    "utf-8",
                )

        target = self._safe_path(root, relative)
        if old_string is None:
            target.write_text(content, "utf-8")
            mode = "overwrite"
        else:
            if not target.exists():
                raise FileNotFoundError(
                    f"{relative} does not exist for str_replace"
                )
            current = target.read_text("utf-8")
            if old_string not in current:
                raise ValueError("old_string not found in file")
            target.write_text(current.replace(old_string, content, 1), "utf-8")
            mode = "str_replace"

        result: dict[str, Any] = {
            "ok": True,
            "mode": mode,
            "path": str(target),
        }
        if target.name == "kernel.py":
            self.loader.discover()
            skill = self.loader.get(name)
            result["sidecar_gate"] = (
                skill.sidecar_gate()
                if skill
                else {"ok": True, "error": None}
            )
        return result

    def publish(self, name: str) -> dict:
        self.loader.discover()
        skill = self.loader.get(name)
        if skill is None:
            raise KeyError(f"no such skill: {name!r}")
        if skill.read_only:
            raise PermissionError(f"skill {name!r} is read-only")
        skill_md = skill.root / "SKILL.md"
        text = skill_md.read_text("utf-8")
        if text.startswith("---"):
            updated = re.sub(r"(?m)^origin:.*$", "origin: personal", text, count=1)
            if "origin:" not in text:
                updated = text.replace("---", "---\norigin: personal", 1)
        else:
            updated = f"---\nname: {name}\norigin: personal\n---\n" + text
        skill_md.write_text(updated, "utf-8")
        return {"ok": True, "origin": "personal"}

    def delete(self, name: str) -> dict:
        self.loader.discover()
        skill = self.loader.get(name)
        if skill is None:
            raise KeyError(f"no such skill: {name!r}")
        if skill.read_only:
            raise PermissionError(f"skill {name!r} is read-only")
        shutil.rmtree(skill.root)
        return {"ok": True, "deleted": name}

    @staticmethod
    def _safe_path(root: Path, relative: str) -> Path:
        root = root.resolve()
        target = (root / relative).resolve()
        if root != target and root not in target.parents:
            raise ValueError(f"path escapes skill dir: {relative!r}")
        return target


__all__ = ["SkillService"]
