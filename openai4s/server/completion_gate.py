"""Stage 4 review gate and post-delivery terminal finalization.

``gate_after_turn`` only reviews a frozen candidate (and optionally runs the
bounded repair pipeline). It returns a proposal; it never stamps a message,
writes a terminal setting, or seals the Auto Mode run. Once the exact answer
is durably deliverable, ``finalize_after_delivery`` asks Store to atomically
promote that exact row/delivery and append the run's one terminal event.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Any

from openai4s.server.auto_mode import public_auto_event
from openai4s.server.auto_repair import AutoRepairService
from openai4s.server.scientific_review import durable_review_matches

EventSink = Callable[[dict], None]
REVIEW_GATE_SETTING = "review-gate:"
_TERMINALS = frozenset(
    {"verified", "completed_with_issues", "review_unavailable", "cancelled"}
)


def terminal_for_review(result: Mapping[str, Any]) -> tuple[str, str]:
    """Map a scientific review result onto a user-visible Stage 4 status."""

    verdict = str(result.get("verdict") or "")
    findings = [
        item for item in (result.get("findings") or []) if isinstance(item, Mapping)
    ]
    material = [item for item in findings if item.get("severity") in {"high", "medium"}]
    if verdict == "review_unavailable" or result.get("status") == "unavailable":
        reason = str(result.get("reason") or "reviewer_inference_failed")
        return "review_unavailable", f"Unavailable · not verified ({reason})"
    if verdict == "incomplete":
        return "review_unavailable", "Unavailable · not verified (evidence_incomplete)"
    if verdict == "issues" or material:
        return (
            "completed_with_issues",
            f"Completed · unverified · {len(material or findings)} unresolved issues",
        )
    if verdict == "pass":
        return "verified", "Verified"
    return "review_unavailable", "Unavailable · not verified"


def message_review_metadata(gate: Mapping[str, Any]) -> dict[str, Any]:
    """Return the one canonical verdict patch for an exact assistant row."""

    terminal = gate.get("terminal") or gate.get("review_status")
    if terminal is None and gate.get("status") in _TERMINALS:
        terminal = gate.get("status")
    metadata = {
        "review_status": terminal,
        "user_truth": gate.get("user_truth"),
        "gates_completion": True,
        "unverified": terminal != "verified",
    }
    proof = gate.get("durable_review")
    if isinstance(proof, Mapping):
        review_run_id = proof.get("review_run_id")
        if isinstance(review_run_id, str) and review_run_id:
            metadata["review_run_id"] = review_run_id
    return metadata


class CompletionGateService:
    """Review a candidate, then seal it only after durable delivery."""

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

    def active_mode(self, root_frame_id: str) -> str:
        """Resolve the mode once; callers pass it back as ``mode_override``."""

        if not self.feature_enabled or self.auto_mode is None:
            return "off" if not self.feature_enabled else "review_only"
        try:
            projected = self.auto_mode.get(root_frame_id)
            selection = (projected or {}).get("selection")
            if (
                not isinstance(selection, Mapping)
                or "result_review_mode" not in selection
            ):
                return "review_only"
            mode = str(selection.get("result_review_mode") or "review_only")
        except Exception:  # noqa: BLE001 - uncertainty must keep the gate armed
            return "review_only"
        return mode if mode in {"off", "review_only", "auto_fix"} else "review_only"

    def _frozen_mode(self, root_frame_id: str, *, mode_override: str | None) -> str:
        if mode_override is None:
            return self.active_mode(root_frame_id)
        mode = str(mode_override)
        return mode if mode in {"off", "review_only", "auto_fix"} else "review_only"

    def gates_turn(
        self, root_frame_id: str, *, mode_override: str | None = None
    ) -> bool:
        if not self.feature_enabled:
            return False
        return self._frozen_mode(root_frame_id, mode_override=mode_override) != "off"

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
        produced_artifacts: list[Mapping[str, Any]] | None = None,
        cell_count_before: int = 0,
        step_count_before: int = 0,
        agent_cfg: Any,
        reviewer_cfg: Any,
        emit: EventSink | None = None,
        checkpoint_id: str | None = None,
        cancel: Callable[[], bool] | None = None,
        mode_override: str | None = None,
        # Compatibility-only no-ops. This method never stamps a row, and a
        # changed repair is always returned for explicit promotion.
        stamp_message: bool | None = None,
        deliver_replacement: bool | None = None,
    ) -> dict[str, Any] | None:
        """Run exactly one review/repair pipeline and return a proposal."""

        del stamp_message
        allow_replacement = deliver_replacement is not False
        if not self.feature_enabled:
            return None
        mode = self._frozen_mode(root_frame_id, mode_override=mode_override)
        if mode == "off":
            return None
        cursor_before = self._event_cursor(root_frame_id, branch_id)
        try:
            reviewed = self.scientific_review.shadow_after_turn(
                root_frame_id=root_frame_id,
                project_id=project_id,
                branch_id=branch_id,
                turn_id=turn_id,
                execution_id=execution_id,
                user_request=user_request,
                candidate_answer=candidate_answer,
                structured_completion=structured_completion,
                artifact_versions_before=artifact_versions_before,
                produced_artifacts=produced_artifacts,
                cell_count_before=cell_count_before,
                step_count_before=step_count_before,
                agent_cfg=agent_cfg,
                reviewer_cfg=reviewer_cfg,
                emit=emit,
                mode_override=mode,
                gates_completion=True,
                round_index=0,
                cancel=cancel,
            )
        except Exception as error:  # noqa: BLE001 - fail closed
            reviewed = {
                "verdict": "review_unavailable",
                "status": "unavailable",
                "reason": "reviewer_inference_failed",
                "summary": str(error)[:300] or "Reviewer inference failed",
                "findings": [],
                "durable_review": self._missing_proof(
                    root_frame_id, branch_id, turn_id, execution_id, error
                ),
            }
        if reviewed is None:
            return None
        result = dict(reviewed)
        result["gates_completion"] = True
        final_answer = candidate_answer
        answer_replaced = False
        repair_attempted = False
        repaired_but_not_deliverable = False
        cancelled = bool(result.get("cancelled")) or self._cancel_requested(cancel)

        if (
            not cancelled
            and self.auto_repair is not None
            and getattr(self.auto_repair, "feature_enabled", False)
            and mode == "auto_fix"
            and terminal_for_review(result)[0] == "completed_with_issues"
        ):
            repair_attempted = True
            try:
                repaired = dict(
                    self.auto_repair.run(
                        initial=result,
                        result_review_mode=mode,
                        agent_cfg=agent_cfg,
                        reviewer_cfg=reviewer_cfg,
                        # Avoid the legacy repair ledger: its empty execution-
                        # group completion can strand a ``repairing`` owner.
                        # The exact final candidate/re-review is persisted below.
                        run_id=None,
                        checkpoint_id=checkpoint_id,
                        cancel=cancel,
                    )
                )
            except Exception as error:  # noqa: BLE001 - preserve initial issues
                repaired = dict(result)
                repaired["stop_reason"] = "repair_failed"
                repaired["repair_error"] = type(error).__name__
            cancelled = bool(repaired.get("cancelled")) or self._cancel_requested(
                cancel
            )
            snapshot = repaired.get("snapshot")
            snapshot = snapshot if isinstance(snapshot, Mapping) else {}
            repaired_answer = str(snapshot.get("candidate_answer") or "")
            if (
                not cancelled
                and repaired_answer
                and repaired_answer != candidate_answer
            ):
                if allow_replacement:
                    final_answer = repaired_answer
                    answer_replaced = True
                    result = repaired
                    try:
                        result = dict(
                            self.scientific_review.persist_review_result(
                                root_frame_id=root_frame_id,
                                project_id=project_id,
                                branch_id=branch_id,
                                turn_id=turn_id,
                                execution_id=execution_id,
                                mode_override=mode,
                                result=result,
                                round_index=1,
                                gates_completion=True,
                                emit=emit,
                            )
                        )
                    except (
                        Exception
                    ) as error:  # noqa: BLE001 - repair stays deliverable
                        result["durable_review"] = self._missing_proof(
                            root_frame_id, branch_id, turn_id, execution_id, error
                        )
                    cancelled = bool(result.get("cancelled")) or self._cancel_requested(
                        cancel
                    )
                else:
                    repaired_but_not_deliverable = True
                    result = repaired
            else:
                result = repaired

        if cancelled:
            # Stop after a review wins over its verdict. Scientific review has
            # already closed the durable owner at this point.
            final_answer = candidate_answer
            answer_replaced = False
            terminal = "cancelled"
            user_truth = "Cancelled · not promoted / not verified"
        else:
            terminal, user_truth = terminal_for_review(result)

        proof_ok = durable_review_matches(
            result,
            candidate_answer=final_answer,
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            turn_id=turn_id,
            execution_id=execution_id,
            gates_completion=True,
        )
        if not cancelled and repaired_but_not_deliverable:
            terminal = "completed_with_issues"
            user_truth = "Completed · repaired answer was not delivered"
            result["reason"] = "repaired_answer_not_delivered"
        elif not cancelled and answer_replaced and not proof_ok:
            # The correction remains useful and may be delivered, but an
            # in-memory re-review is not sufficient to stamp Verified.
            terminal = "completed_with_issues"
            user_truth = "Completed · repaired candidate is not durably re-verified"
        elif terminal == "verified" and not proof_ok:
            if repair_attempted:
                terminal = "completed_with_issues"
                user_truth = "Completed · repaired candidate is not durably re-verified"
            else:
                terminal = "review_unavailable"
                user_truth = "Unavailable · not verified (durable_review_proof_missing)"

        proof_value = result.get("durable_review")
        proof = dict(proof_value) if isinstance(proof_value, Mapping) else {}
        run_id = str(proof.get("run_id") or f"auto-{root_frame_id}-{turn_id}")
        gate = {
            "schema_version": 2,
            "root_frame_id": root_frame_id,
            "branch_id": branch_id,
            "turn_id": turn_id,
            "execution_id": execution_id,
            "run_id": run_id,
            "mode": mode,
            "status": terminal,
            "terminal": terminal,
            "review_status": terminal,
            "user_truth": user_truth,
            "verdict": result.get("verdict"),
            "reason": result.get("reason"),
            "finding_count": len(result.get("findings") or []),
            "gates_completion": True,
            "unverified": terminal != "verified",
            "answer_replaced": answer_replaced,
            "durable_review": proof,
            "finalized": False,
            "durable_terminal": False,
        }
        review_execution_status = result.get("status")
        self._publish_new_events(root_frame_id, branch_id, cursor_before, emit)
        result.update(
            {
                "root_frame_id": root_frame_id,
                "branch_id": branch_id,
                "turn_id": turn_id,
                "execution_id": execution_id,
                "run_id": run_id,
                "mode": mode,
                "status": terminal,
                "review_execution_status": review_execution_status,
                "terminal": terminal,
                "review_status": terminal,
                "user_truth": user_truth,
                "gate": gate,
                "review_metadata": message_review_metadata(gate),
                "final_answer": final_answer,
                "answer_replaced": answer_replaced,
                "durable_review": proof,
                "finalized": False,
                "durable_terminal": False,
            }
        )
        return result

    def finalize_after_delivery(
        self,
        root_frame_id: str,
        branch_id: str,
        result: Mapping[str, Any],
        delivered: bool,
        emit: EventSink | None = None,
        message_id: str | None = None,
        expected_message_content: str | None = None,
        message_metadata: Mapping[str, Any] | None = None,
        promoted_message_content: str | None = None,
        completion_delivery_id: str | None = None,
    ) -> dict[str, Any]:
        """Atomically promote the exact delivery and append one terminal event."""

        current = dict(result)
        terminal = str(
            current.get("terminal")
            or current.get("review_status")
            or current.get("status")
            or "review_unavailable"
        )
        if terminal not in _TERMINALS:
            return self._finalization_failure(current, reason="invalid_review_status")
        final_answer = str(current.get("final_answer") or "")
        gate_value = current.get("gate")
        gate_value = gate_value if isinstance(gate_value, Mapping) else {}
        declared_root = current.get("root_frame_id") or gate_value.get("root_frame_id")
        declared_branch = current.get("branch_id") or gate_value.get("branch_id")
        if (declared_root is not None and str(declared_root) != str(root_frame_id)) or (
            declared_branch is not None and str(declared_branch) != str(branch_id)
        ):
            return self._finalization_failure(
                current, reason="delivery_review_scope_mismatch"
            )
        turn_id = str(current.get("turn_id") or gate_value.get("turn_id") or "")
        execution_id = str(
            current.get("execution_id") or gate_value.get("execution_id") or ""
        )
        proof_value = current.get("durable_review")
        proof = proof_value if isinstance(proof_value, Mapping) else {}
        run_id = str(current.get("run_id") or proof.get("run_id") or "")
        storage_enabled = bool(
            getattr(self.scientific_review, "storage_enabled", False)
            or proof.get("opened") is True
            or proof.get("closed") is True
        )
        if not turn_id or not execution_id or (storage_enabled and not run_id):
            return self._finalization_failure(
                current, reason="durable_review_identity_missing"
            )

        promotion_values = (
            message_id,
            expected_message_content,
            message_metadata,
            promoted_message_content,
            completion_delivery_id,
        )
        promotion_requested = any(value is not None for value in promotion_values)
        if not delivered:
            if promotion_requested:
                return self._finalization_failure(
                    current, reason="promotion_requested_without_delivery"
                )
            if terminal == "cancelled":
                terminal = "cancelled"
                user_truth = "Cancelled · not promoted / not verified"
                current["reason"] = str(current.get("reason") or "cancelled")
            else:
                terminal = "review_unavailable"
                user_truth = "Unavailable · not verified (delivery_unverified)"
                current["reason"] = "delivery_unverified"
            current.update(
                {
                    "status": terminal,
                    "terminal": terminal,
                    "review_status": terminal,
                    "user_truth": user_truth,
                    "unverified": True,
                }
            )
        elif terminal == "cancelled":
            return self._finalization_failure(
                current, reason="cancelled_candidate_cannot_be_delivered"
            )

        if (
            delivered
            and terminal == "verified"
            and not durable_review_matches(
                current,
                candidate_answer=final_answer,
                root_frame_id=root_frame_id,
                branch_id=branch_id,
                turn_id=turn_id,
                execution_id=execution_id,
                gates_completion=True,
            )
        ):
            return self._finalization_failure(
                current, reason="durable_review_proof_mismatch"
            )

        promotion: dict[str, Any] | None = None
        if promotion_requested:
            if (
                not message_id
                or expected_message_content is None
                or not isinstance(message_metadata, Mapping)
            ):
                return self._finalization_failure(
                    current, reason="delivery_promotion_receipt_incomplete"
                )
            promoted = (
                final_answer
                if promoted_message_content is None
                else str(promoted_message_content)
            )
            if promoted != final_answer:
                return self._finalization_failure(
                    current, reason="candidate_delivery_mismatch"
                )
            canonical_metadata = message_review_metadata(current)
            if any(
                message_metadata.get(key) != value
                for key, value in canonical_metadata.items()
            ):
                return self._finalization_failure(
                    current, reason="delivery_review_metadata_mismatch"
                )
            expected_receipt = {
                "turn_id": turn_id,
                "execution_id": execution_id,
                "candidate_content_sha256": hashlib.sha256(
                    str(expected_message_content).encode("utf-8")
                ).hexdigest(),
                "reviewed_content_sha256": hashlib.sha256(
                    promoted.encode("utf-8")
                ).hexdigest(),
            }
            if any(
                message_metadata.get(key) != value
                for key, value in expected_receipt.items()
            ):
                return self._finalization_failure(
                    current, reason="delivery_candidate_receipt_mismatch"
                )
            promotion = {
                "message_id": str(message_id),
                "root_frame_id": str(root_frame_id),
                "branch_id": str(branch_id),
                "frame_id": str(root_frame_id),
                "expected_content": str(expected_message_content),
                "content": promoted,
                "metadata": dict(message_metadata),
            }
            if completion_delivery_id is not None:
                promotion["delivery_id"] = str(completion_delivery_id)

        cursor_before = self._event_cursor(root_frame_id, branch_id)
        transition: Mapping[str, Any] | None = None
        promotion_receipt: Mapping[str, Any] | None = None
        durable_terminal = False
        if storage_enabled:
            terminal_error: Exception | None = None
            # A wrapper may lose the response after SQLite committed. The
            # terminal idempotency key and exact promotion digest make one
            # bounded replay a read, not a second terminal/promotion.
            for _attempt in range(2):
                try:
                    transition = self.store.terminate_auto_mode_run(
                        run_id,
                        idempotency_key=f"{turn_id}:terminal",
                        status=terminal,
                        reason=str(current.get("reason") or terminal),
                        stop_reason=(
                            str(current.get("stop_reason"))
                            if current.get("stop_reason")
                            else None
                        ),
                        **(
                            {"message_promotion": promotion}
                            if promotion is not None
                            else {}
                        ),
                    )
                    break
                except Exception as error:  # noqa: BLE001 - bounded replay
                    terminal_error = error
            if transition is None:
                failed = self._finalization_failure(
                    current, reason="promotion_integrity"
                )
                failed["terminal_error"] = (
                    type(terminal_error).__name__
                    if terminal_error is not None
                    else "unknown"
                )
                return failed
            event = transition.get("event")
            event = event if isinstance(event, Mapping) else {}
            if (
                str(transition.get("run_id") or "") != run_id
                or str(transition.get("status") or "") != terminal
                or str(event.get("type") or "") != "auto_run_terminal"
            ):
                return self._finalization_failure(
                    current, reason="terminal_transition_mismatch"
                )
            durable_terminal = True
        else:
            # Stage 2 is independently configurable. It can never support a
            # Verified terminal, but an exact provisional row can still be
            # durably resolved to a non-green verdict instead of remaining a
            # Candidate forever.
            if not delivered:
                return self._finalization_failure(
                    current, reason="durable_review_run_unavailable"
                )
            if terminal == "verified":
                return self._finalization_failure(
                    current, reason="durable_review_proof_missing"
                )
            if promotion is None:
                return self._finalization_failure(
                    current, reason="durable_promotion_receipt_missing"
                )
            if completion_delivery_id is not None:
                # Without an AutoRun there is no repository transaction that
                # can promote the Stage 1 message, update its committed content
                # hash, and publish the delivery together. Two separate calls
                # would leave a split-brain row if the second one faulted.
                return self._finalization_failure(
                    current, reason="durable_atomic_promotion_unavailable"
                )
            promotion_error: Exception | None = None
            for _attempt in range(2):
                try:
                    promotion_receipt = self.store.promote_candidate_message(
                        message_id=str(message_id),
                        root_frame_id=str(root_frame_id),
                        branch_id=str(branch_id),
                        frame_id=str(root_frame_id),
                        expected_content=str(expected_message_content),
                        content=str(promotion["content"]),
                        metadata=dict(message_metadata or {}),
                    )
                    if str(promotion_receipt.get("message_id") or "") != str(
                        message_id
                    ) or str(promotion_receipt.get("content") or "") != str(
                        promotion["content"]
                    ):
                        raise RuntimeError("candidate message promotion mismatch")
                    promotion_error = None
                    break
                except Exception as error:  # noqa: BLE001 - bounded exact replay
                    promotion_error = error
            if promotion_error is not None:
                failed = self._finalization_failure(
                    current, reason="promotion_integrity"
                )
                failed["promotion_error"] = type(promotion_error).__name__
                return failed

        user_truth = str(current.get("user_truth") or terminal)
        gate = dict(gate_value)
        gate.update(
            {
                "schema_version": 2,
                "root_frame_id": root_frame_id,
                "branch_id": branch_id,
                "turn_id": turn_id,
                "execution_id": execution_id,
                "run_id": run_id,
                "status": terminal,
                "terminal": terminal,
                "review_status": terminal,
                "user_truth": user_truth,
                "unverified": terminal != "verified",
                "finalized": True,
                "durable_terminal": durable_terminal,
                "durable_promotion": promotion is not None,
                **(
                    {"terminal_event_id": transition.get("event_id")}
                    if transition is not None
                    else {}
                ),
                **({"message_id": message_id} if message_id else {}),
            }
        )
        setting_persisted = True
        try:
            self.store.set_setting(
                REVIEW_GATE_SETTING + str(root_frame_id),
                json.dumps(gate, ensure_ascii=False),
            )
        except Exception:  # noqa: BLE001 - terminal event is authoritative
            setting_persisted = False
        self._publish_new_events(root_frame_id, branch_id, cursor_before, emit)
        current.update(
            {
                "status": terminal,
                "terminal": terminal,
                "review_status": terminal,
                "user_truth": user_truth,
                "gate": gate,
                "review_metadata": message_review_metadata(gate),
                "finalized": True,
                "durable_terminal": durable_terminal,
                "durable_promotion": promotion is not None,
                "setting_persisted": setting_persisted,
                **(
                    {"terminal_transition": transition}
                    if transition is not None
                    else {}
                ),
                **(
                    {"promotion_receipt": dict(promotion_receipt)}
                    if promotion_receipt is not None
                    else {}
                ),
                **({"message_id": message_id} if message_id else {}),
            }
        )
        return current

    @staticmethod
    def _missing_proof(
        root_frame_id: str,
        branch_id: str,
        turn_id: str,
        execution_id: str,
        error: Exception,
    ) -> dict[str, Any]:
        return {
            "opened": False,
            "closed": False,
            "gates_completion": True,
            "root_frame_id": root_frame_id,
            "branch_id": branch_id,
            "turn_id": turn_id,
            "execution_id": execution_id,
            "error": type(error).__name__,
        }

    @staticmethod
    def _cancel_requested(cancel: Callable[[], bool] | None) -> bool:
        if cancel is None:
            return False
        try:
            return bool(cancel())
        except Exception:  # noqa: BLE001 - broken cancellation fails closed
            return True

    @staticmethod
    def _finalization_failure(
        result: Mapping[str, Any], *, reason: str
    ) -> dict[str, Any]:
        failed = dict(result)
        truth = f"Unavailable · not verified ({reason})"
        gate_value = failed.get("gate")
        gate = dict(gate_value) if isinstance(gate_value, Mapping) else {}
        gate.update(
            {
                "status": "review_unavailable",
                "terminal": "review_unavailable",
                "review_status": "review_unavailable",
                "user_truth": truth,
                "reason": reason,
                "unverified": True,
                "finalized": False,
                "durable_terminal": False,
            }
        )
        failed.update(
            {
                "status": "review_unavailable",
                "terminal": "review_unavailable",
                "review_status": "review_unavailable",
                "user_truth": truth,
                "reason": reason,
                "gate": gate,
                "review_metadata": message_review_metadata(gate),
                "finalized": False,
                "durable_terminal": False,
            }
        )
        return failed

    def _event_cursor(self, root_frame_id: str, branch_id: str) -> int:
        if not hasattr(self.store, "auto_mode_event_cursor"):
            return 0
        try:
            return int(
                self.store.auto_mode_event_cursor(root_frame_id, branch_id=branch_id)
                or 0
            )
        except Exception:  # noqa: BLE001
            return 0

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
            try:
                public = public_auto_event(event)
                if public is not None:
                    emit(public)
            except Exception:  # noqa: BLE001 - durability already decided
                continue
