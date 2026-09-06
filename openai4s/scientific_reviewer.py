"""Stage 3 Scientific Reviewer V2: independent, schema-strict, tool-free.

The Reviewer sees only a frozen Evidence Snapshot. It never receives the main
Agent's hidden reasoning, never writes the formal workspace, and cannot pass
when the snapshot declares an omission. Production callers inject ``chat_call``
for tests; the default is the same provider-neutral ``chat()`` used elsewhere.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any, Callable

from openai4s.config import LLMConfig
from openai4s.llm import chat
from openai4s.review import ReviewError, _json_object

REVIEWER_V2_SYSTEM_PROMPT = """You are the independent Scientific Reviewer for a research agent.
Audit only the frozen Evidence Snapshot supplied by the host. Do not invent
missing evidence. Do not treat a filename as proof of file contents. Do not
read hidden agent reasoning: it is not present.

Return one JSON object and no prose:
{
  "verdict": "pass" | "issues" | "incomplete",
  "summary": "short user-facing summary",
  "findings": [
    {
      "severity": "high" | "medium" | "low",
      "category": "claim_mismatch" | "missing_artifact" | "evidence_incomplete" | "provenance" | "other",
      "claim_ref": "quoted claim or snapshot field",
      "evidence_refs": ["ref_id from the snapshot only"],
      "reproduction": "how to re-check from the snapshot or scratch",
      "suggested_fix": "narrow repair the Repair Agent could attempt",
      "confidence": 0.0
    }
  ]
}

