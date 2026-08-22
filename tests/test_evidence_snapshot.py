"""Frozen Evidence Snapshot completeness and reference resolution."""

from __future__ import annotations

from openai4s.server.evidence_snapshot import (
    freeze_evidence_snapshot,
    resolve_evidence_ref,
    snapshot_digest,
)


def _parts(**overrides):
    parts = {
        "identity": {
            "root_frame_id": "root-1",
            "branch_id": "root-1",
            "turn_id": "turn-1",
            "execution_id": "exec-1",
        },
        "user_request": "summarize resid.csv",
        "plan": {"title": "analyze residuals"},
        "candidate_answer": "n=2 and mean=2.0",
        "structured_completion": {"output": "done"},
        "artifacts": [
            {
                "artifact_id": "art-1",
                "filename": "resid.csv",
                "content_type": "text/csv",
                "version_id": "ver-1",
                "checksum": "a" * 64,
                "exists": True,
            }
        ],
        "adapters": [
            {
                "adapter": "table",
                "version_id": "ver-1",
                "artifact_id": "art-1",
                "complete": True,
                "summary": {"row_count": 2, "columns": {"value": {"mean": 2.0}}},
            }
        ],
        "cells": [{"cell_id": "cell-1", "status": "ok"}],
        "tool_ledger": [{"kind": "tool", "title": "read"}],
        "lineage": [{"input_version_id": "ver-0", "output_version_id": "ver-1"}],
        "environment": {"language": "python"},
        "truncation": {},
    }
    parts.update(overrides)
    return parts


def test_complete_snapshot_has_resolvable_refs():
    snapshot = freeze_evidence_snapshot(_parts())
    assert snapshot["complete"] is True
    assert snapshot["hidden_reasoning_excluded"] is True
    assert snapshot["frozen"] is True
    assert resolve_evidence_ref(snapshot, "art:ver-1")["kind"] == "artifact_version"
    assert resolve_evidence_ref(snapshot, "adapter:ver-1:table") is not None
    assert resolve_evidence_ref(snapshot, "cell:cell-1") is not None
    assert resolve_evidence_ref(snapshot, "plan:current") is not None
    assert resolve_evidence_ref(snapshot, "forged") is None
    again = freeze_evidence_snapshot(_parts())
    assert again["snapshot_sha256"] == snapshot["snapshot_sha256"]
    assert (
        snapshot_digest({k: v for k, v in snapshot.items() if k != "snapshot_sha256"})
        == snapshot["snapshot_sha256"]
    )


def test_omitted_artifact_makes_snapshot_incomplete():
    snapshot = freeze_evidence_snapshot(
        _parts(omitted_artifact_count=1, changed_artifact_count=2)
    )
    assert snapshot["complete"] is False
    assert any(item["kind"] == "artifact_omitted" for item in snapshot["omissions"])


def test_table_without_adapter_is_incomplete():
    snapshot = freeze_evidence_snapshot(_parts(adapters=[]))
    assert snapshot["complete"] is False
    assert any(item["kind"] == "adapter_incomplete" for item in snapshot["omissions"])


def test_a_clipped_answer_is_an_omission_not_a_silent_cut():
    """`complete` is `not omissions`, so a silent clip publishes Verified.

    `candidate_answer` is cut to 24,000 chars, and both reviewer paths -- the
    deterministic `inspect_snapshot` and the LLM packet -- read only the
    clipped string. A false claim past the cut was invisible to each of them
    while the snapshot still reported complete.
    """

    from openai4s.server.evidence_snapshot import freeze_evidence_snapshot

    short = freeze_evidence_snapshot({"candidate_answer": "x" * 100})
    assert short["complete"] is True

    clipped = freeze_evidence_snapshot({"candidate_answer": "x" * 30_000})
    assert clipped["complete"] is False
    assert {"kind": "truncated", "fields": ["candidate_answer"]} in clipped["omissions"]

    long_request = freeze_evidence_snapshot(
        {"candidate_answer": "x", "user_request": "q" * 20_000}
    )
    assert long_request["complete"] is False
