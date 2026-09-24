"""Read a Session package and say how its runtime behaved.

A Session package is what a user can hand over after something broke. It has
always carried the conversation, the Action Ledger, the Notebook and the kernel
generations, but nothing *read* them: a maintainer holding one still rebuilt the
timeline by hand -- the gap before a stream timeout from message timestamps, a
repeated call from byte-identical argument previews, a child's failure from the
database it did not have. This module is that reading, done once, the same way
for every package.

It is a sibling of ``evidence.py`` and keeps its constraints: the standard
library only, no Store, no configuration, no network. It works on a package
from any release. A package exported before ``runtime/`` existed still yields
turns, stops, parse errors, gaps and Cell failures from the ledger, messages and
Notebook it already carries; a newer one adds the host environment, the model
endpoint's effective capabilities, host calls, delegated children, permission
requests, compactions and per-call model telemetry.

What it writes is deliberately content-free: counts, recorded codes, tool and
method names, exception *type* names, and durations between two recorded
timestamps. No message, argument, Cell source, output, path or URL text is
reproduced, so the report can be read -- and quoted -- without re-reading the
research it describes. It never guesses a cause; a finding states what the
records show and where in the package the rest is.
"""

from __future__ import annotations

import json
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

DIAGNOSIS_SCHEMA_VERSION = 1

#: The package documents a diagnosis reads. Anything absent is simply not used.
DOCUMENTS = (
    "session.json",
    "ledger.json",
    "notebook.json",
    "environment.json",
    "snapshots.json",
    "runtime/environment.json",
    "runtime/frames.json",
    "runtime/activity.json",
    "runtime/host_calls.json",
    "runtime/permissions.json",
    "runtime/compactions.json",
    "runtime/model_calls.json",
    "runtime/collection.json",
)

#: Terminal reasons that mean the turn delivered what it was asked for.
SUCCESSFUL_REASONS = frozenset({"submitted", "plan"})
#: Model-reply group kinds -- one per provider round-trip that was recorded.
REPLY_KINDS = frozenset({"code", "native_tools", "finalize", "no_action"})
#: Kernel generation states that end a worker abnormally.
ABNORMAL_KERNEL_STATES = frozenset({"crashed", "failed", "abandoned", "partial"})
#: Step statuses that describe a failed card.
FAILED_STEP_STATUSES = frozenset({"error", "failed", "failure"})

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
#: The trailing ``SomeError: message`` line of a traceback; only the type is kept.
_EXCEPTION_LINE = re.compile(
    r"^\s*(?:[A-Za-z_][\w]*\.)*([A-Za-z_]\w*(?:Error|Exception|Interrupt|Exit|"
    r"Warning|Timeout|Failure))\b"
)
_TOKEN = re.compile(r"^[A-Za-z0-9_.:/-]{1,80}$")

#: Reading limits for an untrusted archive, mirroring ``evidence.py``.
_MAX_DOCUMENT_BYTES = 64 << 20


class PackageReadError(RuntimeError):
    """The file is not a readable Session package."""


# --------------------------------------------------------------------- readers
def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value == value:
        return int(value)
    return None


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _token(value: Any) -> str:
    """A recorded code or name, kept only when it is shaped like one."""
    text = _str(value).strip()
    return text if _TOKEN.fullmatch(text) else ""


def _exception_type(text: Any) -> str:
    """The exception type a traceback ends with, never its message."""
    lines = [line for line in _str(text).strip().splitlines() if line.strip()]
    for line in reversed(lines[-6:]):
        match = _EXCEPTION_LINE.match(line)
        if match:
            return match.group(1)
    return ""


