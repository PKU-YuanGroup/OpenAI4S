import { isReady } from "../../compat/stub";
import { parseTable } from "../csv/csv";
import { renderMd } from "../md/render";
import { publicText } from "../scrub/scrub";
import { readWorkbenchFlag, renderTableArtifact as renderTableArtifactM04 } from "../table";
import { applyArtifactIframeSandbox } from "../../islands/frames";
import { molTeardown } from "../../islands/mol";
import {
  artifactMediaType,
  beginPreviewRead,
  callWindow,
  CHEMISTRY_SOURCE_PREVIEW_CHARACTERS,
  controlDense,
  el,
  fetchArtifactText,
  hostWindow,
  iconEl,
  isArtifactReadAborted,
  LATEX_SOURCE_PREVIEW_CHARACTERS,
  looksBinary,
  MARKDOWN_PREVIEW_CHARACTERS,
  MOLECULE_FALLBACK_PREVIEW_CHARACTERS,
  previewObjectUrl,
  previewReadSignal,
  readArtifactBytes,
  svgElement,
  translate,
  TEXT_SOURCE_PREVIEW_CHARACTERS,
} from "./api";
import { filesT, readFailureText } from "./copy";
import { artifactMetadataTarget, artUrl, previewSlice } from "./cache";
import {
  artifactRendererDescriptor,
  compatibilityRendererDescriptor,
  scientificRenderers,
  type GenomeFeature,
  type LatexBlock,
  type MolfileModel,
} from "./catalog";
import { renderSheet } from "./sheet";
import { renderHtmlPreview } from "./preview";
import { closeNotebookPreview, renderNotebookArtifact } from "./notebook";
import { readScienceSearchEnvelope, renderScienceResults } from "./science-results";
import type { ArtifactRow } from "./types";
import { TEXT_EXT } from "./types";

/**
 * The persistent notice every `partial` preview carries (F3): how much was
 * shown and where to get the complete file. Complete previews add nothing, so
 * short content renders exactly as it did before the truncation work; a failed
 * read is the third state, rendered by `rendererFailure`.
 */
function partialNotice(container: HTMLElement, shown: number): void {
  const note = el(
    "div",
    "renderer-note renderer-completeness",
    filesT("viewer.preview.partial", shown.toLocaleString()),
  );
  note.setAttribute("data-state", "partial");
  note.setAttribute("role", "status");
  container.appendChild(note);
}

/** The exact-version download that must accompany every partial preview. */
function previewDownload(container: HTMLElement, a: ArtifactRow, url: string): void {
  const download = el("a", "outline-btn small", translate("common.download"));
  download.href = url;
  download.setAttribute("download", a.filename || "artifact");
  container.appendChild(download);
}

/** Partial notice plus the full-file entry, for previews with one display limit. */
function partialPreview(container: HTMLElement, a: ArtifactRow, url: string, shown: number): void {
  partialNotice(container, shown);
  previewDownload(container, a, url);
}

/**
 * A preview that could not be shown: why it failed, an optional manual retry,
 * and the exact-version original download. A failed read is never rendered as
 * a blank success.
 */
export function rendererFailure(
  container: HTMLElement,
  a: ArtifactRow,
  url: string,
  error?: unknown,
  retry?: () => void,
): void {
  container.innerHTML = "";
  const card = el("div", "renderer-fallback");
  card.appendChild(iconEl("alert-triangle", 18));
  const text = el(
    "div",
    "renderer-fallback-text",
    error === undefined ? translate("viewer.renderer.error") : readFailureText(error),
  );
  text.setAttribute("role", "alert");
  card.appendChild(text);
  if (retry) {
    const again = el("button", "outline-btn small", filesT("viewer.read.retry"));
    again.onclick = () => {
      container.innerHTML = "";
      retry();
    };
    card.appendChild(again);
  }
  const download = el("a", "outline-btn small", translate("common.download"));
  download.href = url;
  download.setAttribute("download", a.filename || "artifact");
  card.appendChild(download);
  container.appendChild(card);
}

