"""Stage 4: the candidate is provisional until a review promotes it.

The turn used to emit the answer, persist it, and only then review it. Nothing
about the emission or the row was conditional on the verdict, so a wrong
numeric claim was readable and copyable for the whole reviewer round-trip, a
`kill -9` in between left it durable forever with no review and nothing that
would revisit it, and a repair that corrected the claim had no way to reach the
user at all -- the gate could only refuse to promote the text it could not fix.

These cover the contract that replaced it: candidate -> frozen evidence ->
review -> promotion, with the delivered bytes and the reviewed bytes required
to be the same bytes.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from openai4s.config import AutoModeBudgets, AutoModeConfig, Config, RoadmapFeatureFlags
from openai4s.server.completion_gate import CompletionGateService
from openai4s.server.scientific_review import (
    ScientificReviewService,
    durable_review_matches,
)
from openai4s.store import Store

GATEWAY = Path("openai4s/server/gateway.py")


def _cfg(*, stage5=True, mode="auto_fix", stage2=True, stage3=True):
    return Config(
        roadmap_features=RoadmapFeatureFlags(
            stage2_auto_run_storage=stage2,
            stage3_scientific_review_shadow=stage3,
            stage4_review_completion_gate=True,
            stage5_auto_repair=stage5,
        ),
        auto_mode=AutoModeConfig(
            result_review_mode=mode,
            budgets=AutoModeBudgets(max_repair_rounds=2),
        ),
    )


def _llm(model):
    return SimpleNamespace(
        provider="openai",
        model=model,
        base_url="https://review.example/v1",
        timeout_s=30,
        max_tokens=400,
    )


def _store(tmp_path, name="stage4.db"):
    store = Store(tmp_path / name)
    store.create_project(name="p", project_id="project-1")
    store._conn.execute(
        "INSERT INTO frames(frame_id,parent_id,project_id,root_frame_id,kind,"
        "status,depth,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        ("root-1", None, "project-1", "root-1", "turn", "processing", 0, 1, 1),
    )
    store._conn.commit()
    store.ensure_session_branch(root_frame_id="root-1", branch_id="root-1")
    return store


def _services(store, cfg, chat, mode="auto_fix"):
    auto = SimpleNamespace(
        get=lambda frame_id: {
            "selection": {
                "result_review_mode": mode,
                "preset": "autonomous",
                "approvals_reviewer": "auto_review",
            }
        }
    )
    review = ScientificReviewService(
        store=store, config=cfg, auto_mode=auto, chat_call=chat
    )
    return CompletionGateService(
        store=store, config=cfg, scientific_review=review, auto_mode=auto
    )


def _pass_chat(messages, cfg, **kwargs):
    return {
        "content": json.dumps({"verdict": "pass", "summary": "ok", "findings": []}),
        "usage": {},
    }


# --- promotion ---------------------------------------------------------------


def test_stage4_gate_is_independent_of_the_stage3_shadow_flag(tmp_path):
    """Disabling Stage 3 shadow review must not silently disable Stage 4."""

    store = _store(tmp_path)
    gate = _services(store, _cfg(stage3=False), _pass_chat)
    assert gate.gates_turn("root-1") is True
    result = gate.gate_after_turn(
        root_frame_id="root-1",
        project_id="project-1",
        branch_id="root-1",
        turn_id="turn-stage4-only",
        execution_id="exec-stage4-only",
        user_request="state it",
        candidate_answer="a qualitative limitation",
        agent_cfg=_llm("agent"),
        reviewer_cfg=_llm("reviewer"),
        mode_override="review_only",
    )
    assert result["terminal"] == "verified"
    assert _services(store, _cfg(), _pass_chat).gates_turn("root-1") is True
    assert (
        _services(store, _cfg(), _pass_chat, mode="off").gates_turn("root-1") is False
    )
    store.close()


def test_unknown_or_broken_mode_resolution_keeps_stage4_armed(tmp_path):
    store = _store(tmp_path, "mode-resolution.db")
    cfg = _cfg()
    review = ScientificReviewService(store=store, config=cfg, chat_call=_pass_chat)

    for auto in (
        SimpleNamespace(get=lambda frame_id: None),
        SimpleNamespace(get=lambda frame_id: {"selection": {}}),
        SimpleNamespace(
            get=lambda frame_id: {"selection": {"result_review_mode": "bogus"}}
        ),
    ):
        gate = CompletionGateService(
            store=store, config=cfg, scientific_review=review, auto_mode=auto
        )
        assert gate.active_mode("root-1") == "review_only"
        assert gate.gates_turn("root-1") is True

    def _broken(_frame_id):
        raise RuntimeError("selection unavailable")

    gate = CompletionGateService(
        store=store,
        config=cfg,
        scientific_review=review,
        auto_mode=SimpleNamespace(get=_broken),
    )
    assert gate.active_mode("root-1") == "review_only"
    assert gate.gates_turn("root-1") is True
    assert gate.gates_turn("root-1", mode_override="off") is False
    store.close()


def test_verified_requires_the_delivered_bytes_to_be_the_reviewed_bytes(tmp_path):
    """The one claim `verified` makes, enforced as an invariant not a story.

    `repaired_but_undelivered` covers the single route known to change the
    answer. This is the backstop for every other one, including any added
    later: if the gate cannot show that what it is certifying is what was read,
    it fails closed rather than guessing.
    """

    store = _store(tmp_path)
    gate = _services(store, _cfg(), _pass_chat, mode="review_only")

    reviewed = gate.gate_after_turn(
        root_frame_id="root-1",
        project_id="project-1",
        branch_id="root-1",
        turn_id="turn-ok",
        execution_id="exec-ok",
        user_request="state it",
        candidate_answer="a qualitative limitation",
        agent_cfg=_llm("agent"),
        reviewer_cfg=_llm("reviewer"),
    )
    assert reviewed["terminal"] == "verified"
    assert reviewed["final_answer"] == "a qualitative limitation"
    assert reviewed["answer_replaced"] is False

    # A real turn finalizes before the next starts. Use another session Store
    # here so this artificial second review is not rejected by the first
    # deliberately-unfinalized AutoRun owner.
    drift_store = _store(tmp_path, "stage4-drift.db")
    drift_gate = _services(drift_store, _cfg(), _pass_chat, mode="review_only")
    original = drift_gate.scientific_review.evaluate

    def _drifting(snapshot, **kwargs):
        result = original(snapshot, **kwargs)
        result["snapshot"] = {
            **result["snapshot"],
            "candidate_answer": "something else entirely",
        }
        return result

    drift_gate.scientific_review.evaluate = _drifting
    drifted = drift_gate.gate_after_turn(
        root_frame_id="root-1",
        project_id="project-1",
        branch_id="root-1",
        turn_id="turn-drift",
        execution_id="exec-drift",
        user_request="state it",
        candidate_answer="a qualitative limitation",
        agent_cfg=_llm("agent"),
        reviewer_cfg=_llm("reviewer"),
    )
    assert drifted["terminal"] == "review_unavailable"
    assert "durable_review_proof_missing" in drifted["user_truth"]
    drift_store.close()
    store.close()


def test_same_turn_different_candidate_cannot_replay_a_green_proof(tmp_path):
    store = _store(tmp_path, "same-turn-proof.db")
    gate = _services(store, _cfg(), _pass_chat, mode="review_only")
    common = {
        "root_frame_id": "root-1",
        "project_id": "project-1",
        "branch_id": "root-1",
        "turn_id": "turn-same",
        "execution_id": "exec-same",
        "user_request": "state it",
        "agent_cfg": _llm("agent"),
        "reviewer_cfg": _llm("reviewer"),
    }
    first = gate.gate_after_turn(**common, candidate_answer="candidate alpha")
    assert first["terminal"] == "verified"

    # Even if a caller copies every proof field from the first result, changing
    # the same turn's candidate bytes invalidates both the snapshot digest and
    # exact-candidate comparison.
    replay = dict(first)
    replay["snapshot"] = {
        **dict(first["snapshot"]),
        "candidate_answer": "candidate beta",
    }
    assert not durable_review_matches(
        replay,
        candidate_answer="candidate beta",
        root_frame_id="root-1",
        branch_id="root-1",
        turn_id="turn-same",
        execution_id="exec-same",
        gates_completion=True,
    )

    second = gate.gate_after_turn(**common, candidate_answer="candidate beta")
    assert second["durable_review"]["candidate_snapshot_sha256"] != (
        first["durable_review"]["candidate_snapshot_sha256"]
    )
    if second["terminal"] == "verified":
        assert second["durable_review"]["candidate_id"] != (
            first["durable_review"]["candidate_id"]
        )
        assert durable_review_matches(
            second,
            candidate_answer="candidate beta",
            root_frame_id="root-1",
            branch_id="root-1",
            turn_id="turn-same",
            execution_id="exec-same",
            gates_completion=True,
        )
    store.close()


def test_failed_delivery_seals_the_review_run_as_unavailable_without_promotion(
    tmp_path,
):
    """A reviewed candidate is not terminal until its delivery is resolved."""

    store = _store(tmp_path)
    gate = _services(store, _cfg(), _pass_chat, mode="review_only")
    reviewed = gate.gate_after_turn(
        root_frame_id="root-1",
        project_id="project-1",
        branch_id="root-1",
        turn_id="turn-retract",
        execution_id="exec-retract",
        user_request="state it",
        candidate_answer="a qualitative limitation",
        agent_cfg=_llm("agent"),
        reviewer_cfg=_llm("reviewer"),
    )
    assert reviewed["terminal"] == "verified"
    assert reviewed["finalized"] is False
    assert gate.load("root-1") is None

    failed = gate.finalize_after_delivery("root-1", "root-1", reviewed, delivered=False)
    assert failed["terminal"] == "review_unavailable"
    assert failed["finalized"] is True
    assert failed["durable_terminal"] is True
    assert gate.load("root-1")["terminal"] == "review_unavailable"
    events = store.list_auto_mode_events("root-1", branch_id="root-1")
    assert [item["type"] for item in events].count("auto_run_terminal") == 1

    # Exact replay reads the same immutable terminal instead of emitting one.
    replay = gate.finalize_after_delivery("root-1", "root-1", reviewed, delivered=False)
    assert replay["terminal"] == "review_unavailable"
    assert [
        item["type"]
        for item in store.list_auto_mode_events("root-1", branch_id="root-1")
    ].count("auto_run_terminal") == 1
    store.close()


# --- durability --------------------------------------------------------------


def test_the_candidate_is_durable_before_the_reviewer_is_called(tmp_path):
    """Freeze then review, in storage too, or the record proves nothing.

    All four rows used to be written after `evaluate` returned, which made the
    entire durable sequence conditional on the reviewer answering: a crash
    during the round-trip left no candidate, no evidence and no open review --
    indistinguishable from a turn that never produced an answer.
    """

    store = _store(tmp_path)
    seen: dict[str, object] = {}

    def _observing_chat(messages, cfg, **kwargs):
        # What storage knows at the moment the reviewer is invoked.
        seen["runs"] = store.list_auto_mode_events("root-1", branch_id="root-1")
        return _pass_chat(messages, cfg, **kwargs)

    gate = _services(store, _cfg(), _observing_chat, mode="review_only")
    gate.gate_after_turn(
        root_frame_id="root-1",
        project_id="project-1",
        branch_id="root-1",
        turn_id="turn-durable",
        execution_id="exec-durable",
        user_request="state it",
        candidate_answer="a qualitative limitation",
        agent_cfg=_llm("agent"),
        reviewer_cfg=_llm("reviewer"),
    )
    types = [str(item.get("type")) for item in (seen.get("runs") or [])]
    assert "candidate_ready" in types, types
    assert "auto_audit_started" in types, types
    assert "auto_audit_completed" not in types, types
    store.close()


# --- ordering in the turn loop ----------------------------------------------


def test_the_turn_loop_orders_candidate_review_then_promotion():
    """gateway.py composes the phases in the order Stage 4 defines.

    The gate is a composition-order contract, and the composition lives in the
    one file CLAUDE.md keeps deliberately thin. Reordering these three phases
    is exactly the regression this stage exists to prevent, and it is invisible
    to every test that only calls the services directly.
    """

    source = GATEWAY.read_text("utf-8")

    metadata_at = source.index("provisional_metadata: dict[str, object]")
    delivery_at = source.index("message_metadata=provisional_metadata", metadata_at)
    message_at = source.index("candidate_row = self.store.add_message(", metadata_at)
    withhold_at = source.index('"provisional": True', metadata_at)
    gate_at = source.index("self.completion_gate.gate_after_turn(")
    promote_at = source.index("self.completion_gate.finalize_after_delivery(", gate_at)
    resolved_at = source.index('"type": "candidate_resolved"', promote_at)

    assert delivery_at < gate_at, "the Stage 1 candidate must precede review"
    assert message_at < gate_at, "the plain candidate must precede review"
    assert withhold_at < gate_at, "the provisional marker precedes the review"
    assert gate_at < promote_at, "promotion must follow the review"
    assert promote_at < resolved_at, "only finalized promotion resolves the candidate"

    # Exact identity travels into the atomic terminal transaction. Nothing
    # guesses at whichever assistant row happens to be newest afterwards.
    assert "message_id=(" in source[promote_at:resolved_at]
    assert "expected_message_content=(" in source[promote_at:resolved_at]
    assert "stamp_delivered_answer" not in source
    # The delivery contract has exactly one implementation, so the gated and
    # ungated orderings cannot drift apart.
    assert source.count("def _deliver_final_answer(") == 1
    assert source.count("self._deliver_final_answer(") == 2


# --- the repaired candidate reaches the user ---------------------------------


def _issues_chat(messages, cfg, **kwargs):
    return {
        "content": json.dumps(
            {
                "verdict": "issues",
                "summary": "the count is wrong",
                "findings": [
                    {
                        "severity": "high",
                        "category": "wrong_claim",
                        "claim_ref": "n=100",
                        "evidence_refs": [],
                        "reproduction": "the table has 97 rows",
                    }
                ],
            }
        ),
        "usage": {},
    }


class _StubRepair:
    """Stands in for Stage 5. The real repair logic is covered by its own suite.

    What matters here is only what the gate does with a repaired candidate,
    so the repair is reduced to "it returns corrected text and a clean verdict".
    """

    feature_enabled = True

    def __init__(self, repaired: str) -> None:
        self.repaired = repaired
        self.calls: list[dict] = []

    def run(self, *, initial, **kwargs):
        self.calls.append(kwargs)
        return {
            **dict(initial),
            "verdict": "pass",
            "findings": [],
            "snapshot": {
                **dict(initial.get("snapshot") or {}),
                "candidate_answer": self.repaired,
            },
        }


def _repairing_gate(store, repaired):
    cfg = _cfg(stage2=False)
    auto = SimpleNamespace(
        get=lambda frame_id: {
            "selection": {
                "result_review_mode": "auto_fix",
                "preset": "autonomous",
                "approvals_reviewer": "auto_review",
            }
        }
    )
    review = ScientificReviewService(
        store=store, config=cfg, auto_mode=auto, chat_call=_issues_chat
    )
    repair = _StubRepair(repaired)
    gate = CompletionGateService(
        store=store,
        config=cfg,
        scientific_review=review,
        auto_mode=auto,
        auto_repair=repair,
    )
    return gate, repair


def _run(gate, *, turn_id, deliver_replacement):
    return gate.gate_after_turn(
        root_frame_id="root-1",
        project_id="project-1",
        branch_id="root-1",
        turn_id=turn_id,
        execution_id="exec-" + turn_id,
        user_request="summarise the table",
        candidate_answer="n=100, and there are no missing values",
        agent_cfg=_llm("agent"),
        reviewer_cfg=_llm("reviewer"),
        deliver_replacement=deliver_replacement,
    )


def test_a_repaired_candidate_is_handed_back_for_delivery(tmp_path):
    """The fix has to be able to reach the user, not just exist in memory.

    The repair loop rewrites the answer to compute its verdict, but the message
    was streamed and persisted before it ran, so the correction had nowhere to
    go: the gate's only remaining move was to refuse to promote text it could
    not replace. The user was left reading the wrong number with a badge saying
    the answer was unverified, and no way to see the right one.
    """

    store = _store(tmp_path, "repair-yes.db")
    gate, repair = _repairing_gate(store, "n=97, and age has 3 missing values")

    result = _run(gate, turn_id="turn-fixed", deliver_replacement=True)

    assert result["answer_replaced"] is True
    assert result["final_answer"] == "n=97, and age has 3 missing values"
    # The repair ran, and it was not asked to certify itself.
    assert repair.calls and repair.calls[0]["result_review_mode"] == "auto_fix"
    # Deliverable, so the conservative "could not be delivered" downgrade is
    # exactly what must NOT happen.
    assert "repaired answer was not delivered" not in result["user_truth"]
    store.close()


def test_a_repair_the_caller_cannot_deliver_still_never_reaches_verified(tmp_path):
    """The fail-closed half of the same contract, kept honest.

    A caller that has already shown and stored the answer cannot deliver a
    replacement. Promoting to Verified there would put a green badge on text
    the reviewer never approved -- the user would be told "n=100, no missing
    values" is verified because "n=97, missing values in age=3" passed.
    """

    store = _store(tmp_path, "repair-no.db")
    gate, _ = _repairing_gate(store, "n=97, and age has 3 missing values")

    result = _run(gate, turn_id="turn-stuck", deliver_replacement=False)

    assert result["answer_replaced"] is False
    assert result["final_answer"] == "n=100, and there are no missing values"
    assert result["terminal"] != "verified"
    assert result["gate"]["unverified"] is True
    assert result["reason"] == "repaired_answer_not_delivered"
    store.close()


def test_an_unchanged_repair_leaves_the_candidate_alone(tmp_path):
    """A repair that changed nothing is not a replacement.

    Re-delivering identical bytes would blank and re-render the answer in the
    live UI for no reason, and would persist a second row saying the same
    thing.
    """

    store = _store(tmp_path, "repair-noop.db")
    unchanged = "n=100, and there are no missing values"
    gate, _ = _repairing_gate(store, unchanged)

    result = _run(gate, turn_id="turn-noop", deliver_replacement=True)

    assert result["answer_replaced"] is False
    assert result["final_answer"] == unchanged
    store.close()


def test_a_tool_only_turn_never_stamps_the_previous_turns_answer():
    """`writable` being empty means two opposite things; only one may stamp.

    Stage 1 delivery leaves it empty because it already wrote this turn's row,
    which then needs the verdict stamped onto it. A gated tool-only turn leaves
    it empty because there is no answer at all -- and the newest assistant row
    in the branch is the PREVIOUS turn's, which stamping would relabel with
    this turn's result.
    """

    source = GATEWAY.read_text("utf-8")
    assert "stamp_delivered_answer" not in source
    finalize_at = source.index("self.completion_gate.finalize_after_delivery(")
    resolved_at = source.index('"type": "candidate_resolved"', finalize_at)
    call = source[finalize_at:resolved_at]
    assert "message_id=(" in call
    assert "candidate_row" in call
    assert "if promotion_ready" in call
    assert "expected_message_content=(" in call


def test_the_stamped_row_and_the_persisted_row_carry_the_same_verdict(tmp_path):
    """Two writers, one shape -- or reopen shows a verdict of `null`.

    The persist loop passes the row metadata to `add_message`; the Stage 1
    delivery path writes its own row bound to an Artifact manifest and stamps
    the verdict afterwards. When each built the mapping itself, the stamping
    path read `terminal` off a mapping that calls that field `review_status`,
    and wrote `"review_status": null` beside `"user_truth": "Verified"`. Live
    was right, because the badge came off the socket; reopening showed no badge
    at all, which is the exact failure this stage exists to prevent.
    """

    from openai4s.server.completion_gate import message_review_metadata

    gate = {"terminal": "verified", "user_truth": "Verified", "unverified": False}
    from_gate = message_review_metadata(gate)
    # The row metadata is itself a valid input: the gateway holds that shape,
    # not the gate, by the time Stage 1 delivery needs stamping.
    from_row = message_review_metadata(from_gate)

    assert from_gate == from_row
    assert from_gate["review_status"] == "verified"
    assert from_gate["unverified"] is False

    # `unverified` restates the terminal, so it is derived rather than read.
    # The result mapping the gateway holds carries no `unverified` key at all;
    # reading one produced `None` where it was absent and, on the path that did
    # supply a default, stamped a `completed_with_issues` row `unverified:
    # False` -- one field saying not verified beside another saying it was.
    for terminal in ("completed_with_issues", "review_unavailable"):
        row = message_review_metadata({"terminal": terminal, "unverified": False})
        assert row["unverified"] is True, row
    assert message_review_metadata({"terminal": "verified"})["unverified"] is False

    # The legacy latest-row mutator is intentionally gone: exact row identity
    # is required by ``finalize_after_delivery`` and Store's message CAS.
    assert not hasattr(CompletionGateService, "stamp_delivered_answer")
