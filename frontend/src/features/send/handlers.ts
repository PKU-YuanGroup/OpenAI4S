/**
 * F-11 WS types: problem cards, step, plan, permission, candidate.
 *
 * Port of app.js:5206-5216 and 5277-5310. `frame_update` stays F-06; this
 * lane injects the turn-ticket body through `setFrameUpdateTurnHandler`.
 * Do not register `action_timeline` / `frame_update` / `replay_begin` /
 * `text_chunk`.
 */

import { _openGen } from "../../stores/session";
import { running } from "../../stores/stream";
import { enableComposer } from "../sessions/dom";
import { scheduleWorkbenchRefresh } from "../timeline/island";
import { eventFrameId, isStaleTurnEvent, mine } from "../ws/guards";
import { hasWsHandler, registerWsHandler } from "../ws/registry";
import type { WsHandler, WsMessage } from "../ws/types";
import {
  applyCandidateResolution,
  applyFinalReviewStatus,
  markCandidateReady,
} from "./candidate";
import { handleEnvironmentReadinessTerminal } from "./environment";
import { setCancelHidden } from "./host";
import { renderPermissionCard, resolvePermissionCard } from "./permission";
import { renderPlanCard, updatePlanProgress } from "./plan";
import { renderAttachmentProblems, renderRefProblems } from "./problems";
import { addLiveStep, updateLiveStep } from "./step";
import { activateTurnTicket, resumeWatch } from "./ticket";
import { turnDone } from "./turn";

const TERMINAL_FRAME_STATUS = [
  "completed",
  "failed",
  "cancelled",
  "blocked_by_guardian",
  "success",
  "done",
  "ready",
];

function registerUnlessPresent(type: string, handler: WsHandler): void {
  if (!hasWsHandler(type)) registerWsHandler(type, handler);
}

function rec(m: WsMessage): Record<string, unknown> {
  return m as Record<string, unknown>;
}

function handleArtifactRefProblems(m: WsMessage): void {
  if (mine(eventFrameId(m))) renderRefProblems(m.problems || []);
}

function handleAttachmentProblems(m: WsMessage): void {
  if (mine(eventFrameId(m))) renderAttachmentProblems(m.problems || []);
}

function handleStep(m: WsMessage): void {
  if (mine(eventFrameId(m))) addLiveStep(rec(m));
}

function handleStepUpdate(m: WsMessage): void {
  if (mine(eventFrameId(m))) updateLiveStep(rec(m));
}

function handlePlanReady(m: WsMessage): void {
  if (mine(eventFrameId(m))) {
    renderPlanCard(m.plan, m.status != null ? String(m.status) : undefined);
  }
}

function handlePlanProgress(m: WsMessage): void {
  if (mine(eventFrameId(m))) updatePlanProgress(rec(m));
}

function handleAwaitPermission(m: WsMessage): void {
  if (mine(eventFrameId(m))) {
    renderPermissionCard(m as Parameters<typeof renderPermissionCard>[0]);
    scheduleWorkbenchRefresh();
  }
}

function handlePermissionResolved(m: WsMessage): void {
  if (mine(eventFrameId(m))) {
    resolvePermissionCard(m as Parameters<typeof resolvePermissionCard>[0]);
    scheduleWorkbenchRefresh();
  }
}

function handleCandidateReady(m: WsMessage): void {
  // The original branch is `type === "candidate_ready" && m.gates_completion`.
  if (!m.gates_completion) return;
  const fid = eventFrameId(m);
  if (mine(fid) || mine(m.root_frame_id)) markCandidateReady(m);
}

function handleAutoRunTerminal(m: WsMessage): void {
  const fid = eventFrameId(m);
  if (mine(fid) || mine(m.root_frame_id)) scheduleWorkbenchRefresh(60);
}

function handleCandidateResolved(m: WsMessage): void {
  const fid = eventFrameId(m);
  if (mine(fid) || mine(m.root_frame_id)) applyCandidateResolution(m, fid);
}

/**
 * Turn-ticket / turnDone body of `frame_update` (app.js:5296-5310).
 * F-06 already gated on `mine(frame_id) || mine(fid)` before calling this.
 */
export function handleFrameUpdateTurn(m: WsMessage): void {
  const fid = eventFrameId(m);
  if (m.status === "processing") activateTurnTicket(m.request_id, m.execution_id);
  if (m.status === "processing" && !running.value) {
    running.value = true;
    enableComposer(false);
    setCancelHidden(false);
    if (fid) resumeWatch(String(fid), _openGen.value);
  }
  if (typeof m.status === "string" && TERMINAL_FRAME_STATUS.includes(m.status)) {
    if (isStaleTurnEvent(m)) scheduleWorkbenchRefresh();
    else {
      if (m.review_status) applyFinalReviewStatus(m, fid);
      handleEnvironmentReadinessTerminal(m);
      turnDone(m.status, m);
      scheduleWorkbenchRefresh();
    }
  }
}

export function registerSendHandlers(): void {
  registerUnlessPresent("artifact_ref_problems", handleArtifactRefProblems);
  registerUnlessPresent("attachment_problems", handleAttachmentProblems);
  registerUnlessPresent("step", handleStep);
  registerUnlessPresent("step_update", handleStepUpdate);
  registerUnlessPresent("plan_ready", handlePlanReady);
  registerUnlessPresent("plan_progress", handlePlanProgress);
  registerUnlessPresent("await_permission", handleAwaitPermission);
  registerUnlessPresent("permission_resolved", handlePermissionResolved);
  registerUnlessPresent("candidate_ready", handleCandidateReady);
  registerUnlessPresent("auto_run_terminal", handleAutoRunTerminal);
  registerUnlessPresent("candidate_resolved", handleCandidateResolved);
}
