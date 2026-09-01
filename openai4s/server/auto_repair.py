"""Stage 5 Auto-fix / re-review loop.

Repair runs through a dedicated executor port, never through the Reviewer.
The Reviewer remains read-only. A repair that produces identical bytes must
reuse the previous Artifact version. Budget exhaustion and unchanged finding
fingerprints stop as ``completed_with_issues`` / ``loop_detected``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from openai4s.server.auto_budget import (
    TERMINAL_USER_TRUTH,
    AutoBudgetAdmission,
    AutoBudgetDenied,
    finding_set_digest,
)
from openai4s.server.evidence_snapshot import freeze_evidence_snapshot
from openai4s.storage.auto_mode import AutoModeConflictError

RepairFn = Callable[[Mapping[str, Any], Sequence[Mapping[str, Any]]], Mapping[str, Any]]


def _fingerprint_set(findings: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    return tuple(
        sorted(
            str(item.get("fingerprint") or "")
            for item in findings
            if item.get("severity") in {"high", "medium"}
        )
    )


def apply_claim_repair(
    snapshot: Mapping[str, Any],
    findings: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Deterministic Repair Agent: correct claims from frozen adapter evidence.

    This is the shipped default executor. It never writes the formal workspace
    except through an optional ``artifact_writer`` supplied by the caller. It
    cannot mark its own work as Verified.
    """

    answer = str(snapshot.get("candidate_answer") or "")
    adapters = [
        item for item in (snapshot.get("adapters") or []) if isinstance(item, Mapping)
    ]
    changed = False
    after_versions = [
        str(item.get("version_id"))
        for item in (snapshot.get("artifacts") or [])
        if isinstance(item, Mapping) and item.get("version_id")
    ]
    mean_claims: list[tuple[str, Any]] = []
    for adapter in adapters:
        if adapter.get("adapter") != "table" or adapter.get("complete") is not True:
            continue
        summary = adapter.get("summary") or {}
        row_count = summary.get("row_count")
        columns = (
            summary.get("columns")
            if isinstance(summary.get("columns"), Mapping)
            else {}
        )
        if type(row_count) is int:
            new_answer = _replace_claim(answer, r"n\s*=\s*\d+", f"n={row_count}")
            if new_answer != answer:
                answer = new_answer
                changed = True
        for name, stats in columns.items():
            if not isinstance(stats, Mapping):
                continue
            if "mean" in stats:
                mean = stats["mean"]
                column_name = str(name)
                mean_claims.append((column_name, mean))
                new_answer = _replace_claim(
                    answer,
                    rf"mean\s+of\s+{re.escape(column_name)}\s*[=:]\s*"
                    r"[-+]?\d+(?:\.\d+)?",
                    f"mean of {column_name}={mean}",
                )
                if new_answer != answer:
                    answer = new_answer
                    changed = True
            nulls = stats.get("null_count")
            if type(nulls) is int:
                missing_claim = f"missing values in {name}={nulls}"
                if (
                    _replace_claim(answer, r"no missing values", missing_claim)
                    != answer
                ):
                    answer = _replace_claim(answer, r"no missing values", missing_claim)
                    changed = True
                elif "missing" in answer.lower() and missing_claim not in answer:
                    answer = answer + " " + missing_claim
                    changed = True
    # An unqualified claim is only attributable when the complete evidence has
    # exactly one mean. With two columns (or two tables), rewriting the first
    # ``mean=...`` for every column silently assigns the wrong statistic.
    if len(mean_claims) == 1:
        _column_name, mean = mean_claims[0]
        new_answer = _replace_claim(
            answer,
            r"mean\s*[=:]\s*[-+]?\d+(?:\.\d+)?",
            f"mean={mean}",
        )
        if new_answer != answer:
            answer = new_answer
            changed = True
    for finding in findings:
        if finding.get("category") != "missing_artifact":
            continue
        # Cannot invent a formal Artifact. Leave the finding open.
        continue
    return {
        "candidate_answer": answer,
        "after_version_ids": after_versions,
        "changed": changed,
        "self_certified": False,
    }


