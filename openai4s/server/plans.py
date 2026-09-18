"""Structured-plan lifecycle for web sessions.

``PlanService`` owns the review-plan lifecycle: parsing a planner response,
persisting a draft and its JSON artifact, exposing the public review shape,
and transitioning an approved plan through execution.  The gateway supplies
only event emission and the normal agent turn used to revise or execute a plan.
Live ``host.plan_update`` mutations remain in ``HostDispatcher``; their WebSocket
adapter remains in the gateway.
"""

from __future__ import annotations

import hashlib
import json
import re
import traceback
from pathlib import Path
from typing import Any, Callable, Protocol

from openai4s.host.progress import SETTLED_STEP_STATUSES
from openai4s.server.completions import response_language
from openai4s.store import Store

EventSink = Callable[[dict[str, Any]], None]
EmitterFactory = Callable[[str], EventSink]

#: How a request says it already carries plan-mode instructions. The workbench
#: prepends its localised `plan.prompt.*` text (and the legacy app.js its
#: Chinese literal) starting with one of these, and the revision seed below
#: starts with one too; the server adds its own instruction only when none is
#: present, so a request is never instructed twice.
PLAN_MODE_PROMPT_MARKERS = ("[Plan Mode]", "[计划模式]")

#: The plan-draft instruction a `plan:true` turn adds to the model input when
#: the request does not carry one. Appended after the task (the stored user
#: row is unchanged), mirroring the workbench's `plan.prompt.*` wording so the
#: JSON block `extract_plan_json` looks for is what the model is asked for.
PLAN_DRAFT_INSTRUCTIONS = {
    "en": (
        "[Plan Mode] Do not execute anything or call any tools yet. Devise a "
        "structured execution plan for the task above, and output only two "
        "parts:\n"
        "1) A brief description of the approach (prose, explaining your chosen "
        "goal/approach and the main analytical thread);\n"
        "2) Immediately followed by a ```json code block, strictly using the "
        "following structure:\n"
        '{"title":"Plan title","rationale":"One-sentence rationale",'
        '"confidence":"high|medium|low","steps":[{"id":"s1","title":"Step '
        'title","detail":"What this step does","deliverables":'
        '["intermediate-table.csv","figure.png"]}]}\n'
        "Each step must have a unique id, a clear title, a brief description, "
        "and a list of expected output filenames for that step; where "
        "reasonable, make each step yield a viewable intermediate result -- a "
        "table (.csv) or a figure (.png) -- as a deliverable. Wait for user "
        "approval before executing."
    ),
    "zh": (
        "[计划模式] 请先不要执行、不要调用任何工具。为上面的任务制定一个结构化执行"
        "计划，并只输出两部分：\n"
        "1) 一段简短的方案说明（散文，说明你选择的目标/思路与分析主线）；\n"
        "2) 紧接着一个 ```json 代码块，严格使用如下结构：\n"
        '{"title":"计划标题","rationale":"一句话理由","confidence":'
        '"high|medium|low","steps":[{"id":"s1","title":"步骤标题","detail":'
        '"这一步做什么","deliverables":["中间结果.csv","图.png"]}]}\n'
        "每个步骤要有唯一 id、清晰标题、简要说明，以及该步预期产出的结果文件名列表；"
        "尽量让每一步都产出一个可查看的中间结果——一张表格（.csv）或一张图（.png）"
        "作为 deliverable。等待用户批准后再执行。"
    ),
}


def carries_plan_mode_prompt(text: str) -> bool:
    """Whether ``text`` already opens with plan-mode instructions."""
    return str(text or "").lstrip().startswith(PLAN_MODE_PROMPT_MARKERS)


#: Where a plan-mode prompt ends and the user's own words begin: the
#: workbench's `plan.prompt.part3` and the legacy app.js literal ("Task: " /
#: "任务："), and `PlanService.run_revision`'s seed ("Change requests: " /
#: "修改意见：").
PLAN_MODE_REQUEST_DELIMITERS = (
    "\n\nTask: ",
    "\n\n任务：",
    "\n\nChange requests: ",
    "\n\n修改意见：",
)


def plan_mode_request_text(text: str) -> str:
    """The user's own words inside a plan-mode prompt; ``text`` otherwise.

    For what a person reads -- the session title and the text a title is
    summarised from -- never for model input: the prompt is the instruction the
    turn was drafted under. Cut at the first delimiter, which is the prompt's
    own; the user's text keeps any later one. Text with no marker, or with no
    delimiter to separate, is returned unchanged.
    """
    value = str(text or "")
    if not carries_plan_mode_prompt(value):
        return value
    found = [
        (index, len(delimiter))
        for delimiter in PLAN_MODE_REQUEST_DELIMITERS
        if (index := value.find(delimiter)) >= 0
    ]
    if not found:
        return value
    index, length = min(found)
    return value[index + length :].strip() or value


