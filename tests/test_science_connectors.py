"""Offline contracts for every schema-normalized scientific connector."""

from __future__ import annotations

import json
import urllib.parse

import pytest

from openai4s.host.science import ScienceConnectorError, ScienceConnectorService

ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>https://arxiv.org/abs/2401.12345v2</id>
    <updated>2024-02-02T00:00:00Z</updated>
    <published>2024-01-20T00:00:00Z</published>
    <title>Structured scientific agents</title>
    <summary>A reproducible agent study.</summary>
    <author><name>Ada Researcher</name></author>
    <category term="cs.AI" />
    <arxiv:doi>10.1234/example</arxiv:doi>
  </entry>
</feed>
"""


RESPONSES = {
    "rest.uniprot.org": {
        "results": [
            {
                "primaryAccession": "P01308",
                "uniProtkbId": "INS_HUMAN",
                "entryType": "UniProtKB reviewed (Swiss-Prot)",
                "proteinDescription": {
                    "recommendedName": {"fullName": {"value": "Insulin"}}
                },
                "genes": [{"geneName": {"value": "INS"}}],
                "organism": {"scientificName": "Homo sapiens", "taxonId": 9606},
                "sequence": {"length": 110},
            }
        ]
    },
    "search.rcsb.org": {"result_set": [{"identifier": "4INS", "score": 0.97}]},
    "rest.ensembl.org": [
        {
            "id": "ENSG00000254647",
            "type": "gene",
        }
    ],
    "www.ebi.ac.uk": {
        "molecules": [
            {
                "molecule_chembl_id": "CHEMBL25",
                "pref_name": "ASPIRIN",
                "molecule_type": "Small molecule",
                "max_phase": 4,
                "molecule_properties": {
                    "full_molformula": "C9H8O4",
                    "full_mwt": "180.16",
                    "alogp": "1.31",
                },
                "molecule_structures": {"canonical_smiles": "CC(=O)OC1=CC=CC=C1C(=O)O"},
            }
        ]
    },
    "pubchem.ncbi.nlm.nih.gov": {
        "PropertyTable": {
            "Properties": [
                {
                    "CID": 2244,
                    "Title": "Aspirin",
                    "MolecularFormula": "C9H8O4",
                    "MolecularWeight": "180.16",
                    "ConnectivitySMILES": "CC(=O)OC1=CC=CC=C1C(=O)O",
                    "InChIKey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
                    "XLogP": 1.2,
                    "HBondDonorCount": 1,
                    "HBondAcceptorCount": 4,
                }
            ]
        }
    },
    "api.openalex.org": {
        "meta": {"next_cursor": "next-page-token"},
        "results": [
            {
                "id": "https://openalex.org/W123",
                "display_name": "Scientific foundation models",
                "doi": "https://doi.org/10.1234/foundation",
                "publication_year": 2025,
                "type": "article",
                "authorships": [{"author": {"display_name": "Grace Scientist"}}],
                "concepts": [{"display_name": "Machine learning"}],
                "cited_by_count": 12,
                "open_access": {"is_oa": True},
                "language": "en",
            }
        ],
    },
}


class FakeFetch:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float, int]] = []

    def __call__(self, url: str, fmt: str, timeout: float, max_chars: int) -> str:
        self.calls.append((url, fmt, timeout, max_chars))
        host = urllib.parse.urlsplit(url).hostname
        if host == "export.arxiv.org":
            return ARXIV_XML
        return json.dumps(RESPONSES[host])


@pytest.mark.parametrize(
    ("database", "query", "filters", "expected_id", "expected_type"),
    [
        ("uniprot", "insulin", {"organism_id": "9606"}, "P01308", "protein"),
        ("pdb", "insulin", {}, "4INS", "structure"),
        (
            "ensembl",
            "INS",
            {"species": "homo_sapiens"},
            "ENSG00000254647",
            "genomic_feature",
        ),
        ("chembl", "aspirin", {}, "CHEMBL25", "molecule"),
        ("pubchem", "aspirin", {}, "2244", "compound"),
        ("arxiv", "scientific agents", {}, "2401.12345v2", "preprint"),
        ("openalex", "foundation models", {"year_from": 2024}, "W123", "work"),
    ],
)
def test_each_connector_returns_the_common_record_schema(
    database, query, filters, expected_id, expected_type
):
    fetch = FakeFetch()
    service = ScienceConnectorService(fetch)

    result = service.search(database, query, limit=5, filters=filters)

    assert result["database"] == database
    assert result["count"] == 1
    assert result["results"][0]["id"] == expected_id
    assert result["results"][0]["type"] == expected_type
    assert set(result["results"][0]) == {"id", "title", "url", "type", "attributes"}
    assert result["request_url"].startswith("https://")
    assert len(fetch.calls) == 1
    assert fetch.calls[0][2:] == (30.0, 5_000_000)


def test_database_catalog_spans_roadmap_disciplines():
    service = ScienceConnectorService(FakeFetch())

    assert service.list_databases("ml")["databases"]
    assert service.list_databases("physics")["databases"]
    assert {item["id"] for item in service.list_databases("biology")["databases"]} >= {
        "uniprot",
        "pdb",
        "ensembl",
    }
    assert {
        item["id"] for item in service.list_databases("chemistry")["databases"]
    } >= {
        "chembl",
        "pubchem",
    }
    with pytest.raises(ScienceConnectorError, match="unknown science domain"):
        service.list_databases("astrology")


def test_openalex_filters_and_cursor_are_encoded_without_arbitrary_urls():
    fetch = FakeFetch()
    service = ScienceConnectorService(fetch)

    result = service.search(
        "openalex",
        "genome model",
        cursor="opaque-token",
        filters={"year_from": 2020, "year_to": 2025, "work_type": "article"},
    )

    query = urllib.parse.parse_qs(urllib.parse.urlsplit(result["request_url"]).query)
    assert query["cursor"] == ["opaque-token"]
    assert query["filter"] == [
        "from_publication_date:2020-01-01,to_publication_date:2025-12-31,type:article"
    ]
    assert result["next_cursor"] == "next-page-token"


def test_pdb_request_uses_the_current_v2_paginate_shape():
    service = ScienceConnectorService(FakeFetch())

    result = service.search("pdb", "insulin", limit=7)

    encoded = urllib.parse.parse_qs(urllib.parse.urlsplit(result["request_url"]).query)[
        "json"
    ][0]
    request = json.loads(encoded)
    assert request["request_options"] == {"paginate": {"start": 0, "rows": 7}}


def test_pdb_no_hits_204_body_is_an_empty_result_not_invalid_json():
    service = ScienceConnectorService(lambda *_args: "")

    result = service.search("pdb", "a query with no matching structures")

    assert result["results"] == []
    assert result["count"] == 0


def test_ensembl_uses_symbol_endpoint_and_only_returns_stable_ids():
    service = ScienceConnectorService(FakeFetch())

    result = service.search("ensembl", "INS")

    assert "/xrefs/symbol/homo_sapiens/INS" in result["request_url"]
    assert result["results"][0]["id"].startswith("ENSG")
    assert result["results"][0]["attributes"]["feature_type"] == "gene"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"database": "unknown", "query": "x"}, "unknown scientific database"),
        ({"database": "uniprot", "query": ""}, "must not be empty"),
        ({"database": "uniprot", "query": "x", "limit": 0}, "between 1 and 50"),
        (
            {
                "database": "openalex",
                "query": "x",
                "filters": {"year_from": 2025, "year_to": 2020},
            },
            "must not exceed",
        ),
        (
            {
                "database": "uniprot",
                "query": "x",
                "filters": {"url": "https://example.org"},
            },
            "unknown science filters",
        ),
    ],
)
def test_invalid_searches_fail_before_network(kwargs, message):
    fetch = FakeFetch()
    service = ScienceConnectorService(fetch)

    with pytest.raises(ScienceConnectorError, match=message):
        service.search(**kwargs)

    assert fetch.calls == []


def test_invalid_upstream_schema_is_a_bounded_connector_error():
    service = ScienceConnectorService(lambda *_args: json.dumps({"unexpected": []}))

    with pytest.raises(ScienceConnectorError, match="UniProt.*unexpected"):
        service.search("uniprot", "insulin")
