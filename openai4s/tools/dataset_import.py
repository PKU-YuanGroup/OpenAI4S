"""Import one explicitly selected Zenodo file through the native receipt scope."""

from __future__ import annotations

from typing import Any

from openai4s.tools.base import Tool
from openai4s.tools.contexts import ControlToolContext
from openai4s.tools.taxonomy import WORKSPACE_WRITE


class ScienceImportDatasetTool(Tool):
    name = "science_import_dataset"
    host_method = "science_import_dataset"
    description = (
        "Import one explicitly selected open Zenodo dataset file as an immutable "
        "Artifact before analysis. Supply its exact record ID, file key, declared "
        "size and checksum from science_search, a workspace path and byte budget. "
        "Refreshes the declaration and verifies the bytes. Web native actions only."
    )
    parameters = {
        "properties": {
            "record_id": {"type": "string", "minLength": 1, "maxLength": 20},
            "file_key": {"type": "string", "minLength": 1, "maxLength": 512},
            "expected_size": {"type": "integer", "minimum": 0},
            "expected_checksum": {
                "type": "string",
                "minLength": 36,
                "maxLength": 71,
                "description": "Source-declared md5:<32 hex> or sha256:<64 hex>.",
            },
            "path": {"type": "string", "minLength": 1},
            "max_bytes": {"type": "integer", "minimum": 0},
            "timeout": {"type": "number", "minimum": 1, "maximum": 120},
        },
        "required": [
            "record_id",
            "file_key",
            "expected_size",
            "expected_checksum",
            "path",
            "max_bytes",
        ],
    }
    read_only = False
    writes_files = True
    needs_network = True
    screen_untrusted_output = True
    secret_path_key = "path"
    permission_target_default = "zenodo.org"
    resource_key_prefix = "network"
    resource_target_default = "zenodo.org"
    side_effect_class = WORKSPACE_WRITE

    def validation_error(self, arguments: Any) -> str | None:
        error = super().validation_error(arguments)
        if error is not None:
            return error
        from openai4s import webtools
        from openai4s.host.datasets import validate_selection

        try:
            validate_selection(arguments["record_id"], arguments["file_key"])
            webtools.validate_download_expectation(
                arguments["expected_size"],
                arguments["expected_checksum"],
                arguments["max_bytes"],
            )
        except (ValueError, webtools.ResponseTooLarge) as error:
            return str(error)
        return None

    def execute(self, context: ControlToolContext, arguments: dict) -> dict:
        from openai4s import webtools
        from openai4s.artifact_paths import require_capturable_destination
        from openai4s.host.datasets import resolve_selection, validate_selection
        from openai4s.host.download import download_into_workspace

        error = self.validation_error(arguments)
        if error is not None:
            return {"error": error}
        cancelled = context.download_cancellation()
        try:
            webtools.check_download_cancelled(cancelled)
            selection = validate_selection(
                arguments["record_id"], arguments["file_key"]
            )
            require_capturable_destination(
                context.workspace(), context.resolve(arguments["path"])
            )
            source = resolve_selection(
                selection,
                expected_size=arguments["expected_size"],
                expected_checksum=arguments["expected_checksum"],
                timeout=float(arguments.get("timeout", 60)),
            )
            webtools.check_download_cancelled(cancelled)
            result = download_into_workspace(
                context,
                selection.download_url,
                arguments["path"],
                timeout=float(arguments.get("timeout", 60)),
                max_bytes=arguments["max_bytes"],
                expected_size=arguments["expected_size"],
                expected_checksum=arguments["expected_checksum"],
                cancelled=cancelled,
            )
        except Exception as error:  # noqa: BLE001 - keep the Host soft-fail contract
            return {"error": f"science_import_dataset: {error}"}
        source["dataset"].update(
            file_verification="size_and_source_checksum_verified",
            local_sha256=result["sha256"],
            downloaded_bytes=result["bytes"],
        )
        return {
            **result,
            "source": source,
            "_openai4s_artifact_capture": {
                "filename": result["path"],
                "checksum": result["sha256"],
                "source": source,
            },
        }
