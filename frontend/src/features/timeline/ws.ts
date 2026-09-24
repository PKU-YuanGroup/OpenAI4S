/**
 * Timeline / workbench WS handlers. One handler per type; F-06 owns
 * replay_begin / replay_end / frame_update / artifact_created.
 * Port of app.js:5225-5269.
 */

import { isReady } from "../../compat/stub";
import { publicText } from "../scrub/scrub";
import { eventFrameId, mine } from "../ws/guards";
import { hasWsHandler, registerWsHandler } from "../ws/registry";
import type { WsMessage } from "../ws/types";
import {
  actionTimelineBranchScope,
  mergeDelegationChildEvent,
  rememberExecutionQueue,
  rememberExecutionState,
  scheduleActionTimelineRender,
  scheduleBranchConversationResync,
  scheduleWorkbenchRefresh,
  updateActionTimelineLedger,
} from "./island";
import { S } from "./s";
import {
  branchUndoFromProjection,
  carryRevertPreview,
  keepUnchanged,
  mergeActionTimelines,
  sanitizeActionTimeline,
  sanitizeBranches,
  sanitizeExecutionQueue,
  sanitizeRecovery,
  sanitizeSecurity,
} from "./sanitize";

function registerUnlessPresent(type: string, handler: (m: WsMessage) => void): void {
  if (!hasWsHandler(type)) registerWsHandler(type, handler);
}

function paintNotebook(): void {
  const fn = (globalThis as Record<string, unknown>).renderNotebook;
  if (isReady(fn)) (fn as () => void)();
}

function handleActionTimeline(m: WsMessage): void {
  const fid = eventFrameId(m);
  if (!mine(fid)) return;
  const incoming = sanitizeActionTimeline(m),
    currentBranch = actionTimelineBranchScope(S.actionTimeline);
  if (
    !S.actionTimeline ||
    !incoming.branch_id ||
    !currentBranch ||
    incoming.branch_id === currentBranch
  ) {
    S.actionTimeline = mergeActionTimelines(S.actionTimeline, incoming, "latest");
    if (S.activeTab === "timeline") updateActionTimelineLedger({ direction: "latest" });
  }
}

function handleExecutionQueue(m: WsMessage): void {
  const fid = eventFrameId(m);
  if (!mine(fid)) return;
  rememberExecutionQueue(m);
  if (S.activeTab === "timeline") scheduleActionTimelineRender();
  if (S.activeTab === "notebook") paintNotebook();
}

function handleExecutionState(m: WsMessage): void {
  const fid = eventFrameId(m);
  if (!mine(fid)) return;
  if (m.type === "execution_owner") {
    const current = S.executionQueue || sanitizeExecutionQueue({});
    // A new object: the notebook's owner chips subscribe to executionQueue.
    S.executionQueue = {
      ...current,
      owner: m.owner ? sanitizeExecutionQueue({ owner: { ...m, owner: m.owner } }).owner : null,
    };
    if (m.owner && m.execution_id) rememberExecutionState({ ...m, status: "running" });
    else S.executionIdentity = null;
  } else rememberExecutionState(m);
  scheduleWorkbenchRefresh(60);
  if (S.activeTab === "timeline") scheduleActionTimelineRender();
  if (S.activeTab === "notebook") paintNotebook();
}

function handleRecovery(m: WsMessage): void {
  const fid = eventFrameId(m);
  if (!mine(fid)) return;
  const next = sanitizeRecovery(m),
    previous = S.recoveryState;
  if (previous && m.type === "recovery_log")
    S.recoveryState = {
      ...previous,
      ...Object.fromEntries(
        Object.entries(next).filter(([, value]) => value != null && value !== ""),
      ),
      log: (previous.log || []).concat(next.log || []).slice(-50),
    };
  else S.recoveryState = keepUnchanged(previous, next);
  if (
    m.type === "recovery_state" ||
    ["completed", "failed", "partial", "cancelled"].includes(
      String(m.status || m.state || "").toLowerCase(),
    )
  )
    scheduleWorkbenchRefresh(120);
  if (S.activeTab === "timeline") scheduleActionTimelineRender();
  if (S.activeTab === "notebook") paintNotebook();
}

function handleBranch(m: WsMessage): void {
  const fid = eventFrameId(m);
  if (!mine(fid)) return;
  if (m.type === "branch_projection_restored" || (m.type === "branch_activation_state" && m.branch_id)) {
    scheduleBranchConversationResync(String(fid));
    return;
  }
  if (
    m.type === "branch_reverted" &&
    m.ok === true &&
    publicText(m.branch_id, 96) === publicText(S.branchState && S.branchState.branch_id, 96) &&
    publicText(m.checkpoint_id, 96)
  )
    S.branchUndo = {
      branch_id: publicText(m.branch_id, 96),
      revert_checkpoint_id: publicText(m.checkpoint_id, 96),
    };
  if (m.branches || (m.payload && (m.payload as { branches?: unknown }).branches)) {
    S.branchState = keepUnchanged(
      S.branchState,
      carryRevertPreview(S.branchState, sanitizeBranches(m)),
    );
    S.branchUndo = keepUnchanged(S.branchUndo, branchUndoFromProjection(S.branchState));
  } else scheduleWorkbenchRefresh(m.type === "branch_activation_state" ? 0 : 80);
  if (S.activeTab === "timeline") scheduleActionTimelineRender();
  if (S.activeTab === "notebook") paintNotebook();
}

function handleDelegation(m: WsMessage): void {
  const fid = eventFrameId(m);
  if (!mine(fid)) return;
  if (m.type === "delegation_child_event") mergeDelegationChildEvent(m);
  scheduleWorkbenchRefresh(60);
  if (S.activeTab === "timeline") scheduleActionTimelineRender();
}

function handleSandbox(m: WsMessage): void {
  const fid = eventFrameId(m);
  if (!mine(fid)) return;
  S.securityState = keepUnchanged(S.securityState, sanitizeSecurity(m));
  if (S.activeTab === "timeline") scheduleActionTimelineRender();
}

export function registerTimelineHandlers(): void {
  registerUnlessPresent("action_timeline", handleActionTimeline);
  registerUnlessPresent("action-timeline", handleActionTimeline);
  registerUnlessPresent("execution_queue", handleExecutionQueue);
  registerUnlessPresent("execution_state", handleExecutionState);
  registerUnlessPresent("execution_owner", handleExecutionState);
  for (const type of ["recovery", "recovery_state", "recovery_log"]) {
    registerUnlessPresent(type, handleRecovery);
  }
  for (const type of [
    "branch",
    "branch_state",
    "branch_activation_state",
    "branch_projection_restored",
    "checkpoint",
    "checkpoint_created",
    "branch_created",
    "branch_reverted",
    "branch_revert_conflict",
  ]) {
    registerUnlessPresent(type, handleBranch);
  }
  for (const type of [
    "delegation_child_event",
    "delegation_state",
    "delegation_progress",
    "delegation_steering",
  ]) {
    registerUnlessPresent(type, handleDelegation);
  }
  for (const type of ["sandbox", "sandbox_status", "security_status"]) {
    registerUnlessPresent(type, handleSandbox);
  }
}
