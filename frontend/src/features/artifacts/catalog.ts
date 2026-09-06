import {
  _rendererCatalogPromise,
  rendererCatalog,
  rendererDescriptors,
} from "../../stores/artifacts";
import { api } from "./api";
import { artifactRendererVersion } from "./cache";
import type { ArtifactRow, RendererCatalogItem, RendererDescriptor } from "./types";
import { TEXT_EXT } from "./types";

/**
 * app.js:8578. Empty-value defense: the UMD script may be absent. Do not
 * treat a missing runtime as a callable. Methods are used only after this
 * returns non-null.
 */
export type ScientificRenderersApi = {
  rendererIdFromDescriptor: (
    descriptor: RendererDescriptor,
    catalog: RendererCatalogItem[],
  ) => string;
  parseSequence: (text: string, filename?: string | null) => SequenceParse | null | undefined;
  parseAlignment: (text: string, filename?: string | null) => AlignmentParse | null | undefined;
  parseGenome: (text: string, filename?: string | null) => GenomeParse | null | undefined;
  parseMolfile: (text: string) => MolfileModel | null | undefined;
  residueClass: (residue: string, alphabet: string) => string;
  smilesLines: (text: string) => Array<{ smiles: string; name: string }>;
  latexPreview: (text: string) => LatexBlock[];
};

export type SequenceRecord = {
  name?: string;
  description?: string;
  sequence: string;
};
export type SequenceParse = {
  format: string;
  alphabet: string;
  records: SequenceRecord[];
  total_length: number;
};
export type AlignmentParse = {
  format: string;
  records: SequenceRecord[];
  columns: number;
  alphabet?: string;
};
export type GenomeFeature = {
  chrom: string;
  start: number;
  end: number;
  label: string;
  type: string;
  strand?: string;
  score?: string;
};
export type GenomeParse = {
  format: string;
  features: GenomeFeature[];
  chromosomes: Array<{ chrom: string; start: number; end: number; count: number }>;
  invalid: number;
};
export type MolfileModel = {
  title: string;
  atoms: Array<{ x: number; y: number; element: string }>;
  bonds: Array<{ a: number; b: number; order: number }>;
};
export type LatexBlock = { kind: string; text: string; level?: number };

export function scientificRenderers(): ScientificRenderersApi | null {
  const host = globalThis as unknown as {
    OpenAI4SScientificRenderers?: ScientificRenderersApi | null;
  };
  return host.OpenAI4SScientificRenderers || null;
}

/** app.js:8580-8591 */
export function loadRendererCatalog(): Promise<RendererCatalogItem[]> {
  if (Array.isArray(rendererCatalog.value)) {
    return Promise.resolve(rendererCatalog.value as RendererCatalogItem[]);
  }
  const pending = _rendererCatalogPromise.value as Promise<RendererCatalogItem[]> | null;
  if (pending) return pending;
  const request = api("/renderers")
    .then((result) => {
      const rec = result && typeof result === "object" ? (result as { renderers?: unknown }) : null;
      const catalog = rec && Array.isArray(rec.renderers)
        ? rec.renderers.filter(
            (item): item is RendererCatalogItem =>
              !!item &&
              typeof item === "object" &&
              typeof (item as RendererCatalogItem).renderer_id === "string",
          )
        : [];
      rendererCatalog.value = catalog;
      return catalog;
    })
    .catch(() => {
      rendererCatalog.value = [];
      return [] as RendererCatalogItem[];
    });
  _rendererCatalogPromise.value = request;
  return request;
}

/** app.js:8593-8609 */
export function compatibilityRendererDescriptor(a: ArtifactRow): RendererDescriptor {
  const ct = String(a.content_type || "").toLowerCase().split(";", 1)[0] || "";
  const nm = String(a.filename || "").toLowerCase();
  let id = "download";
  if (ct.startsWith("image/") || /\.(png|jpe?g|gif|webp|svg)$/i.test(nm)) id = "image";
  else if (ct === "application/pdf" || nm.endsWith(".pdf")) id = "pdf";
  else if (ct === "text/html" || /\.html?$/i.test(nm)) id = "html-preview";
  else if (/\.(pdb|cif|mmcif|ent|xyz)$/i.test(nm)) id = "molecule-3d";
  else if (/\.(mol|mol2|sdf|smi|smiles)$/i.test(nm)) id = "chemistry-2d";
  else if (/\.(bed|bedgraph|gff3?|gtf|vcf)$/i.test(nm)) id = "genome-track";
  else if (/\.(aln|a2m|a3m|sto|stockholm)$/i.test(nm)) id = "msa";
  else if (/\.(fa|fasta|faa|fna|fastq|fq)$/i.test(nm)) id = "sequence";
  else if (/\.(md|markdown|rst)$/i.test(nm)) id = "markdown";
  else if (/\.tex$/i.test(nm)) id = "latex";
  else if (/csv|tab-separated/.test(ct) || /\.(csv|tsv)$/i.test(nm)) id = "table";
  else if (ct.startsWith("text/") || /json/.test(ct) || TEXT_EXT.test(nm)) id = "text";
  return {
    artifact_id: a.id,
    version_id: artifactRendererVersion(a),
    matched_by: "compatibility",
    renderer: { renderer_id: id, label: id },
    trusted_html: false,
  };
}

/** app.js:8611-8635 */
export function artifactRendererDescriptor(
  a: ArtifactRow,
): Promise<RendererDescriptor> {
  const version = artifactRendererVersion(a);
  const key = `${a.id}:${version || "latest"}`;
  const bag = rendererDescriptors.value as Record<string, unknown>;
  const cached = bag[key];
  if (cached) return cached as Promise<RendererDescriptor>;
  const suffix = version ? `?version=${encodeURIComponent(version)}` : "";
  const request = Promise.all([
    loadRendererCatalog(),
    api(`/artifacts/${encodeURIComponent(a.id)}/renderer${suffix}`),
  ]).then(([catalog, descriptor]) => {
    const desc = descriptor as RendererDescriptor | null;
    if (!desc || desc.artifact_id !== a.id) {
      throw new Error("renderer descriptor does not match artifact");
    }
    if (version && desc.version_id && desc.version_id !== version) {
      throw new Error("renderer descriptor does not match artifact version");
    }
    const runtime = scientificRenderers();
    const rendererId = runtime
      ? runtime.rendererIdFromDescriptor(desc, catalog)
      : "download";
    const catalogRenderer = catalog.find((item) => item.renderer_id === rendererId);
    return {
      ...desc,
      renderer:
        catalogRenderer || {
          renderer_id: rendererId,
          label: rendererId,
          capabilities: ["view"],
          sandboxed: true,
        },
    };
  }).catch((error: unknown) => {
    delete bag[key];
    throw error;
  });
  bag[key] = request;
  return request;
}
