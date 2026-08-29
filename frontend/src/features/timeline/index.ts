/**
 * F-15 Timeline boot. Assigns this lane's contract globals onto window
 * (overwriting F-05 stubs) and registers WS handlers. Mirrors F-06 bootWs()
 * / F-07 t() — the owning module writes window, not window-exports.ts.
 */

import {
  actionTimelineOverviewVisualExtent,
  actionTimelineSelectionOverlaps,
  actionTimelineSpan,
  actionTimelineEntryKey,
  timelineOverviewTimeToX,
} from "./model";
import {
  commitActionTimelineOverviewSelection,
  loadEarlierActionTimeline,
  mergeDelegationChildEvent,
  renderActionTimeline,
  renderDelegationPanel,
  steerDelegationChild,
  toggleActionTimelineTurn,
  updateActionTimelineLedger,
} from "./island";
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
  scheduleWorkbenchRefresh,
  steerDelegationChild,
  toggleActionTimelineTurn,
  updateActionTimelineLedger,
} from "./island";
export { registerTimelineHandlers, applyKernelSandbox } from "./ws";

const TIMELINE_WINDOW: Record<string, unknown> = {
  actionTimelineEntryKey,
  actionTimelineOverviewVisualExtent,
  actionTimelineSelectionOverlaps,
  actionTimelineSpan,
  commitActionTimelineOverviewSelection,
  loadEarlierActionTimeline,
  mergeDelegationChildEvent,
  renderActionTimeline,
  renderDelegationPanel,
  sanitizeActionTimeline,
  steerDelegationChild,
  timelineOverviewTimeToX,
  toggleActionTimelineTurn,
  updateActionTimelineLedger,
};

export function installTimeline(
  target: WindowExportsTarget = globalThis as unknown as WindowExportsTarget,
): void {
  registerTimelineHandlers();
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
