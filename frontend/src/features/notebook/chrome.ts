/**
 * Notebook chrome: traceback highlight, export split-button, live figures,
 * inline CSV tables, the shared binary-output test. Window contract names
 * assigned in install.ts.
 */

import { fetchArtifactText, isArtifactReadAborted } from "../artifacts/api";
import { filesT, readFailureText } from "../artifacts/copy";
import { paintIcon } from "../icons/paths";
import { isReady } from "../../compat/stub";
import { _tbl } from "../../stores/artifacts";
import { LANG, t } from "../../i18n/runtime";
import { delimiterFor, parseDelimited } from "../csv/csv";
import { esc } from "../md/esc";
import { mdHighlight } from "../md/highlight";
import { API } from "../ws/connect";
import type { WsMessage } from "../ws/types";
import { bindNotebookArtifact, resetCellOutputs } from "./cells";
import type { NotebookCell, NotebookOutputArtifact } from "./types";

export function el(tag: string, cls?: string | null, text?: string | null): HTMLElement {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

export function iconEl(name: string, size: number): HTMLElement {
  const fn = (globalThis as unknown as { iconEl?: unknown }).iconEl;
  if (isReady(fn)) return (fn as (n: string, s: number) => HTMLElement)(name, size);
  // Nothing installs a global iconEl, so this is the path every notebook and
  // provenance icon takes; paintIcons() has long run by then.
  const span = document.createElement("span");
  paintIcon(span, name, size);
  return span;
}

/**
 * One binary-output heuristic for the whole workbench: the Files viewer and
 * the Notebook must agree on what a protein or DNA sequence is.
 */
export { looksBinary } from "../artifacts/api";

/** app.js:10480-10481 */
export function stripAnsi(s: unknown): string {
  return String(s == null ? "" : s).replace(/\x1b\[[0-9;?]*[A-Za-z]/g, "");
}

/**
 * Colour File/line locations and the terminal ExceptionType line.
 * Port of app.js:10513-10521. Uses F-08 `esc` (quote-safe).
 */
export function highlightTraceback(txt: string): string {
  const lines = txt.split("\n");
  let lastIdx = -1;
  for (let i = lines.length - 1; i >= 0; i--) {
    if ((lines[i] || "").trim()) {
      lastIdx = i;
      break;
    }
  }
  return lines
    .map((ln, i) => {
      const e = esc(ln);
      if (/^\s*File ".*", line \d+/.test(ln)) return '<span class="tb-loc">' + e + "</span>";
      if (
        i === lastIdx &&
        /^[A-Za-z_][\w.]*(Error|Exception|Warning|Interrupt|Exit|Fault)\b/.test(ln.trim())
      ) {
        return '<span class="tb-final">' + e + "</span>";
      }
      return e;
    })
    .join("\n");
}

const highlightMemo = new Map<string, { source: string; lang: string; html: string }>();

/** Highlight only when this cell's source (or lang) actually changed. */
export function highlightCellSource(key: string, source: string, lang: string): string {
  const prev = highlightMemo.get(key);
  if (prev && prev.source === source && prev.lang === lang) return prev.html;
  const html = mdHighlight(source, lang);
  highlightMemo.set(key, { source, lang, html });
  return html;
}

export function resetHighlightMemo(): void {
  highlightMemo.clear();
}

/** Drop cell-keyed render state only when the workbench moves to another frame. */
export function resetNotebookCellCaches(
  previousFrameId: string | null | undefined,
  nextFrameId: string | null | undefined,
): void {
  if (previousFrameId === nextFrameId) return;
  resetCellOutputs();
  resetHighlightMemo();
}

export type NotebookExportOption = {
  language?: string;
  /** `clean`: failed/diagnostic cells left out, figures embedded, narration as Markdown. */
  variant?: "clean";
  key: string;
  suffix: string;
  path?: string;
  /** Feature-local label, for options the generated dictionaries do not carry. */
  label?: () => string;
};

/** Export labels the generated i18n dictionaries do not carry. */
const EXPORT_COPY = {
  en: {
    clean: "Download notebook (.ipynb)",
    raw: "Raw execution log (every cell, zip)",
    rawPython: "Raw Python execution log (.ipynb)",
    rawR: "Raw R execution log (.ipynb)",
  },
  zh: {
    clean: "下载 Notebook (.ipynb)",
    raw: "原始执行日志（全部单元，打包 .zip）",
    rawPython: "原始 Python 执行日志 (.ipynb)",
    rawR: "原始 R 执行日志 (.ipynb)",
  },
} as const;

function exportCopy(key: keyof (typeof EXPORT_COPY)["en"]): string {
  return EXPORT_COPY[LANG === "en" ? "en" : "zh"][key];
}

/**
 * The primary download is the clean notebook (owner decision): the raw forms
 * serialise every attempt -- failed cells, environment probes, no figures, no
 * narration -- and stay available as the execution log.
 */
export const NOTEBOOK_EXPORTS: NotebookExportOption[] = [
  { variant: "clean", key: "prov.exec.downloadNotebook", suffix: "ipynb", label: () => exportCopy("clean") },
  { language: "bundle", key: "prov.exec.downloadNotebook", suffix: "notebooks.zip", label: () => exportCopy("raw") },
  { language: "python", key: "prov.exec.downloadPython", suffix: "python.ipynb", label: () => exportCopy("rawPython") },
  { language: "r", key: "prov.exec.downloadR", suffix: "r.ipynb", label: () => exportCopy("rawR") },
  { language: "markdown", key: "prov.exec.downloadMarkdown", suffix: "md" },
  { path: "/execution-sources/export", key: "prov.exec.downloadSources", suffix: "sources.zip" },
];

function exportLabel(option: NotebookExportOption): string {
  return option.label ? option.label() : t(option.key);
}

/** app.js:10133-10136 */
export function notebookExportHref(frameId: string, option: NotebookExportOption): string {
  const base = `${API}/frames/${encodeURIComponent(frameId)}`;
  if (option.path) return `${base}${option.path}`;
  if (option.variant === "clean") return `${base}/notebook/export?variant=clean`;
  return `${base}/notebook/export?language=${option.language}`;
}

/** The saved name: the server's (the session title) for the clean notebook. */
function exportDownloadName(frameId: string, option: NotebookExportOption): string {
  return option.variant === "clean" ? "" : `${frameId}.${option.suffix}`;
}

/** app.js:10231-10264. Contract global. */
export function notebookExportLink(frameId: string): HTMLElement {
  const wrap = el("div", "prov-dl");
  const primary = NOTEBOOK_EXPORTS[0] as NotebookExportOption;
  const dl = el("a", "prov-dlbtn") as HTMLAnchorElement;
  dl.appendChild(iconEl("download", 14));
  dl.appendChild(el("span", null, exportLabel(primary)));
  dl.href = notebookExportHref(frameId, primary);
  dl.setAttribute("download", exportDownloadName(frameId, primary));
  wrap.appendChild(dl);

  const toggle = el("button", "prov-dlmore");
  toggle.setAttribute("aria-label", t("prov.exec.downloadMore"));
  toggle.setAttribute("aria-expanded", "false");
  toggle.appendChild(iconEl("chevron-down", 13));
  const menu = el("div", "prov-dlmenu hidden");
  NOTEBOOK_EXPORTS.slice(1).forEach((option) => {
    const item = el("a", "prov-dlitem") as HTMLAnchorElement;
    item.appendChild(el("span", null, exportLabel(option)));
    item.href = notebookExportHref(frameId, option);
    item.setAttribute("download", exportDownloadName(frameId, option));
    item.onclick = () => {
      menu.classList.add("hidden");
      toggle.setAttribute("aria-expanded", "false");
    };
    menu.appendChild(item);
  });
  toggle.onclick = (e) => {
    e.preventDefault();
    e.stopPropagation();
    const open = menu.classList.toggle("hidden") === false;
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
  };
  wrap.appendChild(toggle);
  wrap.appendChild(menu);
  return wrap;
}

export function notebookArtifactState(cell: NotebookCell, filename: string): {
  state: "confirmed" | "saving" | "unconfirmed";
  artifact?: NotebookOutputArtifact;
} {
  const matches = (cell.output_artifacts || []).filter((row) => row.filename === filename);
  const unique = new Map(matches.map((row) => [JSON.stringify([row.artifact_id, row.version_id]), row]));
  if (unique.size === 1) return { state: "confirmed", artifact: [...unique.values()][0] };
  return { state: cell.live || cell._artifactBindingsPending ? "saving" : "unconfirmed" };
}

/** app.js:9708-9744. `_tbl` lives in the artifacts store (F-06 already busts it). */
export function renderTableInto(holder: HTMLElement, fname: string, url: string): () => void {
  let disposed = false;
  const build = (rows: string[][]) => {
    if (disposed || !rows || !rows.length) return;
    const view = rows.slice(0, 51);
    const width = rows.reduce((most, r) => Math.max(most, (r || []).length), 0);
    const tbl = el("table", "nbc-table");
    const thead = el("thead");
    const htr = el("tr");
    (view[0] || []).slice(0, 24).forEach((h) => htr.appendChild(el("th", null, h)));
    thead.appendChild(htr);
    tbl.appendChild(thead);
    const tb = el("tbody");
    view.slice(1).forEach((r) => {
      const tr = el("tr");
      r.slice(0, 24).forEach((cell) => tr.appendChild(el("td", null, cell)));
      tb.appendChild(tr);
    });
    tbl.appendChild(tb);
    const scroll = el("div", "nbc-table-scroll");
    scroll.appendChild(tbl);
    holder.appendChild(scroll);
    const hiddenRows = Math.max(0, rows.length - 51);
    const hiddenCols = Math.max(0, width - 24);
    if (hiddenRows && hiddenCols) {
      holder.appendChild(el("div", "nbc-table-more", t("nb.table.bothHidden", hiddenRows, hiddenCols)));
    } else if (hiddenRows) {
      holder.appendChild(el("div", "nbc-table-more", t("nb.table.rowsHidden", hiddenRows)));
    } else if (hiddenCols) {
      holder.appendChild(el("div", "nbc-table-more", t("nb.table.colsHidden", hiddenCols)));
    }
  };
  const cache = _tbl.value || {};
  const hit = cache[url];
  if (hit) {
    build(hit as string[][]);
    return () => { disposed = true; };
  }
  const read = () => fetchArtifactText(url)
    .then((text) => {
      if (disposed) return;
      const firstLine = text.replace(/\r/g, "").split("\n", 1)[0] || "";
      const rows = parseDelimited(text, delimiterFor(fname, "", firstLine));
      cache[url] = rows;
      build(rows);
    })
    .catch((error: unknown) => {
      // A cancelled read (the cell was disposed) says nothing to the user.
      if (disposed || isArtifactReadAborted(error)) return;
      holder.innerHTML = "";
      const card = el("div", "nbc-artifact-error");
      card.setAttribute("role", "alert");
      card.appendChild(el("span", null, readFailureText(error)));
      const retry = el("button", "nbc-action", filesT("viewer.read.retry"));
      retry.onclick = () => { card.remove(); void read(); };
      card.appendChild(retry);
      const download = el("a", "nbc-action") as HTMLAnchorElement;
      download.textContent = t("common.download");
      download.href = url;
      download.setAttribute("download", fname || "artifact");
      card.appendChild(download);
      holder.appendChild(card);
    });
  void read();
  return () => { disposed = true; };
}

/**
 * Live-render a produced figure onto the current notebook cell.
 * app.js:5337-5341. `_tbl` bust is already in F-06 upsertArtifactFromEvent.
 */
export function mountLiveNotebookFigure(m: WsMessage): void {
  bindNotebookArtifact(m);
}
