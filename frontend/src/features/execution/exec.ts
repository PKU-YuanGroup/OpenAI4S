/**
 * Executed-code view. Port of app.js:10148-10229.
 *
 * Replaces the Notebook body while open: a read-only view of execution
 * HISTORY (root + delegated child frames), not of the live session's
 * deliverables. Stale-response guards keep identity of `S.execSources`.
 */

import { cells as savedCells, execSources } from "../../stores/notebook";
import { currentId } from "../../stores/session";
import { t } from "../../i18n/runtime";
import { cellNode } from "../notebook/Notebook";
import type { NotebookCell } from "../notebook/types";
import { publicText } from "../scrub/scrub";
import { api, apiErrorText } from "./api";
import type { ExecFrame, ExecSourcesState } from "./types";

function el(tag: string, cls?: string | null, text?: string | null): HTMLElement {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

export function execSourcesState(): ExecSourcesState {
  let st = execSources.value as ExecSourcesState | null;
  if (!st) {
    st = {
      open: false,
      data: null,
      selected: null,
      cells: {},
      loading: false,
      error: "",
      request: 0,
    };
    execSources.value = st;
  }
  return st;
}

// Per-frame ownership lets unrelated reads populate their own cache slots.
const cellRequests = new WeakMap<ExecSourcesState, Map<string, number>>();

let paintExec: (() => void) | null = null;

export function setPaintExecutionChrome(fn: (() => void) | null): void {
  paintExec = fn;
}

function paint(): void {
  if (paintExec) paintExec();
}

/**
 * The session's saved cells, reduced to what changes when one finishes. The
 * executed-code view is a snapshot; this says whether it is older than the
 * Notebook it sits in.
 */
export function executedCellsStamp(): string {
  const list = Array.isArray(savedCells.value) ? (savedCells.value as NotebookCell[]) : [];
  const last = list[list.length - 1];
  return last
    ? `${list.length}:${last.producing_cell_id || last.cell_id || last.cell_index}:${last.status || ""}`
    : "0";
}

export function toggleExecutedCode(): void {
  const st = execSourcesState();
  st.open = !st.open;
  // Every open re-reads: it only ever loaded once, and then showed that first
  // snapshot for the rest of the session. The old one stays up meanwhile.
  if (st.open) void loadExecutionSources();
  else {
    // Closing retires both snapshot and log reads, including a quick reopen.
    st.request += 1;
    st.loading = false;
  }
  paint();
}

/** A loaded, open view whose snapshot predates a cell that finished since. */
export function refreshExecutedCodeIfStale(st: ExecSourcesState): void {
  if (st.open && st.data && !st.loading && st.stamp !== executedCellsStamp())
    void loadExecutionSources();
}

export async function loadExecutionSources(): Promise<void> {
  const id = currentId.value;
  if (!id) return;
  const st = execSourcesState();
  const request = (st.request = (st.request || 0) + 1);
  const ownsRequest = () =>
    id === currentId.value && execSources.value === st && request === st.request;
  st.loading = true;
  st.error = "";
  st.stamp = executedCellsStamp();
  let loaded = false;
  try {
    const d = (await api(`/frames/${encodeURIComponent(id)}/execution-sources`)) as {
      frames?: ExecFrame[];
    };
    if (!ownsRequest()) return;
    // Reads started while the snapshot was loading also belong to the old view.
    cellRequests.get(st)?.clear();
    st.data = d;
    if (!st.selected) st.selected = (d && d.frames && d.frames[0] && d.frames[0].frame_id) || id;
    // Cell lists were read against the previous snapshot: keep the selected
    // one on screen until it is re-read below, let the others reload on click.
    const kept = st.cells[st.selected];
    st.cells = kept ? { [st.selected]: kept } : {};
    loaded = true;
  } catch (e) {
    if (ownsRequest()) st.error = publicText(apiErrorText(e), 240);
  } finally {
    if (ownsRequest()) {
      st.loading = false;
      paint();
    }
  }
  if (ownsRequest() && loaded && st.selected) void selectExecFrame(st.selected, true);
}

export async function selectExecFrame(frameId: string, force = false): Promise<void> {
  const id = currentId.value;
  const st = execSourcesState();
  const snapshot = st.request;
  st.selected = frameId;
  // Even a cached selection retires the previous selection's error ownership.
  const request = (st.cellRequest = (st.cellRequest || 0) + 1);
  paint();
  if (!force && st.cells[frameId]) return;
  let requests = cellRequests.get(st);
  if (!requests) cellRequests.set(st, (requests = new Map()));
  requests.set(frameId, request);
  const ownsRequest = () =>
    id === currentId.value && execSources.value === st && snapshot === st.request &&
    requests.get(frameId) === request;
  try {
    const d = (await api(`/frames/${encodeURIComponent(frameId)}/execution-log`)) as {
      entries?: unknown[];
    };
    if (!ownsRequest()) return;
    st.cells[frameId] = (d && d.entries) || [];
    if (request === st.cellRequest) st.error = "";
  } catch (e) {
    // Do not cache the failure: an empty slot lets the next click retry.
    if (ownsRequest() && request === st.cellRequest)
      st.error = t("nb.exec.loadFailed", publicText(apiErrorText(e), 200));
  }
  if (ownsRequest() && st.open) paint();
}

export function buildExecutedCodeView(st: ExecSourcesState): HTMLElement {
  const wrap = el("div", "nb-exec");
  const head = el("div", "nb-exec-head");
  head.appendChild(el("span", "nb-exec-title", t("nb.exec.title")));
  head.appendChild(el("span", "nb-exec-note", t("nb.exec.note")));
  wrap.appendChild(head);
  if (st.error) wrap.appendChild(el("div", "timeline-error", publicText(st.error, 240)));
  if (!st.data) {
    if (!st.error) wrap.appendChild(el("div", "dock-empty", t("common.loading")));
    return wrap;
  }
  const frames = st.data.frames || [];
  const selected = st.selected || (frames[0] && frames[0].frame_id) || null;
  const nav = el("div", "nb-exec-frames");
  frames.forEach((f) => {
    const isRoot = !f.parent_id;
    const btn = el("button", "nb-exec-frame" + (selected === f.frame_id ? " on" : ""));
    btn.setAttribute("data-frame", publicText(f.frame_id, 96));
    btn.style.setProperty(
      "--exec-indent",
      Math.min(Math.max(Number(f.depth) || 0, 0), 8) * 14 + "px",
    );
    btn.appendChild(
      el(
        "span",
        "nb-exec-frame-name",
        isRoot ? t("nb.exec.root") : publicText(f.name, 80) || publicText(f.frame_id, 24),
      ),
    );
    const counts = f.counts || {};
    btn.appendChild(el("span", "nb-exec-frame-count", t("nb.exec.cellCount", Number(counts.cells) || 0)));
    if (Number(counts.error) > 0)
      btn.appendChild(el("span", "nb-exec-frame-fail", t("nb.exec.failCount", Number(counts.error))));
    btn.onclick = () => {
      void selectExecFrame(String(f.frame_id || ""));
    };
    nav.appendChild(btn);
  });
  wrap.appendChild(nav);
  const body = el("div", "nb-exec-cells");
  const cells = selected != null ? st.cells[selected] : null;
  if (!cells) body.appendChild(el("div", "dock-empty", t("common.loading")));
  else if (!cells.length) body.appendChild(el("div", "dock-empty", t("nb.exec.empty")));
  else cells.forEach((e) => body.appendChild(cellNode(e as NotebookCell)));
  wrap.appendChild(body);
  return wrap;
}

export function paintExecutedCodeView(nb: HTMLElement, st: ExecSourcesState): void {
  const next = buildExecutedCodeView(st);
  const existing = nb.querySelector(".nb-exec");
  if (existing && existing.parentElement) {
    existing.replaceWith(next);
    return;
  }
  const slot = Array.from(nb.children).find(
    (node) => node instanceof HTMLElement && node.tagName === "DIV" && !node.className,
  ) as HTMLElement | undefined;
  if (slot) {
    slot.replaceChildren(next);
    return;
  }
  nb.appendChild(next);
}
