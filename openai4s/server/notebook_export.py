"""Deterministic Python/R ``.ipynb`` export from immutable execution history."""

from __future__ import annotations

import ast
import base64
import hashlib
import io
import json
import re
import zipfile
from typing import Any, Callable, Mapping, Protocol

from openai4s.agent.actions import is_completion_only_cell
from openai4s.server.completions import is_progress_narration, response_language
from openai4s.storage.branch_projection import project_branch_records


class CellStore(Protocol):
    def list_cells(
        self, root_frame_id: str, *, branch_id: str | None = None
    ) -> list[dict]: ...

    def get_session_branch(self, branch_id: str) -> dict | None: ...

    def get_session_checkpoint(self, checkpoint_id: str) -> dict | None: ...

    def list_branch_messages(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None = None,
        limit: int | None = None,
    ) -> list[dict]: ...


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


#: Cells a clean export embeds images from, by file suffix.
_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}
#: One embedded image, and all of them together. A figure past either is
#: listed by name instead, the same way a non-image artifact is.
_MAX_EMBED_BYTES = 8 * 1024 * 1024
_MAX_EMBED_TOTAL = 64 * 1024 * 1024
_HOST_CALL = re.compile(r"\bhost\.([A-Za-z_][A-Za-z0-9_]*)")
#: Environment probes: versions, installed packages, the platform.
_PROBE_SOURCE = re.compile(
    r"__version__|\bsys\.version\b|\bplatform\.|importlib\.metadata|"
    r"pkg_resources|find_spec\(|\b(?:pip3?|conda|mamba)\b[^\n]*"
    r"\b(?:list|show|freeze|check|--version)\b"
)
#: ``host`` calls that look at the session rather than analyse anything.
_DIAGNOSTIC_HOST_CALLS = frozenset(
    {"view_image", "list_skills", "search_skills", "load_skill", "exec_peek"}
)
#: First words of shell commands that only inspect the machine.
_PROBE_COMMANDS = frozenset(
    {
        "ls",
        "pwd",
        "which",
        "whoami",
        "env",
        "printenv",
        "uname",
        "nproc",
        "df",
        "du",
        "free",
        "lscpu",
        "sysctl",
        "nvidia-smi",
        "pip",
        "pip3",
        "conda",
        "mamba",
        "echo",
        "cat",
        "head",
        "tail",
        "wc",
        "file",
        "stat",
    }
)
_FAILED_STATUSES = frozenset({"error", "failed", "interrupted", "cancelled", "timeout"})


def _cell_language(cell: Mapping[str, Any]) -> str:
    return str(cell.get("language") or "python").lower()


def _created_at(row: Mapping[str, Any]) -> int:
    try:
        return int(row.get("created_at") or 0)
    except (TypeError, ValueError):
        return 0


def _suffix(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot >= 0 else ""


def _cell_failed(cell: Mapping[str, Any]) -> bool:
    return bool(
        str(cell.get("error") or "").strip()
        or cell.get("interrupted")
        or str(cell.get("status") or "").lower() in _FAILED_STATUSES
    )


def _host_call_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    target = node.func
    while isinstance(target, ast.Attribute):
        if isinstance(target.value, ast.Name) and target.value.id == "host":
            return target.attr
        target = target.value
    return None


def _is_probe_expression(node: ast.AST) -> bool:
    name = _host_call_name(node)
    if name in _DIAGNOSTIC_HOST_CALLS:
        return True
    if name == "bash" and isinstance(node, ast.Call) and node.args:
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            words = first.value.strip().split()
            return bool(words) and words[0] in _PROBE_COMMANDS
        return False
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"print", "display"}
        and node.args
    ):
        return all(_is_probe_expression(arg) for arg in node.args)
    return False


def _is_diagnostic_cell(code: str) -> bool:
    """A Python cell that only inspects the environment or the session.

    Imports alone are setup and stay. A cell is diagnostic when everything
    besides imports is a probe -- a version or package listing, a machine
    inspection through ``host.bash``, or a ``host`` call that looks at the
    session (``view_image``, skill listings) -- and at least one probe ran.
    """
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return False
    probed = False
    for statement in tree.body:
        if isinstance(statement, (ast.Import, ast.ImportFrom, ast.Pass)):
            continue
        segment = ast.get_source_segment(code, statement) or ""
        if _PROBE_SOURCE.search(segment) or (
            isinstance(statement, ast.Expr) and _is_probe_expression(statement.value)
        ):
            probed = True
            continue
        return False
    return probed


