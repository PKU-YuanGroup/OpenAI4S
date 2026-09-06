"""Completion evidence for turns whose deliverable is source code.

The existing reconciliation is produce-file shaped: it asks whether a claimed
file exists. That is the right question for a figure and the wrong one for a
pipeline — a `.py` path that exists proves nothing about whether the module
imports, whether it was ever captured, or whether any test ran against it.

For ``reusable_pipeline`` and ``codebase_change`` (see
:mod:`openai4s.agent.task_modes`) the completion payload must therefore carry
four fields, and each is verified against something the run cannot author.
The owning loops stamp the dispatcher's binding mode only when the mode was
selected EXPLICITLY (the Web ``task_mode`` body field, ``openai4s run
--mode``); a mode merely detected from the request text drives the prompt
fragment and never reaches this module, so a classifier false positive cannot
refuse an honest completion. Fields volunteered on an unarmed turn ride the
completion envelope as ordinary, unverified output. The four fields, and what
each is verified against:

``source_files``
    Every file resolves inside the session workspace, passes the same
    secret/alias-aware read boundary as the Host file tools, matches its
    declared sha256, and matches the checksum of an artifact version at that
    exact workspace-relative path in the current session root. A basename (or
    same-project artifact from another session) is not evidence for a path.
``entry_points``
    Each file exists; a Python entry point must ``compile()`` from its own
    source bytes. **Compile only, never exec** — validating an entry point must
    not be a way to run arbitrary code inside the daemon process. Non-Python
    entries (``.R``/``.r`` and anything else) are existence-checked only,
    because the daemon has no honest way to parse them.
``architecture_summary``
    A non-empty prose statement of what each module owns.
``test_evidence``
    Each entry names a command and the ``producing_cell_id`` of the cell that
    ran it. The cell must be a real successful execution in the current
    user-turn/branch, and its action group must contain a successful synthetic
    ``bash`` audit receipt for the exact Host-authorized command digest. Merely
    printing passing-looking text, citing a different command, or replaying a
    prior turn's Cell cannot satisfy the contract.

``analysis_run`` is untouched: the fields stay optional and unvalidated, so
every existing completion keeps working byte for byte.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

from openai4s.bash_capability import command_digest, command_preserves_failure_status

#: The task modes whose completion must carry verified code evidence.
EVIDENCE_REQUIRED_MODES = frozenset({"reusable_pipeline", "codebase_change"})

#: The payload keys this module owns, in the order a refusal names them.
CODE_EVIDENCE_KEYS = (
    "source_files",
    "entry_points",
    "architecture_summary",
    "test_evidence",
)

#: Bytes read to hash or compile one declared file. A source file larger than
#: this is not a source file; refusing is honest, guessing is not.
MAX_SOURCE_BYTES = 4 * 1024 * 1024

_PYTHON_SUFFIXES = frozenset({".py", ".pyi"})

#: Failure signals read off the STORED stdout of the cell that ran the tests.
#: Deliberately narrow and anchored: "0 failed" and "no errors" must not match,
#: and a bare "error" in prose is not a test result.
_FAILURE_SIGNALS = (
    re.compile(r"Traceback \(most recent call last\)"),
    re.compile(r"\bFAILED\b"),
    re.compile(r"\bAssertionError\b"),
    re.compile(r"\b[1-9]\d* failed\b"),
    re.compile(r"\b[1-9]\d* errors?\b"),
    re.compile(r"\bfailures=[1-9]"),
    re.compile(r"\berrors=[1-9]"),
)


@dataclass(frozen=True)
class CodeEvidenceContext:
    """What the Host can check a code-evidence claim against.

    ``search_roots`` normally contains exactly the session workspace.
    ``source_reader`` is the Host file service's verified opener projected as a
    bounded bytes reader, so evidence cannot become a second route around its
    secret-name, alias, hardlink, and descriptor checks. ``artifact_checksums``
    maps exact normalized workspace-relative paths to current-root artifact
    checksums. ``cell_lookup`` reads one ``execution_log`` row by producing
    cell id, and ``frame_id`` scopes it: a real row from someone else's session
    is not this run's evidence. ``test_receipt`` proves that the same Cell's
    action group in the bound turn/branch recorded a successful Host-authorized
    execution of the exact command.

    ``has_cells`` is a lazy probe — "did this runtime record ANY cell for this
    run?" — consulted only when a named cell is missing, so the refusal can
    honestly distinguish "this run never executed that cell" from "this
    runtime records no cells at all". ``None`` means unknown and keeps the
    former wording.
    """

    search_roots: tuple[Path, ...] = ()
    # A ``default_factory``, not a shared ``MappingProxyType({})`` default:
    # CPython 3.11 rejects the latter at class creation ("mutable default
    # ... use default_factory") because a mappingproxy was unhashable there,
    # and that ValueError made the whole agent package unimportable on the
    # one supported interpreter the CI matrix does not run.
    artifact_checksums: Mapping[str, frozenset[str]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    source_reader: Callable[[str], tuple[Path, str, bytes] | None] | None = None
    test_receipt: Callable[[str, str], bool] | None = None
    turn_id: str | None = None
    branch_id: str | None = None
    # Compatibility projection for callers that display the old context. It is
    # never consulted for verification: basenames/ids are not path evidence.
    artifact_names: frozenset[str] = frozenset()
    cell_lookup: Callable[[str], Mapping[str, Any] | None] | None = None
    frame_id: str | None = None
    has_cells: Callable[[], bool] | None = None


def gather_code_evidence_context(
    store: Any,
    frame_id: str | None,
    search_roots: Sequence[Path] = (),
    *,
    file_service: Any | None = None,
    turn_id: str | None = None,
    branch_id: str | None = None,
) -> CodeEvidenceContext:
    """Build a context from the live Store. Store failures propagate.

    The caller decides what a broken store means. Swallowing the failure here
    and returning an empty context would turn "cannot verify" into "verified
    nothing", which is the fail-open this module exists to prevent.
    """

    roots = tuple(Path(root).resolve() for root in search_roots)
    workspace = roots[0] if roots else None
    scope = store.resolve_frame_scope(frame_id)
    root_frame_id = str(scope.get("root_frame_id") or frame_id or "") or None
    selected_branch = str(branch_id or root_frame_id or "") or None

    checksums: dict[str, set[str]] = {}
    names: set[str] = set()
    if root_frame_id and workspace is not None:
        for row in store.list_artifacts({"root_frame_id": root_frame_id}) or []:
            version_id = str(row.get("latest_version_id") or "")
            version = store.version_meta(version_id) if version_id else None
            if not isinstance(version, Mapping):
                continue
            key = _artifact_path_key(version.get("path"), workspace)
            checksum = str(version.get("checksum") or "").strip().lower()
            if key is None or not checksum:
                continue
            checksums.setdefault(key, set()).add(checksum)
            names.add(key.lower())

    source_reader = _source_reader(file_service, workspace)
    frame = root_frame_id

    def receipt(cell_id: str, command: str) -> bool:
        if not frame or not turn_id or not selected_branch:
            return False
        return bool(
            store.has_successful_bash_receipt(
                producing_cell_id=cell_id,
                command_sha256=command_digest(command),
                root_frame_id=frame,
                branch_id=selected_branch,
                turn_id=str(turn_id),
            )
        )

    return CodeEvidenceContext(
        search_roots=roots,
        artifact_checksums=MappingProxyType(
            {key: frozenset(value) for key, value in checksums.items()}
        ),
        source_reader=source_reader,
        test_receipt=receipt,
        turn_id=(str(turn_id) if turn_id else None),
        branch_id=selected_branch,
        artifact_names=frozenset(names),
        cell_lookup=store.cell_detail,
        frame_id=frame,
        # Lazy on purpose: paid only on the refusal path, where the answer
        # decides between two very different messages.
        has_cells=(lambda: bool(store.list_cells(frame))) if frame else None,
    )


def _artifact_path_key(value: Any, workspace: Path) -> str | None:
    """Return one exact canonical workspace-relative artifact path."""

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        raw = Path(value)
        target = (raw if raw.is_absolute() else workspace / raw).resolve()
        relative = target.relative_to(workspace.resolve())
    except (OSError, RuntimeError, ValueError):
        return None
    if not relative.parts:
        return None
    return relative.as_posix()


def _source_reader(
    file_service: Any | None, workspace: Path | None
) -> Callable[[str], tuple[Path, str, bytes] | None] | None:
    """Adapt the Host file service's verified descriptor into a byte reader."""

    opener = getattr(file_service, "open_verified_read", None)
    if not callable(opener) or workspace is None:
        return None

    def read(claim: str) -> tuple[Path, str, bytes] | None:
        # The opener performs lexical, resolved-alias, inode, and no-follow
        # checks before exposing the descriptor. Read that descriptor, never a
        # subsequently re-resolved pathname, so a rename cannot swap the file
        # between policy and hashing.
        with opener(claim) as opened:
            if int(opened.size_bytes) > MAX_SOURCE_BYTES:
                return None
            data = opened.handle.read(MAX_SOURCE_BYTES + 1)
            if len(data) > MAX_SOURCE_BYTES:
                return None
            relative = Path(str(opened.relative)).as_posix()
            return workspace / relative, relative, data

    return read


