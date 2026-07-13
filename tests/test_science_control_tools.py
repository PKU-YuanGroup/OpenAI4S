"""Control-plane and in-kernel surfaces for scientific database search."""

from __future__ import annotations

from openai4s.sdk.host import _Host
from openai4s.tools.registry import get_tool
from openai4s.tools.science import ScienceListDatabasesTool, ScienceSearchTool


def test_science_tools_are_two_flat_schema_checked_registry_entries():
    catalog = ScienceListDatabasesTool()
    search = ScienceSearchTool()

    assert get_tool("science_list_dbs") == catalog
    assert get_tool("science_search") == search
    assert catalog.requires_approval is False
    assert search.needs_network is True
    assert search.screen_untrusted_output is True
    assert search.permission_target({"database": "uniprot"}) == "uniprot"
    assert search.resource_keys({"database": "uniprot"}) == ("network:science/uniprot",)
    assert search.validation_error(
        {"database": "uniprot", "query": "insulin", "filters": {"url": "x"}}
    )


def test_science_catalog_executes_without_network():
    result = ScienceListDatabasesTool().execute(None, {"domain": "chemistry"})

    assert {item["id"] for item in result["databases"]} >= {"chembl", "pubchem"}


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
