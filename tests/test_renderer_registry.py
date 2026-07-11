from __future__ import annotations

import pytest

from openai4s.server.renderers import Renderer, RendererRegistry


@pytest.mark.parametrize(
    ("artifact", "renderer_id", "matched_by"),
    [
        ({"filename": "protein.pdb"}, "molecule-3d", "extension"),
        ({"filename": "variants.vcf"}, "genome-track", "extension"),
        ({"filename": "alignment.a3m"}, "msa", "extension"),
        ({"content_type": "text/csv; charset=utf-8"}, "table", "content_type"),
        ({"metadata": {"kind": "protein_sequence"}}, "sequence", "kind"),
        ({"filename": "weights.unknown"}, "download", "fallback"),
    ],
)
def test_renderer_selection_is_deterministic(artifact, renderer_id, matched_by):
    selected = RendererRegistry().select(artifact)
    assert selected["renderer"]["renderer_id"] == renderer_id
    assert selected["matched_by"] == matched_by
    assert selected["trusted_html"] is False


def test_projection_keeps_version_and_provenance_bound_to_renderer():
    selected = RendererRegistry().select(
        {
            "artifact_id": "artifact-1",
            "version_id": "version-2",
            "filename": "plot.png",
            "content_type": "image/png",
            "producing_cell_id": "cell-9",
        }
    )
    assert selected["artifact_id"] == "artifact-1"
    assert selected["version_id"] == "version-2"
    assert selected["provenance"] == {
        "producing_cell_id": "cell-9",
        "lineage_available": True,
    }
    assert "annotate" in selected["renderer"]["capabilities"]


def test_duplicate_renderer_ids_are_rejected():
    duplicate = Renderer("same", "one")
    with pytest.raises(ValueError, match="unique"):
        RendererRegistry((duplicate, Renderer("same", "two")))
