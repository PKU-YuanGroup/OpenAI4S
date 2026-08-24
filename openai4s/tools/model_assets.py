"""Import an operator-owned model asset into the session workspace."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from openai4s.tools.base import Tool
from openai4s.tools.contexts import WorkspaceToolContext

_PORTABLE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _canonical_source(raw: str) -> str:
    """The absolute path the copy will actually read, or ``""`` if unknowable.

    Total by construction: the permission and secret gates run before any
    validation, so this must never raise on a malformed or missing path.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        return str(Path(os.path.expanduser(text)).resolve())
    except (OSError, ValueError, RuntimeError):
        return ""


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
        raw = str(arguments.get("source_path") or "")
        # Approve the bytes that will actually be read, not the spelling used
        # to reach them. A durable approval recorded against `~/models/x` must
        # not keep authorizing whatever that name points at once it becomes a
        # symlink into a credential directory.
        return _canonical_source(raw) or raw

    def secret_path(self, arguments: Any) -> str | None:
        # The caller names a *source*, while the destination is derived. Keep
        # the hard secret-file refusal without pretending the caller controls
        # where the copy is written.
        if not isinstance(arguments, dict):
            return None
        raw = str(arguments.get("source_path") or "")
        from openai4s.host.files import is_secret_path

        # `is_secret_path` is a lexical, directory-aware denylist, so it can
        # only classify the segments it is given. A parent symlink launders a
        # credential path past it -- `/tmp/models -> ~/.ssh` makes
        # `/tmp/models/config` look like neither a secret basename nor a
        # secret directory. Classify the resolved destination as well.
        for candidate in (raw, _canonical_source(raw)):
            if candidate and is_secret_path(candidate):
                return candidate
        return None

    def execute(self, workspace: WorkspaceToolContext, arguments: dict) -> dict:
        raw_source = str(arguments.get("source_path") or "").strip()
        unresolved = Path(os.path.expanduser(raw_source))
        if not unresolved.is_absolute():
            unresolved = Path(os.path.abspath(unresolved))
        if unresolved.is_symlink():
            return {"error": f"model asset must not be a symlink: {raw_source}"}
        source = unresolved.resolve()
        from openai4s.host.files import is_secret_path

        # `is_symlink()` speaks only for the final component, and the denylist
        # is lexical, so a symlinked *parent* launders a credential path past
        # both: `/tmp/models -> ~/.ssh` makes `/tmp/models/config` look like
        # neither a secret basename nor a secret directory. Classify where the
        # read actually lands. (Refusing symlinked ancestors outright would
        # instead reject every path under `/tmp` on macOS, where `/tmp` and
        # `/var` are themselves OS-level symlinks.)
        if is_secret_path(str(source)):
            return {"error": f"model asset resolves onto a secret path: {raw_source}"}
        if not source.is_file():
            return {"error": f"model asset is not a regular file: {raw_source}"}
        name = str(arguments.get("asset_name") or source.name)
        if not _PORTABLE_NAME.fullmatch(name):
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
