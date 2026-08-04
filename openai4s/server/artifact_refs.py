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

from openai4s.server.errors import record_diagnostic

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

#: ...and how much they may add *together*, which is the bound that was
#: missing. Eight references at 200,000 bytes each is 1,600,000 characters —
#: roughly 400,000 tokens against a 262,144-token window, so one message could
#: exceed the whole context by half again, and be eight times the 200,000-char
#: cap on the message a person actually types. A count bounds how many; a
#: per-item cap bounds each; neither bounds the product, which is the same
#: mistake `memory_budget` was written for.
MAX_TOTAL_REF_BYTES = 400_000

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


class ArtifactRef(dict):
    """One reference whose bytes actually reached the prompt, as a record.

    The reference used to exist only as a token inside the message text, so
    every reader -- reopen, branch, export, audit -- had to re-parse a string,
    and even then recovered only the version id the *user* named. That is the
    wrong one whenever a sibling session's file was brought in: the model read
    the local copy, and nothing said so. Six fields, because six is what it
    takes to answer "what did the model actually read":

    ``artifact_id`` / ``version_id`` / ``sha256``  -- the version referenced.
                            ``sha256`` is that version's checksum, which is the
                            bytes that were sent only when ``truncated`` is
                            absent. This line used to read "the bytes that were
                            sent" unconditionally, which was false for any file
                            over the inline budget: the block read as the whole
                            file and the digest asserted the model had seen it.
    ``sent_bytes`` / ``truncated`` -- how much actually reached the prompt.
    ``display_name``     -- what the user called it, which need not be the
                            filename the copy landed under.
    ``source_session``   -- the session the reference named.
    ``materialized_target`` -- the local version a cross-session reference was
                            copied into, or ``None`` when nothing was copied.
    """


def _problem(ref: str, code: str, message: str) -> RefProblem:
    return RefProblem(ref=ref, code=code, message=message)


def _artifact_ref(
    *,
    metadata: dict,
    display_name: str,
    source_session: str,
    materialized_target: str | None,
    sent_bytes: int = 0,
    truncated: bool = False,
) -> ArtifactRef:
    ref = ArtifactRef(
        artifact_id=str(metadata.get("artifact_id") or ""),
        version_id=str(metadata.get("version_id") or ""),
        sha256=str(metadata.get("checksum") or ""),
        display_name=display_name,
        source_session=source_session,
        materialized_target=materialized_target,
        sent_bytes=int(sent_bytes),
    )
    # Present only when true, so an untruncated record keeps the shape it had.
    if truncated:
        ref["truncated"] = True
    return ref


def _is_binary(filename: str) -> bool:
    return Path(str(filename or "")).suffix.lower() in BINARY_SUFFIXES


def _existing_local_copy(
    store: Any, source_version_id: str, source_meta: dict, root_frame_id: str
) -> dict | None:
    """The copy of this exact version this session already holds, if any.

    Without this, the second turn that names the same sibling-session file
    fails outright: materialisation refuses when the live filename is already
    taken, and the first turn is what took it. The user got
    `materialise_failed` and the model got nothing -- for a reference that had
    worked one turn earlier.

    The lineage edge is the durable record of a materialisation, so it is what
    gets asked. The checksum equality is the other half of the question: an
    edge out of this version also exists for anything *derived* from it, and a
    derivative is not the file the reference names.
    """
    for candidate in store.lineage_edges_for(source_version_id, "down"):
        metadata = store.version_meta(str(candidate or ""))
        if metadata is None:
            continue
        if metadata.get("checksum") != source_meta.get("checksum"):
            continue
        parent = store.get_artifact(str(metadata.get("artifact_id") or "")) or {}
        if parent.get("root_frame_id") == root_frame_id:
            return metadata
    return None


