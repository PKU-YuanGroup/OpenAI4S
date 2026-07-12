"""Progressive-disclosure Skill control tools."""

from __future__ import annotations

from typing import Any

from openai4s.tools.base import Tool
from openai4s.tools.contexts import ControlToolContext
from openai4s.tools.taxonomy import RUNTIME_MUTATION, resource_key


class SearchSkillsTool(Tool):
    """Retrieve full recipes only when a task needs them."""

    name = "search_skills"
    host_method = "search_skills"
    description = "Find relevant Skills and load their full recipes on demand."
    parameters = {
        "properties": {
            "query": {
                "type": "string",
                "minLength": 1,
                "description": "Keywords describing the needed method or workflow.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "description": "Maximum matching recipes to return (default 5).",
            },
        },
        "required": ["query"],
    }
    requires_approval = False
    resource_key_prefix = "skill"
    resource_target_key = "query"

    def execute(self, runtime: ControlToolContext, arguments: dict) -> list:
        return runtime.invoke(
            self.host_method,
            {
                "query": arguments.get("query", ""),
                "limit": int(arguments.get("limit") or 5),
            },
        )


class LoadSkillTool(Tool):
    """Load one exact/fuzzy Skill document through the scoped loader."""

    name = "load_skill"
    host_method = "load_skill"
    description = "Load one Skill's complete SKILL.md guidance by name."
    parameters = {
        "properties": {
            "name": {
                "type": "string",
                "minLength": 1,
                "description": "Skill name from the available-skill catalog.",
            }
        },
        "required": ["name"],
    }
    requires_approval = False
    resource_key_prefix = "skill"
    resource_target_key = "name"

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        return runtime.invoke(self.host_method, str(arguments.get("name") or ""))


class SkillStatusTool(Tool):
    """Inspect one exact personal/project Skill activation without reading bytes."""

    name = "skill_status"
    host_method = "skills_status"
    description = "Inspect the active version and safe manifest for one Skill scope."
    parameters = {
        "properties": {
            "name": {
                "type": "string",
                "minLength": 1,
                "description": "Exact declared Skill name.",
            },
            "scope": {
                "type": "string",
                "enum": ["personal", "project"],
                "description": "Personal library or the current project overlay.",
            },
        },
        "required": ["name", "scope"],
    }
    requires_approval = False
    resource_key_prefix = "skill"
    resource_target_key = "name"

    def resource_keys(self, arguments: Any) -> tuple[str, ...]:
        arguments = arguments if isinstance(arguments, dict) else {}
        target = (
            f"{arguments.get('scope') or 'personal'}/{arguments.get('name') or '*'}"
        )
        return (resource_key("skill", target),)

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        return runtime.invoke(
            self.host_method,
            {
                "name": str(arguments.get("name") or ""),
                "scope": str(arguments.get("scope") or ""),
            },
        )


class SkillHistoryTool(SkillStatusTool):
    """List immutable Skill versions and lifecycle events without source bytes."""

    name = "skill_history"
    host_method = "skills_history"
    description = "List immutable versions and install/publish/rollback events."
    parameters = {
        "properties": {
            **SkillStatusTool.parameters["properties"],
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 200,
                "description": "Maximum lifecycle events to return (default 50).",
            },
        },
        "required": ["name", "scope"],
    }
    provider_strict = False

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        return runtime.invoke(
            self.host_method,
            {
                "name": str(arguments.get("name") or ""),
                "scope": str(arguments.get("scope") or ""),
                "limit": int(arguments.get("limit") or 50),
            },
        )


class RollbackSkillVersionTool(SkillStatusTool):
    """Human-approved pointer change to a retained immutable Skill version."""

    name = "rollback_skill_version"
    host_method = "skills_rollback"
    description = (
        "Roll back a writable personal/project Skill to a retained version. "
        "Bundled Skills are immutable."
    )
    parameters = {
        "properties": {
            **SkillStatusTool.parameters["properties"],
            "version_id": {
                "type": "string",
                "minLength": 71,
                "maxLength": 71,
                "description": "Exact version_id returned by skill_history.",
            },
        },
        "required": ["name", "scope", "version_id"],
    }
    read_only = False
    requires_approval = True
    side_effect_class = RUNTIME_MUTATION

    def permission_target(self, arguments: Any) -> str:
        arguments = arguments if isinstance(arguments, dict) else {}
        return (
            f"{arguments.get('scope') or 'personal'}/"
            f"{arguments.get('name') or '*'}/"
            f"{arguments.get('version_id') or '*'}"
        )

    def native_precheck(self, arguments: dict) -> str | None:
        version_id = str(arguments.get("version_id") or "")
        digest = version_id.removeprefix("skillv-")
        if (
            not version_id.startswith("skillv-")
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            return "version_id must be 'skillv-' followed by 64 lowercase hex digits"
        return None

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        return runtime.invoke(
            self.host_method,
            {
                "name": str(arguments.get("name") or ""),
                "scope": str(arguments.get("scope") or ""),
                "version_id": str(arguments.get("version_id") or ""),
            },
        )


__all__ = [
    "LoadSkillTool",
    "RollbackSkillVersionTool",
    "SearchSkillsTool",
    "SkillHistoryTool",
    "SkillStatusTool",
]
