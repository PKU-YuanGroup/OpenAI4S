"""Offline contracts for every schema-normalized scientific connector."""

from __future__ import annotations

import json
from pathlib import Path
import urllib.parse

import pytest

from openai4s.host.science import ScienceConnectorError, ScienceConnectorService

pytestmark = pytest.mark.stubbed_backend

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
    "zenodo.org": json.loads(
        (Path(__file__).parent / "fixtures/zenodo/record-6275421.json").read_text(
            encoding="utf-8"
        )
    ),
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
    "string-db.org": [
        {
            "stringId_A": "9606.ENSP00000269305",
            "stringId_B": "9606.ENSP00000266970",
            "preferredName_A": "TP53",
            "preferredName_B": "MDM2",
            "ncbiTaxonId": 9606,
            "score": 0.994,
            "nscore": 0.0,
            "fscore": 0.0,
            "pscore": 0.0,
            "ascore": 0.098,
            "escore": 0.991,
            "dscore": 0.0,
            "tscore": 0.985,
        }
    ],
    "www.bindingdb.org": {
        "getLindsByUniprotsResponse": {
            "affinities": [
                {
                    "query": "Cyclin-dependent kinase 4",
                    "monomerid": "81430",
                    "smile": "CNc1nc(C)c(s1)-c1ccnc(Nc2cccc(c2)S(N)(=O)=O)n1",
                    "affinity_type": "Ki",
                    "affinity": "9.1",
                    "pmid": "21035734",
                    "doi": "10.1016/j.chembiol.2010.07.016",
                }
            ]
        }
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
        ("zenodo", "hyperspectral", {}, "6275421", "dataset"),
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
        (
            "string",
            "TP53",
            {"species": "homo_sapiens", "required_score": 400},
            "9606.ENSP00000266970--9606.ENSP00000269305",
            "interaction",
        ),
        (
            "bindingdb",
            "P11802",
            {"cutoff": 100},
            "81430",
            "bioactivity",
        ),
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
        (
            {
                "database": "pubchem",
                "query": "aspirin",
                "filters": {"year_from": 2024},
            },
            "PubChem does not support filter.*year_from",
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


def test_string_interaction_scoring_and_attributes():
    fetch = FakeFetch()
    service = ScienceConnectorService(fetch)

    result = service.search("string", "TP53", limit=5)

    assert result["database"] == "string"
    assert result["count"] == 1
    record = result["results"][0]
    assert record["id"] == "9606.ENSP00000266970--9606.ENSP00000269305"
    assert record["type"] == "interaction"
    assert "score: 0.994" in record["title"]
    assert record["url"] == "https://string-db.org/network/9606.ENSP00000269305"
    attrs = record["attributes"]
    assert attrs["source_protein"] == "TP53"
    assert attrs["partner_protein"] == "MDM2"
    assert attrs["partner_string_id"] == "9606.ENSP00000266970"
    assert attrs["source_string_id"] == "9606.ENSP00000269305"
    assert attrs["score"] == 0.994
    assert attrs["experimental_score"] == 0.991
    assert attrs["database_score"] is None or attrs["database_score"] == 0
    assert attrs["textmining_score"] == 0.985
    assert attrs["coexpression_score"] == 0.098
    assert attrs["phylogenetic_score"] == 0
    assert attrs["network_type"] == "functional"
    assert attrs["taxon_id"] == 9606


def test_string_species_slug_mapping_and_filters():
    fetch = FakeFetch()
    service = ScienceConnectorService(fetch)

    # Test common organism slug mapping
    service.search("string", "Trp53", filters={"species": "mus_musculus"})
    url_mouse = fetch.calls[-1][0]
    assert "species=10090" in url_mouse

    # Test direct NCBI taxon id
    service.search("string", "TP53", filters={"species": "9606", "required_score": 700})
    url_human = fetch.calls[-1][0]
    assert "species=9606" in url_human
    assert "required_score=700" in url_human

    # Test invalid species
    with pytest.raises(ScienceConnectorError, match="not a valid NCBI taxonomy id"):
        service.search("string", "TP53", filters={"species": "invalid_alien_species"})

    # Test invalid required_score
    with pytest.raises(
        ScienceConnectorError, match="required_score must be between 0 and 1000"
    ):
        service.search("string", "TP53", filters={"required_score": 2000})


def test_string_empty_result_and_schema_error():
    service_empty = ScienceConnectorService(lambda *_args: "[]")
    res = service_empty.search("string", "nonexistent_protein")
    assert res["count"] == 0
    assert res["results"] == []

    service_malformed = ScienceConnectorService(
        lambda *_args: json.dumps({"error": "unknown"})
    )
    with pytest.raises(
        ScienceConnectorError, match="STRING returned an unexpected result schema"
    ):
        service_malformed.search("string", "TP53")


@pytest.mark.parametrize("separator", ["\n", "\r", "\r\n"])
def test_string_preserves_multiple_identifiers_in_request_and_provenance(separator):
    fetch = FakeFetch()
    result = ScienceConnectorService(fetch).search(
        "string", f" TP53 {separator} CDK2 {separator}", limit=2
    )
    params = urllib.parse.parse_qs(urllib.parse.urlsplit(fetch.calls[0][0]).query)
    assert params["identifiers"] == ["TP53\nCDK2"]
    assert result["query"] == result["provenance"]["query"] == "TP53\nCDK2"
    assert params["limit"] == ["2"]


def test_string_interaction_ids_use_both_endpoints_and_survive_label_changes():
    first = RESPONSES["string-db.org"][0]
    another_source = {**first, "stringId_A": "9606.ENSP00000350283"}
    renamed = {**first, "preferredName_A": "P53", "preferredName_B": "HDM2"}
    reversed_edge = {
        **first,
        "stringId_A": first["stringId_B"],
        "stringId_B": first["stringId_A"],
        "preferredName_A": first["preferredName_B"],
        "preferredName_B": first["preferredName_A"],
    }

    def record(row):
        service = ScienceConnectorService(lambda *_args: json.dumps([row]))
        return service.search("string", "TP53")["results"][0]

    original = record(first)
    assert original["id"] != record(another_source)["id"]
    assert original["id"] == record(renamed)["id"] == record(reversed_edge)["id"]


@pytest.mark.parametrize("field", ["stringId_A", "stringId_B"])
def test_string_drops_rows_without_both_interaction_endpoints(field):
    row = {**RESPONSES["string-db.org"][0], field: None}
    service = ScienceConnectorService(lambda *_args: json.dumps([row]))
    assert service.search("string", "TP53")["results"] == []


@pytest.mark.parametrize(
    "score", [True, False, 0.9, -0.1, 1000.1, float("inf"), "nan", "700.5"]
)
def test_string_rejects_noninteger_threshold_before_fetch(score):
    fetch = FakeFetch()
    with pytest.raises(
        ScienceConnectorError, match="required_score must be an integer"
    ):
        ScienceConnectorService(fetch).search(
            "string", "TP53", filters={"required_score": score}
        )
    assert fetch.calls == []


@pytest.mark.parametrize("threshold", [0, 1000, "700"])
def test_string_keeps_threshold_bounds_on_the_upstream_scale(threshold):
    fetch = FakeFetch()
    result = ScienceConnectorService(fetch).search(
        "string", "TP53", filters={"required_score": threshold}
    )
    params = urllib.parse.parse_qs(urllib.parse.urlsplit(fetch.calls[0][0]).query)
    assert params["required_score"] == [str(threshold)]
    assert result["provenance"]["filters"]["required_score"] == int(threshold)


@pytest.mark.parametrize("score", [0, 1])
def test_string_preserves_integer_confidence_scores(score):
    row = {**RESPONSES["string-db.org"][0], "score": score, "pscore": 0.5}
    result = ScienceConnectorService(lambda *_args: json.dumps([row])).search(
        "string", "TP53"
    )
    record = result["results"][0]
    assert f"score: {score:.3f}" in record["title"]
    assert record["attributes"]["score"] == score
    assert record["attributes"]["phylogenetic_score"] == 0.5


@pytest.mark.parametrize("raw", ["", " ", "null", "{}", "not-json"])
def test_string_does_not_treat_malformed_response_as_no_matches(raw):
    with pytest.raises(ScienceConnectorError):
        ScienceConnectorService(lambda *_args: raw).search("string", "TP53")


def test_string_physical_network_selection_is_explicit():
    fetch = FakeFetch()
    result = ScienceConnectorService(fetch).search(
        "string", "TP53", filters={"network_type": "physical"}
    )
    params = urllib.parse.parse_qs(urllib.parse.urlsplit(fetch.calls[0][0]).query)
    assert params["network_type"] == ["physical"]
    assert result["results"][0]["attributes"]["network_type"] == "physical"
    assert result["results"][0]["url"].endswith("?network_type=physical")
    assert result["provenance"]["filters"]["network_type"] == "physical"
    with pytest.raises(
        ScienceConnectorError, match="network_type must be functional or physical"
    ):
        ScienceConnectorService(fetch).search(
            "string", "TP53", filters={"network_type": "other"}
        )
    assert len(fetch.calls) == 1


@pytest.mark.parametrize("species", [0, "0", -1, "９６０６", True, "alien"])
def test_string_rejects_invalid_taxon_ids_before_fetch(species):
    fetch = FakeFetch()
    with pytest.raises(ScienceConnectorError, match="not a valid NCBI taxonomy id"):
        ScienceConnectorService(fetch).search(
            "string", "TP53", filters={"species": species}
        )
    assert fetch.calls == []


def test_string_caps_total_records_across_multiple_inputs():
    rows = [
        {**RESPONSES["string-db.org"][0], "stringId_B": f"9606.partner{index}"}
        for index in range(4)
    ]
    service = ScienceConnectorService(lambda *_args: json.dumps(rows))
    result = service.search("string", "TP53\nCDK2", limit=2)
    assert result["count"] == len(result["results"]) == 2
    assert result["next_cursor"] is None


@pytest.mark.parametrize(
    "field",
    ["score", "nscore", "fscore", "pscore", "ascore", "escore", "dscore", "tscore"],
)
@pytest.mark.parametrize(
    "value", ["NaN", "Infinity", "-Infinity", -0.1, 1.5, True, False, "invalid", []]
)
def test_string_rejects_invalid_upstream_confidence_scores(field, value):
    # Valid JSON can still encode non-finite scores as strings. Converting
    # those to float would emit invalid JSON and unusable scientific evidence.
    row = {**RESPONSES["string-db.org"][0], field: value}
    body = json.dumps([row], allow_nan=False)
    service = ScienceConnectorService(lambda *_args: body)
    with pytest.raises(ScienceConnectorError, match=rf"STRING.*{field}.*0.*1"):
        service.search("string", "TP53")


@pytest.mark.parametrize("value", [0, 1, 0.994, "0.5"])
def test_string_valid_scores_remain_numeric_and_json_serializable(value):
    row = {**RESPONSES["string-db.org"][0], "score": value, "escore": value}
    service = ScienceConnectorService(lambda *_args: json.dumps([row]))
    result = service.search("string", "TP53")
    attrs = result["results"][0]["attributes"]
    assert attrs["score"] == attrs["experimental_score"] == float(value)
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("value", [None, ""])
def test_string_absent_optional_scores_are_not_invented(value):
    row = {**RESPONSES["string-db.org"][0], "escore": value}
    del row["pscore"]
    service = ScienceConnectorService(lambda *_args: json.dumps([row]))
    attrs = service.search("string", "TP53")["results"][0]["attributes"]
    assert "experimental_score" not in attrs
    assert "phylogenetic_score" not in attrs
    assert attrs["score"] == 0.994


def test_bindingdb_bioactivity_parsing_and_attributes():
    fetch = FakeFetch()
    service = ScienceConnectorService(fetch)

    result = service.search("bindingdb", "P11802", limit=5)

    assert result["database"] == "bindingdb"
    assert result["count"] == 1
    record = result["results"][0]
    assert record["id"] == "81430"
    assert record["type"] == "bioactivity"
    assert "Ki: 9.1 nM" in record["title"]
    assert "Cyclin-dependent kinase 4" in record["title"]
    assert (
        record["url"]
        == "https://www.bindingdb.org/bind/chemsearch/marvin/MolStructure.jsp?monomerid=81430"
    )
    attrs = record["attributes"]
    assert attrs["monomer_id"] == "81430"
    assert attrs["target_name"] == "Cyclin-dependent kinase 4"
    assert attrs["affinity_type"] == "Ki"
    assert attrs["affinity_value"] == 9.1
    assert attrs["affinity_raw"] == "9.1"
    assert attrs["smiles"] == "CNc1nc(C)c(s1)-c1ccnc(Nc2cccc(c2)S(N)(=O)=O)n1"
    assert attrs["pmid"] == "21035734"
    assert attrs["doi"] == "10.1016/j.chembiol.2010.07.016"


def test_bindingdb_pdb_query_routing_and_affinity_filtering():
    pdb_response = {
        "getLindsByPDBsResponse": {
            "affinities": [
                {
                    "query": "Kit",
                    "monomerid": "1001",
                    "smile": "CCC",
                    "affinity_type": "IC50",
                    "affinity": "5.5",
                },
                {
                    "query": "Kit",
                    "monomerid": "1002",
                    "smile": "CCCl",
                    "affinity_type": "Ki",
                    "affinity": "12.0",
                },
            ]
        }
    }
    calls = []

    def fake_fetch(url, *args):
        calls.append(url)
        return json.dumps(pdb_response)

    service = ScienceConnectorService(fake_fetch)

    res = service.search(
        "bindingdb", "1T46", filters={"cutoff": 50, "affinity_type": "IC50"}
    )
    assert "getLigandsByPDBs" in calls[0]
    assert "pdb=1T46" in calls[0]
    assert "cutoff=50" in calls[0]
    assert res["count"] == 1
    assert res["results"][0]["id"] == "1001"
    assert res["results"][0]["attributes"]["affinity_type"] == "IC50"


def test_bindingdb_empty_result_and_schema_validation():
    service_empty = ScienceConnectorService(
        lambda *_args: json.dumps({"getLindsByUniprotsResponse": {"affinities": []}})
    )
    res = service_empty.search("bindingdb", "P99999")
    assert res["count"] == 0
    assert res["results"] == []

    service_bad1 = ScienceConnectorService(lambda *_args: "[]")
    with pytest.raises(
        ScienceConnectorError, match="BindingDB returned an unexpected result schema"
    ):
        service_bad1.search("bindingdb", "P11802")

    fetch = FakeFetch()
    service = ScienceConnectorService(fetch)
    with pytest.raises(
        ScienceConnectorError, match="cutoff must be a positive number in nM"
    ):
        service.search("bindingdb", "P11802", filters={"cutoff": -10})

    with pytest.raises(ScienceConnectorError, match="affinity_type must be one of"):
        service.search("bindingdb", "P11802", filters={"affinity_type": "UNKNOWN"})


@pytest.mark.parametrize(
    "cutoff", [True, False, 0, -1, float("inf"), float("nan"), "inf", "1e999", [], {}]
)
def test_bindingdb_invalid_cutoff_is_rejected_before_fetch(cutoff):
    fetch = FakeFetch()
    with pytest.raises(ScienceConnectorError, match="cutoff"):
        ScienceConnectorService(fetch).search(
            "bindingdb", "P11802", filters={"cutoff": cutoff}
        )
    assert fetch.calls == []


@pytest.mark.parametrize("body", ["", " \n\t"])
def test_bindingdb_documented_empty_body_keeps_provenance(body):
    result = ScienceConnectorService(lambda *_args: body).search("bindingdb", "P11802")
    assert result["results"] == []
    assert result["count"] == 0
    assert result["provenance"]["response_sha256"]
    assert len(result["provenance"]["responses"]) == 1


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"getLindsByUniprotsResponse": {}},
        {"getLindsByUniprotsResponse": {"error": "upstream failed"}},
        {"getLindsByUniprotsResponse": {"affinities": None}},
        {"getLindsByUniprotsResponse": {"affinities": "unavailable"}},
        {"getLindsByUniprotsResponse": {"affinities": ["malformed record"]}},
        {"getLindsByPDBsResponse": {"affinities": []}},
    ],
)
def test_bindingdb_malformed_responses_are_not_empty_successes(payload):
    service = ScienceConnectorService(lambda *_args: json.dumps(payload))
    with pytest.raises(ScienceConnectorError, match="BindingDB.*schema"):
        service.search("bindingdb", "P11802")