/** Catch handler shared by the text-backed previews: silent on cancel, honest otherwise. */
function readFailure(
  container: HTMLElement,
  a: ArtifactRow,
  url: string,
  retry: () => void,
): (error: unknown) => void {
  return (error: unknown) => {
    if (isArtifactReadAborted(error)) return;
    if (!container.isConnected) return;
    rendererFailure(container, a, url, error, retry);
  };
}

function appendResidues(
  container: HTMLElement,
  sequence: string,
  alphabet: string,
  limit: number,
): number {
  const runtime = scientificRenderers();
  const fragment = document.createDocumentFragment();
  const shown = String(sequence || "").slice(0, Math.max(0, limit));
  for (const residue of shown) {
    const cls = runtime ? runtime.residueClass(residue, alphabet) : "other";
    const span = el("span", "bio-residue " + cls, residue);
    fragment.appendChild(span);
  }
  container.appendChild(fragment);
  return shown.length;
}

export function renderMarkdownArtifact(container: HTMLElement, a: ArtifactRow, url: string): void {
  fetchArtifactText(url, { signal: previewReadSignal(container) })
    .then((text) => {
      if (!container.isConnected) return;
      const slice = previewSlice(text, MARKDOWN_PREVIEW_CHARACTERS);
      // Markdown keeps both notices outside the rendered body: the top one is
      // visible before the reader starts, the bottom one right after the cut,
      // so a reader who reaches the end of the shown text cannot mistake it for
      // the end of the report.
      if (slice.truncated) partialNotice(container, slice.shown.length);
      const markdown = el("div", "md renderer-markdown");
      markdown.innerHTML = renderMd(slice.shown);
      container.appendChild(markdown);
      if (slice.truncated) {
        partialNotice(container, slice.shown.length);
        previewDownload(container, a, url);
      }
    })
    .catch(readFailure(container, a, url, () => renderMarkdownArtifact(container, a, url)));
}

const SOURCE_PREVIEW_CHARACTERS = TEXT_SOURCE_PREVIEW_CHARACTERS;

/** Render bounded raw text; returns whether an exact-version download entry was included. */
export function renderRawSource(container: HTMLElement, a: ArtifactRow, text: string, url: string): boolean {
  const slice = previewSlice(text, SOURCE_PREVIEW_CHARACTERS);
  const pre = el("pre", "renderer-source");
  pre.textContent = slice.shown;
  container.appendChild(pre);
  if (!slice.truncated) return false;
  partialNotice(container, slice.shown.length);
  // The raw-source preview can prove the already-read bytes are complete, so
  // it keeps the "show complete text" expansion on top of the download entry.
  const expand = el("button", "outline-btn small", filesT("viewer.text.expand"));
  expand.setAttribute("aria-expanded", "false");
  expand.onclick = () => {
    pre.textContent = text;
    expand.setAttribute("aria-expanded", "true");
    expand.disabled = true;
  };
  container.appendChild(expand);
  previewDownload(container, a, url);
  return true;
}

export function renderStructuredText(
  container: HTMLElement,
  a: ArtifactRow,
  text: string,
  url: string = artUrl(a),
): void {
  // A5: a saved science-search envelope is recognised before the table parse
  // (and before the raw-source fallback); anything that does not pass the
  // strict envelope reader keeps the behaviour below.
  const search = readScienceSearchEnvelope(text);
  if (search) {
    renderScienceResults(container, a, url, text, search, (target) => {
      const hasDownload = renderRawSource(target, a, text, url);
      if (hasDownload) return;
      // A complete raw source has no truncation entry of its own; the raw
      // JSON tab still promises the exact-version download (A5).
      const download = el("a", "outline-btn small", translate("common.download"));
      download.href = url;
      download.setAttribute("download", a.filename || "artifact");
      target.appendChild(download);
    });
    return;
  }
  const rows = parseTable(text, a);
  if (!rows || !rows.length) {
    // The JSON branch runs before `looksBinary` so long string values are
    // not mistaken for bytes; a `.json` that failed to parse AND is dense
    // with control characters is a mislabelled blob, not source to expand.
    // The tile is built directly: `renderDownloadArtifact` would route a
    // `.json` name back into the text renderer and fetch forever.
    if (controlDense(text)) return renderDownloadCard(container, a, url);
    renderRawSource(container, a, text, url);
    return;
  }
  renderSheet(container, rows);
}

