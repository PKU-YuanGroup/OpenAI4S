"""Constrained, single-call review of a completed research turn.

The reviewer is deliberately not a Code-as-Action sub-agent: it cannot run
tools, mutate the workspace, or trigger approval prompts.  It receives a
bounded evidence packet and returns a small JSON verdict that the gateway
persists as an ordinary ``review`` activity step.
"""
from __future__ import annotations

import json
from typing import Any

from openai4s.config import LLMConfig
from openai4s.llm import chat

REVIEWER_SYSTEM_PROMPT = """You are the Reviewer for an autonomous scientific research agent.
Audit only the evidence supplied by the host. Check whether the final answer is supported by
the recorded execution, whether requested deliverables exist, whether quantitative claims match
the evidence, and whether uncertainty, provenance, and correlation-versus-causation limits are
stated appropriately. Do not invent missing evidence and do not redo the research.

Return one JSON object and no prose:
{
  "verdict": "pass" | "issues",
  "summary": "short user-facing summary",
  "issues": [
    {
      "severity": "high" | "medium" | "low",
      "title": "short title",
      "detail": "specific actionable explanation",
      "evidence": "exact evidence or exact absence that supports the finding",
      "artifact_id": "optional artifact id"
    }
  ]
}

Use verdict=pass only when there are no material issues. Minor style preferences are not issues.
If omitted_artifact_count is non-zero, the evidence is incomplete: never return pass.
Limit the list to the most important 8 findings."""


class ReviewError(RuntimeError):
    """Raised when the reviewer response cannot be normalized safely."""


