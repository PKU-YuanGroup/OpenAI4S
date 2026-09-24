"""Bind a selected public dataset file to freshly checked source metadata.

This module does not download files, authorize a request, or register an
Artifact. The dedicated native import tool owns those boundaries. Resolving a
selection here prevents a later import from silently selecting another file
or treating a concept DOI as an exact input version.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from openai4s.host.science import ScienceConnectorService
from openai4s.host.zenodo import _record, dataset_file_url


class DatasetSelectionError(ValueError):
    """The selected file cannot be bound to its source declaration."""


@dataclass(frozen=True)
class DatasetSelection:
    record_id: str
    file_key: str

    @property
    def metadata_url(self) -> str:
        return f"https://zenodo.org/api/records/{self.record_id}"

    @property
    def download_url(self) -> str:
        return dataset_file_url(self.record_id, self.file_key)


def validate_selection(record_id: Any, file_key: Any) -> DatasetSelection:
    if (
        not isinstance(record_id, str)
        or re.fullmatch(r"[1-9][0-9]{0,19}", record_id) is None
    ):
        raise DatasetSelectionError(
            "dataset record_id must name one exact numeric record"
        )
    if not isinstance(file_key, str) or not file_key.strip() or len(file_key) > 512:
        raise DatasetSelectionError("dataset file_key is missing or too large")
    return DatasetSelection(record_id, file_key)


def resolve_selection(
    selection: DatasetSelection,
    *,
    expected_size: int,
    expected_checksum: str,
    timeout: float,
    service: ScienceConnectorService | None = None,
) -> dict[str, Any]:
    """Read the fixed record endpoint and refuse stale or unavailable inputs."""
    service = service or ScienceConnectorService()
    # A fresh service per import keeps response provenance scoped to this read.
    service._responses = []
    retrieved_at = int(time.time() * 1000)
    record = _record(service._json(selection.metadata_url, timeout))
    if record["id"] != selection.record_id:
        raise DatasetSelectionError("dataset record identity changed during resolution")
    metadata = record["attributes"]
    if metadata["access_right"] != "open":
        raise DatasetSelectionError("dataset file access is not declared open")
    if metadata["files"] is None:
        raise DatasetSelectionError("dataset source did not provide a file inventory")
    file = next(
        (item for item in metadata["files"] if item["key"] == selection.file_key), None
    )
    if file is None:
        raise DatasetSelectionError(
            "selected dataset file is absent from the source record"
        )
    if file["declared_size_bytes"] is None or file["declared_checksum"] is None:
        raise DatasetSelectionError(
            "dataset source did not declare a file size and checksum"
        )
    if file["declared_size_bytes"] != expected_size:
        raise DatasetSelectionError("dataset file size changed; refresh the selection")
    if file["declared_checksum"] != expected_checksum.lower():
        raise DatasetSelectionError(
            "dataset file checksum changed; refresh the selection"
        )
    responses = list(service._responses)
    return {
        "kind": "dataset_input",
        "database": "zenodo",
        "source": "Zenodo datasets",
        "retrieved_at": retrieved_at,
        "request_url": selection.metadata_url,
        "normalization_version": 1,
        "responses": responses,
        "response_sha256": responses[0]["sha256"] if len(responses) == 1 else None,
        "dataset": {
            "provider": "zenodo",
            "record_id": selection.record_id,
            "record_url": record["url"],
            "record_doi": metadata["record_doi"],
            "concept_doi": metadata["concept_doi"],
            "version": metadata["version"],
            "title": record["title"],
            "declared_license": metadata["declared_license"],
            "license_status": metadata["license_status"],
            "access_right": metadata["access_right"],
            "file_key": selection.file_key,
            "declared_size_bytes": expected_size,
            "declared_checksum": expected_checksum.lower(),
        },
    }
