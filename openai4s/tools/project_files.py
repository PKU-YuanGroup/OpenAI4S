"""Read selected project inputs and copy exact bytes into the session workspace."""

from __future__ import annotations

import hashlib
import os
from typing import Any, Callable

from openai4s.tools.base import Tool
from openai4s.tools.content_search import ContentSearchTool
from openai4s.tools.contexts import ControlToolContext
from openai4s.tools.glob_files import GlobFilesTool
from openai4s.tools.list_directory import ListDirectoryTool
from openai4s.tools.read_text_file import ReadTextFileTool
from openai4s.tools.taxonomy import WORKSPACE_WRITE, resource_key, workspace_target


def import_destination(arguments: dict) -> str:
    return str(
        arguments.get("path")
        or "project-inputs/" + str(arguments.get("source_path") or "")
    )


#: The workspace permission each project tool mirrors, so a new spelling cannot
#: bypass a standing file-read/write rule.
PROJECT_PERMISSION_ALIASES = {
    "project_list_dir": "list_dir",
    "project_read_file": "read_file",
    "project_glob": "glob",
    "project_grep": "grep",
    "project_import_file": "read_file",
}


def project_permission_checks(
    method: str,
    spec: dict,
    gate_target: Callable[[str, list[Any]], str],
) -> list[tuple[str, str]]:
    """The (permission, target) pairs a project call must also satisfy."""
    base_method = PROJECT_PERMISSION_ALIASES[method]
    source_path = str(
        spec.get("source_path" if method == "project_import_file" else "path") or "."
    )
    checks = [(base_method, gate_target(base_method, [{**spec, "path": source_path}]))]
    if base_method != "read_file":
        checks.append(("read_file", source_path))
    if method == "project_import_file":
        checks.append(("write_file", import_destination(spec)))
    return checks


def resolve_project_permissions(
    checks: list[tuple[str, str]],
    *,
    resolve: Callable[[str, str], str],
    child_decision: Callable[[str], str | None] | None,
) -> tuple[str | None, list[tuple[str, str]]]:
    """Return ``(refusal, asks)``: a hard refusal, or the prompts still owed."""
    asks: list[tuple[str, str]] = []
    for permission, target in checks:
        child = child_decision(permission) if child_decision is not None else None
        if child == "deny":
            return f"Permission denied by delegated child policy: {permission}", asks
        decision = resolve(permission, target)
        if decision == "deny":
            return f"Permission denied: {permission} for {target} is denied", asks
        if child == "ask" or decision == "ask":
            asks.append((permission, target))
    return None, asks


class ProjectListDirectoryTool(ListDirectoryTool):
    name = "project_list_dir"
    host_method = "project_list_dir"
    description = "List one directory inside this session's attached local project folder. Paths are project-relative; source files are read-only."
    resource_key_prefix = "project_files"
    screen_untrusted_output = True

    def execute(self, context: ControlToolContext, arguments: dict) -> dict:
        return super().execute(context.project_files(), arguments)


class ProjectReadFileTool(ReadTextFileTool):
    name = "project_read_file"
    host_method = "project_read_file"
    description = "Read a UTF-8 file or bounded line window inside the attached project folder, using a project-relative path. Import binary/data files with project_import_file before analysis."
    resource_key_prefix = "project_files"
    screen_untrusted_output = True

    def execute(self, context: ControlToolContext, arguments: dict) -> dict:
        return super().execute(context.project_files(), arguments)


class ProjectGlobTool(GlobFilesTool):
    name = "project_glob"
    host_method = "project_glob"
    description = "Recursively find files in the attached project folder by glob, e.g. '**/*.csv'. Paths are relative to that folder."
    resource_key_prefix = "project_files"
    screen_untrusted_output = True

    def execute(self, context: ControlToolContext, arguments: dict) -> dict:
        return super().execute(context.project_files(), arguments)


