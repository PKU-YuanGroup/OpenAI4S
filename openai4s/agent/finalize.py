"""Engine-owned structured finalization for non-scientific turns.

``finalize_response`` is deliberately not a control-plane ``Tool`` and is
never registered in :mod:`openai4s.tools.registry`.  It is a terminal action
understood by the agent engine: providers receive a metadata-only ``ToolSpec``
and the Host validates the same closed schema again before accepting the
completion.

Scientific execution keeps its existing, stronger cell-completion contract:
``host.submit_output(...)`` remains the only completion signal emitted from a
Python Code-as-Action cell.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Callable, Iterable, Mapping, MutableMapping, TypedDict

from openai4s.host.completion import first_english_word, validate_completion_bullets
from openai4s.tools import ToolSpec, finalize_tool_batch, validate_json_schema

from .actions import FINALIZE_RESPONSE_NAME, FinalizeAction, NativeToolCall
from .models import ExecutionOutcome


class CompletionRecord(TypedDict):
    """The completion payload shared with ``host.submit_output`` consumers."""

    output: dict[str, Any]
    completion_bullets: list[str]


class ExecutionEvidence(TypedDict):
    """What this run has actually executed, counted at the execution sites."""

    cells: int
    tool_calls: int


#: ``RunState.metadata`` key under which executors accumulate the evidence.
EXECUTION_EVIDENCE_KEY = "execution_evidence"

#: Past-tense verbs that assert executed work: computation, real I/O, or a
#: produced file. A deliberately conservative core set — a verb belongs here
#: only when a bullet starting with it is dishonest without a cell or tool run
#: backing it. Reflective verbs (``explained``, ``answered``, ``summarised``,
#: ``compared`` …) stay out: they describe reasoning over what is already in
#: context, which needs no execution.
_EXECUTION_CLAIM_STARTERS = frozenset(
    {
        "aligned",
        "benchmarked",
        "built",
        "calculated",
        "computed",
        "converted",
        "created",
        "downloaded",
        "executed",
        "exported",
        "fetched",
        "fitted",
        "folded",
        "generated",
        "installed",
        "measured",
        "plotted",
        "produced",
        "profiled",
        "queried",
        "ran",
        "rendered",
        "reran",
        "retrieved",
        "saved",
        "simulated",
        "trained",
        "uploaded",
        "wrote",
    }
)


def note_execution_evidence(
    metadata: MutableMapping[str, Any], *, cells: int = 0, tool_calls: int = 0
) -> None:
    """Record that this run really dispatched a cell or native tool batch.

    Executors call this at the execution site — after the kernel or dispatcher
    ran, never for a safety-gate refusal — so the finalize-time reconciliation
    reads what happened rather than what was attempted.
    """

    current = metadata.get(EXECUTION_EVIDENCE_KEY)
    if not isinstance(current, dict):
        current = {"cells": 0, "tool_calls": 0}
        metadata[EXECUTION_EVIDENCE_KEY] = current
    current["cells"] = _count(current.get("cells")) + cells
    current["tool_calls"] = _count(current.get("tool_calls")) + tool_calls


def execution_evidence(metadata: Mapping[str, Any]) -> ExecutionEvidence:
    """The run's accumulated evidence; zero counts when nothing was recorded."""

    current = metadata.get(EXECUTION_EVIDENCE_KEY)
    if not isinstance(current, Mapping):
        return {"cells": 0, "tool_calls": 0}
    return {
        "cells": _count(current.get("cells")),
        "tool_calls": _count(current.get("tool_calls")),
    }


