/**
 * Notebook cell merge + live protocol.
 * Port of app.js:9765-9899 (nbEventCellId / merge / draft / start / chunk / finished / live fallback).
 *
 * `_seenChunks` replay dedup at 9851-9853 is load-bearing: a reconnect replays
 * the same chunk_id and must not append twice.
 */

import { signal, type Signal } from "@preact/signals";
import { dockArtifact } from "../../stores/artifacts";
import {
  _executionLoadReq,
  _kc,
  _lineageFor,
  _lineageReq,
  _liveCell,
  cells,
  kernels,
  lineage,
  liveCells,
} from "../../stores/notebook";
import { currentId } from "../../stores/session";
import { provMode } from "../../stores/ui";
import { publicText } from "../scrub/scrub";
import { appendLiveOutput } from "../stream/cap";
import { API } from "../ws/connect";
import type { WsMessage } from "../ws/types";
import { kernelIdFromEnv } from "./labels";
import { nbRender } from "./scroll";
import type { KernelStatus, NotebookCell } from "./types";

export function asCells(value: unknown): NotebookCell[] {
  return Array.isArray(value) ? (value as NotebookCell[]) : [];
}

/** app.js:9765 */
export function nbEventCellId(event: WsMessage | null | undefined): string {
  if (!event) return "";
  const id = event.producing_cell_id || event.cell_id;
  return id == null ? "" : String(id);
}

/** app.js:9766-9768 */
export function nbCellKey(cell: NotebookCell | null | undefined): string {
  if (cell && (cell.producing_cell_id || cell.cell_id)) {
    return String(cell.producing_cell_id || cell.cell_id);
  }
  return (
    "legacy:" +
    String((cell && cell.kernel_id) || "python") +
    ":" +
    String(cell && cell.cell_index != null ? cell.cell_index : "?")
  );
}

/** app.js:9770-9779. Server record wins on an identical Cell ID. */
export function mergeNotebookCells(
  serverCells: NotebookCell[] | null | undefined,
  localCells: NotebookCell[] | null | undefined,
): NotebookCell[] {
  const merged = new Map<string, NotebookCell>();
  (localCells || []).forEach((cell) => merged.set(nbCellKey(cell), cell));
  (serverCells || []).forEach((cell) => merged.set(nbCellKey(cell), cell));
  return Array.from(merged.values()).sort((a, b) => {
    const ai = Number(a.cell_index);
    const bi = Number(b.cell_index);
    if (Number.isFinite(ai) && Number.isFinite(bi) && ai !== bi) return ai - bi;
    return String(nbCellKey(a)).localeCompare(String(nbCellKey(b)));
  });
}

/** app.js:9781-9785 */
export function nbFindCell(producingCellId: unknown): NotebookCell | null {
  const key = String(producingCellId || "");
  return (
    asCells(liveCells.value).find((cell) => nbCellKey(cell) === key) ||
    asCells(cells.value).find((cell) => nbCellKey(cell) === key) ||
    null
  );
}

export type CellOutputSignals = {
  stdout: Signal<string>;
  stderr: Signal<string>;
  source: Signal<string>;
  figures: Signal<string[]>;
  status: Signal<string>;
};

const outputByKey = new Map<string, CellOutputSignals>();

export function resetCellOutputs(): void {
  outputByKey.clear();
}

export function cellOutput(key: string): CellOutputSignals {
  let rec = outputByKey.get(key);
  if (!rec) {
    rec = {
      stdout: signal(""),
      stderr: signal(""),
      source: signal(""),
      figures: signal([] as string[]),
      status: signal(""),
    };
    outputByKey.set(key, rec);
  }
  return rec;
}

export function syncCellOutput(cell: NotebookCell): CellOutputSignals {
  const rec = cellOutput(nbCellKey(cell));
  rec.stdout.value = String(cell.stdout || "");
  rec.stderr.value = String(cell.stderr || "");
  rec.source.value = String(cell.source || "");
  rec.figures.value = Array.isArray(cell.figures) ? cell.figures.slice() : [];
  rec.status.value = String(cell.status || (cell.live ? "running" : "ok"));
  return rec;
}

/**
 * textNode.appendData delta. Returns the new seen length.
 * A shorter `next` (truncation / replay reset) replaces the whole node.
 */
export function appendTextNodeDelta(
  node: { data: string; appendData: (s: string) => void },
  previousLength: number,
  next: string,
): number {
  const text = String(next || "");
  if (previousLength <= 0 || text.length < previousLength) {
    node.data = text;
    return text.length;
  }
  const delta = text.slice(previousLength);
  if (delta) node.appendData(delta);
  return text.length;
}

