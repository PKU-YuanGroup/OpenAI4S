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

    def list_branch_messages(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
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


#: Bounded so one document cannot become a file listing, and the cut is
#: announced rather than silent.
_MAX_RENDERED_INPUTS = 200


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

    @staticmethod
    def _refs_from_metadata(metadata: Any) -> list[Mapping[str, Any]]:
        """The refs a message recorded, parsed strictly and from metadata only.

        `metadata` comes back from the store as a JSON *string*, and it is
        free-form: anything a future writer puts there arrives here too. So the
        shape is checked rather than assumed, and the message *body* is never
        looked at -- the `@name#version` text in it is what the resolver read,
        not what it resolved to, and after a copy it can name a version this
        session cannot see.
        """
        if type(metadata) is str:
            try:
                metadata = json.loads(metadata)
            except (ValueError, TypeError):
                return []
        if not isinstance(metadata, Mapping):
            return []
        refs = metadata.get("artifact_refs")
        if not isinstance(refs, list):
            return []
        return [ref for ref in refs if isinstance(ref, Mapping)]

    def _referenced_artifacts(self, root_frame_id: str, branch_id: str) -> list[dict]:
        """The artifacts this branch's turns were given, pinned as they were.

        Read from the messages rather than the cells because that is where the
        choice lives: a reference is something the researcher wrote into a
        turn, and it is resolved to an exact version at send time. The cells
        only ever see files.

        Branch-aware on purpose. `list_messages` returns the frame's rows;
        `list_branch_messages` runs the production projector, so a fork
        inherits its parent's inputs and a revert stops naming the files an
        abandoned turn referenced -- which would otherwise leak an abandoned
        file name and version into a document meant for publication.

        `limit=None` because `list_branch_messages` defaults to 300, and a
        provenance list that is quietly partial is worse than absent: a reader
        cannot tell. There is deliberately no `TypeError` fallback around this
        call -- one would swallow a `TypeError` raised *inside* the reader and
        silently return to the paged default, which is the same silent-partial
        failure in a shape that is harder to see.
        """
        messages = self.store.list_branch_messages(
            root_frame_id, branch_id=branch_id, limit=None
        )
        seen: set[str] = set()
        out: list[dict] = []
        for message in messages or ():
            if not isinstance(message, Mapping):
                continue
            for ref in self._refs_from_metadata(message.get("metadata")):
                name = ref.get("display_name")
                version = ref.get("version_id")
                if type(name) is not str or not name:
                    continue
                if type(version) is not str:
                    version = ""
                # Keyed on the version, because that is the identity: the same
                # pinned version cited twice -- or under an alias -- is one
                # input, and two versions of one file are two. An unpinned
                # reference has no version to key on, so it falls back to the
                # name rather than collapsing every unpinned file into one.
                key = f"v:{version}" if version else f"n:{name}"
                if key in seen:
                    continue
                seen.add(key)
                out.append({"display_name": name, "version_id": version})
        return out

    def markdown(self, root_frame_id: str, *, branch_id: str | None = None) -> bytes:
        """Render the branch as one Markdown document.

        The `.ipynb` forms are for re-running the work; this one is for reading
        it and for pasting it somewhere that is not Jupyter -- an issue, a lab
        notebook, a supplementary methods section. Both languages appear in the
        one file, in execution order, because the interleaving *is* the record:
        splitting them apart is what the bundle already offers and it loses
        which R cell answered which Python cell.

        Structured rather than pretty-printed. Every cell carries its index,
        language and state revision in a heading a reader can cite, and its
        output is fenced rather than indented so a traceback survives a
        round trip through anything that reflows text. Nothing here re-derives
        a cell's content: it is the same `_branch_cells` the notebooks use, so
        the two exports cannot disagree about what ran.
        """
        selected = branch_id or root_frame_id
        cells = self._branch_cells(root_frame_id, selected)
        lines: list[str] = [
            f"# Session {root_frame_id}",
            "",
            f"- branch: `{selected}`",
            f"- cells: {len(cells)}",
            "- history is read-only; this document is a rendering, not a source",
            "",
        ]
        # Provenance, and only provenance. This document is meant to be pasted
        # into "an issue, a lab notebook, a supplementary methods section", and
        # a methods section whose inputs are unnamed is the kind of incomplete
        # that matters: the reader cannot tell which version of which file
        # produced the numbers, and the session knows, because the reference
        # was pinned when the turn was sent. The message text around it is the
        # researcher's unpublished thinking and is a separate decision from
        # naming the file it pointed at, so it is not exported here.
        inputs = self._referenced_artifacts(root_frame_id, selected)
        if inputs:
            lines.append("## Inputs")
            lines.append("")
            lines.append(
                "Artifacts referenced by this session's turns, at the version "
                "that was sent."
            )
            lines.append("")
            for item in inputs[:_MAX_RENDERED_INPUTS]:
                version = item["version_id"]
                pinned = f"version `{version}`" if version else "unpinned"
                lines.append(f"- `{item['display_name']}` — {pinned}")
            if len(inputs) > _MAX_RENDERED_INPUTS:
                lines.append(
                    f"- ...and {len(inputs) - _MAX_RENDERED_INPUTS} more, "
                    "not listed here"
                )
            lines.append("")
        for index, cell in enumerate(cells, start=1):
            language = str(cell.get("language") or "python").lower()
            revision = self._state_revision(cell, fallback=index)
            lines.append(f"## Cell {index} — {language} (state revision {revision})")
            lines.append("")
            source = str(cell.get("code") or cell.get("source") or "")
            fence = "r" if language == "r" else "python"
            lines.append(f"```{fence}")
            lines.append(source.rstrip("\n"))
            lines.append("```")
            lines.append("")
            # The same three fields `_cell` reads, so the two exports cannot
            # disagree about what a cell produced.
            for name, label in (("stdout", "Output"), ("stderr", "Stderr")):
                text = str(cell.get(name) or "").rstrip("\n")
                if not text:
                    continue
                lines.append(f"{label}:")
                lines.append("")
                lines.append("```text")
                lines.append(text)
                lines.append("```")
                lines.append("")
            figures = list(cell.get("figures") or ())
            if figures:
                lines.append("Artifacts: " + ", ".join(f"`{item}`" for item in figures))
                lines.append("")
            error = str(cell.get("error") or "").rstrip("\n")
            if error:
                # Kept, and labelled. A failed cell is part of the record --
                # dropping it would make the document describe a run that went
                # smoothly, which is the one thing a reader must not conclude.
                lines.append("Error:")
                lines.append("")
                lines.append("```text")
                lines.append(error)
                lines.append("```")
                lines.append("")
        return ("\n".join(lines).rstrip("\n") + "\n").encode("utf-8")

    def export(
        self,
        root_frame_id: str,
        *,
        language: str | None = None,
        branch_id: str | None = None,
    ) -> dict[str, Any]:
        """Return immutable bytes plus the exact HTTP descriptor Gateway needs."""

        stem = self._safe_stem(root_frame_id)
        if language is not None and str(language).lower() == "markdown":
            data = self.markdown(root_frame_id, branch_id=branch_id)
            filename = f"{stem}.md"
            content_type = "text/markdown; charset=utf-8"
        elif language is None or str(language).lower() == "bundle":
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
