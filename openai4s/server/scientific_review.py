"""Scientific Reviewer V2 orchestration for Stage 3 shadow and Stage 4 gate.

Stage 3 records a non-gating shadow judgment. Stage 4 freezes the same evidence
but opens and closes a completion-gating durable review that can later support
an exact post-delivery terminal. Neither path promotes a message itself.
Deterministic snapshot and adapter checks run before any model call so an
omitted artifact cannot pass even if a model says it can.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import asdict
from typing import Any

from openai4s.scientific_reviewer import (
    model_fingerprint,
    review_request_messages,
    review_snapshot,
)
from openai4s.server.auto_budget import (
    TERMINAL_USER_TRUTH,
    AutoBudgetAdmission,
    AutoBudgetDenied,
    execution_action_group,
    token_upper_bound,
    verifiable_token_usage,
)
from openai4s.server.evidence_snapshot import (
    collect_turn_evidence,
    freeze_evidence_snapshot,
    resolve_evidence_ref,
)
from openai4s.server.review_scratch import (
    ReviewScratchError,
    cleanup_scratch,
    prepare_scratch,
    run_scratch_python,
)
from openai4s.storage.auto_mode import AutoModeConflictError


def scoped_finding_id(review_run_id: str, fingerprint: str) -> str:
    """The durable identity of one finding: its content *within one review*.

    `fingerprint` is content-only on purpose -- Stage 5 compares fingerprints
    across repair rounds to notice that a finding did not go away -- so it
    cannot also be the identity. `review_findings.finding_id` is a global
    PRIMARY KEY, so deriving the id from content alone meant two sessions that
    reached the same conclusion collided: the second one's insert raised
    `UNIQUE constraint failed: review_findings.finding_id`, its review died,
    and the turn was reported `review_unavailable` instead of its real verdict.
    A recurring wrong claim is exactly the finding most likely to recur, so the
    collision landed on the case that mattered most.

    Global uniqueness still has to hold, because the session-import owner check
    looks a finding up by id alone (`SELECT run_id FROM review_findings WHERE
    finding_id=?`). Hashing the review scope together with the fingerprint gives
    each allowed pair a stable, collision-resistant identity; the table also
    declares `UNIQUE(review_run_id, fingerprint)`.
    """

    digest = hashlib.sha256(
        f"{review_run_id}|{fingerprint}".encode("utf-8")
    ).hexdigest()
    return f"fnd-{digest[:16]}"


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


def _durable_snapshot_payload(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Return the exact JSON object committed as durable review evidence."""

    payload = {
        key: value for key, value in dict(snapshot).items() if key != "snapshot_sha256"
    }
    # Match AutoModeRepository's JSON boundary.  In particular, a value with a
    # useful ``__str__`` must be frozen before either digest is computed rather
    # than being stringified independently by two callers.
    return json.loads(json.dumps(payload, ensure_ascii=False, default=str))


def candidate_snapshot_digest(snapshot: Mapping[str, Any]) -> str:
    """Digest the exact candidate bytes and structured completion reviewed."""

    durable = _durable_snapshot_payload(snapshot)
    return _storage_digest(
        {
            "candidate_answer": durable.get("candidate_answer"),
            "structured_completion": durable.get("structured_completion"),
        }
    )


