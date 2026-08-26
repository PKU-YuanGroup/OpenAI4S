"""The sole successful completion contract for Code-as-Action tasks."""

from __future__ import annotations

import os
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Protocol

PAST_TENSE_STARTERS = frozenset(
    {
        "built",
        "found",
        "made",
        "ran",
        "reran",
        "wrote",
        "read",
        "sent",
        "set",
        "got",
        "began",
        "chose",
        "drew",
        "fit",
        "held",
        "kept",
        "led",
        "left",
        "put",
        "saw",
        "shown",
        "showed",
        "split",
        "taught",
        "told",
        "understood",
        "computed",
        "created",
        "generated",
        "produced",
        "analyzed",
        "identified",
    }
)
_CJK_START = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

#: The machine-readable completion vocabulary shared by ``host.submit_output``,
#: ``finalize_response``, and the delegation envelope. A submission may
#: declare one; omission means ``completed``. Anything else is a validation
#: error \u2014 free-text statuses would put NL parsing back into the parent.
TASK_STATUS_VALUES = ("completed", "partial", "blocked", "failed")


def first_english_word(bullet: Any) -> str | None:
    """The lowercased first word of an English bullet, or ``None``.

    ``None`` means the word-level heuristics do not apply: the bullet is not a
    non-empty string, or it starts with CJK text, whose morphology does not
    mark tense the way the English guards assume.
    """
    if not isinstance(bullet, str) or not bullet.strip():
        return None
    first = re.split(r"\s+", bullet.strip())[0].lower()
    return None if _CJK_START.match(first) else first


def validate_completion_bullets(bullets: list) -> str | None:
    """Require 1-4 non-empty completed-action bullets.

    English bullets retain the past-tense verb guard. CJK languages do not
    encode tense with the same morphology, so their non-empty verb phrases are
    accepted instead of being forced through an English suffix rule.
    """
    if not isinstance(bullets, list) or not (1 <= len(bullets) <= 4):
        return "completion_bullets must be a list of 1-4 items"
    for bullet in bullets:
        if not isinstance(bullet, str) or not bullet.strip():
            return "each completion bullet must be a non-empty string"
        first = first_english_word(bullet)
        if first is None:
            continue
        if not (first.endswith("ed") or first in PAST_TENSE_STARTERS):
            return (
                f"completion bullet {bullet!r} must start with a past-tense verb "
                f"(e.g. 'Computed...', 'Saved...')"
            )
    return None


def validate_output_schema(output: Any, schema: dict) -> str | None:
    """Apply the legacy minimal JSON-schema-like output validation."""
    if not isinstance(schema, dict):
        return None
    schema_type = schema.get("type")
    if schema_type == "object":
        if not isinstance(output, dict):
            return "output must be an object per output_schema"
        for required in schema.get("required", []):
            if required not in output:
                return f"output missing required field {required!r}"
    elif schema_type == "array" and not isinstance(output, list):
        return "output must be an array per output_schema"
    elif schema_type == "string" and not isinstance(output, str):
        return "output must be a string per output_schema"
    elif schema_type == "number" and not isinstance(output, (int, float)):
        return "output must be a number per output_schema"
    return None


# --- submission reconciliation -------------------------------------------
#
# ``host.submit_output`` is the in-cell sibling of the engine's
# ``finalize_response`` reconciliation (``agent/finalize.py``): a cell that
# really ran can still submit an ``output`` whose prose or artifact list names
# files the run never produced, or a summary whose numbers contradict the same
# submission's own metrics.  Accepting that publishes provenance that is wrong
# rather than absent, so claims are checked per-item against the run's
# evidence and refused as a repairable soft error.

#: ``output`` keys whose string values assert a produced file.  Deliberately
#: produce-shaped: input-shaped keys (``source``, ``input``, ``path``,
#: ``data``) stay out so naming what a cell merely *read* is never flagged.
_FILE_CLAIM_KEYS = frozenset(
    {
        "artifact",
        "artifacts",
        "figure",
        "figures",
        "file",
        "files",
        "files_written",
        "output_file",
        "output_files",
        "output_path",
        "output_paths",
        "plot",
        "plots",
        "saved_file",
        "saved_files",
        "saved_to",
    }
)
#: Keys whose non-file tokens are artifact/version IDs to resolve in the store.
_ID_CLAIM_KEYS = frozenset({"artifact", "artifacts"})