def requires_code_evidence(task_mode: Any) -> bool:
    """Whether ``task_mode`` makes the four fields required and verified."""

    return isinstance(task_mode, str) and task_mode in EVIDENCE_REQUIRED_MODES


def _resolve_in_roots(claim: str, roots: tuple[Path, ...]) -> Path | None:
    """The declared path, resolved inside an evidence root, or ``None``.

    Mirrors the produce-file confinement rule: an absolute path must land
    inside a root, and a relative path is joined to each root and re-confined
    so ``..`` segments cannot climb out of the one they were joined to.

    ``None`` means *unconfinable* — no root can own this claim — which is a
    different refusal from "confined but absent". A confined path that does not
    exist is returned so the caller can say so; conflating the two would tell
    a model that its own missing file lives outside the workspace.
    """

    if not claim:
        return None
    confined: Path | None = None
    try:
        if os.path.isabs(claim):
            candidate = Path(claim).resolve()
            for root in roots:
                try:
                    candidate.relative_to(root.resolve())
                    return candidate
                except (OSError, ValueError, RuntimeError):
                    continue
            return None
        for root in roots:
            candidate = (root / claim).resolve()
            try:
                candidate.relative_to(root.resolve())
            except (OSError, ValueError, RuntimeError):
                continue
            if candidate.exists():
                return candidate
            if confined is None:
                confined = candidate
    except (OSError, ValueError, RuntimeError):
        return None
    return confined


