"""Control-plane and in-kernel surfaces for scientific database search."""

from __future__ import annotations

import json

import pytest

from openai4s.sdk.host import _Host
from openai4s.tools.catalog import SessionToolCatalog
from openai4s.tools.registry import get_tool
from openai4s.tools.science import ScienceListDatabasesTool, ScienceSearchTool


def test_science_tools_are_two_flat_schema_checked_registry_entries():
    catalog = ScienceListDatabasesTool()
    search = ScienceSearchTool()

    assert get_tool("science_list_dbs") == catalog
    assert get_tool("science_search") == search
    assert catalog.requires_approval is False
    assert search.needs_network is True
    assert search.read_only is False
    assert search.writes_files is True
    assert search.derived_write_path is True
    assert search.side_effect_class == "workspace_write"
    assert search.screen_untrusted_output is True
    assert search.permission_target({"database": "uniprot"}) == "uniprot"
    assert search.resource_keys({"database": "uniprot"}) == ("network:science/uniprot",)
    assert search.validation_error(
        {"database": "uniprot", "query": "insulin", "filters": {"url": "x"}}
    )


_STAGE10_ONLY = {"pubmed", "clinicaltrials", "clinvar"}


def _offered_databases() -> list[str]:
    from openai4s.tools.native import control_tool_specs

    (spec,) = control_tool_specs([ScienceSearchTool()])
    return list(spec.input_schema["properties"]["database"]["enum"])


def test_the_model_is_offered_only_the_databases_this_daemon_searches(monkeypatch):
    """The schema named 13 databases while 10 were served by default.

    A model that picked pubmed got "unknown scientific database 'pubmed'".
    """

    monkeypatch.delenv("OPENAI4S_STAGE10_SCIENTIFIC_CONNECTORS", raising=False)
    served = ScienceListDatabasesTool().execute(None, {"domain": "all"})
    offered = _offered_databases()
    assert set(offered) == {item["id"] for item in served["databases"]}
    assert not _STAGE10_ONLY & set(offered)
    # The provider projection narrows; Host validation keeps the vocabulary.
    assert (
        ScienceSearchTool().validation_error({"database": "pubmed", "query": "x"})
        is None
    )
    assert set(ScienceSearchTool().parameters["properties"]["database"]["enum"]) >= (
        _STAGE10_ONLY
    )

    monkeypatch.setenv("OPENAI4S_STAGE10_SCIENTIFIC_CONNECTORS", "1")
    assert _STAGE10_ONLY <= set(_offered_databases())


def test_science_catalog_executes_without_network():
    result = ScienceListDatabasesTool().execute(None, {"domain": "chemistry"})

    assert {item["id"] for item in result["databases"]} >= {"chembl", "pubchem"}


def test_string_catalog_and_control_schema_expose_network_selection():
    catalog = ScienceListDatabasesTool().execute(None, {"domain": "biology"})
    string = next(item for item in catalog["databases"] if item["id"] == "string")
    assert set(string["filters"]) == {"species", "required_score", "network_type"}
    search = ScienceSearchTool()
    args = {
        "database": "string",
        "query": "TP53",
        "filters": {"network_type": "physical", "required_score": 700},
    }
    assert search.validation_error(args) is None
    args["filters"]["network_type"] = "unknown"
    assert search.validation_error(args)


def test_string_is_discoverable_and_activates_science_tools():
    catalog = SessionToolCatalog()
    result = catalog.search_capabilities("STRING")
    assert "science" in {group["id"] for group in result["matched_groups"]}
    for request in (
        "Search STRING database for TP53",
        "查询蛋白互作",
        "string-db TP53",
    ):
        specs = SessionToolCatalog().specs_for([{"role": "user", "content": request}])
        assert {"science_search", "science_list_dbs"} <= {spec.name for spec in specs}


def test_bindingdb_is_discoverable_and_activates_science_tools():
    result = SessionToolCatalog().search_capabilities("BindingDB")
    assert "science" in {group["id"] for group in result["matched_groups"]}
    for request in ("Search BindingDB for P11802", "查询药物靶点结合亲和力"):
        specs = SessionToolCatalog().specs_for([{"role": "user", "content": request}])
        assert {"science_search", "science_list_dbs"} <= {spec.name for spec in specs}


def test_bindingdb_catalog_and_control_schema_expose_affinity_filters():
    catalog = ScienceListDatabasesTool().execute(None, {"domain": "chemistry"})
    bindingdb = next(item for item in catalog["databases"] if item["id"] == "bindingdb")
    assert set(bindingdb["filters"]) == {"cutoff", "affinity_type"}
    search = ScienceSearchTool()
    args = {
        "database": "bindingdb",
        "query": "P11802",
        "filters": {"cutoff": 0.0001, "affinity_type": "Ki"},
    }
    assert search.validation_error(args) is None
    args["filters"]["affinity_type"] = "unknown"
    assert search.validation_error(args)


@pytest.mark.stubbed_backend
def test_bindingdb_invalid_affinity_returns_a_bounded_tool_error(monkeypatch):
    from openai4s import webtools

    row = {"monomerid": "81430", "affinity_type": "Ki", "affinity": "NaN"}
    body = json.dumps({"getLindsByUniprotsResponse": {"affinities": [row]}})
    monkeypatch.setattr(
        webtools, "web_fetch", lambda *_args, **_kwargs: {"content": body}
    )
    result = ScienceSearchTool().execute(
        None, {"database": "bindingdb", "query": "P11802"}
    )
    assert set(result) == {"error"}
    assert "affinity" in result["error"]
    json.dumps(result, allow_nan=False)


@pytest.mark.stubbed_backend
def test_string_invalid_score_returns_a_bounded_tool_error(monkeypatch):
    from openai4s import webtools

    row = {"stringId_A": "9606.query", "stringId_B": "9606.partner", "score": "NaN"}
    monkeypatch.setattr(
        webtools, "web_fetch", lambda *_args, **_kwargs: {"content": json.dumps([row])}
    )
    result = ScienceSearchTool().execute(None, {"database": "string", "query": "TP53"})
    assert set(result) == {"error"}
    assert "score" in result["error"]
    json.dumps(result, allow_nan=False)


def test_host_science_sdk_encodes_only_top_level_wire_fields():
    calls = []

    def host_call(method, args):
        calls.append((method, args))
        return {"ok": True}

    host = _Host(host_call)
    assert host.science.list_databases("biology") == {"ok": True}
    assert host.science.search(
        "openalex",
        "protein language model",
        limit=7,
        cursor="next",
        filters={"year_from": 2024},
    ) == {"ok": True}

    assert calls == [
        ("science_list_dbs", [{"domain": "biology"}]),
        (
            "science_search",
            [
                {
                    "database": "openalex",
                    "query": "protein language model",
                    "limit": 7,
                    "cursor": "next",
                    "filters": {"year_from": 2024},
                    "timeout": 30.0,
                }
            ],
        ),
    ]
