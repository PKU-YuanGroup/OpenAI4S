"""Executed-code view and hierarchical sources export (root + delegated frames).

A session's executed code is spread across per-frame ``execution_log`` rows:
the root Notebook cells under the root frame, every delegated child Cell
under its own ``kind='delegate'`` frame (see ``openai4s/agent/cell_record.py``),
nested children recursively below those.  This service is the one read-only
place that assembles the hierarchy:

* :meth:`ExecutionSourcesService.projection` — a bounded, typed JSON tree for
  the UI's "Executed code" surface.  Frames carry name/parent/depth/status/
  order and per-frame counts; cells carry order, language, status, source
  SHA-256, kernel generation, environment identity, and artifact-version
  links.  Code text is deliberately NOT inlined — the existing per-frame
  ``GET /frames/{fid}/execution-log`` route serves it.
* :meth:`ExecutionSourcesService.export` — ``sources.zip``: the executed
  source files themselves (``root/`` + ``children/<ordinal>_<name>_<fid8>/``
  recursively), per-frame ``session.py``/``session.R`` concatenations with
  ``# %%`` separators, a NEW ``manifest.json`` (this is not the notebook
  bundle's manifest, which stays untouched), and a bilingual README pair
  warning that cells ran in a persistent kernel.

The export carries ONLY ``execution_log`` fields plus public metadata: never
conversation prompts, host payloads, stdout/stderr, or credentials.  Failed
and interrupted cells are included and marked — dropping them would make the
archive describe a run that went smoothly, which is the one thing a reader
must not conclude.

Both surfaces are deliberately the RAW execution history: rows the
read-only Notebook projection hides (protocol-only ``host.submit_output``
completion cells, non-scientific unpinned cells) are counted, listed, and
exported here.  Per-frame ``counts`` can therefore exceed the entries the
frame's own ``/execution-log`` route renders — that route is the Notebook's
curated view; this one is the audit trail.

Determinism: fixed ZIP timestamps, sorted entries, canonical JSON, and a
``generated_at`` DERIVED from the newest stored ``created_at`` rather than
from the wall clock, so exporting the same durable history twice — including
across a daemon restart — yields byte-identical archives.
"""

from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from typing import Any, Mapping

from openai4s.storage.branch_projection import project_branch_records

#: Hard bounds so one projection cannot become an unbounded walk: delegation
#: fan-out is capped at 48 with MAX_DEPTH 4, so real trees are far smaller.
_MAX_FRAMES = 200
_MAX_CELLS_PER_FRAME = 2000
_MAX_DEPTH = 12

_README_EN = """# Executed code sources

This archive is the execution history of one OpenAI4S session: the exact
source code every recorded Cell ran, for the root session (`root/`) and every
delegated child frame (`children/<ordinal>_<name>_<frameid8>/`, nested
recursively), in true execution order.  Failed and interrupted cells are
included and marked by status in the file name (e.g. `cell_0002_error.py`).

* Cells ran sequentially in a **persistent kernel**: earlier cells created
  the variables, imports, and files that later cells rely on.  A single cell
  file is therefore **not guaranteed to run standalone**.  Each frame's
  `session.py` / `session.R` concatenates its cells in order with `# %%`
  separators, which is the closest runnable rendering of the history.
* `manifest.json` describes every frame and cell: ids, order, language,
  status, source SHA-256, kernel generation, environment identity, and
  artifact-version links.
* The archive contains only executed source code and public execution
  metadata — no conversation prompts, no host payloads, no cell output,
  and no credentials.
"""

_README_ZH = """# 已执行代码源文件

本压缩包是一个 OpenAI4S 会话的执行历史：根会话（`root/`）以及每个被委派的
子 frame（`children/<序号>_<名称>_<frameid8>/`，递归嵌套）中每个已记录 Cell
实际执行的源代码，按真实执行顺序排列。失败与被中断的 Cell 一并包含，并在
文件名中标注状态（例如 `cell_0002_error.py`）。

* 这些 Cell 是在**持久内核**中顺序执行的：前面的 Cell 创建了后面 Cell 所
  依赖的变量、导入与文件，因此**单个 Cell 文件不保证可以独立运行**。每个
  frame 的 `session.py` / `session.R` 用 `# %%` 分隔符按顺序拼接了该 frame
  的全部 Cell，是最接近可运行形态的历史呈现。
* `manifest.json` 描述了每个 frame 与 Cell：id、顺序、语言、状态、源代码
  SHA-256、内核 generation、环境标识以及 Artifact 版本关联。
* 压缩包只包含已执行的源代码与公开的执行元数据——不含对话提示词、host
  载荷、Cell 输出或任何凭据。
"""


