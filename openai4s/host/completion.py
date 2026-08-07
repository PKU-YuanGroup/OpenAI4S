"""The sole successful completion contract for Code-as-Action tasks."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

PAST_TENSE_STARTERS = frozenset(
    {
        "built",
        "found",
        "made",
        "ran",
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
_NUMBER_TOKEN = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?%?")
_CLAIM_WALK_MAX_NODES = 512
_CLAIM_WALK_MAX_DEPTH = 6
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
    """The two read-only queries reconciliation needs from the ``Store``."""

    def list_artifacts(self, filters: dict | None = None) -> list[dict]: ...

    def list_cells(
        self, root_frame_id: str, *, branch_id: str | None = None
    ) -> list[dict]: ...


def gather_submission_evidence(
    store: EvidenceStore,
    root_frame_id: str | None,
    search_roots: tuple[Path, ...] = (),
) -> SubmissionEvidence:
    """Collect the run's produced-file evidence from the store.

    Artifacts are matched store-wide rather than per-session on purpose:
    a delegation child submits through its own frame while its files may be
    registered under the parent's scope, and a looser evidence set can only
    let an honest claim through, never refuse one.  Store failures degrade to
    an empty set — missing evidence must never crash the completion path.
    """

    names: set[str] = set()
    try:
        for row in store.list_artifacts() or []:
            for key in ("filename", "artifact_id", "latest_version_id"):
                _add_known_name(names, row.get(key))
    except Exception:
        pass
    if root_frame_id:
        try:
            for cell in store.list_cells(root_frame_id) or []:
                for key in ("files_written", "figures"):
                    values = cell.get(key) or []
                    if isinstance(values, (list, tuple)):
                        for value in values:
                            _add_known_name(names, value)
        except Exception:
            pass
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
        # {"report.md": "the report"}; the file-shape filter disambiguates.
        for key, item in value.items():
            if isinstance(key, str):
                yield key.strip()
            if isinstance(item, str):
                yield item.strip()


def collect_file_claims(output: Any) -> list[tuple[str, str]]:
    """``(key, claim)`` pairs asserting produced files/artifacts in ``output``."""

    claims: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    stack: list[tuple[Any, int]] = [(output, 0)]
    nodes = 0
    while stack:
        node, depth = stack.pop()
        nodes += 1
        if nodes > _CLAIM_WALK_MAX_NODES or depth > _CLAIM_WALK_MAX_DEPTH:
            break
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
                stack.append((value, depth + 1))
        elif isinstance(node, (list, tuple)):
            for value in node:
                stack.append((value, depth + 1))
    return claims


def _scan_basenames(root: Path) -> frozenset[str]:
    """Bounded shallow scan of one disk root's file basenames (lowercased)."""

    names: set[str] = set()
    entries = 0
    try:
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
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
            if Path(claim).exists():
                return True
        else:
            for root in evidence.search_roots:
                if (root / claim).exists():
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


def _claimed_number_matches(token: str, actual: float) -> bool:
    """Whether a number written in prose is the metric at the written precision."""

    text = token[:-1] if token.endswith("%") else token
    try:
        claimed = float(text)
    except ValueError:
        return False
    candidates = [claimed, claimed / 100.0] if token.endswith("%") else [claimed]
    for value in candidates:
        if value == actual:
            return True
        if "." in text and "e" not in text.lower():
            decimals = len(text.rsplit(".", 1)[1])
            if abs(actual - value) <= 10.0**-decimals:
                return True
        scale = max(abs(value), abs(actual))
        if scale and abs(value - actual) / scale <= 0.005:
            return True
    return False


def check_summary_metrics(output: Any) -> list[str]:
    """Contradictions between ``output.summary`` prose and ``output.metrics``.

    For each metric key that appears in the summary with numbers nearby, at
    least one nearby number must equal the metric at its written precision.
    A key the summary never mentions, or mentions without numbers, is fine —
    only a stated, wrong value is a contradiction.
    """

    if not isinstance(output, dict):
        return []
    summary = output.get("summary")
    metrics = output.get("metrics")
    if not isinstance(summary, str) or not isinstance(metrics, dict):
        return []
    problems: list[str] = []
    lowered = summary.lower()
    for key, actual in metrics.items():
        if not isinstance(key, str):
            continue
        if isinstance(actual, bool) or not isinstance(actual, (int, float)):
            continue
        pattern = _metric_key_pattern(key)
        if pattern is None:
            continue
        nearby: list[str] = []
        for match in pattern.finditer(lowered):
            window = lowered[max(0, match.start() - 24) : match.end() + 48]
            nearby.extend(item.group(0) for item in _NUMBER_TOKEN.finditer(window))
        if not nearby:
            continue
        if not any(_claimed_number_matches(token, float(actual)) for token in nearby):
            stated = ", ".join(dict.fromkeys(nearby))
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
    for key, claim in collect_file_claims(output):
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

        schema = spec.get("output_schema")
        if schema is not None:
            error = validate_output_schema(spec.get("output"), schema)
            if error:
                return {"error": error}

        if self._evidence is not None:
            try:
                evidence = self._evidence()
            except Exception:
                # Evidence gathering must never block completion outright: a
                # broken provider degrades to the legacy unreconciled accept
                # rather than refusing every submission (which would deadlock
                # the only completion signal the loop accepts).
                evidence = None
            if evidence is not None:
                error = reconcile_submission_claims(spec, evidence)
                if error:
                    return {"error": error}

        self.last_output = {
            "output": spec.get("output"),
            "completion_bullets": bullets,
        }
        return {"status": "ok"}

    def clear(self) -> None:
        self.last_output = None


__all__ = [
    "CompletionService",
    "EvidenceStore",
    "PAST_TENSE_STARTERS",
    "SubmissionEvidence",
    "check_summary_metrics",
    "collect_file_claims",
    "first_english_word",
    "gather_submission_evidence",
    "reconcile_submission_claims",
    "validate_completion_bullets",
    "validate_output_schema",
]
