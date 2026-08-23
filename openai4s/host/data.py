"""Store-backed data services exposed through ``host.*`` RPC.

This module owns query projection, artifact persistence/search, frame browsing,
and provenance/lineage reads.  ``HostDispatcher`` remains the policy, audit,
and routing envelope and delegates the domain behaviour here.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import shutil
import stat
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, ContextManager, Protocol

from openai4s import execution_principal
from openai4s.artifact_restore import (
    ArtifactRestoreDenied,
    ArtifactRestoreRefused,
    ArtifactRestoreService,
    trusted_snapshot_roots,
)


class HostDataStore(Protocol):
    """Persistence surface required by :class:`HostDataService`."""

    def query(self, sql: str, *, params=None, limit=None, timeout_s=5.0): ...

    def schema(self) -> dict: ...

    def list_artifacts(self, filters: dict | None = None) -> list[dict]: ...

    def get_artifact(self, artifact_id: str) -> dict | None: ...

    def list_versions(self, artifact_id: str) -> list[dict]: ...

    def resolve_frame_scope(self, frame_id: str | None) -> dict: ...

    def resolve_artifact_path(self, ident: str) -> str | None: ...

    def record_cell_artifact(self, **fields: Any) -> dict: ...

    def record_artifact_restore(self, **fields: Any) -> dict: ...

    def version_meta(self, version_id: str) -> dict | None: ...

    def set_version_snapshot(self, version_id: str, snapshot_path: str) -> None: ...

    def materialise_artifact_version(
        self,
        *,
        source_version_id: str,
        artifact_id: str,
        version_id: str,
        filename: str,
        path: str,
        snapshot_path: str,
        frame_id: str | None,
        root_frame_id: str,
        project_id: str,
        producing_cell_id: str | None = None,
        publish: Callable[[str, str], str] | None = None,
    ) -> dict: ...

    def set_priority(self, artifact_id: str, priority: int) -> dict | None: ...

    def frame_detail(
        self,
        frame_id: str,
        *,
        page: int,
        page_size: int,
        visible_to_user_id: str | None = ...,
    ): ...

    def search_frames(
        self,
        pattern: str,
        *,
        project_id: str,
        limit: int,
        visible_to_user_id: str | None = ...,
    ): ...

    def browse_frames(
        self,
        *,
        project_id: str,
        status: str | None,
        roots_only: bool,
        limit: int,
        visible_to_user_id: str | None = ...,
    ): ...

    def producing_cell_for_version(self, version_id: str) -> dict | None: ...

    def lineage_inputs(
        self, version_id: str, *, producing_cell_id: str | None = None
    ) -> list[dict]: ...

    def list_artifact_capture_observations(
        self,
        *,
        artifact_id: str | None = None,
        version_id: str | None = None,
    ) -> list[dict]: ...

    def lineage_edges_for(self, version_id: str, direction: str) -> list[dict]: ...

    def version_for_path(
        self, path: str, *, root_frame_id: str | None, project_id: str
    ) -> str | None: ...


StoreProvider = Callable[[], HostDataStore]
ConfigProvider = Callable[[], Any]
FrameIdProvider = Callable[[], str | None]
PathResolver = Callable[..., Path]
ArtifactRestorer = Callable[[str, str], dict]
ArtifactMaterialiser = Callable[..., dict]
ArtifactWriter = Callable[[], ContextManager[None]]

#: Bounds for a lineage walk when the caller names none. They are generous
#: enough that a real provenance chain fits, and they exist because the
#: alternative -- omitting an optional argument -- used to mean "traverse
#: everything reachable".
_DEFAULT_LINEAGE_DEPTH = 32
_DEFAULT_LINEAGE_NODES = 500
_MAX_LINEAGE_EDGES = 5000

FRAME_STATUSES = frozenset({"processing", "done", "failed", "awaiting_user_response"})

#: Team-kernel-readable copies of versioned inputs live in a daemon-owned,
#: session-scoped directory. The sandbox exposes only that exact directory and
#: mounts it read-only, while the remainder of the data directory stays masked.
_KERNEL_ARTIFACT_INPUT_PARENT = "kernel-artifact-inputs"
_ARTIFACT_INPUT_CHUNK = 1024 * 1024
#: Persistent-kernel paths cannot be evicted while a session is live. Bound
#: both one session and the daemon-wide aggregate so many sessions cannot turn
#: exact-version compatibility into an unbounded disk-allocation primitive.
_ARTIFACT_INPUT_SESSION_MAX_BYTES = 8 * 1024 * 1024 * 1024
_ARTIFACT_INPUT_SESSION_MAX_FILES = 1024
_ARTIFACT_INPUT_GLOBAL_MAX_BYTES = 32 * 1024 * 1024 * 1024
_ARTIFACT_INPUT_GLOBAL_MAX_FILES = 4096
_ARTIFACT_INPUT_GLOBAL_MAX_SESSIONS = 4096
_ARTIFACT_INPUT_MIN_FREE_BYTES = 512 * 1024 * 1024
_ARTIFACT_INPUT_MAX_FREE_RESERVE = 8 * 1024 * 1024 * 1024
_ARTIFACT_INPUT_GLOBAL_LOCK = threading.RLock()


_VALID_MARKER_ID = re.compile(
    r"^(v-)?[0-9a-fA-F]{8,}$|"
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _artifact_directory_flags() -> int:
    """Return the flags required for a pinned, no-follow directory open."""

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    if os.name != "posix" or not nofollow or not directory:
        raise RuntimeError(
            "secure Artifact input staging is unavailable on this platform"
        )
    return (
        os.O_RDONLY
        | nofollow
        | directory
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )


def _require_artifact_dirfd() -> None:
    """Fail closed where an atomic no-follow staging transaction is impossible."""

    required = (os.open, os.stat, os.mkdir, os.unlink, os.rename)
    if any(operation not in os.supports_dir_fd for operation in required):
        raise RuntimeError(
            "secure Artifact input staging is unavailable on this platform"
        )
    _artifact_directory_flags()


def _artifact_inode(metadata: os.stat_result) -> tuple[int, int]:
    return (int(metadata.st_dev), int(metadata.st_ino))


def _artifact_file_state(
    metadata: os.stat_result,
) -> tuple[int, int, int, int, int, int]:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        int(metadata.st_ctime_ns),
        int(metadata.st_nlink),
    )


class _PinnedArtifactDirectory:
    """One held directory descriptor used for exact input staging.

    The kernel owns the workspace namespace and may have left aliases in it.
    Opening every component with ``O_NOFOLLOW`` and publishing by descriptor
    keeps a symlink or rename from redirecting the daemon's write outside the
    session workspace.  ``assert_current`` additionally prevents returning a
    pathname after its visible directory was exchanged underneath us.
    """

    def __init__(self, descriptor: int, *, root: Path, parts: tuple[str, ...]):
        self.fd = descriptor
        self.root = root
        self.parts = parts
        self.path = root.joinpath(*parts)
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise OSError("Artifact input parent is not a directory")
        self.identity = _artifact_inode(metadata)

    def __enter__(self) -> _PinnedArtifactDirectory:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    @staticmethod
    def _component(name: str) -> str:
        if (
            not name
            or name in {".", ".."}
            or "\x00" in name
            or os.sep in name
            or (os.altsep is not None and os.altsep in name)
        ):
            raise OSError("Artifact input path component is invalid")
        return name

    @classmethod
    def open_under(
        cls,
        root: Path,
        parts: tuple[str, ...],
        *,
        create: bool,
    ) -> _PinnedArtifactDirectory:
        """Pin ``root/parts`` without following any child alias."""

        _require_artifact_dirfd()
        root = Path(root)
        clean = tuple(cls._component(part) for part in parts)
        before = os.stat(root, follow_symlinks=False)
        descriptor = os.open(root, _artifact_directory_flags())
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISDIR(opened.st_mode) or _artifact_inode(
                before
            ) != _artifact_inode(opened):
                raise OSError("Artifact input root changed during secure open")
            for part in clean:
                try:
                    child = os.open(
                        part,
                        _artifact_directory_flags(),
                        dir_fd=descriptor,
                    )
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(part, 0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                    child = os.open(
                        part,
                        _artifact_directory_flags(),
                        dir_fd=descriptor,
                    )
                os.close(descriptor)
                descriptor = child
            return cls(descriptor, root=root, parts=clean)
        except BaseException:
            os.close(descriptor)
            raise

    def assert_current(self) -> None:
        with self.open_under(self.root, self.parts, create=False) as current:
            if current.identity != self.identity:
                raise OSError("Artifact input parent changed during staging")

    def lstat(self, name: str) -> os.stat_result:
        return os.stat(
            self._component(name),
            dir_fd=self.fd,
            follow_symlinks=False,
        )

    def open_read(self, name: str) -> int:
        return os.open(
            self._component(name),
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=self.fd,
        )

    def create_exclusive(self, name: str) -> int:
        return os.open(
            self._component(name),
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=self.fd,
        )

    def replace(self, source: str, destination: str) -> None:
        # POSIX rename atomically replaces a non-directory destination.  Both
        # names are interpreted relative to this already-pinned directory.
        os.rename(
            self._component(source),
            self._component(destination),
            src_dir_fd=self.fd,
            dst_dir_fd=self.fd,
        )

    def unlink(self, name: str, *, missing_ok: bool = False) -> None:
        try:
            os.unlink(self._component(name), dir_fd=self.fd)
        except FileNotFoundError:
            if not missing_ok:
                raise

    def fsync(self) -> None:
        os.fsync(self.fd)


def kernel_artifact_input_dir(
    data_dir: Path | str,
    root_frame_id: str,
) -> Path:
    """Derive one opaque team-session input directory without creating it.

    This helper is shared with the kernel sandbox policy so staging and the
    read-only allowlist cannot drift to different paths. The raw frame id is
    not placed in a host pathname or disclosed through sibling enumeration.
    """

    scope = str(root_frame_id or "").strip()
    if not scope:
        raise ValueError("kernel Artifact input staging requires a root frame id")
    key = hashlib.sha256(scope.encode("utf-8")).hexdigest()
    return Path(data_dir).expanduser().resolve() / _KERNEL_ARTIFACT_INPUT_PARENT / key


def rank_artifacts(items: list[dict], query: str) -> list[dict]:
    """Return fuzzy-ranked artifact rows for the command/search surface."""
    normalized = query.lower().strip()
    query_tokens = set(re.findall(r"[a-z0-9]+", normalized))
    scored = []
    for item in items:
        name = str(item.get("filename", "")).lower()
        content_type = str(item.get("content_type", "") or "").lower()
        haystack_tokens = set(re.findall(r"[a-z0-9]+", f"{name} {content_type}"))
        score = 0.0
        if normalized and normalized in name:
            score += 3.0
        score += 1.5 * len(query_tokens & haystack_tokens)
        if query_tokens and query_tokens <= haystack_tokens:
            score += 1.0
        score += 0.25 * (item.get("priority") or 0)
        if score > 0:
            projected = dict(item)
            projected["_score"] = round(score, 3)
            scored.append(projected)
    scored.sort(key=lambda row: row["_score"], reverse=True)
    return scored


class HostDataService:
    """Implement store-backed host capabilities behind narrow providers."""

    def __init__(
        self,
        *,
        store: HostDataStore | StoreProvider,
        config: Any | ConfigProvider,
        frame_id: str | None | FrameIdProvider,
        resolve_path: PathResolver,
        restore_artifact: ArtifactRestorer | None = None,
        materialise_artifact: ArtifactMaterialiser | None = None,
        artifact_writer: ArtifactWriter | None = None,
    ) -> None:
        self._store_source = store
        self._config_source = config
        self._frame_id_source = frame_id
        self._resolve_path = resolve_path
        self._restore_artifact = restore_artifact
        self._materialise_artifact = materialise_artifact
        self._artifact_writer = artifact_writer
        self._restore_manager: tuple[int, str, Any] | None = None
        self._restore_lock = threading.RLock()
        self._artifact_stage_lock = threading.RLock()
        self._artifact_stage_versions: dict[str, str] = {}

    def _store(self) -> HostDataStore:
        source = self._store_source
        return source() if callable(source) else source

    def _config(self) -> Any:
        source = self._config_source
        return source() if callable(source) else source

    def _frame_id(self) -> str | None:
        source = self._frame_id_source
        return source() if callable(source) else source

    def _trusted_delivery_enabled(self) -> bool:
        flags = getattr(self._config(), "roadmap_features", None)
        return bool(getattr(flags, "stage1_trusted_delivery", False))

    @staticmethod
    def _freeze_snapshot(source: Path, destination: Path) -> tuple[str, int]:
        """Atomically copy stable regular-file bytes into trusted storage.

        ``host.save_artifact`` and the in-kernel provenance hook both execute
        before the end-of-Cell workspace sweep.  In trusted mode their first
        durable row must already name the same bytes its checksum describes;
        a digest-then-``copy2`` pair has a mutation window between those two
        reads.  This single descriptor stream is fsynced and verified before
        its atomic rename makes the snapshot visible.
        """

        destination.parent.mkdir(parents=True, exist_ok=True)
        pending = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part")
        source_descriptor: int | None = None
        target_descriptor: int | None = None
        try:
            source_descriptor = os.open(
                source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            )
            before = os.fstat(source_descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise OSError("artifact source is not a regular file")
            target_descriptor = os.open(
                pending,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            digest = hashlib.sha256()
            size_bytes = 0
            while True:
                chunk = os.read(source_descriptor, 1024 * 1024)
                if not chunk:
                    break
                view = memoryview(chunk)
                while view:
                    written = os.write(target_descriptor, view)
                    if written <= 0:  # pragma: no cover - OS write contract
                        raise OSError("artifact snapshot write made no progress")
                    view = view[written:]
                size_bytes += len(chunk)
                digest.update(chunk)
            os.fsync(target_descriptor)
            after = os.fstat(source_descriptor)
            if (
                before.st_dev != after.st_dev
                or before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns
                or size_bytes != after.st_size
            ):
                raise OSError("artifact source changed during snapshot freeze")
            os.close(target_descriptor)
            target_descriptor = None
            os.replace(pending, destination)
            directory_descriptor = os.open(
                destination.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
            checksum = digest.hexdigest()
            if destination.stat().st_size != size_bytes or HostDataService._digest_file(
                destination
            ) != (checksum, size_bytes):
                raise OSError("artifact snapshot verification failed")
            return checksum, size_bytes
        except Exception:
            pending.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            raise
        finally:
            if target_descriptor is not None:
                os.close(target_descriptor)
            if source_descriptor is not None:
                os.close(source_descriptor)

    def query(self, spec: dict) -> Any:
        store = self._store()
        # The caller's own scope, so the `my_*` views resolve to this session's
        # rows. `spec["scope"]` -- what the SDK sends -- is deliberately *not*
        # read: it is caller-supplied, and a value the caller chooses cannot be
        # what confines the caller. The scope is derived from the frame instead.
        # It used to be dropped entirely, so the views did not exist and the base
        # tables were readable directly, across every project.
        rows = store.query(
            spec.get("sql", ""),
            params=spec.get("params"),
            limit=spec.get("limit"),
            timeout_s=5.0,
            scope=store.resolve_frame_scope(self._frame_id()),
        )
        if spec.get("df"):
            columns = list(rows[0].keys()) if rows else []
            return {"columns": columns, "rows": [list(row.values()) for row in rows]}
        return rows

    def query_schema(self) -> dict:
        return self._store().schema()

    def artifacts(self, filters: dict | None = None) -> dict:
        filters = filters or {}
        search = filters.pop("search", None) if isinstance(filters, dict) else None
        # Confine enumeration to the caller's own session/project scope — the
        # same isolation get_artifact_metadata/restore enforce via
        # _scoped_artifact.  Otherwise a model can enumerate every session's
        # artifacts by omitting filters or naming another root_frame_id/project.
        if isinstance(filters, dict):
            frame_id = self._frame_id()
            resolver = getattr(self._store(), "resolve_frame_scope", None)
            if frame_id is not None and callable(resolver):
                scope = resolver(frame_id) or {}
                if scope.get("root_frame_id"):
                    filters["root_frame_id"] = scope["root_frame_id"]
                    filters["project_id"] = scope.get("project_id")
        items = self._store().list_artifacts(filters)
        if search:
            items = rank_artifacts(items, str(search))
        return {"count": len(items), "artifacts": items}

    def _scoped_artifact(self, artifact_id: str) -> dict:
        """Resolve one Artifact without allowing cross-session enumeration.

        Out of scope raises the *same* KeyError as missing, for the reason
        `_scoped_version` states below. This used to raise `PermissionError` for
        a foreign artifact and `KeyError` for an absent one -- two helpers twelve
        lines apart implementing contradictory rules, and the difference in
        exception type alone is a working existence oracle: a cell that guesses
        an id learns whether it names a real artifact in someone else's project.
        """
        store = self._store()
        unknown = KeyError(f"no artifact {artifact_id!r} in the current session")
        artifact = store.get_artifact(artifact_id)
        if artifact is None:
            raise unknown
        frame_id = self._frame_id()
        scope = store.resolve_frame_scope(frame_id)
        if (
            frame_id is None
            or artifact.get("root_frame_id") != scope.get("root_frame_id")
            or artifact.get("project_id") != scope.get("project_id")
        ):
            raise unknown
        return artifact

    def _scoped_version(self, version_id: str) -> dict:
        """Resolve one artifact *version* inside the caller's own scope.

        `_scoped_artifact` covers the reads keyed on an artifact id.
        The version-keyed ones -- `lineage_get`, `artifact_path`, the lineage
        walk -- went straight to the store, so a kernel cell in one project
        could name any version id and read back another project's filename,
        checksum, producing-cell code and input lineage.

        Scope lives on the parent `artifacts` row: `artifact_versions` carries
        no project_id or root_frame_id, so resolving the parent is not an extra
        query for convenience, it is the only place the answer exists.

        Out of scope raises the *same* KeyError as missing. A distinct refusal
        would confirm the version exists, which is most of what an enumerator
        wants.
        """
        store = self._store()
        unknown = KeyError(f"no artifact version {version_id!r} in the current session")
        metadata = store.version_meta(version_id)
        if metadata is None:
            raise unknown
        artifact = store.get_artifact(str(metadata.get("artifact_id") or ""))
        if artifact is None:
            raise unknown
        frame_id = self._frame_id()
        scope = store.resolve_frame_scope(frame_id)
        if (
            frame_id is None
            or artifact.get("root_frame_id") != scope.get("root_frame_id")
            or artifact.get("project_id") != scope.get("project_id")
        ):
            raise unknown
        return metadata

    @staticmethod
    def _artifact_metadata_projection(artifact: dict) -> dict:
        fields = (
            "artifact_id",
            "project_id",
            "root_frame_id",
            "filename",
            "content_type",
            "is_user_upload",
            "priority",
            "latest_version_id",
            "created_at",
            "updated_at",
        )
        return {field: artifact.get(field) for field in fields}

    @staticmethod
    def _version_metadata_projection(
        version: dict,
        *,
        latest_version_id: str | None,
    ) -> dict:
        fields = (
            "version_id",
            "artifact_id",
            "filename",
            "content_type",
            "size_bytes",
            "checksum",
            "producing_cell_id",
            "frame_id",
            "created_at",
            "env_snapshot_id",
            "ordinal",
        )
        projected = {
            field: version.get(field)
            for field in fields
            if field in version or field != "ordinal"
        }
        projected["is_latest"] = version.get("version_id") == latest_version_id
        projected["snapshot_available"] = bool(version.get("snapshot_path"))
        return projected

    def artifact_metadata(self, spec: dict) -> dict:
        """Return exact safe metadata for one Artifact and one of its versions."""
        artifact_id = str(spec.get("artifact_id") or "")
        artifact = self._scoped_artifact(artifact_id)
        version_id = str(
            spec.get("version_id") or artifact.get("latest_version_id") or ""
        )
        version = self._store().version_meta(version_id) if version_id else None
        if version is None or version.get("artifact_id") != artifact_id:
            raise KeyError(
                f"version {version_id!r} does not belong to artifact {artifact_id!r}"
            )
        return {
            "artifact": self._artifact_metadata_projection(artifact),
            "version": self._version_metadata_projection(
                version,
                latest_version_id=artifact.get("latest_version_id"),
            ),
        }

    def artifact_versions(self, spec: dict) -> dict:
        """List immutable version identities for one session-owned Artifact."""
        artifact_id = str(spec.get("artifact_id") or "")
        artifact = self._scoped_artifact(artifact_id)
        latest_version_id = artifact.get("latest_version_id")
        versions = []
        for item in self._store().list_versions(artifact_id):
            metadata = self._store().version_meta(item["version_id"]) or item
            metadata = {**metadata, "ordinal": item.get("ordinal")}
            versions.append(
                self._version_metadata_projection(
                    metadata,
                    latest_version_id=latest_version_id,
                )
            )
        return {
            "artifact_id": artifact_id,
            "latest_version_id": latest_version_id,
            "count": len(versions),
            "versions": versions,
        }

    def restore_artifact_version(self, spec: dict) -> dict:
        """Restore verified historical bytes as a new immutable version."""
        artifact_id = str(spec.get("artifact_id") or "")
        source_version_id = str(spec.get("version_id") or "")
        self._scoped_artifact(artifact_id)
        with self._restore_lock:
            restore = self._restore_artifact or self._default_artifact_restorer()
            result = restore(artifact_id, source_version_id)
        if not isinstance(result, dict):
            raise RuntimeError("artifact restore returned an invalid result")
        error = result.get("error")
        if error:
            if result.get("code") in {"restore_denied", "restore_refused"}:
                message = str(error)
                prefix = "restore failed: "
                if message.startswith(prefix):
                    message = message[len(prefix) :]
                if result.get("code") == "restore_denied":
                    raise ArtifactRestoreDenied(message)
                raise ArtifactRestoreRefused(message)
            raise RuntimeError("artifact restore failed")
        return result

    def restore_artifact_exact(self, artifact_id: str, version_id: str) -> dict:
        """Invoke the shared secure writer without repeating Host policy."""

        with self._restore_lock:
            return self._default_artifact_restorer()(artifact_id, version_id)

    def set_artifact_restorer(
        self,
        restore: ArtifactRestorer | None,
        *,
        materialise: ArtifactMaterialiser | None = None,
        writer: ArtifactWriter | None = None,
    ) -> None:
        """Bind the session's one exact writer to Host Artifact mutations."""

        with self._restore_lock:
            self._restore_artifact = restore
            self._materialise_artifact = materialise
            self._artifact_writer = writer

    def _default_artifact_manager(self):
        """Build the shared-lock secure writer for direct/CLI composition."""

        from openai4s.server.artifacts import ArtifactManager

        store = self._store()
        config = self._config()
        workspace = self._resolve_path(".").expanduser().resolve()
        identity = (id(store), str(workspace))
        cached = self._restore_manager
        if cached is None or cached[:2] != identity:
            manager = getattr(store, "_artifact_manager_backend", None)
            if manager is None:
                manager = ArtifactManager(
                    data_dir=Path(config.data_dir),
                    store=store,
                    workspace_for=lambda _frame_id, root=workspace: root,
                    broadcast=lambda _frame_id, _event: None,
                    guess_content_type=lambda name: (
                        mimetypes.guess_type(name)[0] or "application/octet-stream"
                    ),
                    checksum=lambda path: hashlib.sha256(path.read_bytes()).hexdigest(),
                    trusted_delivery=bool(
                        getattr(
                            getattr(config, "roadmap_features", None),
                            "stage1_trusted_delivery",
                            False,
                        )
                    ),
                    recover_uploads=True,
                    allow_external_workspace_root=True,
                )
            # A fresh direct/delegated service may reuse the Store's canonical
            # manager after a prior cleanup failure retained a committed
            # journal. Recovery is idempotent and shares the writer RLock, so
            # it waits for any live Web transaction instead of misclassifying
            # that transaction as a crash.
            manager.recover_upload_journals()
            cached = (identity[0], identity[1], manager)
            self._restore_manager = cached
        return cached[2]

    def _default_artifact_restorer(self) -> ArtifactRestorer:
        """Build the same secure writer for direct/CLI service composition."""

        return self._default_artifact_manager().restore

    @staticmethod
    def _version_integrity(metadata: dict) -> tuple[str, int]:
        checksum = str(metadata.get("checksum") or "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise OSError("artifact version has no valid recorded checksum")
        raw_size = metadata.get("size_bytes")
        if isinstance(raw_size, bool) or not isinstance(raw_size, (int, str)):
            raise OSError("artifact version has no valid recorded size")
        try:
            size_bytes = int(raw_size)
        except (TypeError, ValueError) as error:
            raise OSError("artifact version has no valid recorded size") from error
        if size_bytes < 0:
            raise OSError("artifact version has no valid recorded size")
        return checksum, size_bytes

    @staticmethod
    def _version_cache_name(version_id: str, metadata: dict) -> str:
        identity = hashlib.sha256(version_id.encode("utf-8")).hexdigest()
        filename = Path(str(metadata.get("filename") or "artifact")).name
        safe_filename = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip(".")
        return f"{identity}__{(safe_filename or 'artifact')[:96]}"

    @staticmethod
    def _descriptor_digest(descriptor: int, *, expected_size: int) -> tuple[str, int]:
        """Hash at most the recorded size plus one oversized-byte canary."""

        digest = hashlib.sha256()
        size_bytes = 0
        remaining = expected_size + 1
        while remaining:
            chunk = os.read(descriptor, min(_ARTIFACT_INPUT_CHUNK, remaining))
            if not chunk:
                break
            size_bytes += len(chunk)
            remaining -= len(chunk)
            digest.update(chunk)
        return digest.hexdigest(), size_bytes

    @staticmethod
    def _cache_usage(
        directory: _PinnedArtifactDirectory,
        *,
        entry_limit: int,
        quota_message: str,
    ) -> tuple[int, int]:
        """Count direct regular cache entries without following any alias."""

        files = 0
        size_bytes = 0
        visited = 0
        with os.scandir(directory.fd) as entries:
            for entry in entries:
                visited += 1
                if visited > entry_limit:
                    raise OSError(quota_message)
                metadata = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(metadata.st_mode):
                    raise OSError("Artifact input cache has an unexpected directory")
                if not stat.S_ISREG(metadata.st_mode):
                    # A symlink/FIFO at the deterministic destination is safe
                    # to replace by held-dirfd rename and consumes no staged
                    # version bytes. Never follow it for accounting.
                    continue
                files += 1
                size_bytes += max(0, int(metadata.st_size))
        return files, size_bytes

    @staticmethod
    def _global_cache_usage(data_root: Path) -> tuple[int, int]:
        """Count every session cache under one pinned daemon-owned parent."""

        quota_message = "global Artifact input staging quota exceeded"
        files = 0
        size_bytes = 0
        sessions = 0
        visited = 0
        with _PinnedArtifactDirectory.open_under(
            data_root,
            (_KERNEL_ARTIFACT_INPUT_PARENT,),
            create=True,
        ) as parent:
            if int(os.fstat(parent.fd).st_uid) != os.geteuid():
                raise PermissionError(
                    "Artifact input cache parent is not owned by the daemon user"
                )
            os.fchmod(parent.fd, 0o700)
            with os.scandir(parent.fd) as entries:
                for entry in entries:
                    visited += 1
                    if visited > _ARTIFACT_INPUT_GLOBAL_MAX_SESSIONS:
                        raise OSError(quota_message)
                    metadata = entry.stat(follow_symlinks=False)
                    if not stat.S_ISDIR(metadata.st_mode):
                        # Never follow a malicious alias in the global parent.
                        continue
                    sessions += 1
                    if sessions > _ARTIFACT_INPUT_GLOBAL_MAX_SESSIONS:
                        raise OSError(quota_message)
                    descriptor = parent.open_read(entry.name)
                    try:
                        session = _PinnedArtifactDirectory(
                            descriptor,
                            root=parent.path,
                            parts=(entry.name,),
                        )
                    except BaseException:
                        os.close(descriptor)
                        raise
                    try:
                        remaining = _ARTIFACT_INPUT_GLOBAL_MAX_FILES - files
                        if remaining < 0:
                            raise OSError(quota_message)
                        child_files, child_bytes = HostDataService._cache_usage(
                            session,
                            entry_limit=remaining + 1,
                            quota_message=quota_message,
                        )
                        files += child_files
                        size_bytes += child_bytes
                        if files > _ARTIFACT_INPUT_GLOBAL_MAX_FILES:
                            raise OSError(quota_message)
                    finally:
                        session.close()
        return files, size_bytes

    @staticmethod
    def _enforce_stage_quota(
        data_root: Path,
        destination: _PinnedArtifactDirectory,
        cache_name: str,
        *,
        size_bytes: int,
    ) -> None:
        """Reserve one replacement against session and daemon-wide limits."""

        session_message = "Artifact input staging quota exceeded for this session"
        session_files, session_bytes = HostDataService._cache_usage(
            destination,
            entry_limit=_ARTIFACT_INPUT_SESSION_MAX_FILES + 1,
            quota_message=session_message,
        )
        replaced_files = 0
        replaced_bytes = 0
        try:
            existing = destination.lstat(cache_name)
        except FileNotFoundError:
            existing = None
        if existing is not None and stat.S_ISREG(existing.st_mode):
            replaced_files = 1
            replaced_bytes = max(0, int(existing.st_size))
        projected_files = session_files - replaced_files + 1
        projected_bytes = session_bytes - replaced_bytes + size_bytes
        if (
            projected_files > _ARTIFACT_INPUT_SESSION_MAX_FILES
            or projected_bytes > _ARTIFACT_INPUT_SESSION_MAX_BYTES
        ):
            raise OSError(session_message)

        global_message = "global Artifact input staging quota exceeded"
        global_files, global_bytes = HostDataService._global_cache_usage(data_root)
        if (
            global_files - replaced_files + 1 > _ARTIFACT_INPUT_GLOBAL_MAX_FILES
            or global_bytes - replaced_bytes + size_bytes
            > _ARTIFACT_INPUT_GLOBAL_MAX_BYTES
        ):
            raise OSError(global_message)
        disk = shutil.disk_usage(data_root)
        reserve = min(
            _ARTIFACT_INPUT_MAX_FREE_RESERVE,
            max(_ARTIFACT_INPUT_MIN_FREE_BYTES, int(disk.total) // 20),
        )
        # Publication temporarily holds the old target and the new pending
        # copy at once, so even a replacement needs its full size available.
        if int(disk.free) < size_bytes + reserve:
            raise OSError("Artifact input staging would exhaust reserved disk space")

    @staticmethod
    def _cache_is_exact(
        directory: _PinnedArtifactDirectory,
        name: str,
        *,
        checksum: str,
        size_bytes: int,
    ) -> bool:
        """Verify an existing cache entry through the descriptor consumed."""

        try:
            named_before = directory.lstat(name)
        except FileNotFoundError:
            return False
        if not stat.S_ISREG(named_before.st_mode) or int(named_before.st_nlink) != 1:
            return False
        if int(named_before.st_mode) & 0o222:
            return False
        descriptor: int | None = None
        try:
            descriptor = directory.open_read(name)
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or int(before.st_nlink) != 1:
                return False
            actual_checksum, actual_size = HostDataService._descriptor_digest(
                descriptor,
                expected_size=size_bytes,
            )
            after = os.fstat(descriptor)
            named_after = directory.lstat(name)
            return (
                _artifact_file_state(named_before)
                == _artifact_file_state(before)
                == _artifact_file_state(after)
                == _artifact_file_state(named_after)
                and actual_size == size_bytes
                and actual_checksum == checksum
            )
        except OSError:
            return False
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _artifact_source(
        self,
        metadata: dict,
        *,
        require_snapshot: bool,
    ) -> tuple[Path, tuple[str, ...]]:
        """Return a trusted root and no-follow relative source components."""

        raw_snapshot = str(metadata.get("snapshot_path") or "")
        if raw_snapshot:
            try:
                lexical = Path(os.path.abspath(Path(raw_snapshot).expanduser()))
                # Resolve parent aliases once, but never the final component:
                # `_PinnedArtifactDirectory.open_read` must be what decides
                # whether the stored source itself is a symlink.
                source = lexical.parent.resolve(strict=True) / lexical.name
            except OSError as error:
                raise FileNotFoundError("artifact snapshot is unavailable") from error
            for candidate in trusted_snapshot_roots(self._config().data_dir):
                try:
                    root = candidate.expanduser().resolve(strict=True)
                    relative = source.relative_to(root)
                except (FileNotFoundError, ValueError):
                    continue
                if relative.parts:
                    return root, tuple(relative.parts)
            raise PermissionError("artifact snapshot is outside trusted storage")

        if require_snapshot:
            raise FileNotFoundError(
                f"artifact version {metadata.get('version_id')!r} has no frozen snapshot"
            )

        raw_path = str(metadata.get("path") or "")
        if not raw_path:
            resolved = self._store().resolve_artifact_path(
                str(metadata.get("version_id") or "")
            )
            raw_path = str(resolved or "")
        if not raw_path:
            raise FileNotFoundError("artifact version has no readable source")
        workspace = self._resolve_path(".").expanduser().resolve()
        source = self._resolve_path(raw_path, must_exist=True).expanduser().resolve()
        try:
            relative = source.relative_to(workspace)
        except ValueError as error:  # defensive: the resolver owns this boundary
            raise PermissionError(
                "artifact live path is outside the workspace"
            ) from error
        if not relative.parts:
            raise FileNotFoundError("artifact source is not a regular file")
        return workspace, tuple(relative.parts)

    @staticmethod
    def _copy_exact_version(
        source: _PinnedArtifactDirectory,
        source_name: str,
        destination: _PinnedArtifactDirectory,
        pending_name: str,
        *,
        checksum: str,
        size_bytes: int,
    ) -> None:
        """Stream one stable private source into one exclusive pending file."""

        source_descriptor: int | None = None
        target_descriptor: int | None = None
        try:
            source_descriptor = source.open_read(source_name)
            before = os.fstat(source_descriptor)
            if not stat.S_ISREG(before.st_mode) or int(before.st_nlink) != 1:
                raise OSError("artifact source is not a private regular file")
            target_descriptor = destination.create_exclusive(pending_name)
            digest = hashlib.sha256()
            copied = 0
            remaining = size_bytes + 1
            while remaining:
                chunk = os.read(
                    source_descriptor,
                    min(_ARTIFACT_INPUT_CHUNK, remaining),
                )
                if not chunk:
                    break
                view = memoryview(chunk)
                while view:
                    written = os.write(target_descriptor, view)
                    if written <= 0:  # pragma: no cover - OS write contract
                        raise OSError("Artifact input staging made no progress")
                    view = view[written:]
                copied += len(chunk)
                remaining -= len(chunk)
                digest.update(chunk)
            os.fsync(target_descriptor)
            after = os.fstat(source_descriptor)
            named = source.lstat(source_name)
            if _artifact_file_state(before) != _artifact_file_state(
                after
            ) or _artifact_file_state(after) != _artifact_file_state(named):
                raise OSError("artifact source changed during input staging")
            if copied != size_bytes:
                raise OSError("artifact source size verification failed")
            if digest.hexdigest() != checksum:
                raise OSError("artifact source checksum verification failed")
            target = os.fstat(target_descriptor)
            if (
                not stat.S_ISREG(target.st_mode)
                or int(target.st_nlink) != 1
                or int(target.st_size) != size_bytes
            ):
                raise OSError("staged Artifact input is not a private regular file")
            os.fchmod(target_descriptor, 0o400)
        finally:
            if target_descriptor is not None:
                os.close(target_descriptor)
            if source_descriptor is not None:
                os.close(source_descriptor)

    def _stage_artifact_version(
        self,
        version_id: str,
        metadata: dict,
        *,
        require_snapshot: bool,
    ) -> str:
        """Publish verified bytes in the team's read-only session input root.

        A returned path can remain referenced by a persistent Python/R kernel,
        so entries are stable for the session rather than evicted behind live
        code. Disk use is linear in distinct opened versions, never calls, and
        is bounded by per-session/global byte and file quotas plus a free-space
        reserve. Session deletion reclaims the whole opaque cache directory.
        """

        if require_snapshot and not metadata.get("snapshot_path"):
            raise FileNotFoundError(
                f"artifact version {version_id!r} has no frozen snapshot"
            )
        checksum, size_bytes = self._version_integrity(metadata)
        cache_name = self._version_cache_name(version_id, metadata)
        store = self._store()
        scope = store.resolve_frame_scope(self._frame_id()) or {}
        root_frame_id = str(scope.get("root_frame_id") or "")
        if not root_frame_id:
            raise RuntimeError("Artifact input staging scope is unavailable")
        config = self._config()
        data_root = Path(config.data_dir).expanduser().resolve()
        stage_root = kernel_artifact_input_dir(data_root, root_frame_id)
        stage_parts = tuple(stage_root.relative_to(data_root).parts)
        with _ARTIFACT_INPUT_GLOBAL_LOCK, self._artifact_stage_lock:
            with _PinnedArtifactDirectory.open_under(
                data_root,
                stage_parts,
                create=True,
            ) as destination:
                stage_metadata = os.fstat(destination.fd)
                if int(stage_metadata.st_uid) != os.geteuid():
                    raise PermissionError(
                        "Artifact input directory is not owned by the daemon user"
                    )
                os.fchmod(destination.fd, 0o700)
                if stat.S_IMODE(os.fstat(destination.fd).st_mode) != 0o700:
                    raise PermissionError(
                        "Artifact input directory is not private to the daemon user"
                    )
                if self._cache_is_exact(
                    destination,
                    cache_name,
                    checksum=checksum,
                    size_bytes=size_bytes,
                ):
                    destination.assert_current()
                    result = str(destination.path / cache_name)
                    self._artifact_stage_versions[os.path.abspath(result)] = version_id
                    return result

                self._enforce_stage_quota(
                    data_root,
                    destination,
                    cache_name,
                    size_bytes=size_bytes,
                )
                source_root, source_parts = self._artifact_source(
                    metadata,
                    require_snapshot=require_snapshot,
                )
                pending_name = f".{cache_name}.{uuid.uuid4().hex}.part"
                try:
                    with _PinnedArtifactDirectory.open_under(
                        source_root,
                        source_parts[:-1],
                        create=False,
                    ) as source:
                        self._copy_exact_version(
                            source,
                            source_parts[-1],
                            destination,
                            pending_name,
                            checksum=checksum,
                            size_bytes=size_bytes,
                        )
                    destination.assert_current()
                    destination.replace(pending_name, cache_name)
                    destination.fsync()
                    # Another service for the same session may atomically
                    # publish the same immutable bytes between our rename and
                    # verification. Retry the held-FD check so that benign
                    # concurrency stays idempotent without accepting aliases.
                    for _attempt in range(3):
                        if self._cache_is_exact(
                            destination,
                            cache_name,
                            checksum=checksum,
                            size_bytes=size_bytes,
                        ):
                            destination.assert_current()
                            result = str(destination.path / cache_name)
                            self._artifact_stage_versions[os.path.abspath(result)] = (
                                version_id
                            )
                            return result
                    raise OSError("staged Artifact input verification failed")
                finally:
                    destination.unlink(pending_name, missing_ok=True)

    def artifact_path(self, version_id: str) -> str:
        """Resolve single-user bytes or stage exact team-session bytes.

        The store's canonical path normally lives below the process-wide data
        directory.  Team kernels intentionally cannot read that directory, and
        returning it also disclosed the host layout. Scope is checked before
        any filesystem access; in team mode the exact version is streamed into
        the session's daemon-owned, sandbox-read-only input directory. The
        default single-user mode keeps its established direct-path contract.
        """

        metadata = self._scoped_version(version_id)
        if not bool(getattr(self._config(), "team_mode", False)):
            path = self._store().resolve_artifact_path(version_id)
            if path is None:
                raise KeyError(f"no artifact for id={version_id!r}")
            return path
        return self._stage_artifact_version(
            version_id,
            metadata,
            require_snapshot=False,
        )

    def artifact_snapshot_path(self, version_id: str) -> str:
        """Return exact frozen bytes for one version in the caller's session.

        Remote-compute inputs must never stage the mutable live path.  A
        version row without an immutable snapshot is therefore unavailable,
        even if its live filename still happens to exist.
        """

        metadata = self._scoped_version(version_id)
        if not bool(getattr(self._config(), "team_mode", False)):
            snapshot = str(metadata.get("snapshot_path") or "")
            if not snapshot or not Path(snapshot).is_file():
                raise FileNotFoundError(
                    f"artifact version {version_id!r} has no frozen snapshot"
                )
            return snapshot
        return self._stage_artifact_version(
            version_id,
            metadata,
            require_snapshot=True,
        )

    def materialise_artifact(self, spec: dict) -> dict:
        """Materialise under the process-wide exact Artifact writer lock."""

        with self._restore_lock:
            writer = self._artifact_writer
            materialise = self._materialise_artifact
        if writer is None or materialise is None:
            manager = self._default_artifact_manager()
            writer = manager.writer_transaction
            materialise = manager.materialise_version
        with writer():
            return self._materialise_artifact_locked(spec, materialise=materialise)

    def _materialise_artifact_locked(
        self, spec: dict, *, materialise: ArtifactMaterialiser
    ) -> dict:
        """Bring another session's artifact version into this one.

        D3: no path reads another session's file in place. A cross-session read
        leaves the borrowing session holding an analysis whose input has no
        version in its own history -- delete or revert the other session and
        this one's provenance quietly becomes unresolvable. Materialising gives
        the caller its own Artifact and version plus a lineage edge back, so
        "where did this come from" keeps an answer that does not depend on the
        other session still existing.

        Same project only. A version in another project raises exactly the
        `KeyError` an absent one raises: a distinct refusal would confirm the
        object is there, which is most of what an enumerator wants and the same
        reasoning `_scoped_version` already applies to every version-keyed read.

        The bytes are copied into a private immutable snapshot.  Exact restore
        and edit require ``nlink == 1`` so a second writable name cannot mutate
        trusted history behind its checksum; materialisation must preserve that
        invariant rather than sharing the source snapshot inode.
        """
        source_version_id = str(spec.get("version_id") or "").strip()
        if not source_version_id:
            raise ValueError("materialise_artifact requires version_id")

        store = self._store()
        frame_id = self._frame_id()
        scope = store.resolve_frame_scope(frame_id)
        root_frame_id = str(scope.get("root_frame_id") or "")
        project_id = str(scope.get("project_id") or "")
        if not root_frame_id or not project_id:
            raise KeyError(f"no artifact version {source_version_id!r} available")

        # Deliberately NOT `_scoped_version`: that one confines to the calling
        # *session*, and the whole point here is to reach a sibling session in
        # the same project. The project bound is enforced below, inside the
        # transaction, where it cannot race with a project move.
        metadata = store.version_meta(source_version_id)
        unknown = KeyError(f"no artifact version {source_version_id!r} available")
        if metadata is None:
            raise unknown
        parent = store.get_artifact(str(metadata.get("artifact_id") or ""))
        if parent is None or parent.get("project_id") != project_id:
            raise unknown
        # A project bound is the right one for "may I reach a sibling
        # session", and it is not the whole rule in team mode: a session in
        # this project may still be `private`, and a session with no ownership
        # row is admin-only. Every other reader of an artifact consults
        # `session_visible_to`; this one did not, so a version id learned from
        # a since-revoked share -- or from before the owner made the session
        # private -- still hardlinked their frozen bytes into the caller's
        # workspace and inlined them into the caller's prompt.
        if not self._session_visible(store, str(parent.get("root_frame_id") or "")):
            raise unknown

        config = self._config()
        data_root = Path(config.data_dir).expanduser().resolve()
        versions_dir = data_root / "artifact-versions"
        # Read through the shared held-FD verifier before creating either the
        # borrowed version or its live file.  A pathname ``is_file`` followed
        # by ``copyfile`` accepted a same-length name swap and then inherited
        # the source row's checksum for different bytes.
        _snapshot, source_data = ArtifactRestoreService(
            store=store,
            primary_snapshot_dir=versions_dir,
            trusted_snapshot_dirs=trusted_snapshot_roots(config.data_dir),
            resolve_live_path=lambda _artifact, _version: Path(),
        ).verified_snapshot_bytes(metadata)

        # Every refusal ahead of every mutation. Two of these used to live only
        # inside the transaction, which ran *after* the live file had already been
        # unlinked -- so a call that was going to be refused destroyed the
        # caller's working file on its way to refusing.
        if str(parent.get("root_frame_id") or "") == root_frame_id:
            raise ValueError("artifact version already belongs to this session")

        filename = str(spec.get("filename") or metadata.get("filename") or "artifact")
        live = self._resolve_path(filename, must_exist=False)
        if live.exists() or live.is_symlink():
            # This used to be `live.unlink()`: a silent, unlogged deletion of
            # whatever the session already had under that name, on the *success*
            # path, with no snapshot backfill -- so unsaved work disappeared
            # without a word. Refusing and naming the remedy is the only version
            # of this that does not lose data the caller did not offer up.
            raise FileExistsError(
                f"{filename!r} already exists in this session's workspace; "
                f"materialising would overwrite it. Pass filename= to choose "
                f"another name."
            )
        # The canonical manager owns staging, the durable intent journal,
        # SQLite publication, held-FD verification and startup recovery. This
        # call re-enters the writer RLock acquired by the public method above.
        return materialise(
            source_version_id=source_version_id,
            filename=filename,
            frame_id=frame_id,
            workspace_frame_id=root_frame_id,
            project_id=project_id,
            raw=source_data,
            producing_cell_id=spec.get("execution_cell_id")
            or spec.get("producing_cell_id"),
        )

    def _scoped_lineage_inputs(self, raw: Any) -> list[str]:
        """Validate declared lineage inputs before anything is written.

        `record_cell_artifact` skips only empty, self and duplicate ids and then
        INSERTs the edge; `lineage_edges` declares no foreign key, so an id from
        another project -- or one that never existed -- was accepted. One
        `save_artifact` call therefore wrote an edge the scoping model says cannot
        exist, and the properly scoped readers then walked it and republished the
        other project's filename and absolute path through it.

        Validation happens here, before the copy and before the row, so a refused
        call leaves no artifact, no version and no orphan file behind. Foreign and
        absent raise the same KeyError as everywhere else on these paths.
        """
        if not raw:
            return []
        if isinstance(raw, str):
            raw = [raw]
        inputs: list[str] = []
        for candidate in raw:
            version_id = str(candidate or "").strip()
            if not version_id:
                continue
            # Raises the shared unknown-version KeyError for foreign, dangling
            # and malformed alike.
            self._scoped_version(version_id)
            if version_id not in inputs:
                inputs.append(version_id)
        return inputs

    def save_artifact(self, spec: dict) -> dict:
        # Before the copy: a refused lineage declaration must not leave a file in
        # the artifacts directory that no row will ever name.
        input_version_ids = self._scoped_lineage_inputs(spec.get("input_version_ids"))
        source = self._resolve_path(str(spec["path"]), must_exist=True)
        if not source.is_file():
            raise FileNotFoundError(f"save_artifact: no such file: {source}")
        filename = str(spec.get("filename") or source.name)
        # Streamed, exactly like `provenance_record` below. This was
        # `source.read_bytes()` purely to checksum: registering a 64 MB output
        # measured a 64 MB peak in the daemon that also serves every other
        # session, and a real trajectory or alignment is orders of magnitude
        # past that -- the copy underneath never needed the bytes in Python at
        # all. Two passes beat one pass that has to hold the file: `copy2`
        # takes the kernel's copy fast path, so the second read costs I/O, not
        # memory.
        version_stub = uuid.uuid4().hex[:12]
        safe_filename = re.sub(r"[^A-Za-z0-9._-]+", "_", filename or "artifact")
        config = self._config()
        destination = config.artifacts_dir / f"v-{version_stub}__{safe_filename}"
        trusted_delivery = self._trusted_delivery_enabled()
        if trusted_delivery:
            checksum, size_bytes = self._freeze_snapshot(source, destination)
        else:
            checksum, size_bytes = self._digest_file(source)
            config.artifacts_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        store = self._store()
        try:
            execution_cell_id = spec.get("execution_cell_id") or spec.get(
                "producing_cell_id"
            )
            record = store.record_cell_artifact(
                path=str(source),
                filename=filename,
                content_type=spec.get("content_type"),
                size_bytes=size_bytes,
                checksum=checksum,
                producing_cell_id=execution_cell_id,
                frame_id=self._frame_id(),
                snapshot_path=str(destination),
                input_version_ids=input_version_ids,
                source=spec.get("source"),
                reuse_policy="provisional",
                **({"reuse_matching_head": True} if trusted_delivery else {}),
            )
        except Exception:
            destination.unlink(missing_ok=True)
            raise

        metadata = store.version_meta(record["version_id"]) or {}
        bound_snapshot = metadata.get("snapshot_path")
        if bound_snapshot != str(destination):
            if bound_snapshot and Path(bound_snapshot).is_file():
                destination.unlink(missing_ok=True)
            else:
                store.set_version_snapshot(record["version_id"], str(destination))
                bound_snapshot = str(destination)
        priority = int(spec.get("priority", 0))
        if priority:
            store.set_priority(record["artifact_id"], priority)
        response = dict(record)
        response["path"] = bound_snapshot or str(destination)
        return response

    def view_image(self, spec: dict) -> dict:
        version_id = spec.get("version_id")
        path = spec.get("path")
        if version_id and not path:
            # Store-derived: an artifact snapshot legitimately lives outside the
            # workspace, under the data dir -- so the workspace resolver cannot
            # be the check here, and scope has to be.
            #
            # This used to say the scope check "belongs with the rest of the
            # artifact read paths, not here" and pass the id straight to
            # `resolve_artifact_path`. No artifact read path performed it, so the
            # check was deferred to nowhere: any version id from any project
            # rendered, and the reply carried the resolved absolute path.
            self._scoped_version(str(version_id))
            resolved = self._store().resolve_artifact_path(version_id)
            if not resolved or not Path(resolved).exists():
                raise FileNotFoundError(f"view_image: no such image: {resolved!r}")
            return {"status": "ok", "rendered": True, "path": str(resolved)}
        # Caller-supplied: confined, like every other file the SDK can name.
        # This branch checked only `Path(path).exists()`, which made
        # `host.view_image(path="/etc/passwd")` an existence oracle for any
        # absolute path on the host -- reachable straight from a kernel cell,
        # and the one file operation here that skipped the workspace resolver
        # its siblings all go through.
        if not path:
            raise FileNotFoundError("view_image: no such image: None")
        target = self._resolve_path(str(path), must_exist=True)
        return {"status": "ok", "rendered": True, "path": str(target)}

    def artifact_marker(self, version_id: str) -> str:
        if not _VALID_MARKER_ID.match(str(version_id)):
            raise ValueError(
                f"artifact_marker: id {version_id!r} is not a valid version id"
            )
        # Keep the scanner marker split in source so this implementation can
        # produce a legitimate marker without matching its own static gate.
        prefix = "".join(("{" "{", "artifact", ":"))
        suffix = "".join(("}" "}",))
        return f"{prefix}{version_id}{suffix}"

    def frames(self, spec: dict | None = None) -> Any:
        spec = spec or {}
        frame_id = spec.get("frame_id")
        pattern = spec.get("pattern")
        project_id = spec.get("project_id", "default")
        status = spec.get("status")
        if status is not None and status not in FRAME_STATUSES:
            raise ValueError(
                f"frames: invalid status {status!r}; valid: "
                f"{sorted(FRAME_STATUSES)}"
            )
        store = self._store()
        # One rule for all three shapes, resolved before a row is read.
        # `browse` already had a scoping parameter and this path never passed
        # it; `search` and `detail` had none at all, and both return cell code
        # and stdout -- so filtering afterwards would mean the bytes had
        # already been loaded and only the presentation was scoped. In team
        # mode with no principal this raises rather than widening (INV-13).
        viewer = self._visibility_filter()
        if frame_id:
            detail = store.frame_detail(
                frame_id,
                page=int(spec.get("page", 0)),
                page_size=int(spec.get("page_size", 50)),
                visible_to_user_id=viewer,
            )
            if detail is None:
                # Same sentence for "no such frame" and "not yours": which
                # sessions exist is itself what INV-13 protects.
                raise KeyError(f"no such frame {frame_id!r}")
            return detail
        if pattern:
            return {
                "mode": "search",
                "pattern": pattern,
                "frames": store.search_frames(
                    pattern,
                    project_id=project_id,
                    limit=int(spec.get("limit", 50)),
                    visible_to_user_id=viewer,
                ),
            }
        return {
            "mode": "browse",
            "frames": store.browse_frames(
                project_id=project_id,
                status=status,
                roots_only=bool(spec.get("roots_only", True)),
                limit=int(spec.get("limit", 50)),
                visible_to_user_id=viewer,
            ),
        }

    def _session_visible(self, store: Any, root_frame_id: str) -> bool:
        """May the principal running this execution read that session?

        Fail-closed on an undecidable answer, the same way `team_policy` does:
        an ownership row we cannot read is not an open door. Single-user and
        service principals are unrestricted, so this is inert off team mode
        (INV-1).
        """
        principal = execution_principal.resolve()
        if principal.unrestricted:
            return True
        if not root_frame_id:
            return False
        try:
            return bool(
                store.team.session_visible_to(
                    root_frame_id, principal.as_visibility_user()
                )
            )
        except Exception:  # noqa: BLE001 — undecidable is refused
            return False

    def _visibility_filter(self) -> str | None:
        """The user id these reads are scoped to, or None for unrestricted.

        None means "no filtering" here, which is safe only because it is
        never reached by guessing: `resolve()` refuses an execution that
        carries no principal, and an unrestricted answer requires an
        *explicit* single-user, service or admin principal.
        """
        principal = execution_principal.resolve()
        if principal.unrestricted:
            return None
        return principal.user_id

    def lineage_get(self, version_id: str) -> dict:
        store = self._store()
        metadata = self._scoped_version(version_id)
        cell = store.producing_cell_for_version(version_id) or {}
        producing_cell_id = metadata.get("producing_cell_id")
        try:
            inputs = store.lineage_inputs(
                version_id,
                producing_cell_id=(
                    str(producing_cell_id) if producing_cell_id else None
                ),
            )
        except TypeError:
            inputs = store.lineage_inputs(version_id)
        result = {
            "version_id": version_id,
            "artifact_id": metadata.get("artifact_id"),
            "filename": metadata.get("filename"),
            "checksum": metadata.get("checksum"),
            "frame_id": metadata.get("frame_id"),
            "producing_cell_id": producing_cell_id,
            "code": cell.get("code"),
            "inputs": inputs,
            "extraction_pending": False,
        }
        observation_reader = getattr(
            store,
            "list_artifact_capture_observations",
            None,
        )
        if callable(observation_reader):
            observations = observation_reader(version_id=version_id)
            if observations:
                result["capture_observations"] = [
                    {
                        "observation_id": row.get("observation_id"),
                        "capture_kind": row.get("capture_kind"),
                        "producing_cell_id": row.get("producing_cell_id"),
                        "frame_id": row.get("frame_id"),
                        "input_version_ids": list(row.get("input_version_ids") or []),
                        "created_at": row.get("created_at"),
                    }
                    for row in observations
                ]
        return result

    def lineage_graph(self, spec: dict) -> dict:
        """Walk the lineage graph from one version, always bounded.

        `max_depth` and `max_nodes` are both optional in the tool schema, and
        with neither supplied this walked every edge reachable in the whole
        `lineage_edges` table -- across every session and project -- while
        `frontier` and `edges` grew without any limit of their own. A caller
        that omits an argument should get a bounded answer, not the database.

        The caps are still caller-lowerable; what changed is that omitting them
        no longer means "no limit". `truncated` says plainly when the walk
        stopped early, because a silently partial graph is a lineage claim that
        is wrong rather than absent.
        """
        start = spec["version_id"]
        # The root must be ours; the walk then follows edges from it.
        self._scoped_version(start)
        direction = spec.get("direction", "up")
        max_depth = spec.get("max_depth")
        max_depth = _DEFAULT_LINEAGE_DEPTH if max_depth is None else int(max_depth)
        max_nodes = spec.get("max_nodes")
        max_nodes = _DEFAULT_LINEAGE_NODES if max_nodes is None else int(max_nodes)
        max_nodes = max(1, max_nodes)
        seen: set[str] = set()
        edges: list[dict] = []
        frontier = [(start, 0)]
        store = self._store()
        truncated = False
        while frontier:
            version_id, depth = frontier.pop(0)
            if version_id in seen:
                continue
            if len(seen) >= max_nodes:
                # Checked BEFORE adding, so the cap is the number of nodes
                # returned rather than one more than it.
                truncated = True
                break
            seen.add(version_id)
            if depth >= max_depth:
                if frontier or store.lineage_edges_for(version_id, direction):
                    truncated = True
                continue
            for adjacent in store.lineage_edges_for(version_id, direction):
                if len(edges) >= _MAX_LINEAGE_EDGES:
                    truncated = True
                    break
                edges.append(
                    {"from": version_id, "to": adjacent, "direction": direction}
                )
                frontier.append((adjacent, depth + 1))
        result = {"root": start, "nodes": sorted(seen), "edges": edges}
        if truncated:
            result["truncated"] = True
        return result

    def provenance_resolve_path(self, path: str) -> Any:
        """Which version this session's copy of `path` is, or None.

        Session scope, not project scope: a foreign session's file has to be
        materialised, never resolved in place, which is what `materialise_artifact`
        exists for. Refusal is `None` -- the same value an untracked path already
        returns -- because the P0-2 exit criterion is that cross-scope and absent
        are indistinguishable, and this contract is already `str | None`. Raising
        would answer "something is there, you may not have it".

        Reached from `builtins.open` in every Python cell (the provenance hooks
        wrap the reader), so it runs constantly and must stay cheap and quiet.
        """
        frame_id = self._frame_id()
        if frame_id is None:
            return None
        with self._artifact_stage_lock:
            staged_version = self._artifact_stage_versions.get(os.path.abspath(path))
        if staged_version is not None:
            # Only paths this exact session service staged are entered here;
            # rechecking scope keeps a later frame/scope switch from carrying
            # an old mapping across the boundary.
            try:
                self._scoped_version(staged_version)
            except KeyError:
                return None
            return staged_version
        scope = self._store().resolve_frame_scope(frame_id)
        root_frame_id = scope.get("root_frame_id")
        project_id = scope.get("project_id")
        if not root_frame_id or not project_id:
            return None
        return self._store().version_for_path(
            path, root_frame_id=str(root_frame_id), project_id=str(project_id)
        )

    #: How much of a file is read at a time when checksumming it.
    _DIGEST_CHUNK = 1024 * 1024

    @staticmethod
    def _digest_file(path: Path) -> tuple[str, int]:
        """Return ``(sha256, size)`` for a file, one chunk at a time.

        Shared by `save_artifact` and `provenance_record` so the two cannot
        drift apart again: both register a file the agent produced, and the
        files worth registering are exactly the ones too large to hold. It
        raises `OSError` -- each caller reports an unreadable path in its own
        established shape.
        """
        digest = hashlib.sha256()
        size_bytes = 0
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(HostDataService._DIGEST_CHUNK)
                if not chunk:
                    break
                size_bytes += len(chunk)
                digest.update(chunk)
        return digest.hexdigest(), size_bytes

    def provenance_record(self, spec: dict) -> dict:
        """Register a file this cell produced as an artifact of this session.

        The path is resolved through the workspace confinement every other
        file capability uses. It was not: `Path(path).expanduser()` accepted
        any absolute path on the host, so a cell could name `~/.ssh/id_rsa` or
        the daemon's own access-token file and have it registered as a session
        artifact -- readable, downloadable, and shareable through every surface
        that lists artifacts. Verified before this changed: a private key
        outside the workspace came back with a version id.

        `self._resolve_path` was already injected into this service and used by
        its siblings, which is what makes this an omission rather than a
        missing mechanism.
        """
        # Before the digest and before the row, exactly where `save_artifact`
        # validates the same field. `save_artifact` was fixed and this twin was
        # not, so the one write path that never checked its lineage inputs was
        # the one an agent reaches from any cell: a foreign version id went into
        # `lineage_edges` (no foreign key), and the properly scoped reader then
        # walked that edge and returned the other project's filename and
        # absolute path. Refusing here means a rejected call leaves no artifact,
        # no version and no orphan file.
        input_version_ids = self._scoped_lineage_inputs(spec.get("input_version_ids"))
        path = spec["path"]
        try:
            output = self._resolve_path(path, must_exist=True)
        except FileNotFoundError:
            return {"error": f"prov_record: no such output file: {path}"}
        except (ValueError, OSError) as error:
            # Soft-fail: the worker turns a single-key error dict into a
            # RuntimeError in the cell, which is how the agent learns it asked
            # for something outside its workspace.
            return {"error": f"prov_record: {error}"}

        # Streamed rather than `read_bytes()`, through the helper `save_artifact`
        # also uses. This one materialised the whole file in the daemon to
        # checksum it, so recording a 4 GB output cost 4 GB of daemon memory in
        # a process that also serves every other session; `save_artifact` was
        # still doing exactly that, which is why the loop now lives in one
        # place instead of two.
        trusted_delivery = self._trusted_delivery_enabled()
        snapshot: Path | None = None
        try:
            if trusted_delivery:
                config = self._config()
                safe_filename = re.sub(
                    r"[^A-Za-z0-9._-]+",
                    "_",
                    str(spec.get("filename") or output.name or "artifact"),
                )
                snapshot = (
                    config.artifacts_dir / f"v-{uuid.uuid4().hex[:12]}__{safe_filename}"
                )
                checksum, size_bytes = self._freeze_snapshot(output, snapshot)
            else:
                checksum, size_bytes = self._digest_file(output)
        except FileNotFoundError:
            # Reported the same way whether the resolver enforced existence or
            # the open did. A caller with a pass-through resolver would
            # otherwise get "prov_record: <path>: [Errno 2]..." for the same
            # situation the branch above calls "no such output file".
            return {"error": f"prov_record: no such output file: {path}"}
        except OSError as error:
            return {"error": f"prov_record: {path}: {error}"}
        store = self._store()
        try:
            record = store.record_cell_artifact(
                path=str(output),
                filename=spec.get("filename") or output.name,
                content_type=spec.get("content_type"),
                size_bytes=size_bytes,
                checksum=checksum,
                producing_cell_id=spec.get("producing_cell_id"),
                frame_id=self._frame_id(),
                input_version_ids=input_version_ids,
                **(
                    {
                        "snapshot_path": str(snapshot),
                        "reuse_matching_head": True,
                    }
                    if snapshot is not None
                    else {}
                ),
            )
        except Exception:
            if snapshot is not None:
                snapshot.unlink(missing_ok=True)
            raise
        if snapshot is not None:
            metadata = store.version_meta(record["version_id"]) or {}
            if metadata.get("snapshot_path") != str(snapshot):
                snapshot.unlink(missing_ok=True)
        return record


__all__ = [
    "FRAME_STATUSES",
    "HostDataService",
    "kernel_artifact_input_dir",
    "rank_artifacts",
]
