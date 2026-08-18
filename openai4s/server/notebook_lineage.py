"""Stage 8 host-side Notebook reads and cross-language lineage.

File reads are mapped to Artifact versions on the host after a Cell returns.
A later write in the same Cell becomes an input→output lineage edge.  This is
deliberately independent of in-kernel provenance so Python and R share one
rule.  The official live REPL is the Stage 8 flag; the older developer
``notebook_repl`` switch remains an independent override.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_PATH_LITERAL = re.compile(r"""['\"]([^'\"]{1,240})['\"]""")
_SKIP_PREFIXES = ("http://", "https://", "s3://", "ftp://")
NOTEBOOK_OWNERS = ("agent", "user_repl", "repair", "review_scratch")


def official_notebook_enabled(config: Any) -> bool:
    """Whether the live Notebook is a first-class execution path."""

    flags = getattr(config, "roadmap_features", None)
    if flags is not None and bool(
        getattr(flags, "stage8_live_notebook_lineage", False)
    ):
        return True
    return bool(getattr(config, "notebook_repl", False))


def mentioned_relative_paths(source: str) -> list[str]:
    """Extract workspace-relative path literals from Cell source."""

    found: list[str] = []
    seen: set[str] = set()
    for raw in _PATH_LITERAL.findall(str(source or "")):
        text = raw.replace("\\", "/").strip()
        if not text or text.startswith(_SKIP_PREFIXES) or text.startswith("/"):
            continue
        while text.startswith("./"):
            text = text[2:]
        if not text or ".." in Path(text).parts:
            continue
        suffix = Path(text).suffix
        if not suffix and "/" not in text:
            continue
        if text not in seen:
            seen.add(text)
            found.append(text)
    return found


def infer_read_paths(
    workspace: Path | str,
    before: Mapping[str, Any],
    source: str,
    files_written: Sequence[str] = (),
) -> list[str]:
    """Host-side read set: existing files named in the Cell that were not new."""

    root = Path(workspace)
    existing: set[str] = set()
    for abs_path in before:
        try:
            existing.add(str(Path(abs_path).resolve().relative_to(root.resolve())))
        except (OSError, ValueError):
            continue
    reads: list[str] = []
    for rel in mentioned_relative_paths(source):
        if rel not in existing:
            continue
        # An overwrite of an existing file is still a read of the previous
        # version; the caller maps that name to the prior Artifact version.
        if rel not in reads:
            reads.append(rel)
    return reads


def _resolve_input_version(
    store: Any,
    *,
    workspace: Path,
    relative: str,
    root_frame_id: str,
    project_id: str,
    output_version_ids: set[str],
) -> str | None:
    abs_path = str((workspace / relative).resolve())
    current = store.version_for_path(
        abs_path, root_frame_id=root_frame_id, project_id=project_id
    )
    if current and current not in output_version_ids:
        return str(current)
    artifacts = store.list_artifacts({"root_frame_id": root_frame_id}) or []
    match = next(
        (
            item
            for item in artifacts
            if str(item.get("filename") or "") == relative
            or str(item.get("path") or "") == abs_path
        ),
        None,
    )
    if match is None:
        return None
    versions = store.list_versions(str(match.get("artifact_id") or "")) or []
    for item in reversed(versions):
        version_id = str(item.get("version_id") or "")
        if version_id and version_id not in output_version_ids:
            return version_id
    return None


def bind_cell_lineage(
    store: Any,
    *,
    workspace: Path | str,
    before: Mapping[str, Any],
    source: str,
    files_written: Sequence[str],
    artifacts: Sequence[Mapping[str, Any]],
    root_frame_id: str,
    project_id: str,
    producing_cell_id: str | None,
    frame_id: str | None = None,
) -> list[str]:
    """Map reads to versions and attach input→output edges for this Cell."""

    root = Path(workspace)
    reads = infer_read_paths(root, before, source, files_written)
    output_ids = {
        str(item.get("version_id") or item.get("latest_version_id") or "")
        for item in artifacts
        if isinstance(item, Mapping)
    }
    output_ids.discard("")
    input_ids: list[str] = []
    for relative in reads:
        version_id = _resolve_input_version(
            store,
            workspace=root,
            relative=relative,
            root_frame_id=root_frame_id,
            project_id=project_id,
            output_version_ids=output_ids,
        )
        if version_id and version_id not in input_ids:
            input_ids.append(version_id)
    if not input_ids or not output_ids:
        return reads
    for output_id in output_ids:
        for input_id in input_ids:
            if input_id == output_id:
                continue
            store.add_lineage_edge(
                input_version_id=input_id,
                output_version_id=output_id,
                producing_cell_id=producing_cell_id,
                frame_id=frame_id or root_frame_id,
            )
    return reads
