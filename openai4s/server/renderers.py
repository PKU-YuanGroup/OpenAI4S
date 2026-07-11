"""Artifact kind → safe scientific renderer selection.

The registry contains metadata only; it does not import scientific libraries or
execute artifact content.  The static UI uses the returned renderer ID to pick
an already-vendored/view-only component.  Every projection retains immutable
artifact/version/provenance identifiers so a visualization cannot drift away
from the bytes it represents.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class Renderer:
    renderer_id: str
    label: str
    kinds: tuple[str, ...] = ()
    content_types: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()
    interactive: bool = False
    sandboxed: bool = True
    capabilities: tuple[str, ...] = ("view",)

    def public(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_RENDERERS: tuple[Renderer, ...] = (
    Renderer(
        "molecule-3d",
        "3D molecular structure",
        kinds=("molecule_3d", "protein_structure", "structure"),
        content_types=("chemical/x-pdb", "chemical/x-mmcif"),
        extensions=(".pdb", ".cif", ".mmcif", ".ent", ".xyz"),
        interactive=True,
        capabilities=("view", "rotate", "style", "annotate"),
    ),
    Renderer(
        "chemistry-2d",
        "2D chemistry",
        kinds=("molecule_2d", "chemical_structure"),
        content_types=("chemical/x-mdl-sdfile", "chemical/x-mdl-molfile"),
        extensions=(".mol", ".mol2", ".sdf", ".smi", ".smiles"),
        interactive=True,
        capabilities=("view", "annotate", "compare_versions"),
    ),
    Renderer(
        "genome-track",
        "Genome track",
        kinds=("genome_track", "genomics"),
        extensions=(".bed", ".bedgraph", ".gff", ".gff3", ".gtf", ".vcf"),
        interactive=True,
        capabilities=("view", "zoom", "annotate", "compare_versions"),
    ),
    Renderer(
        "sequence",
        "Biological sequence",
        kinds=("sequence", "protein_sequence", "dna_sequence", "rna_sequence"),
        extensions=(".fa", ".fasta", ".faa", ".fna", ".fastq"),
        capabilities=("view", "copy", "annotate"),
    ),
    Renderer(
        "msa",
        "Multiple sequence alignment",
        kinds=("msa", "alignment"),
        extensions=(".aln", ".a2m", ".a3m", ".sto", ".stockholm"),
        interactive=True,
        capabilities=("view", "scroll", "color_scheme", "annotate"),
    ),
    Renderer(
        "table",
        "Data table",
        kinds=("table", "dataframe", "dataset"),
        content_types=("text/csv", "text/tab-separated-values"),
        extensions=(".csv", ".tsv", ".parquet", ".arrow"),
        interactive=True,
        capabilities=("view", "sort", "filter", "compare_versions"),
    ),
    Renderer(
        "image",
        "Image",
        kinds=("image", "figure", "plot"),
        content_types=("image/png", "image/jpeg", "image/webp", "image/svg+xml"),
        extensions=(".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"),
        interactive=True,
        capabilities=("view", "zoom", "annotate", "compare_versions"),
    ),
    Renderer(
        "pdf",
        "PDF document",
        kinds=("pdf", "paper", "report"),
        content_types=("application/pdf",),
        extensions=(".pdf",),
        interactive=True,
        capabilities=("view", "search", "annotate"),
    ),
    Renderer(
        "latex",
        "LaTeX source",
        kinds=("latex", "equation"),
        content_types=("application/x-tex",),
        extensions=(".tex",),
        capabilities=("view", "copy"),
    ),
    Renderer(
        "markdown",
        "Markdown",
        kinds=("markdown", "report", "note"),
        content_types=("text/markdown",),
        extensions=(".md", ".markdown", ".rst"),
        capabilities=("view", "search", "copy"),
    ),
    Renderer(
        "text",
        "Plain text",
        kinds=("text", "log", "code"),
        content_types=("text/plain", "application/json"),
        extensions=(".txt", ".log", ".json", ".jsonl", ".py", ".r"),
        capabilities=("view", "search", "copy", "compare_versions"),
    ),
    Renderer(
        "download",
        "Binary artifact",
        kinds=("binary", "model", "checkpoint"),
        extensions=(".pt", ".pth", ".ckpt", ".onnx", ".bin", ".npz"),
        interactive=False,
        capabilities=("metadata", "versions", "provenance"),
    ),
)


class RendererRegistry:
    def __init__(self, renderers: Iterable[Renderer] = DEFAULT_RENDERERS) -> None:
        self._renderers = tuple(renderers)
        ids = [item.renderer_id for item in self._renderers]
        if len(ids) != len(set(ids)):
            raise ValueError("renderer IDs must be unique")

    def select(self, artifact: Mapping[str, Any]) -> dict[str, Any]:
        """Return one renderer projection bound to an immutable version."""

        renderer, reason = self._match(artifact)
        return {
            "renderer": renderer.public(),
            "matched_by": reason,
            "artifact_id": artifact.get("artifact_id"),
            "version_id": artifact.get("version_id")
            or artifact.get("latest_version_id"),
            "filename": artifact.get("filename"),
            "content_type": artifact.get("content_type"),
            "provenance": {
                "producing_cell_id": artifact.get("producing_cell_id"),
                "lineage_available": bool(
                    artifact.get("lineage")
                    or artifact.get("lineage_edges")
                    or artifact.get("producing_cell_id")
                ),
            },
            "trusted_html": False,
        }

    def catalog(self) -> list[dict[str, Any]]:
        return [renderer.public() for renderer in self._renderers]

    def _match(self, artifact: Mapping[str, Any]) -> tuple[Renderer, str]:
        metadata = artifact.get("metadata")
        kind = str(
            artifact.get("kind")
            or (metadata.get("kind") if isinstance(metadata, Mapping) else "")
            or ""
        ).lower()
        content_type = str(artifact.get("content_type") or "").lower().split(";", 1)[0]
        extension = PurePosixPath(str(artifact.get("filename") or "")).suffix.lower()
        for renderer in self._renderers:
            if kind and kind in renderer.kinds:
                return renderer, "kind"
        for renderer in self._renderers:
            if content_type and content_type in renderer.content_types:
                return renderer, "content_type"
        for renderer in self._renderers:
            if extension and extension in renderer.extensions:
                return renderer, "extension"
        fallback = next(
            item for item in self._renderers if item.renderer_id == "download"
        )
        return fallback, "fallback"


__all__ = ["DEFAULT_RENDERERS", "Renderer", "RendererRegistry"]
