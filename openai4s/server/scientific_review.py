"""Stage 3 Scientific Reviewer V2 shadow orchestration.

Shadow mode delivers the existing answer unchanged and records what the
independent Reviewer would have judged. It never promotes Verified, never
starts Repair, and never writes the formal workspace. Deterministic snapshot
and adapter checks run before any model call so an omitted artifact cannot
pass even if a model says it can.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict
from typing import Any

from openai4s.scientific_reviewer import (
    model_fingerprint,
    review_snapshot,
)
from openai4s.server.evidence_snapshot import (
    collect_turn_evidence,
    freeze_evidence_snapshot,
    resolve_evidence_ref,
    snapshot_digest,
)
from openai4s.server.review_scratch import (
    ReviewScratchError,
    cleanup_scratch,
    prepare_scratch,
    run_scratch_python,
)
from openai4s.storage.auto_mode import AutoModeConflictError

EventSink = Callable[[dict], None]
ChatCall = Callable[..., dict[str, Any]]

_FILENAME = re.compile(
    r"\b([\w.-]+\.(?:csv|tsv|json|pdf|png|jpg|jpeg|mol|sdf|smi|parquet))\b", re.I
)
_N_CLAIM = re.compile(r"\bn\s*=\s*(\d+)\b", re.I)
_MEAN_CLAIM = re.compile(
    r"\bmean(?:\s+of\s+([A-Za-z_][\w]*))?\s*[=:]\s*([-+]?\d+(?:\.\d+)?)\b",
    re.I,
)
_ATOM_CLAIM = re.compile(r"\b(\d+)\s+atoms?\b", re.I)
_MISSING_NONE = re.compile(r"\bno missing values\b", re.I)
_MISSING_COUNT = re.compile(r"\bmissing values(?:\s+in\s+(\w+))?\s*=\s*(\d+)\b", re.I)
_SEVERITY_TO_STORAGE = {
    "high": "high",
    "medium": "major",
    "low": "minor",
}


def _storage_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ScientificReviewService:
    """Build snapshots, run independent V2 review, persist shadow judgments."""

    def __init__(
        self,
        *,
        store: Any,
        config: Any,
        auto_mode: Any | None = None,
        chat_call: ChatCall | None = None,
        owner_instance_id: str = "daemon",
    ) -> None:
        self.store = store
        self.config = config
        self.auto_mode = auto_mode
        self.chat_call = chat_call
        self.owner_instance_id = owner_instance_id

    @property
    def feature_enabled(self) -> bool:
        flags = getattr(self.config, "roadmap_features", None)
        return bool(getattr(flags, "stage3_scientific_review_shadow", False))

    @property
    def storage_enabled(self) -> bool:
        flags = getattr(self.config, "roadmap_features", None)
        return bool(getattr(flags, "stage2_auto_run_storage", False))

    def freeze_reviewer_identity(
        self,
        *,
        agent_cfg: Any,
        reviewer_cfg: Any,
        profile: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Freeze profile + revision + model fingerprint for one review."""

        profile = dict(profile or {})
        profile_id = str(
            profile.get("profile_id")
            or profile.get("id")
            or getattr(reviewer_cfg, "model", None)
            or "scientific-reviewer"
        )
        try:
            revision = int(
                profile.get("revision") or profile.get("profile_revision") or 1
            )
        except (TypeError, ValueError):
            revision = 1
        if revision < 1:
            revision = 1
        fingerprint = model_fingerprint(
            str(getattr(reviewer_cfg, "provider", "") or ""),
            str(getattr(reviewer_cfg, "base_url", "") or ""),
            str(getattr(reviewer_cfg, "model", "") or ""),
        )
        agent_fp = model_fingerprint(
            str(getattr(agent_cfg, "provider", "") or ""),
            str(getattr(agent_cfg, "base_url", "") or ""),
            str(getattr(agent_cfg, "model", "") or ""),
        )
        return {
            "profile_id": profile_id,
            "profile_revision": revision,
            "model_fingerprint": fingerprint,
            "agent_fingerprint": agent_fp,
            "independent": fingerprint != agent_fp,
        }

    def inspect_snapshot(self, snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Deterministic high/medium findings from the frozen snapshot."""

        findings: list[dict[str, Any]] = []
        answer = str(snapshot.get("candidate_answer") or "")
        refs = {
            str(row.get("ref_id"))
            for row in (snapshot.get("evidence_refs") or [])
            if isinstance(row, Mapping)
        }
        artifacts = [
            item
            for item in (snapshot.get("artifacts") or [])
            if isinstance(item, Mapping)
        ]
        names = {
            str(item.get("filename") or "").lower()
            for item in artifacts
            if item.get("filename")
        }
        for match in _FILENAME.findall(answer):
            if match.lower() not in names:
                findings.append(
                    self._finding(
                        severity="high",
                        category="missing_artifact",
                        claim_ref=f"claimed file {match}",
                        evidence_refs=["source:candidate_answer"],
                        reproduction="The named file is not a version in the snapshot.",
                    )
                )
        if (
            int(snapshot.get("omitted_artifact_count") or 0) > 0
            or snapshot.get("complete") is not True
        ):
            reasons = [
                str(item.get("kind") or "omission")
                for item in (snapshot.get("omissions") or [])
                if isinstance(item, Mapping)
            ]
            findings.append(
                self._finding(
                    severity="high",
                    category="evidence_incomplete",
                    claim_ref="snapshot.complete",
                    evidence_refs=["source:candidate_answer"],
                    reproduction="Omissions: " + ", ".join(reasons or ["unspecified"]),
                )
            )
        for adapter in snapshot.get("adapters") or []:
            if not isinstance(adapter, Mapping) or adapter.get("complete") is True:
                continue
            version_id = adapter.get("version_id")
            ref = f"adapter:{version_id}:{adapter.get('adapter')}"
            findings.append(
                self._finding(
                    severity="high",
                    category="evidence_incomplete",
                    claim_ref=f"{adapter.get('adapter')} coverage",
                    evidence_refs=[ref] if ref in refs else ["source:candidate_answer"],
                    reproduction=str(
                        adapter.get("omission_reason") or "adapter incomplete"
                    ),
                )
            )
        for adapter in snapshot.get("adapters") or []:
            if not isinstance(adapter, Mapping) or adapter.get("adapter") != "table":
                continue
            if adapter.get("complete") is not True:
                continue
            summary = adapter.get("summary") or {}
            version_id = adapter.get("version_id")
            ref = f"adapter:{version_id}:table"
            row_count = summary.get("row_count")
            for claimed_n in (int(item) for item in _N_CLAIM.findall(answer)):
                if type(row_count) is int and claimed_n != row_count:
                    findings.append(
                        self._finding(
                            severity="high",
                            category="claim_mismatch",
                            claim_ref=f"n={claimed_n}",
                            evidence_refs=(
                                [ref] if ref in refs else ["source:candidate_answer"]
                            ),
                            reproduction=f"table row_count={row_count}",
                        )
                    )
            columns = (
                summary.get("columns")
                if isinstance(summary.get("columns"), Mapping)
                else {}
            )
            for column, claimed in _MEAN_CLAIM.findall(answer):
                target = None
                if column and column in columns:
                    target = columns[column]
                elif len(columns) == 1:
                    target = next(iter(columns.values()))
                if not isinstance(target, Mapping) or "mean" not in target:
                    continue
                try:
                    actual = float(target["mean"])
                    expected = float(claimed)
                except (TypeError, ValueError):
                    continue
                if abs(actual - expected) > 1e-6:
                    findings.append(
                        self._finding(
                            severity="high",
                            category="claim_mismatch",
                            claim_ref=f"mean={claimed}",
                            evidence_refs=(
                                [ref] if ref in refs else ["source:candidate_answer"]
                            ),
                            reproduction=f"adapter mean={actual}",
                        )
                    )
            for name, stats in columns.items():
                if not isinstance(stats, Mapping):
                    continue
                nulls = stats.get("null_count")
                if type(nulls) is not int:
                    continue
                if _MISSING_NONE.search(answer) and nulls > 0:
                    findings.append(
                        self._finding(
                            severity="high",
                            category="claim_mismatch",
                            claim_ref="no missing values",
                            evidence_refs=(
                                [ref] if ref in refs else ["source:candidate_answer"]
                            ),
                            reproduction=f"{name} null_count={nulls}",
                        )
                    )
            # Resolved the same way as `_MEAN_CLAIM` above: a claim that names
            # its column is checked against THAT column. Checking every claim
            # against every column turned "missing values in age=3" into a high
            # claim_mismatch against `height null_count=0` -- a correct answer
            # marked unverified, and under auto_fix a repair round spent on a
            # defect that does not exist.
            counts = {
                name: stats.get("null_count")
                for name, stats in columns.items()
                if isinstance(stats, Mapping) and type(stats.get("null_count")) is int
            }
            for column, claimed_missing in _MISSING_COUNT.findall(answer):
                try:
                    expected = int(claimed_missing)
                except (TypeError, ValueError):
                    continue
                if column:
                    if column in counts:
                        # Named and present: check THAT column, and only it.
                        if expected != counts[column]:
                            mismatch = f"{column} null_count={counts[column]}"
                        else:
                            continue
                    else:
                        # Named but absent. Silently skipping this was a hole:
                        # a claim about a column the table does not have is a
                        # claim about nothing, and "missing values in weight=0"
                        # sailed through as if verified.
                        mismatch = (
                            f"no column {column!r} in the table "
                            f"(columns: {', '.join(sorted(counts)) or 'none'})"
                        )
                elif not counts:
                    continue
                elif len(counts) == 1:
                    only_name, only_nulls = next(iter(counts.items()))
                    if expected == only_nulls:
                        continue
                    mismatch = f"{only_name} null_count={only_nulls}"
                elif expected == sum(counts.values()):
                    # An unqualified count is a claim about the TABLE, so it is
                    # checked against the table total -- the same reading
                    # `_MISSING_NONE` already gives "no missing values".
                    # Accepting it because SOME column happens to match would
                    # pass "missing values = 0" on a table whose `age` column
                    # has three, which is the claim this check exists to catch.
                    continue
                else:
                    mismatch = "; ".join(
                        f"{name} null_count={value}"
                        for name, value in sorted(counts.items())
                    )
                findings.append(
                    self._finding(
                        severity="high",
                        category="claim_mismatch",
                        claim_ref=f"missing values={expected}",
                        evidence_refs=(
                            [ref] if ref in refs else ["source:candidate_answer"]
                        ),
                        reproduction=mismatch,
                    )
                )
        for adapter in snapshot.get("adapters") or []:
            if (
                not isinstance(adapter, Mapping)
                or adapter.get("adapter") != "structure"
            ):
                continue
            if adapter.get("complete") is not True:
                continue
            atoms = (adapter.get("summary") or {}).get("atom_count")
            version_id = adapter.get("version_id")
            ref = f"adapter:{version_id}:structure"
            for claimed in (int(item) for item in _ATOM_CLAIM.findall(answer)):
                if type(atoms) is int and claimed != atoms:
                    findings.append(
                        self._finding(
                            severity="medium",
                            category="claim_mismatch",
                            claim_ref=f"{claimed} atoms",
                            evidence_refs=(
                                [ref] if ref in refs else ["source:candidate_answer"]
                            ),
                            reproduction=f"structure atom_count={atoms}",
                        )
                    )
        for artifact in artifacts:
            expected = artifact.get("checksum")
            observed = artifact.get("observed_checksum")
            if expected and observed and expected != observed:
                version_id = artifact.get("version_id")
                findings.append(
                    self._finding(
                        severity="high",
                        category="provenance",
                        claim_ref="artifact checksum",
                        evidence_refs=[f"art:{version_id}"],
                        reproduction="recorded checksum does not match observed bytes",
                    )
                )
        # Deduplicate by fingerprint while preserving order.
        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for finding in findings:
            if finding["fingerprint"] in seen:
                continue
            seen.add(finding["fingerprint"])
            unique.append(finding)
        return unique

    def bind_finding_refs(
        self,
        snapshot: Mapping[str, Any],
        findings: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Drop forged refs and emit a high finding for each fabrication."""

        bound: list[dict[str, Any]] = []
        extra: list[dict[str, Any]] = []
        for finding in findings:
            valid = []
            forged = []
            for ref in finding.get("evidence_refs") or []:
                if resolve_evidence_ref(snapshot, str(ref)) is not None:
                    valid.append(str(ref))
                else:
                    forged.append(str(ref))
            cleaned = dict(finding)
            cleaned["evidence_refs"] = valid
            # Reviewer-model findings arrive without an identity: `_clean_finding`
            # emits only the schema fields the model is asked for. Everything
            # downstream (the dedup below, the repeated-finding budget, the
            # durable rows) keys on `fingerprint`, so stamp it here — at the one
            # place every model finding passes through — rather than letting the
            # first consumer KeyError on the reviewer's own output.
            if not cleaned.get("fingerprint") or not cleaned.get("finding_id"):
                identity = self._finding(
                    severity=str(cleaned.get("severity") or "medium"),
                    category=str(cleaned.get("category") or "other"),
                    claim_ref=str(
                        cleaned.get("claim_ref") or cleaned.get("claim") or "finding"
                    ),
                    evidence_refs=valid,
                    reproduction=str(cleaned.get("reproduction") or ""),
                )
                cleaned["finding_id"] = identity["finding_id"]
                cleaned["fingerprint"] = identity["fingerprint"]
            if forged:
                extra.append(
                    self._finding(
                        severity="high",
                        category="provenance",
                        claim_ref=f"forged evidence_refs {forged}",
                        evidence_refs=["source:candidate_answer"],
                        reproduction="Reviewer cited a ref_id that is not in the snapshot.",
                    )
                )
            if valid or cleaned.get("category") == "evidence_incomplete":
                bound.append(cleaned)
            elif not forged:
                bound.append(cleaned)
        return bound, extra

    def evaluate(
        self,
        snapshot: Mapping[str, Any],
        *,
        result_review_mode: str,
        agent_cfg: Any,
        reviewer_cfg: Any,
        reviewer_profile: Mapping[str, Any] | None = None,
        chat_call: ChatCall | None = None,
        allow_same_model: bool = False,
    ) -> dict[str, Any]:
        """Evaluate one frozen snapshot. This is the shipped Stage 3 entry."""

        frozen = dict(snapshot)
        if frozen.get("frozen") is not True or "snapshot_sha256" not in frozen:
            frozen = freeze_evidence_snapshot(frozen)
        identity = self.freeze_reviewer_identity(
            agent_cfg=agent_cfg,
            reviewer_cfg=reviewer_cfg,
            profile=reviewer_profile,
        )
        same_model_ok = result_review_mode == "review_only" or allow_same_model
        if result_review_mode == "auto_fix" and not identity["independent"]:
            return {
                "verdict": "review_unavailable",
                "status": "unavailable",
                "reason": "reviewer_independence_unavailable",
                "summary": "auto_fix requires an independent Reviewer fingerprint",
                "findings": [],
                "snapshot": frozen,
                "reviewer": identity,
                "same_model_independent_session": False,
                "gates_completion": False,
                "usage": {},
            }
        if not identity["independent"] and not same_model_ok:
            return {
                "verdict": "review_unavailable",
                "status": "unavailable",
                "reason": "reviewer_independence_unavailable",
                "summary": "Reviewer fingerprint matches the producing Agent",
                "findings": [],
                "snapshot": frozen,
                "reviewer": identity,
                "same_model_independent_session": False,
                "gates_completion": False,
                "usage": {},
            }

        findings = self.inspect_snapshot(frozen)
        model_result: dict[str, Any] | None = None
        error: str | None = None
        invoke = chat_call or self.chat_call
        attempts = 0
        last_error: Exception | None = None
        while attempts < 2:
            attempts += 1
            try:
                model_result = review_snapshot(
                    dict(frozen), reviewer_cfg, chat_call=invoke
                )
                last_error = None
                break
            except Exception as exc:  # noqa: BLE001 - bounded retry then unavailable
                last_error = exc
                error = str(exc)[:500]
        if last_error is not None:
            return {
                "verdict": "review_unavailable",
                "status": "unavailable",
                "reason": "reviewer_inference_failed",
                "summary": error or "Reviewer inference failed",
                "findings": findings,
                "snapshot": frozen,
                "reviewer": identity,
                "same_model_independent_session": not identity["independent"],
                "gates_completion": False,
                "usage": {},
                "attempts": attempts,
            }
        assert model_result is not None
        model_findings, forged = self.bind_finding_refs(
            frozen, list(model_result.get("findings") or [])
        )
        findings.extend(model_findings)
        findings.extend(forged)
        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for finding in findings:
            if finding["fingerprint"] in seen:
                continue
            seen.add(finding["fingerprint"])
            unique.append(finding)
        material = [item for item in unique if item["severity"] in {"high", "medium"}]
        if frozen.get("complete") is not True:
            verdict = "incomplete"
            status = "completed"
        elif material or forged:
            verdict = "issues"
            status = "completed"
        else:
            verdict = str(model_result.get("verdict") or "pass")
            if verdict == "pass" and unique:
                verdict = "issues"
            status = "completed"
        return {
            "verdict": verdict,
            "status": status,
            "reason": None if status == "completed" else "reviewer_inference_failed",
            "summary": model_result.get("summary") or "",
            "findings": unique,
            "snapshot": frozen,
            "reviewer": identity,
            "same_model_independent_session": not identity["independent"],
            "gates_completion": False,
            "usage": model_result.get("usage") or {},
            "attempts": attempts,
        }

    def shadow_after_turn(
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
        workspace: str | None = None,
        artifact_paths: Mapping[str, str] | None = None,
    ) -> dict[str, Any] | None:
        """Record a shadow review after the existing answer is already delivered."""

        if not self.feature_enabled:
            return None
        selection = {
            "result_review_mode": "off",
            "preset": "off",
            "approvals_reviewer": "user",
        }
        if self.auto_mode is not None:
            try:
                projected = self.auto_mode.get(root_frame_id)
                selection = dict((projected or {}).get("selection") or selection)
            except Exception:  # noqa: BLE001 - fail closed to off
                selection = {
                    "result_review_mode": "off",
                    "preset": "off",
                    "approvals_reviewer": "user",
                }
        mode = str(selection.get("result_review_mode") or "off")
        if mode == "off":
            return None
        snapshot = collect_turn_evidence(
            self.store,
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            turn_id=turn_id,
            execution_id=execution_id,
            user_request=user_request,
            candidate_answer=candidate_answer,
            structured_completion=structured_completion,
            artifact_versions_before=artifact_versions_before,
            cell_count_before=cell_count_before,
            step_count_before=step_count_before,
        )
        profile = None
        try:
            profiles = self.store.list_model_profiles()
            wanted = str(getattr(reviewer_cfg, "model", "") or "")
            profile = next(
                (
                    item
                    for item in profiles
                    if str(item.get("model") or "") == wanted
                    or str(item.get("profile_id") or item.get("id") or "") == wanted
                ),
                None,
            )
        except Exception:  # noqa: BLE001
            profile = None
        result = self.evaluate(
            snapshot,
            result_review_mode=mode,
            agent_cfg=agent_cfg,
            reviewer_cfg=reviewer_cfg,
            reviewer_profile=profile,
        )
        if workspace or artifact_paths:
            self._optional_scratch_recheck(
                result, workspace=workspace, artifact_paths=artifact_paths
            )
        self._persist_shadow_step(root_frame_id, result, emit=emit)
        if self.storage_enabled:
            try:
                self._persist_auto_mode(
                    root_frame_id=root_frame_id,
                    project_id=project_id,
                    branch_id=branch_id,
                    turn_id=turn_id,
                    execution_id=execution_id,
                    mode=mode,
                    selection=selection,
                    result=result,
                )
            except (AutoModeConflictError, ValueError, PermissionError, KeyError):
                # Shadow must not fail the already-delivered turn.
                pass
        return result

    def _optional_scratch_recheck(
        self,
        result: dict[str, Any],
        *,
        workspace: str | None,
        artifact_paths: Mapping[str, str] | None,
    ) -> None:
        scratch = None
        try:
            scratch = prepare_scratch(
                result["snapshot"],
                artifact_paths=artifact_paths,
                workspace=workspace,
            )
            probe = run_scratch_python(
                "print('scratch-ok')\n",
                scratch=scratch,
                workspace=workspace,
            )
            result["scratch"] = {
                "ok": probe.get("returncode") == 0,
                "stdout": probe.get("stdout"),
            }
        except ReviewScratchError as exc:
            result["scratch"] = {"ok": False, "error": str(exc)[:300]}
        finally:
            if scratch is not None:
                cleanup_scratch(scratch)

    def _persist_shadow_step(
        self,
        root_frame_id: str,
        result: Mapping[str, Any],
        *,
        emit: EventSink | None,
    ) -> None:
        step_id = f"review-shadow-{uuid.uuid4().hex[:12]}"
        snapshot = result.get("snapshot") or {}
        output = {
            "mode": "shadow",
            "stage": 3,
            "verdict": result.get("verdict"),
            "summary": result.get("summary"),
            "findings": result.get("findings") or [],
            "evidence_snapshot_sha256": snapshot.get("snapshot_sha256"),
            "reviewer": result.get("reviewer"),
            "same_model_independent_session": result.get(
                "same_model_independent_session"
            ),
            "gates_completion": False,
            "reason": result.get("reason"),
        }
        self.store.add_step(
            step_id=step_id,
            frame_id=root_frame_id,
            kind="review",
            title="Scientific Reviewer (shadow)",
            input={"mode": "shadow", "stage": 3},
            status="running",
        )
        self.store.update_step(
            step_id,
            status="done",
            output=output,
            summary=str(result.get("summary") or result.get("verdict") or "shadow"),
        )
        if emit is None:
            return
        emit(
            {
                "type": "step",
                "frame_id": root_frame_id,
                "step_id": step_id,
                "kind": "review",
                "title": "Scientific Reviewer (shadow)",
                "status": "done",
                "output": output,
                "summary": output["summary"],
            }
        )

    def _persist_auto_mode(
        self,
        *,
        root_frame_id: str,
        project_id: str,
        branch_id: str,
        turn_id: str,
        execution_id: str,
        mode: str,
        selection: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> None:
        snapshot = dict(result.get("snapshot") or {})
        payload = {
            key: value for key, value in snapshot.items() if key != "snapshot_sha256"
        }
        # Storage hashes the exact JSON object it persists.
        payload = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
        candidate_id = f"cand-{turn_id}"
        candidate_sha = _storage_digest(
            {
                "candidate_answer": snapshot.get("candidate_answer"),
                "structured_completion": snapshot.get("structured_completion"),
            }
        )
        evidence_sha = _storage_digest(payload)
        versions = [
            str(item.get("version_id"))
            for item in (snapshot.get("artifacts") or [])
            if isinstance(item, Mapping) and item.get("version_id")
        ]
        run_id = f"auto-{root_frame_id}-{turn_id}"
        budgets = {}
        auto_cfg = getattr(self.config, "auto_mode", None)
        if auto_cfg is not None and getattr(auto_cfg, "budgets", None) is not None:
            budgets = asdict(auto_cfg.budgets)
        self.store.start_auto_mode_run(
            run_id=run_id,
            idempotency_key=f"{turn_id}:auto-run",
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            turn_id=turn_id,
            execution_id=execution_id,
            mode=mode if mode in {"review_only", "auto_fix"} else "review_only",
            selection=dict(selection),
            budgets=budgets,
            owner_instance_id=self.owner_instance_id,
        )
        self.store.record_auto_mode_candidate(
            run_id,
            idempotency_key=f"{turn_id}:candidate",
            candidate_id=candidate_id,
            candidate_snapshot_sha256=candidate_sha,
            evidence_snapshot_sha256=evidence_sha,
            candidate_version_ids=versions,
        )
        reviewer = dict(result.get("reviewer") or {})
        review_run_id = f"review-{turn_id}"
        self.store.start_auto_mode_review(
            run_id,
            review_run_id=review_run_id,
            audit_id=f"audit-{turn_id}",
            idempotency_key=f"{turn_id}:review-start",
            candidate_id=candidate_id,
            candidate_snapshot_sha256=candidate_sha,
            evidence_snapshot=payload,
            evidence_snapshot_sha256=evidence_sha,
            round_index=0,
            attempt=int(result.get("attempts") or 1),
            reviewer={
                "profile_id": reviewer.get("profile_id") or "scientific-reviewer",
                "profile_revision": int(reviewer.get("profile_revision") or 1),
                "model_fingerprint": reviewer.get("model_fingerprint") or "unknown",
            },
        )
        findings = []
        for item in result.get("findings") or []:
            finding_id = item.get("finding_id")
            fingerprint = item.get("fingerprint")
            if not finding_id or not fingerprint:
                rebuilt = self._finding(
                    severity=str(item.get("severity") or "medium"),
                    category=str(item.get("category") or "other"),
                    claim_ref=str(
                        item.get("claim_ref") or item.get("claim") or "finding"
                    ),
                    evidence_refs=list(item.get("evidence_refs") or []),
                    reproduction=str(item.get("reproduction") or ""),
                )
                finding_id = rebuilt["finding_id"]
                fingerprint = rebuilt["fingerprint"]
            findings.append(
                {
                    "finding_id": finding_id,
                    "fingerprint": fingerprint,
                    "severity": _SEVERITY_TO_STORAGE.get(item.get("severity"), "major"),
                    "category": item.get("category") or "other",
                    "claim": item.get("claim_ref") or item.get("claim") or "finding",
                    "evidence_refs": item.get("evidence_refs") or [],
                    "status": "open",
                }
            )
        verdict = str(result.get("verdict") or "issues")
        status = "unavailable" if verdict == "review_unavailable" else "completed"
        self.store.complete_auto_mode_review(
            review_run_id,
            idempotency_key=f"{turn_id}:review-complete",
            status=status,
            verdict=verdict,
            assessment={"public_summary": result.get("summary"), "shadow": True},
            findings=findings,
            usage=result.get("usage") or {},
        )

    @staticmethod
    def _finding(
        *,
        severity: str,
        category: str,
        claim_ref: str,
        evidence_refs: list[str],
        reproduction: str,
        suggested_fix: str = "",
        confidence: float = 1.0,
    ) -> dict[str, Any]:
        # Severity is part of the identity. Without it two findings that share
        # a category and claim collapse to one, and the first-wins dedup then
        # keeps whichever arrived first -- so a `low` nit could evict the `high`
        # finding about the same claim, taking it out of `material` and out of
        # repair entirely.
        fingerprint = hashlib.sha256(
            f"{severity}|{category}|{claim_ref}|{','.join(evidence_refs)}".encode(
                "utf-8"
            )
        ).hexdigest()
        return {
            "finding_id": f"fnd-{fingerprint[:16]}",
            "fingerprint": fingerprint,
            "severity": severity,
            "category": category,
            "claim_ref": claim_ref,
            "evidence_refs": list(evidence_refs),
            "reproduction": reproduction,
            "suggested_fix": suggested_fix,
            "confidence": confidence,
        }
