"""Versioned workspace artifact capture for persistent scientific sessions."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Protocol

from openai4s.execution import CaptureResult

_JUNK_DIR_SEGMENTS = frozenset(
    {"__pycache__", "node_modules", "site-packages", "venv"}
)
EventSink = Callable[[dict[str, Any]], None]


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

    def versions_dir(self) -> Path:
        directory = self.data_dir / "artifact-versions"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def live_path(self, artifact: dict) -> Path:
        root_frame_id = artifact.get("root_frame_id") or "default"
        return self.workspace_for(root_frame_id) / artifact["filename"]

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
                        meta.get("filename")
                        or artifact.get("filename")
                        or "artifact",
                        src_path=path,
                    )
            except Exception:  # noqa: BLE001
                continue

    def restore(self, artifact_id: str, version_id: str) -> dict:
        """Make a historical version current and restore its workspace bytes."""
        artifact = self.store.get_artifact(artifact_id)
        version = self.store.version_meta(version_id)
        if not artifact or not version or version.get("artifact_id") != artifact_id:
            return {"error": "version not found"}
        source = version.get("snapshot_path") or version.get("path")
        if not source:
            return {"error": "version has no stored bytes"}
        try:
            data = Path(source).read_bytes()
            live = self.live_path(artifact)
            current_id = artifact.get("latest_version_id")
            current = self.store.version_meta(current_id) if current_id else None
            if (
                current
                and not current.get("snapshot_path")
                and current.get("path")
                and Path(current["path"]).resolve() == live.resolve()
                and live.exists()
            ):
                self.write_version_snapshot(
                    current_id, artifact["filename"], data=live.read_bytes()
                )
            live.parent.mkdir(parents=True, exist_ok=True)
            live.write_bytes(data)
        except OSError as error:
            return {"error": f"restore failed: {error}"}
        self.store.set_latest_version(artifact_id, version_id)
        if artifact.get("root_frame_id"):
            self.broadcast(
                artifact["root_frame_id"],
                {
                    "type": "artifact_created",
                    "root_frame_id": artifact["root_frame_id"],
                },
            )
        return {
            "ok": True,
            "artifact": self.store.get_artifact(artifact_id),
        }

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
        cell_id: str,
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
                "artifact": {
                    "id": record["artifact_id"],
                    "artifact_id": record["artifact_id"],
                    "version_id": record["version_id"],
                    "filename": display_filename,
                    "content_type": record.get("content_type"),
                    "size_bytes": size,
                    "project_id": session.project_id,
                    "root_frame_id": session.root_frame_id,
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
        cell_id: str,
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
        changed = [Path(path) for path, mtime in after.items() if before.get(path) != mtime]
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


__all__ = ["ArtifactManager"]
