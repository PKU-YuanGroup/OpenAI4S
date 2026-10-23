/**
 * Read-only `.ipynb` Artifact preview (A4, F5–F7).
 *
 * Opening a saved notebook renders only what the file already contains: cell
 * sources, saved text streams/errors and validated PNG outputs. It never
 * launches a kernel, calls a Host method, loads a widget/HTML/SVG output or
 * requests another resource; the only request is the exact-version bytes it
 * renders. `metadata.trusted` is ignored — it is not an authorization and says
 * nothing about whether the saved output is true.
 *
 * The parser is a whitelist over the nbformat-4 envelope, not a convenience
 * wrapper: the document version, `cells` array, cell and output field types are
 * checked, and a structural violation fails the whole preview instead of
 * showing a half document.
 *
 * Frozen limits (F6): 200 cells rendered 50 per batch, 20 outputs per cell,
 * 65536 code units per text block, PNG 2 MiB each, 16 million decoded pixels for
 * the whole preview, at most 2 PNG decodes in flight. The whole file was already
 * read under the 8 MiB main preview budget (F1).
 */

import { el, fetchArtifactText, iconEl, isArtifactReadAborted, translate } from "./api";
import { previewSlice } from "./cache";
import { filesT, readFailureText } from "./copy";
import type { ArtifactRow } from "./types";
import { mdCodeBlock, renderMd } from "../md";

export const NOTEBOOK_MAX_CELLS = 200;
export const NOTEBOOK_CELL_BATCH = 50;
export const NOTEBOOK_MAX_OUTPUTS_PER_CELL = 20;
export const NOTEBOOK_TEXT_LIMIT_UNITS = 65_536;
export const NOTEBOOK_PNG_MAX_BYTES = 2_097_152;
export const NOTEBOOK_MAX_PNG_PIXELS = 16_000_000;
export const NOTEBOOK_PNG_CONCURRENCY = 2;

const PNG_SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

export type ParsedOutput =
  | { kind: "text"; label: "stdout" | "stderr" | "error" | "text"; text: string; truncated: boolean }
  | { kind: "png"; base64: string }
  | { kind: "unsupported"; mime: string };

export type ParsedCell = {
  cellType: "code" | "markdown" | "raw";
  source: string;
  sourceTruncated: boolean;
  language: string;
  outputs: ParsedOutput[];
  omittedOutputs: number;
};

export type ParsedNotebook = {
  language: string;
  cells: ParsedCell[];
  /** Cells past the 200-cell limit; they are not parsed. */
  omittedCells: number;
};

export type NotebookProblem = "invalid-json" | "unsupported-version" | "invalid-shape";

export type NotebookParse =
  | { ok: true; notebook: ParsedNotebook }
  | { ok: false; reason: NotebookProblem; version: string };

export type PngInspection =
  | { ok: true; width: number; height: number; bytes: Uint8Array }
  | { ok: false; reason: "invalid-base64" | "too-large" | "not-png" | "bad-header" | "bad-dimensions" };

