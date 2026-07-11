"""Progressive-disclosure Skill control tools."""

from __future__ import annotations

from typing import Any

from openai4s.tools.base import Tool
from openai4s.tools.contexts import ControlToolContext


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


__all__ = ["LoadSkillTool", "SearchSkillsTool"]
