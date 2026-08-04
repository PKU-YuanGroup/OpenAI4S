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