_FILE_EXT = re.compile(r"\.([A-Za-z0-9]{1,8})$")
#: A standalone number in prose.  The lookbehind refuses digits glued to a
#: word or hyphen ("f1", "top_10", "top-5", "v1.2") — those are identifier
#: fragments, not stated values — and comma groups keep "1,500" one token.
_NUMBER_TOKEN = re.compile(
    r"(?<![\w.\-])[-+]?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][-+]?\d+)?%?"
)
#: A number introduced as a bound rather than a statement of the metric:
#: "p < 0.05", "fewer than 100", "short of the 0.95 target".  Matching is
#: anchored so "over" fires but "moreover" does not.
_COMPARATOR_BEFORE = re.compile(
    r"(?:<|>|≤|≥|\b(?:than|least|most|under|over|above|below|within|beyond"
    r"|exceed(?:s|ed)?|up to|short of)\b)[^0-9a-z]*$"
)
_THRESHOLD_AFTER = ("target", "threshold", "cutoff", "goal", "cap", "limit", "budget")
#: Bounds on the claim walk. Neither is a security parameter any more: hitting
#: either makes ``_walk_file_claims`` report ``truncated=True`` and
#: reconciliation *refuses* the submission, so a shape built to overrun a
#: budget (a wall of decoy containers, or a claim buried below the depth cap)
#: is rejected rather than waved through. Both used to end or prune the walk
#: silently, which let exactly those shapes smuggle an unchecked fabricated
#: claim past the whole check.
#:
#: With fail-closed semantics the only thing these still trade off is work
#: against false refusals, so they sit far above any real ``output`` — a
#: conclusion object is a handful of nodes a few levels deep — while keeping
#: the worst case bounded (the walk runs while the kernel worker blocks on the
#: host-call lock).
_CLAIM_WALK_MAX_NODES = 65_536
_CLAIM_WALK_MAX_DEPTH = 24
_SCAN_MAX_ENTRIES = 4096
_SCAN_MAX_DEPTH = 4
_SCAN_SKIP_DIRS = frozenset({"node_modules", "venv", "__pycache__"})


@dataclass(frozen=True)
class SubmissionEvidence:
    """What this run can prove it produced, gathered on the dispatcher side.

    ``known_names`` holds lowercased artifact filenames (full and basename),
    artifact/version IDs, and the ``files_written``/``figures`` recorded by
    this session's executed cells.  ``search_roots`` are directories probed on
    disk — the mid-cell escape hatch: a file the *current* cell just wrote is
    not captured or logged yet, so its only evidence is the filesystem.
    """

    known_names: frozenset[str] = frozenset()
    search_roots: tuple[Path, ...] = ()


class EvidenceStore(Protocol):
    """The two read-only queries reconciliation needs from the ``Store``.

    Deliberately the *narrow* ones: the wide ``list_artifacts``/``list_cells``
    reads join, sort, and materialize every cell's code and stdout — all
    discarded here, on a call that runs while the kernel worker blocks on
    the host-call lock.
    """

    def list_artifact_names(self) -> list[dict]: ...

    def list_cell_outputs(self, root_frame_id: str) -> list[dict]: ...


def gather_submission_evidence(
    store: EvidenceStore,
    root_frame_id: str | None,
    search_roots: tuple[Path, ...] = (),
) -> SubmissionEvidence:
    """Collect the run's produced-file evidence from the store.

    Artifacts are matched store-wide rather than per-session on purpose:
    a delegation child submits through its own frame while its files may be
    registered under the parent's scope, and a looser evidence set can only
    let an honest claim through, never refuse one.  Store failures propagate
    — the caller (``CompletionService.submit``) turns a raising evidence
    provider into the legacy unreconciled accept.  Swallowing them here and
    returning an *empty* set inverted that degradation: an artifact-ID claim
    is backable only by the store, so a failing store refused every honest
    ID claim instead of accepting it.
    """

    names: set[str] = set()
    for row in store.list_artifact_names() or []:
        for key in ("filename", "artifact_id", "latest_version_id"):
            _add_known_name(names, row.get(key))
    if root_frame_id:
        for cell in store.list_cell_outputs(root_frame_id) or []:
            for key in ("files_written", "figures"):
                values = cell.get(key) or []
                if isinstance(values, (list, tuple)):
                    for value in values:
                        _add_known_name(names, value)
    return SubmissionEvidence(frozenset(names), tuple(search_roots))


