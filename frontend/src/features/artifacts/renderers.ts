import { isReady } from "../../compat/stub";
import { parseTable } from "../csv/csv";
import { renderMd } from "../md/render";
import { publicText } from "../scrub/scrub";
import { renderTableArtifact as renderTableArtifactM04 } from "../table";
import { artifactWorkbench, _kc } from "../../stores/notebook";
import { sandboxOrigin } from "../../stores/session";
import { applyArtifactIframeSandbox, htmlPreviewSrc } from "../../islands/frames";
import {
  callWindow,
  el,
  fetchArtifactText,
  hostWindow,
  iconEl,
  looksBinary,
  svgElement,
  translate,
} from "./api";
import { artUrl } from "./cache";
import {
  artifactRendererDescriptor,
  compatibilityRendererDescriptor,
  scientificRenderers,
  type GenomeFeature,
  type LatexBlock,
  type MolfileModel,
} from "./catalog";
import { renderSheet } from "./sheet";
import type { ArtifactRow } from "./types";
import { TEXT_EXT } from "./types";

type RendererHost = HTMLElement & { _rendererRequest?: number };

/** app.js:8710 */
export function artifactWorkbenchOn(): boolean {
  if (artifactWorkbench.value) return true;
  const st = _kc.value.st;
  if (st && typeof st === "object" && (st as { artifact_workbench?: unknown }).artifact_workbench) {
    return true;
  }
  return false;
}

export function rendererFailure(container: HTMLElement, a: ArtifactRow, url: string): void {
  container.innerHTML = "";
  const card = el("div", "renderer-fallback");
  card.appendChild(iconEl("alert-triangle", 18));
  card.appendChild(el("div", "renderer-fallback-text", translate("viewer.renderer.error")));
  const download = el("a", "outline-btn small", translate("common.download"));
  download.href = url;
  download.setAttribute("download", a.filename || "artifact");
  card.appendChild(download);
  container.appendChild(card);
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

export function renderMarkdownArtifact(container: HTMLElement, url: string): void {
  fetchArtifactText(url)
    .then((text) => {
      if (!container.isConnected) return;
      const markdown = el("div", "md renderer-markdown");
      markdown.innerHTML = renderMd(text.slice(0, 1000000));
      container.appendChild(markdown);
    })
    .catch(() => rendererFailure(container, { id: "", filename: "artifact" }, url));
}

export function renderStructuredText(
  container: HTMLElement,
  a: ArtifactRow,
  text: string,
): void {
  const rows = parseTable(text, a);
  if (!rows || !rows.length) {
    const pre = el("pre", "renderer-source");
    pre.textContent = text.slice(0, 300000);
    container.appendChild(pre);
    return;
  }
  renderSheet(container, rows);
}

export function renderTextArtifact(container: HTMLElement, a: ArtifactRow, url: string): void {
  fetchArtifactText(url)
    .then((text) => {
      if (!container.isConnected) return;
      if (looksBinary(text)) return renderDownloadArtifact(container, a, url);
      const ct = String(a.content_type || "").toLowerCase();
      const nm = String(a.filename || "").toLowerCase();
      if (/json/.test(ct) || /\.json$/i.test(nm)) return renderStructuredText(container, a, text);
      const pre = el("pre", "renderer-source");
      pre.textContent = text.slice(0, 300000);
      container.appendChild(pre);
    })
    .catch(() => rendererFailure(container, a, url));
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
  fetchArtifactText(url)
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
    .catch(() => rendererFailure(container, a, url));
}

export function renderAlignmentArtifact(container: HTMLElement, a: ArtifactRow, url: string): void {
  fetchArtifactText(url)
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
    .catch(() => rendererFailure(container, a, url));
}

export function renderGenomeTrack(container: HTMLElement, a: ArtifactRow, url: string): void {
  fetchArtifactText(url)
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
    .catch(() => rendererFailure(container, a, url));
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
  if (artifactWorkbenchOn()) {
    const bar = el("div", "wb-ketcher-bar");
    const open = el("button", "solid-btn small", translate("wb.ketcher.edit"));
    open.onclick = () => {
      callWindow("openKetcher", a);
    };
    bar.appendChild(open);
    container.appendChild(bar);
  }
  fetchArtifactText(url)
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
      const pre = el("pre");
      pre.textContent = text.slice(0, 300000);
      details.appendChild(pre);
      wrap.appendChild(details);
      container.appendChild(wrap);
    })
    .catch(() => rendererFailure(container, a, url));
}

export function renderLatexArtifact(container: HTMLElement, a: ArtifactRow, url: string): void {
  fetchArtifactText(url)
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
      const source = el("pre", "renderer-source latex-source");
      source.textContent = text.slice(0, 500000);
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
    })
    .catch(() => rendererFailure(container, a, url));
}

