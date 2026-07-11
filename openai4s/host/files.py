"""Workspace path boundary shared by class-based file tools.

This service owns only session-root resolution and confinement.  Concrete
read/write/edit/search behaviour lives beside its schema in
``openai4s.tools``.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Callable

_SECRET_BASENAMES = (
    "*.env",
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_ed25519",
    ".netrc",
    ".pgpass",
)


def is_secret_path(path: str) -> bool:
    """Return whether a basename belongs to the host tool secret denylist."""
    import posixpath

    basename = posixpath.basename((path or "").replace("\\", "/").rstrip("/")).lower()
    if not basename:
        return False
    return any(fnmatch.fnmatchcase(basename, pattern) for pattern in _SECRET_BASENAMES)


class WorkspaceFileService:
    """Execute file tools inside the workspace for the current frame.

    ``frame_id`` is a provider rather than a captured value because the CLI may
    assign its root frame after constructing the dispatcher.
    """

    def __init__(
        self,
        *,
        data_dir: Path,
        frame_id: Callable[[], str | None],
    ) -> None:
        self._data_dir = data_dir
        self._frame_id = frame_id

    def workspace(self) -> Path:
        """Return the resolved workspace, creating it on first use."""
        workspace = (
            self._data_dir
            / "agent-workspaces"
            / (self._frame_id() or "default")
        ).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    def relative(self, path: Path) -> str | None:
        """Return a confined workspace-relative path, or ``None`` on escape."""
        try:
            return str(path.resolve().relative_to(self.workspace()))
        except (ValueError, OSError):
            return None

    def resolve(self, relative: str, *, must_exist: bool = False) -> Path:
        """Resolve a path and reject parent, absolute, and symlink escapes."""
        workspace = self.workspace()
        path = Path(relative)
        target = (path if path.is_absolute() else workspace / path).resolve()
        try:
            target.relative_to(workspace)
        except ValueError:
            raise ValueError(
                f"path escapes the workspace: {relative!r} "
                "(stay inside your working dir)"
            )
        if must_exist and not target.exists():
            raise FileNotFoundError(f"no such file: {relative}")
        return target

    @staticmethod
    def is_secret_path(path: str) -> bool:
        """Expose the shared denylist without coupling tools to this module."""
        return is_secret_path(path)

    def _execute_compat(self, host_method: str, spec: dict) -> dict:
        """Preserve the former service API while concrete tools own behaviour."""
        from openai4s.tools.registry import get_tool_by_host_method

        tool = get_tool_by_host_method(host_method)
        if tool is None:
            raise ValueError(f"no control tool registered for {host_method!r}")
        return tool.execute(self, spec)

    def read_file(self, spec: dict) -> dict:
        return self._execute_compat("read_file", spec)

    def write_file(self, spec: dict) -> dict:
        return self._execute_compat("write_file", spec)

    def edit_file(self, spec: dict) -> dict:
        return self._execute_compat("edit_file", spec)

    def glob(self, spec: dict) -> dict:
        return self._execute_compat("glob", spec)

    def grep(self, spec: dict) -> dict:
        return self._execute_compat("grep", spec)

    def list_dir(self, spec: dict) -> dict:
        return self._execute_compat("list_dir", spec)

__all__ = ["WorkspaceFileService", "is_secret_path"]
