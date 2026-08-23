"""Stage 8 host-side Notebook reads and cross-language lineage.

File reads are mapped to Artifact versions on the host after a Cell returns.
A later write in the same Cell becomes an input→output lineage edge.  This is
deliberately independent of in-kernel provenance so Python and R share one
rule.  The official live REPL is the Stage 8 flag; the older developer
``notebook_repl`` switch remains an independent override.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

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
    # ``Store.list_versions`` is newest-first.  When a Cell reads and then
    # overwrites the same Artifact, the freshly captured output version is
    # excluded above and the next row is the exact input head.  Reversing this
    # list linked a third overwrite back to the oldest version instead.
    for item in versions:
        version_id = str(item.get("version_id") or "")
        if version_id and version_id not in output_version_ids:
            return version_id
    return None


def bind_cell_lineage(
    store: Any,
    *,
    workspace: Path | str,
    artifacts: Sequence[Mapping[str, Any]],
    root_frame_id: str,
    project_id: str,
    producing_cell_id: str | None,
    observed_reads: Sequence[str],
    frame_id: str | None = None,
) -> list[str]:
    """Map worker-observed reads to versions and attach Cell lineage edges."""

    root = Path(workspace)
    reads = []
    resolved_root = root.resolve()
    for raw in observed_reads:
        text = str(raw or "").replace("\\", "/").strip()
        if (
            not text
            or text.startswith(_SKIP_PREFIXES)
            or text.startswith("/")
            or ".." in Path(text).parts
        ):
            continue
        try:
            relative = str((resolved_root / text).resolve().relative_to(resolved_root))
        except (OSError, ValueError):
            continue
        if relative and relative not in reads:
            reads.append(relative)
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
