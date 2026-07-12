"""Versioned workspace artifact capture for persistent scientific sessions."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable, Protocol

from openai4s.artifact_restore import ArtifactRestoreService
from openai4s.execution import CaptureResult

_JUNK_DIR_SEGMENTS = frozenset({"__pycache__", "node_modules", "site-packages", "venv"})
EventSink = Callable[[dict[str, Any]], None]
Broadcast = Callable[[str, dict[str, Any]], None]

_TEXT_EDIT_EXT = (
    ".txt",
    ".log",
    ".md",
    ".markdown",
    ".csv",
    ".tsv",
    ".json",
    ".py",
    ".js",
    ".ts",
    ".fasta",
    ".fa",
    ".nwk",
    ".treefile",
    ".xml",
    ".yaml",
    ".yml",
    ".sh",
    ".r",
    ".tex",
    ".html",
    ".htm",
    ".css",
)
_BINARY_EXT = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".pdf",
    ".pdb",
    ".cif",
    ".mol",
    ".mol2",
    ".sdf",
    ".xyz",
)


class ArtifactOperationError(Exception):
    """An artifact mutation that the HTTP layer can map to a response."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ArtifactSession(Protocol):
    root_frame_id: str
    project_id: str
    workspace: Path


class ArtifactManager:
    def __init__(
        self,
        *,
        data_dir: Path,
        store: Any,
        workspace_for: Callable[[str], Path],
        broadcast: Callable[[str, dict], None],
        environment_snapshot: Callable[[], dict],
        guess_content_type: Callable[[str], str],
        checksum: Callable[[Path], str],
    ) -> None:
        self.data_dir = data_dir
        self.store = store
        self.workspace_for = workspace_for
        self.broadcast = broadcast
        self.environment_snapshot = environment_snapshot
        self.guess_content_type = guess_content_type
        self.checksum = checksum

    def _notify(
        self,
        root_frame_id: str | None,
        event: dict[str, Any],
        broadcast: Broadcast | None,
    ) -> None:
        if root_frame_id:
            (broadcast or self.broadcast)(root_frame_id, event)

    def versions_dir(self) -> Path:
        directory = self.data_dir / "artifact-versions"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def live_path(self, artifact: dict) -> Path:
        root_frame_id = artifact.get("root_frame_id") or "default"
        workspace = self.workspace_for(root_frame_id).expanduser().resolve()
        filename = artifact.get("filename")
        if not isinstance(filename, str) or not filename or "\x00" in filename:
            raise ArtifactOperationError(400, "artifact filename is invalid")
        candidate = Path(filename)
        if candidate.is_absolute():
            raise ArtifactOperationError(400, "artifact path must be relative")
        target = (workspace / candidate).expanduser().resolve()
        try:
            target.relative_to(workspace)
        except ValueError as error:
            raise ArtifactOperationError(
                400, "artifact live path escapes its workspace"
            ) from error
        return target

    def restore_live_path(self, artifact: dict, current: dict) -> Path:
        """Resolve the exact live file while rejecting workspace escapes."""
        root_frame_id = artifact.get("root_frame_id") or "default"
        workspace = self.workspace_for(root_frame_id).expanduser().resolve()
        raw_path = current.get("path") or artifact.get("filename") or ""
        candidate = Path(raw_path)
        target = (
            (candidate if candidate.is_absolute() else workspace / candidate)
            .expanduser()
            .resolve()
        )
        try:
            target.relative_to(workspace)
        except ValueError as error:
            raise PermissionError("artifact live path escapes its workspace") from error
        return target

    def write_version_snapshot(
        self,
        version_id: str,
        filename: str,
        *,
        src_path: Path | None = None,
        data: bytes | None = None,
    ) -> None:
        """Freeze one version's bytes while its DB path stays live/mutable."""
        try:
            current = self.store.version_meta(version_id)
            existing = (current or {}).get("snapshot_path")
            if existing and Path(existing).is_file():
                return
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename or "artifact")
            snapshot = self.versions_dir() / f"{version_id}__{safe}"
            if data is not None:
                snapshot.write_bytes(data)
            elif src_path is not None:
                shutil.copyfile(src_path, snapshot)
            else:
                return
            self.store.set_version_snapshot(version_id, str(snapshot))
        except OSError:
            pass

    def protect_latest(self, session: ArtifactSession) -> None:
        """Backfill immutable bytes before a later cell overwrites a live file."""
        try:
            artifacts = self.store.list_artifacts(
                {"root_frame_id": session.root_frame_id}
            )
        except Exception:  # noqa: BLE001
            return
        for artifact in artifacts:
            version_id = artifact.get("latest_version_id")
            if not version_id:
                continue
            try:
                meta = self.store.version_meta(version_id)
                if not meta or meta.get("snapshot_path") or not meta.get("path"):
                    continue
                path = Path(meta["path"])
                if path.is_file():
                    self.write_version_snapshot(
                        version_id,
                        meta.get("filename") or artifact.get("filename") or "artifact",
                        src_path=path,
                    )
            except Exception:  # noqa: BLE001
                continue

    def restore(self, artifact_id: str, version_id: str) -> dict:
        """Restore a historical snapshot as a fresh immutable version."""
        artifact = self.store.get_artifact(artifact_id)
        version = self.store.version_meta(version_id)
        if not artifact or not version or version.get("artifact_id") != artifact_id:
            return {"error": "version not found"}
        try:
            restored = ArtifactRestoreService(
                store=self.store,
                primary_snapshot_dir=self.versions_dir(),
                trusted_snapshot_dirs=(self.data_dir / "artifacts",),
                resolve_live_path=self.restore_live_path,
            ).restore(
                artifact=artifact,
                source_version_id=version_id,
                frame_id=artifact.get("root_frame_id"),
            )
        except (KeyError, OSError, PermissionError, RuntimeError, ValueError) as error:
            return {"error": f"restore failed: {error}"}

        current_artifact = self.store.get_artifact(artifact_id)
        root_frame_id = artifact.get("root_frame_id")
        if root_frame_id:
            self.broadcast(
                root_frame_id,
                {
                    "type": "artifact_created",
                    "root_frame_id": root_frame_id,
                    "artifact": {
                        "id": artifact_id,
                        "artifact_id": artifact_id,
                        "filename": restored.get("filename"),
                        "content_type": restored.get("content_type"),
                        "version_id": restored["version_id"],
                        "root_frame_id": root_frame_id,
                        "restored_from_version_id": version_id,
                    },
                },
            )
        return {
            "ok": True,
            "artifact": current_artifact,
            "version_id": restored["version_id"],
            "restored_from_version_id": version_id,
            "snapshot_verified": True,
        }

    def edit(
        self,
        artifact_id: str,
        content: str,
        *,
        broadcast: Broadcast | None = None,
    ) -> dict:
        """Save edited text as a new version without changing its live path."""
        artifact = self.store.get_artifact(artifact_id)
        if not artifact:
            raise ArtifactOperationError(404, "artifact not found")
        if not is_text_editable(artifact.get("filename"), artifact.get("content_type")):
            raise ArtifactOperationError(415, "artifact is not text-editable")

        live = self.live_path(artifact)
        current_version_id = artifact.get("latest_version_id")
        current = (
            self.store.version_meta(current_version_id) if current_version_id else None
        )
        try:
            if (
                current
                and not current.get("snapshot_path")
                and current.get("path")
                and Path(current["path"]).resolve() == live.resolve()
                and live.exists()
            ):
                self.write_version_snapshot(
                    current_version_id,
                    artifact["filename"],
                    data=live.read_bytes(),
                )
        except OSError:
            pass

        raw = content.encode("utf-8")
        try:
            live.parent.mkdir(parents=True, exist_ok=True)
            live.write_text(content, encoding="utf-8")
        except OSError as error:
            raise ArtifactOperationError(500, f"write failed: {error}") from error

        record = self.store.save_artifact(
            path=str(live),
            filename=artifact["filename"],
            content_type=artifact.get("content_type"),
            size_bytes=len(raw),
            checksum=hashlib.sha256(raw).hexdigest(),
            frame_id=artifact.get("root_frame_id"),
            project_id=artifact.get("project_id"),
            artifact_id=artifact_id,
        )
        self.write_version_snapshot(
            record["version_id"], artifact["filename"], data=raw
        )
        root_frame_id = artifact.get("root_frame_id")
        self._notify(
            root_frame_id,
            {
                "type": "artifact_created",
                "artifact": {
                    "id": artifact_id,
                    "filename": artifact["filename"],
                    "version_id": record["version_id"],
                    "root_frame_id": root_frame_id,
                },
            },
            broadcast,
        )
        return {
            "ok": True,
            "artifact_id": artifact_id,
            "version_id": record["version_id"],
            "size_bytes": len(raw),
        }

    def rename(
        self,
        artifact_id: str,
        filename: str | None,
        *,
        broadcast: Broadcast | None = None,
    ) -> dict:
        """Rename artifact metadata; the historical live file stays in place."""
        if not filename:
            raise ArtifactOperationError(400, "filename required")
        artifact = self.store.get_artifact(artifact_id)
        if not artifact:
            raise ArtifactOperationError(404, "artifact not found")
        self.live_path({**artifact, "filename": filename})
        self.store.rename_artifact(artifact_id, filename)
        root_frame_id = artifact.get("root_frame_id")
        self._notify(
            root_frame_id,
            {
                "type": "artifact_created",
                "artifact": {
                    "id": artifact_id,
                    "filename": filename,
                    "root_frame_id": root_frame_id,
                },
            },
            broadcast,
        )
        return {"ok": True, "artifact_id": artifact_id, "filename": filename}

    def upload(
        self,
        payload: dict,
        *,
        broadcast: Broadcast | None = None,
    ) -> dict:
        """Decode and register one JSON/base64 upload as a versioned artifact."""
        filename = payload.get("filename") or f"upload-{uuid.uuid4().hex[:8]}"
        encoded = payload.get("content_base64") or payload.get("content") or ""
        frame_id = payload.get("frame_id")
        project_id = payload.get("project_id") or "default"
        try:
            raw = base64.b64decode(encoded) if encoded else b""
        except (binascii.Error, ValueError):
            raw = encoded.encode("utf-8") if isinstance(encoded, str) else b""

        workspace = (
            self.workspace_for(frame_id) if frame_id else self.data_dir / "uploads"
        )
        workspace.mkdir(parents=True, exist_ok=True)
        target = workspace / Path(filename).name
        target.write_bytes(raw)
        existing = (
            self.store.artifact_by_filename(target.name, frame_id, strict=True)
            if frame_id
            else None
        )
        record = self.store.save_artifact(
            path=str(target),
            filename=target.name,
            content_type=self.guess_content_type(target.name),
            size_bytes=len(raw),
            checksum=hashlib.sha256(raw).hexdigest(),
            frame_id=frame_id,
            project_id=project_id,
            is_user_upload=True,
            artifact_id=(existing["artifact_id"] if existing else None),
        )
        self.write_version_snapshot(record["version_id"], target.name, data=raw)
        self._notify(
            frame_id,
            {
                "type": "artifact_created",
                "artifact": {
                    "id": record["artifact_id"],
                    "filename": target.name,
                    "content_type": record.get("content_type"),
                    "root_frame_id": frame_id,
                },
            },
            broadcast,
        )
        return {
            "artifact_id": record["artifact_id"],
            "id": record["artifact_id"],
            "filename": target.name,
        }

    def delete(
        self,
        artifact_id: str,
        *,
        broadcast: Broadcast | None = None,
    ) -> dict:
        """Delete an artifact, reclaim unreferenced files, and notify its frame."""
        artifact = self.store.get_artifact(artifact_id)
        stale_paths = self.store.delete_artifact(artifact_id)
        root_frame_id = artifact.get("root_frame_id") if artifact else None
        trusted_roots = [self.versions_dir()]
        if root_frame_id:
            trusted_roots.append(self.workspace_for(root_frame_id))
        else:
            trusted_roots.append(self.data_dir / "uploads")
        for path in stale_paths:
            try:
                candidate = Path(os.path.abspath(Path(path).expanduser()))
                if candidate.is_symlink():
                    continue
                resolved = candidate.resolve(strict=False)
                allowed = False
                for root in trusted_roots:
                    lexical_root = Path(os.path.abspath(root))
                    resolved_root = root.resolve()
                    if (
                        candidate == lexical_root or lexical_root in candidate.parents
                    ) and (
                        resolved == resolved_root or resolved_root in resolved.parents
                    ):
                        allowed = True
                        break
                if not allowed:
                    continue
                candidate.unlink()
            except OSError:
                pass
        self._notify(
            root_frame_id,
            {
                "type": "artifact_created",
                "root_frame_id": root_frame_id,
            },
            broadcast,
        )
        return {"ok": True}

    def snapshot(self, workspace: Path) -> dict[str, int]:
        """Return mtimes for deliverables, excluding dependency/repository trees."""
        try:
            repo_roots = {git_dir.parent for git_dir in workspace.rglob(".git")}
        except OSError:
            repo_roots = set()
        result: dict[str, int] = {}
        for path in workspace.rglob("*"):
            if not path.is_file() or _ignored_file(path.relative_to(workspace)):
                continue
            if repo_roots and any(root in path.parents for root in repo_roots):
                continue
            try:
                result[str(path)] = path.stat().st_mtime_ns
            except OSError:
                pass
        return result

    def register_file(
        self,
        session: ArtifactSession,
        path: Path,
        cell_id: str | None,
        emit: EventSink,
        env_snapshot_id: str | None = None,
    ) -> dict | None:
        """Persist one produced file as a versioned artifact and notify the UI."""
        relative = str(path.relative_to(session.workspace))
        try:
            size = path.stat().st_size
            checksum = self.checksum(path)
        except OSError:
            return None
        record = self.store.record_cell_artifact(
            path=str(path),
            filename=relative,
            content_type=self.guess_content_type(relative),
            size_bytes=size,
            checksum=checksum,
            producing_cell_id=cell_id,
            frame_id=session.root_frame_id,
            root_frame_id=session.root_frame_id,
            project_id=session.project_id,
            env_snapshot_id=env_snapshot_id,
            preserve_filename=True,
            preserve_content_type=True,
        )
        display_filename = record.get("filename") or relative
        self.write_version_snapshot(
            record["version_id"], display_filename, src_path=path
        )
        emit(
            {
                "type": "artifact_created",
                "producing_cell_id": cell_id,
                "artifact": {
                    "id": record["artifact_id"],
                    "artifact_id": record["artifact_id"],
                    "version_id": record["version_id"],
                    "filename": display_filename,
                    "content_type": record.get("content_type"),
                    "size_bytes": size,
                    "project_id": session.project_id,
                    "root_frame_id": session.root_frame_id,
                    "producing_cell_id": cell_id,
                },
            }
        )
        try:
            version_number = len(self.store.list_versions(record["artifact_id"]))
        except Exception:  # noqa: BLE001
            version_number = 1
        return {
            "artifact_id": record["artifact_id"],
            "version_id": record["version_id"],
            "version_number": version_number,
            "filename": display_filename,
            "content_type": record.get("content_type"),
            "size_bytes": size,
            "checksum": checksum,
            "storage_path": record.get("path"),
        }

    def capture(
        self,
        session: ArtifactSession,
        cell_index: int,
        cell_id: str | None,
        before: dict[str, int],
        emit: EventSink,
        language: str = "python",
        run_system_cell: Callable[[str], dict] | None = None,
        drain_remote_provenance: Callable[[], Any] | None = None,
    ) -> CaptureResult:
        figures: list[str] = []
        if language == "python" and run_system_cell is not None:
            try:
                response = run_system_cell(_capture_snippet(cell_index))
                for line in (response.get("stdout") or "").splitlines():
                    if line.startswith("__OSFIGS__"):
                        try:
                            figures = json.loads(line[len("__OSFIGS__") :]) or []
                        except (ValueError, TypeError):
                            figures = []
            except Exception:  # noqa: BLE001 — capture is best-effort
                figures = []
        after = self.snapshot(session.workspace)
        changed = [
            Path(path) for path, mtime in after.items() if before.get(path) != mtime
        ]
        figure_set = set(figures)
        files_written: list[str] = []
        artifacts: list[dict] = []
        env_snapshot_id = (
            self.capture_environment(drain_remote_provenance) if changed else None
        )
        for path in sorted(
            changed,
            key=lambda item: (
                str(item.relative_to(session.workspace)) not in figure_set,
                str(item),
            ),
        ):
            relative = str(path.relative_to(session.workspace))
            metadata = self.register_file(
                session,
                path,
                cell_id,
                emit,
                env_snapshot_id=env_snapshot_id,
            )
            if metadata is not None:
                artifacts.append(metadata)
            if relative not in figure_set:
                files_written.append(relative)
        return CaptureResult(figures, files_written, artifacts)

    def capture_environment(
        self, drain_remote_provenance: Callable[[], Any] | None = None
    ) -> str | None:
        """Freeze the local env plus buffered remote-compute provenance once."""
        try:
            snapshot = self.environment_snapshot()
            if drain_remote_provenance is not None:
                remote = drain_remote_provenance()
                if remote:
                    snapshot["remote"] = remote
            return self.store.upsert_env_snapshot(snapshot)
        except Exception:  # noqa: BLE001 — provenance cannot break artifact saving
            return None