def durable_review_matches(
    result: Mapping[str, Any],
    *,
    candidate_answer: str,
    root_frame_id: str,
    branch_id: str,
    turn_id: str,
    execution_id: str,
    gates_completion: bool = True,
) -> bool:
    """Validate a returned proof against the exact candidate it may certify.

    A truthy ``storage_enabled`` flag is not proof.  This deliberately
    recomputes both storage digests and checks the durable open/close event
    identities so a proof from another candidate (including another candidate
    in the same turn) cannot be replayed into a green badge.
    """

    proof = result.get("durable_review")
    snapshot = result.get("snapshot")
    if not isinstance(proof, Mapping) or not isinstance(snapshot, Mapping):
        return False
    identity = snapshot.get("identity")
    if not isinstance(identity, Mapping):
        return False
    expected_identity = {
        "root_frame_id": str(root_frame_id),
        "branch_id": str(branch_id),
        "turn_id": str(turn_id),
        "execution_id": str(execution_id),
    }
    if any(
        str(identity.get(key) or "") != value
        for key, value in expected_identity.items()
    ):
        return False
    if any(
        str(proof.get(key) or "") != value for key, value in expected_identity.items()
    ):
        return False
    if snapshot.get("candidate_answer") != candidate_answer:
        return False
    payload = _durable_snapshot_payload(snapshot)
    candidate_sha = candidate_snapshot_digest(snapshot)
    evidence_sha = _storage_digest(payload)
    if str(snapshot.get("snapshot_sha256") or "") != evidence_sha:
        return False
    if (
        proof.get("opened") is not True
        or proof.get("closed") is not True
        or proof.get("gates_completion") is not gates_completion
        or str(proof.get("candidate_snapshot_sha256") or "") != candidate_sha
        or str(proof.get("evidence_snapshot_sha256") or "") != evidence_sha
        or str(proof.get("snapshot_sha256") or "") != evidence_sha
        or str(proof.get("verdict") or "") != "pass"
        or str(proof.get("status") or "") != "completed"
        or str(result.get("verdict") or "") != "pass"
    ):
        return False
    for key in (
        "run_id",
        "candidate_id",
        "review_run_id",
        "open_event_id",
        "close_event_id",
    ):
        if not isinstance(proof.get(key), str) or not proof.get(key):
            return False
    return proof.get("open_event_id") != proof.get("close_event_id")


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

    def begin_turn_run(
        self,
        *,
        root_frame_id: str,
        branch_id: str,
        turn_id: str,
        execution_id: str,
        mode_override: str | None = None,
    ) -> dict[str, Any] | None:
        """Durably bind an Auto Run before the first model action executes.

        Permission Guardian decisions happen during the turn, while result
        review happens after its Candidate exists.  Starting only in
        ``open_review_run`` left Guardian with no owning run and left a crash
        window where a Candidate message existed without any recoverable run.
        This start is deterministic and idempotent; the later review open
        replays the same owner rather than creating a second run.
        """

        if not self.storage_enabled:
            return None
        selection: dict[str, Any] = {
            "result_review_mode": "off",
            "preset": "off",
            "approvals_reviewer": "user",
        }
        if self.auto_mode is not None:
            projected = self.auto_mode.get(root_frame_id)
            selected = (projected or {}).get("selection")
            if isinstance(selected, Mapping):
                selection = dict(selected)
        if mode_override is not None:
            mode = str(mode_override)
            if mode not in {"off", "review_only", "auto_fix"}:
                mode = "review_only"
            selection["result_review_mode"] = mode
        else:
            mode = str(selection.get("result_review_mode") or "off")
            if mode not in {"off", "review_only", "auto_fix"}:
                mode = "review_only"
                selection["result_review_mode"] = mode
        if mode == "off" and selection.get("approvals_reviewer") != "auto_review":
            return None
        turn_token = hashlib.sha256(str(turn_id).encode("utf-8")).hexdigest()[:12]
        return self._start_auto_mode_run(
            run_id=f"auto-{root_frame_id}-{turn_id}",
            idempotency_key=f"{turn_token}:auto-run",
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            turn_id=turn_id,
            execution_id=execution_id,
            mode=mode,
            selection=selection,
        )

    def _start_auto_mode_run(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        root_frame_id: str,
        branch_id: str,
        turn_id: str,
        execution_id: str,
        mode: str,
        selection: Mapping[str, Any],
    ) -> dict[str, Any]:
        budgets = {}
        auto_cfg = getattr(self.config, "auto_mode", None)
        if auto_cfg is not None and getattr(auto_cfg, "budgets", None) is not None:
            budgets = asdict(auto_cfg.budgets)
        return self.store.start_auto_mode_run(
            run_id=run_id,
            idempotency_key=idempotency_key,
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            turn_id=turn_id,
            execution_id=execution_id,
            mode=mode,
            selection=dict(selection),
            budgets=budgets,
            owner_instance_id=self.owner_instance_id,
        )

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
                        reproduction=(
                            "Reviewer cited a ref_id that is not in the snapshot."
                        ),
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
        cancel: Callable[[], bool] | None = None,
        run_id: str | None = None,
        action_group_id: str | None = None,
    ) -> dict[str, Any]:
        """Evaluate one frozen snapshot. This is the shipped Stage 3 entry."""

        frozen = dict(snapshot)
        if frozen.get("frozen") is not True or str(
            frozen.get("snapshot_sha256") or ""
        ) != _storage_digest(_durable_snapshot_payload(frozen)):
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
        budget = self._auto_budget()
        while attempts < 2:
            if self._cancel_requested(cancel):
                return {
                    "verdict": "review_unavailable",
                    "status": "unavailable",
                    "reason": "review_cancelled",
                    "summary": "Reviewer inference was cancelled",
                    "findings": findings,
                    "snapshot": frozen,
                    "reviewer": identity,
                    "same_model_independent_session": not identity["independent"],
                    "gates_completion": False,
                    "usage": {},
                    "attempts": attempts,
                    "cancelled": True,
                }
            attempts += 1
            admission_id = None
            token_admission_id = None
            try:
                if budget is not None and run_id:
                    group_id = execution_action_group(
                        str(action_group_id or f"{run_id}:review"),
                        f"attempt-{attempts}-{uuid.uuid4().hex[:16]}",
                    )
                    admission_id = f"{run_id}:review:{group_id}"
                    budget.reserve(
                        run_id=str(run_id),
                        admission_id=admission_id,
                        consumer="review",
                        action_group_id=group_id,
                        amount=1,
                    )
                    review_messages, _packet_complete = review_request_messages(
                        dict(frozen)
                    )
                    review_max_tokens = min(
                        int(getattr(reviewer_cfg, "max_tokens", 1800) or 1800),
                        1800,
                    )
                    bound = token_upper_bound(
                        reviewer_cfg,
                        messages=review_messages,
                        max_tokens=review_max_tokens,
                    )
                    if bound is None:
                        budget.release(admission_id, started=False)
                        budget.fail_measurement(str(run_id))
                        return self._budget_terminal(
                            frozen,
                            identity,
                            findings,
                            "budget_measurement_unavailable",
                            attempts=attempts,
                        )
                    token_admission_id = f"{admission_id}:token"
                    budget.reserve(
                        run_id=str(run_id),
                        admission_id=token_admission_id,
                        consumer="token",
                        action_group_id=f"{group_id}:token",
                        amount=bound,
                        token_upper_bound=bound,
                    )
                model_result = review_snapshot(
                    dict(frozen), reviewer_cfg, chat_call=invoke
                )
                last_error = None
                if (
                    budget is not None
                    and run_id
                    and admission_id
                    and token_admission_id
                ):
                    usage_total = verifiable_token_usage(
                        (model_result or {}).get("usage")
                    )
                    if usage_total is None:
                        budget.fail_measurement(str(run_id), token_admission_id)
                        budget.mark_unknown(admission_id)
                        return self._budget_terminal(
                            frozen,
                            identity,
                            findings,
                            "budget_measurement_unavailable",
                            attempts=attempts,
                        )
                    budget.commit(admission_id, committed_amount=1)
                    budget.commit(token_admission_id, committed_amount=usage_total)
                break
            except AutoBudgetDenied as denied:
                if budget is not None and admission_id:
                    try:
                        budget.release(admission_id, started=False)
                    except Exception:  # noqa: BLE001 - denial already fail-closed
                        pass
                return self._budget_terminal(
                    frozen,
                    identity,
                    findings,
                    denied.reason,
                    attempts=attempts,
                )
            except Exception as exc:  # noqa: BLE001 - bounded retry then unavailable
                last_error = exc
                error = str(exc)[:500]
                if budget is not None and admission_id:
                    try:
                        budget.mark_unknown(admission_id)
                    except Exception:  # noqa: BLE001 - unknown is fail-closed
                        pass
                    if token_admission_id:
                        try:
                            budget.mark_unknown(token_admission_id)
                        except Exception:  # noqa: BLE001 - unknown is fail-closed
                            pass
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

    def _auto_budget(self) -> AutoBudgetAdmission | None:
        store = self.store
        if store is None or not callable(
            getattr(store, "reserve_auto_mode_budget", None)
        ):
            return None
        budgets = getattr(getattr(self.config, "auto_mode", None), "budgets", None)
        return AutoBudgetAdmission(store, budgets)

    def _budget_terminal(
        self,
        frozen: Mapping[str, Any],
        identity: Mapping[str, Any],
        findings: list[dict[str, Any]],
        reason: str,
        *,
        attempts: int,
    ) -> dict[str, Any]:
        summary = TERMINAL_USER_TRUTH.get(reason, reason)
        return {
            "verdict": "review_unavailable",
            "status": "unavailable",
            "reason": reason,
            "stop_reason": reason,
            "summary": summary,
            "findings": findings,
            "snapshot": frozen,
            "reviewer": identity,
            "same_model_independent_session": not identity["independent"],
            "gates_completion": False,
            "usage": {},
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
        produced_artifacts: list[Mapping[str, Any]] | None = None,
        cell_count_before: int = 0,
        step_count_before: int = 0,
        agent_cfg: Any,
        reviewer_cfg: Any,
        emit: EventSink | None = None,
        workspace: str | None = None,
        artifact_paths: Mapping[str, str] | None = None,
        mode_override: str | None = None,
        gates_completion: bool = False,
        round_index: int = 0,
        cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any] | None:
        """Review one turn and return the exact durable proof, when available.

        Stage 3 uses the defaults and remains a non-gating shadow.  Stage 4
        passes a mode frozen by the gateway plus ``gates_completion=True``;
        that path must never re-read a newly changed session selection.
        """

        if not self.feature_enabled and not gates_completion:
            return None
        mode, selection = self._selection_for_review(
            root_frame_id,
            mode_override=mode_override,
            fail_closed=gates_completion,
        )
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
            produced_artifacts=produced_artifacts,
            cell_count_before=cell_count_before,
            step_count_before=step_count_before,
        )
        profile = self._reviewer_profile(reviewer_cfg)
        # Freeze here, not inside `evaluate`, so the bytes the reviewer is
        # asked about are the same bytes the candidate row is hashed from. A
        # snapshot frozen twice is frozen once: `evaluate` leaves an already
        # frozen snapshot alone.
        frozen = freeze_evidence_snapshot(snapshot)
        identity = self.freeze_reviewer_identity(
            agent_cfg=agent_cfg,
            reviewer_cfg=reviewer_cfg,
            profile=profile,
        )
        handle: dict[str, Any] | None = None
        proof = self._empty_durable_proof(
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            turn_id=turn_id,
            execution_id=execution_id,
            mode=mode,
            snapshot=frozen,
            gates_completion=gates_completion,
            round_index=round_index,
        )
        if self.storage_enabled:
            try:
                handle = self.open_review_run(
                    root_frame_id=root_frame_id,
                    project_id=project_id,
                    branch_id=branch_id,
                    turn_id=turn_id,
                    execution_id=execution_id,
                    mode=mode,
                    selection=selection,
                    snapshot=frozen,
                    reviewer=identity,
                    gates_completion=gates_completion,
                    round_index=round_index,
                )
                proof = self._proof_from_handle(handle, opened=True, closed=False)
            except Exception as error:  # noqa: BLE001 - close a lost-response open
                # ``start_review`` is durable before the model call.  A wrapper
                # can therefore raise after SQLite committed.  The deterministic
                # handle lets us close that possible owner as unavailable instead
                # of leaving the run forever in ``reviewing``.
                handle = self._review_binding(
                    root_frame_id=root_frame_id,
                    branch_id=branch_id,
                    turn_id=turn_id,
                    execution_id=execution_id,
                    mode=mode,
                    snapshot=frozen,
                    gates_completion=gates_completion,
                    round_index=round_index,
                )
                result = self._unavailable_result(
                    frozen,
                    identity,
                    reason="durable_review_open_failed",
                    summary=str(error)[:300] or "Durable review could not be opened",
                    gates_completion=gates_completion,
                )
                result, proof = self._close_review_safely(
                    handle, result, gates_completion=gates_completion
                )
                proof["open_error"] = type(error).__name__
                result["durable_review"] = proof
                self._persist_review_step_safely(
                    root_frame_id,
                    result,
                    emit=emit,
                    gates_completion=gates_completion,
                )
                return result
        try:
            result = self.evaluate(
                frozen,
                result_review_mode=mode,
                agent_cfg=agent_cfg,
                reviewer_cfg=reviewer_cfg,
                reviewer_profile=profile,
                cancel=cancel,
                run_id=(
                    str(handle.get("run_id") or "") or None
                    if isinstance(handle, Mapping)
                    else None
                ),
            )
        except Exception as error:  # noqa: BLE001 - durable owner must be closed
            result = self._unavailable_result(
                frozen,
                identity,
                reason="reviewer_inference_failed",
                summary=str(error)[:300] or "Reviewer inference failed",
                gates_completion=gates_completion,
            )
        result = dict(result)
        result["gates_completion"] = bool(gates_completion)
        if workspace or artifact_paths:
            self._optional_scratch_recheck(
                result, workspace=workspace, artifact_paths=artifact_paths
            )
        if handle is not None:
            result, proof = self._close_review_safely(
                handle, result, gates_completion=gates_completion
            )
        result["durable_review"] = proof
        # Persist the visible step only after the durable review owner is
        # closed.  A step-store exception must never strand the review phase.
        self._persist_review_step_safely(
            root_frame_id,
            result,
            emit=emit,
            gates_completion=gates_completion,
        )
        return result

    @staticmethod
    def _cancel_requested(cancel: Callable[[], bool] | None) -> bool:
        if cancel is None:
            return False
        try:
            return bool(cancel())
        except Exception:  # noqa: BLE001 - cancellation checks fail closed
            return True

    def persist_review_result(
        self,
        *,
        root_frame_id: str,
        project_id: str,
        branch_id: str,
        turn_id: str,
        execution_id: str,
        mode_override: str,
        result: Mapping[str, Any],
        round_index: int,
        gates_completion: bool = True,
        emit: EventSink | None = None,
    ) -> dict[str, Any]:
        """Durably bind an already-computed fresh re-review to its candidate.

        ``AutoRepairService`` performs the re-review in memory.  Stage 4 calls
        this for the final repaired snapshot; without the returned open+close
        proof the repaired text remains deliverable but cannot become Verified.
        No model call is made here.
        """

        del project_id  # retained for a stable orchestration signature
        mode, selection = self._selection_for_review(
            root_frame_id,
            mode_override=mode_override,
            fail_closed=gates_completion,
        )
        current = dict(result)
        snapshot = current.get("snapshot")
        if not isinstance(snapshot, Mapping):
            snapshot = {}
        frozen = dict(snapshot)
        if frozen.get("frozen") is not True or str(
            frozen.get("snapshot_sha256") or ""
        ) != _storage_digest(_durable_snapshot_payload(frozen)):
            frozen = freeze_evidence_snapshot(frozen)
        current["snapshot"] = frozen
        current["gates_completion"] = bool(gates_completion)
        reviewer = current.get("reviewer")
        reviewer = (
            dict(reviewer)
            if isinstance(reviewer, Mapping)
            else {
                "profile_id": "scientific-reviewer",
                "profile_revision": 1,
                "model_fingerprint": "unknown",
            }
        )
        proof = self._empty_durable_proof(
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            turn_id=turn_id,
            execution_id=execution_id,
            mode=mode,
            snapshot=frozen,
            gates_completion=gates_completion,
            round_index=round_index,
        )
        if not self.storage_enabled or mode == "off":
            current["durable_review"] = proof
            return current
        try:
            handle = self.open_review_run(
                root_frame_id=root_frame_id,
                project_id="",
                branch_id=branch_id,
                turn_id=turn_id,
                execution_id=execution_id,
                mode=mode,
                selection=selection,
                snapshot=frozen,
                reviewer=reviewer,
                gates_completion=gates_completion,
                round_index=round_index,
            )
        except Exception as error:  # noqa: BLE001 - fail closed, try lost open
            handle = self._review_binding(
                root_frame_id=root_frame_id,
                branch_id=branch_id,
                turn_id=turn_id,
                execution_id=execution_id,
                mode=mode,
                snapshot=frozen,
                gates_completion=gates_completion,
                round_index=round_index,
            )
            unavailable = self._unavailable_result(
                frozen,
                reviewer,
                reason="durable_review_open_failed",
                summary=str(error)[:300] or "Durable re-review could not be opened",
                gates_completion=gates_completion,
            )
            closed_result, proof = self._close_review_safely(
                handle, unavailable, gates_completion=gates_completion
            )
            proof["open_error"] = type(error).__name__
            closed_result["durable_review"] = proof
            self._persist_review_step_safely(
                root_frame_id,
                closed_result,
                emit=emit,
                gates_completion=gates_completion,
            )
            return closed_result
        current, proof = self._close_review_safely(
            handle, current, gates_completion=gates_completion
        )
        current["durable_review"] = proof
        self._persist_review_step_safely(
            root_frame_id,
            current,
            emit=emit,
            gates_completion=gates_completion,
        )
        return current

    def _selection_for_review(
        self,
        root_frame_id: str,
        *,
        mode_override: str | None,
        fail_closed: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        if mode_override is not None:
            mode = str(mode_override)
            if mode not in {"off", "review_only", "auto_fix"}:
                return "review_only", {
                    "result_review_mode": "review_only",
                    "preset": "off",
                    "approvals_reviewer": "user",
                }
            return mode, {
                "result_review_mode": mode,
                "preset": "autonomous" if mode == "auto_fix" else "off",
                "approvals_reviewer": "auto_review" if mode == "auto_fix" else "user",
            }
        selection: dict[str, Any] = {
            "result_review_mode": "off",
            "preset": "off",
            "approvals_reviewer": "user",
        }
        if self.auto_mode is not None:
            try:
                projected = self.auto_mode.get(root_frame_id)
                selected = (projected or {}).get("selection")
                if isinstance(selected, Mapping) and "result_review_mode" in selected:
                    selection = dict(selected)
                elif fail_closed:
                    selection["result_review_mode"] = "review_only"
            except Exception:  # noqa: BLE001 - Stage 4 must stay armed
                if fail_closed:
                    selection["result_review_mode"] = "review_only"
        elif fail_closed:
            selection["result_review_mode"] = "review_only"
        mode = str(selection.get("result_review_mode") or "off")
        if mode not in {"off", "review_only", "auto_fix"}:
            mode = "review_only" if fail_closed else "off"
        selection["result_review_mode"] = mode
        return mode, selection

    def _reviewer_profile(self, reviewer_cfg: Any) -> Mapping[str, Any] | None:
        try:
            profiles = self.store.list_model_profiles()
            wanted = str(getattr(reviewer_cfg, "model", "") or "")
            return next(
                (
                    item
                    for item in profiles
                    if str(item.get("model") or "") == wanted
                    or str(item.get("profile_id") or item.get("id") or "") == wanted
                ),
                None,
            )
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _unavailable_result(
        snapshot: Mapping[str, Any],
        reviewer: Mapping[str, Any],
        *,
        reason: str,
        summary: str,
        gates_completion: bool,
    ) -> dict[str, Any]:
        return {
            "verdict": "review_unavailable",
            "status": "unavailable",
            "reason": reason,
            "summary": summary,
            "findings": [],
            "snapshot": dict(snapshot),
            "reviewer": dict(reviewer),
            "same_model_independent_session": False,
            "gates_completion": bool(gates_completion),
            "usage": {},
            "attempts": 1,
        }

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

    def _persist_review_step_safely(
        self,
        root_frame_id: str,
        result: Mapping[str, Any],
        *,
        emit: EventSink | None,
        gates_completion: bool,
    ) -> None:
        try:
            self._persist_review_step(
                root_frame_id,
                result,
                emit=emit,
                gates_completion=gates_completion,
            )
        except Exception:  # noqa: BLE001 - the durable review is already closed
            return

    def _persist_review_step(
        self,
        root_frame_id: str,
        result: Mapping[str, Any],
        *,
        emit: EventSink | None,
        gates_completion: bool,
    ) -> None:
        mode = "completion_gate" if gates_completion else "shadow"
        stage = 4 if gates_completion else 3
        title = (
            "Scientific Reviewer (completion gate)"
            if gates_completion
            else "Scientific Reviewer (shadow)"
        )
        step_id = f"review-{mode}-{uuid.uuid4().hex[:12]}"
        snapshot = result.get("snapshot") or {}
        output = {
            "mode": mode,
            "stage": stage,
            "verdict": result.get("verdict"),
            "summary": result.get("summary"),
            "findings": result.get("findings") or [],
            "evidence_snapshot_sha256": snapshot.get("snapshot_sha256"),
            "reviewer": result.get("reviewer"),
            "same_model_independent_session": result.get(
                "same_model_independent_session"
            ),
            "gates_completion": bool(gates_completion),
            "reason": result.get("reason"),
            "durable_review": result.get("durable_review"),
        }
        self.store.add_step(
            step_id=step_id,
            frame_id=root_frame_id,
            kind="review",
            title=title,
            input={"mode": mode, "stage": stage},
            status="running",
        )
        self.store.update_step(
            step_id,
            status="done",
            output=output,
            summary=str(result.get("summary") or result.get("verdict") or mode),
        )
        if emit is None:
            return
        emit(
            {
                "type": "step",
                "frame_id": root_frame_id,
                "step_id": step_id,
                "kind": "review",
                "title": title,
                "status": "done",
                "output": output,
                "summary": output["summary"],
            }
        )

    def open_review_run(
        self,
        *,
        root_frame_id: str,
        project_id: str,
        branch_id: str,
        turn_id: str,
        execution_id: str,
        mode: str,
        selection: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        reviewer: Mapping[str, Any],
        gates_completion: bool = False,
        round_index: int = 0,
    ) -> dict[str, Any]:
        """Commit the candidate and open the review BEFORE the reviewer runs.

        Stage 4 orders the turn as candidate -> frozen evidence -> review ->
        promotion, and the durable record has to follow the same order or it
        proves nothing.  Writing all four rows after ``evaluate`` returned made
        the whole sequence conditional on the reviewer answering: a crash
        during the round-trip left no candidate row, no evidence and no open
        review -- exactly the state that cannot be told apart from a turn that
        never produced an answer.  Opening the run first means a lost daemon
        leaves a run in ``reviewing`` with the frozen evidence attached, which
        recovery can abandon and an operator can read.

        Returns the handle :meth:`close_review_run` needs.
        """

        del project_id  # kept in the public orchestration signature
        handle = self._review_binding(
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            turn_id=turn_id,
            execution_id=execution_id,
            mode=mode,
            snapshot=snapshot,
            gates_completion=gates_completion,
            round_index=round_index,
        )
        payload = handle["evidence_snapshot"]
        candidate_id = str(handle["candidate_id"])
        candidate_sha = str(handle["candidate_snapshot_sha256"])
        evidence_sha = str(handle["evidence_snapshot_sha256"])
        versions = [
            str(item.get("version_id"))
            for item in (snapshot.get("artifacts") or [])
            if isinstance(item, Mapping) and item.get("version_id")
        ]
        run_id = str(handle["run_id"])
        # ``begin_turn_run`` may already own this turn so Guardian can audit
        # actions before a Candidate exists.  Reuse its exact frozen selection;
        # re-reading a concurrently changed setting here would turn an
        # idempotent replay into a digest mismatch.
        projected = self.store.project_auto_mode_run(root_frame_id, branch_id)
        projected_run = projected.get("run") if isinstance(projected, Mapping) else None
        run_selection = dict(selection)
        if (
            isinstance(projected_run, Mapping)
            and projected_run.get("run_id") == run_id
            and isinstance(projected_run.get("selection"), Mapping)
        ):
            run_selection = dict(projected_run["selection"])
        self._start_auto_mode_run(
            run_id=run_id,
            idempotency_key=str(handle["run_idempotency_key"]),
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            turn_id=turn_id,
            execution_id=execution_id,
            mode=mode if mode in {"review_only", "auto_fix"} else "review_only",
            selection=run_selection,
        )
        self.store.record_auto_mode_candidate(
            run_id,
            idempotency_key=str(handle["candidate_idempotency_key"]),
            candidate_id=candidate_id,
            candidate_snapshot_sha256=candidate_sha,
            evidence_snapshot_sha256=evidence_sha,
            candidate_version_ids=versions,
        )
        reviewer_identity = dict(reviewer)
        review_run_id = str(handle["review_run_id"])
        opened = self.store.start_auto_mode_review(
            run_id,
            review_run_id=review_run_id,
            audit_id=str(handle["audit_id"]),
            idempotency_key=str(handle["review_start_idempotency_key"]),
            candidate_id=candidate_id,
            candidate_snapshot_sha256=candidate_sha,
            evidence_snapshot=payload,
            evidence_snapshot_sha256=evidence_sha,
            round_index=round_index,
            # The round's first attempt. How many transient reviewer retries it
            # took is only knowable once the reviewer answered, and lands on the
            # completion assessment instead -- this row exists precisely to be
            # durable before that is known.
            attempt=1,
            reviewer={
                "profile_id": reviewer_identity.get("profile_id")
                or "scientific-reviewer",
                "profile_revision": int(reviewer_identity.get("profile_revision") or 1),
                "model_fingerprint": reviewer_identity.get("model_fingerprint")
                or "unknown",
            },
        )
        if (
            str(opened.get("run_id") or "") != run_id
            or str(opened.get("review_run_id") or "") != review_run_id
            or str(opened.get("candidate_id") or "") != candidate_id
            or str(opened.get("candidate_snapshot_sha256") or "") != candidate_sha
            or str(opened.get("evidence_snapshot_sha256") or "") != evidence_sha
            or str((opened.get("event") or {}).get("type") or "")
            != "auto_audit_started"
        ):
            raise AutoModeConflictError("durable review open transition mismatch")
        handle["open_transition"] = opened
        return handle

    def _review_binding(
        self,
        *,
        root_frame_id: str,
        branch_id: str,
        turn_id: str,
        execution_id: str,
        mode: str,
        snapshot: Mapping[str, Any],
        gates_completion: bool,
        round_index: int,
    ) -> dict[str, Any]:
        payload = _durable_snapshot_payload(snapshot)
        candidate_sha = candidate_snapshot_digest(snapshot)
        evidence_sha = _storage_digest(payload)
        binding_sha = _storage_digest(
            {
                "turn_id": turn_id,
                "candidate_snapshot_sha256": candidate_sha,
                "evidence_snapshot_sha256": evidence_sha,
            }
        )
        turn_token = hashlib.sha256(str(turn_id).encode("utf-8")).hexdigest()[:12]
        binding_token = binding_sha[:20]
        round_token = max(0, int(round_index))
        candidate_id = f"cand-{turn_token}-{binding_token}"
        review_run_id = f"review-{turn_token}-r{round_token}-{binding_token}"
        return {
            "run_id": f"auto-{root_frame_id}-{turn_id}",
            "review_run_id": review_run_id,
            "audit_id": f"audit-{turn_token}-r{round_token}-{binding_token}",
            "candidate_id": candidate_id,
            "root_frame_id": str(root_frame_id),
            "branch_id": str(branch_id),
            "turn_id": str(turn_id),
            "execution_id": str(execution_id),
            "mode": mode,
            "round_index": round_token,
            "gates_completion": bool(gates_completion),
            "candidate_snapshot_sha256": candidate_sha,
            "evidence_snapshot_sha256": evidence_sha,
            "snapshot_sha256": str(snapshot.get("snapshot_sha256") or ""),
            "evidence_snapshot": payload,
            "run_idempotency_key": f"{turn_token}:auto-run",
            "candidate_idempotency_key": f"{turn_token}:candidate:{binding_token}",
            "review_start_idempotency_key": (
                f"{turn_token}:review:r{round_token}:{binding_token}:start"
            ),
            "review_close_idempotency_key": (
                f"{turn_token}:review:r{round_token}:{binding_token}:complete"
            ),
        }

    def _empty_durable_proof(
        self,
        *,
        root_frame_id: str,
        branch_id: str,
        turn_id: str,
        execution_id: str,
        mode: str,
        snapshot: Mapping[str, Any],
        gates_completion: bool,
        round_index: int,
    ) -> dict[str, Any]:
        binding = self._review_binding(
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            turn_id=turn_id,
            execution_id=execution_id,
            mode=mode,
            snapshot=snapshot,
            gates_completion=gates_completion,
            round_index=round_index,
        )
        return self._proof_from_handle(binding, opened=False, closed=False)

    @staticmethod
    def _proof_from_handle(
        handle: Mapping[str, Any],
        *,
        opened: bool,
        closed: bool,
        close_transition: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        opening = handle.get("open_transition")
        opening = opening if isinstance(opening, Mapping) else {}
        open_event = opening.get("event")
        open_event = open_event if isinstance(open_event, Mapping) else {}
        closing = close_transition if isinstance(close_transition, Mapping) else {}
        close_event = closing.get("event")
        close_event = close_event if isinstance(close_event, Mapping) else {}
        return {
            "schema_version": 1,
            "run_id": handle.get("run_id"),
            "review_run_id": handle.get("review_run_id"),
            "candidate_id": handle.get("candidate_id"),
            "root_frame_id": handle.get("root_frame_id"),
            "branch_id": handle.get("branch_id"),
            "turn_id": handle.get("turn_id"),
            "execution_id": handle.get("execution_id"),
            "mode": handle.get("mode"),
            "round_index": handle.get("round_index"),
            "gates_completion": bool(handle.get("gates_completion")),
            "candidate_snapshot_sha256": handle.get("candidate_snapshot_sha256"),
            "evidence_snapshot_sha256": handle.get("evidence_snapshot_sha256"),
            "snapshot_sha256": handle.get("snapshot_sha256"),
            "opened": bool(opened),
            "closed": bool(closed),
            "status": closing.get("status") if closed else None,
            "verdict": closing.get("verdict") if closed else None,
            "open_event_id": open_event.get("event_id") if opened else None,
            "close_event_id": close_event.get("event_id") if closed else None,
        }

    def _bind_finding_identities(
        self, review_run_id: str, result: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        """Give every finding the durable id it will be stored under.

        Idempotent: :func:`scoped_finding_id` is a pure function of the review
        run and the fingerprint, so calling this again cannot change an answer.
        That is what lets `close_review_run` stay correct when called on its
        own, without the caller having to remember to bind first. Read-only
        mappings are normalized so a valid finding is never silently omitted.
        """

        bound: list[dict[str, Any]] = []
        for item in result.get("findings") or []:
            if not isinstance(item, Mapping):
                continue
            normalized = dict(item)
            fingerprint = normalized.get("fingerprint")
            if not fingerprint:
                rebuilt = self._finding(
                    severity=str(normalized.get("severity") or "medium"),
                    category=str(normalized.get("category") or "other"),
                    claim_ref=str(
                        normalized.get("claim_ref")
                        or normalized.get("claim")
                        or "finding"
                    ),
                    evidence_refs=list(normalized.get("evidence_refs") or []),
                    reproduction=str(normalized.get("reproduction") or ""),
                )
                fingerprint = rebuilt["fingerprint"]
                normalized["fingerprint"] = fingerprint
            normalized["finding_id"] = scoped_finding_id(
                review_run_id, str(fingerprint)
            )
            bound.append(normalized)
        if isinstance(result, MutableMapping):
            result["findings"] = bound
        return bound

    def close_review_run(
        self,
        handle: Mapping[str, Any],
        result: Mapping[str, Any],
        *,
        gates_completion: bool = False,
    ) -> dict[str, Any]:
        """Record the verdict on the review opened by :meth:`open_review_run`."""

        review_run_id = str(handle["review_run_id"])
        # Idempotent, and the reason the id the caller goes on to quote is the
        # id that was stored: Stage 5 hands these to `start_auto_mode_repair`,
        # which checks they exist.
        bound_findings = self._bind_finding_identities(review_run_id, result)
        findings = []
        for item in bound_findings:
            fingerprint = str(item.get("fingerprint") or "")
            finding_id = str(item.get("finding_id") or "")
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
        return self.store.complete_auto_mode_review(
            review_run_id,
            idempotency_key=str(handle["review_close_idempotency_key"]),
            status=status,
            verdict=verdict,
            assessment={
                "public_summary": result.get("summary"),
                "shadow": not gates_completion,
                "gates_completion": bool(gates_completion),
                "stage": 4 if gates_completion else 3,
                "attempts": int(result.get("attempts") or 1),
            },
            findings=findings,
            usage=result.get("usage") or {},
        )

    def _close_review_safely(
        self,
        handle: Mapping[str, Any],
        result: Mapping[str, Any],
        *,
        gates_completion: bool,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        current = dict(result)
        last_error: Exception | None = None
        # The first retry covers a response lost after SQLite committed: the
        # Store's idempotency proof turns it into a read.
        for _attempt in range(2):
            try:
                closed = self.close_review_run(
                    handle, current, gates_completion=gates_completion
                )
                if (
                    str(closed.get("run_id") or "") != str(handle.get("run_id") or "")
                    or str(closed.get("review_run_id") or "")
                    != str(handle.get("review_run_id") or "")
                    or str(closed.get("candidate_id") or "")
                    != str(handle.get("candidate_id") or "")
                    or str((closed.get("event") or {}).get("type") or "")
                    != "auto_audit_completed"
                ):
                    raise AutoModeConflictError(
                        "durable review close transition mismatch"
                    )
                return current, self._proof_from_handle(
                    handle, opened=True, closed=True, close_transition=closed
                )
            except Exception as error:  # noqa: BLE001 - retry then close unavailable
                last_error = error

        # An invalid assessment must not keep its durable owner active.  A fixed
        # unavailable result has no findings and is accepted by the same review
        # completion transaction whenever the original completion rolled back.
        snapshot = current.get("snapshot")
        snapshot = dict(snapshot) if isinstance(snapshot, Mapping) else {}
        reviewer = current.get("reviewer")
        reviewer = dict(reviewer) if isinstance(reviewer, Mapping) else {}
        unavailable = self._unavailable_result(
            snapshot,
            reviewer,
            reason="durable_review_close_failed",
            summary=(str(last_error)[:300] if last_error else "Review close failed"),
            gates_completion=gates_completion,
        )
        for _attempt in range(2):
            try:
                closed = self.close_review_run(
                    handle, unavailable, gates_completion=gates_completion
                )
                return unavailable, self._proof_from_handle(
                    handle, opened=True, closed=True, close_transition=closed
                )
            except Exception as error:  # noqa: BLE001 - caller must fail closed
                last_error = error
        proof = self._proof_from_handle(handle, opened=False, closed=False)
        proof["close_error"] = type(last_error).__name__ if last_error else "unknown"
        return current, proof

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