def _add_known_name(names: set[str], value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        return
    text = value.strip().lower()
    names.add(text)
    basename = text.rstrip("/").rsplit("/", 1)[-1]
    if basename:
        names.add(basename)


def _looks_like_file(text: str) -> bool:
    if not (1 <= len(text) <= 240) or any(ch.isspace() for ch in text):
        return False
    if "://" in text:
        return False
    match = _FILE_EXT.search(text)
    return bool(match) and any(ch.isalpha() for ch in match.group(1))


def _looks_like_identifier(text: str) -> bool:
    return 1 <= len(text) <= 120 and not any(ch.isspace() for ch in text)


def _claim_strings(value: Any) -> Iterator[str]:
    """Candidate claim strings directly under one produce-shaped key."""

    if isinstance(value, str):
        yield value.strip()
    elif isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, str):
                yield item.strip()
    elif isinstance(value, dict):
        # Both shapes occur in the wild: {"report": "report.md"} and
        # {"report.md": "the report"}.  Keys are claims only when they are
        # file-shaped themselves: under the ID keys any bare word passes the
        # identifier filter, so yielding descriptive labels ({"heatmap":
        # "heatmap.svg"}) turned the label into a must-resolve artifact ID
        # and refused honest submissions for their own captions.
        for key, item in value.items():
            if isinstance(key, str) and _looks_like_file(key.strip()):
                yield key.strip()
            if isinstance(item, str):
                yield item.strip()


def _walk_file_claims(output: Any) -> tuple[list[tuple[str, str]], bool]:
    """``((key, claim) pairs, truncated)`` for the produce keys in ``output``.

    ``truncated`` is True when any container went uninspected — the node budget
    ran out, or a subtree sat below the depth cap. Callers that gate a
    submission on this treat it as fail-closed: a shape too large or too deep
    to verify is refused, never accepted on the strength of the claims that
    happened to be reachable. Both bounds used to be silent (one ended the
    walk, the other pruned a subtree), and each was a constructible bypass:
    decoy containers ahead of a buried claim, or a claim nested one level below
    the depth cap, went unchecked and the submission was accepted.

    Breadth-first over container nodes only, so shallow claims are visited
    before deep decoys and leaves cost no budget.
    """

    claims: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    queue: deque[tuple[Any, int]] = deque([(output, 0)])
    nodes = 0
    truncated = False
    while queue:
        node, depth = queue.popleft()
        nodes += 1
        if nodes > _CLAIM_WALK_MAX_NODES:
            return claims, True
        if depth > _CLAIM_WALK_MAX_DEPTH:
            truncated = True
            continue
        children: Iterable[Any] = ()
        if isinstance(node, dict):
            for key, value in node.items():
                key_text = key.lower() if isinstance(key, str) else ""
                if key_text in _FILE_CLAIM_KEYS:
                    for claim in _claim_strings(value):
                        is_file = _looks_like_file(claim)
                        is_id = key_text in _ID_CLAIM_KEYS and _looks_like_identifier(
                            claim
                        )
                        if (is_file or is_id) and (key_text, claim) not in seen:
                            seen.add((key_text, claim))
                            claims.append((key_text, claim))
            children = node.values()
        elif isinstance(node, (list, tuple)):
            children = node
        for value in children:
            if isinstance(value, (dict, list, tuple)):
                queue.append((value, depth + 1))
    return claims, truncated


def collect_file_claims(output: Any) -> list[tuple[str, str]]:
    """``(key, claim)`` pairs asserting produced files/artifacts in ``output``."""

    return _walk_file_claims(output)[0]


