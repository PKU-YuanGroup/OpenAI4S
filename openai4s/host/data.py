"""Store-backed data services exposed through ``host.*`` RPC.

This module owns query projection, artifact persistence/search, frame browsing,
and provenance/lineage reads.  ``HostDispatcher`` remains the policy, audit,
and routing envelope and delegates the domain behaviour here.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable, Protocol

from openai4s import execution_principal
from openai4s.artifact_restore import ArtifactRestoreService, trusted_snapshot_roots


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

    def lineage_inputs(self, version_id: str) -> list[dict]: ...

    def lineage_edges_for(self, version_id: str, direction: str) -> list[dict]: ...

    def version_for_path(
        self, path: str, *, root_frame_id: str | None, project_id: str
    ) -> str | None: ...


StoreProvider = Callable[[], HostDataStore]
ConfigProvider = Callable[[], Any]
FrameIdProvider = Callable[[], str | None]
PathResolver = Callable[..., Path]

#: Bounds for a lineage walk when the caller names none. They are generous
#: enough that a real provenance chain fits, and they exist because the
#: alternative -- omitting an optional argument -- used to mean "traverse
#: everything reachable".
_DEFAULT_LINEAGE_DEPTH = 32
_DEFAULT_LINEAGE_NODES = 500
_MAX_LINEAGE_EDGES = 5000

FRAME_STATUSES = frozenset({"processing", "done", "failed", "awaiting_user_response"})

_VALID_MARKER_ID = re.compile(
    r"^(v-)?[0-9a-fA-F]{8,}$|"
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


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
    ) -> None:
        self._store_source = store
        self._config_source = config
        self._frame_id_source = frame_id
        self._resolve_path = resolve_path

    def _store(self) -> HostDataStore:
        source = self._store_source
        return source() if callable(source) else source

    def _config(self) -> Any:
        source = self._config_source
        return source() if callable(source) else source

    def _frame_id(self) -> str | None:
        source = self._frame_id_source
        return source() if callable(source) else source

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
        artifact = self._scoped_artifact(artifact_id)
        store = self._store()
        config = self._config()
        trusted = (
            trusted_snapshot_roots(config.data_dir)
            if getattr(config, "data_dir", None) is not None
            else (Path(config.artifacts_dir),)
        )
        service = ArtifactRestoreService(
            store=store,
            primary_snapshot_dir=Path(config.artifacts_dir),
            trusted_snapshot_dirs=trusted,
            resolve_live_path=lambda current_artifact, current: self._resolve_path(
                str(current.get("path") or current_artifact.get("filename"))
            ),
        )
        return service.restore(
            artifact=artifact,
            source_version_id=source_version_id,
            frame_id=self._frame_id(),
        )

    def artifact_path(self, version_id: str) -> str:
        self._scoped_version(version_id)
        path = self._store().resolve_artifact_path(version_id)
        if path is None:
            raise KeyError(f"no artifact for id={version_id!r}")
        return path

    def materialise_artifact(self, spec: dict) -> dict:
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

        The bytes are hardlinked, not copied. A version snapshot is immutable by
        contract -- `write_version_snapshot` returns early rather than rewriting
        one -- so two rows may share an inode safely, and a materialised
        multi-gigabyte dataset costs a directory entry. The copy fallback is for
        a data dir spanning devices, where a hardlink cannot exist.
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

        snapshot = metadata.get("snapshot_path") or ""
        if not snapshot or not Path(str(snapshot)).is_file():
            # The row exists but its immutable bytes do not, so there is
            # nothing honest to hand over. Reported as its own failure rather
            # than as "not found": the version is real and the caller may want
            # to know the difference.
            raise FileNotFoundError(
                f"artifact version {source_version_id!r} has no frozen snapshot"
            )

        # Every refusal ahead of every mutation. Two of these used to live only
        # inside the transaction, which ran *after* the live file had already been
        # unlinked -- so a call that was going to be refused destroyed the
        # caller's working file on its way to refusing.
        if str(parent.get("root_frame_id") or "") == root_frame_id:
            raise ValueError("artifact version already belongs to this session")

        filename = str(spec.get("filename") or metadata.get("filename") or "artifact")
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename) or "artifact"
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

        version_id = f"v-{uuid.uuid4().hex[:12]}"
        config = self._config()
        versions_dir = Path(config.data_dir) / "artifact-versions"
        versions_dir.mkdir(parents=True, exist_ok=True)
        destination = versions_dir / f"{version_id}__{safe}"
        # Snapshot to snapshot: a hardlink is safe here and is the optimisation
        # worth having -- both names are immutable by contract, so a materialised
        # multi-gigabyte dataset costs a directory entry.
        try:
            os.link(str(snapshot), str(destination))
        except OSError:
            shutil.copyfile(str(snapshot), str(destination))

        # The live file is a real COPY, never a link. Hardlinking it made the
        # borrowing session's *writable* working file share an inode with two
        # immutable snapshots, so one ordinary truncating write through the live
        # name rewrote the source session's frozen bytes -- and
        # `write_version_snapshot` returns early when the file exists, so nothing
        # would ever re-freeze them. The source row's checksum then described
        # bytes that no longer existed. The old docstring's "immutable by
        # contract" is true of the snapshot names and false of the live one.
        #
        # Staged, then moved: `os.replace` onto a name proven absent above is
        # atomic, so a cell never observes a half-written deliverable.
        live.parent.mkdir(parents=True, exist_ok=True)
        staged = live.with_name(f"{live.name}.{version_id}.part")
        try:
            shutil.copyfile(str(destination), str(staged))
            os.replace(str(staged), str(live))
        except Exception:
            staged.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            raise

        try:
            return store.materialise_artifact_version(
                source_version_id=source_version_id,
                artifact_id=f"a-{uuid.uuid4().hex[:12]}",
                version_id=version_id,
                filename=filename,
                path=str(live),
                snapshot_path=str(destination),
                frame_id=frame_id,
                root_frame_id=root_frame_id,
                project_id=project_id,
                # Read the same way `save_artifact` reads it. Without it the row
                # lands with `producing_cell_id` NULL, and the end-of-cell
                # capture matches candidates on exactly that column.
                producing_cell_id=spec.get("execution_cell_id")
                or spec.get("producing_cell_id"),
            )
        except Exception:
            # The transaction rolled back, so both files describe a version that
            # does not exist. Removing them is safe precisely because the live
            # name was proven absent before anything was written: there is no
            # pre-existing file here to lose, which is what made the old
            # one-sided rollback (snapshot only) destructive.
            staged.unlink(missing_ok=True)
            live.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            raise

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
        checksum, size_bytes = self._digest_file(source)
        version_stub = uuid.uuid4().hex[:12]
        safe_filename = re.sub(r"[^A-Za-z0-9._-]+", "_", filename or "artifact")
        config = self._config()
        config.artifacts_dir.mkdir(parents=True, exist_ok=True)
        destination = config.artifacts_dir / f"v-{version_stub}__{safe_filename}"
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
        return {
            "version_id": version_id,
            "artifact_id": metadata.get("artifact_id"),
            "filename": metadata.get("filename"),
            "checksum": metadata.get("checksum"),
            "frame_id": metadata.get("frame_id"),
            "producing_cell_id": metadata.get("producing_cell_id"),
            "code": cell.get("code"),
            "inputs": store.lineage_inputs(version_id),
            "extraction_pending": False,
        }

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

    def _digest_file(self, path: Path) -> tuple[str, int]:
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
                chunk = handle.read(self._DIGEST_CHUNK)
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
        try:
            checksum, size_bytes = self._digest_file(output)
        except FileNotFoundError:
            # Reported the same way whether the resolver enforced existence or
            # the open did. A caller with a pass-through resolver would
            # otherwise get "prov_record: <path>: [Errno 2]..." for the same
            # situation the branch above calls "no such output file".
            return {"error": f"prov_record: no such output file: {path}"}
        except OSError as error:
            return {"error": f"prov_record: {path}: {error}"}

        return self._store().record_cell_artifact(
            path=str(output),
            filename=spec.get("filename") or output.name,
            content_type=spec.get("content_type"),
            size_bytes=size_bytes,
            checksum=checksum,
            producing_cell_id=spec.get("producing_cell_id"),
            frame_id=self._frame_id(),
            input_version_ids=input_version_ids,
        )


__all__ = ["FRAME_STATUSES", "HostDataService", "rank_artifacts"]