_RELATIVE_LINK = re.compile(r"\[([^\]\n]+)\]\((/[^)\s]*)\)")


def _portable_markdown(text: str) -> str:
    """Narration without links into this daemon, which cannot resolve in Jupyter."""

    return _RELATIVE_LINK.sub(r"\1", text)


def _clean_header(
    title: str | None, language: str, failed: int, diagnostic: int, *, zh: bool
) -> str:
    heading = f"# {title.strip()}" if title and title.strip() else "# OpenAI4S session"
    label = "R" if language == "r" else "Python"
    if zh:
        return (
            f"{heading}\n\n"
            f"由 OpenAI4S 导出的整理版 {label} Notebook：省略了 {failed} 个失败单元"
            f"和 {diagnostic} 个诊断单元，图已内嵌，智能体的叙述保留为 Markdown。"
            "需要每个单元的原始执行记录时，请在 OpenAI4S 里选择「原始执行日志」导出。\n\n"
            "调用 `host.*` 的单元只能在 OpenAI4S 内核里运行，已在单元开头注明。"
        )
    return (
        f"{heading}\n\n"
        f"Cleaned {label} notebook exported from OpenAI4S: {failed} failed and "
        f"{diagnostic} diagnostic cell(s) were left out, figures are embedded, "
        "and the agent's narration is kept as Markdown. For every cell exactly "
        "as it ran, use the raw execution log export in OpenAI4S.\n\n"
        "Cells that call `host.*` only run in an OpenAI4S kernel; each is "
        "marked at the top."
    )


#: M-61: outside OpenAI4S there is no ``host``, and the first cell calling it
#: stopped the notebook with a NameError. This cell, placed before them, makes
#: every ``host.*`` call a printed no-op there and leaves a real ``host`` alone.
_HOST_STUB_SOURCE = """\
# OpenAI4S: `host` exists only in an OpenAI4S kernel. Outside it, this stub
# turns each host.* call into a printed no-op returning None, so the notebook
# runs top to bottom; what those calls did (files, skills, sub-agents, model
# calls) is not reproduced.
try:
    host
except NameError:
    class _OpenAI4SHostStub:
        def __init__(self, name="host"):
            self._name = name

        def __getattr__(self, attribute):
            return _OpenAI4SHostStub(f"{self._name}.{attribute}")

        def __call__(self, *args, **kwargs):
            print(f"[skipped outside OpenAI4S] {self._name}(...)")
            return None

    host = _OpenAI4SHostStub()
"""
_HOST_STUB_NOTE = {
    "en": (
        "The first code cell is a stub: run outside OpenAI4S, `host.*` calls "
        "print a note and return None instead of stopping the notebook."
    ),
    "zh": "第一个代码单元是桩：在 OpenAI4S 之外运行时，`host.*` 调用只打印提示并返回 None，不会中断 Notebook。",
}
_BACKGROUND_NOTE = (
    "# Ran as a background task (host.exec_background) in a kernel of its own;\n"
    "# it shares no variables with the cells around it.\n"
)


