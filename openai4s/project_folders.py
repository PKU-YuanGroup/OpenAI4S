"""Explicit local project source folders, separate from writable workspaces."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openai4s.host.files import (
    SecureWorkspaceDirectory,
    UnsafeWorkspaceCandidate,
    WorkspaceFileService,
    is_secret_path,
)


class ProjectFolderError(ValueError):
    """A project-folder refusal that can also be projected by HTTP adapters."""

    def __init__(self, status: int, message: str, code: str = "project_folder"):
        super().__init__(message)
        self.status = status
        self.code = code


def require_local_project_folders(cfg: Any) -> None:
    """Arbitrary host folder grants belong only to the standalone local app."""
    if (
        getattr(cfg, "team_mode", False)
        or str(getattr(cfg, "host", "")).strip("[]").lower()
        not in ("127.0.0.1", "::1", "localhost")
        or getattr(cfg, "trusted_proxy_origins", ())
    ):
        raise ProjectFolderError(
            403,
            "project folders require a standalone loopback daemon",
            "local_project_folders_only",
        )


def validate_folder_path(value: Any, cfg: Any = None) -> str | None:
    """Validate an explicit grant without creating or changing the directory."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ProjectFolderError(400, "folder_path must be an absolute directory")
    original = Path(value.strip()).expanduser()
    if not original.is_absolute():
        raise ProjectFolderError(400, "folder_path must be an absolute directory")
    if is_secret_path(str(original)):
        raise ProjectFolderError(403, "credential directories cannot be projects")
    try:
        resolved = original.resolve(strict=True)
        if not resolved.is_dir():
            raise ProjectFolderError(400, "project folder is not a directory")
    except (OSError, RuntimeError) as error:
        raise ProjectFolderError(404, "project folder not found") from error
    if is_secret_path(str(resolved)):
        raise ProjectFolderError(403, "credential directories cannot be projects")
    if cfg is not None:
        data_dir = Path(cfg.data_dir).expanduser().resolve()
        if (
            resolved == data_dir
            or resolved in data_dir.parents
            or data_dir in resolved.parents
        ):
            raise ProjectFolderError(
                400,
                "project folder must not overlap the OpenAI4S data directory",
                "project_folder_overlap",
            )
    return str(resolved)


def project_folder_path(store: Any, cfg: Any, project_id: str) -> Path:
    require_local_project_folders(cfg)
    project = store.get_project(project_id)
    if project is None:
        raise ProjectFolderError(404, "project not found", "not_found")
    value = project.get("folder_path")
    if not value:
        raise ProjectFolderError(
            409, "project has no linked folder", "no_project_folder"
        )
    canonical = validate_folder_path(value, cfg)
    if canonical != value:
        raise ProjectFolderError(409, "project folder changed; select it again")
    assert canonical is not None
    return Path(canonical)


class ReadOnlyProjectFiles(WorkspaceFileService):
    """Reuse the descriptor/secret boundary without creating a source root."""

    def __init__(self, root: Path):
        self._project_root = Path(root)
        super().__init__(data_dir=root, frame_id=lambda: None)

    def workspace(self) -> Path:
        canonical = validate_folder_path(str(self._project_root))
        if canonical != str(self._project_root):
            raise ProjectFolderError(409, "project folder changed; select it again")
        return self._project_root

    @staticmethod
    def is_secret_path(path: str) -> bool:
        # History can contain other conversations and generated files. It has
        # its own explicit UI/API; recursive input searches must not ingest it.
        return is_secret_path(path) or any(
            part.casefold() == ".openai4s"
            for part in str(path).replace("\\", "/").split("/")
        )

    def _candidate_parts(self, relative: str | Path) -> tuple[str, ...]:
        parts = super()._candidate_parts(relative)
        if any(part.casefold() == ".openai4s" for part in parts):
            raise UnsafeWorkspaceCandidate(
                "project history is available through the Local history view"
            )
        return parts

    def open_verified_directory(self, relative: str | Path) -> SecureWorkspaceDirectory:
        directory = super().open_verified_directory(relative)
        directory._predicate = self.is_secret_path
        return directory

    def resolve(self, relative: str, *, must_exist: bool = False) -> Path:
        self._candidate_parts(relative)
        return super().resolve(relative, must_exist=must_exist)

    def write_file(self, spec: dict) -> dict:
        raise ProjectFolderError(403, "project source folders are read-only")

    def edit_file(self, spec: dict) -> dict:
        raise ProjectFolderError(403, "project source folders are read-only")