class ProjectGrepTool(ContentSearchTool):
    name = "project_grep"
    host_method = "project_grep"
    description = "Search UTF-8 contents recursively inside the attached project folder. Uses a regular expression and optional filename glob."
    resource_key_prefix = "project_files"
    screen_untrusted_output = True

    def execute(self, context: ControlToolContext, arguments: dict) -> dict:
        return super().execute(context.project_files(), arguments)


class ProjectImportFileTool(Tool):
    name = "project_import_file"
    host_method = "project_import_file"
    description = "Copy one file from the attached project folder into this session for Python/R analysis, preserving source bytes and recording SHA-256 provenance. source_path is project-relative; path defaults to project-inputs/<source_path>. Never changes the source."
    parameters = {
        "properties": {
            "source_path": {"type": "string", "minLength": 1},
            "path": {
                "type": "string",
                "minLength": 1,
                "description": "Destination relative to the session workspace.",
            },
            "max_bytes": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1073741824,
                "description": "Copy budget in bytes; defaults to 256 MiB, maximum 1 GiB.",
            },
        },
        "required": ["source_path"],
    }
    read_only = False
    writes_files = True
    side_effect_class = WORKSPACE_WRITE
    secret_path_key = "source_path"
    screen_untrusted_output = True

    def permission_target(self, arguments: Any) -> str:
        return import_destination(arguments) if isinstance(arguments, dict) else ""

    def resource_keys(self, arguments: Any) -> tuple[str, ...]:
        spec = arguments if isinstance(arguments, dict) else {}
        return (
            resource_key("project_files", workspace_target(spec.get("source_path"))),
            resource_key("workspace", workspace_target(import_destination(spec))),
        )

    def execute(self, context: ControlToolContext, arguments: dict) -> dict:
        from openai4s import webtools
        from openai4s.artifact_paths import require_capturable_destination

        error = self.validation_error(arguments)
        if error is not None:
            return {"error": error}
        source = context.project_files()
        destination = import_destination(arguments)
        require_capturable_destination(
            context.workspace(), context.resolve(destination)
        )
        limit = int(arguments.get("max_bytes", 256 * 1024 * 1024))
        cancelled = context.download_cancellation()
        webtools.check_download_cancelled(cancelled)
        with source.open_verified_read(arguments["source_path"]) as opened:
            if opened.size_bytes > limit:
                raise ValueError(
                    "project file exceeds max_bytes; increase the explicit budget or select a smaller file"
                )
            before = os.fstat(opened.handle.fileno())
            with context.secure_parent(destination, create_parents=True) as parent:
                descriptor, staged = parent.create_staged(suffix=".project-import")
                try:
                    digest = hashlib.sha256()
                    copied = 0
                    with os.fdopen(descriptor, "wb") as sink:
                        while True:
                            webtools.check_download_cancelled(cancelled)
                            chunk = opened.handle.read(256 * 1024)
                            if not chunk:
                                break
                            copied += len(chunk)
                            if copied > limit:
                                raise ValueError("project file exceeds max_bytes")
                            digest.update(chunk)
                            sink.write(chunk)
                    after = os.fstat(opened.handle.fileno())
                    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                        after.st_size,
                        after.st_mtime_ns,
                        after.st_ctime_ns,
                    ) or copied != before.st_size:
                        raise RuntimeError(
                            "project source changed during import; retry with a stable file"
                        )
                    webtools.check_download_cancelled(cancelled)
                    parent.publish(staged)
                except BaseException:
                    parent.discard(staged)
                    raise
        checksum = digest.hexdigest()
        provenance = {
            "kind": "local_project",
            "source_path": opened.relative,
            "sha256": checksum,
            "size_bytes": copied,
        }
        return {
            "path": parent.target_relative,
            "bytes": copied,
            "sha256": checksum,
            "source": provenance,
            "_openai4s_artifact_capture": {
                "filename": parent.target_relative,
                "checksum": checksum,
                "source": provenance,
            },
        }
