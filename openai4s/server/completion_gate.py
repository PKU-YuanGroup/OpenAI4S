"""Stage 4 completion gate: candidate → review → promotion.

When the Stage 4 flag is on and result review is selected, the existing
answer remains provisional until a durable review judgment is recorded.
Promotion never happens before that review event. Repair is still Stage 5:
``auto_fix`` issues stop as ``completed_with_issues``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from openai4s.server.auto_mode import public_auto_event
from openai4s.server.auto_repair import AutoRepairService
from openai4s.storage.auto_mode import AutoModeConflictError

EventSink = Callable[[dict], None]
REVIEW_GATE_SETTING = "review-gate:"


def terminal_for_review(result: Mapping[str, Any]) -> tuple[str, str]:
    """Map a Stage 3 review result onto a Stage 4 user-visible terminal."""

    verdict = str(result.get("verdict") or "")
    findings = list(result.get("findings") or [])
    material = [item for item in findings if item.get("severity") in {"high", "medium"}]
    if verdict == "review_unavailable" or result.get("status") == "unavailable":
        reason = str(result.get("reason") or "reviewer_inference_failed")
        return "review_unavailable", f"Unavailable · not verified ({reason})"
    if verdict == "incomplete":
        return (
            "review_unavailable",
            "Unavailable · not verified (evidence_incomplete)",
        )
    if verdict == "issues" or material:
        return (
            "completed_with_issues",
            f"Completed · unverified · {len(material or findings)} unresolved issues",
        )
    if verdict == "pass":
        return "verified", "Verified"
    return "review_unavailable", "Unavailable · not verified"


class CompletionGateService:
    """Promote a provisional candidate only after a durable review."""

    def __init__(
        self,
        *,
        store: Any,
        config: Any,
        scientific_review: Any,
        auto_mode: Any | None = None,
        auto_repair: Any | None = None,
    ) -> None:
        self.store = store
        self.config = config
        self.scientific_review = scientific_review
        self.auto_mode = auto_mode
        self.auto_repair = auto_repair or AutoRepairService(
            store=store, config=config, scientific_review=scientific_review
        )

    @property
    def feature_enabled(self) -> bool:
        flags = getattr(self.config, "roadmap_features", None)
        return bool(getattr(flags, "stage4_review_completion_gate", False))

    def load(self, root_frame_id: str) -> dict[str, Any] | None:
        raw = self.store.get_setting(REVIEW_GATE_SETTING + str(root_frame_id))
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def gate_after_turn(
        self,
        *,
        root_frame_id: str,
        project_id: str,
        branch_id: str,
        turn_id: str,
        execution_id: str,
        user_request: str,
        candidate_answer: str,
        structured_completion: Any = None,
        artifact_versions_before: Mapping[str, Any] | None = None,
        cell_count_before: int = 0,
        step_count_before: int = 0,
        agent_cfg: Any,
        reviewer_cfg: Any,
        emit: EventSink | None = None,
        checkpoint_id: str | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any] | None:
        if not self.feature_enabled:
            return None
        if self.auto_mode is not None:
            try:
                projected = self.auto_mode.get(root_frame_id)
                mode = str(
                    ((projected or {}).get("selection") or {}).get("result_review_mode")
                    or "off"
                )
            except Exception:  # noqa: BLE001
                mode = "off"
            if mode == "off":
                return None
        cursor_before = 0
        if hasattr(self.store, "auto_mode_event_cursor"):
            cursor_before = int(
                self.store.auto_mode_event_cursor(root_frame_id, branch_id=branch_id)
                or 0
            )
        if emit is not None:
            emit(
                {
                    "type": "candidate_ready",
                    "root_frame_id": root_frame_id,
                    "review_status": "candidate",
                    "user_truth": "Candidate · provisional / not verified",
                    "gates_completion": True,
                }
            )
        result = self.scientific_review.shadow_after_turn(
            root_frame_id=root_frame_id,
            project_id=project_id,
            branch_id=branch_id,
            turn_id=turn_id,
            execution_id=execution_id,
            user_request=user_request,
            candidate_answer=candidate_answer,
            structured_completion=structured_completion,
            artifact_versions_before=artifact_versions_before,
            cell_count_before=cell_count_before,
            step_count_before=step_count_before,
            agent_cfg=agent_cfg,
            reviewer_cfg=reviewer_cfg,
            emit=emit,
        )
        if result is None:
            return None
        result = dict(result)
        result["gates_completion"] = True
        mode = "off"
        if self.auto_mode is not None:
            try:
                mode = str(
                    (
                        (self.auto_mode.get(root_frame_id) or {}).get("selection") or {}
                    ).get("result_review_mode")
                    or "off"
                )
            except Exception:  # noqa: BLE001
                mode = "off"
        delivered_answer = str(
            (result.get("snapshot") or {}).get("candidate_answer") or ""
        )
        repaired_but_undelivered = False
        if (
            self.auto_repair is not None
            and getattr(self.auto_repair, "feature_enabled", False)
            and mode == "auto_fix"
            and terminal_for_review(result)[0] == "completed_with_issues"
        ):
            result = dict(
                self.auto_repair.run(
                    initial=result,
                    result_review_mode=mode,
                    agent_cfg=agent_cfg,
                    reviewer_cfg=reviewer_cfg,
                    run_id=f"auto-{root_frame_id}-{turn_id}",
                    checkpoint_id=checkpoint_id,
                    cancel=cancel,
                )
            )
            result["gates_completion"] = True
            # The repair loop rewrites the answer IN MEMORY to compute its
            # verdict, but the message the user is reading was persisted and
            # streamed before this ran. Promoting that to Verified would put a
            # green badge on text the reviewer never approved -- the user would
            # be told "n=100, no missing values" is verified because "n=97,
            # missing values in age=3" passed. Until the repaired candidate is
            # actually delivered, the honest terminal is "not verified".
            repaired_answer = str(
                (result.get("snapshot") or {}).get("candidate_answer") or ""
            )
            repaired_but_undelivered = bool(
                repaired_answer and repaired_answer != delivered_answer
            )
        terminal, user_truth = terminal_for_review(result)
        storage_enabled = bool(
            getattr(self.scientific_review, "storage_enabled", False)
        )
        if terminal == "verified" and repaired_but_undelivered:
            terminal, user_truth = (
                "completed_with_issues",
                "Issues · repaired answer was not delivered",
            )
        if terminal == "verified" and not storage_enabled:
            # `_assert_verified_locked` is the ONLY thing that checks "an
            # independent pass review exists, in the right event order, with no
            # material findings open". It lives behind Stage 2 storage, and the
            # stage flags are independent booleans with no cross-validation, so
            # Stage 3/4/5 without Stage 2 would stamp Verified from an
            # in-memory dict. Verified must come from a durable pass or not at
            # all.
            terminal, user_truth = (
                "review_unavailable",
                "Unavailable · not verified (durable review storage disabled)",
            )
        if storage_enabled and result.get("verdict") is not None:
            try:
                self.store.terminate_auto_mode_run(
                    f"auto-{root_frame_id}-{turn_id}",
                    idempotency_key=f"{turn_id}:terminal",
                    status=terminal,
                    reason=str(result.get("reason") or terminal),
                )
            except (AutoModeConflictError, ValueError, PermissionError, KeyError):
                if terminal == "verified":
                    terminal, user_truth = (
                        "review_unavailable",
                        "Unavailable · not verified (promotion_integrity)",
                    )
        gate = {
            "schema_version": 1,
            "root_frame_id": root_frame_id,
            "turn_id": turn_id,
            "execution_id": execution_id,
            "terminal": terminal,
            "user_truth": user_truth,
            "verdict": result.get("verdict"),
            "reason": result.get("reason"),
            "finding_count": len(result.get("findings") or []),
            "gates_completion": True,
            "unverified": terminal != "verified",
        }
        self.store.set_setting(
            REVIEW_GATE_SETTING + root_frame_id, json.dumps(gate, ensure_ascii=False)
        )
        # Safe here because the caller persists this turn's rows BEFORE the
        # gate runs, so the newest assistant row in the branch is this turn's.
        self._stamp_last_assistant(root_frame_id, branch_id, gate)
        self._publish_new_events(root_frame_id, branch_id, cursor_before, emit)
        if emit is not None:
            emit(
                {
                    "type": "auto_run_terminal",
                    "root_frame_id": root_frame_id,
                    "review_status": terminal,
                    "user_truth": user_truth,
                    "gates_completion": True,
                }
            )
        result["terminal"] = terminal
        result["user_truth"] = user_truth
        result["gate"] = gate
        return result

    def _stamp_last_assistant(
        self, root_frame_id: str, branch_id: str, gate: Mapping[str, Any]
    ) -> None:
        try:
            messages = self.store.list_branch_message_boundaries(
                root_frame_id, branch_id=branch_id, limit=None
            )
        except Exception:  # noqa: BLE001
            messages = self.store.list_messages(root_frame_id)
        last = None
        for item in messages or []:
            if item.get("role") == "assistant":
                last = item
        if not last or not last.get("message_id"):
            return
        try:
            self.store.update_message_metadata(
                str(last["message_id"]),
                {
                    "review_status": gate.get("terminal"),
                    "user_truth": gate.get("user_truth"),
                    "gates_completion": True,
                    "unverified": gate.get("unverified"),
                },
            )
        except Exception:  # noqa: BLE001 - reopen still has the setting
            return

    def _publish_new_events(
        self,
        root_frame_id: str,
        branch_id: str,
        after_cursor: int,
        emit: EventSink | None,
    ) -> None:
        if emit is None:
            return
        try:
            events = self.store.list_auto_mode_events(
                root_frame_id,
                branch_id=branch_id,
                after_cursor=after_cursor,
            )
        except Exception:  # noqa: BLE001
            return
        for event in events or []:
            public = public_auto_event(event)
            if public is None:
                continue
            emit(public)
