"""Read-only execution and lineage projections for the Web UI."""

from __future__ import annotations

from typing import Callable, Protocol

from openai4s.agent.actions import is_completion_only_cell


class ExecutionViewStore(Protocol):
    def list_cells(self, root_frame_id: str) -> list[dict]: ...

    def get_artifact(self, artifact_id: str) -> dict | None: ...

    def version_meta(self, version_id: str) -> dict | None: ...

    def lineage_inputs(self, version_id: str) -> list[dict]: ...

    def cell_detail(self, producing_cell_id: str) -> dict | None: ...


class ExecutionViewService:
    """Project persisted execution records into Notebook/Provenance DTOs."""

    def __init__(
        self,
        *,
        store: ExecutionViewStore,
        format_timestamp: Callable[[int | float | None], str | None],
    ) -> None:
        self.store = store
        self.format_timestamp = format_timestamp

    def execution_log(self, root_frame_id: str) -> dict:
        kernels: list[str] = []
        entries: list[dict] = []
        for ordinal, cell in enumerate(self.store.list_cells(root_frame_id), 1):
            language = cell.get("language") or "python"
            if is_completion_only_cell(cell.get("code") or "", language):
                continue
            kernel_id = cell.get("kernel_id") or "python"
            if kernel_id not in kernels:
                kernels.append(kernel_id)
            cell_index = cell.get("cell_index")
            producing_cell_id = cell.get("producing_cell_id")
            identity = producing_cell_id or f"legacy-cell-{cell_index or ordinal}"
            revision_of = None
            attempt_group_id = identity
            attempt = 1
            if entries and _continues_failed_attempt(entries[-1], cell, kernel_id, language):
                previous = entries[-1]
                revision_of = previous["producing_cell_id"]
                attempt_group_id = previous["attempt_group_id"]
                attempt = previous["attempt"] + 1
            entries.append(
                {
                    "producing_cell_id": identity,
                    "cell_index": cell_index,
                    "state_revision": (
                        cell.get("state_revision")
                        if cell.get("state_revision") is not None
                        else cell_index
                    ),
                    # Store derives this from the immutable execution-attempt
                    # association; the view never guesses from kernel labels.
                    "generation_id": cell.get("generation_id"),
                    "kernel_id": kernel_id,
                    "language": language,
                    "origin": cell.get("origin"),
                    "source": cell.get("code") or "",
                    "stdout": cell.get("stdout") or "",
                    "stderr": cell.get("stderr") or "",
                    "error": cell.get("error") or "",
                    "status": cell.get("status") or "ok",
                    "figures": cell.get("figures") or [],
                    "files_written": cell.get("files_written") or [],
                    "files_read": cell.get("files_read") or [],
                    "cpu_seconds": cell.get("cpu_s"),
                    "peak_rss_kb": cell.get("peak_rss_kb"),
                    # Retry metadata is a read-only projection. Every physical
                    # attempt remains a separate immutable execution-log row;
                    # the Notebook may collapse a group and let users expand it.
                    "attempt_group_id": attempt_group_id,
                    "attempt": attempt,
                    "revision_of": revision_of,
                    "is_latest_attempt": True,
                    "attempt_count": 1,
                }
            )
        groups: dict[str, list[dict]] = {}
        for entry in entries:
            groups.setdefault(entry["attempt_group_id"], []).append(entry)
        for attempts in groups.values():
            count = len(attempts)
            for position, entry in enumerate(attempts, 1):
                entry["attempt_count"] = count
                entry["is_latest_attempt"] = position == count
        return {"kernels": kernels, "entries": entries}

    def artifact_lineage(self, artifact_id: str) -> dict:
        artifact = self.store.get_artifact(artifact_id)
        if not artifact:
            return {
                "artifact_id": artifact_id,
                "filename": None,
                "interactions": [],
                "dependency_mappings": {"inputs": []},
            }

        interactions = []
        version_id = artifact.get("latest_version_id")
        cell = None
        version = None
        edge_inputs: list[str] = []
        if version_id:
            version = self.store.version_meta(version_id)
            for item in self.store.lineage_inputs(version_id):
                label = (
                    item.get("filename")
                    or item.get("path")
                    or item.get("version_id")
                )
                if label:
                    edge_inputs.append(str(label))
            producing_cell_id = (version or {}).get("producing_cell_id")
            if producing_cell_id:
                cell = self.store.cell_detail(producing_cell_id)

        files_written: list[str] = []
        legacy_reads: list[str] = []
        if cell:
            files_written = cell.get("files_written") or []
            legacy_reads = cell.get("files_read") or []

        known_reads: list[str] = []
        seen_reads: set[str] = set()
        for filename in [*legacy_reads, *edge_inputs]:
            if filename and filename not in seen_reads:
                seen_reads.add(filename)
                known_reads.append(filename)

        outputs = set(files_written)
        outputs.add(artifact["filename"])
        inputs = [filename for filename in known_reads if filename not in outputs]
        if cell:
            interactions.append(
                {
                    "kind": "cell",
                    "cell_index": cell.get("cell_index"),
                    "kernel_id": cell.get("kernel_id") or "python",
                    "language": cell.get("language") or "python",
                    "exit_status": cell.get("status") or "ok",
                    "source": cell.get("code") or "",
                    "files_written": files_written,
                    "files_read": known_reads,
                }
            )
        interactions.append(
            {
                "kind": "save",
                "at": self.format_timestamp(
                    (version or {}).get("created_at")
                    or artifact.get("created_at")
                ),
            }
        )
        return {
            "artifact_id": artifact_id,
            "filename": artifact.get("filename"),
            "interactions": interactions,
            "dependency_mappings": {"inputs": inputs},
        }


__all__ = ["ExecutionViewService"]


def _continues_failed_attempt(
    previous: dict,
    current: dict,
    kernel_id: str,
    language: str,
) -> bool:
    """Recognize the smallest reliable retry shape without mutating history.

    A retry chain starts only after a failed agent-style Cell and stays inside
    the same language/runtime segment. The first success after that failure is
    the final revision; a later independent Cell starts a new group.
    """

    if previous.get("status") not in {"error", "failed"}:
        return False
    if previous.get("kernel_id") != kernel_id:
        return False
    if previous.get("language") != language:
        return False
    return previous.get("origin") == "agent" and current.get("origin") == "agent"
