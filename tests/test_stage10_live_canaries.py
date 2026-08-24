"""Live canaries for Stage 10 public science APIs.

These hit production ClinVar, PubMed, and ClinicalTrials.gov without
credentials. They are opted in with ``network``/``external`` and are not part
of the default offline suite.
"""

from __future__ import annotations

import time

import pytest

from openai4s.host.science import ScienceConnectorError, ScienceConnectorService

pytestmark = [pytest.mark.network, pytest.mark.external]


def _search(database: str, query: str):
    last = None
    for attempt in range(3):
        try:
            return ScienceConnectorService(stage10=True).search(
                database, query, limit=1, timeout=20
            )
        except ScienceConnectorError as error:
            last = error
            if "429" not in str(error):
                raise
            time.sleep(1.5 * (attempt + 1))
    raise last


@pytest.mark.network
@pytest.mark.external
def test_live_clinvar_variant_query_returns_accession_and_url():
    result = _search("clinvar", "VCV000000012")
    assert result["count"] >= 1
    row = result["results"][0]
    assert row["id"]
    assert row["url"].startswith("https://www.ncbi.nlm.nih.gov/clinvar/")
    assert result["request_url"].startswith("https://eutils.ncbi.nlm.nih.gov/")
    assert result["provenance"]["retrieved_at"]


@pytest.mark.network
@pytest.mark.external
def test_live_pubmed_and_clinicaltrials_return_public_records():
    time.sleep(1.2)
    papers = _search("pubmed", "BRCA2[Title]")
    assert papers["count"] >= 1
    assert papers["results"][0]["id"].isdigit()
    trials = _search("clinicaltrials", "NCT00001379")
    assert trials["count"] >= 1
    assert trials["results"][0]["id"].startswith("NCT")
