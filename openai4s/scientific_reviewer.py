"""Stage 3 Scientific Reviewer V2: independent, schema-strict, tool-free.

The Reviewer sees only a frozen Evidence Snapshot. It never receives the main
Agent's hidden reasoning, never writes the formal workspace, and cannot pass
when the snapshot declares an omission. Production callers inject ``chat_call``
for tests; the default is the same provider-neutral ``chat()`` used elsewhere.
"""

from __future__ import annotations

import hashlib
import json
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
        if verdict == "pass":
            raise ReviewError("incomplete evidence cannot pass scientific review")
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


def _snapshot_packet(snapshot: dict[str, Any]) -> str:
    """Serialize the frozen snapshot for the independent reviewer context."""

    packet = json.dumps(snapshot, ensure_ascii=False, default=str, sort_keys=True)
    if len(packet) <= 80_000:
        return packet
    # Keep identity, completeness, refs, and adapter summaries; drop bulky
    # cell stdout last so the Reviewer still knows what must be verified.
    compact = dict(snapshot)
    for key in ("cells", "tool_ledger"):
        rows = compact.get(key)
        if isinstance(rows, list) and len(rows) > 8:
            compact[key] = rows[-8:]
            compact.setdefault("truncation", {})
            if isinstance(compact["truncation"], dict):
                compact["truncation"][f"{key}_packet"] = True
    packet = json.dumps(compact, ensure_ascii=False, default=str, sort_keys=True)
    if len(packet) > 80_000:
        packet = (
            packet[:79_000] + ',"host_note":"[host truncated the snapshot packet]"}'
        )
    return packet


def review_snapshot(
    snapshot: dict[str, Any],
    cfg: LLMConfig,
    *,
    chat_call: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run one independent V2 review against a frozen snapshot."""

    if not isinstance(snapshot, dict):
        raise ReviewError("scientific review requires a frozen snapshot object")
    complete = snapshot.get("complete") is True
    if snapshot.get("omitted_artifact_count"):
        complete = False
    packet = _snapshot_packet(snapshot)
    invoke = chat if chat_call is None else chat_call
    result = invoke(
        [
            {"role": "system", "content": REVIEWER_V2_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Review this frozen Evidence Snapshot:\n" + packet,
            },
        ],
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