function record(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

/** nbformat string-or-string-array fields; anything else is a shape violation. */
export function textField(value: unknown): string | null {
  if (typeof value === "string") return value;
  if (Array.isArray(value) && value.every((item) => typeof item === "string")) {
    return (value as string[]).join("");
  }
  return null;
}

function textBlock(text: string): { text: string; truncated: boolean } {
  const slice = previewSlice(text, NOTEBOOK_TEXT_LIMIT_UNITS);
  return { text: slice.shown, truncated: slice.truncated };
}

function languageOf(metadata: unknown): string {
  const rec = record(metadata);
  if (!rec) return "";
  const info = record(rec.language_info);
  if (info) {
    const name = info.name;
    if (typeof name === "string" && name) return name;
  }
  const kernelspec = record(rec.kernelspec);
  if (kernelspec) {
    const language = kernelspec.language;
    if (typeof language === "string" && language) return language;
  }
  return "";
}

function parseOutput(value: unknown): ParsedOutput | null {
  const output = record(value);
  if (!output) return null;
  const outputType = output.output_type;
  if (typeof outputType !== "string") return null;
  if (outputType === "stream") {
    const text = textField(output.text);
    if (text === null) return null;
    return {
      kind: "text",
      label: output.name === "stderr" ? "stderr" : "stdout",
      ...textBlock(text),
    };
  }
  if (outputType === "error") {
    const ename = textField(output.ename);
    const evalue = textField(output.evalue);
    if (ename === null || evalue === null) return null;
    const traceback = textField(output.traceback);
    if (traceback === null && output.traceback !== undefined) return null;
    const head = ename + (evalue ? ": " + evalue : "");
    return { kind: "text", label: "error", ...textBlock(head + (traceback ? "\n" + traceback : "")) };
  }
  if (outputType === "execute_result" || outputType === "display_data") {
    const data = record(output.data);
    if (!data) return null;
    const png = textField(data["image/png"]);
    if (png !== null) return { kind: "png", base64: png };
    const plain = textField(data["text/plain"]);
    if (plain !== null) return { kind: "text", label: "text", ...textBlock(plain) };
    // HTML, SVG, widgets, JavaScript and every other rich MIME only get a
    // notice: rendering them would execute or fetch what the file chose.
    const mime = Object.keys(data)[0] || "unknown";
    return { kind: "unsupported", mime };
  }
  return null;
}

/**
 * Validate one nbformat-4 notebook. A structural/type violation fails the whole
 * preview (the file is damaged, so no part of it can be called complete); the
 * cell/output limits only omit their over-limit part and report how much.
 */
export function parseNotebook(text: string): NotebookParse {
  let root: unknown;
  try {
    root = JSON.parse(text);
  } catch {
    return { ok: false, reason: "invalid-json", version: "" };
  }
  const rec = record(root);
  if (!rec) return { ok: false, reason: "invalid-shape", version: "" };
  const version = rec.nbformat;
  if (version !== 4) {
    return {
      ok: false,
      reason: "unsupported-version",
      version: typeof version === "string" || typeof version === "number" ? String(version) : "",
    };
  }
  const cellsIn = rec.cells;
  if (!Array.isArray(cellsIn)) return { ok: false, reason: "invalid-shape", version: "4" };
  const language = languageOf(rec.metadata);
  const shownCells = Math.min(cellsIn.length, NOTEBOOK_MAX_CELLS);
  const cells: ParsedCell[] = [];
  for (let index = 0; index < shownCells; index++) {
    const cell = record(cellsIn[index]);
    if (!cell) return { ok: false, reason: "invalid-shape", version: "4" };
    const cellType = cell.cell_type;
    if (cellType !== "code" && cellType !== "markdown" && cellType !== "raw") {
      return { ok: false, reason: "invalid-shape", version: "4" };
    }
    const source = textField(cell.source);
    if (source === null) return { ok: false, reason: "invalid-shape", version: "4" };
    const sourceBlock = textBlock(source);
    const outputs: ParsedOutput[] = [];
    let omittedOutputs = 0;
    if (cellType === "code" && cell.outputs !== undefined) {
      if (!Array.isArray(cell.outputs)) return { ok: false, reason: "invalid-shape", version: "4" };
      const shownOutputs = Math.min(cell.outputs.length, NOTEBOOK_MAX_OUTPUTS_PER_CELL);
      omittedOutputs = Math.max(0, cell.outputs.length - shownOutputs);
      for (let out = 0; out < shownOutputs; out++) {
        const parsed = parseOutput(cell.outputs[out]);
        if (parsed === null) return { ok: false, reason: "invalid-shape", version: "4" };
        outputs.push(parsed);
      }
    }
    cells.push({
      cellType,
      source: sourceBlock.text,
      sourceTruncated: sourceBlock.truncated,
      language,
      outputs,
      omittedOutputs,
    });
  }
  return {
    ok: true,
    notebook: {
      language,
      cells,
      omittedCells: Math.max(0, cellsIn.length - shownCells),
    },
  };
}

function readUint32(bytes: Uint8Array, offset: number): number {
  return (
    ((bytes[offset] || 0) * 0x1000000 +
      ((bytes[offset + 1] || 0) << 16) +
      ((bytes[offset + 2] || 0) << 8) +
      (bytes[offset + 3] || 0)) >>>
    0
  );
}

/**
 * Check a saved PNG before any image or decoding context is created: the
 * Base64 text must be well-formed, its estimated decoded size must fit the
 * per-image budget, and the PNG signature plus the IHDR chunk must name a
 * positive width/height. A file-size limit is not a decoded-memory limit, so
 * the caller also tracks cumulative pixels.
 */
export function inspectPng(base64: string): PngInspection {
  const clean = String(base64 || "").replace(/[\r\n\t ]/g, "");
  if (!clean || /[^A-Za-z0-9+/=]/.test(clean)) return { ok: false, reason: "invalid-base64" };
  const padded = clean + "=".repeat((4 - (clean.length % 4)) % 4);
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(padded)) return { ok: false, reason: "invalid-base64" };
  let estimated = Math.floor(padded.length / 4) * 3;
  if (padded.endsWith("==")) estimated -= 2;
  else if (padded.endsWith("=")) estimated -= 1;
  // Reject before decoding: the estimate is the only way to keep a huge Base64
  // payload from becoming a huge allocation just to learn it is too large.
  if (estimated > NOTEBOOK_PNG_MAX_BYTES) return { ok: false, reason: "too-large" };
  let raw: string;
  try {
    raw = atob(padded);
  } catch {
    return { ok: false, reason: "invalid-base64" };
  }
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
  if (bytes.length < 33) return { ok: false, reason: "not-png" };
  for (let i = 0; i < PNG_SIGNATURE.length; i++) {
    if (bytes[i] !== PNG_SIGNATURE[i]) return { ok: false, reason: "not-png" };
  }
  if (bytes[12] !== 0x49 || bytes[13] !== 0x48 || bytes[14] !== 0x44 || bytes[15] !== 0x52) {
    return { ok: false, reason: "bad-header" };
  }
  if (readUint32(bytes, 8) !== 13) return { ok: false, reason: "bad-header" };
  const width = readUint32(bytes, 16);
  const height = readUint32(bytes, 20);
  if (!width || !height) return { ok: false, reason: "bad-dimensions" };
  return { ok: true, width, height, bytes };
}