def _capture_snippet(index: int) -> str:
    return (
        "import json as __oj\n"
        "__osfigs=[]\n"
        "try:\n"
        " import sys as __sys\n"
        " if 'matplotlib' in __sys.modules:\n"
        "  import matplotlib.pyplot as __plt\n"
        "  for __n in list(__plt.get_fignums()):\n"
        f"   __nm='figure_cell{index}_'+str(__n)+'.png'\n"
        "   try:\n"
        "    __plt.figure(__n).savefig(__nm,dpi=130,bbox_inches='tight')\n"
        "    __plt.close(__n); __osfigs.append(__nm)\n"
        "   except Exception: pass\n"
        "except Exception: pass\n"
        "print('__OSFIGS__'+__oj.dumps(__osfigs))\n"
    )


def _ignored_file(path: Path) -> bool:
    parts = path.parts
    if any(part.startswith(".") for part in parts):
        return True
    if any(
        part in _JUNK_DIR_SEGMENTS or part.endswith((".egg-info", ".dist-info"))
        for part in parts
    ):
        return True
    return path.name.endswith((".pyc", ".pyo"))


def is_text_editable(filename: str | None, content_type: str | None) -> bool:
    name = (filename or "").lower()
    content = (content_type or "").lower()
    if content.startswith("image/") or name.endswith(_BINARY_EXT):
        return False
    return (
        name.endswith(_TEXT_EDIT_EXT)
        or content.startswith("text/")
        or any(kind in content for kind in ("json", "csv", "xml", "javascript"))
    )


__all__ = ["ArtifactManager", "ArtifactOperationError", "is_text_editable"]
