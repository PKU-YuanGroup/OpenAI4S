/**
 * Notebook chrome: traceback highlight, export split-button, live figures,
 * inline CSV tables, binary elision. Window contract names assigned in install.ts.
 */

import { isReady } from "../../compat/stub";
import { _artBust, _tbl, artifacts } from "../../stores/artifacts";
import { _liveCell, liveCells } from "../../stores/notebook";
import { running } from "../../stores/stream";
import { t } from "../../i18n/runtime";
import { delimiterFor, parseDelimited } from "../csv/csv";
import { esc } from "../md/esc";
import { mdHighlight } from "../md/highlight";
import { API } from "../ws/connect";
import type { WsMessage } from "../ws/types";
import { asCells, nbFindCell, resetCellOutputs, syncCellOutput } from "./cells";
import { nbRender } from "./scroll";
import type { NotebookCell } from "./types";

export function el(tag: string, cls?: string | null, text?: string | null): HTMLElement {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

export function iconEl(name: string, size: number): HTMLElement {
  const fn = (globalThis as unknown as { iconEl?: unknown }).iconEl;
  if (isReady(fn)) return (fn as (n: string, s: number) => HTMLElement)(name, size);
  const span = document.createElement("span");
  span.setAttribute("data-icon", name);
  span.setAttribute("data-icon-size", String(size));
  return span;
}

export function bytes(b: number): string {
  b = b || 0;
  if (b < 1024) return b + " B";
  if (b < 1048576) return (b / 1024).toFixed(1) + " KB";
  return (b / 1048576).toFixed(1) + " MB";
}

/** app.js:6055-6066 */
export function looksBinary(s: string | null | undefined): boolean {
  if (!s) return false;
  const sample = s.slice(0, 4096);
  let ctrl = 0;
  for (let i = 0; i < sample.length; i++) {
    const c = sample.charCodeAt(i);
    if (c === 9 || c === 10 || c === 13) continue;
    if (c < 32 || c === 127 || c === 0xfffd) ctrl++;
  }
  if (sample.length && ctrl / sample.length > 0.12) return true;
  return /[A-Za-z0-9+/=]{1200,}/.test(s) || /(?:\\x[0-9a-fA-F]{2}){400,}/.test(s);
}

export function binElide(len: number): HTMLElement {
  const d = el("div", "bin-elide");
  d.appendChild(iconEl("file", 13));
  d.appendChild(el("span", null, t("output.binaryElided", bytes(len || 0))));
  return d;
}

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
  key: string;
  suffix: string;
  path?: string;
};

/** app.js:10119-10132 */
export const NOTEBOOK_EXPORTS: NotebookExportOption[] = [
  { language: "bundle", key: "prov.exec.downloadNotebook", suffix: "notebooks.zip" },
  { language: "python", key: "prov.exec.downloadPython", suffix: "python.ipynb" },
  { language: "r", key: "prov.exec.downloadR", suffix: "r.ipynb" },
  { language: "markdown", key: "prov.exec.downloadMarkdown", suffix: "md" },
  { path: "/execution-sources/export", key: "prov.exec.downloadSources", suffix: "sources.zip" },
];

/** app.js:10133-10136 */
export function notebookExportHref(frameId: string, option: NotebookExportOption): string {
  const base = `${API}/frames/${encodeURIComponent(frameId)}`;
  return option.path ? `${base}${option.path}` : `${base}/notebook/export?language=${option.language}`;
}

/** app.js:10231-10264. Contract global. */
export function notebookExportLink(frameId: string): HTMLElement {
  const wrap = el("div", "prov-dl");
  const primary = NOTEBOOK_EXPORTS[0] as NotebookExportOption;
  const dl = el("a", "prov-dlbtn") as HTMLAnchorElement;
  dl.appendChild(iconEl("download", 14));
  dl.appendChild(el("span", null, t(primary.key)));
  dl.href = notebookExportHref(frameId, primary);
  dl.setAttribute("download", `${frameId}.${primary.suffix}`);
  wrap.appendChild(dl);

  const toggle = el("button", "prov-dlmore");
  toggle.setAttribute("aria-label", t("prov.exec.downloadMore"));
  toggle.setAttribute("aria-expanded", "false");
  toggle.appendChild(iconEl("chevron-down", 13));
  const menu = el("div", "prov-dlmenu hidden");
  NOTEBOOK_EXPORTS.slice(1).forEach((option) => {
    const item = el("a", "prov-dlitem") as HTMLAnchorElement;
    item.appendChild(el("span", null, t(option.key)));
    item.href = notebookExportHref(frameId, option);
    item.setAttribute("download", `${frameId}.${option.suffix}`);
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

export function artUrl(a: { id?: string }): string {
  const b = (_artBust.value || {})[String(a.id)];
  return `${API}/artifacts/${a.id}` + (b ? `?_=${b}` : "");
}

/** app.js:9676-9681 */
export function artUrlByName(fname: string): string {
  if (!fname) return "";
  const base = String(fname).split("/").pop();
  const list = Array.isArray(artifacts.value) ? artifacts.value : [];
  const a = list.find((x) => {
    const rec = x as { filename?: string };
    return (rec.filename || "") === fname || (rec.filename || "").split("/").pop() === base;
  }) as { id?: string } | undefined;
  return a ? artUrl(a) : `${API}/artifacts/${encodeURIComponent(fname)}`;
}

export function artUrlBust(fname: string): string {
  return artUrlByName(fname);
}

/** app.js:9708-9744. `_tbl` lives in the artifacts store (F-06 already busts it). */
export function renderTableInto(holder: HTMLElement, fname: string): void {
  const url = artUrlBust(fname);
  const build = (rows: string[][]) => {
    if (!rows || !rows.length) return;
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
    return;
  }
  fetch(url)
    .then((r) => (r.ok ? r.text() : null))
    .then((text) => {
      if (text == null) return;
      const firstLine = text.replace(/\r/g, "").split("\n", 1)[0] || "";
      const rows = parseDelimited(text, delimiterFor(fname, "", firstLine));
      cache[url] = rows;
      build(rows);
    })
    .catch(() => {});
}

/**
 * Live-render a produced figure onto the current notebook cell.
 * app.js:5337-5341. `_tbl` bust is already in F-06 upsertArtifactFromEvent.
 */
export function mountLiveNotebookFigure(m: WsMessage): void {
  const art = m.artifact && typeof m.artifact === "object" ? m.artifact : {};
  const fn = String(art.filename || m.filename || "");
  const isImg =
    /^image\//.test(String(art.content_type || "")) ||
    /\.(png|jpe?g|gif|svg|webp|bmp)$/i.test(fn);
  if (!(running.value && fn && isImg)) return;
  const producer = art.producing_cell_id || m.producing_cell_id;
  const live = asCells(liveCells.value);
  const cell =
    (producer && nbFindCell(producer)) ||
    (_liveCell.value as NotebookCell | null) ||
    (live.length ? live[live.length - 1] : null);
  if (cell && !(cell.figures || []).includes(fn)) {
    cell.figures = cell.figures || [];
    cell.figures.push(fn);
    const rec = syncCellOutput(cell);
    rec.figures.value = cell.figures.slice();
    nbRender();
  }
}