type NotebookSession = {
  controller: AbortController;
  urls: Set<string>;
  container: HTMLElement | null;
};

let activeSession: NotebookSession | null = null;

/**
 * Leaving the notebook (closing the dock, opening another artifact, or a new
 * render) cancels its read, releases every image object URL it created and
 * clears the rendered cells.
 */
export function closeNotebookPreview(): void {
  const session = activeSession;
  activeSession = null;
  if (!session) return;
  session.controller.abort();
  for (const url of session.urls) {
    try {
      URL.revokeObjectURL(url);
    } catch {
      /* already released or unsupported */
    }
  }
  session.urls.clear();
  if (session.container) session.container.innerHTML = "";
  session.container = null;
}

function note(text: string): HTMLElement {
  const node = el("div", "renderer-note", text);
  node.setAttribute("role", "status");
  return node;
}

function downloadLink(url: string, a: ArtifactRow): HTMLAnchorElement {
  const link = el("a", "outline-btn small", translate("common.download"));
  link.href = url;
  link.setAttribute("download", a.filename || "artifact");
  return link;
}

function problemCard(container: HTMLElement, message: string, text: string, url: string, a: ArtifactRow): void {
  container.innerHTML = "";
  const card = el("div", "renderer-fallback");
  card.appendChild(iconEl("alert-triangle", 18));
  const body = el("div", "renderer-fallback-text", message);
  body.setAttribute("role", "alert");
  card.appendChild(body);
  card.appendChild(downloadLink(url, a));
  container.appendChild(card);
  // "Provide the original text and the download": the saved bytes are shown,
  // bounded, and never interpreted once the schema check failed. The download
  // above still serves the untouched file.
  const details = el("details", "notebook-original");
  details.appendChild(el("summary", null, filesT("notebook.originalText")));
  const pre = el("pre", "renderer-source");
  pre.textContent = previewSlice(text, 300_000).shown;
  details.appendChild(pre);
  container.appendChild(details);
}

type PngQueue = {
  session: NotebookSession;
  pixels: number;
  active: number;
  queue: Array<() => Promise<void>>;
};

/**
 * F6 decode queue: a job owns a slot until its image has decoded, so at most
 * two saved images are decoding at once. A structural failure inside a job is
 * contained (one omitted image) instead of rejecting the queue.
 */
function pumpPng(queue: PngQueue): void {
  while (queue.active < NOTEBOOK_PNG_CONCURRENCY && queue.queue.length) {
    const job = queue.queue.shift();
    if (!job) return;
    queue.active += 1;
    Promise.resolve()
      .then(job)
      .catch(() => {
        /* the job reports its own omission */
      })
      .finally(() => {
        queue.active -= 1;
        pumpPng(queue);
      });
  }
}

