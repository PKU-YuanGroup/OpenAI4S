/**
 * Register F-14 WS handlers and assign this lane's contract globals.
 * `highlightTraceback` / `notebookExportLink` are assigned here (Wave 3:
 * the owning module writes window, like F-06 `bootWs()` → `onEvent`).
 */

import { setArtifactCreatedSideEffects } from "../ws/handlers";
import { eventFrameId, mine } from "../ws/guards";
import { hasWsHandler, registerWsHandler } from "../ws/registry";
import type { WsHandler, WsMessage } from "../ws/types";
import { mountLiveNotebookFigure, highlightTraceback, notebookExportLink } from "./chrome";
import { nbCellDraft, nbCellFinished, nbCellStart, nbCellChunk } from "./cells";
import { handleKernelStatus, scheduleWorkbenchRefresh } from "./kernel";
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
      nbCellFinished(m);
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
}

export { highlightTraceback, notebookExportLink };