def _count(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def reconcile_completion_claims(
    arguments: Mapping[str, Any], evidence: Mapping[str, Any]
) -> str | None:
    """Reject completion payloads whose claims outrun the turn's ledger.

    A run that executed nothing — no code cell, no native tool — cannot have
    computed metrics, produced artifacts, or completed an execution-shaped
    action. Accepting such a payload publishes provenance that is wrong rather
    than absent, so it is refused as a repairable validation error: the model
    can perform the work and finalize again, or restate the completion in
    non-execution terms.

    The check is conservative on purpose: any recorded cell or tool call
    satisfies it, and CJK bullets (no tense morphology) are never flagged.
    """

    if _count(evidence.get("cells")) > 0 or _count(evidence.get("tool_calls")) > 0:
        return None
    claims: list[str] = []
    bullets = arguments.get("completion_bullets")
    if isinstance(bullets, list):
        flagged = [
            str(bullet)
            for bullet in bullets
            if first_english_word(bullet) in _EXECUTION_CLAIM_STARTERS
        ]
        if flagged:
            claims.append(
                "completion bullets claim executed work ("
                + "; ".join(repr(item) for item in flagged)
                + ")"
            )
    if arguments.get("artifacts"):
        claims.append("'artifacts' names produced files")
    if arguments.get("metrics"):
        claims.append("'metrics' reports measured values")
    if not claims:
        return None
    return (
        "completion claims are not backed by this run's ledger: "
        + "; ".join(claims)
        + ". No code cell and no tool ran this turn. Either perform the "
        "claimed work first (run a cell or call a tool), or restate the "
        "completion without execution claims — e.g. 'Explained…' or "
        "'Answered…' bullets, and omit artifacts/metrics."
    )


_TEXT_ITEM = {"type": "string", "minLength": 1, "maxLength": 2_000}
_FINALIZE_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "minLength": 1,
            "maxLength": 4_000,
            "description": "A concise answer grounded in work that actually completed.",
        },
        "findings": {
            "type": "array",
            "items": _TEXT_ITEM,
            "maxItems": 50,
            "description": "Optional evidence-backed findings.",
        },
        "metrics": {
            "type": "object",
            "additionalProperties": {"type": "number"},
            "description": "Optional finite numeric metrics keyed by stable names.",
        },
        "artifacts": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 1_000},
            "maxItems": 100,
            "description": "Optional artifact IDs, version IDs, or workspace paths.",
        },
        "limitations": {
            "type": "array",
            "items": _TEXT_ITEM,
            "maxItems": 50,
            "description": "Optional limitations or unresolved uncertainty.",
        },
        "next_steps": {
            "type": "array",
            "items": _TEXT_ITEM,
            "maxItems": 20,
            "description": "Optional concrete follow-up steps.",
        },
        "completion_bullets": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 500},
            "minItems": 1,
            "maxItems": 4,
            "description": "One to four completed-action phrases.",
        },
        "source_files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1, "maxLength": 1_000},
                    "sha256": {"type": "string", "minLength": 64, "maxLength": 64},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            "maxItems": 200,
            "description": (
                "Source files this run saved. Required and verified for the "
                "reusable_pipeline and codebase_change task modes."
            ),
        },
        "entry_points": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 1_000},
            "maxItems": 20,
            "description": (
                "Runnable entry-point paths. Python entries must compile from "
                "their own source; they are never executed to check that."
            ),
        },
        "architecture_summary": {
            "type": "string",
            "minLength": 1,
            "maxLength": 4_000,
            "description": "One paragraph naming what each module owns.",
        },
        "test_evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "minLength": 1, "maxLength": 1_000},
                    "producing_cell_id": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 200,
                    },
                },
                "required": ["command", "producing_cell_id"],
                "additionalProperties": False,
            },
            "maxItems": 50,
            "description": (
                "Test commands and the id of the cell that ran each. There is "
                "deliberately no field for the output: pass/fail is read off "
                "the recorded stdout of that cell, never from a claim."
            ),
        },
        "task_status": {
            "type": "string",
            "enum": ["completed", "partial", "blocked", "failed"],
            "description": (
                "Optional honest machine-readable completion status; omitted "
                "means completed. Declare partial/blocked/failed instead of "
                "dressing an incomplete task as done."
            ),
        },
    },
    "required": ["summary", "completion_bullets"],
    "additionalProperties": False,
}


def finalize_response_schema() -> dict[str, Any]:
    """Return an isolated copy of the Host-enforced completion schema."""

    return copy.deepcopy(_FINALIZE_RESPONSE_SCHEMA)


def finalize_response_tool_spec() -> ToolSpec:
    """Return the provider-neutral terminal declaration.

    The schema is closed and is strictly revalidated by the Host.  Wire-level
    ``strict`` stays false because the portable strict subset requires every
    declared property to be required, which would make the structured fields
    that are intentionally optional impossible to omit.
    """

    return ToolSpec(
        name=FINALIZE_RESPONSE_NAME,
        description=(
            "Finish the current response with a structured, evidence-grounded "
            "completion. Call this only when it is the sole tool call in the "
            "assistant turn. It does not replace host.submit_output for a "
            "Python scientific cell."
        ),
        input_schema=finalize_response_schema(),
        strict=False,
    )


