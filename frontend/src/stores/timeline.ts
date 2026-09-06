import { field } from "./signal-field";

/** app.js:2784 — ledger page size; window export in F-05. */
export const ACTION_TIMELINE_PAGE_SIZE = 500;
/** app.js:2785 — 46px row height; window export in F-05. */
export const ACTION_TIMELINE_ROW_HEIGHT = 46;
/** app.js:2786 — virtualizer overscan; window export in F-05. */
export const ACTION_TIMELINE_OVERSCAN = 8;
/** app.js:2789 — overview SVG viewBox width; window export in F-05. */
export const ACTION_TIMELINE_OVERVIEW_WIDTH = 1000;

/** S.actionTimeline — app.js:124; stored by reference */
export const actionTimeline = field(() => null as unknown);
/** S.executionQueue — app.js:124; stored by reference */
export const executionQueue = field(() => null as unknown);
/** S.executionIdentity — app.js:124 */
export const executionIdentity = field(() => null as unknown);
/** S.recoveryState — app.js:124 */
export const recoveryState = field(() => null as unknown);
/** S.actionTimelineSelectedGroupId — app.js:125 */
export const actionTimelineSelectedGroupId = field(() => null as string | null);
/** S.actionTimelineSelectedBranchId — app.js:125 */
export const actionTimelineSelectedBranchId = field(() => null as string | null);
/** S.recoveryActions — app.js:126 */
export const recoveryActions = field(() => null as unknown);
/** S.branchState — app.js:126; nested `.revert_preview` writes */
export const branchState = field(() => null as unknown);
/** S.branchUndo — app.js:126 */
export const branchUndo = field(() => null as unknown);
/** S.contextState — app.js:126 */
export const contextState = field(() => null as unknown);
/** S.securityState — app.js:126 */
export const securityState = field(() => null as unknown);
/** S.computeTasks — app.js:126 */
export const computeTasks = field(() => null as unknown);
/** S.delegationState — app.js:127; tests write the object then call render */
export const delegationState = field(() => null as unknown);
/** S.workbenchErrors — app.js:129; nested `delete S.workbenchErrors.*` */
export const workbenchErrors = field(() => Object.create(null) as Record<string, unknown>);
/** S._workbenchReq — app.js:129 */
export const _workbenchReq = field(() => 0);
/** S._timelineHistoryReq — app.js:129 */
export const _timelineHistoryReq = field(() => 0);
/** S._timelineHistoryLoading — app.js:129 */
export const _timelineHistoryLoading = field(() => null as unknown);
/** S._timelineView — app.js:129; stored by reference (collapsedTurns / search*) */
export const _timelineView = field(() => null as unknown);
/** S._recoveryActionLoading — app.js:130 */
export const _recoveryActionLoading = field(() => null as unknown);
/** S._branchActionLoading — app.js:130 */
export const _branchActionLoading = field(() => null as unknown);
/** S._timelineRestoreFocusGroupId — app.js:130 */
export const _timelineRestoreFocusGroupId = field(() => null as string | null);
/** S._workbenchLoading — app.js:3356 */
export const _workbenchLoading = field(() => null as unknown);
/** S._workbenchTimer — app.js:3387 */
export const _workbenchTimer = field(() => null as unknown);
/** S._branchConversationTimer — app.js:3391 */
export const _branchConversationTimer = field(() => null as unknown);
/** S.computeStatus — app.js:7153 */
export const computeStatus = field(() => null as unknown);

export const timelineSignals = {
  actionTimeline,
  executionQueue,
  executionIdentity,
  recoveryState,
  actionTimelineSelectedGroupId,
  actionTimelineSelectedBranchId,
  recoveryActions,
  branchState,
  branchUndo,
  contextState,
  securityState,
  computeTasks,
  delegationState,
  workbenchErrors,
  _workbenchReq,
  _timelineHistoryReq,
  _timelineHistoryLoading,
  _timelineView,
  _recoveryActionLoading,
  _branchActionLoading,
  _timelineRestoreFocusGroupId,
  _workbenchLoading,
  _workbenchTimer,
  _branchConversationTimer,
  computeStatus,
};