def plan_draft_instruction(user_text: str) -> str | None:
    """The instruction a `plan:true` turn appends, or None when the request
    already carries one. Localised by the request, as the turn's own
    completion and narration text are."""
    if carries_plan_mode_prompt(user_text):
        return None
    return PLAN_DRAFT_INSTRUCTIONS[response_language(user_text)]


class MessageRunner(Protocol):
    """The normal agent-turn port used by plan execution and revision."""

    def __call__(
        self,
        root_frame_id: str,
        project_id: str,
        user_text: str,
        model: str | None = None,
        *,
        plan: bool = False,
    ) -> dict[str, Any]: ...


class PlanSession(Protocol):
    """The small session surface needed while capturing a planner reply."""

    root_frame_id: str
    project_id: str
    workspace: Path
    messages: list[dict[str, Any]]


class PlanService:
    """Own the reviewable plan lifecycle independently of HTTP and jobs."""

    def __init__(
        self,
        *,
        store: Store,
        emitter_for: EmitterFactory,
        run_message: MessageRunner,
    ) -> None:
        self.store = store
        self.emitter_for = emitter_for
        self.run_message = run_message

    def finalize(
        self,
        session: PlanSession,
        reply: str,
        prose: str,
        emit: EventSink,
    ) -> dict[str, Any] | None:
        """Capture a planner reply as a draft plan and a JSON artifact.

        Returns the stored draft row, or None when the reply yields no steps
        -- the caller reports that miss, since nothing is stored for it.
        """
        root_frame_id = session.root_frame_id
        raw = extract_plan_json(reply)
        task_hint = ""
        for message in reversed(session.messages):
            if message.get("role") == "user":
                task_hint = re.sub(
                    r"\s+", " ", str(message.get("content") or "")
                ).strip()
                break
        plan = normalize_plan(raw, prose, task_hint)
        if not plan["steps"]:
            # Keep the prose-only fallback card when no plan can be recovered.
            return None

        previous = self.store.get_plan_by_frame(root_frame_id)
        reusable = previous if previous and previous.get("status") == "draft" else None
        artifact = self.write_artifact(
            session,
            plan,
            reusable.get("artifact_id") if reusable else None,
            emit,
        )
        artifact_id = (
            artifact.get("artifact_id")
            if artifact
            else (reusable.get("artifact_id") if reusable else None)
        )

        if reusable:
            self.store.update_plan(
                reusable["plan_id"],
                title=plan["title"],
                rationale=plan["rationale"],
                confidence=plan["confidence"],
                steps=plan["steps"],
                status="draft",
                step_status={},
                artifact_id=artifact_id,
            )
            row = self.store.get_plan(reusable["plan_id"])
        else:
            row = self.store.create_plan(
                frame_id=root_frame_id,
                project_id=session.project_id,
                title=plan["title"],
                rationale=plan["rationale"],
                confidence=plan["confidence"],
                steps=plan["steps"],
                artifact_id=artifact_id,
                status="draft",
            )
        self.emit_ready(emit, root_frame_id, row)
        return row

    def write_artifact(
        self,
        session: PlanSession,
        plan: dict[str, Any],
        artifact_id: str | None,
        emit: EventSink,
    ) -> dict[str, Any] | None:
        """Write and version the plan JSON so it also appears in Files."""
        try:
            if artifact_id:
                existing = self.store.get_artifact(artifact_id) or {}
                filename = existing.get("filename") or plan_filename(
                    plan["title"], session.root_frame_id
                )
            else:
                filename = plan_filename(plan["title"], session.root_frame_id)
            body = json.dumps(
                {
                    "title": plan["title"],
                    "rationale": plan["rationale"],
                    "confidence": plan["confidence"],
                    "steps": plan["steps"],
                },
                ensure_ascii=False,
                indent=2,
            )
            path = session.workspace / filename
            path.write_text(body, encoding="utf-8")
            data = body.encode("utf-8")
            record = self.store.save_artifact(
                path=str(path),
                filename=filename,
                content_type="application/json",
                size_bytes=len(data),
                checksum=hashlib.sha256(data).hexdigest(),
                frame_id=session.root_frame_id,
                project_id=session.project_id,
                artifact_id=artifact_id,
            )
            emit(
                {
                    "type": "artifact_created",
                    "frame_id": session.root_frame_id,
                    "artifact_id": record.get("artifact_id"),
                    "filename": filename,
                }
            )
            return record
        except Exception:  # noqa: BLE001 - plan capture must survive artifact I/O
            traceback.print_exc()
            return None

    def emit_ready(
        self,
        emit: EventSink,
        root_frame_id: str,
        plan: dict[str, Any] | None,
    ) -> None:
        public = public_plan(plan)
        if public is None:
            return
        emit(
            {
                "type": "plan_ready",
                "frame_id": root_frame_id,
                "plan_id": public.get("plan_id"),
                "status": public.get("status"),
                "plan": public,
                "artifact_id": public.get("artifact_id"),
            }
        )

    def get_state(self, root_frame_id: str) -> dict[str, Any]:
        public = public_plan(self.store.get_plan_by_frame(root_frame_id))
        return {
            "frame_id": root_frame_id,
            "plan_id": public.get("plan_id") if public else None,
            "status": public.get("status") if public else None,
            "plan": public,
        }

    def discard(self, root_frame_id: str) -> dict[str, Any]:
        plan = self.store.get_plan_by_frame(root_frame_id)
        if not plan:
            return {"ok": False, "error": "no plan for this session"}
        self.store.update_plan(plan["plan_id"], status="discarded")
        emit = self.emitter_for(root_frame_id)
        self.emit_ready(
            emit,
            root_frame_id,
            self.store.get_plan(plan["plan_id"]),
        )
        return {
            "ok": True,
            "plan_id": plan["plan_id"],
            "status": "discarded",
        }

    def plan_language(self, plan: dict[str, Any] | None, *extra: str) -> str:
        """The language a plan's seeds are written in: ``zh`` or ``en``.

        Carried from the request that drafted the plan -- the draft turn's own
        narration and completion text already followed that request -- and
        read back from the stored message rather than from memory, so an
        approval or resume after a daemon restart speaks the same language.
        The plan's own text (and any ``extra`` a seed quotes, such as revision
        feedback) counts too, by the same any-CJK rule `response_language`
        applies to the whole seed: an English seed therefore never quotes
        Chinese, and the gateway's re-detection of the seed agrees with the
        template it was built from.
        """
        parts = [self._drafting_request(plan), *extra]
        if plan:
            parts.append(str(plan.get("title") or ""))
            parts.append(str(plan.get("rationale") or ""))
            for step in plan.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                parts.append(str(step.get("title") or ""))
                parts.append(str(step.get("detail") or ""))
                parts.extend(str(item) for item in step.get("deliverables") or [])
        return response_language("\n".join(parts))

    def _drafting_request(self, plan: dict[str, Any] | None) -> str:
        """The newest user message stored on the plan's frame no later than
        the plan row itself -- the request its draft turn answered."""
        if not plan or self.store is None:
            return ""
        frame_id = plan.get("frame_id")
        created_at = plan.get("created_at")
        if not frame_id or created_at is None:
            return ""
        try:
            rows = self.store.list_messages(str(frame_id), limit=300, newest_first=True)
            drafted_at = int(created_at)
        except Exception:  # noqa: BLE001 - language is a best-effort projection
            return ""
        for row in rows:
            if row.get("role") != "user":
                continue
            try:
                if int(row.get("created_at") or 0) <= drafted_at:
                    return str(row.get("content") or "")
            except (TypeError, ValueError):
                continue
        return ""

    @staticmethod
    def _step_lines(steps: list[dict[str, Any]], zh: bool) -> list[str]:
        lines = []
        for index, step in enumerate(steps):
            step_id = step.get("id") or ("s" + str(index + 1))
            if zh:
                deliverables = (
                    "、".join(step.get("deliverables") or []) or "（无指定文件）"
                )
                lines.append(
                    f"- [{step_id}] {step.get('title', '')}：{step.get('detail', '')}"
                    f"  → 产出：{deliverables}"
                )
            else:
                deliverables = (
                    ", ".join(step.get("deliverables") or []) or "(no files specified)"
                )
                lines.append(
                    f"- [{step_id}] {step.get('title', '')}: {step.get('detail', '')}"
                    f"  → deliverables: {deliverables}"
                )
        return lines

    def execution_seed(self, plan: dict[str, Any]) -> str:
        zh = self.plan_language(plan) == "zh"
        steps_text = "\n".join(self._step_lines(plan.get("steps") or [], zh))
        if not zh:
            return (
                f'Plan "{plan.get("title", "")}" is approved; start executing it '
                "automatically now.\n\n"
                "Follow these steps strictly in order:\n" + steps_text + "\n\n"
                "Execution rules:\n"
                '1. Before starting each step, call host.plan_update("<step_id>", '
                '"in_progress") (this marks the step as in progress on the plan '
                "card).\n"
                "2. Once every deliverable file listed for the step is written, "
                'call host.plan_update("<step_id>", "completed"). If a step '
                'genuinely cannot be completed, call host.plan_update("<step_id>", '
                '"failed", note="reason") and continue with the next step.\n'
                "3. Work through the steps in order, writing each step's result "
                "files to the working directory (they become artifacts "
                "automatically).\n"
                "4. Strictly follow every constraint from my original task (for "
                "example: in the final summary, do not use Markdown links for raw "
                "data files larger than about 1MB; refer to them by filename "
                "only).\n"
                "5. When everything is done, write a concise final summary and "
                "call host.submit_output(...)."
            )
        return (
            f"已批准计划「{plan.get('title', '')}」，现在开始自动执行。\n\n"
            "请严格按下面的步骤顺序推进：\n" + steps_text + "\n\n"
            "执行规则：\n"
            '1. 每开始一个步骤前，先调用 host.plan_update("<step_id>", '
            '"in_progress")（这会把计划卡上的该步标记为进行中）。\n'
            "2. 该步骤列出的产物文件全部写好后，调用 "
            'host.plan_update("<step_id>", "completed")。若某步确实无法完成，'
            '调用 host.plan_update("<step_id>", "failed", note="原因") 后继续下一步。\n'
            "3. 按顺序逐步推进，把每一步的结果文件写到工作目录（会自动成为产物）。\n"
            "4. 严格遵守我在原始任务中提出的所有约束（例如：最终总结里不要对大于约 "
            "1MB 的原始数据文件使用 Markdown 链接，只按文件名引用）。\n"
            "5. 全部完成后写一段简洁的最终总结，并调用 host.submit_output(...)。"
        )

    #: A step is settled once the agent has recorded a *decision* about it:
    #: done, cannot be done, or deliberately not done. It is unsettled only
    #: while no decision exists -- `pending`, or `in_progress` when the turn
    #: that owned it ended.
    #:
    #: Stated as the rule rather than as a list, because listing it is how
    #: `skipped` was left out. The old comment argued precisely why `failed` is
    #: a decision and not an interruption -- "the execution seed tells the agent
    #: to mark a step failed with a reason *and carry on*, so re-running it on
    #: resume would redo work someone already concluded cannot be done" -- and
    #: `skipped` is a more explicit decision than `failed` is. It is in
    #: `PLAN_STEP_STATUSES`, `host.plan_update` accepts it, and `app.js` renders
    #: it with its own glyph; only this partition had never heard of it, so
    #: every skipped step was re-run on resume.
    _SETTLED_STEP_STATUSES = SETTLED_STEP_STATUSES

    def unfinished_steps(self, plan: dict[str, Any]) -> list[dict[str, Any]]:
        """The steps a resume still has to run.

        `in_progress` counts as unfinished. A step in that state when the plan
        paused was interrupted part-way -- the pause is usually a cancel or a
        daemon that went away -- and there is no record of how far it got, so
        the honest thing is to run it again rather than assume it landed.
        """
        step_status = plan.get("step_status") or {}
        remaining = []
        for index, step in enumerate(plan.get("steps") or []):
            step_id = step.get("id") or f"s{index + 1}"
            status = (step_status.get(step_id) or {}).get("status") or step.get(
                "status"
            )
            if status not in self._SETTLED_STEP_STATUSES:
                remaining.append(step)
        return remaining

    def _status_after_turn(self, plan_id: str, final_status: str) -> str:
        """The plan status a finished execution turn may write.

        A completed turn is not a completed plan while a step is still
        `in_progress`. The model can mark the last step in progress and call
        `host.submit_output` in the same Cell, which ends the turn before it
        records the step's outcome; mapping that turn straight to `completed`
        stored -- and the card showed -- PLAN COMPLETE next to a step still in
        progress, permanently, since only a paused plan can resume.

        Nothing records whether that step's work landed, so it is not settled
        on the agent's behalf. The plan pauses instead: the step stays in
        progress, `unfinished_steps` already counts it, and Resume finishes it.
        A step the turn never started (`pending`) keeps the existing mapping.
        """
        if final_status != "completed":
            return final_status
        plan = self.store.get_plan(plan_id) or {}
        step_status = plan.get("step_status") or {}
        for index, step in enumerate(plan.get("steps") or []):
            step_id = step.get("id") or f"s{index + 1}"
            status = (step_status.get(step_id) or {}).get("status") or step.get(
                "status"
            )
            if status == "in_progress":
                return "paused"
        return final_status

    def resume_seed(self, plan: dict[str, Any], remaining: list[dict[str, Any]]) -> str:
        """The execution seed for a resume: only what is left, and a standing
        instruction not to redo the rest.

        Listing the finished steps by name is deliberate. Sending only the
        remainder would leave the agent to infer that earlier work exists, and
        a plan whose first three steps produced files is one where "start from
        the top" quietly overwrites them.
        """
        zh = self.plan_language(plan) == "zh"
        settled = []
        step_status = plan.get("step_status") or {}
        for index, step in enumerate(plan.get("steps") or []):
            step_id = step.get("id") or f"s{index + 1}"
            status = (step_status.get(step_id) or {}).get("status") or step.get(
                "status"
            )
            if status in self._SETTLED_STEP_STATUSES:
                settled.append(
                    f"- [{step_id}] {step.get('title', '')}（{status}）"
                    if zh
                    else f"- [{step_id}] {step.get('title', '')} ({status})"
                )
        lines = self._step_lines(remaining, zh)
        if not zh:
            settled_text = (
                "These steps already reached a conclusion in the previous run; "
                "**do not redo them or overwrite their deliverables**:\n"
                + "\n".join(settled)
                + "\n\n"
                if settled
                else ""
            )
            return (
                f'Continue executing plan "{plan.get("title", "")}" (the previous '
                "run was interrupted).\n\n"
                + settled_text
                + "Steps still to complete:\n"
                + "\n".join(lines)
                + "\n\n"
                "Execution rules:\n"
                '1. Before starting each step, call host.plan_update("<step_id>", '
                '"in_progress").\n'
                "2. Once every deliverable file listed for the step is written, "
                'call host.plan_update("<step_id>", "completed"). If a step '
                'genuinely cannot be completed, call host.plan_update("<step_id>", '
                '"failed", note="reason") and continue with the next step.\n'
                "3. Only work on the steps listed above; do not re-run steps that "
                "already have a conclusion.\n"
                "4. Strictly follow every constraint from my original task.\n"
                "5. When everything is done, write a concise final summary and "
                "call host.submit_output(...)."
            )
        settled_text = (
            "以下步骤在上一次执行中已经有结论，**不要重做、不要覆盖它们的产物**：\n"
            + "\n".join(settled)
            + "\n\n"
            if settled
            else ""
        )
        return (
            f"继续执行计划「{plan.get('title', '')}」（上一次执行被中断）。\n\n"
            + settled_text
            + "还需要完成的步骤：\n"
            + "\n".join(lines)
            + "\n\n"
            "执行规则：\n"
            '1. 每开始一个步骤前，先调用 host.plan_update("<step_id>", '
            '"in_progress")。\n'
            "2. 该步骤列出的产物文件全部写好后，调用 "
            'host.plan_update("<step_id>", "completed")。若某步确实无法完成，'
            '调用 host.plan_update("<step_id>", "failed", note="原因") 后继续下一步。\n'
            "3. 只推进上面列出的步骤；已有结论的步骤不要重跑。\n"
            "4. 严格遵守我在原始任务中提出的所有约束。\n"
            "5. 全部完成后写一段简洁的最终总结，并调用 host.submit_output(...)。"
        )

    def claim_approval(self, root_frame_id: str) -> dict[str, Any]:
        """Take this frame's plan from `draft` to `executing`, or say why not.

        `claim_resume` below has done this for the resume path since the race it
        documents was found; approve had the same shape and none of the fix. It
        read the status inside `run_execution`, which the web route reaches only
        *after* answering 202 on a background thread -- so two POSTs both read
        `draft`, both were accepted, and both turns executed the same steps
        against the same session. A read followed by an unconditional write is
        not a claim no matter which thread it runs on.

        Refused per-status for the same reason: the caller's next move differs
        between "somebody else is already running it" and "this one is
        finished", and one flat "cannot approve" tells them neither.
        """
        plan = self.store.get_plan_by_frame(root_frame_id)
        if not plan:
            return {
                "ok": False,
                "plan_id": None,
                "plan_status": None,
                "error": "no plan to approve",
            }
        plan_id = str(plan["plan_id"])
        if self.store.compare_and_set_plan_status(
            plan_id, expected="draft", new_status="executing"
        ):
            return {
                "ok": True,
                "plan_id": plan_id,
                "plan_status": "executing",
                "plan": self.store.get_plan(plan_id),
            }
        current = self.store.get_plan(plan_id) or {}
        status = current.get("status")
        return {
            "ok": False,
            "plan_id": plan_id,
            "plan_status": status,
            "error": f"only a draft plan can be approved; this one is "
            f"{status or 'absent'}",
        }

    def claim_resume(self, root_frame_id: str) -> dict[str, Any]:
        """Take this frame's plan from `paused` to `executing`, or say why not.

        The claim is a compare-and-swap, not a status check, and it is the
        whole point of this method existing separately from
        `resume_execution`: the web route answers 202 and *then* runs the plan
        on a background thread, so if the transition is not already made by the
        time that 202 is written, two requests can both be accepted and both
        turns can execute the same steps.

        Refused per-status rather than with one "cannot resume", because the
        caller's next move differs: approve a draft, wait for an executing one,
        do nothing for a finished one. The status is read after the swap has
        already lost, so it describes the row as it is now -- for the loser of
        a real race that is `executing`, which is exactly the advice it needs.
        """
        plan = self.store.get_plan_by_frame(root_frame_id)
        if not plan:
            return {
                "ok": False,
                "plan_id": None,
                "plan_status": None,
                "error": "no plan to resume",
            }
        plan_id = str(plan["plan_id"])
        if self.store.compare_and_set_plan_status(
            plan_id, expected="paused", new_status="executing"
        ):
            return {
                "ok": True,
                "plan_id": plan_id,
                "plan_status": "executing",
                "plan": self.store.get_plan(plan_id),
            }
        current = self.store.get_plan(plan_id) or {}
        status = current.get("status")
        return {
            "ok": False,
            "plan_id": plan_id,
            "plan_status": status,
            "error": f"only a paused plan can resume; this one is "
            f"{status or 'absent'}",
        }

    def resume_execution(
        self,
        root_frame_id: str,
        project_id: str,
        model: str | None = None,
        *,
        claimed_plan_id: str | None = None,
    ) -> dict[str, Any]:
        """Run the steps a paused plan has left, and nothing else.

        `claim_resume` is what enforces "only paused resumes", and being a
        compare-and-swap it also enforces "only once". `claimed_plan_id` is
        passed by the web route, which has to claim synchronously for its 202
        to mean anything; claiming again here would look for a `paused` row
        that the route already moved and refuse its own turn.

        Goes through the same `run_message` the approve path uses, so the
        resumed turn is an ordinary FIFO-owned execution with its own owner and
        lease, and cancelling it pauses the plan again exactly as before.
        """
        if claimed_plan_id is None:
            claim = self.claim_resume(root_frame_id)
            if not claim["ok"]:
                return {
                    "status": "failed",
                    "frame_id": root_frame_id,
                    "plan_id": claim["plan_id"],
                    "plan_status": claim["plan_status"],
                    "error": claim["error"],
                }
            plan = claim["plan"]
        else:
            # Claimed by the route; the row is already `executing`.
            plan = self.store.get_plan(claimed_plan_id)
        if not plan:
            return {
                "status": "failed",
                "frame_id": root_frame_id,
                "error": "no plan to resume",
            }

        remaining = self.unfinished_steps(plan)
        if not remaining:
            # Every step settled while the plan sat paused. Completing it is
            # the truthful end state, and it also clears the row -- a paused
            # plan keeps shadowing new drafts, because `get_by_frame` prefers
            # the newest non-discarded one.
            self.store.update_plan(plan["plan_id"], status="completed")
            refreshed = self.store.get_plan(plan["plan_id"])
            self.emit_ready(self.emitter_for(root_frame_id), root_frame_id, refreshed)
            return {
                "status": "completed",
                "frame_id": root_frame_id,
                "plan_id": plan["plan_id"],
                "plan_status": "completed",
                "resumed_steps": 0,
            }

        emit = self.emitter_for(root_frame_id)
        # No status write here: the claim already made it, and writing
        # `executing` unconditionally is precisely the step that let a second
        # resume overwrite a first one's claim.
        self.emit_ready(emit, root_frame_id, plan)
        result = self.run_message(
            root_frame_id,
            project_id,
            self.resume_seed(plan, remaining),
            model,
            plan=False,
        )
        turn_status = result.get("status")
        if turn_status == "completed":
            final_status = "completed"
        elif turn_status == "failed":
            final_status = "failed"
        elif turn_status == "cancelled":
            final_status = "paused"  # same reasoning as run_execution
        else:
            final_status = (
                self.store.get_plan(plan["plan_id"]).get("status") or "completed"
            )
        final_status = self._status_after_turn(plan["plan_id"], final_status)
        if final_status in ("completed", "failed", "paused"):
            self.store.update_plan(plan["plan_id"], status=final_status)
        self.emit_ready(emit, root_frame_id, self.store.get_plan(plan["plan_id"]))
        result["plan_id"] = plan["plan_id"]
        result["plan_status"] = final_status
        result["resumed_steps"] = len(remaining)
        return result

    def run_execution(
        self,
        root_frame_id: str,
        project_id: str,
        model: str | None = None,
        *,
        claimed_plan_id: str | None = None,
    ) -> dict[str, Any]:
        """Approve a draft and execute it through the normal agent turn.

        `claimed_plan_id` is passed by the web route, which claims synchronously
        so its 202 means something; claiming again here would look for a `draft`
        row the route already moved and refuse its own turn. The same contract
        `resume_execution` has, for the same reason.
        """
        if claimed_plan_id is not None:
            # Claimed by the route; the row is already `executing`.
            plan = self.store.get_plan(claimed_plan_id)
            if not plan:
                return {
                    "status": "failed",
                    "frame_id": root_frame_id,
                    "error": "no plan to approve",
                }
        else:
            claim = self.claim_approval(root_frame_id)
            if not claim["ok"]:
                return {
                    "status": "failed",
                    "frame_id": root_frame_id,
                    "plan_id": claim["plan_id"],
                    "plan_status": claim["plan_status"],
                    "error": claim["error"],
                }
            plan = claim["plan"]
        if not plan:
            return {
                "status": "failed",
                "frame_id": root_frame_id,
                "error": "no plan to approve",
            }

        emit = self.emitter_for(root_frame_id)
        # The swap above already wrote `executing`; this is where the
        # unconditional write used to be, and it is gone rather than moved.
        self.emit_ready(
            emit,
            root_frame_id,
            self.store.get_plan(plan["plan_id"]),
        )
        result = self.run_message(
            root_frame_id,
            project_id,
            self.execution_seed(plan),
            model,
            plan=False,
        )
        turn_status = result.get("status")
        if turn_status == "completed":
            final_status = "completed"
        elif turn_status == "failed":
            final_status = "failed"
        elif turn_status == "cancelled":
            # Cancelling leaves work to finish, so the plan is paused, not
            # over. It used to fall through to "keep whatever is stored",
            # which was `executing` -- and since `get_by_frame` prefers the
            # newest non-discarded plan, that row then shadowed every new
            # draft for the session, permanently.
            final_status = "paused"
        else:
            final_status = (
                self.store.get_plan(plan["plan_id"]).get("status") or "completed"
            )
        final_status = self._status_after_turn(plan["plan_id"], final_status)
        if final_status in ("completed", "failed", "paused"):
            self.store.update_plan(plan["plan_id"], status=final_status)
        self.emit_ready(
            emit,
            root_frame_id,
            self.store.get_plan(plan["plan_id"]),
        )
        result["plan_id"] = plan["plan_id"]
        result["plan_status"] = final_status
        return result

    def run_revision(
        self,
        root_frame_id: str,
        project_id: str,
        changes: str,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Regenerate a draft through a plan-only agent turn.

        The seed opens with the plan-mode marker, so the plan turn it starts
        does not append the generic draft instruction on top of it.
        """
        plan = self.store.get_plan_by_frame(root_frame_id) if self.store else None
        if self.plan_language(plan, changes) == "zh":
            seed = (
                "[计划模式] 请根据下面的修改意见，重新拟定上面的执行计划，并再次只输出："
                "一段简短的方案说明（散文）＋ 一个 ```json 代码块（"
                "{title, rationale, confidence, steps:[{id,title,detail,deliverables}]} "
                "结构，与之前一致）。不要执行、不要调用任何工具。\n\n修改意见："
                + changes
            )
        else:
            seed = (
                "[Plan Mode] Revise the execution plan above according to the "
                "change requests below, and again output only: a brief description "
                "of the approach (prose) + one ```json code block (the same "
                "{title, rationale, confidence, steps:[{id,title,detail,deliverables}]} "
                "structure as before). Do not execute anything or call any tools."
                "\n\nChange requests: " + changes
            )
        return self.run_message(
            root_frame_id,
            project_id,
            seed,
            model,
            plan=True,
        )


def short_hash(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:8]


def slugify(text: str, maxlen: int = 44) -> str:
    value = re.sub(r"[^\w\s-]", "", (text or "").lower())
    value = re.sub(r"[\s_-]+", "-", value).strip("-")
    return value[:maxlen].strip("-") or "plan"


def plan_filename(title: str, root_frame_id: str) -> str:
    return f"plan_{slugify(title)}_{short_hash(root_frame_id)}.json"


def _try_json(text: str) -> Any:
    try:
        return json.loads((text or "").strip())
    except (ValueError, TypeError):
        return None


def _first_json_object(text: str) -> Any:
    """Return the first balanced JSON object in ``text`` that parses."""
    start = (text or "").find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return _try_json(text[start : index + 1])
    return None


def extract_plan_json(reply: str) -> Any:
    """Extract a plan object from a fenced or bare planner response."""
    if not reply:
        return None
    for match in re.finditer(r"```json\s*\n(.*?)```", reply, re.DOTALL | re.IGNORECASE):
        value = _try_json(match.group(1))
        if isinstance(value, dict) and ("steps" in value or "title" in value):
            return value
    for match in re.finditer(r"```[a-zA-Z0-9]*\s*\n(.*?)```", reply, re.DOTALL):
        value = _try_json(match.group(1))
        if isinstance(value, dict) and "steps" in value:
            return value
    value = _first_json_object(reply)
    if isinstance(value, dict) and "steps" in value:
        return value
    return None


_PLAN_NUM_LINE_RE = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+(.*)$")


def _steps_from_prose(prose: str) -> list[dict[str, Any]]:
    """Turn a numbered or bulleted prose list into canonical plan steps."""
    steps: list[dict[str, Any]] = []
    for line in (prose or "").splitlines():
        match = _PLAN_NUM_LINE_RE.match(line)
        if not match:
            continue
        text = match.group(1).strip()
        text = re.sub(r"^\*\*(.+?)\*\*", r"\1", text)
        if not text:
            continue
        head = re.split(r"\s[—:：-]\s", text, maxsplit=1)
        steps.append(
            {
                "id": f"s{len(steps) + 1}",
                "title": head[0].strip()[:120],
                "detail": head[1].strip() if len(head) > 1 else "",
                "deliverables": [],
            }
        )
        if len(steps) >= 24:
            break
    return steps


def normalize_plan(
    raw: Any,
    prose: str = "",
    task_hint: str = "",
) -> dict[str, Any]:
    """Coerce a loose planner object into the canonical persisted shape."""
    raw = raw if isinstance(raw, dict) else {}
    steps: list[dict[str, Any]] = []
    source = raw.get("steps")
    if isinstance(source, list):
        for index, step in enumerate(source):
            if isinstance(step, str):
                step = {"title": step}
            if not isinstance(step, dict):
                continue
            deliverables = (
                step.get("deliverables")
                or step.get("outputs")
                or step.get("files")
                or []
            )
            if isinstance(deliverables, str):
                deliverables = [deliverables]
            steps.append(
                {
                    "id": str(step.get("id") or f"s{index + 1}"),
                    "title": (
                        str(
                            step.get("title")
                            or step.get("content")
                            or step.get("name")
                            or ""
                        ).strip()
                        or f"Step {index + 1}"
                    ),
                    "detail": str(
                        step.get("detail")
                        or step.get("description")
                        or step.get("summary")
                        or ""
                    ).strip(),
                    "deliverables": [
                        str(deliverable) for deliverable in deliverables if deliverable
                    ],
                }
            )
    if not steps:
        steps = _steps_from_prose(prose)
    confidence = raw.get("confidence")
    if isinstance(confidence, (int, float)):
        confidence = (
            "high" if confidence >= 0.75 else "low" if confidence < 0.4 else "medium"
        )
    confidence = (str(confidence).strip() or None) if confidence is not None else None
    return {
        "title": (
            str(raw.get("title") or "").strip()
            or (task_hint[:80] if task_hint else "")
            or "执行计划"
        ),
        "rationale": str(raw.get("rationale") or raw.get("reasoning") or "").strip(),
        "confidence": confidence,
        "steps": steps,
    }


def public_plan(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    """Fold live step progress into the plan shape sent to the web client."""
    if not plan:
        return None
    step_status = plan.get("step_status") or {}
    steps = []
    for step in plan.get("steps") or []:
        public_step = dict(step)
        public_step["status"] = (
            (step_status.get(step.get("id")) or {}).get("status")
            or step.get("status")
            or "pending"
        )
        steps.append(public_step)
    return {
        "plan_id": plan.get("plan_id"),
        "title": plan.get("title"),
        "rationale": plan.get("rationale"),
        "confidence": plan.get("confidence"),
        "steps": steps,
        "status": plan.get("status"),
        "artifact_id": plan.get("artifact_id"),
    }