export function renderTextArtifact(container: HTMLElement, a: ArtifactRow, url: string): void {
  fetchArtifactText(url, { signal: previewReadSignal(container) })
    .then((text) => {
      if (!container.isConnected) return;
      const ct = String(a.content_type || "").toLowerCase();
      const nm = String(a.filename || "").toLowerCase();
      // Long string values are valid JSON even when they resemble encoded bytes.
      if (/json/.test(ct) || /\.json$/i.test(nm)) return renderStructuredText(container, a, text, url);
      // The tile directly, as the JSON branch does: `renderDownloadArtifact`
      // sends a text-like name straight back here, which fetched forever.
      if (looksBinary(text)) return renderDownloadCard(container, a, url);
      renderRawSource(container, a, text, url);
    })
    .catch(readFailure(container, a, url, () => renderTextArtifact(container, a, url)));
}

export function renderTableArtifact(
  container: HTMLElement,
  a: ArtifactRow,
  url: string,
  renderer?: { capabilities?: readonly string[] | null },
): void {
  renderTableArtifactM04(container, a, url, {
    capabilities: renderer?.capabilities,
  });
}

export function renderSequenceArtifact(container: HTMLElement, a: ArtifactRow, url: string): void {
  fetchArtifactText(url, { signal: previewReadSignal(container) })
    .then((text) => {
      if (!container.isConnected) return;
      const runtime = scientificRenderers();
      const parsed = runtime && runtime.parseSequence(text, a.filename);
      if (!parsed || !parsed.records.length) return renderTextArtifact(container, a, url);
      const summary = el(
        "div",
        "bio-summary",
        translate(
          "viewer.sequence.summary",
          parsed.records.length,
          parsed.total_length.toLocaleString(),
          parsed.alphabet,
        ),
      );
      container.appendChild(summary);
      const list = el("div", "sequence-list");
      let remaining = 30000;
      let shown = 0;
      parsed.records.slice(0, 100).forEach((record) => {
        if (remaining <= 0) return;
        const card = el("section", "sequence-record");
        const head = el("div", "sequence-head");
        head.appendChild(el("strong", null, record.name || "sequence"));
        head.appendChild(
          el(
            "span",
            null,
            `${record.sequence.length.toLocaleString()} ${parsed.alphabet === "protein" ? "aa" : "nt"}`,
          ),
        );
        card.appendChild(head);
        if (record.description) {
          card.appendChild(el("div", "sequence-description", record.description));
        }
        const sequence = el("div", "bio-sequence");
        const used = appendResidues(
          sequence,
          record.sequence,
          parsed.alphabet,
          Math.min(remaining, 10000),
        );
        remaining -= used;
        shown += used;
        card.appendChild(sequence);
        list.appendChild(card);
      });
      container.appendChild(list);
      if (shown < parsed.total_length) {
        container.appendChild(
          el(
            "div",
            "renderer-note",
            translate("viewer.sequence.omitted", (parsed.total_length - shown).toLocaleString()),
          ),
        );
      }
    })
    .catch(readFailure(container, a, url, () => renderSequenceArtifact(container, a, url)));
}

