"""Deterministic Python/R ``.ipynb`` export from immutable execution history."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from typing import Any, Mapping, Protocol

from openai4s.storage.branch_projection import project_branch_records


class CellStore(Protocol):
    def list_cells(
        self, root_frame_id: str, *, branch_id: str | None = None
    ) -> list[dict]:
        ...

    def get_session_branch(self, branch_id: str) -> dict | None:
        ...

    def get_session_checkpoint(self, checkpoint_id: str) -> dict | None:
        ...


_LANGUAGE = {
    "python": {
        "display_name": "Python (OpenAI4S export)",
        "name": "python3",
        "language": "python",
        "mimetype": "text/x-python",
        "file_extension": ".py",
    },
    "r": {
        "display_name": "R (OpenAI4S export)",
        "name": "ir",
        "language": "R",
        "mimetype": "text/x-r-source",
        "file_extension": ".r",
    },
}


class NotebookExportService:
    """Project append-only cell records into canonical Jupyter documents."""

    def __init__(self, store: CellStore) -> None:
        self.store = store

    def notebook(
        self,
        root_frame_id: str,
        language: str,
        *,
        branch_id: str | None = None,
    ) -> dict[str, Any]:
        language = language.lower()
        if language not in _LANGUAGE:
            raise ValueError("notebook language must be python or r")
        source_cells = [
            cell
            for cell in self._branch_cells(root_frame_id, branch_id or root_frame_id)
            if str(cell.get("language") or "python").lower() == language
        ]
        cells = [
            self._cell(
                cell,
                revision=self._state_revision(cell, fallback=index + 1),
            )
            for index, cell in enumerate(source_cells)
        ]
        spec = _LANGUAGE[language]
        return {
            "cells": cells,
            "metadata": {
                "kernelspec": {
                    "display_name": spec["display_name"],
                    "language": spec["language"],
                    "name": spec["name"],
                },
                "language_info": {
                    "name": spec["language"],
                    "mimetype": spec["mimetype"],
                    "file_extension": spec["file_extension"],
                },
                "openai4s": {
                    "root_frame_id": root_frame_id,
                    "branch_id": branch_id or root_frame_id,
                    "language": language,
                    "history_is_read_only": True,
                    "completion_contract": (
                        "host.submit_output from Python scientific cells"
                    ),
                },
            },
            "nbformat": 4,
            "nbformat_minor": 5,
        }

    def bundle(self, root_frame_id: str, *, branch_id: str | None = None) -> bytes:
        """Return a stable ZIP with separate Python and R notebooks."""

        stem = self._safe_stem(root_frame_id)
        documents = {
            f"{stem}.python.ipynb": self._encode(
                self.notebook(root_frame_id, "python", branch_id=branch_id)
            ),
            f"{stem}.r.ipynb": self._encode(
                self.notebook(root_frame_id, "r", branch_id=branch_id)
            ),
        }
        manifest = {
            "version": 1,
            "root_frame_id": root_frame_id,
            "branch_id": branch_id or root_frame_id,
            "files": [
                {
                    "name": name,
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
                for name, data in sorted(documents.items())
            ],
        }
        documents["manifest.json"] = self._encode(manifest)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(documents.items()):
                info = zipfile.ZipInfo(name)
                # A fixed timestamp makes equal execution histories byte-stable.
                info.date_time = (1980, 1, 1, 0, 0, 0)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                archive.writestr(info, data)
        return output.getvalue()

    def export(
        self,
        root_frame_id: str,
        *,
        language: str | None = None,
        branch_id: str | None = None,
    ) -> dict[str, Any]:
        """Return immutable bytes plus the exact HTTP descriptor Gateway needs."""

        stem = self._safe_stem(root_frame_id)
        if language is None or str(language).lower() == "bundle":
            data = self.bundle(root_frame_id, branch_id=branch_id)
            filename = f"{stem}.notebooks.zip"
            content_type = "application/zip"
        else:
            normalized = str(language).lower()
            data = self._encode(
                self.notebook(root_frame_id, normalized, branch_id=branch_id)
            )
            filename = f"{stem}.{normalized}.ipynb"
            content_type = "application/x-ipynb+json"
        return {
            "filename": filename,
            "content_type": content_type,
            "data": data,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "immutable": True,
        }

    def _branch_cells(self, root_frame_id: str, branch_id: str) -> list[dict]:
        def local(selected: str) -> list[dict]:
            try:
                return self.store.list_cells(root_frame_id, branch_id=selected)
            except TypeError as error:
                if selected != root_frame_id or "branch_id" not in str(error):
                    raise
                return self.store.list_cells(root_frame_id)

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

    @classmethod
    def _cell(cls, cell: Mapping[str, Any], *, revision: int) -> dict[str, Any]:
        outputs: list[dict[str, Any]] = []
        for name in ("stdout", "stderr"):
            text = str(cell.get(name) or "")
            if text:
                outputs.append(
                    {
                        "name": name,
                        "output_type": "stream",
                        "text": cls._lines(text),
                    }
                )
        error = str(cell.get("error") or "")
        if error:
            headline = next(
                (line for line in error.splitlines() if line.strip()), error
            )
            outputs.append(
                {
                    "ename": "OpenAI4SCellError",
                    "evalue": headline[:1000],
                    "output_type": "error",
                    "traceback": cls._lines(error),
                }
            )
        figures = list(cell.get("figures") or ())
        if figures:
            outputs.append(
                {
                    "name": "stdout",
                    "output_type": "stream",
                    "text": [
                        "[OpenAI4S artifacts] "
                        + ", ".join(str(item) for item in figures)
                    ],
                }
            )
        cell_id = str(cell.get("producing_cell_id") or f"export-{revision}")
        return {
            "cell_type": "code",
            "execution_count": cls._execution_count(cell, revision),
            "id": cls._jupyter_id(cell_id),
            "metadata": {
                "openai4s": {
                    "producing_cell_id": cell_id,
                    "cell_index": cell.get("cell_index"),
                    "kernel_id": cell.get("kernel_id"),
                    "origin": cell.get("origin"),
                    "status": cell.get("status"),
                    "state_revision": revision,
                    "generation_id": cell.get("generation_id"),
                    "created_at": cell.get("created_at"),
                    "figures": figures,
                    "files_read": list(cell.get("files_read") or ()),
                    "files_written": list(cell.get("files_written") or ()),
                    "history_is_read_only": True,
                }
            },
            "outputs": outputs,
            "source": cls._lines(str(cell.get("code") or "")),
        }

    @staticmethod
    def _execution_count(cell: Mapping[str, Any], fallback: int) -> int:
        value = cell.get("cell_index")
        try:
            count = int(value)
        except (TypeError, ValueError):
            count = fallback
        return max(1, count)

    @staticmethod
    def _state_revision(cell: Mapping[str, Any], fallback: int) -> int:
        """Keep the session-global revision in language-split exports."""

        value = cell.get("state_revision")
        if value is None:
            value = cell.get("cell_index")
        try:
            revision = int(value)
        except (TypeError, ValueError):
            revision = fallback
        return max(1, revision)

    @staticmethod
    def _jupyter_id(value: str) -> str:
        safe = "".join(ch for ch in value if ch.isalnum() or ch in "-_")
        if not safe:
            safe = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
        return safe[:64]

    @staticmethod
    def _lines(value: str) -> list[str]:
        if not value:
            return []
        return value.splitlines(keepends=True)

    @staticmethod
    def _encode(value: Any) -> bytes:
        return (
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
        ).encode("utf-8")

    @staticmethod
    def _safe_stem(value: str) -> str:
        stem = "".join(
            character
            for character in str(value or "")
            if character.isalnum() or character in "-_"
        )
        if not stem:
            raise ValueError("root_frame_id must contain a safe filename character")
        return stem[:120]


__all__ = ["CellStore", "NotebookExportService"]
