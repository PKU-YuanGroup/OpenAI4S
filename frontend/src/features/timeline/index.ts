/**
 * F-15 Timeline boot. Assigns this lane's contract globals onto window
 * (overwriting F-05 stubs) and registers WS handlers. Mirrors F-06 bootWs()
 * / F-07 t() — the owning module writes window, not window-exports.ts.
 */

import { onLanguageChange } from "../../i18n/runtime";
import { setScheduleWorkbenchRefresh } from "../notebook/kernel";
import {
  actionTimelineOverviewVisualExtent,
  actionTimelineSelectionOverlaps,
  actionTimelineSpan,
  actionTimelineEntryKey,
  timelineOverviewTimeToX,
} from "./model";
import {
  commitActionTimelineOverviewSelection,
  destroyActionTimelineView,
  loadEarlierActionTimeline,
  loadWorkbenchState,
  mergeDelegationChildEvent,
  relabelActionTimeline,
  renderActionTimeline,
  renderDelegationPanel,
  scheduleWorkbenchRefresh,
  steerDelegationChild,
  toggleActionTimelineTurn,
  updateActionTimelineLedger,
} from "./island";
import { renderQueueStrip } from "./queue";
import { sanitizeActionTimeline } from "./sanitize";
import { registerTimelineHandlers } from "./ws";

type WindowExportsTarget = Record<string, unknown>;

export {
  mergeActionTimelines,
  sanitizeActionTimeline,
  sanitizeBranches,
  sanitizeComputeTasks,
  sanitizeContext,
  sanitizeDelegations,
  sanitizeExecutionQueue,
  sanitizeRecovery,
  sanitizeRecoveryActions,
  sanitizeSecurity,
  sanitizeVariableInspection,
  timelineOrdinal,
} from "./sanitize";
export {
  actionTimelineEntryKey,
  actionTimelineOverviewVisualExtent,
  actionTimelineSelectionOverlaps,
  actionTimelineSpan,
  timelineOverviewTimeToX,
} from "./model";
export {
  commitActionTimelineOverviewSelection,
  destroyActionTimelineView,
  loadEarlierActionTimeline,
  loadWorkbenchState,
  mergeDelegationChildEvent,
  rememberExecutionQueue,
  rememberExecutionState,
  renderActionTimeline,
  renderBranchPanel,
  renderComputeTasksPanel,
  renderContextPanel,
  renderDelegationPanel,
  renderSecurityPanel,
  scheduleActionTimelineRender,
  scheduleWorkbenchRefresh,
  steerDelegationChild,
  toggleActionTimelineTurn,
  updateActionTimelineLedger,
} from "./island";
export { renderQueueStrip } from "./queue";
export { registerTimelineHandlers } from "./ws";

const TIMELINE_WINDOW: Record<string, unknown> = {
  actionTimelineEntryKey,
  actionTimelineOverviewVisualExtent,
  actionTimelineSelectionOverlaps,
  actionTimelineSpan,
  commitActionTimelineOverviewSelection,
  // messages/open.ts calls it on every session switch / branch reset.
  destroyActionTimelineView,
  loadEarlierActionTimeline,
  loadWorkbenchState,
  mergeDelegationChildEvent,
  renderActionTimeline,
  renderDelegationPanel,
  // Called by features/sessions (enableComposer) after a session switch has
  // cleared the queue; unassigned, the previous session's strip stayed up.
  renderQueueStrip,
  sanitizeActionTimeline,
  steerDelegationChild,
  timelineOverviewTimeToX,
  toggleActionTimelineTurn,
  updateActionTimelineLedger,
};

let relabelHooked = false;

export function installTimeline(
  target: WindowExportsTarget = globalThis as unknown as WindowExportsTarget,
): void {
  registerTimelineHandlers();
  // Panels are cached per section now, so they need telling when the words
  // change: the dictionaries landing after first paint, or a language switch.
  if (!relabelHooked) {
    relabelHooked = true;
    onLanguageChange(relabelActionTimeline);
  }
  // The notebook's cell-finished / kernel_status handlers ask for a workbench
  // refresh through this seam; until it is set, their request is a no-op.
  setScheduleWorkbenchRefresh(scheduleWorkbenchRefresh);
  for (const [name, value] of Object.entries(TIMELINE_WINDOW)) {
    target[name] = value;
  }
}

export function bootTimeline(
  target: WindowExportsTarget = globalThis as unknown as WindowExportsTarget,
): void {
  installTimeline(target);
}

const hostWindow = (globalThis as unknown as { window?: WindowExportsTarget }).window;
if (hostWindow) bootTimeline(hostWindow);
