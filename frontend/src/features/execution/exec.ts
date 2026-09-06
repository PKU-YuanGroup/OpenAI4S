/**
 * Executed-code view. Port of app.js:10148-10229.
 *
 * Replaces the Notebook body while open: a read-only view of execution
 * HISTORY (root + delegated child frames), not of the live session's
 * deliverables. Stale-response guards keep identity of `S.execSources`.
 */

import { execSources } from "../../stores/notebook";
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

let paintExec: (() => void) | null = null;

export function setPaintExecutionChrome(fn: (() => void) | null): void {
  paintExec = fn;
}

function paint(): void {
  if (paintExec) paintExec();
}

export function toggleExecutedCode(): void {
  const st = execSourcesState();
  st.open = !st.open;
  if (st.open && !st.data && !st.loading) void loadExecutionSources();
  paint();
}

export async function loadExecutionSources(): Promise<void> {
  const id = currentId.value;
  if (!id) return;
  const st = execSourcesState();
  const request = (st.request = (st.request || 0) + 1);
  st.loading = true;
  st.error = "";
  try {
    const d = (await api(`/frames/${encodeURIComponent(id)}/execution-sources`)) as {
      frames?: ExecFrame[];
    };
    if (id !== currentId.value || execSources.value !== st || request !== st.request) return;
    st.data = d;
    if (!st.selected) st.selected = (d && d.frames && d.frames[0] && d.frames[0].frame_id) || id;
  } catch (e) {
    if (id === currentId.value && execSources.value === st) st.error = publicText(apiErrorText(e), 240);
  } finally {
    if (id === currentId.value && execSources.value === st) {
      st.loading = false;
      paint();
    }
  }
  if (execSources.value === st && st.data && st.selected) void selectExecFrame(st.selected);
}

export async function selectExecFrame(frameId: string): Promise<void> {
  const st = execSourcesState();
  st.selected = frameId;
  paint();
  if (st.cells[frameId]) return;
  // Guarded like loadExecutionSources: a stale response (frame re-selected,
  // session switched) may still fill its own cache slot, but only the latest
  // request owns the shared error banner.
  const request = (st.cellRequest = (st.cellRequest || 0) + 1);
  try {
    const d = (await api(`/frames/${encodeURIComponent(frameId)}/execution-log`)) as {
      entries?: unknown[];
    };
    st.cells[frameId] = (d && d.entries) || [];
    if (execSources.value === st && request === st.cellRequest) st.error = "";
  } catch (e) {
    // Do not cache the failure: an empty slot lets the next click retry
    // instead of pinning an empty cell list until the session reopens.
    if (execSources.value === st && request === st.cellRequest)
      st.error = t("nb.exec.loadFailed", publicText(apiErrorText(e), 200));
  }
  if (execSources.value === st && st.open) paint();
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