function setLive(next: NotebookCell[]): void {
  liveCells.value = next;
}

function setSaved(next: NotebookCell[]): void {
  cells.value = next;
}

/** app.js:9787-9814 */
export function nbCellDraft(event: WsMessage): void {
  const draftId = publicText(event && event.draft_id, 160);
  if (!draftId) return;
  const revision = Math.max(0, Number(event.revision) || 0);
  const previous = nbFindCell(draftId);
  if (previous && Number(previous._draftRevision || 0) > revision) return;
  if (event.status === "discarded") {
    setLive(asCells(liveCells.value).filter((cell) => nbCellKey(cell) !== draftId));
    const remaining = asCells(liveCells.value);
    _liveCell.value = remaining[remaining.length - 1] || null;
    nbRender();
    return;
  }
  const language = String(event.language || "").toLowerCase() === "r" ? "r" : "python";
  const status = event.status === "ready" ? "ready" : "drafting";
  const cell: NotebookCell = {
    producing_cell_id: draftId,
    cell_id: draftId,
    cell_index: null,
    kernel_id: language,
    language,
    origin: "agent",
    source: typeof event.source === "string" ? event.source.slice(0, 200000) : "",
    stdout: "",
    stderr: "",
    error: "",
    status,
    figures: [],
    files_written: [],
    files_read: [],
    complete: event.complete === true,
    draft: true,
    live: true,
    _draftRevision: revision,
  };
  // One Agent writer owns the session. A new turn replaces any stale draft
  // left by a dropped terminal event instead of appending another partial Cell.
  setLive(
    mergeNotebookCells(
      [cell],
      asCells(liveCells.value).filter(
        (candidate) => !candidate.draft || nbCellKey(candidate) === draftId,
      ),
    ),
  );
  _liveCell.value = cell;
  syncCellOutput(cell);
  nbRender();
}

/** app.js:9815-9844 */
export function nbCellStart(event: WsMessage): void {
  const id = nbEventCellId(event);
  if (!id) return;
  setLive(asCells(liveCells.value).filter((candidate) => !candidate.draft));
  const previous = nbFindCell(id) || ({} as NotebookCell);
  // A persisted finished Cell may still be present when a live-turn replay
  // begins.  Never inherit its complete output and then append replay chunks a
  // second time; only an already-live in-memory Cell may continue its stream.
  const inheritLiveOutput = previous.live === true && previous.status === "running";
  const cell: NotebookCell = {
    ...previous,
    producing_cell_id: String(id),
    cell_id: String(event.cell_id || previous.cell_id || id),
    cell_index: event.cell_index != null ? (event.cell_index as number) : previous.cell_index,
    kernel_id: String(event.kernel_id || previous.kernel_id || "python"),
    language: String(event.language || previous.language || "python"),
    origin: (event.origin as string | null | undefined) || previous.origin || null,
    source: event.source != null ? String(event.source) : previous.source || "",
    stdout: inheritLiveOutput ? previous.stdout || "" : "",
    stderr: inheritLiveOutput ? previous.stderr || "" : "",
    error: "",
    status: "running",
    figures: previous.figures || [],
    files_written: previous.files_written || [],
    files_read: previous.files_read || [],
    generation_id: String(event.generation_id || previous.generation_id || "") || undefined,
    state_revision:
      event.state_revision != null ? (event.state_revision as number) : previous.state_revision,
    attempt_group_id: String(event.attempt_group_id || previous.attempt_group_id || "") || undefined,
    revision_of: (event.revision_of as string | undefined) || previous.revision_of,
    replay_policy: (event.replay_policy as string | undefined) || previous.replay_policy,
    visibility: (event.visibility as string | undefined) || previous.visibility,
    _seenChunks: inheritLiveOutput ? previous._seenChunks : undefined,
    live: true,
  };
  setLive(mergeNotebookCells([cell], asCells(liveCells.value)));
  setSaved(asCells(cells.value).filter((saved) => nbCellKey(saved) !== String(id)));
  const found = nbFindCell(id);
  _liveCell.value = found;
  if (found) syncCellOutput(found);
  nbRender();
}

/**
 * app.js:9845-9856.
 * `_seenChunks` key is `stream + ":" + chunkId`. Duplicate replay is a no-op.
 * Chunk updates only this cell's output signal (no pane rebuild).
 */
