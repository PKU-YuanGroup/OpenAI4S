"""The renderer catalog must not advertise what the viewer cannot do.

A ``Renderer.capabilities`` entry is served to the browser and read by a person
as a statement about what they can do with a scientific artifact.  Three of them
were fiction: the table renderer declared ``sort`` and ``filter`` while the
viewer draws one static capped table, and ``compare_versions`` was declared on
five renderers while no version-comparison UI exists at all.  The same overclaim
listed ``.parquet``/``.arrow`` as viewable tables with no parser for either, so
the descriptor promised a table and the fetch then fell through to a download
card once the bytes turned out to be binary.

These assertions are the enforcement: the implemented set was established by
grepping every capability string against its consumer in ``webui/app.js``, and
that audit cannot be re-run automatically, so re-adding a name here has to be a
deliberate act that also updates this test.
"""

from __future__ import annotations

import pytest

from openai4s.server.renderers import RendererRegistry

# Capability names with no implementation anywhere in the UI at the time of the
# audit.  ``sort``/``filter``: the table renderer appends a plain <table>.
# ``compare_versions``: the viewer has no version-diff surface of any kind.
UNIMPLEMENTED_CAPABILITIES = frozenset({"sort", "filter", "compare_versions"})


def _catalog_by_id() -> dict[str, dict]:
    return {item["renderer_id"]: item for item in RendererRegistry().catalog()}


def test_no_renderer_advertises_an_unimplemented_capability() -> None:
    offenders = {
        renderer_id: sorted(
            UNIMPLEMENTED_CAPABILITIES.intersection(item["capabilities"])
        )
        for renderer_id, item in _catalog_by_id().items()
        if UNIMPLEMENTED_CAPABILITIES.intersection(item["capabilities"])
    }
    assert offenders == {}


def test_table_renderer_declares_only_viewing() -> None:
    table = _catalog_by_id()["table"]
    assert list(table["capabilities"]) == ["view"]


def test_flag_off_catalog_does_not_advertise_workbench_table_verbs() -> None:
    registry = RendererRegistry(workbench_enabled=False, parquet_available=True)
    catalog = {item["renderer_id"]: item for item in registry.catalog()}
    assert list(catalog["table"]["capabilities"]) == ["view"]
    assert "parquet" not in catalog["table"]["capabilities"]
    assert "profile" not in catalog["table"]["capabilities"]
    selected = registry.select({"filename": "expression.parquet"})
    assert selected["renderer"]["renderer_id"] == "download"
    assert selected["matched_by"] == "extension"


def test_workbench_catalog_declares_profile_only_when_enabled() -> None:
    registry = RendererRegistry(workbench_enabled=True, parquet_available=False)
    table = {item["renderer_id"]: item for item in registry.catalog()}["table"]
    assert "view" in table["capabilities"]
    assert "sort" in table["capabilities"]
    assert "filter" in table["capabilities"]
    assert "profile" in table["capabilities"]
    assert "export" in table["capabilities"]
    assert "parquet" not in table["capabilities"]
    assert "compare_versions" not in table["capabilities"]
    selected = registry.select({"filename": "expression.parquet"})
    assert selected["renderer"]["renderer_id"] == "download"


def test_parquet_is_available_only_with_workbench_and_engine() -> None:
    registry = RendererRegistry(workbench_enabled=True, parquet_available=True)
    table = {item["renderer_id"]: item for item in registry.catalog()}["table"]
    assert "parquet" in table["capabilities"]
    assert ".parquet" in table["extensions"]
    selected = registry.select({"filename": "expression.parquet"})
    assert selected["renderer"]["renderer_id"] == "table"
    assert selected["matched_by"] == "extension"
    download = {item["renderer_id"]: item for item in registry.catalog()}["download"]
    assert ".parquet" not in download["extensions"]


def test_unsupported_parquet_engine_never_reports_available() -> None:
    registry = RendererRegistry(workbench_enabled=True, parquet_available=False)
    catalog = {item["renderer_id"]: item for item in registry.catalog()}
    assert "parquet" not in catalog["table"]["capabilities"]
    assert ".parquet" not in catalog["table"]["extensions"]
    assert catalog["download"]["extensions"].count(".parquet") == 1
    selected = registry.select({"filename": "counts.parquet"})
    assert selected["renderer"]["renderer_id"] == "download"


@pytest.mark.parametrize(
    "filename", ["expression.parquet", "cells.arrow", "counts.feather"]
)
def test_columnar_binaries_are_declared_download_only(filename: str) -> None:
    selected = RendererRegistry().select({"filename": filename})
    assert selected["renderer"]["renderer_id"] == "download"
    # ``extension``, not ``fallback``: the format is named as download-only on
    # purpose, rather than reaching the download renderer by failing to match.
    assert selected["matched_by"] == "extension"


@pytest.mark.parametrize("filename", ["counts.csv", "counts.tsv"])
def test_delimited_text_still_selects_the_table_renderer(filename: str) -> None:
    selected = RendererRegistry().select({"filename": filename})
    assert selected["renderer"]["renderer_id"] == "table"
    assert selected["matched_by"] == "extension"
