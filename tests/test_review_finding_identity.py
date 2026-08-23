"""A finding's identity is its content *within one review*, not its content.

`review_findings.finding_id` is a global PRIMARY KEY, and it used to be derived
from the finding's content alone. Two sessions that reached the same conclusion
therefore collided: the second one's `complete_review` raised
`UNIQUE constraint failed: review_findings.finding_id`, its review died, and the
turn reported `review_unavailable` instead of the `completed_with_issues` it had
actually earned. The finding most likely to recur across sessions is a recurring
wrong claim, so the collision landed on exactly the case worth catching.

The content-only `fingerprint` stays content-only -- Stage 5 compares
fingerprints across repair rounds to notice a finding did not go away.
"""

from __future__ import annotations

import json
import sqlite3
from types import MappingProxyType, SimpleNamespace

import pytest

from openai4s.config import AutoModeConfig, Config, RoadmapFeatureFlags
from openai4s.server.completion_gate import CompletionGateService
from openai4s.server.scientific_review import ScientificReviewService, scoped_finding_id
from openai4s.store import Store

FINDING = {
    "severity": "high",
    "category": "wrong_claim",
    "claim_ref": "n=100",
    "evidence_refs": [],
    "reproduction": "the table has 97 rows",
    "suggested_fix": "recount from the table",
    "confidence": "high",
}


def _cfg():
    return Config(
        roadmap_features=RoadmapFeatureFlags(
            stage2_auto_run_storage=True,
            stage3_scientific_review_shadow=True,
            stage4_review_completion_gate=True,
        ),
        auto_mode=AutoModeConfig(result_review_mode="review_only"),
    )


def _llm(model):
    return SimpleNamespace(
        provider="openai",
        model=model,
        base_url="https://review.example/v1",
        timeout_s=30,
        max_tokens=400,
    )


def _issues_chat(messages, cfg, **kwargs):
    """Always the same finding -- the whole point of the regression."""

    return {
        "content": json.dumps(
            {
                "verdict": "issues",
                "summary": "the count is wrong",
                "findings": [dict(FINDING)],
            }
        ),
        "usage": {},
    }


def _store(tmp_path):
    store = Store(tmp_path / "identity.db")
    store.create_project(name="p", project_id="project-1")
    return store


def _frame(store, frame_id):
    store._conn.execute(
        "INSERT INTO frames(frame_id,parent_id,project_id,root_frame_id,kind,"
        "status,depth,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (frame_id, None, "project-1", frame_id, "turn", "processing", 0, 1, 1),
    )
    store._conn.commit()
    store.ensure_session_branch(root_frame_id=frame_id, branch_id=frame_id)


def _gate(store, cfg):
    auto = SimpleNamespace(
        get=lambda frame_id: {
            "selection": {
                "result_review_mode": "review_only",
                "preset": "off",
                "approvals_reviewer": "user",
            }
        }
    )
    review = ScientificReviewService(
        store=store, config=cfg, auto_mode=auto, chat_call=_issues_chat
    )
    return CompletionGateService(
        store=store, config=cfg, scientific_review=review, auto_mode=auto
    )


def _run(gate, frame_id, turn_id):
    return gate.gate_after_turn(
        root_frame_id=frame_id,
        project_id="project-1",
        branch_id=frame_id,
        turn_id=turn_id,
        execution_id="exec-" + turn_id,
        user_request="how many rows?",
        candidate_answer="the dataset has n=100 rows",
        agent_cfg=_llm("agent"),
        reviewer_cfg=_llm("reviewer"),
    )


def test_two_sessions_with_the_identical_finding_both_complete(tmp_path):
    store = _store(tmp_path)
    _frame(store, "root-1")
    _frame(store, "root-2")
    gate = _gate(store, _cfg())

    first = _run(gate, "root-1", "turn-1")
    second = _run(gate, "root-2", "turn-2")

    # Both reached the verdict they earned. Before, the second was
    # `review_unavailable (review_did_not_run)`.
    for result in (first, second):
        assert result["terminal"] == "completed_with_issues", result["user_truth"]

    rows = store._conn.execute(
        "SELECT finding_id, fingerprint, review_run_id FROM review_findings "
        "ORDER BY rowid"
    ).fetchall()
    assert len(rows) == 2, rows
    # Same content, so the same fingerprint -- Stage 5 depends on that.
    assert rows[0]["fingerprint"] == rows[1]["fingerprint"]
    # Different reviews, so different identities.
    assert rows[0]["finding_id"] != rows[1]["finding_id"]
    assert rows[0]["finding_id"] == scoped_finding_id(
        rows[0]["review_run_id"], rows[0]["fingerprint"]
    )
    store.close()


def test_the_id_the_caller_quotes_is_the_id_that_was_stored(tmp_path):
    """Stage 5 hands these ids back to `start_auto_mode_repair`, which checks
    they exist. A durable id the in-memory result never learned about would
    fail that check on every repair."""

    store = _store(tmp_path)
    _frame(store, "root-1")
    result = _run(_gate(store, _cfg()), "root-1", "turn-1")

    stored = {
        row["finding_id"]
        for row in store._conn.execute("SELECT finding_id FROM review_findings")
    }
    in_memory = {
        str(item["finding_id"]) for item in result["findings"] if item.get("finding_id")
    }
    assert in_memory and in_memory <= stored, (in_memory, stored)
    store.close()