def resolve_message_refs(
    text: str,
    *,
    store: Any,
    root_frame_id: str,
    project_id: str,
    materialise: Callable[[str, str], dict] | None = None,
    on_resolved: Callable[[dict], None] | None = None,
    limit: int = MAX_REFS,
) -> tuple[str, list[dict]]:
    """Return the prompt with referenced content appended, plus what failed.

    The second element is the point. A reference that cannot be resolved is
    reported to the caller so the user can be told; the previous behaviour was
    to drop it and leave them asking about a file the model never received.

    ``materialise`` is injected rather than imported so this module stays
    testable without a dispatcher, and so the caller decides whether a
    cross-session reference is allowed to write at all.

    ``on_resolved`` receives one :class:`ArtifactRef` per reference whose bytes
    actually entered the prompt. It is a callback rather than a third return
    value on purpose: every caller in the tree unpacks two, and widening the
    tuple to carry an optional record is a breaking change for a field most
    callers do not want.
    """
    problems: list[dict] = []
    blocks: list[str] = []
    seen: set[str] = set()

    def _record(ref: dict | None, token: str) -> None:
        """Announce a reference only once its bytes are in the prompt.

        Recording one that lost the budget race below would make the durable
        record claim the model was handed a file it never saw -- provenance
        that is wrong rather than absent, which is worse because it is
        believed.

        A partial send is named here, beside `ref_budget_exhausted`, and in
        this one place rather than in each `_resolve_*` branch -- the two
        spellings of a reference are two ways of writing the same request and
        must not be able to drift into telling the user different things.
        """
        if ref is None:
            return
        if ref.get("truncated"):
            sent = int(ref.get("sent_bytes") or 0)
            meta = store.version_meta(str(ref.get("version_id") or "")) or {}
            size = int(meta.get("size_bytes") or 0)
            whole = f"{size:,}" if size else "all its"
            problems.append(
                _problem(
                    token,
                    "ref_truncated",
                    f"{ref.get('display_name') or token} was sent only in "
                    f"part: the first {sent:,} of {whole} bytes. Ask the "
                    "agent to read the rest in a cell.",
                )
            )
        if on_resolved is not None:
            on_resolved(ref)

    pinned = [(name, version) for name, version in PINNED_REF.findall(text or "")]
    # The legacy spelling only counts where a pinned one did not already claim
    # that name, so `@a.csv#v-1` is not also read as a bare `@a.csv`.
    pinned_names = {name for name, _ in pinned}
    legacy = [n for n in LEGACY_REF.findall(text or "") if n not in pinned_names]

    spent = 0

    def _afford(block: str) -> bool:
        """Charge a resolved block against the shared budget."""
        nonlocal spent
        if spent + len(block) > MAX_TOTAL_REF_BYTES:
            return False
        spent += len(block)
        return True

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
        block, problem, ref = _resolve_pinned(
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
            if _afford(block):
                blocks.append(block)
                _record(ref, f"{name}#{version_id}")
            else:
                # Named, not silently dropped. A reference the user inserted
                # and the model never received is the failure this module was
                # written to stop; running out of budget is a different reason
                # for it, not an exemption from saying so.
                problems.append(
                    _problem(
                        f"{name}#{version_id}",
                        "ref_budget_exhausted",
                        f"{name} was not sent: the referenced files together "
                        f"exceed this turn's {MAX_TOTAL_REF_BYTES:,}-character "
                        "budget. Reference fewer files, or ask the agent to "
                        "read this one in a cell.",
                    )
                )

    for name in legacy:
        if len(seen) >= limit:
            break
        seen.add(name)
        block, problem, ref = _resolve_legacy(
            name, store=store, root_frame_id=root_frame_id
        )
        if problem is not None:
            problems.append(problem)
        elif block:
            if _afford(block):
                blocks.append(block)
                _record(ref, name)
            else:
                problems.append(
                    _problem(
                        name,
                        "ref_budget_exhausted",
                        f"{name} was not sent: the referenced files together "
                        f"exceed this turn's {MAX_TOTAL_REF_BYTES:,}-character "
                        "budget.",
                    )
                )

    if not blocks:
        return text, problems
    body = "\n\n".join(blocks)
    return (
        f"{text}\n\n---\n(附:被引用的文件内容 / referenced files)\n\n{body}",
        problems,
    )


def _read_snapshot(
    metadata: dict, name: str
) -> tuple[str | None, RefProblem | None, int, bool]:
    """The frozen bytes of one version, as text, how many, whether that is all.

    Reads `snapshot_path`, never the live path. The live file is whatever the
    latest cell left there; the snapshot is what this version *is*, which is
    the whole reason a pinned reference is worth having.

    The last two elements are the part that was missing. Bounding the read
    fixed what the daemon allocated and said nothing about what was handed on:
    a file over the budget arrived as a fenced block that reads as the whole
    thing, in a record whose own docstring calls its `sha256` "the bytes that
    were sent". A truncation nobody is told about is a statement that is wrong
    rather than absent.

    The count is of the *file's* bytes, not of the returned string: the caller
    appends nothing, but this function does -- and a `sent_bytes` that silently
    included the daemon's own truncation notice would answer "how much of the
    file did the model get" with a number larger than the budget.
    """
    snapshot = metadata.get("snapshot_path") or ""
    if not snapshot or not Path(str(snapshot)).is_file():
        return (
            None,
            _problem(
                name,
                "no_frozen_bytes",
                f"{name} exists but its frozen bytes are missing, so the exact "
                "version referenced cannot be sent",
            ),
            0,
            False,
        )
    if _is_binary(metadata.get("filename") or name):
        return (
            None,
            _problem(
                name,
                "not_text",
                f"{name} is a binary file; reference it by name and let the agent "
                "open it in a cell rather than pasting it into the prompt",
            ),
            0,
            False,
        )
    try:
        # Bounded at the read, not after it. `read_bytes()[:MAX_REF_BYTES]`
        # allocates the whole file first and then discards most of it, so the
        # cap described what the model saw while the daemon still paid for
        # every byte -- and this runs on a file another session chose the size
        # of. Plan section 7.4: budgets are enforced during read or
        # allocation, never on an already-materialised buffer.
        with Path(str(snapshot)).open("rb") as handle:
            # One byte past the budget, discarded. It is the only way to tell
            # "the file ended exactly at the cap" from "there is more", without
            # reading the more.
            raw = handle.read(MAX_REF_BYTES + 1)
        cut = len(raw) > MAX_REF_BYTES
        raw = raw[:MAX_REF_BYTES]
    except OSError as error:
        # The card is rendered in the composer, so it carries the daemon's own
        # words. `strerror` here quotes the snapshot path it could not read --
        # absolute, under the data directory, and with it the username.
        record_diagnostic(error, surface="artifact_refs:read")
        return (
            None,
            _problem(name, "unreadable", f"{name} could not be read"),
            0,
            False,
        )
    text = raw.decode("utf-8", errors="replace")
    # A high replacement-character density means this was not text after all --
    # a suffix allowlist cannot know about every binary format.
    if text.count("�") > max(16, len(text) // 20):
        return (
            None,
            _problem(
                name,
                "not_text",
                f"{name} does not decode as text; reference it by name instead",
            ),
            0,
            False,
        )
    if cut:
        # In band, inside the fence, because the model reads the fence and not
        # the envelope around it. `agent/runtime.py::_budgeted` states an
        # omission the same way for the same reason.
        size = int(metadata.get("size_bytes") or 0)
        total = f"{size:,} bytes" if size else "more"
        text += (
            f"\n\n[... truncated: the first {len(raw):,} bytes of {total}. "
            "Open the file in a cell to read the rest.]"
        )
    return text, None, len(raw), cut


def _resolve_pinned(
    name: str,
    version_id: str,
    *,
    store: Any,
    root_frame_id: str,
    project_id: str,
    materialise: Callable[[str, str], dict] | None,
) -> tuple[str | None, RefProblem | None, ArtifactRef | None]:
    ref = f"{name}#{version_id}"
    unknown = _problem(
        ref, "not_found", f"no artifact version {version_id} is available here"
    )
    metadata = store.version_meta(version_id)
    if metadata is None:
        return None, unknown, None
    parent = store.get_artifact(str(metadata.get("artifact_id") or "")) or {}
    # Another project's version answers exactly as an absent one does. A
    # distinct refusal would confirm it exists, and version ids are short.
    if parent.get("project_id") != project_id:
        return None, unknown, None

    source_session = str(parent.get("root_frame_id") or "")
    materialized_target: str | None = None
    if source_session != root_frame_id:
        # D3: bring it in rather than reading it in place, and only now --
        # at send -- so an inserted-then-deleted chip leaves nothing behind.
        if materialise is None:
            return (
                None,
                _problem(
                    ref,
                    "cross_session_not_allowed",
                    f"{name} belongs to another session and cannot be brought in here",
                ),
                None,
            )
        # Ask first whether this session already holds this exact version.
        # Materialisation refuses when the live filename is taken, and after
        # one successful reference *it* is what took it -- so naming the same
        # sibling file a second time used to come back `materialise_failed`
        # for a reference that had worked one turn earlier.
        local = _existing_local_copy(store, version_id, dict(metadata), root_frame_id)
        if local is None:
            try:
                brought = materialise(version_id, name)
            except FileExistsError:
                # A *different* artifact already owns this filename here -- two
                # files may share a name and still be different files, which is
                # the whole reason a reference is pinned to a version. Land the
                # copy under a version-scoped name rather than refusing the
                # reference or overwriting the session's own file.
                stem = Path(name)
                alternative = f"{stem.stem} ({version_id[2:8]}){stem.suffix}"
                try:
                    brought = materialise(version_id, alternative)
                except Exception as error:  # noqa: BLE001 - reported, not raised
                    record_diagnostic(error, surface="artifact_refs:materialise")
                    return (
                        None,
                        _problem(
                            ref,
                            "materialise_failed",
                            f"{name} could not be brought into this session",
                        ),
                        None,
                    )
            except Exception as error:  # noqa: BLE001 - reported, not raised
                # A bare `Exception`: whatever `materialise` raises is by
                # definition something nobody wrote a message for, so its text
                # is the least safe thing on this path and the most tempting to
                # forward.
                record_diagnostic(error, surface="artifact_refs:materialise")
                return (
                    None,
                    _problem(
                        ref,
                        "materialise_failed",
                        f"{name} could not be brought into this session",
                    ),
                    None,
                )
            # A refusal is a dict, not an exception. `HostDispatcher` answers a
            # denied permission with the single-key soft-fail shape rather than
            # raising, so `except Exception` above never sees it and the code
            # below would have read on with `metadata` still pointing at the
            # foreign version -- turning "denied" into "served anyway".
            if isinstance(brought, dict) and set(brought.keys()) == {"error"}:
                return (
                    None,
                    _problem(
                        ref,
                        "cross_session_denied",
                        f"{name} belongs to another session and was not "
                        "approved for copying into this one",
                    ),
                    None,
                )
            local = store.version_meta(str(brought.get("version_id") or ""))
        if local is None:
            # Fail closed. This used to fall through, leaving `metadata` as the
            # *foreign* version's row, so a materialise that produced no local
            # version still had its snapshot read and inlined from the other
            # session's file.
            return (
                None,
                _problem(
                    ref,
                    "materialise_failed",
                    f"{name} could not be brought into this session",
                ),
                None,
            )
        metadata = local
        materialized_target = str(local.get("version_id") or "") or None

    body, problem, sent, truncated = _read_snapshot(dict(metadata), name)
    if problem is not None:
        return None, problem, None
    # The header names the version the user pinned *and*, when they differ, the
    # local one the model actually read. Naming only the first was a citation
    # to a version this session cannot resolve.
    where = f"version {version_id}"
    if materialized_target and materialized_target != version_id:
        where += f", copied into this session as {materialized_target}"
    return (
        f"### Referenced file: {name} ({where})\n```\n{body}\n```",
        None,
        _artifact_ref(
            metadata=dict(metadata),
            display_name=name,
            source_session=source_session,
            materialized_target=materialized_target,
            sent_bytes=sent,
            truncated=truncated,
        ),
    )


def _resolve_legacy(
    name: str, *, store: Any, root_frame_id: str
) -> tuple[str | None, RefProblem | None, ArtifactRef | None]:
    """The unpinned spelling: this session only, and it says it is unpinned.

    Kept for one minor release. It resolves through the artifact's *latest*
    version rather than its live path, so at least the bytes sent are a version
    that exists rather than whatever a concurrent cell happened to leave on
    disk mid-write.
    """
    row = store.artifact_by_filename(name, root_frame_id, strict=True)
    if not row:
        return (
            None,
            _problem(
                name,
                "not_found",
                f"no artifact named {name} in this session; if it belongs to "
                "another session, insert it from the Files panel so it is pinned",
            ),
            None,
        )
    artifact = store.get_artifact(str(row.get("artifact_id") or "")) or {}
    latest = str(artifact.get("latest_version_id") or "")
    metadata = store.version_meta(latest) if latest else None
    if metadata is None:
        return (
            None,
            _problem(name, "not_found", f"{name} has no readable version"),
            None,
        )
    body, problem, sent, truncated = _read_snapshot(dict(metadata), name)
    if problem is not None:
        return None, problem, None
    return (
        f"### Referenced file: {name} (unpinned — latest version {latest}; "
        "insert it from the Files panel to pin it)\n```\n" + body + "\n```",
        None,
        # Recorded even though it is unpinned: the record is what the model was
        # given, and "the latest version at send time" is a fact worth freezing
        # precisely because the token in the text does not carry it.
        _artifact_ref(
            metadata=dict(metadata),
            display_name=name,
            source_session=root_frame_id,
            materialized_target=None,
            sent_bytes=sent,
            truncated=truncated,
        ),
    )


__all__ = [
    "ArtifactRef",
    "BINARY_SUFFIXES",
    "LEGACY_REF",
    "MAX_REFS",
    "MAX_REF_BYTES",
    "MAX_TOTAL_REF_BYTES",
    "PINNED_REF",
    "resolve_message_refs",
]
