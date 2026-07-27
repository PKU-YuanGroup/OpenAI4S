"""Version-pinned artifact references in a user message.

`@results.csv` resolved to the artifact's *live path* and injected whatever
bytes were there at send time. Three things were wrong with that, and only the
first is obvious:

1. It is not pinned. A later cell overwrites `results.csv`, and the same
   reference in the same message now means different bytes -- so a replayed
   session shows a prompt whose content nobody can reconstruct. This is the
   same failure D2 removed for model selection, in a different column.
2. It failed silently. An unresolvable name hit `continue`, the block was
   simply absent from the prompt, and the user was told nothing. They asked a
   question about a file the model never saw.
3. It decoded every artifact as UTF-8 with `errors="replace"`, so referencing a
   `.npz` or a PDF injected a wall of U+FFFD that reads, to a model, as
   corrupted text rather than as "this is not text".

A pinned reference is written `@name#v-<version_id>`. Self-contained on purpose:
the composer is a plain textarea, so any out-of-band ref list would desync the
moment somebody edits the words around it, and the text has to be the single
source of truth.

Cross-session references in the same project are **materialised** rather than
read in place (D3), and only when the turn is actually sent -- inserting a chip
and deleting it again must not leave an unreferenced Artifact and a lineage
edge behind in the session.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

#: `@results.csv#v-abc123def456` -- a name a human can read plus the version
#: that fixes what it means.
PINNED_REF = re.compile(r"(?:^|\s)@([\w./-]+\.\w+)#(v-[0-9a-zA-Z]{6,})")

#: The unpinned spelling, kept working for one minor release. It resolves
#: inside the calling session only, exactly as before -- widening it to the
#: project would let a guessed filename pull in another session's file, which
#: is the thing the pinned form asks for explicitly and gets checked for.
# The trailing guard must reject a *word* character too, not just `#`.
# `(?!#v-)` alone was defeated by backtracking: `\w+` gave back the final
# character of "csv", the lookahead then saw "v" rather than "#", and
# `@a.csv#v-abc123` produced a phantom legacy reference to "a.cs".
LEGACY_REF = re.compile(r"(?:^|\s)@([\w./-]+\.\w+)(?![\w#])")

#: How many references one message may resolve, and how much of each is
#: injected. Both bound the *prompt*, which is the scarce resource here: a
#: reference is cheap to type and expensive to send.
MAX_REFS = 8
MAX_REF_BYTES = 200_000

#: Extensions whose bytes are not text. Injecting them as `errors="replace"`
#: produces a wall of U+FFFD that a model reads as corrupted text rather than
#: as "this is a binary file", which is a worse answer than saying so.
BINARY_SUFFIXES = frozenset(
    {
        ".npy",
        ".npz",
        ".pkl",
        ".pickle",
        ".h5",
        ".hdf5",
        ".parquet",
        ".feather",
        ".zip",
        ".gz",
        ".bz2",
        ".xz",
        ".tar",
        ".7z",
        ".rar",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".tiff",
        ".ico",
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".so",
        ".dylib",
        ".dll",
        ".exe",
        ".bin",
        ".dat",
        ".db",
        ".sqlite",
    }
)


class RefProblem(dict):
    """One reference that did not resolve, and why. A dict so it serialises."""


def _problem(ref: str, code: str, message: str) -> RefProblem:
    return RefProblem(ref=ref, code=code, message=message)


def _is_binary(filename: str) -> bool:
    return Path(str(filename or "")).suffix.lower() in BINARY_SUFFIXES


def resolve_message_refs(
    text: str,
    *,
    store: Any,
    root_frame_id: str,
    project_id: str,
    materialise: Callable[[str, str], dict] | None = None,
    limit: int = MAX_REFS,
) -> tuple[str, list[dict]]:
    """Return the prompt with referenced content appended, plus what failed.

    The second element is the point. A reference that cannot be resolved is
    reported to the caller so the user can be told; the previous behaviour was
    to drop it and leave them asking about a file the model never received.

    ``materialise`` is injected rather than imported so this module stays
    testable without a dispatcher, and so the caller decides whether a
    cross-session reference is allowed to write at all.
    """
    problems: list[dict] = []
    blocks: list[str] = []
    seen: set[str] = set()

    pinned = [(name, version) for name, version in PINNED_REF.findall(text or "")]
    # The legacy spelling only counts where a pinned one did not already claim
    # that name, so `@a.csv#v-1` is not also read as a bare `@a.csv`.
    pinned_names = {name for name, _ in pinned}
    legacy = [n for n in LEGACY_REF.findall(text or "") if n not in pinned_names]

    for name, version_id in pinned:
        if len(seen) >= limit:
            problems.append(
                _problem(
                    f"{name}#{version_id}",
                    "too_many_refs",
                    f"only the first {limit} references are sent",
                )
            )
            continue
        if version_id in seen:
            continue
        seen.add(version_id)
        block, problem = _resolve_pinned(
            name,
            version_id,
            store=store,
            root_frame_id=root_frame_id,
            project_id=project_id,
            materialise=materialise,
        )
        if problem is not None:
            problems.append(problem)
        elif block:
            blocks.append(block)

    for name in legacy:
        if len(seen) >= limit:
            break
        seen.add(name)
        block, problem = _resolve_legacy(name, store=store, root_frame_id=root_frame_id)
        if problem is not None:
            problems.append(problem)
        elif block:
            blocks.append(block)

    if not blocks:
        return text, problems
    body = "\n\n".join(blocks)
    return f"{text}\n\n---\n(附:被引用的文件内容 / referenced files)\n\n{body}", problems


def _read_snapshot(metadata: dict, name: str) -> tuple[str | None, RefProblem | None]:
    """The frozen bytes of one version, as text, or why not.

    Reads `snapshot_path`, never the live path. The live file is whatever the
    latest cell left there; the snapshot is what this version *is*, which is
    the whole reason a pinned reference is worth having.
    """
    snapshot = metadata.get("snapshot_path") or ""
    if not snapshot or not Path(str(snapshot)).is_file():
        return None, _problem(
            name,
            "no_frozen_bytes",
            f"{name} exists but its frozen bytes are missing, so the exact "
            "version referenced cannot be sent",
        )
    if _is_binary(metadata.get("filename") or name):
        return None, _problem(
            name,
            "not_text",
            f"{name} is a binary file; reference it by name and let the agent "
            "open it in a cell rather than pasting it into the prompt",
        )
    try:
        raw = Path(str(snapshot)).read_bytes()[:MAX_REF_BYTES]
    except OSError as error:
        return None, _problem(name, "unreadable", f"{name}: {error}")
    text = raw.decode("utf-8", errors="replace")
    # A high replacement-character density means this was not text after all --
    # a suffix allowlist cannot know about every binary format.
    if text.count("�") > max(16, len(text) // 20):
        return None, _problem(
            name,
            "not_text",
            f"{name} does not decode as text; reference it by name instead",
        )
    return text, None


def _resolve_pinned(
    name: str,
    version_id: str,
    *,
    store: Any,
    root_frame_id: str,
    project_id: str,
    materialise: Callable[[str, str], dict] | None,
) -> tuple[str | None, RefProblem | None]:
    ref = f"{name}#{version_id}"
    unknown = _problem(
        ref, "not_found", f"no artifact version {version_id} is available here"
    )
    metadata = store.version_meta(version_id)
    if metadata is None:
        return None, unknown
    parent = store.get_artifact(str(metadata.get("artifact_id") or "")) or {}
    # Another project's version answers exactly as an absent one does. A
    # distinct refusal would confirm it exists, and version ids are short.
    if parent.get("project_id") != project_id:
        return None, unknown

    if parent.get("root_frame_id") != root_frame_id:
        # D3: bring it in rather than reading it in place, and only now --
        # at send -- so an inserted-then-deleted chip leaves nothing behind.
        if materialise is None:
            return None, _problem(
                ref,
                "cross_session_not_allowed",
                f"{name} belongs to another session and cannot be brought in here",
            )
        try:
            brought = materialise(version_id, name)
        except Exception as error:  # noqa: BLE001 - reported, never raised at the user
            return None, _problem(ref, "materialise_failed", f"{name}: {error}")
        metadata = store.version_meta(str(brought.get("version_id") or "")) or metadata

    body, problem = _read_snapshot(dict(metadata), name)
    if problem is not None:
        return None, problem
    return f"### Referenced file: {name} (version {version_id})\n```\n{body}\n```", None


def _resolve_legacy(
    name: str, *, store: Any, root_frame_id: str
) -> tuple[str | None, RefProblem | None]:
    """The unpinned spelling: this session only, and it says it is unpinned.

    Kept for one minor release. It resolves through the artifact's *latest*
    version rather than its live path, so at least the bytes sent are a version
    that exists rather than whatever a concurrent cell happened to leave on
    disk mid-write.
    """
    row = store.artifact_by_filename(name, root_frame_id, strict=True)
    if not row:
        return None, _problem(
            name,
            "not_found",
            f"no artifact named {name} in this session; if it belongs to "
            "another session, insert it from the Files panel so it is pinned",
        )
    artifact = store.get_artifact(str(row.get("artifact_id") or "")) or {}
    latest = str(artifact.get("latest_version_id") or "")
    metadata = store.version_meta(latest) if latest else None
    if metadata is None:
        return None, _problem(name, "not_found", f"{name} has no readable version")
    body, problem = _read_snapshot(dict(metadata), name)
    if problem is not None:
        return None, problem
    return (
        f"### Referenced file: {name} (unpinned — latest version {latest}; "
        "insert it from the Files panel to pin it)\n```\n" + body + "\n```",
        None,
    )


__all__ = [
    "BINARY_SUFFIXES",
    "LEGACY_REF",
    "MAX_REFS",
    "MAX_REF_BYTES",
    "PINNED_REF",
    "resolve_message_refs",
]
