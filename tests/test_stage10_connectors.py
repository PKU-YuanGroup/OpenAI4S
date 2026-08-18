"""Stage 10 ClinVar / PubMed / ClinicalTrials connectors."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openai4s.config import Config, RoadmapFeatureFlags
from openai4s.host.science import ScienceConnectorError, ScienceConnectorService
from openai4s.host.stage10_science import (
    official_stage10_enabled,
    record_search_artifact,
)
from openai4s.store import get_store
from openai4s.tools.science import ScienceListDatabasesTool, ScienceSearchTool

CLINVAR_SEARCH = {
    "esearchresult": {"count": "1", "idlist": ["424712"]},
}
CLINVAR_SUMMARY = {
    "result": {
        "uids": ["424712"],
        "424712": {
            "uid": "424712",
            "accession": "VCV000012345",
            "title": "NM_000059.4(BRCA2):c.5946del",
            "clinical_significance": "Pathogenic",
            "review_status": "reviewed by expert panel",
            "gene_sort": "BRCA2",
        },
    }
}
PUBMED_SEARCH = {"esearchresult": {"count": "1", "idlist": ["20301425"]}}
PUBMED_SUMMARY = {
    "result": {
        "uids": ["20301425"],
        "20301425": {
            "uid": "20301425",
            "title": "BRCA1- and BRCA2-Associated Hereditary Breast and Ovarian Cancer",
            "fulljournalname": "GeneReviews",
            "pubdate": "1998 Sep 4",
            "elocationid": "doi: 10.0000/example",
        },
    }
}
TRIALS = {
    "studies": [
        {
            "protocolSection": {
                "identificationModule": {
                    "nctId": "NCT00001379",
                    "briefTitle": "A study of BRCA-related cancer",
                    "organization": {"fullName": "NCI"},
                },
                "statusModule": {"overallStatus": "COMPLETED"},
            }
        }
    ],
    "nextPageToken": "",
}


def _fetch(url, fmt, timeout, max_chars):
    if "esearch" in url and "clinvar" in url:
        body = CLINVAR_SEARCH
    elif "esummary" in url and "clinvar" in url:
        body = CLINVAR_SUMMARY
    elif "esearch" in url and "pubmed" in url:
        body = PUBMED_SEARCH
    elif "esummary" in url and "pubmed" in url:
        body = PUBMED_SUMMARY
    elif "clinicaltrials.gov" in url:
        body = TRIALS
    else:
        raise AssertionError(url)
    return json.dumps(body)


def test_flag_keeps_new_sources_out_of_the_default_catalog():
    off = ScienceConnectorService()
    ids = {item["id"] for item in off.list_databases()["databases"]}
    assert "clinvar" not in ids
    on = ScienceConnectorService(stage10=True)
    ids = {item["id"] for item in on.list_databases()["databases"]}
    assert {"clinvar", "pubmed", "clinicaltrials"} <= ids
    assert official_stage10_enabled(Config()) is False


def test_clinvar_search_records_accession_url_time_and_artifact(tmp_path):
    service = ScienceConnectorService(fetch=_fetch, stage10=True)
    result = service.search("clinvar", "VCV000012345", limit=1)
    assert result["count"] == 1
    row = result["results"][0]
    assert row["id"] == "VCV000012345"
    assert "ncbi.nlm.nih.gov/clinvar" in row["url"]
    assert result["provenance"]["retrieved_at"]
    assert result["request_url"]
    store = get_store(tmp_path / "db.sqlite")
    root = store.new_frame(kind="turn", project_id="default", status="ready")
    artifact = record_search_artifact(
        store, tmp_path / "ws", result, root_frame_id=root
    )
    meta = store.version_meta(artifact["version_id"])
    source = meta.get("source") or {}
    if isinstance(source, str):
        source = json.loads(source)
    assert source["query"] == "VCV000012345"
    assert "eutils.ncbi.nlm.nih.gov" in source["endpoint"]
    assert source["retrieved_at"]
    assert "VCV000012345" in source["accessions"]
    store.close()


def test_empty_429_and_schema_drift_are_honest():
    def empty(url, fmt, timeout, max_chars):
        if "esearch" in url:
            return json.dumps({"esearchresult": {"count": "0", "idlist": []}})
        raise AssertionError(url)

    empty_result = ScienceConnectorService(fetch=empty, stage10=True).search(
        "pubmed", "no-such-term-xyz", limit=1
    )
    assert empty_result["count"] == 0
    assert empty_result["results"] == []

    def drifted(url, fmt, timeout, max_chars):
        return json.dumps({"unexpected": True})

    with pytest.raises(ScienceConnectorError, match="unexpected"):
        ScienceConnectorService(fetch=drifted, stage10=True).search(
            "clinvar", "BRCA1", limit=1
        )

    def limited(url, fmt, timeout, max_chars):
        raise RuntimeError("HTTP Error 429: Too Many Requests")

    with pytest.raises(ScienceConnectorError, match="429"):
        ScienceConnectorService(fetch=limited, stage10=True).search(
            "clinicaltrials", "melanoma", limit=1
        )


def test_clinicaltrials_and_pubmed_normalize(tmp_path):
    service = ScienceConnectorService(fetch=_fetch, stage10=True)
    papers = service.search("pubmed", "BRCA2", limit=1)
    assert papers["results"][0]["id"] == "20301425"
    assert papers["next_cursor"] is None
    trials = service.search("clinicaltrials", "BRCA", limit=1)
    assert trials["results"][0]["id"] == "NCT00001379"
    assert "clinicaltrials.gov/study/NCT00001379" in trials["results"][0]["url"]


def test_tool_catalog_hides_stage10_until_the_flag_is_on():
    result = ScienceListDatabasesTool().execute(None, {"domain": "all"})
    assert "clinvar" not in {item["id"] for item in result["databases"]}
    runtime = SimpleNamespace(
        cfg=Config(
            roadmap_features=RoadmapFeatureFlags(stage10_scientific_connectors=True)
        )
    )
    enabled = ScienceListDatabasesTool().execute(runtime, {"domain": "all"})
    assert "clinvar" in {item["id"] for item in enabled["databases"]}
