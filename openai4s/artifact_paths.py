"""Shared visibility rules for files the Artifact capture pipeline records."""

from __future__ import annotations

import os
import re
from pathlib import Path

JUNK_DIR_SEGMENTS = frozenset({"__pycache__", "node_modules", "site-packages", "venv"})

#: The name the gateway gives a large tool result it moved out of the model's
#: context (`_archive_context_output`). It is stored as an Artifact version so
#: the spill stays exact and addressable, but it is nobody's result.
_CONTEXT_SPILL = re.compile(r"context-output-[0-9a-f]{16}\.json")
#: Suffixes and folders of files that record how a result was made.
_PROCESS_SUFFIXES = frozenset(
    {".py", ".r", ".sh", ".bash", ".ipynb", ".log", ".pyc", ".tmp", ".bak", ".rds"}
)
_PROCESS_DIRS = ("project-inputs/", "inputs/", "__pycache__/", ".openai4s")


_ROLES = frozenset({"internal", "process", "deliverable"})


def artifact_role(filename: str | None, marked: str | None = None) -> str:
    """``internal``, ``process`` or ``deliverable`` -- decided here and only here.

    ``internal`` is the platform's own bookkeeping (context spill files): never
    listed or counted, still readable by id and version for lineage. ``process``
    is how the result was made -- scripts (a scratch script written four times
    is four versions of one), logs, copies of inputs: kept and linked, folded
    away from the deliverables and left out of their count. Everything else is
    a ``deliverable``. The record itself is never changed by this answer.

    ``marked`` is a role the server recorded for the artifact itself
    (`Store.artifact_role_marks`: a plan's JSON is ``process``) and wins over
    anything the filename suggests.
    """
    if marked in _ROLES:
        return str(marked)
    lower = str(filename or "").lower().replace("\\", "/")
    slash = lower.rfind("/")
    if _CONTEXT_SPILL.fullmatch(lower[slash + 1 :]):
        return "internal"
    dot = lower.rfind(".")
    if dot > slash and lower[dot:] in _PROCESS_SUFFIXES:
        return "process"
    if lower.startswith(_PROCESS_DIRS) or any(f"/{d}" in lower for d in _PROCESS_DIRS):
        return "process"
    return "deliverable"


def ignored_artifact_path(path: Path) -> bool:
    if any(part.startswith(".") for part in path.parts):
        return True
    if any(
        part in JUNK_DIR_SEGMENTS or part.endswith((".egg-info", ".dist-info"))
        for part in path.parts
    ):
        return True
    return path.name.endswith((".pyc", ".pyo"))


def require_capturable_destination(workspace: Path, target: Path) -> None:
    """Refuse a destination the normal workspace sweep deliberately omits.

    This is a capture-visibility check, not a filesystem authorization check.
    Callers still acquire the existing pinned workspace parent for I/O.
    """
    try:
        relative = target.relative_to(workspace)
    except ValueError:
        raise ValueError("dataset destination must be inside its workspace") from None
    if not relative.parts:
        raise ValueError("dataset destination must name a file within the workspace")
    if ignored_artifact_path(relative):
        raise ValueError("dataset destination is excluded from Artifact capture")
    _require_exact_existing_spelling(workspace, relative)
    parent = target.parent
    while True:
        try:
            (parent / ".git").lstat()
        except FileNotFoundError:
            pass
        except OSError as error:
            raise ValueError(
                "dataset destination capture visibility is unavailable"
            ) from error
        else:
            raise ValueError(
                "dataset destination is inside a repository excluded from Artifact capture"
            )
        if parent == workspace:
            return
        parent = parent.parent


def _require_exact_existing_spelling(workspace: Path, relative: Path) -> None:
    """Refuse a name that reaches an existing entry spelled differently.

    The receipt names the file as the caller spelled it, while the capture
    sweep names it as the directory stores it. On a case- or
    normalization-insensitive filesystem (macOS and Windows defaults)
    ``DATASETS/x.csv`` lands in an existing ``datasets/`` and the two never
    match, so the import would fail only after publishing the file. A name
    that does not exist yet is created with the requested spelling.
    """
    current = workspace
    for part in relative.parts:
        try:
            names = os.listdir(current)
        except FileNotFoundError:
            return
        except OSError as error:
            raise ValueError(
                "dataset destination capture visibility is unavailable"
            ) from error
        if part not in names:
            try:
                os.lstat(current / part)
            except FileNotFoundError:
                return
            except OSError as error:
                raise ValueError(
                    "dataset destination capture visibility is unavailable"
                ) from error
            raise ValueError(
                "dataset destination differs from an existing workspace entry "
                "only by letter case or Unicode normalization; use its exact name"
            )
        current = current / part