def _json_object(text: str) -> dict[str, Any]:
    """Extract the first JSON object from a plain or fenced model response."""
    raw = (text or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            return value
    except (TypeError, ValueError):
        pass
    decoder = json.JSONDecoder()
    for idx, char in enumerate(raw):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(raw[idx:])
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    raise ReviewError("reviewer returned no valid JSON object")


def _clean_issue(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    severity = str(value.get("severity") or "medium").lower().strip()
    if severity not in {"high", "medium", "low"}:
        severity = "medium"
    title = str(value.get("title") or "Review finding").strip()[:160]
    detail = str(value.get("detail") or "").strip()[:2400]
    evidence = str(value.get("evidence") or "").strip()[:1600]
    artifact_id = str(value.get("artifact_id") or "").strip()[:160]
    if not detail and not evidence:
        return None
    issue = {
        "severity": severity,
        "title": title,
        "detail": detail,
        "evidence": evidence,
    }
    if artifact_id:
        issue["artifact_id"] = artifact_id
    return issue


def normalize_review(value: dict[str, Any]) -> dict[str, Any]:
    raw_issues = value.get("issues") or []
    issues = []
    for raw in raw_issues:
        issue = _clean_issue(raw)
        if issue:
            issues.append(issue)
        if len(issues) >= 8:
            break
    verdict = str(value.get("verdict") or "").lower().strip()
    if issues:
        verdict = "issues"
    elif raw_issues:
        raise ReviewError("reviewer returned findings with no usable evidence")
    elif verdict == "issues":
        raise ReviewError("reviewer issues verdict contained no usable findings")
    elif verdict != "pass":
        raise ReviewError("reviewer verdict must be 'pass' or 'issues'")
    summary = str(value.get("summary") or "").strip()[:320]
    if verdict == "pass":
        summary = "No issues found"
    elif not summary:
        summary = f"{len(issues)} issue{'s' if len(issues) != 1 else ''} found"
    return {"verdict": verdict, "summary": summary, "issues": issues}


def _bounded_packet(evidence: dict[str, Any]) -> str:
    """Budget evidence by field while always returning valid JSON.

    Artifact metadata is kept ahead of verbose execution logs so a long stdout
    cannot silently remove the very deliverables the Reviewer must verify.
    """
    truncated = False

    def clip(value: Any, limit: int) -> str:
        nonlocal truncated
        text = str(value or "")
        if len(text) > limit:
            truncated = True
            return text[:limit] + "\n[truncated]"
        return text

    def compact_json(value: Any, limit: int) -> str:
        try:
            rendered = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            rendered = str(value or "")
        return clip(rendered, limit)

    raw_artifacts = evidence.get("changed_artifacts") or []
    try:
        reported_artifact_count = max(
            len(raw_artifacts), int(evidence.get("changed_artifact_count") or 0)
        )
    except (TypeError, ValueError):
        reported_artifact_count = len(raw_artifacts)
    try:
        omitted_artifact_count = max(
            0, int(evidence.get("omitted_artifact_count") or 0)
        )
    except (TypeError, ValueError):
        omitted_artifact_count = 0
    artifacts = []
    for artifact in raw_artifacts[:64]:
        if not isinstance(artifact, dict):
            continue
        item = {
            "artifact_id": clip(artifact.get("artifact_id"), 160),
            "filename": clip(artifact.get("filename"), 320),
            "content_type": clip(artifact.get("content_type"), 120),
            "size_bytes": (
                artifact.get("size_bytes")
                if isinstance(artifact.get("size_bytes"), (int, float))
                else clip(artifact.get("size_bytes"), 64)
            ),
            "latest_version_id": clip(artifact.get("latest_version_id"), 160),
            "exists": bool(artifact.get("exists")),
        }
        if artifact.get("excerpt") and len(artifacts) < 12:
            item["excerpt"] = clip(artifact["excerpt"], 1_200)
        artifacts.append(item)
    omitted_artifact_count = max(
        omitted_artifact_count, reported_artifact_count - len(artifacts)
    )
    if omitted_artifact_count or len(raw_artifacts) > len(artifacts):
        truncated = True

    raw_execution = evidence.get("execution") or []
    execution = []
    for cell in raw_execution[-8:]:
        if not isinstance(cell, dict):
            continue
        execution.append(
            {
                "cell_index": clip(cell.get("cell_index"), 32),
                "status": clip(cell.get("status"), 80),
                "files_written": [
                    clip(value, 160) for value in (cell.get("files_written") or [])[:6]
                ],
                "files_read": [
                    clip(value, 160) for value in (cell.get("files_read") or [])[:6]
                ],
                "source": clip(cell.get("source"), 1_200),
                "stdout": clip(cell.get("stdout"), 700),
                "stderr": clip(cell.get("stderr"), 400),
                "error": clip(cell.get("error"), 400),
            }
        )
    if len(raw_execution) > len(execution):
        truncated = True

    raw_tools = evidence.get("tool_evidence") or []
    tool_evidence = []
    for step in raw_tools[-10:]:
        if not isinstance(step, dict):
            continue
        tool_evidence.append(
            {
                "kind": clip(step.get("kind"), 80),
                "title": clip(step.get("title"), 240),
                "status": clip(step.get("status"), 80),
                "summary": clip(step.get("summary"), 400),
                "input": compact_json(step.get("input"), 800),
                "output": compact_json(step.get("output"), 1_600),
            }
        )
    if len(raw_tools) > len(tool_evidence):
        truncated = True

    submitted = evidence.get("submitted_output")
    submitted_json = json.dumps(submitted, ensure_ascii=False, default=str)
    if len(submitted_json) > 4_000:
        submitted = clip(submitted_json, 4_000)

    bounded = {
        "user_request": clip(evidence.get("user_request"), 9_000),
        "final_answer": clip(evidence.get("final_answer"), 14_000),
        "changed_artifacts": artifacts,
        "changed_artifact_count": reported_artifact_count,
        "omitted_artifact_count": omitted_artifact_count,
        "submitted_output": submitted,
        "execution": execution,
        "tool_evidence": tool_evidence,
    }
    if truncated:
        bounded["host_note"] = "[host truncated the evidence packet]"
    packet = json.dumps(bounded, ensure_ascii=False, default=str)
    limit = 60_000

    def trim_tier(refs: list[tuple[dict, str]]) -> None:
        """Shrink the largest strings in one evidence tier, preserving structure."""
        nonlocal packet, truncated
        while len(packet) > limit:
            candidates = [
                (len(value), container, key)
                for container, key in refs
                if isinstance((value := container.get(key)), str) and value
            ]
            if not candidates:
                return
            size, container, key = max(candidates, key=lambda item: item[0])
            excess = len(packet) - limit
            keep = max(0, size - max(excess + 256, size // 3))
            container[key] = container[key][:keep]
            truncated = True
            bounded["host_note"] = "[host truncated the evidence packet]"
            packet = json.dumps(bounded, ensure_ascii=False, default=str)

    # Artifact excerpts are the first expendable text. Keep artifact metadata
    # and execution-cell structure so the Reviewer still knows what to verify.
    trim_tier([(item, "excerpt") for item in artifacts if "excerpt" in item])
    trim_tier(
        [
            *[
                (cell, key)
                for cell in execution
                for key in ("source", "stdout", "stderr", "error")
            ],
            *[
                (step, key)
                for step in tool_evidence
                for key in ("output", "input", "summary")
            ],
        ]
    )
    trim_tier(
        [
            (bounded, "final_answer"),
            (bounded, "user_request"),
            (bounded, "submitted_output"),
        ]
    )
    trim_tier(
        [
            (item, key)
            for item in artifacts
            for key in ("filename", "artifact_id", "latest_version_id", "content_type")
        ]
    )
    if len(packet) > limit:
        # Defensive last resort for adversarial JSON escaping or unusual scalar
        # objects: retain compact artifact + execution metadata, never raw-slice
        # the serialized JSON.
        bounded = {
            "user_request": clip(evidence.get("user_request"), 1_000),
            "final_answer": clip(evidence.get("final_answer"), 2_000),
            "changed_artifacts": [
                {
                    "artifact_id": item.get("artifact_id", "")[:80],
                    "filename": item.get("filename", "")[:120],
                    "exists": item.get("exists", False),
                }
                for item in artifacts
            ],
            "changed_artifact_count": reported_artifact_count,
            "omitted_artifact_count": omitted_artifact_count,
            "submitted_output": "[truncated]",
            "execution": [
                {"cell_index": cell.get("cell_index"), "status": cell.get("status")}
                for cell in execution[-4:]
            ],
            "tool_evidence": [
                {
                    "kind": step.get("kind"),
                    "title": step.get("title", "")[:120],
                    "status": step.get("status"),
                }
                for step in tool_evidence[-4:]
            ],
            "host_note": "[host truncated the evidence packet]",
        }
        packet = json.dumps(bounded, ensure_ascii=False, default=str)
    if len(packet) > limit:
        raise ReviewError("review evidence could not be bounded safely")
    return packet


def review_evidence(evidence: dict[str, Any], cfg: LLMConfig) -> dict[str, Any]:
    """Run one bounded reviewer call and return a normalized verdict + usage."""
    packet = _bounded_packet(evidence)
    raw_artifacts = evidence.get("changed_artifacts") or []
    try:
        reported_count = max(
            len(raw_artifacts), int(evidence.get("changed_artifact_count") or 0)
        )
        omitted_count = max(
            int(evidence.get("omitted_artifact_count") or 0),
            reported_count - 64,
            len(raw_artifacts) - 64,
        )
    except (TypeError, ValueError):
        omitted_count = max(0, len(raw_artifacts) - 64)
    if omitted_count > 0:
        raise ReviewError("review evidence omitted changed artifacts")
    result = chat(
        [
            {"role": "system", "content": REVIEWER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Review this completed research turn:\n" + packet,
            },
        ],
        cfg,
        max_tokens=min(cfg.max_tokens, 1800),
        temperature=0.1,
    )
    normalized = normalize_review(_json_object(result.get("content") or ""))
    usage = result.get("usage") or {}
    normalized["usage"] = {
        "input_tokens": usage.get("prompt_tokens", 0) or 0,
        "output_tokens": usage.get("completion_tokens", 0) or 0,
    }
    normalized["model"] = cfg.model or None
    return normalized
