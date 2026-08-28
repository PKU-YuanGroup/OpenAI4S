"""Feature-flagged private scratch workspace for delegated children.

Default off: children keep sharing the parent session workspace. When
``OPENAI4S_DELEGATION_PRIVATE_SCRATCH`` is strictly enabled, a child receives a
read-only snapshot of the parent workspace as input, writes only into its own
scratch directory, and can publish outputs solely as immutable Artifact
versions. The parent workspace changes only after an explicit materialize.
Published Artifact versions are never deleted on rollback.

This prototype does not lift trusted-capture concurrency limits.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from openai4s.storage.snapshots import WorkspaceCAS

_TRUE = frozenset(("1", "true", "yes", "on"))
_FALSE = frozenset(("0", "false", "no", "off"))


def private_scratch_enabled() -> bool:
    """Strict opt-in. Unknown values fail closed rather than enabling."""

    raw = os.environ.get("OPENAI4S_DELEGATION_PRIVATE_SCRATCH")
    if raw is None:
        return False
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    choices = ", ".join(sorted(_TRUE | _FALSE))
    raise ValueError(
        f"invalid OPENAI4S_DELEGATION_PRIVATE_SCRATCH: expected one of {choices}"
    )


@dataclass
class ChildScratch:
    """One child's private working directory plus the parent snapshot it saw."""

    child_id: str
    directory: Path
    parent_tree_id: str | None
    cas: WorkspaceCAS
    snapshot_paths: tuple[str, ...] = ()


def cas_for(data_dir: str | Path) -> WorkspaceCAS:
    return WorkspaceCAS(Path(data_dir) / "workspace-cas")


def scratch_directory(data_dir: str | Path, root_frame_id: str, child_id: str) -> Path:
    return Path(data_dir) / "delegation-scratch" / root_frame_id / child_id


def prepare_child_scratch(
    *,
    data_dir: str | Path,
    root_frame_id: str,
    child_id: str,
    parent_workspace: str | Path | None,
    cas: WorkspaceCAS | None = None,
) -> ChildScratch:
    """Materialize a parent snapshot into a fresh, child-private directory.

    Snapshot files are made read-only so the child cannot mutate the parent's
    bytes in place. New files in the scratch remain writable.
    """

    cas = cas or cas_for(data_dir)
    directory = scratch_directory(data_dir, root_frame_id, child_id)
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True, exist_ok=True)
    parent_tree_id = None
    snapshot_paths: tuple[str, ...] = ()
    if parent_workspace is not None and Path(parent_workspace).is_dir():
        tree = cas.capture(parent_workspace)
        cas.materialize(tree["tree_id"], directory)
        # Re-capture the scratch so mode/path identity matches what the child
        # will later be diffed against. chmod of snapshot files is a best-effort
        # read-only hint and must not itself count as a child write.
        for entry in tree.get("entries") or []:
            path = directory / str(entry["path"])
            if path.is_file():
                try:
                    os.chmod(path, 0o444)
                except OSError:
                    pass
        baseline = cas.capture(directory)
        parent_tree_id = baseline["tree_id"]
        snapshot_paths = tuple(entry["path"] for entry in baseline.get("entries") or [])
    return ChildScratch(
        child_id=child_id,
        directory=directory,
        parent_tree_id=parent_tree_id,
        cas=cas,
        snapshot_paths=snapshot_paths,
    )


def publish_child_outputs(
    scratch: ChildScratch,
    *,
    store: Any | None,
    child_frame_id: str | None,
    root_frame_id: str | None,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Publish scratch files that the child created or changed as Artifact versions.

    Each child writes into a child-scoped durable path, so two children that
    emit the same filename cannot overwrite each other's published bytes.
    """

    if store is None or not callable(getattr(store, "save_artifact", None)):
        return []
    if not scratch.directory.is_dir():
        return []
    after = scratch.cas.capture(scratch.directory)
    if scratch.parent_tree_id:
        diff = scratch.cas.diff_trees(scratch.parent_tree_id, after["tree_id"])
        output_paths = list(diff.get("added") or []) + list(diff.get("changed") or [])
    else:
        output_paths = [entry["path"] for entry in after.get("entries") or []]
    refs: list[dict[str, Any]] = []
    durable_root = (
        Path(scratch.cas.root).parent / "artifacts" / "delegation" / scratch.child_id
    )
    for relative in output_paths:
        source = scratch.directory / relative
        if not source.is_file():
            continue
        data = source.read_bytes()
        checksum = hashlib.sha256(data).hexdigest()
        scratch.cas.put_blob(data)
        destination = durable_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        content_type = mimetypes.guess_type(relative)[0] or "application/octet-stream"
        record = store.save_artifact(
            path=str(destination),
            filename=Path(relative).name,
            content_type=content_type,
            size_bytes=len(data),
            checksum=checksum,
            frame_id=child_frame_id,
            root_frame_id=root_frame_id,
            project_id=project_id,
        )
        refs.append(
            {
                "artifact_id": record.get("artifact_id"),
                "version_id": record.get("version_id"),
                "filename": record.get("filename") or Path(relative).name,
                "checksum": checksum,
                "frame_id": child_frame_id,
                "path": relative,
                "durable_path": str(destination),
            }
        )
    return refs


def materialize_outputs_into_parent(
    *,
    parent_workspace: str | Path,
    artifact_refs: Sequence[Mapping[str, Any]],
    store: Any | None = None,
    paths: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Copy selected published child outputs into the parent workspace.

    Never deletes Artifact versions. A missing version is reported, not rebuilt.
    """

    del store  # versions stay in SQLite; this path only copies bytes
    base = Path(parent_workspace).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)
    wanted = None if paths is None else {str(item) for item in paths}
    written: list[str] = []
    missing: list[str] = []
    for ref in artifact_refs:
        relative = str(ref.get("path") or ref.get("filename") or "")
        if not relative:
            continue
        if (
            wanted is not None
            and relative not in wanted
            and Path(relative).name not in wanted
        ):
            continue
        durable = ref.get("durable_path")
        source_path = Path(str(durable)) if durable else None
        if source_path is None or not source_path.is_file():
            missing.append(relative)
            continue
        destination = (base / relative).resolve()
        if base not in destination.parents and destination != base:
            raise ValueError(f"materialize escaped workspace: {relative}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_name(f".{destination.name}.materialize")
        tmp.write_bytes(source_path.read_bytes())
        os.replace(tmp, destination)
        written.append(relative)
    return {
        "written": written,
        "missing": missing,
        "deleted_versions": 0,
    }


__all__ = [
    "ChildScratch",
    "cas_for",
    "materialize_outputs_into_parent",
    "prepare_child_scratch",
    "private_scratch_enabled",
    "publish_child_outputs",
    "scratch_directory",
]