def _read_source(path: Path) -> bytes | None:
    try:
        if not path.is_file() or path.stat().st_size > MAX_SOURCE_BYTES:
            return None
        return path.read_bytes()
    except (OSError, ValueError):
        return None


def _read_claim(
    claim: str, context: CodeEvidenceContext
) -> tuple[Path, str, bytes] | None:
    if context.source_reader is not None:
        try:
            return context.source_reader(claim)
        except Exception:  # noqa: BLE001 - an unsafe/unreadable path is no evidence
            return None
    resolved = _resolve_in_roots(claim, context.search_roots)
    if resolved is None:
        return None
    data = _read_source(resolved)
    if data is None:
        return None
    relative: str | None = None
    for root in context.search_roots:
        try:
            relative = resolved.relative_to(root.resolve()).as_posix()
            break
        except (OSError, RuntimeError, ValueError):
            continue
    if relative is None:
        return None
    return resolved, relative, data


def _entries(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _check_source_files(
    value: Any,
    context: CodeEvidenceContext,
    problems: list[str],
    verified_files: dict[str, str] | None = None,
) -> None:
    entries = _entries(value)
    if not entries:
        problems.append("'source_files' names no file this run saved")
        return
    for item in entries:
        if isinstance(item, str):
            item = {"path": item}
        if not isinstance(item, Mapping):
            problems.append(f"'source_files' entry {item!r} is not a {{path, sha256}}")
            continue
        claim = str(item.get("path") or "")
        read = _read_claim(claim, context)
        if read is None:
            problems.append(
                f"source file {claim!r} does not exist or is unavailable inside "
                "this session's workspace (unreadable, unsafe, or outside the root)"
            )
            continue
        _resolved, relative, data = read
        actual = hashlib.sha256(data).hexdigest()
        declared = item.get("sha256")
        if isinstance(declared, str) and declared.strip():
            if actual != declared.strip().lower():
                problems.append(
                    f"source file {claim!r} declares sha256 that does not match "
                    "its current bytes (declared sha256 mismatch)"
                )
                continue
        captured = context.artifact_checksums.get(relative, frozenset())
        if actual not in captured:
            problems.append(
                f"source file {claim!r} does not match an artifact version at "
                "that exact path in this session"
            )
            continue
        if verified_files is not None:
            verified_files[f"source:{relative}"] = actual


def _check_entry_points(
    value: Any,
    context: CodeEvidenceContext,
    problems: list[str],
    verified_files: dict[str, str] | None = None,
) -> None:
    entries = _entries(value)
    if not entries:
        problems.append("'entry_points' names no runnable entry point")
        return
    for item in entries:
        claim = str(item.get("path") if isinstance(item, Mapping) else item or "")
        read = _read_claim(claim, context)
        if read is None:
            problems.append(
                f"entry point {claim!r} is unavailable inside this session's "
                "workspace (missing, unreadable, unsafe, or outside the root)"
            )
            continue
        resolved, relative, data = read
        if resolved.suffix.lower() not in _PYTHON_SUFFIXES:
            # Existence and bytes only. The daemon has no honest parser for R
            # (or anything else), and pretending otherwise would either reject
            # valid code or accept broken code on a guess.
            if verified_files is not None:
                verified_files[f"entry:{relative}"] = hashlib.sha256(data).hexdigest()
            continue
        try:
            # compile(), never exec(): verifying an entry point must not be a
            # way to run agent-authored code inside the daemon process.
            compile(data, str(resolved), "exec", dont_inherit=True)
        except (SyntaxError, ValueError) as error:
            problems.append(f"entry point {claim!r} does not compile: {error}")
            continue
        if verified_files is not None:
            verified_files[f"entry:{relative}"] = hashlib.sha256(data).hexdigest()


def _check_architecture_summary(value: Any, problems: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        problems.append(
            "'architecture_summary' must state, in prose, what each module owns"
        )


def stdout_reports_failure(text: Any) -> str | None:
    """The first failure signal in stored stdout, or ``None``."""

    body = text if isinstance(text, str) else ""
    for signal in _FAILURE_SIGNALS:
        found = signal.search(body)
        if found is not None:
            return found.group(0)
    return None


def _check_test_evidence(
    value: Any, context: CodeEvidenceContext, problems: list[str]
) -> None:
    entries = _entries(value)
    if not entries:
        problems.append("'test_evidence' names no test run")
        return
    lookup = context.cell_lookup
    for item in entries:
        if not isinstance(item, Mapping):
            problems.append(
                f"'test_evidence' entry {item!r} is not a "
                "{command, producing_cell_id}"
            )
            continue
        command = str(item.get("command") or "").strip()
        cell_id = str(item.get("producing_cell_id") or "").strip()
        if not command:
            problems.append("a 'test_evidence' entry names no command")
            continue
        if not command_preserves_failure_status(command):
            problems.append(
                f"test command {command!r} uses shell composition that can mask "
                "an earlier failure status"
            )
            continue
        if not cell_id:
            problems.append(
                f"test evidence for {command!r} names no producing_cell_id, so "
                "there is no recorded execution behind it"
            )
            continue
        if lookup is None:
            problems.append(
                "test evidence cannot be verified in this runtime: no execution "
                "log is reachable"
            )
            continue
        try:
            row = lookup(cell_id)
        except Exception:  # noqa: BLE001 - an unreadable log is not a pass
            row = None
        if not isinstance(row, Mapping):
            recorded_any: bool | None = None
            if context.has_cells is not None:
                try:
                    recorded_any = bool(context.has_cells())
                except Exception:  # noqa: BLE001 - an unreadable log is unknown
                    recorded_any = None
            if recorded_any is False:
                # A different failure with a different repair: the cell may
                # well have run, but this runtime recorded no cells at all, so
                # NO id could ever verify here. Saying "this run never
                # executed it" would be actively false and send the model
                # chasing a phantom.
                problems.append(
                    f"test evidence for {command!r} names cell {cell_id!r}, "
                    "but this runtime recorded no cells at all for this run — "
                    "its execution log is empty, so no cell id can back the "
                    "claim"
                )
            else:
                problems.append(
                    f"test evidence for {command!r} names cell {cell_id!r}, "
                    "which this run never executed"
                )
            continue
        if context.frame_id and str(row.get("root_frame_id") or "") != context.frame_id:
            problems.append(
                f"test evidence for {command!r} names cell {cell_id!r} from "
                "another session; a real row elsewhere is not this run's evidence"
            )
            continue
        status = str(row.get("status") or "")
        if status != "ok":
            problems.append(
                f"test evidence for {command!r} names cell {cell_id!r}, whose "
                f"recorded status is {status or 'unknown'!r}, not 'ok'"
            )
            continue
        failure = stdout_reports_failure(row.get("stdout"))
        if failure is not None:
            problems.append(
                f"the recorded output of cell {cell_id!r} reports {failure!r}, "
                f"so {command!r} did not pass"
            )
            continue
        receipt = context.test_receipt
        if receipt is None:
            problems.append(
                f"test evidence for {command!r} cannot be verified because no "
                "trusted shell-execution receipt source is available"
            )
            continue
        try:
            verified = bool(receipt(cell_id, command))
        except Exception:  # noqa: BLE001 - an unreadable receipt is not a pass
            verified = False
        if not verified:
            problems.append(
                f"cell {cell_id!r} has no successful Host-authorized execution "
                f"receipt for the exact command {command!r} in this turn and branch"
            )
            continue


def verify_code_evidence(
    payload: Mapping[str, Any],
    *,
    task_mode: Any,
    context: CodeEvidenceContext | None,
) -> str | None:
    """Refuse a code-mode completion whose evidence does not check out.

    Returns ``None`` when the mode needs no code evidence (the default) or
    every check passed; otherwise the soft-fail refusal message.
    """

    error, _seal = validate_code_evidence(
        payload,
        task_mode=task_mode,
        context=context,
    )
    return error


def validate_code_evidence(
    payload: Mapping[str, Any],
    *,
    task_mode: Any,
    context: CodeEvidenceContext | None,
) -> tuple[str | None, Mapping[str, str]]:
    """Validate claims and seal every file byte-string checked successfully."""

    empty: Mapping[str, str] = MappingProxyType({})
    if not requires_code_evidence(task_mode):
        return None, empty
    if context is None:
        return (
            (
                f"this turn runs in {task_mode} mode, whose completion must carry "
                "verified source_files / entry_points / architecture_summary / "
                "test_evidence, but no evidence context is available in this "
                "runtime to check them against"
            ),
            empty,
        )
    missing = [key for key in CODE_EVIDENCE_KEYS if not payload.get(key)]
    if missing:
        return (
            (
                f"this turn runs in {task_mode} mode, so its completion must "
                "declare " + ", ".join(missing) + ". Save the implementation to "
                "source files, keep a thin entry point, run the tests in a cell, "
                "then submit again naming each file, each entry point, what each "
                "module owns, and the cell id that ran each test command."
            ),
            empty,
        )
    problems: list[str] = []
    verified_files: dict[str, str] = {}
    _check_source_files(payload.get("source_files"), context, problems, verified_files)
    _check_entry_points(payload.get("entry_points"), context, problems, verified_files)
    _check_architecture_summary(payload.get("architecture_summary"), problems)
    _check_test_evidence(payload.get("test_evidence"), context, problems)
    if not problems:
        return None, MappingProxyType(dict(sorted(verified_files.items())))
    return (
        (
            f"the {task_mode} completion evidence does not check out: "
            + "; ".join(problems)
            + ". Fix the code or the claim — do not restate it."
        ),
        empty,
    )


__all__ = [
    "CODE_EVIDENCE_KEYS",
    "CodeEvidenceContext",
    "EVIDENCE_REQUIRED_MODES",
    "MAX_SOURCE_BYTES",
    "gather_code_evidence_context",
    "requires_code_evidence",
    "stdout_reports_failure",
    "validate_code_evidence",
    "verify_code_evidence",
]
