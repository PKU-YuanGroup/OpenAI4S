"""Per-claim reconciliation for the in-cell ``host.submit_output`` sibling.

``finalize_response`` reconciles against the turn's execution ledger
(``tests/test_structured_finalize.py``); these contracts cover the other
completion path: a cell that really ran (so the zero-execution guard does not
apply) submitting an ``output`` whose file claims or summary numbers outrun
what the run can prove.  Refusals stay soft — ``{"error": msg}`` becomes a
``RuntimeError`` in the worker — so the model can repair and resubmit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openai4s.config import Config
from openai4s.host.completion import (
    CompletionService,
    SubmissionEvidence,
    check_summary_metrics,
    collect_file_claims,
    gather_submission_evidence,
    reconcile_submission_claims,
)
from openai4s.host_dispatch import HostDispatcher
from openai4s.store import Store


def _spec(output, bullets=("Saved the results",)):
    return {"output": output, "completion_bullets": list(bullets)}


def test_collect_file_claims_targets_produce_shaped_keys_only():
    output = {
        "files": ["result.csv", "the final table", 7],
        "artifacts": ["artifact-1", "plot.png", "the plot shown above"],
        "figures": {"heatmap": "heatmap.svg"},
        "nested": {"report": {"saved_to": "out/report.md"}},
        # Input-shaped keys and prose must never become produce claims.
        "source": "input.csv",
        "path": "/data/raw.h5",
        "inputs": ["training.parquet"],
        "note": "wrote summary.txt to disk",
        "url": "https://example.org/data.csv",
    }

    claims = dict(
        (claim, key) for key, claim in collect_file_claims({"output": output})
    )
    assert claims == {
        "result.csv": "files",
        "artifact-1": "artifacts",
        "plot.png": "artifacts",
        "heatmap.svg": "figures",
        "out/report.md": "saved_to",
    }
    # Free text under 'artifacts' is skipped (whitespace), and non-artifact
    # produce keys require file-shaped strings, so bare words there are inert.
    assert collect_file_claims({"files": ["notes without extension"]}) == []
    assert collect_file_claims("plain prose output") == []
    # The bounded walk survives hostile shapes: deep nesting and cycles.
    deep = {"files": ["deep.csv"]}
    for _ in range(20):
        deep = {"wrap": deep}
    assert collect_file_claims(deep) == []
    cyclic = {"files": ["seen.csv"]}
    cyclic["self"] = cyclic
    assert collect_file_claims(cyclic) == [("files", "seen.csv")]


def test_fabricated_claims_are_refused_with_repair_guidance():
    empty = SubmissionEvidence()
    error = reconcile_submission_claims(
        _spec({"files": ["fabricated_9f3a.csv"], "artifacts": ["artifact-77"]}),
        empty,
    )
    assert "fabricated_9f3a.csv" in error and "artifact-77" in error
    assert "never produced" in error
    assert "host.submit_output" in error  # repair guidance, not a verdict
    # No claims at all reconciles clean.
    assert reconcile_submission_claims(_spec({"answer": 42}), empty) is None


def test_claims_backed_by_store_names_pass(tmp_path):
    evidence = SubmissionEvidence(
        frozenset({"published/result.csv", "result.csv", "artifact-1", "ver-9"})
    )
    for claim in (
        {"files": ["result.csv"]},  # basename recorded with a subdir
        {"files": ["published/result.csv"]},
        {"saved_to": "elsewhere/result.csv"},  # claim basename vs stored name
        {"artifacts": ["artifact-1"]},
        {"artifacts": ["ver-9"]},
        {"files": ["RESULT.CSV"]},  # case-insensitive
    ):
        assert reconcile_submission_claims(_spec(claim), evidence) is None, claim


def test_claims_backed_by_disk_pass(tmp_path):
    (tmp_path / "real.csv").write_text("x")
    nested = tmp_path / "out" / "deep"
    nested.mkdir(parents=True)
    (nested / "buried.h5").write_text("x")
    evidence = SubmissionEvidence(search_roots=(tmp_path,))

    assert reconcile_submission_claims(_spec({"files": ["real.csv"]}), evidence) is None
    assert (
        reconcile_submission_claims(_spec({"files": ["out/deep/buried.h5"]}), evidence)
        is None
    )
    # Basename-only claims are found by the bounded shallow scan.
    assert (
        reconcile_submission_claims(_spec({"files": ["buried.h5"]}), evidence) is None
    )
    # Absolute paths are probed directly.
    assert (
        reconcile_submission_claims(
            _spec({"saved_to": str(tmp_path / "real.csv")}), evidence
        )
        is None
    )
    assert "missing.csv" in reconcile_submission_claims(
        _spec({"files": ["missing.csv"]}), evidence
    )


def test_summary_metric_contradiction_refused_and_honest_restatements_pass():
    # The real-LLM incident: summary said 2.4495, metrics said 2.3664.
    contradiction = check_summary_metrics(
        {"summary": "The stdev 2.4495 shows the spread.", "metrics": {"stdev": 2.3664}}
    )
    assert len(contradiction) == 1
    assert "2.4495" in contradiction[0] and "2.3664" in contradiction[0]
    error = reconcile_submission_claims(
        _spec({"summary": "stdev 2.4495", "metrics": {"stdev": 2.3664}}),
        SubmissionEvidence(),
    )
    assert "stdev" in error and "2.3664" in error

    for honest in (
        {"summary": "stdev 2.3664 across runs", "metrics": {"stdev": 2.3664}},
        {"summary": "stdev of about 2.37", "metrics": {"stdev": 2.3664}},  # rounded
        {"summary": "stdev 2.4", "metrics": {"stdev": 2.3664}},  # coarser rounding
        # Nearby unrelated numbers do not shadow the stated value.
        {"summary": "stdev across 3 runs was 2.3664", "metrics": {"stdev": 2.3664}},
        {"summary": "accuracy reached 93%", "metrics": {"accuracy": 0.93}},
        {"summary": "std dev came to 2.3664", "metrics": {"std_dev": 2.3664}},
        # A key mentioned without numbers, or never mentioned, is not a claim.
        {"summary": "the stdev improved noticeably", "metrics": {"stdev": 2.3664}},
        {"summary": "analysis complete", "metrics": {"stdev": 2.3664}},
        # Non-numeric and boolean metric values are outside the contract.
        {"summary": "converged 1", "metrics": {"converged": True, "label": "a"}},
    ):
        assert check_summary_metrics(honest) == [], honest
    assert check_summary_metrics({"summary": "stdev 2.4495"}) == []
    assert check_summary_metrics("prose only") == []


def test_gather_submission_evidence_reads_store_and_survives_failure(tmp_path):
    store = Store(tmp_path / "openai4s.db")
    store.save_artifact(
        path=str(tmp_path / "model.pt"),
        filename="published/model.pt",
        content_type="application/octet-stream",
        size_bytes=4,
        checksum="c1",
        producing_cell_id="cell-1",
        frame_id=None,
    )
    store.log_cell(
        frame_id=None,
        code="open('deep/output.h5','w')",
        result={"stdout": "", "stderr": "", "error": None},
        root_frame_id="root-e",
        files_written=["deep/output.h5"],
        figures=["fig1.png"],
    )

    evidence = gather_submission_evidence(store, "root-e", (tmp_path,))
    assert {"published/model.pt", "model.pt"} <= evidence.known_names
    assert {"deep/output.h5", "output.h5", "fig1.png"} <= evidence.known_names
    assert evidence.search_roots == (tmp_path,)
    # Cell evidence is per-session; artifacts are deliberately store-wide.
    assert "output.h5" not in gather_submission_evidence(store, None, ()).known_names
    assert "model.pt" in gather_submission_evidence(store, None, ()).known_names
    # The gatherer's queries are the narrow ones: three columns per artifact,
    # two decoded lists per cell — never code/stdout or the versions join.
    names = store.list_artifact_names()
    assert names and all(
        set(row) == {"filename", "artifact_id", "latest_version_id"} for row in names
    )
    outputs = store.list_cell_outputs("root-e")
    assert outputs and all(set(row) == {"files_written", "figures"} for row in outputs)
    assert outputs[0]["files_written"] == ["deep/output.h5"]
    store.close()

    class BrokenStore:
        def list_artifact_names(self):
            raise RuntimeError("store offline")

        def list_cell_outputs(self, root_frame_id):
            raise RuntimeError("store offline")

    # A failing store must propagate so the submit path degrades to the
    # legacy accept.  Swallowing it here and returning *empty* evidence
    # inverted the degradation: an artifact-ID claim is backable only by the
    # store, so every honest ID claim was refused instead of accepted.
    with pytest.raises(RuntimeError, match="store offline"):
        gather_submission_evidence(BrokenStore(), "root-e", ())
    service = CompletionService(
        evidence=lambda: gather_submission_evidence(BrokenStore(), "root-e", ())
    )
    assert service.submit(_spec({"artifacts": ["artifact-1"]})) == {"status": "ok"}


def test_service_without_evidence_provider_keeps_legacy_accept():
    service = CompletionService()
    result = service.submit(_spec({"files": ["never_written_anywhere.csv"]}))
    assert result == {"status": "ok"}


def test_reconciliation_soft_fails_preserves_prior_state_and_is_repairable(
    tmp_path,
):
    (tmp_path / "real.csv").write_text("x")
    service = CompletionService(
        evidence=lambda: SubmissionEvidence(search_roots=(tmp_path,))
    )
    prior = {"output": {"ok": True}, "completion_bullets": ["Computed the answer"]}
    service.last_output = prior

    refused = service.submit(_spec({"files": ["fabricated_9f3a.csv"]}))
    assert set(refused) == {"error"}  # the worker soft-fail contract shape
    assert "fabricated_9f3a.csv" in refused["error"]
    assert service.last_output is prior

    repaired = service.submit(_spec({"files": ["real.csv"]}))
    assert repaired == {"status": "ok"}
    assert service.last_output["output"] == {"files": ["real.csv"]}


def test_broken_evidence_provider_degrades_to_legacy_accept():
    def explode():
        raise OSError("workspace unavailable")

    service = CompletionService(evidence=explode)
    assert service.submit(_spec({"files": ["anything.csv"]})) == {"status": "ok"}


def test_claimless_submission_never_pays_for_evidence():
    """The provider runs only when the output names produced files.

    Claim collection and the summary/metrics check are pure functions of the
    spec; a claim-less submission used to trigger the full store sweep while
    the kernel worker held the host-call lock.
    """
    calls: list[bool] = []

    def counting_evidence():
        calls.append(True)
        return SubmissionEvidence()

    service = CompletionService(evidence=counting_evidence)
    assert service.submit(_spec({"answer": 42})) == {"status": "ok"}
    assert calls == [], "no file claims — the evidence provider must not run"

    # The evidence-free half of reconciliation still applies without the
    # provider: a summary contradicting its own metrics is refused.
    refused = service.submit(
        _spec({"summary": "stdev 2.4495", "metrics": {"stdev": 2.3664}})
    )
    assert "2.3664" in refused["error"]
    assert calls == []

    # A file claim is what buys the store sweep.
    assert service.submit(_spec({"files": ["missing_zz.csv"]}))["error"]
    assert calls == [True]


def test_dispatcher_submit_reconciles_against_store_cells_and_workspace(tmp_path):
    dispatcher = HostDispatcher(Config(data_dir=tmp_path))

    refused = dispatcher._m_submit_output(
        _spec(
            {
                "files": ["fabricated_9f3a.csv"],
                "summary": "stdev 2.4495",
                "metrics": {"stdev": 2.3664},
            }
        )
    )
    assert "fabricated_9f3a.csv" in refused["error"]
    assert "2.3664" in refused["error"]
    assert dispatcher.last_output is None

    # A file the current cell just wrote exists only in the workspace so far.
    (dispatcher._files.workspace() / "fresh.csv").write_text("x")
    assert dispatcher._m_submit_output(_spec({"files": ["fresh.csv"]})) == {
        "status": "ok"
    }

    # Captured artifacts and executed-cell writes back later submissions,
    # including after the CLI late-binds the root frame.
    dispatcher.store.save_artifact(
        path=str(tmp_path / "model.pt"),
        filename="model.pt",
        content_type="application/octet-stream",
        size_bytes=4,
        checksum="c2",
        producing_cell_id="cell-9",
        frame_id=None,
    )
    dispatcher.frame_id = "root-live"
    dispatcher.store.log_cell(
        frame_id=None,
        code="save()",
        result={"stdout": "", "stderr": "", "error": None},
        root_frame_id="root-live",
        files_written=["deep/output.h5"],
    )
    accepted = dispatcher._m_submit_output(
        _spec(
            {
                "artifacts": ["model.pt"],
                "files": ["output.h5"],
                "summary": "stdev 2.3664",
                "metrics": {"stdev": 2.3664},
            }
        )
    )
    assert accepted == {"status": "ok"}
    assert dispatcher.last_output["output"]["artifacts"] == ["model.pt"]


def test_summary_metric_check_ignores_non_restatement_numbers():
    """Digits in key mentions, bounds, counts, and comma groups are not claims.

    Each of these refused an honest submission: the key's own digit ("f1",
    "top 10 accuracy"), a statistical bound ("p < 0.05", "short of the 0.95
    target"), a different-quantity count ("over the 100 epochs", "12 bootstrap
    resamples"), a comma-grouped restatement of the correct value ("1,500"),
    and an identifier fragment ("top-5" parsed as -5).  A contradiction now
    requires a near-miss: a same-sign, same-magnitude number that fails to
    match at its written precision.
    """
    for honest in (
        {"summary": "the f1 improved noticeably", "metrics": {"f1": 0.85}},
        {"summary": "we report top 10 accuracy", "metrics": {"top_10_accuracy": 0.95}},
        {"summary": "significant (p < 0.05)", "metrics": {"p": 0.032}},
        {
            "summary": "converged in fewer than 100 iterations",
            "metrics": {"iterations": 37},
        },
        {
            "summary": "accuracy fell short of the 0.95 target",
            "metrics": {"accuracy": 0.91},
        },
        {"summary": "processed 1,500 samples", "metrics": {"samples": 1500}},
        {
            "summary": "The stdev, computed over 12 bootstrap resamples of the "
            "filtered dataset, equals 2.3664.",
            "metrics": {"stdev": 2.3664},
        },
        {
            "summary": "accuracy of the top-5 classifier improved",
            "metrics": {"accuracy": 0.93},
        },
        {
            "summary": "the loss curve over the 100 epochs converged smoothly",
            "metrics": {"loss": 0.02},
        },
    ):
        assert check_summary_metrics(honest) == [], honest


def test_summary_metric_check_catches_scaled_percent_fabrication():
    """'99.9%' is one decimal of a *percentage* — ±0.001 of the fraction.

    The unscaled written-precision tolerance (±0.1) accepted 99.9% as a
    restatement of 0.93, missing exactly the percent fabrication the check
    exists for.  Integer-percent rounding stays honest.
    """
    flagged = check_summary_metrics(
        {"summary": "accuracy reached 99.9%", "metrics": {"accuracy": 0.93}}
    )
    assert len(flagged) == 1 and "99.9%" in flagged[0]
    assert (
        check_summary_metrics(
            {"summary": "accuracy reached 94%", "metrics": {"accuracy": 0.9351}}
        )
        == []
    )


def test_collect_file_claims_survives_decoy_exhaustion():
    """A deep branch or a long leaf list must not shadow shallow claims.

    The old LIFO walk aborted wholesale on the depth/node caps, so a decoy
    inserted after the claim-bearing subtree exhausted the budget first and
    the fabricated claim was never collected — a constructible bypass of the
    reconciliation.
    """
    decoy = {"x": 1}
    for _ in range(9):
        decoy = {"wrap": decoy}
    nested = {"claims_here": {"files": ["fabricated_zz.csv"]}, "decoy": decoy}
    assert collect_file_claims(nested) == [("files", "fabricated_zz.csv")]
    padded = {
        "claims_here": {"files": ["fabricated_zz.csv"]},
        "padding": [str(index) for index in range(600)],
    }
    assert collect_file_claims(padded) == [("files", "fabricated_zz.csv")]
    assert reconcile_submission_claims(_spec(nested), SubmissionEvidence()) is not None


def test_artifacts_mapping_labels_are_not_id_claims():
    """{'artifacts': {label: file}} must be judged by its values, not labels.

    The label key ('heatmap') passed the identifier filter and became a
    must-resolve artifact ID, refusing an honest submission whose value was
    fully backed.
    """
    evidence = SubmissionEvidence(frozenset({"heatmap.svg"}))
    spec = _spec({"artifacts": {"heatmap": "heatmap.svg"}})
    assert reconcile_submission_claims(spec, evidence) is None
    # File-shaped keys ({"report.md": "the report"}) remain claims.
    assert ("artifacts", "report.md") in collect_file_claims(
        {"artifacts": {"report.md": "the report"}}
    )