async function decodeImage(img: HTMLImageElement): Promise<void> {
  if (typeof img.decode !== "function") return;
  try {
    await img.decode();
  } catch {
    /* a corrupt body already passed the header check; the download remains */
  }
}

function omittedImage(holder: HTMLElement, a: ArtifactRow, url: string): void {
  holder.appendChild(note(filesT("notebook.imageOmitted")));
  holder.appendChild(downloadLink(url, a));
}

async function materializePng(
  queue: PngQueue,
  holder: HTMLElement,
  checked: Extract<PngInspection, { ok: true }>,
  a: ArtifactRow,
  url: string,
): Promise<void> {
  const canCreate =
    typeof URL !== "undefined" &&
    typeof URL.createObjectURL === "function" &&
    typeof Blob !== "undefined";
  if (!canCreate) {
    omittedImage(holder, a, url);
    return;
  }
  try {
    const blob = new Blob([checked.bytes], { type: "image/png" });
    const objectUrl = URL.createObjectURL(blob);
    queue.session.urls.add(objectUrl);
    const img = el("img", "notebook-png");
    img.alt = filesT("notebook.pngAlt");
    img.src = objectUrl;
    holder.appendChild(img);
    await decodeImage(img);
  } catch {
    omittedImage(holder, a, url);
  }
}

function schedulePng(
  queue: PngQueue,
  holder: HTMLElement,
  base64: string,
  a: ArtifactRow,
  url: string,
): void {
  queue.queue.push(async () => {
    // The preview may have been closed between scheduling and this microtask
    // (dock closed, another artifact opened). Creating an object URL then would
    // leak an image the session no longer tracks.
    if (activeSession !== queue.session) return;
    const checked = inspectPng(base64);
    if (!checked.ok) {
      omittedImage(holder, a, url);
      return;
    }
    const pixels = checked.width * checked.height;
    // The file-byte limit is not the decoded-memory limit: the whole preview
    // may only materialize 16 million pixels, however many small images.
    if (queue.pixels + pixels > NOTEBOOK_MAX_PNG_PIXELS) {
      omittedImage(holder, a, url);
      return;
    }
    queue.pixels += pixels;
    await materializePng(queue, holder, checked, a, url);
  });
  pumpPng(queue);
}

function renderCellOutputs(
  cell: ParsedCell,
  cellNode: HTMLElement,
  queue: PngQueue,
  a: ArtifactRow,
  url: string,
): void {
  for (const output of cell.outputs) {
    if (output.kind === "text") {
      const holder = el("div", "notebook-output notebook-output-text");
      holder.appendChild(el("div", "notebook-output-label", output.label));
      const pre = el("pre", "renderer-source notebook-output-pre");
      // Saved streams/errors are plain text; `[OpenAI4S artifacts] …` lines in
      // exported notebooks therefore never request the file they name.
      pre.textContent = output.text;
      holder.appendChild(pre);
      if (output.truncated) holder.appendChild(note(filesT("notebook.textTruncated")));
      cellNode.appendChild(holder);
      continue;
    }
    if (output.kind === "png") {
      const holder = el("div", "notebook-output notebook-output-image");
      cellNode.appendChild(holder);
      schedulePng(queue, holder, output.base64, a, url);
      continue;
    }
    const holder = el("div", "notebook-output notebook-output-unsupported");
    holder.appendChild(note(filesT("notebook.mimeUnsupported", previewSlice(String(output.mime), 80).shown)));
    holder.appendChild(downloadLink(url, a));
    cellNode.appendChild(holder);
  }
  if (cell.omittedOutputs) {
    cellNode.appendChild(note(filesT("notebook.outputsOmitted", cell.omittedOutputs)));
  }
}

function renderCell(
  cell: ParsedCell,
  index: number,
  queue: PngQueue,
  a: ArtifactRow,
  url: string,
): HTMLElement {
  const section = el("section", "notebook-cell notebook-cell-" + cell.cellType);
  section.setAttribute("data-cell-type", cell.cellType);
  const head = el("div", "notebook-cell-head");
  head.appendChild(el("span", "notebook-cell-index", "#" + (index + 1)));
  head.appendChild(el("span", "notebook-cell-type", cell.cellType));
  if (cell.language) head.appendChild(el("span", "notebook-cell-lang", cell.language));
  section.appendChild(head);
  if (cell.cellType === "markdown") {
    const md = el("div", "md notebook-markdown");
    // F7: no image node (including an inline data: image) and no navigable
    // link can be produced from the saved markdown.
    md.innerHTML = renderMd(cell.source, { resources: "none" });
    section.appendChild(md);
  } else if (cell.cellType === "code") {
    // F5: the escaped/highlighted code block carries the Copy button, so the
    // renderer's `copy` capability is implemented rather than claimed.
    const code = el("div", "notebook-code");
    code.innerHTML = mdCodeBlock(cell.source, cell.language || "text");
    section.appendChild(code);
  } else {
    const pre = el("pre", "renderer-source notebook-source");
    pre.textContent = cell.source;
    section.appendChild(pre);
  }
  if (cell.sourceTruncated) section.appendChild(note(filesT("notebook.textTruncated")));
  if (cell.cellType === "code") renderCellOutputs(cell, section, queue, a, url);
  return section;
}