Use verdict=pass only when the snapshot is complete and there are no material
issues. If the snapshot complete flag is false, or omitted_artifact_count is
non-zero, or any required adapter is incomplete, verdict must be incomplete
and must not be pass. Limit findings to the most important 8.
"""

_VERDICTS = frozenset({"pass", "issues", "incomplete"})
_SEVERITIES = frozenset({"high", "medium", "low"})
_CATEGORIES = frozenset(
    {
        "claim_mismatch",
        "missing_artifact",
        "evidence_incomplete",
        "provenance",
        "other",
    }
)
_PACKET_LIMIT = 80_000
_PACKET_STRING_LIMIT = 4_000
_PACKET_ITEM_LIMIT = 24
_PACKET_DEPTH_LIMIT = 6
_PACKET_NODE_LIMIT = 256


def canonical_digest(value: Any) -> str:
    """Stable SHA-256 of a JSON-canonical value."""

    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def model_fingerprint(provider: str, base_url: str, model: str) -> str:
    """Freeze the exact provider/endpoint/model triple used for a review."""

    return canonical_digest(
        {
            "provider": str(provider or "").strip().lower(),
            "base_url": str(base_url or "").strip(),
            "model": str(model or "").strip(),
        }
    )


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _clean_finding(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    severity = str(value.get("severity") or "medium").lower().strip()
    if severity not in _SEVERITIES:
        severity = "medium"
    category = str(value.get("category") or "other").lower().strip()
    if category not in _CATEGORIES:
        category = "other"
    claim_ref = _clip(value.get("claim_ref") or value.get("claim") or "", 800)
    detail = _clip(value.get("detail") or value.get("reproduction") or "", 2400)
    reproduction = _clip(value.get("reproduction") or detail, 1600)
    suggested = _clip(value.get("suggested_fix") or "", 800)
    refs_raw = value.get("evidence_refs") or []
    evidence_refs = []
    if isinstance(refs_raw, (list, tuple)):
        for item in refs_raw:
            ref = _clip(item, 256)
            if ref and ref not in evidence_refs:
                evidence_refs.append(ref)
    if not claim_ref and not reproduction and not evidence_refs:
        return None
    try:
        confidence = float(value.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    if confidence != confidence:  # NaN
        confidence = 0.5
    confidence = min(1.0, max(0.0, confidence))
    finding = {
        "severity": severity,
        "category": category,
        "claim_ref": claim_ref or "unspecified claim",
        "evidence_refs": evidence_refs,
        "reproduction": reproduction,
        "suggested_fix": suggested,
        "confidence": confidence,
    }
    return finding


def normalize_review_v2(
    value: dict[str, Any],
    *,
    snapshot_complete: bool,
) -> dict[str, Any]:
    """Normalize a V2 reviewer object and refuse a pass on an incomplete snapshot."""

    raw_findings = value.get("findings") or value.get("issues") or []
    findings: list[dict[str, Any]] = []
    if isinstance(raw_findings, (list, tuple)):
        for raw in raw_findings:
            finding = _clean_finding(raw)
            if finding:
                findings.append(finding)
            if len(findings) >= 8:
                break
    verdict = str(value.get("verdict") or "").lower().strip()
    material = [item for item in findings if item["severity"] in {"high", "medium"}]
    if not snapshot_complete:
        verdict = "incomplete"
    elif material:
        verdict = "issues"
    elif findings and verdict == "pass":
        verdict = "issues"
    elif raw_findings and not findings:
        raise ReviewError("reviewer returned findings with no usable evidence")
    elif verdict == "issues" and not findings:
        raise ReviewError("reviewer issues verdict contained no usable findings")
    elif verdict == "incomplete" and snapshot_complete and not findings:
        raise ReviewError("incomplete verdict requires an omission or finding")
    elif verdict not in _VERDICTS:
        raise ReviewError("reviewer verdict must be pass, issues, or incomplete")
    summary = _clip(value.get("summary") or "", 320)
    if verdict == "pass":
        summary = "No issues found"
    elif not summary:
        if verdict == "incomplete":
            summary = "Evidence snapshot is incomplete"
        else:
            summary = (
                f"{len(findings)} finding{'s' if len(findings) != 1 else ''} found"
            )
    return {"verdict": verdict, "summary": summary, "findings": findings}


def _bounded_packet_value(
    value: Any,
    *,
    depth: int,
    remaining_nodes: list[int],
) -> tuple[Any, bool]:
    """Make one JSON-safe bounded copy without slicing serialized JSON."""

    if remaining_nodes[0] <= 0:
        return "[host omitted value from reviewer packet]", True
    remaining_nodes[0] -= 1
    if value is None or isinstance(value, (bool, int)):
        return value, False
    if isinstance(value, float):
        if math.isfinite(value):
            return value, False
        return str(value), True
    if isinstance(value, str):
        if len(value) <= _PACKET_STRING_LIMIT:
            return value, False
        return value[:_PACKET_STRING_LIMIT] + "[host truncated value]", True
    if depth >= _PACKET_DEPTH_LIMIT:
        return "[host omitted nested value from reviewer packet]", True
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        truncated = False
        for index, (raw_key, item) in enumerate(value.items()):
            if index >= _PACKET_ITEM_LIMIT or remaining_nodes[0] <= 0:
                truncated = True
                break
            key = str(raw_key)
            if len(key) > 256:
                key = key[:256] + "[host truncated key]"
                truncated = True
            bounded, child_truncated = _bounded_packet_value(
                item,
                depth=depth + 1,
                remaining_nodes=remaining_nodes,
            )
            # A clipped key can collide with an earlier key. Keep both visible
            # without pretending that the representation is complete.
            while key in result:
                key += "#"
                truncated = True
            result[key] = bounded
            truncated = truncated or child_truncated
        return result, truncated
    if isinstance(value, (list, tuple)):
        result_list: list[Any] = []
        truncated = False
        for index, item in enumerate(value):
            if index >= _PACKET_ITEM_LIMIT or remaining_nodes[0] <= 0:
                truncated = True
                break
            bounded, child_truncated = _bounded_packet_value(
                item,
                depth=depth + 1,
                remaining_nodes=remaining_nodes,
            )
            result_list.append(bounded)
            truncated = truncated or child_truncated
        return result_list, truncated
    text = str(value)
    if len(text) > _PACKET_STRING_LIMIT:
        text = text[:_PACKET_STRING_LIMIT] + "[host truncated value]"
    return text, True


def _minimal_incomplete_packet(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Return a fixed-size valid envelope when the richer compact form is large."""

    raw_identity = snapshot.get("identity")
    identity = raw_identity if isinstance(raw_identity, Mapping) else {}
    raw_omitted_count = snapshot.get("omitted_artifact_count")
    omitted_count = (
        raw_omitted_count
        if isinstance(raw_omitted_count, int)
        and not isinstance(raw_omitted_count, bool)
        else 0
    )
    return {
        "schema_version": _clip(snapshot.get("schema_version"), 64),
        "frozen": snapshot.get("frozen") is True,
        "identity": {
            key: _clip(identity.get(key), 160)
            for key in ("root_frame_id", "branch_id", "turn_id", "execution_id")
        },
        "complete": False,
        "omitted_artifact_count": omitted_count,
        "truncation": {"reviewer_packet": True},
        "omissions": [
            {"kind": "truncated", "fields": ["reviewer_packet"]},
        ],
        "snapshot_sha256": _clip(snapshot.get("snapshot_sha256"), 128),
        "host_note": "Reviewer packet was structurally truncated by the host",
    }


