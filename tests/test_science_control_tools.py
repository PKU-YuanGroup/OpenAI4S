"""Control-plane and in-kernel surfaces for scientific database search."""

from __future__ import annotations

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