function renderParsedNotebook(
  container: HTMLElement,
  a: ArtifactRow,
  url: string,
  notebook: ParsedNotebook,
  session: NotebookSession,
): void {
  const shell = el("div", "notebook-preview");
  const banner = el("div", "renderer-note notebook-readonly", filesT("notebook.readonly"));
  banner.setAttribute("role", "note");
  shell.appendChild(banner);
  shell.appendChild(
    el(
      "div",
      "notebook-meta",
      filesT(
        "notebook.meta",
        Math.min(notebook.cells.length, NOTEBOOK_MAX_CELLS).toLocaleString(),
        notebook.language || filesT("notebook.languageUnknown"),
      ),
    ),
  );
  if (!notebook.cells.length) shell.appendChild(note(filesT("notebook.empty")));
  const cellsHost = el("div", "notebook-cells");
  shell.appendChild(cellsHost);
  const more = el("button", "outline-btn small notebook-more", "");
  more.hidden = true;
  shell.appendChild(more);
  const queue: PngQueue = { session, pixels: 0, active: 0, queue: [] };
  let rendered = 0;
  const paintBatch = (): void => {
    const end = Math.min(rendered + NOTEBOOK_CELL_BATCH, notebook.cells.length);
    for (let index = rendered; index < end; index++) {
      cellsHost.appendChild(renderCell(notebook.cells[index]!, index, queue, a, url));
    }
    rendered = end;
    const remaining = notebook.cells.length - rendered;
    more.hidden = remaining === 0;
    if (remaining > 0) more.textContent = filesT("notebook.showMore", remaining.toLocaleString());
  };
  more.onclick = () => {
    paintBatch();
  };
  paintBatch();
  if (notebook.omittedCells) {
    shell.appendChild(note(filesT("notebook.cellsOmitted", notebook.omittedCells)));
    shell.appendChild(downloadLink(url, a));
  }
  container.appendChild(shell);
}

/**
 * Render one exact notebook version into `container`. The read is strict (A1),
 * cancelled when the preview is replaced or closed; a structural failure shows
 * "cannot preview (not nbformat 4)" with the original text and download.
 */
export function renderNotebookArtifact(container: HTMLElement, a: ArtifactRow, url: string): void {
  closeNotebookPreview();
  const session: NotebookSession = {
    controller: new AbortController(),
    urls: new Set<string>(),
    container,
  };
  activeSession = session;
  const retry = (): void => {
    closeNotebookPreview();
    renderNotebookArtifact(container, a, url);
  };
  fetchArtifactText(url, { signal: session.controller.signal })
    .then((text) => {
      if (!container.isConnected || activeSession !== session) return;
      const parsed = parseNotebook(text);
      if (!parsed.ok) {
        container.innerHTML = "";
        problemCard(container, filesT("notebook.problem.invalid"), text, url, a);
        void retry;
        return;
      }
      renderParsedNotebook(container, a, url, parsed.notebook, session);
    })
    .catch((error: unknown) => {
      if (isArtifactReadAborted(error) || activeSession !== session) return;
      if (!container.isConnected) return;
      container.innerHTML = "";
      const card = el("div", "renderer-fallback");
      card.appendChild(iconEl("alert-triangle", 18));
      const body = el("div", "renderer-fallback-text", readFailureText(error));
      body.setAttribute("role", "alert");
      card.appendChild(body);
      const again = el("button", "outline-btn small", filesT("viewer.read.retry"));
      again.onclick = () => {
        container.innerHTML = "";
        retry();
      };
      card.appendChild(again);
      card.appendChild(downloadLink(url, a));
      container.appendChild(card);
    });
}