def _replace_claim(text: str, pattern: str, replacement: str) -> str:
    return re.sub(pattern, lambda _match: replacement, text, count=1, flags=re.I)


class AutoRepairService:
    """Bounded repair/re-review supervisor. Reviewer is never the executor."""

    def __init__(
        self,
        *,
        store: Any,
        config: Any,
        scientific_review: Any,
        repair_fn: RepairFn | None = None,
    ) -> None:
        self.store = store
        self.config = config
        self.scientific_review = scientific_review
        self.repair_fn = repair_fn or apply_claim_repair

    @property
    def feature_enabled(self) -> bool:
        flags = getattr(self.config, "roadmap_features", None)
        return bool(getattr(flags, "stage5_auto_repair", False))

    def run(
        self,
        *,
        initial: Mapping[str, Any],
        result_review_mode: str,
        agent_cfg: Any,
        reviewer_cfg: Any,
        run_id: str | None = None,
        checkpoint_id: str | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        if not self.feature_enabled:
            return dict(initial)
        if result_review_mode != "auto_fix":
            return dict(initial)
        budgets = getattr(getattr(self.config, "auto_mode", None), "budgets", None)
        # `or 2` would turn an explicit, in-range `0` back into 2 rounds:
        # `max_repair_rounds` is declared with minimum=0 precisely so an
        # operator can disable auto-repair, and that setting was unreachable.
        configured = getattr(budgets, "max_repair_rounds", None)
        try:
            max_rounds = 2 if configured is None else int(configured)
        except (TypeError, ValueError):
            max_rounds = 2
        if max_rounds < 0:
            max_rounds = 0
        current = dict(initial)
        previous_prints: list[tuple[str, ...]] = [
            _fingerprint_set(current.get("findings") or [])
        ]
        budget = self._auto_budget()
        for round_index in range(max_rounds):
            if cancel is not None and cancel():
                current["cancelled"] = True
                return current
            material = [
                item
                for item in (current.get("findings") or [])
                if item.get("severity") in {"high", "medium"}
            ]
            if current.get("verdict") == "pass" or not material:
                return current
            prints = _fingerprint_set(material)
            if previous_prints.count(prints) >= 2:
                current["stop_reason"] = "loop_detected"
                current["verdict"] = "issues"
                return current
            admission_id = None
            finding_admission_id = None
            if budget is not None and run_id:
                admission_id = f"{run_id}:repair:{round_index}"
                digest = finding_set_digest(prints)
                # Held in a variable because it has to be settled below. It
                # was previously built inline, so the only id that reached
                # commit/mark_unknown was the repair one: every
                # `repeated_finding` reservation stayed `reserved` for the
                # life of the run, and since reserved counts against
                # remaining, the budget only ever shrank.
                finding_admission_id = f"{run_id}:finding:{digest}:{round_index}"
                try:
                    budget.reserve(
                        run_id=str(run_id),
                        admission_id=finding_admission_id,
                        consumer="repeated_finding",
                        action_group_id=f"{digest}:{round_index}",
                        amount=1,
                    )
                    budget.reserve(
                        run_id=str(run_id),
                        admission_id=admission_id,
                        consumer="repair",
                        action_group_id=admission_id,
                        amount=1,
                    )
                except AutoBudgetDenied as denied:
                    return self._budget_stop(current, denied.reason)
            snapshot = dict(current.get("snapshot") or {})
            try:
                repair_payload = dict(self.repair_fn(snapshot, material))
            except Exception:
                if budget is not None:
                    for pending in (admission_id, finding_admission_id):
                        if pending:
                            budget.mark_unknown(pending)
                raise
            if budget is not None:
                for settled in (admission_id, finding_admission_id):
                    if settled:
                        budget.commit(settled, committed_amount=1)
            if repair_payload.get("self_certified"):
                raise RuntimeError("Repair Agent cannot certify its own review")
            if run_id and checkpoint_id:
                self._persist_repair_round(
                    run_id=run_id,
                    round_index=round_index,
                    material=material,
                    snapshot=snapshot,
                    repair_payload=repair_payload,
                    checkpoint_id=checkpoint_id,
                )
            if not repair_payload.get("changed"):
                current["stop_reason"] = "loop_detected"
                return current
            repaired_snapshot = freeze_evidence_snapshot(
                {
                    **{k: v for k, v in snapshot.items() if k != "snapshot_sha256"},
                    "candidate_answer": repair_payload.get("candidate_answer"),
                    "artifacts": self._reuse_identical_versions(
                        snapshot.get("artifacts") or [],
                        repair_payload.get("artifacts"),
                    ),
                }
            )
            current = self.scientific_review.evaluate(
                repaired_snapshot,
                result_review_mode=result_review_mode,
                agent_cfg=agent_cfg,
                reviewer_cfg=reviewer_cfg,
                cancel=cancel,
                run_id=run_id,
            )
            previous_prints.append(_fingerprint_set(current.get("findings") or []))
        current["stop_reason"] = current.get("stop_reason") or "budget_exhausted"
        if current.get("verdict") == "pass":
            return current
        if budget is not None and run_id:
            return self._budget_stop(
                current, str(current.get("stop_reason") or "budget_exhausted")
            )
        current["verdict"] = "issues"
        return current

    def _auto_budget(self) -> AutoBudgetAdmission | None:
        store = self.store
        if store is None or not callable(
            getattr(store, "reserve_auto_mode_budget", None)
        ):
            return None
        budgets = getattr(getattr(self.config, "auto_mode", None), "budgets", None)
        return AutoBudgetAdmission(store, budgets)

    @staticmethod
    def _budget_stop(current: dict[str, Any], reason: str) -> dict[str, Any]:
        stopped = dict(current)
        stopped["stop_reason"] = reason
        stopped["reason"] = reason
        if stopped.get("verdict") == "pass":
            stopped["verdict"] = "review_unavailable"
        elif reason in TERMINAL_USER_TRUTH:
            stopped["verdict"] = "review_unavailable"
            stopped["summary"] = TERMINAL_USER_TRUTH[reason]
        else:
            stopped["verdict"] = "issues"
        return stopped

    def _reuse_identical_versions(
        self, before: Sequence[Any], after: Sequence[Any] | None
    ) -> list[Any]:
        if not after:
            return [dict(item) for item in before if isinstance(item, Mapping)]
        reused = []
        before_by_id = {
            str(item.get("artifact_id")): item
            for item in before
            if isinstance(item, Mapping)
        }
        for item in after:
            if not isinstance(item, Mapping):
                continue
            previous = before_by_id.get(str(item.get("artifact_id")))
            if (
                previous
                and previous.get("checksum")
                and previous.get("checksum") == item.get("checksum")
            ):
                reused.append(dict(previous))
            else:
                reused.append(dict(item))
        return reused

    def _persist_repair_round(
        self,
        *,
        run_id: str,
        round_index: int,
        material: Sequence[Mapping[str, Any]],
        snapshot: Mapping[str, Any],
        repair_payload: Mapping[str, Any],
        checkpoint_id: str,
    ) -> None:
        finding_ids = [
            str(item.get("finding_id")) for item in material if item.get("finding_id")
        ]
        if not finding_ids:
            return
        before = [
            str(item.get("version_id"))
            for item in (snapshot.get("artifacts") or [])
            if isinstance(item, Mapping) and item.get("version_id")
        ]
        repair_id = f"repair-{run_id}-{round_index}"
        try:
            self.store.start_auto_mode_repair(
                run_id,
                repair_run_id=repair_id,
                idempotency_key=f"{repair_id}:start",
                finding_ids=finding_ids,
                before_version_ids=before,
                checkpoint_id=checkpoint_id,
            )
            self.store.complete_auto_mode_repair(
                repair_id,
                idempotency_key=f"{repair_id}:complete",
                status="completed",
                after_version_ids=list(
                    repair_payload.get("after_version_ids") or before
                ),
                execution_group_ids=[],
            )
        except (AutoModeConflictError, ValueError, PermissionError, KeyError):
            return
