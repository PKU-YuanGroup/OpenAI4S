"""Flat control-tool surface for schema-normalized scientific databases."""

from __future__ import annotations

from typing import Any

from openai4s.tools.base import Tool
from openai4s.tools.taxonomy import resource_key


class ScienceListDatabasesTool(Tool):
    name = "science_list_dbs"
    host_method = "science_list_dbs"
    description = (
        "List structured public scientific databases, query hints, and filters."
    )
    parameters = {
        "properties": {
            "domain": {
                "type": "string",
                "enum": ["all", "biology", "chemistry", "literature", "ml", "physics"],
                "description": "Optional discipline filter (default all).",
            }
        },
        "required": [],
    }
    requires_approval = False
    resource_key_prefix = "science"
    resource_target_default = "catalog"

    def execute(self, _runtime: Any, arguments: dict) -> dict:
        from openai4s.host.science import ScienceConnectorError, ScienceConnectorService

        try:
            return ScienceConnectorService().list_databases(
                str(arguments.get("domain") or "all")
            )
        except ScienceConnectorError as error:
            return {"error": str(error)}


class ScienceSearchTool(Tool):
    name = "science_search"
    host_method = "science_search"
    description = (
        "Search one supported scientific database and return normalized typed records."
    )
    parameters = {
        "properties": {
            "database": {
                "type": "string",
                "enum": [
                    "uniprot",
                    "pdb",
                    "ensembl",
                    "chembl",
                    "pubchem",
                    "arxiv",
                    "openalex",
                ],
            },
            "query": {"type": "string", "minLength": 1, "maxLength": 500},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            "cursor": {"type": "string", "maxLength": 2000},
            "filters": {
                "type": "object",
                "properties": {
                    "organism_id": {"type": "string", "maxLength": 30},
                    "species": {"type": "string", "maxLength": 80},
                    "year_from": {"type": "integer", "minimum": 1000, "maximum": 3000},
                    "year_to": {"type": "integer", "minimum": 1000, "maximum": 3000},
                    "work_type": {"type": "string", "maxLength": 50},
                },
                "additionalProperties": False,
            },
            "timeout": {"type": "number", "minimum": 1, "maximum": 120},
        },
        "required": ["database", "query"],
    }
    needs_network = True
    screen_untrusted_output = True
    output_limit = 100_000
    permission_target_key = "database"
    resource_key_prefix = "science"
    resource_target_key = "database"

    def resource_keys(self, arguments: Any) -> tuple[str, ...]:
        database = arguments.get("database") if isinstance(arguments, dict) else "*"
        return (resource_key("network", f"science/{database or '*'}"),)

    def execute(self, _runtime: Any, arguments: dict) -> dict:
        from openai4s import egress, webtools
        from openai4s.host.science import ScienceConnectorError, ScienceConnectorService

        try:
            return ScienceConnectorService().search(
                arguments.get("database", ""),
                arguments.get("query", ""),
                limit=int(arguments.get("limit") or 10),
                cursor=arguments.get("cursor"),
                filters=arguments.get("filters"),
                timeout=float(arguments.get("timeout") or 30),
            )
        except (
            ScienceConnectorError,
            webtools.NetworkDisabled,
            egress.EgressBlocked,
        ) as error:
            return {"error": str(error)}
        except Exception as error:  # noqa: BLE001 - preserve the soft-fail contract
            return {"error": f"science_search: {error}"}


__all__ = ["ScienceListDatabasesTool", "ScienceSearchTool"]
