"""Import an operator-owned model asset into the session workspace."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from openai4s.tools.base import Tool
from openai4s.tools.contexts import WorkspaceToolContext


class StageModelAssetTool(Tool):
    """Copy one explicitly approved local file into a confined workspace."""

    name = "stage_model_asset"
    host_method = "stage_model_asset"
    description = (
        "Import an existing local checkpoint or model asset into the session "
        "workspace and compute its SHA-256 before backend bring-up."
    )
    parameters = {
        "properties": {
            "source_path": {
                "type": "string",
                "minLength": 1,
                "description": "Existing local file path supplied by the user.",
            },
            "asset_name": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
                "description": "Portable staged filename; defaults to source basename.",
            },
            "expected_sha256": {
                "type": "string",
                "minLength": 64,
                "maxLength": 64,
                "description": "Optional independently known digest.",
            },
        },
        "required": ["source_path"],
    }
    read_only = False
    writes_files = True
    derived_write_path = True
    dangerous = True
    side_effect_class = "workspace_write"
    resource_key_prefix = "model_asset"
    resource_target_key = "source_path"

    def permission_target(self, arguments: Any) -> str:
        if not isinstance(arguments, dict):
            return ""
        return str(arguments.get("source_path") or "")

    def secret_path(self, arguments: Any) -> str | None:
        # The caller names a *source*, while the destination is derived. Keep
        # the hard secret-file refusal without pretending the caller controls
        # where the copy is written.
        if not isinstance(arguments, dict):
            return None
        source = str(arguments.get("source_path") or "")
        from openai4s.host.files import is_secret_path

        return source if is_secret_path(source) else None

    def execute(self, workspace: WorkspaceToolContext, arguments: dict) -> dict:
        raw_source = str(arguments.get("source_path") or "").strip()
        unresolved = Path(os.path.expanduser(raw_source))
        if unresolved.is_symlink():
            return {"error": f"model asset must not be a symlink: {raw_source}"}
        source = unresolved.resolve()
        if not source.is_file():
            return {"error": f"model asset is not a regular file: {raw_source}"}
        name = str(arguments.get("asset_name") or source.name)
        import re

        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name):
            return {"error": "asset_name must be a portable filename"}
        destination = workspace.resolve(f"model-assets/{name}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=destination.parent, prefix=f".{name}.", delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
                with source.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                        size += len(chunk)
                        temporary.write(chunk)
                temporary.flush()
                os.fsync(temporary.fileno())
            observed = digest.hexdigest()
            expected = str(arguments.get("expected_sha256") or "").lower()
            if expected and observed != expected:
                return {
                    "error": (
                        "model asset SHA-256 mismatch: "
                        f"expected {expected}, observed {observed}"
                    )
                }
            os.replace(temporary_path, destination)
            temporary_path = None
        except OSError as error:
            return {"error": f"could not stage model asset: {error}"}
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except OSError:
                    pass
        return {
            "status": "staged",
            "path": workspace.relative(destination),
            "sha256": observed,
            "size": size,
            "source_basename": source.name,
            "admitted": False,
            "note": "Run and verify a real inference canary before formal use.",
        }


__all__ = ["StageModelAssetTool"]