export function nbCellChunk(event: WsMessage): void {
  const producingCellId = event && (event.producing_cell_id || event.cell_id);
  const cell = event && nbFindCell(producingCellId);
  if (!cell) return;
  const stream = event.stream === "stderr" ? "stderr" : "stdout";
  const chunkId =
    event.chunk_id != null
      ? event.chunk_id
      : event.sequence != null
        ? event.sequence
        : null;
  if (chunkId != null) {
    const seen: Record<string, boolean> = cell._seenChunks || Object.create(null);
    cell._seenChunks = seen;
    const seenKey = stream + ":" + String(chunkId);
    if (seen[seenKey]) return;
    seen[seenKey] = true;
  }
  cell[stream] = appendLiveOutput(cell[stream], String(event.chunk || ""));
  const rec = cellOutput(nbCellKey(cell));
  rec[stream].value = String(cell[stream] || "");
}

/** app.js:9858-9876 */
export function nbCellFinished(event: WsMessage): void {
  const id = event && (event.producing_cell_id || event.cell_id);
  if (!id) return;
  const active = nbFindCell(id) || ({} as NotebookCell);
  const cell: NotebookCell = {
    ...active,
    ...(event as NotebookCell),
    producing_cell_id: String(id),
    cell_id: String(event.cell_id || active.cell_id || id),
    source: event.source != null ? String(event.source) : active.source || "",
    stdout:
      event.stdout != null
        ? appendLiveOutput("", String(event.stdout))
        : active.stdout || "",
    stderr:
      event.stderr != null
        ? appendLiveOutput("", String(event.stderr))
        : active.stderr || "",
    error: event.error ? String(event.error) : "",
    status: String(event.status || (event.error ? "error" : "ok")),
    figures: (event.figures as string[] | undefined) || active.figures || [],
    files_written:
      (event.files_written as string[] | undefined) || active.files_written || [],
    files_read: (event.files_read as string[] | undefined) || active.files_read || [],
    live: false,
    draft: false,
  };
  setLive(asCells(liveCells.value).filter((candidate) => nbCellKey(candidate) !== String(id)));
  setSaved(mergeNotebookCells([cell], asCells(cells.value)));
  const remaining = asCells(liveCells.value);
  _liveCell.value = remaining[remaining.length - 1] || null;
  syncCellOutput(cell);
  nbRender();
}

const _NB_DIV = "----- output -----";

/** app.js:9880-9889. Legacy unstructured tool stream. */
export function nbLiveStart(
  tool: string | null | undefined,
  raw: string | null | undefined,
  serverKernelId: string | null | undefined,
  serverCellIndex: number | string | null | undefined,
  serverLanguage: string | null | undefined,
): void {
  const codeTools = /^(run_python|python|exec|run_bash|bash)/;
  const isCode =
    serverCellIndex != null ||
    codeTools.test(tool || "") ||
    !TOOL_LABELS_HAS(tool || "");
  if (!isCode) {
    _liveCell.value = null;
    return;
  }
  const idx =
    serverCellIndex || ((raw || "").match(/cell\s+(\d+)/) || [])[1];
  const st = _kc.value.st as KernelStatus | null;
  const kernelId =
    serverKernelId || kernelIdFromEnv((st && st.env) || null);
  const live = asCells(liveCells.value).slice();
  const cell: NotebookCell = {
    cell_index: idx ? +idx : live.length + 1,
    kernel_id: kernelId,
    language: serverLanguage || "python",
    source: "",
    stdout: "",
    stderr: "",
    status: "running",
    figures: [],
    live: true,
    _out: false,
  };
  live.push(cell);
  setLive(live);
  _liveCell.value = cell;
  syncCellOutput(cell);
  nbRender();
}

function TOOL_LABELS_HAS(tool: string): boolean {
  const w = globalThis as unknown as { TOOL_LABELS?: Record<string, unknown> };
  const labels = w.TOOL_LABELS;
  return !!(labels && tool && labels[tool]);
}

/** app.js:9891-8898 */
export function nbLiveAppend(txt: string): void {
  const c = _liveCell.value as NotebookCell | null;
  if (!c) return;
  if (!c._out) {
    const i = txt.indexOf(_NB_DIV);
    if (i === -1) {
      c.source = (c.source || "") + txt;
    } else {
      c.source = (c.source || "") + txt.slice(0, i);
      c._out = true;
      c.stdout = (c.stdout || "") + txt.slice(i + _NB_DIV.length).replace(/^\n/, "");
    }
  } else {
    c.stdout = (c.stdout || "") + txt;
  }
  const rec = syncCellOutput(c);
  rec.stdout.value = String(c.stdout || "");
  rec.source.value = String(c.source || "");
  nbRender();
}