class ExecutionSourcesService:
    """Read-only projection/export of the per-frame executed-code hierarchy."""

    def __init__(self, store: Any) -> None:
        self.store = store

    # -- JSON projection -------------------------------------------------
    def projection(
        self, root_frame_id: str, *, branch_id: str | None = None
    ) -> dict[str, Any]:
        frames, truncated = self._collect(root_frame_id, branch_id)
        return {
            "root_frame_id": root_frame_id,
            "truncated": truncated,
            "frames": [
                {
                    "frame_id": frame["frame_id"],
                    "parent_id": frame["parent_id"],
                    "root_frame_id": frame["root_frame_id"],
                    "name": frame["name"],
                    "kind": frame["kind"],
                    "depth": frame["depth"],
                    "status": frame["status"],
                    "order": frame["order"],
                    "counts": frame["counts"],
                    "cells": [
                        {
                            "id": cell["id"],
                            "seq": cell["order"],
                            "language": cell["language"],
                            "status": cell["status"],
                            "source_sha256": cell["source_sha256"],
                            "generation_id": cell["generation_id"],
                            "environment": cell["environment"],
                            "artifacts": cell["artifacts"],
                            "interrupted": cell["interrupted"],
                        }
                        for cell in frame["cells"]
                    ],
                }
                for frame in frames
            ],
        }

    # -- sources.zip -----------------------------------------------------
    def export(
        self, root_frame_id: str, *, branch_id: str | None = None
    ) -> dict[str, Any]:
        """Return immutable ZIP bytes plus the HTTP descriptor Gateway needs."""

        frames, truncated = self._collect(root_frame_id, branch_id)
        documents: dict[str, bytes] = {
            "README.md": _README_EN.encode("utf-8"),
            "README_zh.md": _README_ZH.encode("utf-8"),
        }
        manifest_frames: list[dict[str, Any]] = []
        manifest_cells: list[dict[str, Any]] = []
        newest = 0
        for frame in frames:
            directory = frame["path"]
            manifest_frames.append(
                {
                    "frame_id": frame["frame_id"],
                    "parent_id": frame["parent_id"],
                    "root_frame_id": frame["root_frame_id"],
                    "name": frame["name"],
                    "kind": frame["kind"],
                    "depth": frame["depth"],
                    "status": frame["status"],
                    "order": frame["order"],
                    "path": directory,
                }
            )
            newest = max(newest, frame["created_at"] or 0)
            sessions: dict[str, list[str]] = {}
            for cell in frame["cells"]:
                newest = max(newest, cell["created_at"] or 0)
                extension = "R" if cell["language"] == "r" else "py"
                file_name = f"cell_{cell['order']:04d}_{cell['status']}.{extension}"
                path = f"{directory}/{file_name}"
                documents[path] = cell["source_bytes"]
                manifest_cells.append(
                    {
                        "id": cell["id"],
                        "frame_id": frame["frame_id"],
                        "order": cell["order"],
                        "language": cell["language"],
                        "status": cell["status"],
                        "source_sha256": cell["source_sha256"],
                        "generation_id": cell["generation_id"],
                        "environment": cell["environment"],
                        "artifacts": cell["artifacts"],
                        "interrupted": cell["interrupted"],
                        "path": path,
                    }
                )
                separator = (
                    f"# %% cell {cell['order']} — {cell['language']} — "
                    f"{cell['status']}\n"
                )
                sessions.setdefault(extension, []).append(
                    separator + cell["source_bytes"].decode("utf-8")
                )
            for extension, blocks in sessions.items():
                label = frame["name"] or frame["frame_id"]
                header = (
                    f"# Executed code — frame {frame['frame_id']} ({label})\n"
                    "# Cells ran in order in a persistent kernel; single cells\n"
                    "# are not guaranteed to run standalone.\n\n"
                )
                documents[f"{directory}/session.{extension}"] = (
                    header + "\n".join(blocks)
                ).encode("utf-8")
        manifest = {
            "version": 1,
            "root_frame_id": root_frame_id,
            # Derived from the newest stored created_at (never the wall
            # clock) so identical durable history exports byte-identically.
            "generated_at": self._stored_timestamp(newest),
            "truncated": truncated,
            "frames": manifest_frames,
            "cells": manifest_cells,
        }
        documents["manifest.json"] = (
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
        ).encode("utf-8")

        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(documents.items()):
                info = zipfile.ZipInfo(name)
                # A fixed timestamp makes equal execution histories byte-stable.
                info.date_time = (1980, 1, 1, 0, 0, 0)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                archive.writestr(info, data)
        data = output.getvalue()
        return {
            "filename": f"{self._safe_stem(root_frame_id)}.sources.zip",
            "content_type": "application/zip",
            "data": data,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "immutable": True,
        }

    # -- collection ------------------------------------------------------
    def _collect(
        self, root_frame_id: str, branch_id: str | None
    ) -> tuple[list[dict[str, Any]], bool]:
        root = self.store.get_frame(root_frame_id)
        if root is None:
            raise KeyError(f"unknown frame {root_frame_id!r}")
        artifact_links = self._artifact_links(root_frame_id)
        generation_cache: dict[str, dict[str, Any] | None] = {}
        frames: list[dict[str, Any]] = []
        truncated = False

        def visit(
            frame: Mapping[str, Any],
            *,
            parent_id: str | None,
            depth: int,
            order: int,
            directory: str,
        ) -> None:
            nonlocal truncated
            if len(frames) >= _MAX_FRAMES or depth > _MAX_DEPTH:
                truncated = True
                return
            frame_id = str(frame.get("frame_id"))
            cells, cells_truncated = self._frame_cells(
                frame_id,
                root_frame_id=root_frame_id,
                branch_id=branch_id,
                is_root=parent_id is None,
                artifact_links=artifact_links,
                generation_cache=generation_cache,
            )
            truncated = truncated or cells_truncated
            counts = {
                "cells": len(cells),
                "ok": sum(1 for cell in cells if cell["status"] == "ok"),
                "error": sum(1 for cell in cells if cell["status"] == "error"),
                "interrupted": sum(
                    1 for cell in cells if cell["status"] == "interrupted"
                ),
            }
            frames.append(
                {
                    "frame_id": frame_id,
                    "parent_id": parent_id,
                    "root_frame_id": str(frame.get("root_frame_id") or frame_id),
                    "name": frame.get("name"),
                    "kind": str(frame.get("kind") or "turn"),
                    "depth": depth,
                    "status": frame.get("status"),
                    "order": order,
                    "created_at": self._int(frame.get("created_at")),
                    "path": directory,
                    "counts": counts,
                    "cells": cells,
                }
            )
            for child_order, child in enumerate(
                self._delegate_children(frame_id), start=1
            ):
                child_id = str(child.get("frame_id"))
                child_dir = (
                    f"{directory}/children/"
                    f"{child_order}_{self._safe_component(child.get('name'))}_"
                    f"{self._safe_stem(child_id)[:8]}"
                )
                # Child rows come back name/kind/status/depth only; re-read
                # the full row so created_at and root scope are real.
                full = self.store.get_frame(child_id) or dict(child)
                visit(
                    full,
                    parent_id=frame_id,
                    depth=depth + 1,
                    order=child_order,
                    directory=child_dir,
                )

        visit(root, parent_id=None, depth=0, order=0, directory="root")
        # The archive nests children under root/'s sibling namespace, not
        # inside it: strip the leading "root/" from child paths.
        for frame in frames:
            if frame["path"].startswith("root/children/"):
                frame["path"] = frame["path"][len("root/") :]
        return frames, truncated

    def _delegate_children(self, frame_id: str) -> list[dict[str, Any]]:
        detail = None
        try:
            detail = self.store.frame_detail(frame_id, page=0, page_size=1)
        except Exception:  # noqa: BLE001 - a missing frame yields no children
            detail = None
        children = (detail or {}).get("children") or []
        return [
            child
            for child in children
            if isinstance(child, Mapping) and child.get("kind") == "delegate"
        ]

    def _frame_cells(
        self,
        frame_id: str,
        *,
        root_frame_id: str,
        branch_id: str | None,
        is_root: bool,
        artifact_links: Mapping[str, list[str]],
        generation_cache: dict[str, dict[str, Any] | None],
    ) -> tuple[list[dict[str, Any]], bool]:
        if is_root:
            rows = self._branch_cells(frame_id, branch_id or frame_id)
        else:
            rows = self.store.list_cells(frame_id)
        truncated = len(rows) > _MAX_CELLS_PER_FRAME
        cells: list[dict[str, Any]] = []
        for order, row in enumerate(rows[:_MAX_CELLS_PER_FRAME], start=1):
            code = str(row.get("code") or row.get("source") or "")
            source_bytes = (
                code.encode("utf-8")
                if code.endswith("\n")
                else (code + "\n").encode("utf-8")
            )
            language = str(row.get("language") or "python").lower()
            cell_id = str(row.get("producing_cell_id") or f"cell-{order}")
            generation_id = row.get("generation_id")
            cells.append(
                {
                    "id": cell_id,
                    "order": order,
                    "language": "r" if language == "r" else "python",
                    "status": self._status(row),
                    "source_bytes": source_bytes,
                    "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
                    "generation_id": (str(generation_id) if generation_id else None),
                    "environment": self._environment(generation_id, generation_cache),
                    "artifacts": sorted(artifact_links.get(cell_id, [])),
                    "interrupted": bool(row.get("interrupted")),
                    "created_at": self._int(row.get("created_at")),
                }
            )
        return cells, truncated

    def _branch_cells(self, root_frame_id: str, branch_id: str) -> list[dict]:
        """The root frame follows its active branch, like every other export."""

        def local(selected: str) -> list[dict]:
            try:
                return self.store.list_cells(root_frame_id, branch_id=selected)
            except TypeError as error:
                if selected != root_frame_id or "branch_id" not in str(error):
                    raise
                return self.store.list_cells(root_frame_id)

        try:
            return project_branch_records(
                self.store,
                root_frame_id,
                branch_id,
                list_local=local,
                record_position=lambda cell: int(
                    cell.get("state_revision") or cell.get("cell_index") or 0
                ),
                cursor_key="cell_cursor",
            )
        except Exception:  # noqa: BLE001 - degrade to the raw per-frame log
            return self.store.list_cells(root_frame_id)

    def _artifact_links(self, root_frame_id: str) -> dict[str, list[str]]:
        """producing_cell_id -> artifact version ids, versions + observations.

        ``artifact_versions.producing_cell_id`` is stamped by the capture
        paths; ``artifact_capture_observations`` records a Cell that
        re-observed an existing version's bytes without minting a new one.
        Both are producer attributions the store made itself — never claims.
        """
        links: dict[str, set[str]] = {}

        def add(cell_id: Any, version_id: Any) -> None:
            if not cell_id or not version_id:
                return
            links.setdefault(str(cell_id), set()).add(str(version_id))

        try:
            artifacts = self.store.list_artifacts({"root_frame_id": root_frame_id})
        except Exception:  # noqa: BLE001 - links are optional metadata
            artifacts = []
        for artifact in artifacts or []:
            artifact_id = artifact.get("artifact_id")
            if not artifact_id:
                continue
            try:
                versions = self.store.list_versions(artifact_id)
            except Exception:  # noqa: BLE001
                versions = []
            for version in versions or []:
                add(version.get("producing_cell_id"), version.get("version_id"))
            try:
                observations = self.store.list_artifact_capture_observations(
                    artifact_id=artifact_id
                )
            except Exception:  # noqa: BLE001
                observations = []
            for observation in observations or []:
                add(
                    observation.get("producing_cell_id"),
                    observation.get("version_id"),
                )
        return {cell_id: sorted(ids) for cell_id, ids in links.items()}

    def _environment(
        self,
        generation_id: Any,
        cache: dict[str, dict[str, Any] | None],
    ) -> dict[str, Any] | None:
        """The generation's own environment identity, or an honest null."""
        if not generation_id:
            return None
        key = str(generation_id)
        if key not in cache:
            resolved: dict[str, Any] | None = None
            try:
                row = self.store.get_kernel_generation(key)
            except Exception:  # noqa: BLE001 - provenance stays optional
                row = None
            if isinstance(row, Mapping):
                environment = row.get("environment")
                if isinstance(environment, Mapping):
                    resolved = {
                        "name": environment.get("environment_name"),
                        "interpreter": environment.get("interpreter"),
                    }
            cache[key] = resolved
        return cache[key]

    @staticmethod
    def _status(row: Mapping[str, Any]) -> str:
        status = str(row.get("status") or "").lower()
        if status in {"ok", "error", "interrupted"}:
            return status
        if row.get("interrupted"):
            return "interrupted"
        return "error" if row.get("error") else "ok"

    @staticmethod
    def _int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _stored_timestamp(newest_ms: int) -> str | None:
        if newest_ms <= 0:
            return None
        base = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(newest_ms / 1000))
        return f"{base}.{newest_ms % 1000:03d}Z"

    @staticmethod
    def _safe_component(value: Any) -> str:
        text = str(value or "").strip()
        safe = "".join(
            character if (character.isalnum() or character in "-_") else "-"
            for character in text
        ).strip("-")
        while "--" in safe:
            safe = safe.replace("--", "-")
        return (safe or "child")[:40]

    @staticmethod
    def _safe_stem(value: str) -> str:
        stem = "".join(
            character
            for character in str(value or "")
            if character.isalnum() or character in "-_"
        )
        if not stem:
            raise ValueError("frame id must contain a safe filename character")
        return stem[:120]


__all__ = ["ExecutionSourcesService"]
