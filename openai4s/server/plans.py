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

from openai4s.store import Store

EventSink = Callable[[dict[str, Any]], None]
EmitterFactory = Callable[[str], EventSink]


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
    ) -> dict[str, Any]:
        ...


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
    ) -> None:
        """Capture a planner reply as a draft plan and a JSON artifact."""
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
            return

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

    def execution_seed(self, plan: dict[str, Any]) -> str:
        lines = []
        for index, step in enumerate(plan.get("steps") or []):
            deliverables = "、".join(step.get("deliverables") or []) or "（无指定文件）"
            lines.append(
                f"- [{step.get('id') or ('s' + str(index + 1))}] "
                f"{step.get('title', '')}：{step.get('detail', '')}  "
                f"→ 产出：{deliverables}"
            )
        steps_text = "\n".join(lines)
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

    #: A step the agent explicitly gave up on. `failed` is a decision, not an
    #: interruption: the execution seed tells the agent to mark a step failed
    #: with a reason *and carry on*, so re-running it on resume would redo work
    #: someone already concluded cannot be done.
    _SETTLED_STEP_STATUSES = frozenset({"completed", "failed"})

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

    def resume_seed(self, plan: dict[str, Any], remaining: list[dict[str, Any]]) -> str:
        """The execution seed for a resume: only what is left, and a standing
        instruction not to redo the rest.

        Listing the finished steps by name is deliberate. Sending only the
        remainder would leave the agent to infer that earlier work exists, and
        a plan whose first three steps produced files is one where "start from
        the top" quietly overwrites them.
        """
        settled = []
        step_status = plan.get("step_status") or {}
        for index, step in enumerate(plan.get("steps") or []):
            step_id = step.get("id") or f"s{index + 1}"
            status = (step_status.get(step_id) or {}).get("status") or step.get(
                "status"
            )
            if status in self._SETTLED_STEP_STATUSES:
                settled.append(f"- [{step_id}] {step.get('title', '')}（{status}）")
        lines = []
        for index, step in enumerate(remaining):
            deliverables = "、".join(step.get("deliverables") or []) or "（无指定文件）"
            lines.append(
                f"- [{step.get('id') or ('s' + str(index + 1))}] "
                f"{step.get('title', '')}：{step.get('detail', '')}  "
                f"→ 产出：{deliverables}"
            )
        settled_text = (
            "以下步骤在上一次执行中已经有结论，**不要重做、不要覆盖它们的产物**：\n" + "\n".join(settled) + "\n\n"
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
    ) -> dict[str, Any]:
        """Approve a draft and execute it through the normal agent turn."""
        plan = self.store.get_plan_by_frame(root_frame_id)
        if not plan:
            return {
                "status": "failed",
                "frame_id": root_frame_id,
                "error": "no plan to approve",
            }
        if plan.get("status") in ("executing", "completed"):
            return {
                "status": "failed",
                "frame_id": root_frame_id,
                "error": f"plan already {plan['status']}",
            }

        emit = self.emitter_for(root_frame_id)
        self.store.update_plan(plan["plan_id"], status="executing")
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
        """Regenerate a draft through a plan-only agent turn."""
        seed = (
            "请根据下面的修改意见，重新拟定上面的执行计划，并再次只输出："
            "一段简短的方案说明（散文）＋ 一个 ```json 代码块（"
            "{title, rationale, confidence, steps:[{id,title,detail,deliverables}]} "
            "结构，与之前一致）。不要执行、不要调用任何工具。\n\n修改意见：" + changes
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
