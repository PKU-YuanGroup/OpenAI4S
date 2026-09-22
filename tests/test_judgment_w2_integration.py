"""The W2 seam: the step card that carries suggestions to the workbench.

W2-A wraps `search_skills` in a dict when the capability is on. W2-B reads
suggestions off `step.output`. Between them sits `_step_end`, which no package
owned: it iterated the result as a list, so with the capability on the card
reported "no match" beside real hits and the chips had nothing to render.
W2-B's Vitest fixtures were green the whole time, because they feed the dict
in directly.
"""

from __future__ import annotations

from openai4s.host_dispatch import _step_end

_LEXICAL = [{"name": "single-cell-rna-analysis"}, {"name": "figure-composer"}]
_SUGGESTIONS = [
    {"name": "proteinmpnn", "p_fit": 0.81, "confidence": 0.7, "template_version": "1"}
]


def test_capability_off_projection_is_unchanged() -> None:
    output, summary = _step_end("search_skills", "skill", _LEXICAL, True)
    assert output == {"skills": ["single-cell-rna-analysis", "figure-composer"]}
    assert summary == "single-cell-rna-analysis, figure-composer"
    # No experimental keys appear on the default path, at all.
    assert "semantic_status" not in output
    assert "semantic_suggestions" not in output


def test_wrapped_result_keeps_lexical_names_and_carries_suggestions() -> None:
    payload = {
        "results": _LEXICAL,
        "semantic_status": "ok",
        "semantic_suggestions": _SUGGESTIONS,
    }
    output, summary = _step_end("search_skills", "skill", payload, True)
    assert output["skills"] == ["single-cell-rna-analysis", "figure-composer"]
    assert summary == "single-cell-rna-analysis, figure-composer"
    assert output["semantic_status"] == "ok"
    assert output["semantic_suggestions"] == _SUGGESTIONS


def test_suggestions_without_lexical_hits_are_summarised() -> None:
    """The Chinese-query case the whole capability exists for."""

    payload = {
        "results": [],
        "semantic_status": "ok",
        "semantic_suggestions": _SUGGESTIONS,
    }
    output, summary = _step_end("search_skills", "skill", payload, True)
    assert output["skills"] == []
    assert output["semantic_suggestions"] == _SUGGESTIONS
    assert summary == "1 suggestion"


def test_unavailable_status_still_shows_the_lexical_hits() -> None:
    payload = {
        "results": _LEXICAL,
        "semantic_status": "unavailable",
        "semantic_suggestions": [],
    }
    output, summary = _step_end("search_skills", "skill", payload, True)
    assert output["skills"] == ["single-cell-rna-analysis", "figure-composer"]
    assert summary == "single-cell-rna-analysis, figure-composer"
    assert output["semantic_status"] == "unavailable"
