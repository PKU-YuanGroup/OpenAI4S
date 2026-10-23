/**
 * Read-only `.ipynb` Artifact preview (A3, report R4).
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
 * checked, and every limit below is enforced with a visible omission note.
 *
 * Frozen limits (PLAN §2.4): 200 cells, 20 outputs per cell, one text display
 * 64 KiB, PNG 2 MiB each, 16 million decoded pixels for the whole preview, at
 * most 2 PNG decodes in flight. The whole file was already read under the
 * 8 MiB main preview budget.
 */

import {
  el,
  fetchArtifactText,
  iconEl,
  isArtifactReadCancelled,
  translate,
  unicodeSafeSlice,
} from "./api";
import { filesT, readFailureText } from "./copy";
import type { ArtifactRow } from "./types";
import { renderMd } from "../md/render";

export const NOTEBOOK_MAX_CELLS = 200;
export const NOTEBOOK_MAX_OUTPUTS_PER_CELL = 20;
export const NOTEBOOK_TEXT_LIMIT_BYTES = 64 * 1024;
export const NOTEBOOK_PNG_MAX_BYTES = 2 * 1024 * 1024;
export const NOTEBOOK_MAX_PNG_PIXELS = 16_000_000;
export const NOTEBOOK_PNG_CONCURRENCY = 2;

const PNG_SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

export type NotebookText = { text: string; truncated: boolean };

export type ParsedOutput =
  | { kind: "text"; label: "stdout" | "stderr" | "error" | "text"; value: NotebookText }
  | { kind: "png"; base64: string }
  | { kind: "unsupported"; mime: string };

export type ParsedCell = {
  cellType: "code" | "markdown" | "raw" | "unknown";
  language: string;
  source: NotebookText;
  outputs: ParsedOutput[];
  omittedOutputs: number;
};

export type ParsedNotebook = {
  language: string;
  cells: ParsedCell[];
  omittedCells: number;
};

export type NotebookProblem = "invalid-json" | "unsupported-version" | "invalid-shape";

export type NotebookParse =
  | { ok: true; notebook: ParsedNotebook }
  | { ok: false; reason: NotebookProblem; version: string };

export type PngInspection =
  | { ok: true; width: number; height: number; bytes: Uint8Array }
  | { ok: false; reason: "invalid-base64" | "too-large" | "not-png" | "bad-header" | "bad-dimensions" };

function utf8Length(text: string): number {
  if (typeof TextEncoder !== "undefined") return new TextEncoder().encode(text).length;
  // No encoder in this environment: a UTF-16 length is an upper bound for the
  // limit below, so truncation stays within the byte budget either way.
  return text.length * 3;
}

/** Keep a single text display inside its UTF-8 byte budget without splitting a surrogate pair. */
export function truncateTextBytes(text: string, limit: number = NOTEBOOK_TEXT_LIMIT_BYTES): NotebookText {
  if (utf8Length(text) <= limit) return { text, truncated: false };
  let low = 0;
  let high = text.length;
  while (low < high) {
    const mid = (low + high + 1) >> 1;
    if (utf8Length(text.slice(0, mid)) <= limit) low = mid;
    else high = mid - 1;
  }
  return { text: unicodeSafeSlice(text, low), truncated: true };
}

/** nbformat string-or-string-array fields; anything else is a shape error. */
export function textField(value: unknown): string | null {
  if (typeof value === "string") return value;
  if (Array.isArray(value) && value.every((item) => typeof item === "string")) {
    return (value as string[]).join("");
  }
  return null;
}

function parseOutput(value: unknown): ParsedOutput | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const output = value as Record<string, unknown>;
  const outputType = output.output_type;
  if (typeof outputType !== "string") return null;
  if (outputType === "stream") {
    const text = textField(output.text);
    if (text === null) return null;
    return {
      kind: "text",
      label: output.name === "stderr" ? "stderr" : "stdout",
      value: truncateTextBytes(text),
    };
  }
  if (outputType === "error") {
    const ename = textField(output.ename);
    const evalue = textField(output.evalue);
    const traceback = textField(output.traceback);
    if (ename === null || evalue === null || traceback === null) return null;
    const head = ename + (evalue ? ": " + evalue : "");
    return { kind: "text", label: "error", value: truncateTextBytes(head + (traceback ? "\n" + traceback : "")) };
  }
  if (outputType === "execute_result" || outputType === "display_data") {
    const data = output.data;
    if (!data || typeof data !== "object" || Array.isArray(data)) return null;
    const bundle = data as Record<string, unknown>;
    const png = textField(bundle["image/png"]);
    if (png !== null) return { kind: "png", base64: png };
    const plain = textField(bundle["text/plain"]);
    if (plain !== null) return { kind: "text", label: "text", value: truncateTextBytes(plain) };
    // HTML, SVG, widgets, JavaScript and every other rich MIME only get a
    // notice: rendering them would execute or fetch what the file chose.
    const mime = Object.keys(bundle)[0] || "unknown";
    return { kind: "unsupported", mime };
  }
  return { kind: "unsupported", mime: outputType };
}

