"""Server-side cleanup for durable session deletion results."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, Protocol

from openai4s.storage.snapshots import WorkspaceCAS
from openai4s.tools.dynamic_scopes import DynamicScopeStore


class SessionDeletionStore(Protocol):
    def get_frame(self, frame_id: str) -> dict | None:
        ...

    def project_session_ids(self, project_id: str) -> list[str]:
        ...

    def delete_frame(self, frame_id: str) -> dict[str, Any]:
        ...

    def delete_project(self, project_id: str) -> dict[str, Any]:
        ...

    def retained_workspace_tree_ids(self) -> tuple[str, ...]:
        ...


class SessionDeletionService:
    """Stop live resources, delete durable rows, then clean owned files."""

    def __init__(
        self,
        store: SessionDeletionStore,
        *,
        data_dir: str | Path,
        cas: WorkspaceCAS,
        drop_runtime: Callable[[str, str], Any],
        drop_resume_window: Callable[[str], Any],
    ) -> None:
        self.store = store
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.workspace_root = self.data_dir / "agent-workspaces"
        self.cas = cas
        self.dynamic_scopes = DynamicScopeStore(
            self.data_dir / "dynamic-tools" / "_scoped"
        )
        self._drop_runtime = drop_runtime
        self._drop_resume_window = drop_resume_window

    def delete_session(
        self, root_frame_id: str, *, reason: str = "frame_deleted"
    ) -> dict[str, Any]:
        frame = self.store.get_frame(root_frame_id)
        if frame is not None:
            canonical = str(frame.get("root_frame_id") or frame.get("frame_id"))
            if canonical != root_frame_id:
                raise ValueError("session deletion requires a root frame id")
            self._drop_runtime(root_frame_id, reason)
        result = self.store.delete_frame(root_frame_id)
        cleanup = self._cleanup(result)
        self._drop_resume_window(root_frame_id)
        return {"ok": True, **cleanup}

    def delete_project(
        self, project_id: str, *, reason: str = "project_deleted"
    ) -> dict[str, Any]:
        roots = self.store.project_session_ids(project_id)
        for root_frame_id in roots:
            self._drop_runtime(root_frame_id, reason)
        result = self.store.delete_project(project_id)
        deleted_roots = tuple(
            dict.fromkeys(
                str(value) for value in result.get("root_frame_ids", ()) if value
            )
        )
        # Admission is closed by SessionRunner while this service runs. This
        # second pass is a fail-safe for legacy/direct Store writers that may
        # have inserted a root after the initial enumeration.
        for root_frame_id in deleted_roots:
            if root_frame_id not in roots:
                self._drop_runtime(root_frame_id, reason)
        cleanup = self._cleanup(result)
        dynamic = self.dynamic_scopes.delete_project_scope(project_id)
        for root_frame_id in deleted_roots:
            self._drop_resume_window(root_frame_id)
        return {
            "ok": True,
            **cleanup,
            "freed_dynamic_events": dynamic["events"],
            "freed_dynamic_manifests": dynamic["manifests"],
        }

    def _cleanup(self, result: Mapping[str, Any]) -> dict[str, Any]:
        roots = tuple(
            dict.fromkeys(
                str(value) for value in result.get("root_frame_ids", ()) if value
            )
        )
        # Only immutable, version-id-prefixed snapshots are unlinked one by
        # one. Live workspace files are reclaimed by the confined tree removal
        # below; shared ``uploads`` paths are deliberately left for a later
        # reference-aware sweeper because their basenames can be reused.
        trusted_roots = (self.data_dir / "artifact-versions",)
        freed_files = 0
        skipped_files = 0
        retained_paths, retained_files = self._retained_path_identities(
            result.get("retained_paths", ())
        )
        for raw_path in result.get("stale_paths", ()):
            if self._unlink_owned_file(
                raw_path,
                trusted_roots,
                retained_paths=retained_paths,
                retained_files=retained_files,
            ):
                freed_files += 1
            else:
                skipped_files += 1

        freed_workspaces = 0
        for root_frame_id in roots:
            if self._remove_root_workspace(root_frame_id):
                freed_workspaces += 1
            if self._remove_branch_workspaces(root_frame_id):
                freed_workspaces += 1
            if self._remove_dynamic_tools(root_frame_id):
                freed_workspaces += 1
            if self._remove_session_import(root_frame_id):
                freed_workspaces += 1

        cas = self.cas.release_trees(
            result.get("cas_tree_ids", ()),
            retained_tree_ids=result.get("retained_cas_tree_ids", ()),
            retained_tree_ids_provider=self.store.retained_workspace_tree_ids,
        )
        return {
            "deleted": bool(result.get("deleted")),
            "freed_sessions": len(roots),
            "freed_files": freed_files,
            "skipped_unowned_files": skipped_files,
            "freed_workspaces": freed_workspaces,
            "freed_cas_trees": cas["trees"],
            "freed_cas_blobs": cas["blobs"],
            "shared_cas_trees": cas["shared_trees"],
            "deleted_rows": dict(result.get("deleted_rows") or {}),
        }

    @staticmethod
    def _unlink_owned_file(
        raw_path: Any,
        trusted_roots: Iterable[Path],
        *,
        retained_paths: set[str],
        retained_files: set[tuple[int, int]],
    ) -> bool:
        if not isinstance(raw_path, str) or not raw_path:
            return False
        candidate = Path(os.path.abspath(Path(raw_path).expanduser()))
        if not candidate.is_absolute():
            return False
        try:
            # Version snapshots are flat files. Requiring the exact managed
            # parent avoids symlinked intermediate directories and prevents a
            # DB path from selecting another session's workspace subtree.
            managed_roots = tuple(root.resolve() for root in trusted_roots)
            allowed = candidate.parent.resolve() in managed_roots
            if candidate.is_symlink():
                return False
            resolved = candidate.resolve(strict=False)
        except OSError:
            return False
        identity = SessionDeletionService._file_identity(resolved)
        if os.path.normcase(str(resolved)) in retained_paths or (
            identity is not None and identity in retained_files
        ):
            return False
        if not allowed or candidate.is_dir():
            return False
        try:
            # Unlink the checked directory entry, never a resolved symlink
            # target. A last-moment regular->symlink swap therefore removes
            # only the link itself.
            candidate.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return False

    @staticmethod
    def _retained_path_identities(
        paths: Iterable[Any],
    ) -> tuple[set[str], set[tuple[int, int]]]:
        resolved_paths: set[str] = set()
        file_ids: set[tuple[int, int]] = set()
        for raw_path in paths:
            if not isinstance(raw_path, str) or not raw_path:
                continue
            try:
                path = Path(raw_path).expanduser().resolve(strict=False)
            except OSError:
                continue
            resolved_paths.add(os.path.normcase(str(path)))
            identity = SessionDeletionService._file_identity(path)
            if identity is not None:
                file_ids.add(identity)
        return resolved_paths, file_ids

    @staticmethod
    def _file_identity(path: Path) -> tuple[int, int] | None:
        try:
            info = path.stat()
        except OSError:
            return None
        return int(info.st_dev), int(info.st_ino)

    def _remove_root_workspace(self, root_frame_id: str) -> bool:
        if Path(root_frame_id).name != root_frame_id or root_frame_id in {".", ".."}:
            return False
        return self._remove_owned_tree(
            self.workspace_root / root_frame_id,
            direct_parent=self.workspace_root,
        )

    def _remove_branch_workspaces(self, root_frame_id: str) -> bool:
        root_key = hashlib.sha256(root_frame_id.encode("utf-8")).hexdigest()[:24]
        parent = self.workspace_root / ".branches"
        removed = self._remove_owned_tree(parent / root_key, direct_parent=parent)
        try:
            parent.rmdir()
        except OSError:
            pass
        return removed

    def _remove_dynamic_tools(self, root_frame_id: str) -> bool:
        safe_session = re.sub(r"[^A-Za-z0-9._-]+", "_", root_frame_id)
        # Avoid sanitizer collisions and the shared project/global audit tree.
        if safe_session != root_frame_id or safe_session == "_scoped":
            return False
        parent = self.data_dir / "dynamic-tools"
        return self._remove_owned_tree(
            parent / safe_session,
            direct_parent=parent,
        )

    def _remove_session_import(self, root_frame_id: str) -> bool:
        if Path(root_frame_id).name != root_frame_id or root_frame_id in {".", ".."}:
            return False
        parent = self.data_dir / "session-imports"
        removed = self._remove_owned_tree(
            parent / root_frame_id,
            direct_parent=parent,
        )
        try:
            parent.rmdir()
        except OSError:
            pass
        return removed

    @staticmethod
    def _remove_owned_tree(path: Path, *, direct_parent: Path) -> bool:
        try:
            if path.parent.resolve() != direct_parent.resolve():
                return False
            if path.is_symlink():
                path.unlink()
                return True
            if not path.exists():
                return False
            if not path.is_dir():
                return False
            shutil.rmtree(path)
            return True
        except OSError:
            return False


__all__ = ["SessionDeletionService", "SessionDeletionStore"]
