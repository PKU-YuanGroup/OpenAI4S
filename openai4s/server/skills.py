"""Web Customize service for user-authored skill documents.

This service intentionally stays separate from ``openai4s.host.skills``.  The
Web Customize API writes whole user ``SKILL.md`` documents and uses soft
dictionaries for domain errors, while the in-kernel host service has a richer
file editor, origin transitions, and permission behavior.
"""

from __future__ import annotations

import re
import shutil
from typing import Any

from openai4s.skills_loader import SkillLoader


class SkillCustomizationService:
    """Own user-skill CRUD, import, catalog projection, and UI enablement."""

    def __init__(self, loader: SkillLoader) -> None:
        self.loader = loader
        self.disabled_names: set[str] = set()

    @staticmethod
    def slug(name: str) -> str:
        value = re.sub(
            r"[^a-z0-9_-]+",
            "-",
            (name or "").strip().lower(),
        ).strip("-")
        return value[:64] or "skill"

    @staticmethod
    def parse_document(content: str) -> tuple[dict, str]:
        from openai4s.skills_loader.loader import _parse_frontmatter

        try:
            return _parse_frontmatter(content)
        except Exception:  # noqa: BLE001 - malformed imports keep their raw body
            return {}, content

    def create_or_update(
        self,
        name: str,
        description: str,
        body: str,
        *,
        existing: bool = False,
    ) -> dict:
        name = (name or "").strip()
        if not name:
            return {"error": "skill name is required"}
        slug = self.slug(name)

        # Discovery gives bundled skills precedence, so reject a new user skill
        # that would otherwise be written successfully and then ignored.
        try:
            if not existing and (self.loader.skills_dir / slug).is_dir():
                return {
                    "error": f"'{slug}' collides with a built-in skill — "
                    "pick a different name"
                }
        except Exception:  # noqa: BLE001 - preserve the legacy soft collision check
            pass

        user_directory = self.loader.user_skills_dir()
        user_directory.mkdir(parents=True, exist_ok=True)
        user_directory = user_directory.resolve()
        root = user_directory / slug
        if root.is_symlink():
            return {"error": "unsafe user skill path"}
        root.mkdir(parents=True, exist_ok=True)
        root = root.resolve()
        if root == user_directory or not root.is_relative_to(user_directory):
            return {"error": "unsafe user skill path"}
        document = root / "SKILL.md"
        if document.is_symlink():
            return {"error": "unsafe user skill path"}
        description = " ".join((description or "").split())
        frontmatter = (
            f"---\nname: {name}\ndescription: {description}\n"
            "origin: user\n---\n\n"
        )
        document.write_text(
            frontmatter + (body or "").strip() + "\n",
            "utf-8",
        )
        self.loader.discover()
        return {"ok": True, "name": name, "slug": slug, "origin": "user"}

    def import_document(
        self,
        *,
        content: str = "",
        name: str = "",
        description: str = "",
        body: str = "",
    ) -> dict:
        content = content or ""
        name = name or ""
        description = description or ""
        body = body or ""
        if content and not body:
            metadata, parsed_body = self.parse_document(content)
            name = name or metadata.get("name") or ""
            description = description or metadata.get("description") or ""
            body = parsed_body
        return self.create_or_update(name, description, body)

    def get(self, name: str) -> dict:
        for skill in self.loader.skills().values():
            if skill.name == name or skill.root.name == name:
                _metadata, body = self.parse_document(
                    (skill.root / "SKILL.md").read_text("utf-8")
                )
                return {
                    "name": skill.name,
                    "description": skill.description,
                    "body": body,
                    "origin": skill.origin,
                    "editable": skill.origin == "user",
                }
        return {"error": "skill not found"}

    def delete(self, name: str) -> dict:
        user_directory = self.loader.user_skills_dir().resolve()
        for skill in self.loader.skills().values():
            if skill.name == name or skill.root.name == name:
                if skill.root.is_symlink():
                    return {"error": "unsafe user skill path"}
                root = skill.root.resolve()
                if root != user_directory and root.is_relative_to(user_directory):
                    shutil.rmtree(root, ignore_errors=True)
                    self.loader.discover()
                    return {"ok": True}
                return {"error": "only user-authored skills can be deleted"}
        return {"error": "skill not found"}

    def set_enabled(self, name: str, enabled: Any) -> dict:
        if enabled:
            self.disabled_names.discard(name)
        else:
            self.disabled_names.add(name)
        return {"ok": True}

    def catalog(self, disabled: set[str] | None = None) -> list[dict]:
        disabled_names = self.disabled_names if disabled is None else disabled
        try:
            catalog = self.loader.catalog()
        except Exception:  # noqa: BLE001 - Customize degrades to an empty catalog
            return []

        output = []
        for item in catalog:
            name = item.get("name") if isinstance(item, dict) else str(item)
            origin = item.get("origin") if isinstance(item, dict) else None
            output.append(
                {
                    "name": name,
                    "displayName": (
                        item.get("displayName") or item.get("title") or name
                    )
                    if isinstance(item, dict)
                    else name,
                    "description": (item.get("description") or "")
                    if isinstance(item, dict)
                    else "",
                    "origin": origin,
                    "editable": origin == "user",
                    "enabled": name not in disabled_names,
                }
            )
        return output


__all__ = ["SkillCustomizationService"]