function languageOf(metadata: unknown): string {
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) return "";
  const rec = metadata as Record<string, unknown>;
  const kernelspec = rec.kernelspec;
  if (kernelspec && typeof kernelspec === "object" && !Array.isArray(kernelspec)) {
    const language = (kernelspec as Record<string, unknown>).language;
    if (typeof language === "string" && language) return language;
  }
  const info = rec.language_info;
  if (info && typeof info === "object" && !Array.isArray(info)) {
    const name = (info as Record<string, unknown>).name;
    if (typeof name === "string" && name) return name;
  }
  return "";
}

/**
 * Validate one nbformat-4 notebook. A structural/type violation fails the whole
 * preview (the file is damaged, so no part of it can be called complete); a
 * limit only omits the part over it and reports how much.
 */
export function parseNotebook(text: string): NotebookParse {
  let root: unknown;
  try {
    root = JSON.parse(text);
  } catch {
    return { ok: false, reason: "invalid-json", version: "" };
  }
  if (!root || typeof root !== "object" || Array.isArray(root)) {
    return { ok: false, reason: "invalid-shape", version: "" };
  }
  const rec = root as Record<string, unknown>;
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
    const raw = cellsIn[index];
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
      return { ok: false, reason: "invalid-shape", version: "4" };
    }
    const cell = raw as Record<string, unknown>;
    const cellType = cell.cell_type;
    if (typeof cellType !== "string") return { ok: false, reason: "invalid-shape", version: "4" };
    const source = textField(cell.source);
    if (source === null) return { ok: false, reason: "invalid-shape", version: "4" };
    const parsedType: ParsedCell["cellType"] =
      cellType === "code" || cellType === "markdown" || cellType === "raw" ? cellType : "unknown";
    const outputs: ParsedOutput[] = [];
    let omittedOutputs = 0;
    if (parsedType === "code" && cell.outputs !== undefined) {
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
      cellType: parsedType,
      language,
      source: truncateTextBytes(source),
      outputs,
      omittedOutputs,
    });
  }
  return {
    ok: true,
    notebook: { language, cells, omittedCells: Math.max(0, cellsIn.length - shownCells) },
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
 * Base64 text must be well-formed, the decoded bytes must stay inside the
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
  // Reject before decoding: a 100 MB image must not become a 100 MB allocation
  // just to learn it is too large.
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
 * render) cancels its read and releases every image URL it created.
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
  session.container = null;
}

export type NotebookFailureRenderer = (
  container: HTMLElement,
  a: ArtifactRow,
  url: string,
  error?: unknown,
  retry?: () => void,
) => void;

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

function problemMessage(reason: NotebookProblem, version: string): string {
  if (reason === "invalid-json") return filesT("notebook.problem.invalidJson");
  if (reason === "unsupported-version") return filesT("notebook.problem.version", version || "?");
  return filesT("notebook.problem.shape");
}

function renderNotebookProblem(
  container: HTMLElement,
  a: ArtifactRow,
  url: string,
  message: string,
  retry: () => void,
): void {
  container.innerHTML = "";
  const card = el("div", "renderer-fallback");
  card.appendChild(iconEl("alert-triangle", 18));
  const text = el("div", "renderer-fallback-text", message);
  text.setAttribute("role", "alert");
  card.appendChild(text);
  const again = el("button", "outline-btn small", filesT("viewer.read.retry"));
  again.onclick = () => {
    container.innerHTML = "";
    retry();
  };
  card.appendChild(again);
  card.appendChild(downloadLink(url, a));
  container.appendChild(card);
}

type PngQueue = {
  session: NotebookSession;
  pixels: number;
  active: number;
  queue: Array<() => void>;
};

function pumpPng(queue: PngQueue): void {
  while (queue.active < NOTEBOOK_PNG_CONCURRENCY && queue.queue.length) {
    const job = queue.queue.shift();
    if (!job) return;
    queue.active += 1;
    Promise.resolve().then(() => {
      try {
        job();
      } finally {
        queue.active -= 1;
        pumpPng(queue);
      }
    });
  }
}