def _scan_basenames(root: Path) -> frozenset[str]:
    """Bounded shallow scan of one disk root's file basenames (lowercased)."""

    names: set[str] = set()
    entries = 0
    try:
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            # Checked per directory, not only per file: a directory-heavy,
            # file-light tree never tripped the in-loop file check and was
            # walked in full while the submitting cell blocked on the host
            # RPC lock.
            if entries >= _SCAN_MAX_ENTRIES:
                return frozenset(names)
            depth = len(Path(dirpath).parts) - base_depth
            if depth >= _SCAN_MAX_DEPTH:
                dirnames[:] = []
            else:
                dirnames[:] = [
                    name
                    for name in dirnames
                    if not name.startswith(".") and name not in _SCAN_SKIP_DIRS
                ]
            for filename in filenames:
                names.add(filename.lower())
                entries += 1
                if entries >= _SCAN_MAX_ENTRIES:
                    return frozenset(names)
            entries += len(dirnames)
    except OSError:
        pass
    return frozenset(names)


def _within_a_root(path: Path, roots: tuple[Path, ...]) -> bool:
    """Whether ``path`` resolves to a location inside one of ``roots``.

    A path is disk-backed evidence only when it lands inside an authorized
    evidence root (the session workspace / process cwd). Existence alone says
    nothing about *this run* having produced it: an absolute claim of
    ``/etc/hosts`` — or a relative ``../../etc/passwd`` climbing out of a
    root — pointed at a real file the run never wrote and backed a fabricated
    artifact claim. ``resolve()`` also collapses a workspace symlink aimed
    outside the roots, so a link cannot launder an external file back in.
    """
    try:
        resolved = path.resolve()
    except (OSError, ValueError, RuntimeError):
        return False
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return True
        except (OSError, ValueError, RuntimeError):
            continue
    return False


def _claim_backed(
    claim: str,
    evidence: SubmissionEvidence,
    scanned: dict[Path, frozenset[str]],
) -> bool:
    lowered = claim.lower()
    basename = lowered.rstrip("/").rsplit("/", 1)[-1]
    if lowered in evidence.known_names or basename in evidence.known_names:
        return True
    try:
        if os.path.isabs(claim):
            candidate = Path(claim)
            if candidate.exists() and _within_a_root(candidate, evidence.search_roots):
                return True
        else:
            for root in evidence.search_roots:
                candidate = root / claim
                # Confine the join to the root: a relative claim with ``..``
                # segments resolves outside it, and existence there is not
                # this run's evidence any more than an absolute system path is.
                if candidate.exists() and _within_a_root(candidate, (root,)):
                    return True
    except (OSError, ValueError):
        pass
    if _looks_like_file(claim):
        for root in evidence.search_roots:
            if root not in scanned:
                scanned[root] = _scan_basenames(root)
            if basename in scanned[root]:
                return True
    return False


def _metric_key_pattern(key: str) -> re.Pattern[str] | None:
    tokens = [token for token in re.split(r"[\s_\-]+", key.lower()) if token]
    if not tokens:
        return None
    return re.compile(
        r"\b" + r"[\s_\-]*".join(re.escape(token) for token in tokens) + r"\b"
    )


def _claim_candidates(token: str) -> list[tuple[float, float]]:
    """``(value, written_scale)`` readings of a number token, [] if unparsable.

    A percent token reads both ways ("93%" may state 93.0 or 0.93); the scale
    records how much smaller the *fraction* reading is than the written text,
    so written-precision tolerances can be applied in the right unit.
    """

    text = (token[:-1] if token.endswith("%") else token).replace(",", "")
    try:
        claimed = float(text)
    except ValueError:
        return []
    if token.endswith("%"):
        return [(claimed, 1.0), (claimed / 100.0, 100.0)]
    return [(claimed, 1.0)]


def _claimed_number_matches(token: str, actual: float) -> bool:
    """Whether a number written in prose is the metric at the written precision."""

    text = (token[:-1] if token.endswith("%") else token).replace(",", "")
    for value, written_scale in _claim_candidates(token):
        if value == actual:
            return True
        if "e" not in text.lower():
            decimals = len(text.rsplit(".", 1)[1]) if "." in text else 0
            # The tolerance lives in the token's own unit: "99.9%" states one
            # decimal of a *percentage*, i.e. ±0.001 of the fraction reading.
            # Unscaled, the same tolerance was ±0.1 and accepted 99.9% as a
            # restatement of 0.93.
            if abs(actual - value) <= 10.0**-decimals / written_scale:
                return True
        scale = max(abs(value), abs(actual))
        if scale and abs(value - actual) / scale <= 0.005:
            return True
    return False


