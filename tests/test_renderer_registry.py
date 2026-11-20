from __future__ import annotations

import pytest

from openai4s.server.renderers import Renderer, RendererRegistry


@pytest.mark.parametrize(
    ("artifact", "renderer_id", "matched_by"),
    [
        ({"filename": "protein.pdb"}, "molecule-3d", "extension"),
        ({"filename": "variants.vcf"}, "genome-track", "extension"),
        ({"filename": "alignment.a3m"}, "msa", "extension"),
        ({"content_type": "text/html; charset=utf-8"}, "html-preview", "content_type"),
        ({"content_type": "text/csv; charset=utf-8"}, "table", "content_type"),
        ({"metadata": {"kind": "protein_sequence"}}, "sequence", "kind"),
        # A saved notebook is selected by its own extension before the generic
        # kind/MIME loops, whatever the capturing tool stamped on it.
        ({"filename": "analysis.ipynb"}, "notebook", "extension"),
        (
            {
                "filename": "session.python.ipynb",
                "content_type": "application/x-ipynb+json",
            },
            "notebook",
            "extension",
        ),
        (
            {"filename": "analysis.ipynb", "content_type": "application/octet-stream"},
            "notebook",
            "extension",
        ),
        (
            {"filename": "analysis.ipynb", "content_type": "application/json"},
            "notebook",
            "extension",
        ),
        ({"filename": "analysis.ipynb", "kind": "code"}, "notebook", "extension"),
        # A plain .json is still plain text, not a notebook.
        (
            {"filename": "result.json", "content_type": "application/json"},
            "text",
            "content_type",
        ),
        ({"filename": "weights.unknown"}, "download", "fallback"),
        # SCI-27: a generic type does not outrank the extension. A `.fasta` is
        # served as text/plain, and that used to route it to the plain-text
        # viewer instead of the sequence viewer.
        (
            {"filename": "1crn.fasta", "content_type": "text/plain; charset=utf-8"},
            "sequence",
            "extension",
        ),
        (
            {"filename": "query.a2m", "content_type": "application/octet-stream"},
            "msa",
            "extension",
        ),
        # ...and still decides when the extension names nothing.
        (
            {"filename": "notes.unknown", "content_type": "text/plain"},
            "text",
            "content_type",
        ),
        # A specific type still wins over the extension, as before.
        (
            {"filename": "table.txt", "content_type": "text/csv"},
            "table",
            "content_type",
        ),
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


def test_notebook_renderer_is_read_only_and_declares_view_copy_only():
    from openai4s.server.renderers import DEFAULT_RENDERERS

    notebook = next(
        item for item in DEFAULT_RENDERERS if item.renderer_id == "notebook"
    )
    assert notebook.capabilities == ("view", "copy")
    assert notebook.interactive is False
    assert ".ipynb" in notebook.extensions
    assert "application/x-ipynb+json" in notebook.content_types