def test_a_storage_failure_does_not_escape_as_a_bare_exception(tmp_path):
    """The turn keeps its verdict; only the promotion is refused.

    A narrow `except` tuple let `sqlite3.IntegrityError` out of
    `shadow_after_turn`, the gate discarded the entire result far upstream, and
    the user was told the review did not run when it had reached a verdict.
    """

    store = _store(tmp_path)
    _frame(store, "root-1")
    gate = _gate(store, _cfg())
    review = gate.scientific_review

    def _boom(*args, **kwargs):
        raise sqlite3.IntegrityError("UNIQUE constraint failed: review_findings.x")

    review.store = SimpleNamespace(
        **{
            name: getattr(store, name)
            for name in dir(store)
            if not name.startswith("__") and callable(getattr(store, name, None))
        }
    )
    review.store.complete_auto_mode_review = _boom

    result = _run(gate, "root-1", "turn-1")

    assert result is not None, "the verdict must survive a storage failure"
    assert result["verdict"] == "issues"
    # No durable pass exists, so promotion is refused -- but with a terminal
    # that describes the turn rather than claiming the review never happened.
    assert result["terminal"] != "verified"
    store.close()


@pytest.mark.parametrize("review_run_id", ["review-a", "review-b"])
def test_scoped_ids_are_stable_and_scope_separating(review_run_id):
    assert scoped_finding_id(review_run_id, "abc") == scoped_finding_id(
        review_run_id, "abc"
    )
    assert scoped_finding_id("review-a", "abc") != scoped_finding_id("review-b", "abc")
    assert scoped_finding_id(review_run_id, "abc") != scoped_finding_id(
        review_run_id, "abd"
    )


def test_a_collision_no_longer_strands_the_branch_in_reviewing(tmp_path):
    """Why the fix needs no data migration for existing databases.

    The collision rolled `complete_review` back, so the Auto Run stayed in
    `reviewing` -- not a terminal status -- and later work on that branch was
    refused. The current gate returns a reviewed proposal first; the delivery
    finalizer then commits its terminal state.

    Both proposals can now be finalized, so no branch is left blocked.
    Databases that were already stranded do not need a migration: the run
    carries the dead daemon's `owner_instance_id`, and boot-time reconciliation
    abandons it (see `test_auto_mode_recovery_regressions.py`).
    """

    store = _store(tmp_path)
    _frame(store, "root-1")
    _frame(store, "root-2")
    gate = _gate(store, _cfg())

    first = _run(gate, "root-1", "turn-1")
    second = _run(gate, "root-2", "turn-2")
    for frame_id, reviewed in (("root-1", first), ("root-2", second)):
        finalized = gate.finalize_after_delivery(
            frame_id, frame_id, reviewed, delivered=True
        )
        assert finalized["terminal"] == "completed_with_issues", finalized
        assert finalized["durable_terminal"] is True

    for frame_id in ("root-1", "root-2"):
        run = store.project_auto_mode_run(frame_id, frame_id)["run"]
        assert run is not None, frame_id
        assert run["status"] != "reviewing", (frame_id, run["status"])
        assert run["status"] == "completed_with_issues", (frame_id, run["status"])
    store.close()


def test_the_review_step_and_the_findings_table_agree_on_every_id(tmp_path):
    """Three records of one finding, one id.

    The durable close writes the findings table before the visible review step,
    whose output JSON embeds the same findings. Binding before that close keeps
    both records aligned; assigning an id to only one writer would recreate the
    same two-records-disagreeing shape one layer up, and a test that reads only
    the table would miss it.
    """

    store = _store(tmp_path)
    _frame(store, "root-1")
    _run(_gate(store, _cfg()), "root-1", "turn-1")

    stored = {
        row["finding_id"]
        for row in store._conn.execute("SELECT finding_id FROM review_findings")
    }
    assert stored

    steps = [
        json.loads(row["output"])
        for row in store._conn.execute(
            "SELECT output FROM frame_steps WHERE kind='review' AND output IS NOT NULL"
        )
    ]
    quoted = {
        str(finding["finding_id"])
        for step in steps
        for finding in (step.get("findings") or [])
        if finding.get("finding_id")
    }
    assert quoted, "the review step records its findings"
    assert quoted <= stored, sorted(quoted - stored)
    store.close()


def test_read_only_mapping_finding_is_normalized_and_persisted(tmp_path):
    """A valid Mapping must not disappear merely because it is immutable."""

    store = _store(tmp_path)
    _frame(store, "root-1")
    gate = _gate(store, _cfg())
    review = gate.scientific_review
    evaluate = review.evaluate

    def _read_only_findings(*args, **kwargs):
        result = evaluate(*args, **kwargs)
        result["findings"] = [
            MappingProxyType(dict(item)) for item in result.get("findings") or []
        ]
        return result

    review.evaluate = _read_only_findings
    result = _run(gate, "root-1", "turn-1")

    assert len(result["findings"]) == 1
    assert isinstance(result["findings"][0], dict)
    finding_id = result["findings"][0]["finding_id"]
    stored = store._conn.execute("SELECT finding_id FROM review_findings").fetchall()
    assert [row["finding_id"] for row in stored] == [finding_id]
    store.close()
