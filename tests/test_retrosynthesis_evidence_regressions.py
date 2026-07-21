"""Regression tests for retrosynthesis evidence retrieval and normalization."""

from __future__ import annotations

import sys

import pytest

from openai4s.config import get_config

sys.path.insert(0, str(get_config().skills_dir))
from retrosynthesis_planning import kernel as retro  # noqa: E402


def _route(rank: int, product: str, *precursors: str) -> dict:
    return {
        "rank": rank,
        "tree": {
            "type": "mol",
            "smiles": product,
            "children": [
                {
                    "type": "reaction",
                    "template": "generic disconnection",
                    "metadata": {"classification": "Unclassified reaction"},
                    "children": [
                        {"type": "mol", "smiles": precursor, "in_stock": True}
                        for precursor in precursors
                    ],
                }
            ],
        },
    }


def test_unmapped_reaction_keys_include_product_and_precursor_context_with_alias():
    routes = [
        _route(1, "CCOC(=O)N", "CCO", "NC=O"),
        _route(2, "CCNC(=O)O", "CCN", "OC=O"),
    ]

    briefs = retro.collect_reaction_briefs(routes)

    assert len(briefs) == 2
    assert len({brief["reaction_key"] for brief in briefs}) == 2
    assert {brief["product_smiles"] for brief in briefs} == {
        "CCOC(=O)N",
        "CCNC(=O)O",
    }
    assert {tuple(brief["precursor_smiles"]) for brief in briefs} == {
        ("CCO", "NC=O"),
        ("CCN", "OC=O"),
    }
    public_brief = retro._public_reaction_brief(briefs[0])
    assert public_brief["product_smiles"] == briefs[0]["product_smiles"]
    assert public_brief["precursor_smiles"] == briefs[0]["precursor_smiles"]
    fallback_query = retro._fallback_reaction_evidence_query(briefs[0])
    assert briefs[0]["product_smiles"] in fallback_query
    assert ".".join(briefs[0]["precursor_smiles"]) in fallback_query

    _node, details = next(retro._iter_reaction_contexts(routes[0]["tree"]))
    legacy_key = retro._legacy_reaction_annotation_key(details)
    assert legacy_key != briefs[0]["reaction_key"]
    assert legacy_key in retro._reaction_annotation_keys(details)
    legacy_record = {"title": "legacy integration record"}
    assert retro._reaction_evidence_for_details(
        {legacy_key: [legacy_record]}, details
    ) == [legacy_record]


def test_evidence_booleans_are_explicit_and_candidates_cannot_be_verified():
    normalized = retro._normalize_evidence_record(
        {
            "verified": "false",
            "candidate": "true",
            "identifier_verified": "no",
        }
    )
    assert normalized["verified"] is False
    assert normalized["candidate"] is True
    assert normalized["identifier_verified"] is False

    conflicting = retro._normalize_evidence_record(
        {"verified": "true", "candidate": "true", "identifier_verified": "yes"}
    )
    assert conflicting["verified"] is False
    assert conflicting["candidate"] is True
    assert conflicting["identifier_verified"] is True

    reviewed = retro._normalize_evidence_record(
        {"verified": "true", "candidate": "false"}
    )
    assert reviewed["verified"] is True
    assert reviewed["candidate"] is False


def test_candidate_only_source_diversity_cannot_raise_coverage_above_30():
    records = [
        {
            "source_type": source_type,
            "match_level": "exact_substrate",
            "candidate": True,
        }
        for source_type in ("literature", "patent", "reaction_database", "vendor")
    ]

    summary = retro._reaction_evidence_summary(records)

    assert summary["coverage"] == 30
    assert summary["status"] == "Retrieved source candidates need review"


def test_explicit_empty_record_wrappers_do_not_become_pseudo_evidence():
    assert (
        retro.normalize_reaction_evidence(
            [{"reaction_key": "rxn:empty", "records": []}]
        )
        == {}
    )
    assert (
        retro.normalize_reaction_evidence(
            [{"reaction_key": "rxn:empty", "evidence": []}]
        )
        == {}
    )
    assert retro.normalize_reaction_evidence({"rxn:empty": {"records": []}}) == {}
    assert (
        retro.normalize_reaction_evidence(
            {"reactions": {"rxn:empty": {"evidence": []}}}
        )
        == {}
    )

    actual = {"title": "real evidence record"}
    assert retro.normalize_reaction_evidence({"rxn:one": {"records": [actual]}}) == {
        "rxn:one": [actual]
    }


