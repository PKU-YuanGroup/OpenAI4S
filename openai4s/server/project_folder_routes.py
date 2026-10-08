"""Local project folder selection and bounded, read-only file browsing."""

from __future__ import annotations

import codecs
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from openai4s.host.files import MAX_SCAN_ENTRIES, MAX_SCAN_SECONDS, is_secret_path
from openai4s.project_folders import (
    ProjectFolderError,
    ReadOnlyProjectFiles,
    project_folder_path,
    require_local_project_folders,
    validate_folder_path,
)

from . import contract

_PICKER = contract.RouteSpec(
    "projects.local_folders", "GET", r"/local-folders", mutates=False
)
_FILES = contract.RouteSpec(
    "projects.files", "GET", r"/projects/([^/]+)/files", mutates=False
)
_FILE = contract.RouteSpec(
    "projects.file", "GET", r"/projects/([^/]+)/file", mutates=False
)
ROUTES = contract.validate_routes((_PICKER, _FILES, _FILE))
MAX_ENTRIES = 500
MAX_PREVIEW_BYTES = 256 * 1024


def require_local_request(handler: Any, cfg: Any) -> None:
    require_local_project_folders(cfg)
    if not handler._peer_is_loopback():
        raise ProjectFolderError(
            403,
            "project folders require a local connection",
            "local_project_folders_only",
        )


def _relative(raw: str) -> str:
    if not isinstance(raw, str) or "\x00" in raw or "\\" in raw:
        raise ProjectFolderError(400, "invalid project-relative path")
    path = Path(raw or ".")
    if path.is_absolute() or ".." in path.parts:
        raise ProjectFolderError(400, "path must stay inside the project folder")
    return "" if str(path) == "." else path.as_posix()


def local_folders(raw: str) -> dict:
    current = Path(validate_folder_path(raw or str(Path.home())) or "")
    entries = []
    truncated = False
    deadline = time.monotonic() + MAX_SCAN_SECONDS
    with os.scandir(current) as scan:
        for index, entry in enumerate(scan):
            if index >= MAX_SCAN_ENTRIES or time.monotonic() > deadline:
                truncated = True
                break
            if ReadOnlyProjectFiles.is_secret_path(str(current / entry.name)):
                continue
            if entry.is_dir(follow_symlinks=False):
                if len(entries) >= MAX_ENTRIES:
                    truncated = True
                    break
                entries.append({"name": entry.name, "path": str(current / entry.name)})
    entries.sort(key=lambda entry: entry["name"].casefold())
    return {
        "path": str(current),
        "parent_path": str(current.parent) if current.parent != current else None,
        "entries": entries,
        "truncated": truncated,
    }


def project_files(root: Path, raw: str) -> dict:
    relative = _relative(raw)
    result = ReadOnlyProjectFiles(root).list_dir({"path": relative or "."})
    if "error" in result:
        raise ProjectFolderError(400, result["error"])
    entries = [
        {
            "name": entry["name"],
            "path": entry["path"],
            "kind": "directory" if entry["is_dir"] else "file",
            "size": entry["size_bytes"],
        }
        for entry in result["entries"][:MAX_ENTRIES]
        if entry["is_dir"] or entry["size_bytes"] is not None
    ]
    entries.sort(
        key=lambda entry: (entry["kind"] != "directory", entry["name"].casefold())
    )
    parent = Path(relative).parent.as_posix() if relative else None
    return {
        "folder_path": str(root),
        "path": relative,
        "parent_path": "" if parent == "." else parent,
        "entries": entries,
        "truncated": bool(
            result.get("truncated") or len(result["entries"]) > MAX_ENTRIES
        ),
    }


def project_file(root: Path, raw: str) -> dict:
    relative = _relative(raw)
    if not relative:
        raise ProjectFolderError(400, "a file path is required")
    with ReadOnlyProjectFiles(root).open_verified_read(relative) as source:
        data = source.handle.read(MAX_PREVIEW_BYTES + 1)
        size = source.size_bytes
    truncated = len(data) > MAX_PREVIEW_BYTES
    if b"\x00" in data:
        raise ProjectFolderError(
            415,
            "binary files can be imported for analysis but cannot be previewed",
            "binary_preview",
        )
    try:
        content = codecs.getincrementaldecoder("utf-8")("strict").decode(
            data[:MAX_PREVIEW_BYTES], final=not truncated
        )
    except UnicodeDecodeError as error:
        raise ProjectFolderError(
            415, "preview requires UTF-8 text", "binary_preview"
        ) from error
    return {
        "path": relative,
        "name": Path(relative).name,
        "size": size,
        "content": content,
        "encoding": "utf-8",
        "truncated": truncated,
    }


def handle(handler: Any, method: str, sub: str, q: dict, cfg: Any, store: Any) -> bool:
    picker = _PICKER.match(method, sub)
    files = _FILES.match(method, sub)
    file = _FILE.match(method, sub)
    if not (picker or files or file):
        return False
    try:
        require_local_request(handler, cfg)
        raw = (q.get("path") or [""])[0]
        if picker:
            result = local_folders(raw)
        else:
            match = files or file
            assert match is not None
            root = project_folder_path(store, cfg, unquote(match.group(1)))
            result = project_files(root, raw) if files else project_file(root, raw)
        handler._json(result)
    except ProjectFolderError as error:
        handler._json({"error": str(error), "code": error.code}, error.status)
    except FileNotFoundError:
        handler._json({"error": "project path not found", "code": "not_found"}, 404)
    except (OSError, ValueError, RuntimeError):
        handler._json(
            {"error": "project path cannot be accessed", "code": "project_path_denied"},
            403,
        )
    return True
