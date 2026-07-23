"""Versioned workspace artifact capture for persistent scientific sessions."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import mimetypes
import os
import platform as _pf
import re
import shutil
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from openai4s.artifact_restore import ArtifactRestoreService
from openai4s.execution import CaptureResult

_JUNK_DIR_SEGMENTS = frozenset({"__pycache__", "node_modules", "site-packages", "venv"})
_EMBEDDED_IMAGE_TYPES = frozenset(
    {"image/gif", "image/jpeg", "image/png", "image/webp"}
)
_MAX_EMBEDDED_FIGURE_BYTES = 8 * 1024 * 1024
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


@dataclass(frozen=True)
class PromotionTarget:
    """A minimal ArtifactSession for REST-time cell promotion.

    Promoting a cell happens outside any live kernel session, so the gateway
    supplies just the three fields ``register_file`` needs rather than reviving
    a full SessionState.
    """

    root_frame_id: str
    project_id: str
    workspace: Path


def _md_fence(body: str) -> str:
    """A backtick fence guaranteed longer than any backtick run in ``body``."""
    longest = max((len(run) for run in re.findall(r"`+", body)), default=0)
    return "`" * max(3, longest + 1)


def _write_confined_text(workspace: Path, relative: Path, content: str) -> Path:
    """Write under ``workspace`` without following a final-component symlink."""
    root = workspace.expanduser().resolve()
    directory = root / relative.parent
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise OSError("artifact directory must not be a symlink")
    resolved_directory = directory.resolve(strict=True)
    resolved_directory.relative_to(root)
    target = resolved_directory / relative.name
    if target.is_symlink():
        raise OSError("artifact target must not be a symlink")
    target.resolve(strict=False).relative_to(root)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    directory_descriptor: int | None = None
    try:
        if os.open in os.supports_dir_fd:
            directory_flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            directory_descriptor = os.open(resolved_directory, directory_flags)
            descriptor = os.open(
                relative.name,
                flags,
                0o600,
                dir_fd=directory_descriptor,
            )
        else:  # pragma: no cover - native Windows kernels are unsupported
            descriptor = os.open(target, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
    finally:
        if directory_descriptor is not None:
            os.close(directory_descriptor)
    if target.is_symlink() or not target.resolve(strict=True).is_relative_to(root):
        raise OSError("artifact target escaped its workspace")
    return target


def _same_interpreter(interpreter: Any, has_generation: bool = False) -> bool:
    """True when the kernel ran in this very process's interpreter.

    Only then may this process's own version strings be attributed to it.

    A *missing* interpreter is the daemon fallback only when there is no
    generation on record. With a generation but no interpreter — a legacy or
    imported one — the runtime is unknown, and stamping the daemon's Python
    version and implementation onto it is the same confidently-wrong provenance
    the package-list path already refuses. So a missing interpreter matches
    only in the no-generation case.
    """
    import os
    import sys

    if not interpreter:
        return not has_generation
    try:
        return os.path.realpath(str(interpreter)) == os.path.realpath(sys.executable)
    except OSError:
        return False


class ArtifactManager:
    #: A generation ends when its kernel does, so this cannot grow without
    #: bound in practice. The ceiling is a backstop against a session that
    #: restarts its kernel thousands of times, not a tuning knob.
    _FREEZE_CACHE_MAX = 256

    def __init__(
        self,
        *,
        data_dir: Path,
        store: Any,
        workspace_for: Callable[[str], Path],
        broadcast: Callable[[str, dict], None],
        guess_content_type: Callable[[str], str],
        checksum: Callable[[Path], str],
    ) -> None:
        self.data_dir = data_dir
        self.store = store
        self.workspace_for = workspace_for
        self.broadcast = broadcast
        self.guess_content_type = guess_content_type
        self.checksum = checksum
        # (generation_id, interpreter) -> frozen packages, or None when the
        # interpreter refused to be read. See _frozen_packages.
        self._freeze_cache: dict[tuple[str, str], list[dict[str, Any]] | None] = {}
        self._freeze_lock = threading.Lock()

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

    def promote_cell(
        self,
        session: ArtifactSession,
        cell: dict,
        emit: EventSink,
    ) -> dict | None:
        """Freeze one notebook cell as a self-contained Markdown artifact.

        A cell's *files* are already captured as artifacts when it runs (see
        ``capture``); promotion fixes the analysis *step* itself — its code,
        stdout, and pointers to what it produced — into a shareable, versioned
        document the Files panel manages like any other artifact. The target
        path is derived from the cell id, so re-promoting the same cell rewrites
        the same file and the store versions it in place instead of spawning a
        duplicate.
        """
        cell_id = str(cell.get("producing_cell_id") or "").strip() or None
        index = cell.get("cell_index")
        stem = f"cell-{index}" if index is not None else "cell"
        token = hashlib.sha1((cell_id or stem).encode("utf-8")).hexdigest()[:8]
        relative = Path("promoted") / f"{stem}-{token}.md"
        try:
            _write_confined_text(
                session.workspace,
                relative,
                self._render_cell_markdown(cell, session.workspace),
            )
        except (OSError, ValueError):
            return None
        # _write_confined_text returns a fully-resolved path, but register_file
        # relativizes against the unresolved session.workspace; hand it the
        # unresolved path (same on-disk file) so relative_to() cannot raise when
        # the workspace prefix contains a symlink (e.g. /tmp -> /private/tmp).
        return self.register_file(session, session.workspace / relative, cell_id, emit)

    def _render_cell_markdown(self, cell: dict, workspace: Path) -> str:
        """Render a cell (code + output + produced files) as Markdown."""
        index = cell.get("cell_index")
        language = str(cell.get("language") or cell.get("kernel_id") or "python")
        heading = f"Cell {index}" if index is not None else "Notebook cell"
        source = (cell.get("source") or "").rstrip("\n")
        fence = _md_fence(source)
        lines: list[str] = [f"# {heading}", "", f"{fence}{language}", source, fence]
        stdout = (cell.get("stdout") or "").rstrip("\n")
        if stdout:
            out_fence = _md_fence(stdout)
            lines += ["", "## Output", "", out_fence, stdout, out_fence]
        error = (cell.get("error") or "").rstrip("\n")
        if error:
            err_fence = _md_fence(error)
            lines += ["", "## Error", "", err_fence, error, err_fence]
        figures = [str(fig) for fig in (cell.get("figures") or []) if fig]
        if figures:
            lines += ["", "## Figures", ""]
            lines += [self._render_promoted_figure(workspace, fig) for fig in figures]
        files = [str(name) for name in (cell.get("files_written") or []) if name]
        if files:
            lines += ["", "## Produced files", ""]
            lines += [f"- `{name}`" for name in files]
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_promoted_figure(workspace: Path, figure: str) -> str:
        """Embed a confined raster figure so the Markdown stays shareable."""
        label = Path(figure).name or "figure"
        try:
            root = workspace.expanduser().resolve()
            candidate = (root / figure).resolve(strict=True)
            candidate.relative_to(root)
            media_type = mimetypes.guess_type(candidate.name)[0] or ""
            size = candidate.stat().st_size
            if media_type not in _EMBEDDED_IMAGE_TYPES or not (
                0 < size <= _MAX_EMBEDDED_FIGURE_BYTES
            ):
                raise ValueError("figure is not an embeddable raster image")
            encoded = base64.b64encode(candidate.read_bytes()).decode("ascii")
            return f"![{label}](data:{media_type};base64,{encoded})"
        except (OSError, ValueError):
            # Preserve a useful, non-broken pointer when a historical figure is
            # missing, too large, unsupported, or outside the workspace.
            return f"- Figure artifact: `{figure}`"

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
        # `language` and the session's frame id were already in scope here and
        # simply were not passed on, which is why every artifact was stamped
        # with the daemon's Python environment regardless of what ran.
        env_snapshot_id = (
            self.capture_environment(
                drain_remote_provenance,
                root_frame_id=getattr(session, "root_frame_id", None),
                language=language,
            )
            if changed
            else None
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
        self,
        drain_remote_provenance: Callable[[], Any] | None = None,
        *,
        root_frame_id: str | None = None,
        language: str = "python",
    ) -> str | None:
        """Record the environment of the kernel that produced these files.

        It used to record the *daemon's* — a zero-argument freeze of this
        process, stamped ``kind: "python"`` whatever had actually run. An R
        cell's artifact therefore carried a Python package list, and so did a
        Python cell running in a selected conda environment. Both are the same
        failure: provenance that is wrong rather than absent, presented by the
        UI as the kernel's own.

        The kernel generation is the authority. It knows the runtime, the
        interpreter, and the environment name, and its id ties the artifact to
        one exact kernel lifetime.
        """
        try:
            generation = self._generation_for(root_frame_id, language)
            snapshot = self._snapshot_for(generation, language)
            if drain_remote_provenance is not None:
                remote = drain_remote_provenance()
                if remote:
                    snapshot["remote"] = remote
            return self.store.upsert_env_snapshot(snapshot)
        except Exception:  # noqa: BLE001 — provenance cannot break artifact saving
            return None

    def _generation_for(
        self, root_frame_id: str | None, language: str
    ) -> dict[str, Any] | None:
        """The generation that actually produced these files, on this branch.

        Generations are registered per ``branch_id``, and the repository
        defaults an omitted one to ``root_frame_id`` — the root branch. Omitting
        it here meant a file written by a cell on a *forked* branch was
        attributed to the root branch's most recent kernel, or, if the root had
        none, degraded to the assumed snapshot. Either way the artifact's
        interpreter and package provenance described a kernel that did not
        produce it, which is the failure this whole path exists to prevent.
        """
        if not root_frame_id:
            return None
        latest = getattr(self.store, "latest_kernel_generation", None)
        if latest is None:
            return None
        try:
            active = getattr(self.store, "active_session_branch", None)
            branch_id = active(root_frame_id) if callable(active) else None
            return latest(root_frame_id, language, branch_id=branch_id or None)
        except Exception:  # noqa: BLE001
            return None

    def _snapshot_for(
        self, generation: dict[str, Any] | None, language: str
    ) -> dict[str, Any]:
        """Build the snapshot from what the generation actually says.

        With no generation on record -- a cell that wrote files before any
        kernel was registered, or a store that predates them -- fall back to
        describing this process, but say so, so a reader can tell a measured
        environment from an assumed one.
        """
        from openai4s.kernel import preinstall

        environment = (generation or {}).get("environment")
        environment = environment if isinstance(environment, dict) else {}
        runtime = str(environment.get("runtime") or language or "python").lower()
        interpreter = environment.get("interpreter")

        snapshot: dict[str, Any] = {
            "kind": runtime,
            "interpreter": interpreter,
            "environment_name": environment.get("environment_name"),
            "platform": _pf.platform(),
        }
        if generation:
            snapshot["generation_id"] = generation.get("generation_id")
            snapshot["environment_manifest_id"] = generation.get(
                "environment_manifest_id"
            )
        else:
            snapshot["provenance"] = "assumed: no kernel generation on record"

        if runtime == "python":
            if interpreter:
                packages = self._frozen_packages(interpreter, generation)
            elif generation:
                # A generation *is* on record — legacy, imported, or written
                # before the environment carried an interpreter path. Freezing
                # the daemon here attributed this process's packages to that
                # generation id, which is confidently wrong provenance rather
                # than absent provenance. The daemon may only describe the case
                # where no generation exists at all.
                packages = None
            else:
                packages = preinstall.full_freeze()
            if packages is None:
                # Naming what we could not read beats implying the daemon's
                # packages were this kernel's.
                snapshot["packages"] = []
                snapshot["package_count"] = 0
                snapshot["packages_unavailable"] = (
                    f"could not read distributions from {interpreter!r}"
                    if interpreter
                    else (
                        "this kernel generation records no interpreter, and "
                        "the daemon's packages are not this kernel's"
                    )
                )
            else:
                snapshot["packages"] = packages
                snapshot["package_count"] = len(packages)
            snapshot["python_version"] = (
                _pf.python_version()
                if _same_interpreter(interpreter, bool(generation))
                else None
            )
            snapshot["implementation"] = (
                _pf.python_implementation()
                if _same_interpreter(interpreter, bool(generation))
                else None
            )
        else:
            # A non-Python kernel has no Python package set, and claiming an
            # empty one would read as "nothing installed" rather than "not
            # applicable".
            snapshot["packages"] = []
            snapshot["package_count"] = 0
            snapshot[
                "packages_unavailable"
            ] = f"{runtime} kernel: Python distribution metadata does not apply"
        return snapshot

    def _frozen_packages(
        self, interpreter: Any, generation: dict[str, Any] | None
    ) -> list[dict[str, Any]] | None:
        """Freeze a foreign interpreter once per kernel generation.

        ``freeze_for`` launches the target interpreter and enumerates its
        distributions — up to a 20-second wait. Its docstring says callers
        cache per generation because an environment cannot change within one;
        no caller did, so every cell that produced a file paid the full probe
        again. A persistent kernel writing a figure per cell paid it per
        figure.

        A failed probe is cached too: an interpreter that could not be read
        will not become readable within the same generation, and re-paying the
        timeout to rediscover that is the worst version of this.

        Keyed by generation because that is the exact lifetime over which the
        answer is constant. Without one there is nothing bounding the
        environment's stability, so the probe runs.
        """
        from openai4s.kernel import preinstall

        generation_id = str((generation or {}).get("generation_id") or "")
        if not generation_id:
            return preinstall.freeze_for(interpreter)
        key = (generation_id, str(interpreter))
        with self._freeze_lock:
            if key in self._freeze_cache:
                return self._freeze_cache[key]
        packages = preinstall.freeze_for(interpreter)
        with self._freeze_lock:
            # Bounded: one entry per (generation, interpreter), and a
            # generation ends when its kernel does.
            if len(self._freeze_cache) >= self._FREEZE_CACHE_MAX:
                self._freeze_cache.clear()
            self._freeze_cache[key] = packages
        return packages


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