def test_retrieval_isolates_search_and_fetch_failures_and_keeps_sources():
    searches = []
    fetches = []

    def search(query, **_kwargs):
        searches.append(query)
        if query == "broken query":
            raise RuntimeError("search backend timeout")
        return {
            "results": [
                {"title": "First source", "url": "https://example.test/first"},
                {"title": "Second source", "url": "https://example.test/second"},
            ]
        }

    def fetch(url, **_kwargs):
        fetches.append(url)
        if url.endswith("/first"):
            raise RuntimeError("publisher returned 403")
        return {"url": url, "content": "Fetched source text"}

    provider = retro.OpenAI4SLLMReactionEvidenceProvider(
        llm=lambda _request: "{}",
        search=search,
        fetch=fetch,
        max_queries_per_reaction=2,
        fetch_top_results=2,
    )
    brief = {"reaction_key": "rxn:one"}

    with pytest.warns(RuntimeWarning) as caught:
        sources = provider._retrieve_sources(
            [brief], {"rxn:one": ["broken query", "working query"]}
        )

    assert searches == ["broken query", "working query"]
    assert fetches == [
        "https://example.test/first",
        "https://example.test/second",
    ]
    assert [source["title"] for source in sources] == [
        "First source",
        "Second source",
    ]
    assert "excerpt" not in sources[0]
    assert sources[1]["excerpt"] == "Fetched source text"
    messages = [str(warning.message) for warning in caught]
    assert any("search failed" in message for message in messages)
    assert any("fetch failed" in message for message in messages)


def test_doi_identifier_ignores_excerpt_references_and_accepts_structured_inputs():
    source_url = "https://example.test/reaction"
    evidence = retro._source_linked_evidence_candidates(
        [
            {
                "reaction_key": "rxn:one",
                "source_id": "source-1",
                "match_level": "reaction_class",
            }
        ],
        [
            {
                "source_id": "source-1",
                "reaction_key": "rxn:one",
                "url": source_url,
                "title": "A reaction article",
                "excerpt": "References include unrelated DOI 10.9999/not-this-source.",
            }
        ],
    )
    assert evidence["rxn:one"][0]["identifier"] == source_url

    assert (
        retro._source_identifier({"url": source_url, "doi": "DOI: 10.1234/structured"})
        == "10.1234/structured"
    )
    assert (
        retro._source_identifier({"url": "https://doi.org/10.1234/encoded%28suffix%29"})
        == "10.1234/encoded(suffix)"
    )


def test_host_web_fetch_doi_verifier_uses_host_path_and_preserves_unknown():
    calls = []

    def web_fetch(url, **_kwargs):
        calls.append(url)
        if url.endswith("/10.1234/transient"):
            return {"error": "egress temporarily unavailable"}
        if url.endswith("/10.1234/raised"):
            raise RuntimeError("host RPC interrupted")
        return {"url": "https://publisher.test/article", "content": "resolved"}

    verifier = retro.build_host_web_fetch_doi_verifier(web_fetch)
    result = verifier(
        [
            "10.1234/ok(suffix)",
            "10.1234/transient",
            "10.1234/raised",
            "10.1234/a/../b",
            "10.1234/a%2F..%2Fb",
            "not-a-doi",
        ]
    )

    assert calls == [
        "https://doi.org/10.1234/ok%28suffix%29",
        "https://doi.org/10.1234/transient",
        "https://doi.org/10.1234/raised",
    ]
    assert result["10.1234/ok(suffix)"]["ok"] is True
    assert result["10.1234/transient"]["ok"] is None
    assert result["10.1234/raised"]["ok"] is None
    assert result["10.1234/a/../b"]["ok"] is False
    assert result["10.1234/a%2F..%2Fb"]["ok"] is False
    assert result["not-a-doi"]["ok"] is False