def _bindingdb_result(**fields):
    row = dict(
        RESPONSES["www.bindingdb.org"]["getLindsByUniprotsResponse"]["affinities"][0]
    )
    row.update(fields)
    # BindingDB can also return a singleton object instead of an array.
    payload = {"getLindsByUniprotsResponse": {"affinities": row}}
    return ScienceConnectorService(lambda *_args: json.dumps(payload)).search(
        "bindingdb", "P11802"
    )


@pytest.mark.parametrize(
    "affinity",
    [True, False, "NaN", "Infinity", "-inf", "1e999", "-1", -1, "<NaN", "bad", [], {}],
)
def test_bindingdb_invalid_affinities_are_rejected(affinity):
    with pytest.raises(ScienceConnectorError, match="BindingDB.*affinity"):
        _bindingdb_result(affinity=affinity)


@pytest.mark.parametrize(
    "affinity, expected", [(0, 0), ("0", 0), (0.13, 0.13), ("1e-3", 0.001)]
)
def test_bindingdb_valid_affinities_are_finite_json_numbers(affinity, expected):
    result = _bindingdb_result(affinity=affinity)
    attrs = result["results"][0]["attributes"]
    assert attrs["affinity_value"] == expected
    assert attrs["affinity_raw"] == str(affinity)
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("affinity", ["<1", "> 10", "<=0.1", "~5", None, ""])
def test_bindingdb_qualified_or_absent_affinity_is_not_an_exact_measurement(affinity):
    result = _bindingdb_result(affinity=affinity)
    attrs = result["results"][0]["attributes"]
    assert "affinity_value" not in attrs
    if affinity:
        assert attrs["affinity_raw"] == affinity
    else:
        assert "affinity_raw" not in attrs
    json.dumps(result, allow_nan=False)


def test_bindingdb_keeps_complete_smiles():
    # A long peptide is structural data, not prose that may be clipped at 500.
    smiles = "N" + "CC(=O)N" * 80 + "CC(=O)O"
    assert len(smiles) > 500
    assert (
        _bindingdb_result(smile=smiles)["results"][0]["attributes"]["smiles"] == smiles
    )


def test_bindingdb_filters_before_applying_result_limit():
    row = RESPONSES["www.bindingdb.org"]["getLindsByUniprotsResponse"]["affinities"][0]
    payload = {
        "getLindsByUniprotsResponse": {
            "affinities": [dict(row, affinity_type="IC50"), row]
        }
    }
    calls = []

    def fetch(url, *_args):
        calls.append(url)
        return json.dumps(payload)

    result = ScienceConnectorService(fetch).search(
        "bindingdb",
        "P11802",
        limit=1,
        filters={"affinity_type": "Ki", "cutoff": 0.0001},
    )
    assert result["count"] == 1
    assert result["results"][0]["attributes"]["affinity_type"] == "Ki"
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(calls[0]).query)["cutoff"] == [
        "0.0001"
    ]
