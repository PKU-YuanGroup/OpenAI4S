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

from openai4s.skills_loader import SkillLoader, SkillVersionService


class SkillCustomizationService:
    """Own user-skill CRUD, import, catalog projection, and UI enablement."""

    def __init__(
        self,
        loader: SkillLoader,
        *,
        scope: str = "personal",
        project_id: str | None = None,
        versions: SkillVersionService | None = None,
    ) -> None:
        self.scope = str(scope or "personal").strip().lower()
        self.project_id = str(project_id or "").strip() or None
        if self.scope not in {"personal", "project"}:
            raise ValueError("skill scope must be 'personal' or 'project'")
        if self.scope == "project" and not self.project_id:
            raise ValueError("project skill scope requires project_id")
        self.loader = (
            loader.scoped(project_id=self.project_id)
            if self.scope == "project" and hasattr(loader, "scoped")
            else loader
        )
        cfg = getattr(self.loader, "cfg", None)
        self.versions = versions or (
            SkillVersionService(cfg) if cfg is not None else None
        )
        try:
            self.disabled_names = self.loader.capabilities.disabled_names("skill")
        except Exception:  # noqa: BLE001 - compatibility with simple test doubles
            self.disabled_names: set[str] = set()

    def _all_skills(self):
        try:
            return self.loader.skills(include_disabled=True)
        except TypeError:
            return self.loader.skills()

    def _find_skill(self, name: str):
        for skill in self._all_skills().values():
            if skill.name == name or skill.root.name == name:
                return skill
        return None

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

        existing_skill = self._find_skill(name) if existing else None
        if (
            self.scope == "project"
            and existing_skill is not None
            and getattr(existing_skill, "source", None) != "project"
        ):
            # A project edit creates/updates its overlay; it must never mutate
            # the personal fallback that happened to satisfy discovery.
            existing_skill = None

        # Discovery gives bundled skills precedence, so reject a new user skill
        # that would otherwise be written successfully and then ignored. Check
        # both its directory slug and its declared canonical identity.
        try:
            collision = self.loader.bundled_name_collision(
                existing_skill.name if existing_skill is not None else name
            )
            if collision is not None:
                return {
                    "error": f"'{slug}' collides with a built-in skill — "
                    "pick a different name"
                }
            if not existing and (self.loader.skills_dir / slug).is_dir():
                return {
                    "error": f"'{slug}' collides with a built-in skill — "
                    "pick a different name"
                }
        except Exception:  # noqa: BLE001 - preserve the legacy soft collision check
            pass

        user_directory = (
            self.versions.scope_root(scope=self.scope, project_id=self.project_id)
            if self.versions is not None
            else self.loader.user_skills_dir()
        )
        if user_directory.is_symlink():
            return {"error": "unsafe user skill path"}
        user_directory.mkdir(parents=True, exist_ok=True)
        user_directory = user_directory.resolve()
        root = (
            existing_skill.root if existing_skill is not None else user_directory / slug
        )
        if root.is_symlink():
            return {"error": "unsafe user skill path"}
        root = root.resolve()
        if root == user_directory or not root.is_relative_to(user_directory):
            return {"error": "unsafe user skill path"}
        if self.versions is None:
            root.mkdir(parents=True, exist_ok=True)
        document = root / "SKILL.md"
        if document.is_symlink():
            return {"error": "unsafe user skill path"}
        description = " ".join((description or "").split())
        document_name = existing_skill.name if existing_skill is not None else name
        origin = (
            existing_skill.origin
            if existing_skill is not None
            and existing_skill.origin in {"draft", "personal"}
            else "user"
        )
        frontmatter = (
            f"---\nname: {document_name}\ndescription: {description}\n"
            f"origin: {origin}\n---\n\n"
        )
        content = frontmatter + (body or "").strip() + "\n"
        if self.versions is not None:
            try:
                files = self.versions.read_package(root) if document.exists() else {}
                files["SKILL.md"] = content.encode("utf-8")
                self.versions.install(
                    document_name,
                    files,
                    scope=self.scope,
                    project_id=self.project_id,
                    event="upgraded" if existing_skill is not None else "installed",
                    slug=root.name,
                    require_sidecar_gate=False,
                    metadata={"source": "web_customize"},
                )
            except (OSError, ValueError, PermissionError, RuntimeError) as error:
                message = str(error)
                if "unsafe" in message.lower() or "symlink" in message.lower():
                    return {"error": "unsafe user skill path"}
                return {"error": message or "skill version update failed"}
        else:
            document.write_text(content, "utf-8")
        self.loader.discover()
        return {
            "ok": True,
            "name": document_name,
            "slug": root.name,
            "origin": origin,
        }

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
        skill = self._find_skill(name)
        if skill is not None:
            _metadata, body = self.parse_document(
                (skill.root / "SKILL.md").read_text("utf-8")
            )
            return {
                "name": skill.name,
                "description": skill.description,
                "body": body,
                "origin": skill.origin,
                "editable": not skill.read_only,
            }
        return {"error": "skill not found"}

    def delete(self, name: str) -> dict:
        user_directory = (
            self.versions.scope_root(scope=self.scope, project_id=self.project_id)
            if self.versions is not None
            else self.loader.user_skills_dir()
        ).resolve()
        for skill in self._all_skills().values():
            if skill.name == name or skill.root.name == name:
                if skill.root.is_symlink():
                    return {"error": "unsafe user skill path"}
                root = skill.root.resolve()
                if root != user_directory and root.is_relative_to(user_directory):
                    if self.versions is not None:
                        installation = self.versions.repository.get_installation(
                            skill.name,
                            scope=self.scope,
                            scope_id=self.project_id or "",
                        )
                        if installation is not None and installation.get(
                            "active_version_id"
                        ):
                            self.versions.delete(
                                skill.name,
                                scope=self.scope,
                                project_id=self.project_id,
                            )
                        else:
                            shutil.rmtree(root, ignore_errors=True)
                    else:
                        shutil.rmtree(root, ignore_errors=True)
                    self.loader.discover()
                    return {"ok": True}
                return {"error": "only user-authored skills can be deleted"}
        return {"error": "skill not found"}

    def set_enabled(self, name: str, enabled: Any) -> dict:
        state = self.loader.set_enabled(
            name,
            bool(enabled),
            scope="project" if self.scope == "project" else "global",
            scope_id=self.project_id,
        )
        canonical = str(state.get("name") or name)
        if enabled:
            self.disabled_names.discard(canonical)
            self.disabled_names.discard(name)
        else:
            self.disabled_names.add(canonical)
        return {"ok": True}

    def catalog(self, disabled: set[str] | None = None) -> list[dict]:
        disabled_names = self.disabled_names if disabled is None else disabled
        try:
            catalog = self.loader.catalog(include_disabled=True)
        except Exception:  # noqa: BLE001 - Customize degrades to an empty catalog
            return []

        try:
            editable = {
                skill.name: not skill.read_only for skill in self._all_skills().values()
            }
        except Exception:  # noqa: BLE001 - preserve compatibility with test doubles
            editable = {}
        output = []
        for item in catalog:
            name = item.get("name") if isinstance(item, dict) else str(item)
            origin = item.get("origin") if isinstance(item, dict) else None
            distribution = (
                item.get("distribution_scope") if isinstance(item, dict) else None
            )
            item_scope = (
                "bundled"
                if distribution == "bundled"
                else "project"
                if distribution == "project"
                else "personal"
            )
            installation = None
            if self.versions is not None and item_scope == self.scope:
                try:
                    installation = self.versions.repository.get_installation(
                        name,
                        scope=self.scope,
                        scope_id=self.project_id or "",
                    )
                except (KeyError, ValueError):
                    installation = None
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
                    "scope": item_scope,
                    "editable": editable.get(name, origin == "user"),
                    "enabled": name not in disabled_names,
                    "versioned": bool(installation),
                    "activeVersionId": (
                        installation.get("active_version_id")
                        if installation is not None
                        else None
                    ),
                }
            )
        return output

    def status(self, name: str) -> dict:
        skill = self._find_skill(name)
        if skill is not None and skill.read_only:
            return {
                "name": skill.name,
                "scope": "bundled",
                "installed": True,
                "active": True,
                "active_version_id": None,
                "read_only": True,
                "rollback_available": False,
            }
        if self.versions is None:
            return {"error": "skill version storage is unavailable"}
        return {
            **self.versions.status(
                name,
                scope=self.scope,
                project_id=self.project_id,
            ),
            "read_only": False,
        }

    def history(self, name: str, *, limit: int = 200) -> dict:
        """Return immutable install/upgrade/publish/rollback events."""

        if self.versions is None:
            return {"error": "skill version storage is unavailable"}
        try:
            return self.versions.history(
                name,
                scope=self.scope,
                project_id=self.project_id,
                limit=limit,
            )
        except KeyError:
            return {"error": "skill has no version history"}

    def rollback(self, name: str, version_id: str) -> dict:
        """Activate a prior version without deleting newer immutable history."""

        skill = self._find_skill(name)
        if skill is not None and skill.read_only:
            return {"error": "built-in skills are read-only"}
        if self.versions is None:
            return {"error": "skill version storage is unavailable"}
        try:
            result = self.versions.rollback(
                name,
                version_id,
                scope=self.scope,
                project_id=self.project_id,
            )
        except (KeyError, PermissionError, ValueError, RuntimeError) as error:
            return {"error": str(error)}
        self.loader.discover()
        return result


__all__ = ["SkillCustomizationService"]
