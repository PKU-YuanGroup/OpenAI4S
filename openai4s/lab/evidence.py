"""Host-only completion checks. Evaluation values never leave this boundary.

Explicit run declarations avoid guessing experiment identity from prose. The
native finalize payload carries ``lab_runs`` directly; submit_output carries
it in its output dictionary, preserving the existing SDK signature.

Scope is the current user turn: the dispatcher records the session's Lab event
cursor when a turn starts, and only runs this turn created or commanded are
"used". A completed task must declare every one of them, so an older verified
run cannot vouch for an experiment that this turn stopped or left running. A
turn that did no Lab work, and a delegated child (which has no Lab), needs no
declaration. Incomplete reports can omit declarations with
partial/blocked/failed task_status.

What a person does in the workbench while the turn runs is not the turn's: a
command recorded with origin ``manual_ui`` is not "used", and neither is a run
the turn did not create itself. A run whose creation failed never opened a
session, so there is no experiment to declare for it.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Mapping
from typing import Any

from openai4s.lab.evaluation import default_goal, evaluate_run
from openai4s.lab.ports import LabLedgerPort

_ALL_ROWS = 2**63 - 1
_EVENT_PAGE = 1000
_UNVERIFIABLE = "Lab completion evidence could not be verified."
RUNNING_NOTICE = "Simulation experiment is still running."
INCOMPLETE_WORDING = (
    "An incomplete Lab report cannot say the experiment or its goal succeeded. "
    "Describe what was observed (for example stopped or unresolved), "
    "or declare completed runs in lab_runs."
)
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"))
_VERB = (
    r"(?:complete(?:d)?|finished|succeeded|successful(?:ly)?|achieved|met|"
    r"reached|attained|accomplished|done)"
)
_OBJECT = r"(?:experiments?|simulations?|runs?|goals?|targets?|objectives?|tasks?)"
# A negated or not-yet clause is an honest report, never a success claim.
_NEGATED = re.compile(
    r"\b(?:not|never|no longer|cannot|can't|couldn't|didn't|wasn't|isn't|"
    r"hasn't|haven't|unable to|failed to|without|before)\b"
    rf"(?:\s+\w+){{0,4}}?\s+(?:be\s+|been\s+)?{_VERB}\b"
    r"|(?:未能|未|没有|没|尚未|无法|不能)(?:完成|成功|达成|实现|结束)",
    re.IGNORECASE,
)
# Success is a claim about the experiment, run, goal or task. A bullet like
# "Completed two transfers before the stop" reports progress and passes.
_SUCCESS = re.compile(
    rf"\b{_OBJECT}\b(?:\s+\w+){{0,4}}?\s+{_VERB}\b"
    rf"|\b{_VERB}\s+(?:the\s+|this\s+|our\s+|its\s+|all\s+)?(?:\w+\s+)?{_OBJECT}\b"
    r"|(?:实验|仿真|目标|任务)[^。！？；，,.!?;\n]{0,8}(?:完成|成功|达成|实现|完毕)"
    r"|(?:完成|达成|实现)(?:了)?[^。！？；，,.!?;\n]{0,4}(?:实验|仿真|目标|任务)",
    re.IGNORECASE,
)


def _success_text(text):
    text = unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH)
    text = _NEGATED.sub(" ", text.replace("_", " "))
    return bool(_SUCCESS.search(text))


def _success_prose(claim, parent=""):
    # Only the two known envelope levels exclude verified machine fields,
    # before entering this recursion; nested keys are prose like any other.
    # A structured claim reads as "<parent> <key> <value>", so a nested
    # {"task_status": "completed"} or {"experiment_completed": true} counts.
    if isinstance(claim, str):
        return _success_text(claim)
    if isinstance(claim, Mapping):
        for key, value in claim.items():
            if _success_text(f"{parent} {key}"):
                return True
            if isinstance(value, (str, bool, int, float)) and _success_text(
                f"{parent} {key} {value}"
            ):
                return True
            if _success_prose(value, str(key)):
                return True
        return False
    if isinstance(claim, (list, tuple)):
        return any(_success_prose(value, parent) for value in claim)
    return False


def _claim_success_prose(claim):
    public = {k: v for k, v in claim.items() if k not in {"lab_runs", "task_status"}}
    if isinstance(public.get("output"), Mapping):
        public["output"] = {
            k: v
            for k, v in public["output"].items()
            if k not in {"lab_runs", "task_status"}
        }
    return _success_prose(public)


def _turn_runs(ledger, root, turn_cursor, created_runs=None):
    """Runs this turn created or commanded; without a turn, every run."""
    if turn_cursor is None:
        return {
            row["run_id"]
            for row in ledger.list_runs(root, limit=_ALL_ROWS)
            if row.get("status") != "failed"
        }
    used, after = set(), turn_cursor
    manual: dict[Any, bool] = {}
    while True:
        page = ledger.events_since(root, after_seq=after, limit=_EVENT_PAGE)
        for event in page:
            run_id = event.get("run_id")
            if not run_id:
                continue
            # Sweeps and stops of untouched runs emit run events only.
            if event.get("kind") == "command":
                ref = event.get("ref_id")
                if ref not in manual:
                    # A command without its row is not proof of a person's
                    # action: it stays the turn's.
                    command = ledger.get_command(ref)
                    manual[ref] = (
                        command is not None and command.get("origin") == "manual_ui"
                    )
                if not manual[ref]:
                    used.add(run_id)
            elif (
                event.get("kind") == "run"
                and event.get("state") == "creating"
                and (created_runs is None or run_id in created_runs)
            ):
                used.add(run_id)
        if len(page) < _EVENT_PAGE:
            break
        after = page[-1]["event_seq"]
    # Only a run still being created can fail (create_failed): it never ran.
    return {
        run_id
        for run_id in used
        if (ledger.get_run(run_id) or {}).get("status") != "failed"
    }


def lab_completion_check(
    ledger: LabLedgerPort,
    root_frame_id: str,
    claim: Mapping[str, Any],
    *,
    turn_cursor: int | None = None,
    created_runs: Collection[str] | None = None,
) -> str | None:
    """Return a fixed, value-free refusal or accept the exact declared runs.

    ``turn_cursor`` is the session's Lab event cursor when the current turn
    began; None treats every run in the session as this turn's.
    ``created_runs`` names the runs this turn's own create calls returned, so
    a run a person created in the workbench meanwhile is not the turn's; None
    counts every run created since the cursor. Reads exhaust
    repository limits, and the event cursor fences a concurrent write across
    the snapshot. Any unavailable evidence fails closed; neither exceptions
    nor evaluation diagnostics are reflected to the caller or log.
    """
    try:
        return _check(ledger, root_frame_id, claim, turn_cursor, created_runs)
    except Exception:
        return _UNVERIFIABLE


def _check(ledger, root, claim, turn_cursor, created_runs=None):
    output = claim.get("output")
    payload = output if isinstance(output, Mapping) else claim
    declarations = payload.get("lab_runs")
    if "lab_runs" in claim and payload is not claim:
        return "Put lab_runs inside the submit_output output dictionary."
    used = _turn_runs(ledger, root, turn_cursor, created_runs)
    if not used and (declarations is None or not ledger.list_runs(root, limit=1)):
        # No Lab work this turn. In a session that never used Lab, a key
        # named lab_runs is ordinary output data, not a declaration.
        return None
    if (
        payload is not claim
        and claim.get("task_status") is not None
        and payload.get("task_status") is not None
        and claim["task_status"] != payload["task_status"]
    ):
        return "Lab task_status declarations conflict."
    status = claim.get("task_status") or payload.get("task_status") or "completed"
    incomplete = status in {"partial", "blocked", "failed"}
    if declarations is None:
        if incomplete:
            return INCOMPLETE_WORDING if _claim_success_prose(claim) else None
        return (
            "Lab completion requires explicit lab_runs declaring every run used "
            "in this turn: " + ", ".join(sorted(used)[:20]) + "."
        )
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
            if _claim_success_prose(claim):
                return "A running Lab declaration cannot support completion wording."
            if run.get("status") not in {"creating", "ready", "busy", "quarantined"}:
                return "The declared Lab run is no longer running."
            summary = payload.get("summary", "")
            # Web projection exposes only the first 4000 stripped characters.
            visible_summary = summary.strip()[:4000] if isinstance(summary, str) else ""
            if (
                status not in {"partial", "blocked"}
                or not isinstance(summary, str)
                or not (
                    RUNNING_NOTICE in visible_summary
                    or "仿真实验仍在运行" in visible_summary
                )
            ):
                return (
                    "A running Lab run requires task_status partial or blocked "
                    "and summary: " + RUNNING_NOTICE
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
        try:
            goal = default_goal(run["profile"], target=target)
        except ValueError:
            return (
                "This simulation profile defines no completion goal; "
                "report the run as partial instead of completed."
            )
        result = evaluate_run(run, commands, observations, evaluations, goal=goal)
        coverage = result["evidence_completeness"]
        if coverage["successful_commands"] != coverage["with_observation"]:
            return "A successful Lab command is missing its matching observation."
        if result["goal_met"] is not True:
            return "The Lab goal evaluation is not met or could not be verified."
    if not incomplete:
        undeclared = sorted(used - seen)
        if undeclared:
            # Otherwise a verified run could vouch for one this turn stopped.
            return (
                "Declare every Lab run used in this turn in lab_runs: "
                + ", ".join(undeclared[:20])
                + "."
            )
    if ledger.latest_event_seq(root) != cursor:
        return "Lab evidence changed during verification; query the run and try again."
    return None
