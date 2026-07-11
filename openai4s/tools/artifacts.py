"""Artifact metadata control tools; scientific file production stays in code."""

from __future__ import annotations

from openai4s.tools.base import Tool
from openai4s.tools.contexts import ControlToolContext


class ListArtifactsTool(Tool):
    """Search versioned Artifact metadata without reading arbitrary SQL."""

    name = "list_artifacts"
    host_method = "artifacts"
    description = "List or search versioned artifacts and their metadata."
    parameters = {
        "properties": {
            "search": {"type": "string", "minLength": 1},
            "artifact_id": {"type": "string", "minLength": 1},
            "root_frame_id": {"type": "string", "minLength": 1},
            "project_id": {"type": "string", "minLength": 1},
            "filename": {"type": "string", "minLength": 1},
            "content_type": {"type": "string", "minLength": 1},
        },
        "required": [],
    }
    requires_approval = False
    resource_key_prefix = "artifact"
    resource_target_default = "catalog"

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        filters = {
            key: value for key, value in arguments.items() if value not in (None, "")
        }
        return runtime.invoke(self.host_method, filters)


class SaveArtifactTool(Tool):
    """Register an existing workspace file as a versioned Artifact."""

    name = "save_artifact"
    host_method = "save_artifact"
    description = "Register an existing workspace file as a versioned artifact."
    parameters = {
        "properties": {
            "path": {
                "type": "string",
                "minLength": 1,
                "description": "Existing workspace file to register.",
            },
            "filename": {"type": "string", "minLength": 1},
            "content_type": {"type": "string", "minLength": 1},
            "input_version_ids": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "maxItems": 500,
            },
            "priority": {"type": "integer", "minimum": 0, "maximum": 100},
        },
        "required": ["path"],
    }
    read_only = False
    permission_target_key = "path"
    secret_path_key = "path"
    side_effect_class = "external_write"
    resource_key_prefix = "artifact"
    resource_target_key = "path"

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        return runtime.invoke(self.host_method, dict(arguments))


__all__ = ["ListArtifactsTool", "SaveArtifactTool"]
