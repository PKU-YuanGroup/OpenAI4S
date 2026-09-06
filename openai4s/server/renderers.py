"""Artifact kind → safe scientific renderer selection.

The registry contains metadata only; it does not import scientific libraries or
execute artifact content.  The static UI uses the returned renderer ID to pick
an already-vendored/view-only component.  Every projection retains immutable
artifact/version/provenance identifiers so a visualization cannot drift away
from the bytes it represents.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
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


#: A capability name here is a promise to whoever reads the artifact that the
#: viewer can do that thing with it.  ``compare_versions`` was declared on five
#: renderers while no version-comparison UI exists anywhere, so it is removed
#: rather than left standing as an aspiration: a wrong claim about scientific
#: output is worse than a missing one, because it is believed.
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
        capabilities=("view", "annotate"),
    ),
    Renderer(
        "genome-track",
        "Genome track",
        kinds=("genome_track", "genomics"),
        extensions=(".bed", ".bedgraph", ".gff", ".gff3", ".gtf", ".vcf"),
        interactive=True,
        capabilities=("view", "zoom", "annotate"),
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
        # Delimited text only in the default (flag-off) catalog.  ``.parquet``
        # is added to this renderer only when the official workbench is on
        # *and* the optional Parquet engine is importable; otherwise it stays
        # on the download renderer.  ``sort``/``filter``/``profile``/``export``
        # follow the same gate: the flag-off viewer is still one static capped
        # table, so those names must not appear until the workbench is on.
        extensions=(".csv", ".tsv"),
        interactive=True,
        capabilities=("view",),
    ),
    Renderer(
        "image",
        "Image",
        kinds=("image", "figure", "plot"),
        content_types=("image/png", "image/jpeg", "image/webp", "image/svg+xml"),
        extensions=(".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"),
        interactive=True,
        capabilities=("view", "zoom", "annotate"),
    ),
    Renderer(
        "pdf",
        "PDF document",
        # ``report`` also belongs to the markdown renderer; since ``kind`` wins
        # over content-type and pdf precedes markdown, keeping it here routed
        # every markdown/text report to the PDF viewer.  Real PDFs still match
        # via content-type/extension.
        kinds=("pdf", "paper"),
        content_types=("application/pdf",),
        extensions=(".pdf",),
        interactive=True,
        capabilities=("view", "search", "annotate"),
    ),
    Renderer(
        "html-preview",
        "Sandboxed HTML preview",
        kinds=("html", "web_page"),
        content_types=("text/html",),
        extensions=(".html", ".htm"),
        interactive=True,
        capabilities=("view",),
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
        capabilities=("view", "search", "copy"),
    ),
    Renderer(
        "download",
        "Binary artifact",
        kinds=("binary", "model", "checkpoint"),
        # Columnar binary formats live here, not on the table renderer: with no
        # parser in-tree the only true statement about them is "download".
        extensions=(
            ".pt",
            ".pth",
            ".ckpt",
            ".onnx",
            ".bin",
            ".npz",
            ".parquet",
            ".arrow",
            ".feather",
        ),
        interactive=False,
        capabilities=("metadata", "versions", "provenance"),
    ),
)


_WORKBENCH_TABLE_CAPABILITIES = (
    "view",
    "sort",
    "filter",
    "profile",
    "export",
)


def _catalog_renderers(
    *,
    workbench_enabled: bool,
    parquet_available: bool,
    renderers: Iterable[Renderer],
) -> tuple[Renderer, ...]:
    """Project the static catalog through the live workbench/Parquet posture.

    Under-claiming is the allowed direction: a missing engine or a flag-off
    process never advertises ``table``/``profile``/``parquet``. A present
    engine is still not advertised unless the workbench itself is on, because
    ``GET .../table`` answers 403 when the flag is off.
    """

    parquet_on_table = bool(workbench_enabled and parquet_available)
    projected: list[Renderer] = []
    for renderer in renderers:
        if renderer.renderer_id == "table":
            extensions = renderer.extensions
            if parquet_on_table and ".parquet" not in extensions:
                extensions = extensions + (".parquet",)
            extra = ("parquet",) if parquet_on_table else ()
            capabilities = (
                _WORKBENCH_TABLE_CAPABILITIES + extra
                if workbench_enabled
                else renderer.capabilities
            )
            projected.append(
                replace(renderer, extensions=extensions, capabilities=capabilities)
            )
            continue
        if renderer.renderer_id == "download":
            extensions = renderer.extensions
            if parquet_on_table:
                extensions = tuple(item for item in extensions if item != ".parquet")
            projected.append(replace(renderer, extensions=extensions))
            continue
        projected.append(renderer)
    return tuple(projected)


class RendererRegistry:
    def __init__(
        self,
        renderers: Iterable[Renderer] | None = None,
        *,
        workbench_enabled: bool = False,
        parquet_available: bool | None = None,
    ) -> None:
        from openai4s.server.table_profile import parquet_engine_available

        source = DEFAULT_RENDERERS if renderers is None else tuple(renderers)
        parquet_ok = (
            parquet_engine_available()
            if parquet_available is None
            else bool(parquet_available)
        )
        self._workbench_enabled = bool(workbench_enabled)
        self._parquet_available = parquet_ok
        # A caller-supplied catalog is taken as already honest; only the
        # default catalog is projected through the workbench/Parquet gate.
        self._renderers = (
            _catalog_renderers(
                workbench_enabled=self._workbench_enabled,
                parquet_available=self._parquet_available,
                renderers=source,
            )
            if renderers is None
            else source
        )
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