def read_package(path: str | Path) -> dict[str, Any]:
    """Parse the JSON documents a diagnosis reads from one package ZIP.

    Integrity is not checked here; ``evidence.verify_package`` does that, and a
    caller that cares runs it first. Sizes are bounded before any read.
    """

    try:
        archive = zipfile.ZipFile(Path(path))
    except (OSError, zipfile.BadZipFile) as error:
        raise PackageReadError(f"not a readable zip archive: {error}") from error
    documents: dict[str, Any] = {}
    with archive:
        names = set(archive.namelist())
        if "manifest.json" not in names:
            raise PackageReadError("no manifest.json; not an OpenAI4S package")
        for name in ("manifest.json", *DOCUMENTS):
            if name not in names:
                continue
            info = archive.getinfo(name)
            if info.file_size > _MAX_DOCUMENT_BYTES:
                raise PackageReadError(f"{name} is too large to read")
            try:
                documents[name] = json.loads(archive.read(info).decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as error:
                raise PackageReadError(f"{name} is not valid JSON") from error
    return documents


# ----------------------------------------------------------------------- turns
def _events(group: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [event for event in _list(group.get("events")) if isinstance(event, dict)]


def _terminal_result(group: Mapping[str, Any]) -> dict[str, Any]:
    for event in _events(group):
        result = event.get("result")
        if isinstance(result, dict) and result.get("reason"):
            return result
    return {}


def _usage_tokens(usage: Any) -> tuple[int, int]:
    usage = _dict(usage)
    return (
        _int(usage.get("input_tokens")) or _int(usage.get("prompt_tokens")) or 0,
        _int(usage.get("output_tokens")) or _int(usage.get("completion_tokens")) or 0,
    )


def _failure_messages(messages: list[Any]) -> list[dict[str, Any]]:
    found = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        failure = _dict(_dict(message.get("metadata")).get("failure"))
        if not failure:
            continue
        found.append(
            {
                "at": _int(message.get("created_at")),
                "code": _token(failure.get("code")),
                "request_id": _token(failure.get("request_id")),
                "output_committed": failure.get("output_committed") is True,
            }
        )
    return found


def _turn_rows(
    groups: list[Any],
    attempts: list[Any],
    messages: list[Any],
) -> list[dict[str, Any]]:
    """One row per (branch, turn), oldest first, derived from the ledger."""

    by_turn: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        key = (_str(group.get("branch_id")), _str(group.get("turn_id")))
        by_turn.setdefault(key, []).append(group)
    attempts_by_group: dict[str, list[dict[str, Any]]] = {}
    for attempt in attempts:
        if isinstance(attempt, dict):
            attempts_by_group.setdefault(_str(attempt.get("group_id")), []).append(
                attempt
            )

    rows: list[dict[str, Any]] = []
    for (branch_id, turn_id), members in by_turn.items():
        members.sort(
            key=lambda item: (
                _int(item.get("created_at")) or 0,
                _int(item.get("ordinal")) or 0,
            )
        )
        stamps: list[int] = []
        tools: Counter[str] = Counter()
        malformed = 0
        empty_arguments = 0
        replies = 0
        cells = 0
        tokens_in = tokens_out = 0
        terminal: dict[str, Any] | None = None
        attempt_states: Counter[str] = Counter()
        providers: Counter[str] = Counter()
        models: Counter[str] = Counter()
        for group in members:
            created = _int(group.get("created_at"))
            if created is not None:
                stamps.append(created)
            kind = _str(group.get("kind"))
            if kind in REPLY_KINDS:
                replies += 1
                provider = _token(group.get("provider"))
                model = _token(group.get("model"))
                if provider:
                    providers[provider] += 1
                if model:
                    models[model] += 1
            if kind == "code":
                cells += 1
            if kind == "terminal":
                terminal = group
            used_in, used_out = _usage_tokens(group.get("usage"))
            tokens_in += used_in
            tokens_out += used_out
            for event in _events(group):
                event_at = _int(event.get("created_at"))
                if event_at is not None:
                    stamps.append(event_at)
                if event.get("type") != "proposed" or kind not in {
                    "native_tools",
                    "finalize",
                }:
                    continue
                arguments = _dict(event.get("canonical_arguments"))
                name = _token(arguments.get("name")) or "unnamed"
                tools[name] += 1
                if arguments.get("parse_error"):
                    malformed += 1
                raw = event.get("raw_arguments")
                if isinstance(raw, str) and not raw.strip():
                    empty_arguments += 1
            for attempt in attempts_by_group.get(_str(group.get("group_id")), []):
                attempt_states[_token(attempt.get("terminal_state")) or "open"] += 1
        stamps.sort()
        started = stamps[0] if stamps else None
        ended = stamps[-1] if stamps else None
        longest_gap = 0
        for before, after in zip(stamps, stamps[1:]):
            longest_gap = max(longest_gap, after - before)
        tail_gap = None
        if terminal is not None:
            terminal_at = _int(terminal.get("created_at"))
            earlier = [stamp for stamp in stamps if terminal_at and stamp < terminal_at]
            if terminal_at is not None and earlier:
                tail_gap = terminal_at - earlier[-1]
        result = _terminal_result(terminal) if terminal is not None else {}
        error = _dict(result.get("error"))
        detail = _dict(error.get("detail"))
        row: dict[str, Any] = {
            "branch_id": branch_id,
            "turn_id": turn_id,
            "started_at": started,
            "ended_at": ended,
            "duration_ms": (ended - started) if started is not None else None,
            "replies": replies,
            "cells": cells,
            "tool_calls": dict(sorted(tools.items())),
            "malformed_tool_calls": malformed,
            "empty_argument_calls": empty_arguments,
            "tokens": {"input": tokens_in, "output": tokens_out},
            "longest_gap_ms": longest_gap,
            "tail_gap_ms": tail_gap,
            "outcome": _token(result.get("reason"))
            or ("open" if terminal is None else "unknown"),
            "provider": providers.most_common(1)[0][0] if providers else "",
            "model": models.most_common(1)[0][0] if models else "",
        }
        if attempt_states:
            row["cell_attempts"] = dict(sorted(attempt_states.items()))
        if result.get("progress_reason"):
            row["progress_reason"] = _token(result.get("progress_reason"))
        if error:
            row["error_type"] = _token(error.get("type"))
        if detail:
            row["error_detail"] = _failure_detail(detail)
        rows.append(row)

    rows.sort(key=lambda row: (row["started_at"] or 0, row["turn_id"]))
    failures = _failure_messages(messages)
    for index, row in enumerate(rows):
        row["index"] = index + 1
        upper = rows[index + 1]["started_at"] if index + 1 < len(rows) else None
        for failure in failures:
            at = failure["at"]
            if at is None or row["started_at"] is None or at < row["started_at"]:
                continue
            if upper is not None and at >= upper:
                continue
            row["failure"] = {
                key: value
                for key, value in failure.items()
                if key != "at" and value not in ("", False)
            }
    return rows


def _failure_detail(detail: Mapping[str, Any]) -> dict[str, Any]:
    """The bounded, content-free subset of a recorded failure's detail."""

    out: dict[str, Any] = {}
    for key in ("category", "code", "operation", "error_class"):
        if _token(detail.get(key)):
            out[key] = _token(detail.get(key))
    for key in ("status", "attempts"):
        if _int(detail.get(key)) is not None:
            out[key] = _int(detail.get(key))
    for key in ("output_committed", "retryable", "llm_not_started"):
        if isinstance(detail.get(key), bool):
            out[key] = detail[key]
    chain = [_token(item) for item in _list(detail.get("chain")) if _token(item)]
    if chain:
        out["chain"] = chain[:6]
    where = [_token(item) for item in _list(detail.get("where")) if _token(item)]
    if where:
        out["where"] = where[:8]
    call = _dict(detail.get("call"))
    if call:
        out["call"] = _call_summary(call)
    return out


def _call_summary(call: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in (
        "duration_ms",
        "first_delta_ms",
        "last_delta_ms",
        "deltas",
        "attempts",
    ):
        if _int(call.get(key)) is not None:
            out[key] = _int(call.get(key))
    for key in ("stream", "sent"):
        if isinstance(call.get(key), bool):
            out[key] = call[key]
    if _token(call.get("outcome")):
        out["outcome"] = _token(call.get("outcome"))
    return out


# -------------------------------------------------------------------- sections
def _environment_summary(documents: Mapping[str, Any]) -> dict[str, Any]:
    runtime = _dict(documents.get("runtime/environment.json"))
    if runtime:
        return runtime
    # An older package: the artifact environment snapshots still record the
    # platform and interpreter the session's artifacts were produced under.
    environment = _dict(documents.get("environment.json"))
    snapshots = _list(environment.get("artifact_environment_snapshots"))
    platforms = sorted(
        {
            _str(_dict(item).get("platform"))
            for item in snapshots
            if _str(_dict(item).get("platform"))
        }
    )
    return {"derived_from": "artifact_environment_snapshots", "platforms": platforms}


def _cell_summary(cells: list[Any]) -> dict[str, Any]:
    errors: Counter[str] = Counter()
    interrupted = 0
    languages: Counter[str] = Counter()
    wall_max = 0.0
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        languages[_token(cell.get("language")) or "python"] += 1
        if cell.get("interrupted"):
            interrupted += 1
        if cell.get("error"):
            errors[_exception_type(cell.get("error")) or "error"] += 1
        wall = cell.get("wall_s")
        if isinstance(wall, (int, float)) and not isinstance(wall, bool):
            wall_max = max(wall_max, float(wall))
    return {
        "total": sum(languages.values()),
        "languages": dict(sorted(languages.items())),
        "errors": sum(errors.values()),
        "error_types": dict(errors.most_common(10)),
        "interrupted": interrupted,
        "longest_wall_s": round(wall_max, 3),
    }


def _generation_summary(documents: Mapping[str, Any]) -> dict[str, Any]:
    generations = _list(_dict(documents.get("environment.json")).get("generations"))
    frames = _dict(documents.get("runtime/frames.json"))
    generations = generations + _list(frames.get("child_generations"))
    states: Counter[str] = Counter()
    abnormal: Counter[str] = Counter()
    sandboxes: Counter[str] = Counter()
    for generation in generations:
        if not isinstance(generation, dict):
            continue
        state = _token(generation.get("state")) or "unknown"
        states[state] += 1
        if state in ABNORMAL_KERNEL_STATES:
            abnormal[
                f"{_token(generation.get('language')) or 'python'}:"
                f"{_token(generation.get('ended_reason')) or state}"
            ] += 1
        environment = generation.get("environment")
        if isinstance(environment, str):
            try:
                environment = json.loads(environment)
            except ValueError:
                environment = None
        sandbox = _dict(_dict(environment).get("sandbox"))
        if sandbox:
            sandboxes[
                f"{_token(sandbox.get('mode')) or '?'}:"
                f"{_token(sandbox.get('state')) or '?'}"
            ] += 1
    return {
        "total": sum(states.values()),
        "states": dict(sorted(states.items())),
        "abnormal": dict(sorted(abnormal.items())),
        "sandbox": dict(sorted(sandboxes.items())),
    }


def _recovery_summary(documents: Mapping[str, Any]) -> dict[str, Any]:
    journal = _list(_dict(documents.get("snapshots.json")).get("recovery_journal"))
    statuses: Counter[str] = Counter()
    for entry in journal:
        if isinstance(entry, dict):
            statuses[
                f"{_token(entry.get('phase')) or 'phase'}:"
                f"{_token(entry.get('status')) or 'unknown'}"
            ] += 1
    return {
        "entries": sum(statuses.values()),
        "by_phase": dict(sorted(statuses.items())),
    }


def _host_call_summary(documents: Mapping[str, Any]) -> dict[str, Any] | None:
    document = documents.get("runtime/host_calls.json")
    if not isinstance(document, dict):
        return None
    totals = [item for item in _list(document.get("totals")) if isinstance(item, dict)]
    failing = {
        _token(item.get("method"))
        or "unknown": {
            "failed": _int(item.get("failed")) or 0,
            "calls": _int(item.get("calls")) or 0,
        }
        for item in totals
        if (_int(item.get("failed")) or 0) > 0
    }
    return {
        "calls": sum(_int(item.get("calls")) or 0 for item in totals),
        "failed": sum(_int(item.get("failed")) or 0 for item in totals),
        "methods": len(totals),
        "failing_methods": dict(sorted(failing.items())),
        "exported": len(_list(document.get("calls"))),
        "truncated": document.get("truncated") is True,
    }


def _activity_summary(documents: Mapping[str, Any]) -> dict[str, Any] | None:
    document = documents.get("runtime/activity.json")
    if not isinstance(document, dict):
        return None
    failed: Counter[str] = Counter()
    running = 0
    total = 0
    for frame in _list(document.get("frames")):
        for step in _list(_dict(frame).get("steps")):
            if not isinstance(step, dict):
                continue
            total += 1
            status = _str(step.get("status")).casefold()
            if status in FAILED_STEP_STATUSES:
                failed[_token(step.get("kind")) or "step"] += 1
            elif status == "running":
                running += 1
    return {
        "steps": total,
        "failed": sum(failed.values()),
        "failed_kinds": dict(sorted(failed.items())),
        "left_running": running,
    }


def _children_summary(documents: Mapping[str, Any]) -> dict[str, Any] | None:
    document = documents.get("runtime/frames.json")
    if not isinstance(document, dict):
        return None
    children = [
        child
        for child in _list(_dict(document.get("delegation")).get("children"))
        if isinstance(child, dict)
    ]
    statuses: Counter[str] = Counter(
        _token(child.get("status")) or "unknown" for child in children
    )
    stop_reasons: Counter[str] = Counter(
        _token(child.get("stop_reason"))
        for child in children
        if _token(child.get("stop_reason"))
    )
    child_turns = []
    for ledger in _list(document.get("child_ledgers")):
        ledger = _dict(ledger)
        rows = _turn_rows(_list(ledger.get("groups")), [], [])
        for row in rows:
            child_turns.append(
                {
                    "frame_id": _token(ledger.get("frame_id")),
                    "outcome": row["outcome"],
                    "progress_reason": row.get("progress_reason", ""),
                    "replies": row["replies"],
                    "malformed_tool_calls": row["malformed_tool_calls"],
                }
            )
    frames = [
        frame for frame in _list(document.get("frames")) if isinstance(frame, dict)
    ]
    return {
        "frames": len(frames),
        "children": len(children),
        "statuses": dict(sorted(statuses.items())),
        "stop_reasons": dict(sorted(stop_reasons.items())),
        "child_turn_outcomes": dict(
            sorted(Counter(item["outcome"] for item in child_turns).items())
        ),
        "child_turns": child_turns[-50:],
    }


def _permission_summary(documents: Mapping[str, Any]) -> dict[str, Any] | None:
    document = documents.get("runtime/permissions.json")
    if not isinstance(document, dict):
        return None
    states: Counter[str] = Counter()
    by_tool: Counter[str] = Counter()
    for request in _list(document.get("requests")):
        if not isinstance(request, dict):
            continue
        state = _token(request.get("state")) or "unknown"
        states[state] += 1
        if state != "approved" and state != "allowed":
            by_tool[f"{_token(request.get('tool')) or 'tool'}:{state}"] += 1
    return {
        "requests": sum(states.values()),
        "states": dict(sorted(states.items())),
        "not_granted": dict(sorted(by_tool.items())),
    }


def _compaction_summary(documents: Mapping[str, Any]) -> dict[str, Any]:
    document = _dict(documents.get("runtime/compactions.json"))
    archives = [
        item for item in _list(document.get("archives")) if isinstance(item, dict)
    ]
    ledger_compactions = sum(
        1
        for group in _list(_dict(documents.get("ledger.json")).get("groups"))
        if isinstance(group, dict) and group.get("kind") == "compaction"
    )
    return {
        "ledger_compactions": ledger_compactions,
        "archives": len(archives),
        "messages_compacted": sum(
            _int(item.get("n_messages")) or 0 for item in archives
        ),
    }


def _model_call_summary(documents: Mapping[str, Any]) -> dict[str, Any] | None:
    document = documents.get("runtime/model_calls.json")
    if not isinstance(document, dict):
        return None
    calls = [item for item in _list(document.get("calls")) if isinstance(item, dict)]
    outcomes: Counter[str] = Counter(
        _token(call.get("outcome")) or "unknown" for call in calls
    )
    codes: Counter[str] = Counter(
        _token(call.get("failure_code"))
        for call in calls
        if _token(call.get("failure_code"))
    )
    retried = sum(1 for call in calls if (_int(call.get("attempts")) or 0) > 1)
    durations = sorted(
        _int(call.get("duration_ms"))
        for call in calls
        if _int(call.get("duration_ms")) is not None
    )
    first = sorted(
        _int(call.get("first_delta_ms"))
        for call in calls
        if _int(call.get("first_delta_ms")) is not None
    )
    streamed = sum(1 for call in calls if call.get("stream") is True)

    def percentile(values: list[int], fraction: float) -> int | None:
        if not values:
            return None
        return values[min(len(values) - 1, int(round(fraction * (len(values) - 1))))]

    return {
        "calls": len(calls),
        "outcomes": dict(sorted(outcomes.items())),
        "failure_codes": dict(sorted(codes.items())),
        "retried": retried,
        "streamed": streamed,
        "duration_ms": {
            "p50": percentile(durations, 0.5),
            "p95": percentile(durations, 0.95),
            "max": durations[-1] if durations else None,
        },
        "first_delta_ms": {
            "p50": percentile(first, 0.5),
            "max": first[-1] if first else None,
        },
        "total": _int(document.get("total")),
        "truncated": document.get("truncated") is True,
    }


# -------------------------------------------------------------------- findings
def _seconds(ms: Any) -> str:
    value = _int(ms)
    if value is None:
        return "?"
    if value < 1000:
        return f"{value} ms"
    return f"{value / 1000:.1f} s"


def _findings(diagnosis: Mapping[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    environment = _dict(diagnosis.get("environment"))
    llm = _dict(environment.get("llm"))
    read_timeout = llm.get("timeout_s")

    def add(severity: str, kind: str, text: str, **data: Any) -> None:
        findings.append({"severity": severity, "kind": kind, "text": text, **data})

    for row in _list(diagnosis.get("turns")):
        turn = f"turn {row['index']}"
        outcome = row.get("outcome") or "unknown"
        failure = _dict(row.get("failure"))
        detail = _dict(row.get("error_detail"))
        where = (
            f" (request_id {failure['request_id']})"
            if failure.get("request_id")
            else ""
        )
        if outcome in SUCCESSFUL_REASONS:
            pass
        elif outcome in {"llm_stream_timeout", "llm_stream_interrupted"}:
            call = _dict(detail.get("call"))
            if call.get("duration_ms") is not None:
                # The failing call's own clock, when the daemon recorded it:
                # when the first and last deltas arrived, then the silence.
                text = f"{turn} stopped with {outcome}: the model call ran " + _seconds(
                    call.get("duration_ms")
                )
                if call.get("deltas"):
                    text += (
                        f", received {call['deltas']} stream delta(s) (first at "
                        f"{_seconds(call.get('first_delta_ms'))}, last at "
                        f"{_seconds(call.get('last_delta_ms'))})"
                    )
                    last = _int(call.get("last_delta_ms"))
                    total = _int(call.get("duration_ms"))
                    if last is not None and total is not None and total >= last:
                        text += f", then nothing for {_seconds(total - last)}"
                else:
                    text += " without a single stream delta"
                if call.get("attempts"):
                    text += f"; {call['attempts']} send attempt(s)"
            else:
                silence = _seconds(row.get("tail_gap_ms"))
                text = (
                    f"{turn} stopped with {outcome} after {silence} with no recorded "
                    "progress"
                )
            if isinstance(read_timeout, (int, float)) and not isinstance(
                read_timeout, bool
            ):
                text += f" (configured read timeout {read_timeout:g} s)"
            if failure.get("output_committed") or detail.get("output_committed"):
                text += "; output was already committed, so it was not retried"
            add("error", "stream_failure", text + where, turn=row["index"])
        elif outcome == "no_progress":
            repeated = ", ".join(
                f"{name}×{count}"
                for name, count in sorted(
                    row.get("tool_calls", {}).items(), key=lambda item: -item[1]
                )[:3]
            )
            text = (
                f"{turn} stopped by the no-progress circuit "
                f"({row.get('progress_reason') or 'reason not recorded'}) after "
                f"{row['replies']} model replies"
            )
            if repeated:
                text += f"; tool calls {repeated}"
            if row.get("malformed_tool_calls"):
                text += f"; {row['malformed_tool_calls']} with unparseable arguments"
            if row.get("empty_argument_calls"):
                text += f"; {row['empty_argument_calls']} with empty argument strings"
            add("warning", "no_progress", text + where, turn=row["index"])
        elif outcome == "max_turns":
            add(
                "warning",
                "max_turns",
                f"{turn} used its whole step budget ({row['replies']} model replies) "
                "without a structured completion" + where,
                turn=row["index"],
            )
        elif outcome == "cancelled":
            add(
                "info",
                "cancelled",
                f"{turn} was cancelled by the user",
                turn=row["index"],
            )
        elif outcome == "open":
            add(
                "warning",
                "unterminated",
                f"{turn} has no terminal record: the daemon stopped or the export ran "
                "while it was still going",
                turn=row["index"],
            )
        else:
            kind = detail.get("category") or row.get("error_type") or "error"
            text = f"{turn} failed with {outcome} ({kind})"
            if detail.get("where"):
                text += f" at {detail['where'][0]}"
            if detail.get("status"):
                text += f", HTTP {detail['status']}"
            add("error", "turn_failed", text + where, turn=row["index"])
        if outcome != "no_progress" and row.get("malformed_tool_calls"):
            add(
                "warning",
                "malformed_tool_calls",
                f"{turn}: {row['malformed_tool_calls']} tool call(s) arrived with "
                "arguments that did not parse",
                turn=row["index"],
            )
        if (
            outcome not in {"llm_stream_timeout", "llm_stream_interrupted"}
            and (_int(row.get("longest_gap_ms")) or 0) >= 300_000
        ):
            add(
                "info",
                "long_gap",
                f"{turn}: {_seconds(row['longest_gap_ms'])} passed between two "
                "recorded events",
                turn=row["index"],
            )

    cells = _dict(diagnosis.get("cells"))
    if cells.get("errors"):
        kinds = ", ".join(
            f"{name}×{count}" for name, count in cells["error_types"].items()
        )
        add("warning", "cell_errors", f"{cells['errors']} Cell(s) raised: {kinds}")
    if cells.get("interrupted"):
        add(
            "info",
            "cell_interrupts",
            f"{cells['interrupted']} Cell(s) were interrupted",
        )
    kernels = _dict(diagnosis.get("kernel_generations"))
    if kernels.get("abnormal"):
        ended = ", ".join(
            f"{name}×{count}" for name, count in kernels["abnormal"].items()
        )
        add("error", "kernel_ended", f"kernel generation(s) ended abnormally: {ended}")
    unconfined = {
        key: count
        for key, count in _dict(kernels.get("sandbox")).items()
        if key.endswith((":unavailable", ":disabled"))
    }
    if unconfined:
        add(
            "info",
            "sandbox",
            "kernel sandbox (mode:state) "
            + ", ".join(f"{key}×{count}" for key, count in unconfined.items()),
        )
    recovery = _dict(diagnosis.get("recovery"))
    failed_recovery = {
        key: count
        for key, count in _dict(recovery.get("by_phase")).items()
        if key.endswith((":failed", ":partial"))
    }
    if failed_recovery:
        add(
            "warning",
            "recovery",
            "recovery journal records "
            + ", ".join(f"{key}×{count}" for key, count in failed_recovery.items()),
        )
    host = _dict(diagnosis.get("host_calls"))
    if host.get("failed"):
        methods = ", ".join(
            f"{name} {counts['failed']}/{counts['calls']}"
            for name, counts in _dict(host.get("failing_methods")).items()
        )
        add(
            "warning",
            "host_call_failures",
            f"{host['failed']} host call(s) failed: {methods}",
        )
    activity = _dict(diagnosis.get("activity"))
    if activity.get("left_running"):
        add(
            "warning",
            "steps_left_running",
            f"{activity['left_running']} activity card(s) were still 'running' at "
            "export: the step never recorded an end",
        )
    children = _dict(diagnosis.get("children"))
    bad_children = {
        key: count
        for key, count in _dict(children.get("statuses")).items()
        if key in {"failed", "stopped"}
    }
    if bad_children:
        add(
            "warning",
            "delegated_children",
            "delegated children ended "
            + ", ".join(f"{key}×{count}" for key, count in bad_children.items())
            + (
                " (stop reasons: "
                + ", ".join(
                    f"{key}×{count}"
                    for key, count in _dict(children.get("stop_reasons")).items()
                )
                + ")"
                if children.get("stop_reasons")
                else ""
            ),
        )
    child_outcomes = {
        key: count
        for key, count in _dict(children.get("child_turn_outcomes")).items()
        if key not in SUCCESSFUL_REASONS
    }
    if child_outcomes:
        add(
            "warning",
            "delegated_child_turns",
            "delegated child turns ended "
            + ", ".join(f"{key}×{count}" for key, count in child_outcomes.items())
            + " (their ledgers are in runtime/frames.json)",
        )
    permissions = _dict(diagnosis.get("permissions"))
    if permissions.get("not_granted"):
        add(
            "info",
            "permissions",
            "permission requests not granted: "
            + ", ".join(
                f"{key}×{count}" for key, count in permissions["not_granted"].items()
            ),
        )
    model_calls = _dict(diagnosis.get("model_calls"))
    if model_calls.get("failure_codes"):
        add(
            "warning",
            "model_call_failures",
            "model calls failed: "
            + ", ".join(
                f"{key}×{count}" for key, count in model_calls["failure_codes"].items()
            ),
        )
    if model_calls.get("retried"):
        add(
            "info",
            "model_call_retries",
            f"{model_calls['retried']} model call(s) needed more than one attempt",
        )
    capabilities = _dict(llm.get("capabilities"))
    if capabilities.get("local_endpoint") and capabilities.get("tool_calling") is False:
        add(
            "info",
            "local_endpoint",
            "the model endpoint is local/private, so native tool calls are off unless "
            "a capability override enables them; the model works through code cells",
        )
    if llm.get("stream") is False:
        add("info", "streaming_off", "streaming is disabled (OPENAI4S_LLM_STREAM)")
    posture = _dict(environment.get("posture"))
    if _str(posture.get("kernel_sandbox")) == "off":
        add("info", "sandbox_off", "the kernel sandbox is turned off")
    findings.sort(
        key=lambda item: (
            _SEVERITY_ORDER.get(item["severity"], 3),
            _int(item.get("turn")) or 0,
        )
    )
    return findings


# ------------------------------------------------------------------ diagnosis
def diagnose(documents: Mapping[str, Any]) -> dict[str, Any]:
    """A bounded, content-free diagnosis of one package's runtime behaviour."""

    session = _dict(documents.get("session.json"))
    ledger = _dict(documents.get("ledger.json"))
    notebook = _dict(documents.get("notebook.json"))
    manifest = _dict(documents.get("manifest.json"))
    messages = _list(session.get("messages"))
    turns = _turn_rows(
        _list(ledger.get("groups")),
        _list(ledger.get("execution_attempts")),
        messages,
    )
    diagnosis: dict[str, Any] = {
        "schema_version": DIAGNOSIS_SCHEMA_VERSION,
        "package": {
            "format": _token(manifest.get("format")) or "openai4s.session",
            "schema_version": _int(manifest.get("schema_version")),
            "source_session": _token(_dict(session.get("source")).get("root_frame_id")),
            "runtime_evidence": isinstance(
                documents.get("runtime/environment.json"), dict
            ),
        },
        "environment": _environment_summary(documents),
        "counts": {
            "messages": len(messages),
            "turns": len(turns),
            "failed_turns": sum(
                1
                for row in turns
                if row["outcome"] not in SUCCESSFUL_REASONS
                and row["outcome"] not in {"cancelled"}
            ),
        },
        "turns": turns,
        "cells": _cell_summary(_list(notebook.get("cells"))),
        "kernel_generations": _generation_summary(documents),
        "recovery": _recovery_summary(documents),
        "compactions": _compaction_summary(documents),
    }
    for key, section in (
        ("host_calls", _host_call_summary(documents)),
        ("activity", _activity_summary(documents)),
        ("children", _children_summary(documents)),
        ("permissions", _permission_summary(documents)),
        ("model_calls", _model_call_summary(documents)),
    ):
        if section is not None:
            diagnosis[key] = section
    collection = documents.get("runtime/collection.json")
    if isinstance(collection, dict):
        diagnosis["collection"] = collection
    diagnosis["findings"] = _findings(diagnosis)
    return diagnosis


# ------------------------------------------------------------------ rendering
def _when(ms: Any) -> str:
    value = _int(ms)
    if value is None:
        return "?"
    import datetime as _datetime

    stamp = _datetime.datetime.fromtimestamp(value / 1000, tz=_datetime.timezone.utc)
    return stamp.strftime("%Y-%m-%d %H:%M:%S") + "Z"


def _environment_lines(environment: Mapping[str, Any]) -> list[str]:
    if environment.get("derived_from"):
        platforms = ", ".join(_list(environment.get("platforms"))) or "not recorded"
        return [
            "- This package predates runtime evidence; the only host facts are the "
            f"artifact environment platforms: {platforms}."
        ]
    lines = []
    openai4s = _dict(environment.get("openai4s"))
    python = _dict(environment.get("python"))
    host = _dict(environment.get("platform"))
    parts = [
        f"OpenAI4S {openai4s.get('version') or '?'}"
        + (f" ({openai4s['channel']})" if openai4s.get("channel") else ""),
        f"Python {python.get('version') or '?'}",
        " ".join(
            str(item)
            for item in (host.get("system"), host.get("release"), host.get("machine"))
            if item
        )
        or "platform ?",
    ]
    if host.get("wsl"):
        parts.append("WSL")
    if host.get("container"):
        parts.append("container")
    lines.append("- Host: " + " · ".join(parts))
    llm = _dict(environment.get("llm"))
    if not llm:
        lines.append("- Model: not recorded by this export")
    elif llm.get("status") == "unavailable":
        lines.append(
            f"- Model: not resolvable at export ({llm.get('reason') or 'unknown'})"
        )
    else:
        endpoint = _dict(llm.get("endpoint"))
        endpoint_text = endpoint.get("class") or "?"
        if endpoint.get("provider_default"):
            endpoint_text = "the provider's default endpoint"
        elif endpoint.get("fingerprint"):
            endpoint_text += f" (fingerprint {endpoint['fingerprint']})"
        lines.append(
            f"- Model: `{llm.get('model') or '?'}` via provider `{llm.get('provider') or '?'}` "
            f"(wire {llm.get('wire') or '?'}), streaming "
            f"{'on' if llm.get('stream') else 'off'}, read timeout "
            f"{llm.get('timeout_s', '?')} s, total {llm.get('total_timeout_s', '?')} s, "
            f"max output {llm.get('max_tokens', '?')} tokens; endpoint: {endpoint_text}"
        )
        capabilities = _dict(llm.get("capabilities"))
        if capabilities:
            flags = [
                f"{name} {'on' if capabilities.get(name) else 'off'}"
                for name in (
                    "tool_calling",
                    "parallel_tool_calls",
                    "streaming",
                    "vision",
                )
                if name in capabilities
            ]
            window = capabilities.get("context_window_tokens")
            if window:
                flags.append(f"context {window} tokens")
            lines.append("- Effective model capabilities: " + ", ".join(flags))
        receipt = _dict(llm.get("capability_receipt"))
        if receipt:
            lines.append(
                "- Capability probe receipt: native tool call "
                f"{receipt.get('native_tool_call', '?')}, streaming "
                f"{receipt.get('streaming', '?')}, observed {_when(receipt.get('observed_at'))}"
            )
    # `name: value`, never `NAME=value`: this page passes the same secret
    # scrubber as the package, and `SECRET_STORE=...` is exactly the shape of
    # an environment assignment it redacts.
    posture = _dict(environment.get("posture"))
    changed = {key: value for key, value in posture.items() if value != "(default)"}
    if posture:
        lines.append(
            "- Posture: "
            + (
                ", ".join(f"{key}: {value}" for key, value in sorted(changed.items()))
                if changed
                else "every knob at its default"
            )
        )
    agent = _dict(environment.get("agent"))
    if agent:
        lines.append(
            "- Agent limits: "
            + ", ".join(
                f"{key}: {value}"
                for key, value in sorted(agent.items())
                if value is not None
            )
        )
    return lines


def render_markdown(diagnosis: Mapping[str, Any]) -> str:
    """``DIAGNOSTICS.md``: the page a maintainer reads first."""

    lines = [
        "# Runtime diagnosis",
        "",
        "Derived from this package's own records by `openai4s inspect-package`.",
        "It reproduces no message, code, output, path or URL text: only counts,",
        "recorded codes, tool and method names, exception type names, and",
        "durations between recorded timestamps. It does name the model and",
        "provider the session used.",
        "",
        "## Where it ran",
        "",
        *_environment_lines(_dict(diagnosis.get("environment"))),
        "",
        "## What the records show",
        "",
    ]
    findings = _list(diagnosis.get("findings"))
    if findings:
        for index, finding in enumerate(findings, start=1):
            lines.append(f"{index}. **{finding['severity']}** — {finding['text']}")
    else:
        lines.append("Nothing abnormal is recorded: every turn ended normally.")
    turns = _list(diagnosis.get("turns"))
    lines += ["", "## Turns", ""]
    if turns:
        lines += [
            "| # | started (UTC) | duration | replies | tools | cells | tokens in/out | outcome |",
            "|---|---|---|---|---|---|---|---|",
        ]
        shown = turns[-60:]
        if len(turns) > len(shown):
            lines.insert(
                len(lines) - 2,
                f"_Showing the last {len(shown)} of {len(turns)} turns._",
            )
        for row in shown:
            tools = sum(_dict(row.get("tool_calls")).values())
            outcome = row.get("outcome") or "?"
            if row.get("progress_reason"):
                outcome += f" ({row['progress_reason']})"
            tokens = _dict(row.get("tokens"))
            lines.append(
                f"| {row['index']} | {_when(row.get('started_at'))} | "
                f"{_seconds(row.get('duration_ms'))} | {row['replies']} | {tools} | "
                f"{row['cells']} | {tokens.get('input', 0)}/{tokens.get('output', 0)} | "
                f"{outcome} |"
            )
    else:
        lines.append("The ledger records no turns.")
    lines += ["", "## Other signals", ""]
    cells = _dict(diagnosis.get("cells"))
    lines.append(
        f"- Cells: {cells.get('total', 0)} run, {cells.get('errors', 0)} raised, "
        f"{cells.get('interrupted', 0)} interrupted; longest "
        f"{cells.get('longest_wall_s', 0)} s"
    )
    kernels = _dict(diagnosis.get("kernel_generations"))
    lines.append(
        f"- Kernel generations: {kernels.get('total', 0)} "
        + (
            "("
            + ", ".join(
                f"{key} {value}" for key, value in _dict(kernels.get("states")).items()
            )
            + ")"
            if kernels.get("states")
            else ""
        )
    )
    for key, label in (
        ("host_calls", "Host calls"),
        ("activity", "Activity cards"),
        ("children", "Delegated children"),
        ("permissions", "Permission requests"),
        ("model_calls", "Model calls"),
        ("compactions", "Compactions"),
    ):
        section = diagnosis.get(key)
        if isinstance(section, dict):
            summary = ", ".join(
                (
                    f"{name} {json.dumps(value, ensure_ascii=False, sort_keys=True)}"
                    if isinstance(value, (dict, list))
                    else f"{name} {value}"
                )
                for name, value in section.items()
                if name not in {"child_turns"} and value not in (None, {}, [], 0, False)
            )
            lines.append(f"- {label}: {summary or 'none'}")
    collection = _dict(diagnosis.get("collection"))
    missing = {
        name: status
        for name, status in _dict(collection.get("sections")).items()
        if _dict(status).get("status") not in (None, "ok")
    }
    if missing:
        lines += ["", "## Evidence this package could not carry", ""]
        for name, status in sorted(missing.items()):
            status = _dict(status)
            lines.append(
                f"- `{name}`: {status.get('status')}"
                + (f" ({status['reason']})" if status.get("reason") else "")
            )
    lines += [
        "",
        "## Where to look next",
        "",
        "- `ledger.json` — every model reply, tool call (raw and parsed arguments),",
        "  tool result and each turn's terminal event (`reason`, `progress_reason`,",
        "  `error.detail`).",
        "- `session.json` — the conversation; a failed turn's last message carries",
        "  `metadata.failure` with the `request_id` the daemon log is keyed by.",
        "- `notebook.json` — every Cell with stdout, stderr, error and timings.",
        "- `runtime/` — the host environment, model endpoint and capabilities,",
        "  delegated children and their ledgers, activity cards, host calls,",
        "  permission requests, compactions and per-call model telemetry.",
        "",
    ]
    return "\n".join(lines)


def diagnose_package(path: str | Path) -> dict[str, Any]:
    """Read and diagnose one package file."""

    return diagnose(read_package(path))


def iter_problem_lines(diagnosis: Mapping[str, Any]) -> Iterable[str]:
    """One line per finding, for terminals and logs."""

    for finding in _list(diagnosis.get("findings")):
        yield f"[{finding['severity']}] {finding['text']}"


__all__ = [
    "DIAGNOSIS_SCHEMA_VERSION",
    "DOCUMENTS",
    "PackageReadError",
    "diagnose",
    "diagnose_package",
    "iter_problem_lines",
    "read_package",
    "render_markdown",
]