function schedulePng(
  queue: PngQueue,
  holder: HTMLElement,
  base64: string,
  a: ArtifactRow,
  url: string,
): void {
  queue.queue.push(() => {
    // The preview may have been closed between scheduling and this microtask
    // (dock closed, another artifact opened). Creating an object URL then
    // would leak an image the session no longer tracks.
    if (activeSession !== queue.session) return;
    const checked = inspectPng(base64);
    if (!checked.ok) {
      const reason =
        checked.reason === "too-large"
          ? filesT("notebook.imageLimitBytes")
          : filesT("notebook.imageInvalid");
      holder.appendChild(note(reason));
      holder.appendChild(downloadLink(url, a));
      return;
    }
    const pixels = checked.width * checked.height;
    // The file-byte limit is not the decoded-memory limit: the whole preview
    // may only materialize 16 million pixels, however many small files.
    if (queue.pixels + pixels > NOTEBOOK_MAX_PNG_PIXELS) {
      holder.appendChild(note(filesT("notebook.imageLimitPixels")));
      holder.appendChild(downloadLink(url, a));
      return;
    }
    queue.pixels += pixels;
    const canCreate =
      typeof URL !== "undefined" &&
      typeof URL.createObjectURL === "function" &&
      typeof Blob !== "undefined";
    if (!canCreate) {
      holder.appendChild(note(filesT("notebook.imageUnavailable")));
      holder.appendChild(downloadLink(url, a));
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
    } catch {
      // A DOM/storage failure while materializing one image must not reject the
      // queue microtask (an unhandled rejection) or blank the rest of the file.
      holder.appendChild(note(filesT("notebook.imageUnavailable")));
      holder.appendChild(downloadLink(url, a));
    }
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
      pre.textContent = output.value.text;
      holder.appendChild(pre);
      if (output.value.truncated) holder.appendChild(note(filesT("notebook.textTruncated")));
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
    holder.appendChild(
      note(filesT("notebook.mimeUnsupported", unicodeSafeSlice(String(output.mime), 80))),
    );
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
    // The file's markdown is rendered with resources disabled: no image node
    // (including an inline data: image) and no navigable link is produced.
    md.innerHTML = renderMd(cell.source.text, {
      plainResources: true,
      omittedResourceText: filesT("notebook.imageOmitted"),
    });
    section.appendChild(md);
  } else {
    const pre = el("pre", "renderer-source notebook-source");
    pre.textContent = cell.source.text;
    section.appendChild(pre);
  }
  if (cell.source.truncated) section.appendChild(note(filesT("notebook.textTruncated")));
  if (cell.cellType === "code") renderCellOutputs(cell, section, queue, a, url);
  if (cell.cellType === "unknown") {
    section.appendChild(note(filesT("notebook.unknownCell")));
  }
  return section;
}

/**
 * Render one exact notebook version into `container`. `onFailure` lets the
 * caller reuse the viewer's classified failure card; without it a local card
 * with the same information is rendered.
 */
export function renderNotebookArtifact(
  container: HTMLElement,
  a: ArtifactRow,
  url: string,
  onFailure?: NotebookFailureRenderer,
): void {
  closeNotebookPreview();
  const session: NotebookSession = {
    controller: new AbortController(),
    urls: new Set<string>(),
    container,
  };
  activeSession = session;
  const retry = (): void => {
    closeNotebookPreview();
    renderNotebookArtifact(container, a, url, onFailure);
  };
  fetchArtifactText(url, {
    signal: session.controller.signal,
    declaredBytes: a.size_bytes,
  })
    .then((text) => {
      if (!container.isConnected || activeSession !== session) return;
      const parsed = parseNotebook(text);
      if (!parsed.ok) {
        renderNotebookProblem(container, a, url, problemMessage(parsed.reason, parsed.version), retry);
        return;
      }
      renderParsedNotebook(container, a, url, parsed.notebook, session, retry, onFailure);
    })
    .catch((error: unknown) => {
      if (isArtifactReadCancelled(error) || activeSession !== session) return;
      if (!container.isConnected) return;
      if (onFailure) onFailure(container, a, url, error, retry);
      else renderNotebookProblem(container, a, url, readFailureText(error), retry);
    });
}

function renderParsedNotebook(
  container: HTMLElement,
  a: ArtifactRow,
  url: string,
  notebook: ParsedNotebook,
  session: NotebookSession,
  retry: () => void,
  onFailure?: NotebookFailureRenderer,
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
        notebook.cells.length.toLocaleString(),
        notebook.language || filesT("notebook.languageUnknown"),
      ),
    ),
  );
  if (notebook.omittedCells) {
    shell.appendChild(note(filesT("notebook.cellsOmitted", notebook.omittedCells)));
  }
  if (!notebook.cells.length) shell.appendChild(note(filesT("notebook.empty")));
  const queue: PngQueue = { session, pixels: 0, active: 0, queue: [] };
  notebook.cells.forEach((cell, index) => {
    shell.appendChild(renderCell(cell, index, queue, a, url));
  });
  container.appendChild(shell);
  void retry;
  void onFailure;
}