/** app.js:10076-10112 */
export function projectNotebookCells(
  rawEntries: NotebookCell[] | null | undefined,
): NotebookCell[] {
  const entries = (rawEntries || []).map((cell) => ({ ...cell }));
  let previous: NotebookCell | null = null;
  entries.forEach((cell) => {
    const previousFailed =
      previous && ["error", "failed"].includes(String(previous.status));
    const agentRetry = previous && previous.origin === "agent" && cell.origin === "agent";
    const sameRuntime =
      previous &&
      (previous.kernel_id || "python") === (cell.kernel_id || "python") &&
      (previous.language || "python") === (cell.language || "python");
    if (!cell.attempt_group_id) {
      if (previous && previousFailed && sameRuntime && agentRetry) {
        cell.attempt_group_id = previous.attempt_group_id || nbCellKey(previous);
        cell.revision_of = nbCellKey(previous);
        cell.attempt = (previous.attempt || 1) + 1;
      } else {
        cell.attempt_group_id = nbCellKey(cell);
        cell.revision_of = null;
        cell.attempt = 1;
      }
    }
    previous = cell;
  });
  const groups = new Map<string, NotebookCell[]>();
  entries.forEach((cell) => {
    const group = String(cell.attempt_group_id || nbCellKey(cell));
    let list = groups.get(group);
    if (!list) {
      list = [];
      groups.set(group, list);
    }
    list.push(cell);
  });
  return Array.from(groups.values()).map((attempts) => {
    const latest = attempts[attempts.length - 1] as NotebookCell;
    return {
      ...latest,
      attempt: attempts.length,
      attempt_count: attempts.length,
      is_latest_attempt: true,
      _revisions: attempts.slice(0, -1),
    };
  });
}

export function notebookDisplayEntries(): NotebookCell[] {
  const saved = asCells(cells.value);
  const live = asCells(liveCells.value);
  const combined = live.length ? saved.concat(live) : saved.slice();
  return projectNotebookCells(combined);
}

let loadArtifactsFn: ((id: string) => void) | null = null;
let showProvenanceFn: ((a: unknown) => void) | null = null;
let notebookApi: import("./types").NotebookApi | null = null;

export function setNotebookApi(fn: import("./types").NotebookApi | null): void {
  notebookApi = fn;
}

export function setNotebookLoadArtifacts(fn: ((id: string) => void) | null): void {
  loadArtifactsFn = fn;
}

export function setShowProvenance(fn: ((a: unknown) => void) | null): void {
  showProvenanceFn = fn;
}

export async function notebookFetch(
  path: string,
  init?: RequestInit,
): Promise<Record<string, unknown> | null> {
  if (notebookApi) return notebookApi(path, init);
  if (typeof path !== "string" || path[0] !== "/" || path[1] === "/") {
    throw new Error("invalid api path");
  }
  const r = await fetch(API + path, {
    headers: { "content-type": "application/json" },
    ...init,
  });
  const text = await r.text();
  let j: unknown = null;
  try {
    j = text ? JSON.parse(text) : null;
  } catch {
    j = text;
  }
  if (!r.ok) {
    const body = j && typeof j === "object" ? (j as { error?: string }) : null;
    throw new Error((body && body.error) || "HTTP " + r.status);
  }
  return j && typeof j === "object" ? (j as Record<string, unknown>) : null;
}

/** app.js:9746-9763 */
export async function loadExecutionLog(id: string): Promise<void> {
  const request = (_executionLoadReq.value = (_executionLoadReq.value || 0) + 1);
  let d: Record<string, unknown> | null = null;
  try {
    d = await notebookFetch(`/frames/${id}/execution-log`);
  } catch {
    d = null;
  }
  if (id !== currentId.value || request !== _executionLoadReq.value) return;
  const serverCells = (d && (d.entries as NotebookCell[])) || [];
  setSaved(mergeNotebookCells(serverCells, asCells(cells.value)));
  const nextKernels = ((d && d.kernels) as string[]) || [];
  kernels.value = nextKernels;
  asCells(cells.value).forEach((cell) => {
    const k = cell.kernel_id || "python";
    if (!nextKernels.includes(k)) nextKernels.push(k);
  });
  kernels.value = nextKernels;
  nbRender();
  if (provMode.value && dockArtifact.value) {
    lineage.value = null;
    _lineageFor.value = null;
    _lineageReq.value = (_lineageReq.value || 0) + 1;
    if (showProvenanceFn) showProvenanceFn(dockArtifact.value);
  }
}

export function notifyLoadArtifacts(id: string): void {
  if (loadArtifactsFn) loadArtifactsFn(id);
}