def _is_near_miss(token: str, actual: float) -> bool:
    """Whether a non-matching number plausibly *restates* this metric.

    The motivating incident is a same-quantity wrong value (summary 2.4495,
    metric 2.3664): same sign, same order of magnitude.  A count or epoch in
    the window ("over the 100 epochs" near a 0.02 loss) is a different
    quantity, not a contradiction — requiring every nearby number to match
    refused honest prose on the loop's only completion signal.
    """

    for value, _scale in _claim_candidates(token):
        if value == 0.0 and actual == 0.0:
            return True
        if value == 0.0 or actual == 0.0:
            continue
        if (value > 0) != (actual > 0):
            continue
        ratio = abs(value) / abs(actual)
        if 1 / 3 <= ratio <= 3:
            return True
    return False


def _in_comparator_context(lowered: str, token: re.Match[str]) -> bool:
    """A number introduced as a bound, not a statement of the metric.

    Exclusion fails open: a skipped token can only let a submission pass,
    never refuse one.
    """

    before = lowered[max(0, token.start() - 16) : token.start()]
    if _COMPARATOR_BEFORE.search(before):
        return True
    after = lowered[token.end() : token.end() + 16].lstrip(" \t:—-")
    return any(after.startswith(marker) for marker in _THRESHOLD_AFTER)


def check_summary_metrics(output: Any) -> list[str]:
    """Contradictions between ``output.summary`` prose and ``output.metrics``.

    A contradiction requires a nearby number that plausibly *restates* the
    metric (same sign and order of magnitude) while no nearby number matches
    it at its written precision.  Digits inside the key's own mention
    ("top 10 accuracy"), identifier fragments ("f1", "top-5"), bounds
    ("p < 0.05", "short of the 0.95 target"), and different-quantity numbers
    ("over the 100 epochs" near a loss) are not stated values — every
    exclusion fails open, because a false refusal here blocks the loop's
    only completion signal.
    """

    if not isinstance(output, dict):
        return []
    summary = output.get("summary")
    metrics = output.get("metrics")
    if not isinstance(summary, str) or not isinstance(metrics, dict):
        return []
    problems: list[str] = []
    lowered = summary.lower()
    tokens = list(_NUMBER_TOKEN.finditer(lowered))
    for key, actual in metrics.items():
        if not isinstance(key, str):
            continue
        if isinstance(actual, bool) or not isinstance(actual, (int, float)):
            continue
        pattern = _metric_key_pattern(key)
        if pattern is None:
            continue
        spans = [(m.start(), m.end()) for m in pattern.finditer(lowered)]
        if not spans:
            continue
        nearby = [
            token
            for token in tokens
            if any(
                start - 24 <= token.start() and token.end() <= end + 48
                for start, end in spans
            )
            and not any(
                token.start() < end and token.end() > start for start, end in spans
            )
            and not _in_comparator_context(lowered, token)
        ]
        if not nearby:
            continue
        actual_value = float(actual)
        texts = [token.group(0) for token in nearby]
        if any(_claimed_number_matches(text, actual_value) for text in texts):
            continue
        misses = [text for text in texts if _is_near_miss(text, actual_value)]
        if not misses:
            continue
        stated = ", ".join(dict.fromkeys(misses))
        problems.append(
            f"summary states {key!r} as {stated} but metrics[{key!r}] = {actual}"
        )
    return problems