export function renderAlignmentArtifact(container: HTMLElement, a: ArtifactRow, url: string): void {
  fetchArtifactText(url, { signal: previewReadSignal(container) })
    .then((text) => {
      if (!container.isConnected) return;
      const runtime = scientificRenderers();
      const parsed = runtime && runtime.parseAlignment(text, a.filename);
      if (!parsed || !parsed.records.length) return renderTextArtifact(container, a, url);
      container.appendChild(
        el(
          "div",
          "bio-summary",
          translate(
            "viewer.msa.summary",
            parsed.records.length,
            parsed.columns.toLocaleString(),
            parsed.format,
          ),
        ),
      );
      const viewport = el("div", "msa-viewport");
      parsed.records.slice(0, 48).forEach((record) => {
        const row = el("div", "msa-row");
        const label = el("div", "msa-label", record.name || "sequence");
        label.title = record.name || "sequence";
        row.appendChild(label);
        const sequence = el("div", "msa-sequence");
        appendResidues(sequence, record.sequence, parsed.alphabet || "protein", 1200);
        row.appendChild(sequence);
        viewport.appendChild(row);
      });
      container.appendChild(viewport);
      const omitted = parsed.records.reduce(
        (sum, record, index) =>
          sum + (index >= 48 ? record.sequence.length : Math.max(0, record.sequence.length - 1200)),
        0,
      );
      if (omitted) {
        container.appendChild(
          el("div", "renderer-note", translate("viewer.sequence.omitted", omitted.toLocaleString())),
        );
      }
    })
    .catch(readFailure(container, a, url, () => renderAlignmentArtifact(container, a, url)));
}

export function renderGenomeTrack(container: HTMLElement, a: ArtifactRow, url: string): void {
  fetchArtifactText(url, { signal: previewReadSignal(container) })
    .then((text) => {
      if (!container.isConnected) return;
      const runtime = scientificRenderers();
      const parsed = runtime && runtime.parseGenome(text, a.filename);
      if (!parsed || !parsed.features.length) return renderTextArtifact(container, a, url);
      container.appendChild(
        el(
          "div",
          "bio-summary",
          `${parsed.format} · ${translate("viewer.genome.features", parsed.features.length.toLocaleString(), parsed.chromosomes.length)}`,
        ),
      );
      if (parsed.invalid) {
        container.appendChild(
          el(
            "div",
            "renderer-note",
            translate("viewer.genome.invalid", parsed.invalid.toLocaleString()),
          ),
        );
      }
      const grouped = new Map<string, GenomeFeature[]>();
      parsed.features.forEach((feature) => {
        const list = grouped.get(feature.chrom) || [];
        list.push(feature);
        grouped.set(feature.chrom, list);
      });
      const tracks = el("div", "genome-tracks");
      let budget = 500;
      parsed.chromosomes.slice(0, 24).forEach((chromosome) => {
        const row = el("section", "genome-row");
        const head = el("div", "genome-head");
        head.appendChild(el("strong", null, chromosome.chrom));
        head.appendChild(
          el(
            "span",
            null,
            `${chromosome.start.toLocaleString()}–${chromosome.end.toLocaleString()} · ${chromosome.count}`,
          ),
        );
        row.appendChild(head);
        const svg = svgElement("svg", {
          viewBox: "0 0 1000 58",
          role: "img",
          "aria-label": `${chromosome.chrom} genome track`,
        });
        svg.appendChild(svgElement("line", { x1: 18, y1: 29, x2: 982, y2: 29, class: "genome-axis" }));
        const span = Math.max(1, chromosome.end - chromosome.start);
        const features = (grouped.get(chromosome.chrom) || []).slice(0, Math.max(0, budget));
        budget -= features.length;
        features.forEach((feature, index) => {
          const x = 18 + 964 * ((feature.start - chromosome.start) / span);
          const width = Math.max(2, 964 * ((feature.end - feature.start) / span));
          const rect = svgElement("rect", {
            x: x.toFixed(2),
            y: 9 + (index % 5) * 8,
            width: Math.min(982 - x, width).toFixed(2),
            height: 6,
            rx: 2,
            class: `genome-feature genome-${String(feature.type || "feature").replace(/[^a-z0-9_-]/gi, "").toLowerCase()}`,
          });
          const title = svgElement("title");
          title.textContent = `${feature.label} · ${feature.chrom}:${feature.start + 1}-${feature.end} · ${feature.type}`;
          rect.appendChild(title);
          svg.appendChild(rect);
        });
        row.appendChild(svg);
        tracks.appendChild(row);
      });
      container.appendChild(tracks);
      const details = el("details", "genome-descriptors");
      details.appendChild(el("summary", null, translate("viewer.genome.list")));
      parsed.features.slice(0, 300).forEach((feature) => {
        const row = el("div", "genome-descriptor");
        row.appendChild(el("code", null, `${feature.chrom}:${feature.start + 1}-${feature.end}`));
        row.appendChild(el("span", "genome-type", feature.type));
        row.appendChild(el("span", "genome-label", feature.label));
        details.appendChild(row);
      });
      container.appendChild(details);
    })
    .catch(readFailure(container, a, url, () => renderGenomeTrack(container, a, url)));
}