def _snapshot_packet(snapshot: dict[str, Any]) -> tuple[str, bool]:
    """Serialize a valid reviewer packet and report whether it is complete."""

    try:
        packet = json.dumps(
            snapshot,
            ensure_ascii=False,
            default=str,
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        packet = ""
    if packet and len(packet) <= _PACKET_LIMIT:
        return packet, True

    priority = (
        "schema_version",
        "frozen",
        "hidden_reasoning_excluded",
        "identity",
        "complete",
        "omitted_artifact_count",
        "truncation",
        "omissions",
        "evidence_refs",
        "adapters",
        "artifacts",
        "user_request",
        "candidate_answer",
        "structured_completion",
        "cells",
        "tool_ledger",
        "lineage",
        "plan",
        "environment",
        "source_metadata",
        "snapshot_sha256",
    )
    ordered = {key: snapshot[key] for key in priority if key in snapshot}
    for key, value in snapshot.items():
        if key not in ordered:
            ordered[key] = value
    compact_value, _truncated = _bounded_packet_value(
        ordered,
        depth=0,
        remaining_nodes=[_PACKET_NODE_LIMIT],
    )
    compact = compact_value if isinstance(compact_value, dict) else {}
    compact["complete"] = False
    truncation = compact.get("truncation")
    compact["truncation"] = dict(truncation) if isinstance(truncation, Mapping) else {}
    compact["truncation"]["reviewer_packet"] = True
    omissions = compact.get("omissions")
    compact["omissions"] = list(omissions) if isinstance(omissions, list) else []
    compact["omissions"].append({"kind": "truncated", "fields": ["reviewer_packet"]})
    compact["host_note"] = "Reviewer packet was structurally truncated by the host"
    packet = json.dumps(
        compact,
        ensure_ascii=False,
        default=str,
        sort_keys=True,
        allow_nan=False,
    )
    if len(packet) > _PACKET_LIMIT:
        packet = json.dumps(
            _minimal_incomplete_packet(snapshot),
            ensure_ascii=False,
            default=str,
            sort_keys=True,
            allow_nan=False,
        )
    return packet, False


def review_request_messages(
    snapshot: dict[str, Any],
) -> tuple[list[dict[str, str]], bool]:
    """Build the exact reviewer prompt and its completeness projection."""

    if not isinstance(snapshot, dict):
        raise ReviewError("scientific review requires a frozen snapshot object")
    complete = snapshot.get("complete") is True
    if snapshot.get("omitted_artifact_count"):
        complete = False
    packet, packet_complete = _snapshot_packet(snapshot)
    complete = complete and packet_complete
    return (
        [
            {"role": "system", "content": REVIEWER_V2_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Review this frozen Evidence Snapshot:\n" + packet,
            },
        ],
        complete,
    )


def review_snapshot(
    snapshot: dict[str, Any],
    cfg: LLMConfig,
    *,
    chat_call: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run one independent V2 review against a frozen snapshot."""

    messages, complete = review_request_messages(snapshot)
    invoke = chat if chat_call is None else chat_call
    result = invoke(
        messages,
        cfg,
        max_tokens=min(int(getattr(cfg, "max_tokens", 1800) or 1800), 1800),
        temperature=0.1,
    )
    normalized = normalize_review_v2(
        _json_object(result.get("content") or ""),
        snapshot_complete=complete,
    )
    usage = result.get("usage") or {}
    normalized["usage"] = {
        "input_tokens": usage.get("prompt_tokens", 0) or 0,
        "output_tokens": usage.get("completion_tokens", 0) or 0,
    }
    return normalized