def reconcile_submission_claims(spec: dict, evidence: SubmissionEvidence) -> str | None:
    """Refuse a submission whose claims outrun the run's evidence.

    Per-claim, not per-run: the producing cell really executed, so the
    zero-execution finalize guard does not apply here.  Every file or
    artifact the ``output`` names must be backed by the artifact store, an
    executed cell's recorded writes, or the filesystem, and numbers the
    summary repeats must agree with the same submission's metrics.
    """

    output = spec.get("output")
    problems: list[str] = []
    unmatched: list[str] = []
    scanned: dict[Path, frozenset[str]] = {}
    file_claims, truncated = _walk_file_claims(output)
    if truncated:
        # Fail closed: the output is too large or has too many containers to
        # verify exhaustively, so some claim may be unchecked. Refusing (rather
        # than accepting the reachable claims) is what stops a decoy-padded
        # shape from smuggling a fabricated claim past the budget.
        problems.append(
            "output is too large or too deeply nested to verify its file "
            "claims exhaustively; reduce it to a small, shallow conclusion "
            "object that names only the files this run produced"
        )
    for key, claim in file_claims:
        if not _claim_backed(claim, evidence, scanned):
            unmatched.append(f"{claim!r} (under {key!r})")
    if unmatched:
        problems.append(
            "output names files this run never produced: "
            + ", ".join(unmatched)
            + " — not in the artifact store, not recorded as written by any "
            "executed cell, and not present on disk"
        )
    problems.extend(check_summary_metrics(output))
    if not problems:
        return None
    return (
        "submitted output is not backed by this run's evidence: "
        + "; ".join(problems)
        + ". Name only files the run actually wrote (or drop the claim), "
        "keep the summary consistent with the submitted metrics, then call "
        "host.submit_output again."
    )


class CompletionService:
    """Validate and commit the one terminal signal accepted from a cell.

    Prose never completes a task.  A successful :meth:`submit` stores the
    structured output that the outer Agent/Gateway loop observes after cell
    execution.  Validation failures are soft errors and leave the prior state
    untouched, so the model can recover in a later cell.

    ``evidence`` supplies the run's produced-file evidence at submit time
    (late-bound: the CLI assigns its root frame after the dispatcher exists).
    ``None`` preserves the legacy behaviour for callers that keep no ledger,
    mirroring ``execute_finalize_action(evidence=None)``.
    """

    def __init__(
        self, evidence: Callable[[], SubmissionEvidence] | None = None
    ) -> None:
        self.last_output: dict | None = None
        self._evidence = evidence

    def submit(self, spec: dict) -> dict:
        bullets = spec.get("completion_bullets") or []
        error = validate_completion_bullets(bullets)
        if error:
            return {"error": error}

        task_status = spec.get("task_status")
        if task_status is not None and (
            not isinstance(task_status, str) or task_status not in TASK_STATUS_VALUES
        ):
            return {
                "error": "task_status must be one of "
                + ", ".join(TASK_STATUS_VALUES)
                + " (omit it for a fully completed task)"
            }

        schema = spec.get("output_schema")
        if schema is not None:
            error = validate_output_schema(spec.get("output"), schema)
            if error:
                return {"error": error}

        if self._evidence is not None:
            # The provider is consulted only when the output actually names
            # produced files: claim collection and the summary/metrics check
            # are pure functions of the spec, so a claim-less submission
            # ({"output": {"answer": 42}}) must not pay the store sweep —
            # this path runs while the kernel worker blocks on the host-call
            # lock.  (collect_file_claims runs again inside reconciliation;
            # it is bounded and walk-only, far cheaper than the queries it
            # gates.)
            evidence: SubmissionEvidence | None = SubmissionEvidence()
            if collect_file_claims(spec.get("output")):
                try:
                    evidence = self._evidence()
                except Exception:
                    # Evidence gathering must never block completion
                    # outright: a broken provider degrades to the legacy
                    # unreconciled accept rather than refusing every
                    # submission (which would deadlock the only completion
                    # signal the loop accepts).
                    evidence = None
            if evidence is not None:
                error = reconcile_submission_claims(spec, evidence)
                if error:
                    return {"error": error}

        self.last_output = {
            "output": spec.get("output"),
            "completion_bullets": bullets,
        }
        if task_status is not None:
            # Additive: the two-key CompletionRecord shape is preserved when
            # no status is declared, so every existing consumer keeps its
            # contract; a declared status rides alongside for the delegation
            # envelope's single-writer derivation.
            self.last_output["task_status"] = task_status
        return {"status": "ok"}

    def clear(self) -> None:
        self.last_output = None


__all__ = [
    "CompletionService",
    "EvidenceStore",
    "PAST_TENSE_STARTERS",
    "TASK_STATUS_VALUES",
    "SubmissionEvidence",
    "check_summary_metrics",
    "collect_file_claims",
    "first_english_word",
    "gather_submission_evidence",
    "reconcile_submission_claims",
    "validate_completion_bullets",
    "validate_output_schema",
]
