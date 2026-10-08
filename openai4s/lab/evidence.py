"""Host-only completion checks. Evaluation values never leave this boundary.

Explicit run declarations avoid guessing experiment identity from prose. The
native finalize payload carries ``lab_runs`` directly; submit_output carries
it in its output dictionary, preserving the existing SDK signature. A session
with Lab history must name runs to claim a completed task. Incomplete reports
can omit declarations with partial/blocked/failed task_status.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from openai4s.lab.evaluation import default_goal, evaluate_run
from openai4s.lab.ports import LabLedgerPort

_ALL_ROWS = 2**63 - 1
_UNVERIFIABLE = "Lab completion evidence could not be verified."
RUNNING_NOTICE = "Simulation experiment is still running."
_SUCCESS_WORDS = re.compile(
    r"\b(?:complete(?:d)?|finished|succeeded|successful(?:ly)?|done|achieved|attained|accomplished|"
    r"(?:goal|target)\s+(?:met|attainment))\b"
    r"|完成|成功|达成|完毕",
    re.IGNORECASE,
)


def _success_prose(claim):
    # Conservative in Lab sessions: an incomplete report should state the
    # observed state (stopped/unresolved), not use completion wording. Do not
    # infer negation across arbitrary model prose or treat task_status as a
    # licence to contradict the public summary. Machine status fields are
    # deliberately excluded from this text check.
    if isinstance(claim, str):
        return bool(_SUCCESS_WORDS.search(claim.replace("_", " ")))
    if isinstance(claim, Mapping):
        return any(
            _success_prose(str(key)) or _success_prose(value)
            for key, value in claim.items()
            if key not in {"lab_runs", "task_status"}
        )
    if isinstance(claim, (list, tuple)):
        return any(_success_prose(value) for value in claim)
    return False


def lab_completion_check(
    ledger: LabLedgerPort, root_frame_id: str, claim: Mapping[str, Any]
) -> str | None:
    """Return a fixed, value-free refusal or accept the exact declared runs.

    Reads exhaust repository limits, and the event cursor fences a concurrent
    write across the snapshot. Any unavailable evidence fails closed; neither
    exceptions nor evaluation diagnostics are reflected to the caller or log.
    """
    try:
        return _check(ledger, root_frame_id, claim)
    except Exception:
        return _UNVERIFIABLE


def _check(ledger, root, claim):
    output = claim.get("output")
    payload = output if isinstance(output, Mapping) else claim
    declarations = payload.get("lab_runs")
    if "lab_runs" in claim and payload is not claim:
        return "Put lab_runs inside the submit_output output dictionary."
    status = claim.get("task_status") or payload.get("task_status") or "completed"
    if declarations is None:
        if not ledger.list_runs(root, limit=1):
            return None
        if status in {"partial", "blocked", "failed"} and not _success_prose(claim):
            return None
        return "Lab completion requires explicit lab_runs with run_id and status."
    if not isinstance(declarations, list) or not 1 <= len(declarations) <= 100:
        return "lab_runs must be a nonempty list of run declarations."
    seen = set()
    cursor = ledger.latest_event_seq(root)
    for item in declarations:
        if (
            not isinstance(item, dict)
            or set(item) != {"run_id", "status"}
            or not isinstance(item["run_id"], str)
            or not item["run_id"]
            or len(item["run_id"]) > 200
            or item["status"] not in ("completed", "running")
            or item["run_id"] in seen
        ):
            return "Each lab_runs entry needs a unique run_id and completed/running status."
        run_id = item["run_id"]
        seen.add(run_id)
        run = ledger.get_run(run_id)
        if run is None or run.get("root_frame_id") != root:
            return "The declared Lab run is not available in this session."
        if item["status"] == "running":
            if _success_prose(claim):
                return "A running Lab declaration cannot support completion wording."
            if run.get("status") not in {"creating", "ready", "busy", "quarantined"}:
                return "The declared Lab run is no longer running."
            summary = payload.get("summary", "")
            # Web projection exposes only the first 4000 stripped characters.
            visible_summary = summary.strip()[:4000] if isinstance(summary, str) else ""
            if (
                status != "partial"
                or not isinstance(summary, str)
                or not (
                    RUNNING_NOTICE in visible_summary
                    or "仿真实验仍在运行" in visible_summary
                )
            ):
                return (
                    "A running Lab run requires task_status partial and summary: "
                    + RUNNING_NOTICE
                )
            continue
        if run.get("status") != "ended" or run.get("end_reason") not in {
            "end_action",
            "max_steps",
        }:
            return (
                "The Lab run has not ended normally; it cannot be declared completed."
            )
        commands = ledger.list_commands(run_id, limit=_ALL_ROWS)
        if any(c.get("state") == "outcome_unknown" for c in commands):
            return "The Lab run has an unresolved command; query its status, never resend it."
        observations = ledger.list_observations(run_id, limit=_ALL_ROWS)
        evaluations = ledger.list_evaluations(run_id, limit=_ALL_ROWS)
        target = None
        if run["profile"] == "GenWurtzExtract-v2":
            initial = next((o for o in observations if o.get("sequence") == 0), {})
            targets = [
                c.get("value")
                for c in initial.get("channels", [])
                if c.get("name") == "targets"
                and c.get("source") == "simulated_sensor"
                and c.get("quality") == "ok"
            ]
            if len(targets) != 1 or not isinstance(targets[0], str):
                return "The Lab goal definition could not be verified."
            target = targets[0]
        goal = default_goal(run["profile"], target=target)
        result = evaluate_run(run, commands, observations, evaluations, goal=goal)
        coverage = result["evidence_completeness"]
        if coverage["successful_commands"] != coverage["with_observation"]:
            return "A successful Lab command is missing its matching observation."
        if result["goal_met"] is not True:
            return "The Lab goal evaluation is not met or could not be verified."
    if ledger.latest_event_seq(root) != cursor:
        return "Lab evidence changed during verification; query the run and try again."
    return None