class NotebookExportService:
    """Project append-only cell records into canonical Jupyter documents."""

    def __init__(
        self,
        store: CellStore,
        *,
        read_version_bytes: Callable[[str], bytes | None] | None = None,
    ) -> None:
        self.store = store
        # Frozen bytes of one Artifact version, or None. Only the clean export
        # needs them, to embed a cell's figures.
        self._read_version_bytes = read_version_bytes

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
        }
        # M-61: a session with no R cell no longer ships an empty r.ipynb.
        r_notebook = self.notebook(root_frame_id, "r", branch_id=branch_id)
        if r_notebook["cells"]:
            documents[f"{stem}.r.ipynb"] = self._encode(r_notebook)
        return self._zip(documents, root_frame_id, branch_id)

    def _zip(
        self,
        documents: dict[str, bytes],
        root_frame_id: str,
        branch_id: str | None,
    ) -> bytes:
        documents = dict(documents)
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
        variant: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Return immutable bytes plus the exact HTTP descriptor Gateway needs."""

        stem = self._safe_stem(root_frame_id)
        if variant is not None and str(variant).lower() == "clean":
            human = _human_stem(title)
            automatic = language is None or str(language).lower() == "auto"
            # M-61: one .ipynb for a one-language session; both clean
            # notebooks in a zip only when Python and R cells both remain.
            both = (
                self._clean_documents(root_frame_id, branch_id=branch_id, title=title)
                if automatic
                else {}
            )
            if len(both) == 2:
                base = human or stem
                data = self._zip(
                    {
                        f"{base}.{name}.ipynb": self._encode(document)
                        for name, document in both.items()
                    },
                    root_frame_id,
                    branch_id,
                )
                filename = f"{base}.notebooks.zip"
                content_type = "application/zip"
            else:
                document = next(iter(both.values()), None) or self.clean_notebook(
                    root_frame_id, language, branch_id=branch_id, title=title
                )
                data = self._encode(document)
                suffix = document["metadata"]["openai4s"]["language"]
                filename = f"{human}.ipynb" if human else f"{stem}.{suffix}.ipynb"
                content_type = "application/x-ipynb+json"
        elif language is not None and str(language).lower() == "markdown":
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

    def clean_notebook(
        self,
        root_frame_id: str,
        language: str | None = None,
        *,
        branch_id: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        """A notebook to read and re-run, not the execution log.

        The plain export serialised every attempt: failed cells, environment
        probes and ``host.*`` calls that only make sense inside OpenAI4S, with
        no figures and none of the narration that explains the analysis. This
        one keeps the scientific cells that ran cleanly, embeds the figures
        each produced, renumbers them, interleaves the agent's narration as
        Markdown in the order it was written, and says what it left out.
        """
        selected = branch_id or root_frame_id
        cells = self._branch_cells(root_frame_id, selected)
        if language is None or str(language).lower() == "auto":
            languages = {_cell_language(cell) for cell in cells}
            language = "python" if "python" in languages or not languages else "r"
        language = str(language).lower()
        if language not in _LANGUAGE:
            raise ValueError("notebook language must be python or r")

        kept: list[Mapping[str, Any]] = []
        failed = diagnostic = 0
        for cell in cells:
            if _cell_language(cell) != language:
                continue
            code = str(cell.get("code") or "")
            if is_completion_only_cell(code, language):
                continue  # the completion RPC, not analysis
            if _cell_failed(cell):
                failed += 1
            elif (
                cell.get("visibility") not in (None, "", "scientific")
                and not cell.get("pin")
            ) or (language == "python" and _is_diagnostic_cell(code)):
                diagnostic += 1
            else:
                kept.append(cell)

        bindings = self._output_bindings(root_frame_id, kept)
        narration = [
            message
            for message in self.store.list_branch_messages(
                root_frame_id, branch_id=selected, limit=None
            )
            or ()
            if isinstance(message, Mapping)
            and message.get("role") == "assistant"
            and str(message.get("content") or "").strip()
            and not is_progress_narration(message.get("content"))
        ]
        timeline: list[tuple[int, int, str, Mapping[str, Any]]] = [
            (_created_at(message), 0, "markdown", message) for message in narration
        ] + [(_created_at(cell), 1, "code", cell) for cell in kept]
        timeline.sort(key=lambda item: (item[0], item[1]))

        zh = (
            response_language(" ".join(str(m.get("content") or "") for m in narration))
            == "zh"
        )
        header = _clean_header(title, language, failed, diagnostic, zh=zh)
        needs_stub = language == "python" and any(
            _HOST_CALL.search(str(cell.get("code") or "")) for cell in kept
        )
        if needs_stub:
            header += "\n\n" + _HOST_STUB_NOTE["zh" if zh else "en"]
        out: list[dict[str, Any]] = [self._markdown_cell("header", header)]
        if needs_stub:
            out.append(
                {
                    "cell_type": "code",
                    "execution_count": None,
                    "id": self._jupyter_id("openai4s-host-stub"),
                    "metadata": {"openai4s": {"stub": "host"}},
                    "outputs": [],
                    "source": self._lines(_HOST_STUB_SOURCE),
                }
            )
        budget = {"bytes": 0}
        count = 0
        for _at, _order, kind, item in timeline:
            if kind == "markdown":
                out.append(
                    self._markdown_cell(
                        str(item.get("message_id") or len(out)),
                        _portable_markdown(str(item.get("content") or "")),
                    )
                )
                continue
            count += 1
            out.append(
                self._clean_cell(
                    item,
                    count,
                    bindings.get(str(item.get("producing_cell_id") or ""), []),
                    budget,
                )
            )
        document = self.notebook(root_frame_id, language, branch_id=branch_id)
        document["cells"] = out
        document["metadata"]["openai4s"].update(
            {
                "variant": "clean",
                "left_out": {"failed_cells": failed, "diagnostic_cells": diagnostic},
                "raw_export": "language=bundle (the execution log, every cell)",
            }
        )
        return document

    def _clean_documents(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None = None,
        title: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        """The clean notebook of each language that kept at least one cell."""

        present = {
            _cell_language(cell)
            for cell in self._branch_cells(root_frame_id, branch_id or root_frame_id)
        }
        documents: dict[str, dict[str, Any]] = {}
        for name in ("python", "r"):
            if name not in present:
                continue
            document = self.clean_notebook(
                root_frame_id, name, branch_id=branch_id, title=title
            )
            if any(
                cell["cell_type"] == "code"
                and cell["metadata"].get("openai4s", {}).get("producing_cell_id")
                for cell in document["cells"]
            ):
                documents[name] = document
        return documents

    def _output_bindings(
        self, root_frame_id: str, cells: list[Mapping[str, Any]]
    ) -> dict[str, list[dict[str, str]]]:
        from openai4s.server.execution_sources import output_artifact_bindings

        cell_ids = {
            str(cell.get("producing_cell_id"))
            for cell in cells
            if cell.get("producing_cell_id")
        }
        if not cell_ids:
            return {}
        return output_artifact_bindings(
            self.store,
            root_frame_id,
            producer_frame_id=root_frame_id,
            cell_ids=cell_ids,
            newest_only=True,
        )

    def _clean_cell(
        self,
        cell: Mapping[str, Any],
        count: int,
        bindings: list[dict[str, str]],
        budget: dict[str, int],
    ) -> dict[str, Any]:
        outputs: list[dict[str, Any]] = []
        for name in ("stdout", "stderr"):
            text = str(cell.get(name) or "")
            if text:
                outputs.append(
                    {"name": name, "output_type": "stream", "text": self._lines(text)}
                )
        embedded: set[str] = set()
        for binding in bindings:
            filename = str(binding.get("filename") or "")
            mime = _IMAGE_TYPES.get(_suffix(filename))
            version_id = str(binding.get("version_id") or "")
            if mime is None or not version_id or self._read_version_bytes is None:
                continue
            data = self._read_version_bytes(version_id)
            if (
                not data
                or len(data) > _MAX_EMBED_BYTES
                or budget["bytes"] + len(data) > _MAX_EMBED_TOTAL
            ):
                continue
            budget["bytes"] += len(data)
            payload = (
                data.decode("utf-8", errors="replace")
                if mime == "image/svg+xml"
                else base64.b64encode(data).decode("ascii")
            )
            outputs.append(
                {
                    "data": {mime: payload, "text/plain": [f"<{filename}>"]},
                    "metadata": {
                        "openai4s": {"filename": filename, "version_id": version_id}
                    },
                    "output_type": "display_data",
                }
            )
            embedded.add(filename)
        others = [
            str(item)
            for item in (
                list(cell.get("figures") or ())
                + [str(b.get("filename") or "") for b in bindings]
            )
            if item and str(item) not in embedded
        ]
        if others:
            outputs.append(
                {
                    "name": "stdout",
                    "output_type": "stream",
                    "text": [
                        "[OpenAI4S artifacts] " + ", ".join(dict.fromkeys(others))
                    ],
                }
            )
        source = str(cell.get("code") or "")
        uses = sorted(set(_HOST_CALL.findall(source)))
        if uses:
            source = (
                "# Needs an OpenAI4S kernel: "
                + ", ".join(f"host.{name}" for name in uses)
                + " only exist there.\n"
                + source
            )
        if "background" in str(cell.get("kernel_id") or ""):
            source = _BACKGROUND_NOTE + source
        cell_id = str(cell.get("producing_cell_id") or f"clean-{count}")
        return {
            "cell_type": "code",
            "execution_count": count,
            "id": self._jupyter_id(cell_id),
            "metadata": {
                "openai4s": {
                    "producing_cell_id": cell_id,
                    "cell_index": cell.get("cell_index"),
                    "state_revision": cell.get("state_revision"),
                    "generation_id": cell.get("generation_id"),
                }
            },
            "outputs": outputs,
            "source": self._lines(source),
        }

    @classmethod
    def _markdown_cell(cls, key: str, text: str) -> dict[str, Any]:
        return {
            "cell_type": "markdown",
            "id": cls._jupyter_id("md-" + key),
            "metadata": {},
            "source": cls._lines(text.rstrip("\n") + "\n"),
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


def _human_stem(title: str | None) -> str:
    """A file name a person recognises: the session title, made path-safe."""

    text = " ".join(str(title or "").split())
    safe = "".join(ch if ch.isalnum() or ch in " -_.()" else "_" for ch in text)
    return safe.strip(" ._")[:120]


__all__ = ["CellStore", "NotebookExportService"]
