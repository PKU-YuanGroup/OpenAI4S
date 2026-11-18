/**
 * Register F-14 WS handlers and assign this lane's contract globals.
 * `highlightTraceback` / `notebookExportLink` are assigned here (Wave 3:
 * the owning module writes window, like F-06 `bootWs()` → `onEvent`).
 */

import { effect, untracked } from "@preact/signals";
import { settleLiveCellCard } from "../messages/cardState";
import { setArtifactCreatedSideEffects } from "../ws/handlers";
import { currentId } from "../../stores/session";
import { _replayGap } from "../../stores/stream";
import { eventFrameId, mine } from "../ws/guards";
import { hasWsHandler, registerWsHandler } from "../ws/registry";
import type { WsHandler, WsMessage } from "../ws/types";
import { mountLiveNotebookFigure, highlightTraceback, notebookExportLink } from "./chrome";
import { loadExecutionLog, nbCellDraft, nbCellFinished, nbCellStart, nbCellChunk } from "./cells";
import { handleKernelStatus, reconcilePendingRepl, scheduleWorkbenchRefresh } from "./kernel";
import { watchNotebookVisibility } from "./scroll";
import type { WindowTarget } from "./types";

function registerUnless(type: string, handler: WsHandler): void {
  if (!hasWsHandler(type)) registerWsHandler(type, handler);
}

export function registerNotebookHandlers(): void {
  registerUnless("notebook_cell_draft", (m: WsMessage) => {
    if (mine(eventFrameId(m))) nbCellDraft(m);
  });
  registerUnless("notebook_cell_start", (m: WsMessage) => {
    if (mine(eventFrameId(m))) nbCellStart(m);
  });
  registerUnless("notebook_cell_chunk", (m: WsMessage) => {
    if (mine(eventFrameId(m))) nbCellChunk(m);
  });
  registerUnless("notebook_cell_finished", (m: WsMessage) => {
    if (mine(eventFrameId(m))) {
      // The chat's activity card shows the same outcome (one handler per type).
      settleLiveCellCard(m);
      nbCellFinished(m);
      if (currentId.value) void loadExecutionLog(currentId.value);
      scheduleWorkbenchRefresh();
    }
  });
  registerUnless("kernel_status", (m: WsMessage) => {
    if (mine(m.frame_id)) handleKernelStatus(m);
  });
}

function assignWindow(target: WindowTarget): void {
  target.highlightTraceback = highlightTraceback;
  target.notebookExportLink = notebookExportLink;
  target.loadExecutionLog = loadExecutionLog;
}

let replayGapWatch: (() => void) | null = null;

/**
 * After a resubscribe that could not replay what this page missed, ask the
 * server whether the REPL execution it is waiting for already finished: its
 * terminal event may have been lost with the dropped socket. One per page.
 */
function watchReplayGaps(): void {
  if (replayGapWatch) return;
  replayGapWatch = effect(() => {
    const fid = _replayGap.value;
    // Untracked: the reconcile reads the pending identity, and a change to it
    // must not re-run this watch.
    untracked(() => {
      if (typeof fid === "string" && fid) void reconcilePendingRepl(fid);
    });
  });
}

function wireArtifactFigures(): void {
  setArtifactCreatedSideEffects((m: WsMessage) => {
    mountLiveNotebookFigure(m);
  });
}

/**
 * F-14 boot. Safe to call more than once (duplicate WS types are skipped).
 * Does not import `compat/window-exports.ts` (that module installs `window.S`).
 */
export function installNotebook(
  target: WindowTarget = globalThis as unknown as WindowTarget,
): void {
  registerNotebookHandlers();
  assignWindow(target);
  wireArtifactFigures();
  watchNotebookVisibility();
  watchReplayGaps();
}

export { highlightTraceback, notebookExportLink };