def with_finalize_response(tools: Iterable[Any]) -> tuple[Any, ...]:
    """Append the engine terminal declaration to a provider tool catalogue."""

    values = tuple(tools)
    names = {_tool_spec_name(tool) for tool in values}
    if FINALIZE_RESPONSE_NAME in names:
        raise ValueError(
            "finalize_response is engine-owned and cannot be supplied by a tool registry"
        )
    return (*values, finalize_response_tool_spec())


def _tool_spec_name(tool: Any) -> str:
    if not isinstance(tool, Mapping):
        return str(getattr(tool, "name", "") or "")
    function = tool.get("function")
    source = function if isinstance(function, Mapping) else tool
    return str(source.get("name") or "")


def validate_finalize_arguments(arguments: Any) -> str | None:
    """Host-side validation for one provider-originated terminal payload."""

    issues = validate_json_schema(
        arguments,
        _FINALIZE_RESPONSE_SCHEMA,
        unknown_properties="forbid",
    )
    if issues:
        return "invalid arguments: " + "; ".join(str(issue) for issue in issues)
    # Preserve parity with the in-kernel completion contract: JSON Schema can
    # express the cardinality and non-empty strings, while this semantic guard
    # verifies that the bullets describe completed work.
    bullet_error = validate_completion_bullets(arguments["completion_bullets"])
    return str(bullet_error) if bullet_error is not None else None


def _completion_record(arguments: Mapping[str, Any]) -> CompletionRecord:
    """Build the existing renderer-compatible completion envelope."""

    payload = copy.deepcopy(dict(arguments))
    bullets = list(payload.pop("completion_bullets"))
    return {
        "output": payload,
        "completion_bullets": bullets,
    }


def execute_finalize_action(
    action: FinalizeAction,
    *,
    refusal: str | None = None,
    stop_reason: str | None = None,
    evidence: Mapping[str, Any] | None = None,
    code_evidence: Callable[[Mapping[str, Any]], str | None] | None = None,
) -> ExecutionOutcome:
    """Close the provider call, then optionally accept structured completion.

    Even malformed, cancelled, or plan-mode declarations produce exactly one
    canonical provider tool result.  A validation failure is observable but is
    never a completion signal, allowing the model to repair it next turn.

    ``evidence`` is the run's execution ledger (see ``execution_evidence``);
    when supplied, completion claims are reconciled against it and a payload
    claiming unexecuted work is refused. ``None`` preserves the legacy
    behaviour for callers that keep no ledger.

    ``code_evidence`` is the Host's code-mode check (see
    ``openai4s.host.code_evidence``), bound by the executor to the dispatcher
    that knows this turn's task mode. It receives the accepted arguments and
    returns a refusal string or ``None``. Omitted, the finalize contract is
    exactly what it was — which is also what an ``analysis_run`` turn gets.
    """

    call = action.call
    error = refusal or _call_error(call)
    record: CompletionRecord | None = None
    if error is None:
        error = validate_finalize_arguments(call.arguments)
    if error is None and evidence is not None:
        assert call.arguments is not None
        error = reconcile_completion_claims(call.arguments, evidence)
    if error is None and code_evidence is not None:
        assert call.arguments is not None
        error = code_evidence(call.arguments)
    if error is None:
        assert call.arguments is not None
        record = _completion_record(call.arguments)
        text = json.dumps(
            {"status": "accepted", "action": FINALIZE_RESPONSE_NAME},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    else:
        text = f"[Tool error] {FINALIZE_RESPONSE_NAME}: {error}"

    result = {
        "role": "tool",
        "tool_call_id": call.id,
        "wire_id": call.wire_id,
        "name": FINALIZE_RESPONSE_NAME,
        "content": text,
        "is_error": error is not None,
    }
    return ExecutionOutcome(
        (result,),
        observation=finalize_tool_batch([text], 1, []),
        completion=record,
        stop_reason=stop_reason,
    )


def _call_error(call: NativeToolCall) -> str | None:
    if call.name != FINALIZE_RESPONSE_NAME:
        return f"unexpected terminal action name {call.name!r}"
    if call.parse_error is not None:
        return call.parse_error
    if call.arguments is None:
        return "arguments are not a JSON object"
    return None


__all__ = [
    "CompletionRecord",
    "EXECUTION_EVIDENCE_KEY",
    "ExecutionEvidence",
    "execute_finalize_action",
    "execution_evidence",
    "finalize_response_schema",
    "finalize_response_tool_spec",
    "note_execution_evidence",
    "reconcile_completion_claims",
    "validate_finalize_arguments",
    "with_finalize_response",
]