function chemistryElementColor(element: string): string {
  return (
    ({
      C: "#38434f",
      N: "#2563eb",
      O: "#dc2626",
      S: "#ca8a04",
      P: "#ea580c",
      F: "#16a34a",
      CL: "#16a34a",
      BR: "#9a3412",
      I: "#7e22ce",
      H: "#64748b",
    } as Record<string, string>)[String(element || "").toUpperCase()] || "#475569"
  );
}

function molecule2dSvg(model: MolfileModel | null | undefined): SVGElement | null {
  if (!model || !model.atoms.length) return null;
  const xs = model.atoms.map((atom) => atom.x);
  const ys = model.atoms.map((atom) => atom.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  if (model.atoms.length > 1 && Math.abs(maxX - minX) < 1e-8 && Math.abs(maxY - minY) < 1e-8) {
    return null;
  }
  const width = 900,
    height = 520,
    pad = 64;
  const sx = Math.max(1e-6, maxX - minX);
  const sy = Math.max(1e-6, maxY - minY);
  const scale = Math.min((width - pad * 2) / sx, (height - pad * 2) / sy);
  const usedW = sx * scale;
  const usedH = sy * scale;
  const point = (atom: { x: number; y: number }) => ({
    x: (width - usedW) / 2 + (atom.x - minX) * scale,
    y: height - ((height - usedH) / 2 + (atom.y - minY) * scale),
  });
  const svg = svgElement("svg", {
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-label": model.title || "2D molecule",
  });
  model.bonds.forEach((bond) => {
    const p1 = point(model.atoms[bond.a] || { x: 0, y: 0 });
    const p2 = point(model.atoms[bond.b] || { x: 0, y: 0 });
    const dx = p2.x - p1.x;
    const dy = p2.y - p1.y;
    const length = Math.max(1, Math.hypot(dx, dy));
    const nx = (-dy / length) * 4;
    const ny = (dx / length) * 4;
    const count = Math.max(1, Math.min(3, bond.order));
    for (let index = 0; index < count; index++) {
      const offset = index - (count - 1) / 2;
      svg.appendChild(
        svgElement("line", {
          x1: p1.x + nx * offset,
          y1: p1.y + ny * offset,
          x2: p2.x + nx * offset,
          y2: p2.y + ny * offset,
          class: "chem-bond",
        }),
      );
    }
  });
  model.atoms.forEach((atom) => {
    const p = point(atom);
    svg.appendChild(svgElement("circle", { cx: p.x, cy: p.y, r: 13, class: "chem-atom-bg" }));
    const label = svgElement("text", {
      x: p.x,
      y: p.y + 5,
      class: "chem-atom",
      fill: chemistryElementColor(atom.element),
      "text-anchor": "middle",
    });
    label.textContent = atom.element;
    svg.appendChild(label);
  });
  return svg;
}

export function renderChemistry2D(container: HTMLElement, a: ArtifactRow, url: string): void {
  if (readWorkbenchFlag()) {
    const bar = el("div", "wb-ketcher-bar");
    const open = el("button", "solid-btn small", translate("wb.ketcher.edit"));
    open.onclick = () => {
      callWindow("openKetcher", a);
    };
    bar.appendChild(open);
    container.appendChild(bar);
  }
  fetchArtifactText(url, { signal: previewReadSignal(container) })
    .then((text) => {
      if (!container.isConnected) return;
      const runtime = scientificRenderers();
      const model = runtime && runtime.parseMolfile(text);
      const drawing = molecule2dSvg(model || null);
      const wrap = el("div", "chemistry-view");
      if (drawing && model) {
        const head = el(
          "div",
          "bio-summary",
          `${model.title} · ${model.atoms.length} atoms · ${model.bonds.length} bonds`,
        );
        wrap.appendChild(head);
        wrap.appendChild(drawing);
      } else {
        wrap.appendChild(el("div", "renderer-note", translate("viewer.chem.fallback")));
        const smiles = runtime ? runtime.smilesLines(text) : [];
        if (/\.(smi|smiles)$/i.test(String(a.filename || "")) && smiles.length) {
          const list = el("div", "smiles-list");
          smiles.forEach((item) => {
            const row = el("div", "smiles-row");
            row.appendChild(el("span", "smiles-name", item.name));
            row.appendChild(el("code", "smiles-code", item.smiles));
            list.appendChild(row);
          });
          wrap.appendChild(list);
        }
      }
      const details = el("details", "chem-source");
      details.appendChild(el("summary", null, translate("viewer.chem.source")));
      const slice = previewSlice(text, CHEMISTRY_SOURCE_PREVIEW_CHARACTERS);
      const pre = el("pre");
      pre.textContent = slice.shown;
      details.appendChild(pre);
      wrap.appendChild(details);
      container.appendChild(wrap);
      if (slice.truncated) partialPreview(container, a, url, slice.shown.length);
    })
    .catch(readFailure(container, a, url, () => renderChemistry2D(container, a, url)));
}

export function renderLatexArtifact(container: HTMLElement, a: ArtifactRow, url: string): void {
  fetchArtifactText(url, { signal: previewReadSignal(container) })
    .then((text) => {
      if (!container.isConnected) return;
      const runtime = scientificRenderers();
      const blocks: LatexBlock[] = runtime ? runtime.latexPreview(text) : [];
      const wrap = el("div", "latex-view");
      const tabs = el("div", "latex-tabs");
      const previewButton = el("button", "latex-tab active", translate("viewer.latex.preview"));
      const sourceButton = el("button", "latex-tab", translate("viewer.latex.source"));
      tabs.appendChild(previewButton);
      tabs.appendChild(sourceButton);
      wrap.appendChild(tabs);
      const preview = el("article", "latex-preview");
      blocks.forEach((block) => {
        const level = Math.max(2, Math.min(4, (block.level || 1) + 1));
        const node =
          block.kind === "heading"
            ? document.createElement("h" + level)
            : el(block.kind === "math" ? "div" : "p", block.kind === "math" ? "latex-math" : null);
        node.textContent = block.text;
        preview.appendChild(node);
      });
      if (!blocks.length) preview.appendChild(el("div", "renderer-note", translate("viewer.chem.fallback")));
      const slice = previewSlice(text, LATEX_SOURCE_PREVIEW_CHARACTERS);
      const source = el("pre", "renderer-source latex-source");
      source.textContent = slice.shown;
      source.classList.add("hidden");
      wrap.appendChild(preview);
      wrap.appendChild(source);
      const show = (mode: string) => {
        const isPreview = mode === "preview";
        preview.classList.toggle("hidden", !isPreview);
        source.classList.toggle("hidden", isPreview);
        previewButton.classList.toggle("active", isPreview);
        sourceButton.classList.toggle("active", !isPreview);
      };
      previewButton.onclick = () => show("preview");
      sourceButton.onclick = () => show("source");
      container.appendChild(wrap);
      if (slice.truncated) partialPreview(container, a, url, slice.shown.length);
    })
    .catch(readFailure(container, a, url, () => renderLatexArtifact(container, a, url)));
}

export function renderDownloadArtifact(container: HTMLElement, a: ArtifactRow, url: string): void {
  container.innerHTML = "";
  const ct = String(a.content_type || "").toLowerCase();
  const nm = String(a.filename || "").toLowerCase();
  if (ct.startsWith("text/") || /json|xml|javascript/.test(ct) || TEXT_EXT.test(nm)) {
    return renderTextArtifact(container, a, url);
  }
  renderDownloadCard(container, a, url);
}

/** The download tile itself, for callers that already know the bytes are not text. */
function renderDownloadCard(container: HTMLElement, a: ArtifactRow, url: string): void {
  const card = el("div", "download-artifact");
  card.appendChild(iconEl("package", 28));
  card.appendChild(el("strong", null, a.filename || "artifact"));
  card.appendChild(el("span", null, translate("viewer.downloadOnly")));
  const link = el("a", "solid-btn small", translate("common.download"));
  link.href = url;
  link.setAttribute("download", a.filename || "artifact");
  card.appendChild(link);
  container.appendChild(card);
}

function runIsland(name: string, ...args: unknown[]): boolean {
  const fn = hostWindow()[name];
  if (!isReady(fn)) return false;
  (fn as (...inner: unknown[]) => unknown)(...args);
  return true;
}

function renderImageGlue(content: HTMLElement, a: ArtifactRow, url: string): void {
  if (runIsland("renderAnnotatableImage", content, a, url)) return;
  const img = el("img");
  img.alt = a.filename || "artifact";
  content.appendChild(img);
  readArtifactBytes(url, { signal: previewReadSignal(content) })
    .then((bytes) => {
      if (!content.isConnected) return;
      img.src = previewObjectUrl(
        bytes,
        artifactMediaType(a.filename, a.content_type, "application/octet-stream"),
        content,
      );
    })
    .catch(readFailure(content, a, url, () => renderImageGlue(content, a, url)));
}

function renderPdfGlue(content: HTMLElement, a: ArtifactRow, url: string): void {
  const frame = el("iframe");
  applyArtifactIframeSandbox(frame, "pdf");
  frame.dataset.currentPage = "1";
  content.appendChild(frame);
  readArtifactBytes(url, { signal: previewReadSignal(content) })
    .then((bytes) => {
      if (!content.isConnected) return;
      frame.src = previewObjectUrl(bytes, "application/pdf", content);
    })
    .catch(readFailure(content, a, url, () => renderPdfGlue(content, a, url)));
  if (readWorkbenchFlag()) callWindow("renderLocatorComments", content, a, "pdf", frame);
}

function renderHtmlPreviewGlue(content: HTMLElement, a: ArtifactRow): void {
  renderHtmlPreview(content, a);
  if (readWorkbenchFlag()) callWindow("renderLocatorComments", content, a, "html");
}

function renderMolecule3dGlue(content: HTMLElement, a: ArtifactRow, url: string, nm: string): void {
  if (runIsland("molecule", content, url, nm)) return;
  // Degraded path when the 3Dmol island is not mounted: the coordinates as
  // text, still under the main preview budget and cancellable.
  fetchArtifactText(url, { signal: previewReadSignal(content) })
    .then((text) => {
      if (!content.isConnected) return;
      const slice = previewSlice(text, MOLECULE_FALLBACK_PREVIEW_CHARACTERS);
      const pre = el("pre");
      pre.style.padding = "16px";
      pre.textContent = slice.shown;
      content.appendChild(pre);
      if (slice.truncated) partialPreview(content, a, url, slice.shown.length);
    })
    .catch(readFailure(content, a, url, () => renderMolecule3dGlue(content, a, url, nm)));
}

export function renderArtifactDescriptor(
  body: HTMLElement,
  a: ArtifactRow,
  descriptor: ReturnType<typeof compatibilityRendererDescriptor>,
): void {
  body.innerHTML = "";
  const renderer = descriptor.renderer || {};
  const rendererId = renderer.renderer_id || "download";
  // F4: freeze the read target once for this paint. Every renderer, the
  // expansion and the download use this exact target and URL, so a version
  // published after this paint cannot move part of the preview to new bytes.
  const target = artifactMetadataTarget(a);
  const shell = el("div", "renderer-shell");
  shell.dataset.rendererId = rendererId;
  const meta = el("div", "renderer-meta");
  meta.appendChild(el("span", "renderer-name", publicText(renderer.label || rendererId, 80)));
  const match =
    descriptor.matched_by === "compatibility"
      ? translate("viewer.renderer.compat")
      : translate("viewer.renderer.matched", publicText(descriptor.matched_by || "metadata", 30));
  meta.appendChild(el("span", "renderer-detail", match));
  if (descriptor.matched_by === "compatibility") {
    // The compatibility descriptor is what a failed catalog read degrades to.
    // Retrying re-requests the catalog and the server descriptor in place, so
    // a recovered catalog (and its declared capabilities) applies without a
    // page reload; a still-failing read just degrades again.
    const retry = el("button", "outline-btn small", filesT("viewer.renderer.retry"));
    retry.onclick = () => {
      renderArtifactBody(body, a);
    };
    meta.appendChild(retry);
  }
  if (target._exactVersion) {
    meta.appendChild(
      el(
        "span",
        "renderer-version",
        translate("viewer.renderer.version", publicText(String(target.version_id), 12)),
      ),
    );
  } else {
    // No version identity: the preview reads latest, and this is visible.
    meta.appendChild(el("span", "renderer-version", filesT("viewer.preview.latest")));
  }
  shell.appendChild(meta);
  const content = el("div", "renderer-content");
  shell.appendChild(content);
  body.appendChild(shell);
  const url = artUrl(target);
  const nm = String(target.filename || "").toLowerCase();
  if (rendererId === "image") renderImageGlue(content, target, url);
  else if (rendererId === "pdf") renderPdfGlue(content, target, url);
  else if (rendererId === "html-preview") renderHtmlPreviewGlue(content, target);
  else if (rendererId === "molecule-3d") renderMolecule3dGlue(content, target, url, nm);
  else if (rendererId === "chemistry-2d") renderChemistry2D(content, target, url);
  else if (rendererId === "genome-track") renderGenomeTrack(content, target, url);
  else if (rendererId === "sequence") renderSequenceArtifact(content, target, url);
  else if (rendererId === "msa") renderAlignmentArtifact(content, target, url);
  else if (rendererId === "latex") renderLatexArtifact(content, target, url);
  else if (rendererId === "markdown") renderMarkdownArtifact(content, target, url);
  else if (rendererId === "notebook") renderNotebookArtifact(content, target, url);
  else if (rendererId === "table") renderTableArtifact(content, target, url, renderer);
  else if (rendererId === "text") renderTextArtifact(content, target, url);
  else renderDownloadArtifact(content, target, url);
}

/**
 * app.js:8637-8647.
 *
 * A descriptor paints only while this call's loading row is still in the
 * document. The guard used to be a counter on `body`, but `renderViewer`
 * builds a fresh body every time, so a late descriptor for the previous
 * artifact always matched its own counter and ran its glue: molecule()
 * tore down the 3Dmol viewer now showing, and the image glue dropped the
 * annotation draft being written. The row detaches with its body, and when
 * any later render (or another modal) clears a reused body.
 */
export function renderArtifactBody(body: HTMLElement, a: ArtifactRow): void {
  // A new artifact (or a repaint) owns the viewer: anything the previous view
  // was still reading is no longer needed, and a notebook releases the image
  // URLs it created. The new read is bound to this body, so replacing the body
  // (or closing the viewer) aborts exactly that read.
  closeNotebookPreview(body);
  // The molecule island keeps its own abort controller. Replacing the view
  // has to cancel that read here: the next renderer may not be a structure,
  // so waiting for `molecule()` would leave the previous bytes downloading.
  molTeardown();
  const signal = beginPreviewRead(body);
  body.innerHTML = "";
  const loading = el("div", "renderer-loading");
  loading.appendChild(iconEl("loader", 16, "spin"));
  loading.appendChild(el("span", null, translate("viewer.loading")));
  body.appendChild(loading);
  artifactRendererDescriptor(a)
    .then((descriptor) => {
      if (signal.aborted || !loading.isConnected) return;
      renderArtifactDescriptor(body, a, descriptor);
    })
    .catch(() => {
      if (signal.aborted || !loading.isConnected) return;
      renderArtifactDescriptor(body, a, compatibilityRendererDescriptor(a));
    });
}
