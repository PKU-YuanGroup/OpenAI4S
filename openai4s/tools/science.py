"""Flat control-tool surface for schema-normalized scientific databases."""

from __future__ import annotations

from typing import Any

from openai4s.tools.base import Tool
from openai4s.tools.taxonomy import READ_ONLY, WORKSPACE_WRITE, resource_key

#: An ordinary tool's output limit: about a third of one batch's combined
#: tool-result budget, so several searches in one reply keep their receipts.
_OBSERVATION_SHARE = Tool.output_limit


def _stage10(runtime: Any) -> bool:
    from openai4s.host.stage10_science import official_stage10_enabled

    enabled = getattr(runtime, "stage10_enabled", None)
    if callable(enabled):
        return bool(enabled())
    cfg = getattr(runtime, "cfg", None) or getattr(runtime, "config", None)
    if cfg is None:
        from openai4s.config import Config

        cfg = Config()
    return official_stage10_enabled(cfg)


def _maybe_record(runtime: Any, result: dict) -> dict | None:
    if not _stage10(runtime):
        return None
    recorder = getattr(runtime, "record_science_artifact", None)
    if not callable(recorder):
        return None
    return recorder(result)


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
            return ScienceConnectorService(stage10=_stage10(_runtime)).list_databases(
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
                    "zenodo",
                    "uniprot",
                    "pdb",
                    "ensembl",
                    "chembl",
                    "pubchem",
                    "arxiv",
                    "openalex",
                    "string",
                    "bindingdb",
                    "clinvar",
                    "pubmed",
                    "clinicaltrials",
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
                    "required_score": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 1000,
                    },
                    "network_type": {
                        "type": "string",
                        "enum": ["functional", "physical"],
                    },
                    "cutoff": {
                        "type": "number",
                        "minimum": 0,
                        "description": "BindingDB affinity cutoff in nM; must be strictly positive and finite (default 100).",
                    },
                    "affinity_type": {
                        "type": "string",
                        "enum": ["Ki", "IC50", "Kd", "EC50"],
                    },
                },
                "additionalProperties": False,
            },
            "timeout": {"type": "number", "minimum": 1, "maximum": 120},
        },
        "required": ["database", "query"],
    }
    read_only = False
    needs_network = True
    writes_files = True
    derived_write_path = True
    screen_untrusted_output = True
    output_limit = 100_000
    permission_target_key = "database"
    resource_key_prefix = "science"
    resource_target_key = "database"
    side_effect_class = WORKSPACE_WRITE

    def writes_files_for(self, runtime: Any) -> bool:
        return _stage10(runtime)

    def read_only_for(self, runtime: Any) -> bool:
        return not _stage10(runtime)

    def side_effect_class_for(self, runtime: Any) -> str:
        return WORKSPACE_WRITE if _stage10(runtime) else READ_ONLY

    def resource_keys(self, arguments: Any) -> tuple[str, ...]:
        database = arguments.get("database") if isinstance(arguments, dict) else "*"
        return (resource_key("network", f"science/{database or '*'}"),)

    def execute(self, _runtime: Any, arguments: dict) -> dict:
        from openai4s import egress, webtools
        from openai4s.host.science import ScienceConnectorError, ScienceConnectorService

        try:
            result = ScienceConnectorService(stage10=_stage10(_runtime)).search(
                arguments.get("database", ""),
                arguments.get("query", ""),
                limit=int(arguments.get("limit") or 10),
                cursor=arguments.get("cursor"),
                filters=arguments.get("filters"),
                timeout=float(arguments.get("timeout") or 30),
            )
            artifact = _maybe_record(_runtime, result)
            if artifact:
                result = dict(result)
                # The Gateway consumes this marker after its trusted native
                # writer capture and replaces it with a real Artifact ref.
                result["_openai4s_artifact_capture"] = artifact
            return result
        except (
            ScienceConnectorError,
            webtools.NetworkDisabled,
            egress.EgressBlocked,
        ) as error:
            return {"error": str(error)}
        except Exception as error:  # noqa: BLE001 - preserve the soft-fail contract
            return {"error": f"science_search: {error}"}

    def render_observation(self, result: Any) -> str | None:
        """Show the model the receipt first, then records that fit its share.

        The shared renderer shows only the results list, which hid every
        database's cursor and response receipt. File inventories are listed
        while the observation stays within an ordinary tool's share of a batch.
        Past that, the largest ones are replaced by an explicit count instead
        of being cut mid-list or refusing the page. The returned data, which a
        Python cell or the Stage 10 Artifact receives, is never trimmed.
        """
        if not isinstance(result, dict) or not isinstance(result.get("results"), list):
            return None
        import json

        from openai4s.tools.registry import _safe_json

        receipt = {
            key: result.get(key)
            for key in ("database", "source", "query", "count", "next_cursor")
        }
        receipt["provenance"] = result.get("provenance")
        receipt["hash_scope"] = "metadata_response_only_not_dataset_file_bytes"
        if result.get("artifact"):
            receipt["artifact"] = result["artifact"]
        records = list(result["results"])
        items = [_safe_json(item) for item in records]
        size = sum(len(item) + 1 for item in items) + len(_safe_json(receipt))
        omitted = 0
        for index in sorted(range(len(items)), key=lambda i: -len(items[i])):
            if size <= _OBSERVATION_SHARE:
                break
            record = records[index]
            attributes = record.get("attributes") if isinstance(record, dict) else None
            files = attributes.get("files") if isinstance(attributes, dict) else None
            if not isinstance(files, list) or not files:
                continue
            kept = {key: value for key, value in attributes.items() if key != "files"}
            kept["files_omitted_from_view"] = len(files)
            trimmed = _safe_json({**record, "attributes": kept})
            size += len(trimmed) - len(items[index])
            items[index] = trimmed
            omitted += 1
        if omitted:
            receipt["view_note"] = (
                f"{omitted} file inventories omitted to fit the observation; "
                "call host.science.search in a Python cell for every entry"
            )
        parts = []
        warning = result.get("_security_warning")
        if isinstance(warning, str) and warning:
            parts.append(f"[SECURITY WARNING]\n{warning}")
        parts.append(json.dumps(receipt, ensure_ascii=False))
        parts.append(f"results ({len(items)}):\n" + "\n".join(items))
        return "\n".join(parts)


__all__ = ["ScienceListDatabasesTool", "ScienceSearchTool"]