export function renderDownloadArtifact(container: HTMLElement, a: ArtifactRow, url: string): void {
  container.innerHTML = "";
  const ct = String(a.content_type || "").toLowerCase();
  const nm = String(a.filename || "").toLowerCase();
  if (ct.startsWith("text/") || /json|xml|javascript/.test(ct) || TEXT_EXT.test(nm)) {
    return renderTextArtifact(container, a, url);
  }
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
  img.src = url;
  img.alt = a.filename || "artifact";
  content.appendChild(img);
}

function renderPdfGlue(content: HTMLElement, a: ArtifactRow, url: string): void {
  const frame = el("iframe");
  applyArtifactIframeSandbox(frame, "pdf");
  frame.dataset.currentPage = "1";
  frame.src = url + "#page=1";
  content.appendChild(frame);
  if (artifactWorkbenchOn()) callWindow("renderLocatorComments", content, a, "pdf", frame);
}

function renderHtmlPreviewGlue(content: HTMLElement, a: ArtifactRow): void {
  const frame = el("iframe");
  applyArtifactIframeSandbox(frame, "html-preview");
  frame.src = htmlPreviewSrc(sandboxOrigin.value || "", a.id);
  content.appendChild(frame);
  content.appendChild(el("p", "muted renderer-noscript", translate("viewer.renderer.noscript")));
  if (artifactWorkbenchOn()) callWindow("renderLocatorComments", content, a, "html");
}

function renderMolecule3dGlue(content: HTMLElement, url: string, nm: string): void {
  if (runIsland("molecule", content, url, nm)) return;
  fetchArtifactText(url)
    .then((text) => {
      if (!content.isConnected) return;
      const pre = el("pre");
      pre.style.padding = "16px";
      pre.textContent = text.slice(0, 8000);
      content.appendChild(pre);
    })
    .catch(() => {
      /* F-18 3Dmol island not mounted; bytes still downloadable via chrome */
    });
}

export function renderArtifactDescriptor(
  body: HTMLElement,
  a: ArtifactRow,
  descriptor: ReturnType<typeof compatibilityRendererDescriptor>,
): void {
  body.innerHTML = "";
  const renderer = descriptor.renderer || {};
  const rendererId = renderer.renderer_id || "download";
  const shell = el("div", "renderer-shell");
  shell.dataset.rendererId = rendererId;
  const meta = el("div", "renderer-meta");
  meta.appendChild(el("span", "renderer-name", publicText(renderer.label || rendererId, 80)));
  const match =
    descriptor.matched_by === "compatibility"
      ? translate("viewer.renderer.compat")
      : translate("viewer.renderer.matched", publicText(descriptor.matched_by || "metadata", 30));
  meta.appendChild(el("span", "renderer-detail", match));
  if (descriptor.version_id) {
    meta.appendChild(
      el(
        "span",
        "renderer-version",
        translate(
          "viewer.renderer.version",
          publicText(String(descriptor.version_id).slice(0, 10), 12),
        ),
      ),
    );
  }
  shell.appendChild(meta);
  const content = el("div", "renderer-content");
  shell.appendChild(content);
  body.appendChild(shell);
  const url = artUrl(a);
  const nm = String(a.filename || "").toLowerCase();
  if (rendererId === "image") renderImageGlue(content, a, url);
  else if (rendererId === "pdf") renderPdfGlue(content, a, url);
  else if (rendererId === "html-preview") renderHtmlPreviewGlue(content, a);
  else if (rendererId === "molecule-3d") renderMolecule3dGlue(content, url, nm);
  else if (rendererId === "chemistry-2d") renderChemistry2D(content, a, url);
  else if (rendererId === "genome-track") renderGenomeTrack(content, a, url);
  else if (rendererId === "sequence") renderSequenceArtifact(content, a, url);
  else if (rendererId === "msa") renderAlignmentArtifact(content, a, url);
  else if (rendererId === "latex") renderLatexArtifact(content, a, url);
  else if (rendererId === "markdown") renderMarkdownArtifact(content, url);
  else if (rendererId === "table") renderTableArtifact(content, a, url, renderer);
  else if (rendererId === "text") renderTextArtifact(content, a, url);
  else renderDownloadArtifact(content, a, url);
}

/** app.js:8637-8647 */
export function renderArtifactBody(body: HTMLElement, a: ArtifactRow): void {
  const host = body as RendererHost;
  const request = (host._rendererRequest = (host._rendererRequest || 0) + 1);
  body.innerHTML = "";
  const loading = el("div", "renderer-loading");
  loading.appendChild(iconEl("loader", 16, "spin"));
  loading.appendChild(el("span", null, translate("viewer.loading")));
  body.appendChild(loading);
  artifactRendererDescriptor(a)
    .then((descriptor) => {
      if (host._rendererRequest !== request) return;
      renderArtifactDescriptor(body, a, descriptor);
    })
    .catch(() => {
      if (host._rendererRequest !== request) return;
      renderArtifactDescriptor(body, a, compatibilityRendererDescriptor(a));
    });
}
