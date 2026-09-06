/**
 * F-11 send chain + cards. Importing this module assigns the lane's
 * contract names onto `window` (the F-06 `bootWs()` / F-07 `t()` /
 * F-10 `installMessages` pattern) and registers cards / candidate /
 * step / plan / permission WS types.
 *
 * Do not import `compat/window-exports.ts` from here — that module
 * installs the S Proxy as a side effect and would clobber a test's
 * own `window.S`. `frame_update` is not registered here; the turn
 * body is injected through F-06 `setFrameUpdateTurnHandler`.
 */

import { isReady } from "../../compat/stub";
import { setRenderStoredStepImpl } from "../messages/list";
import { setFrameUpdateTurnHandler } from "../ws/handlers";
import {
  admissionSettled,
  forgetAdmission,
  outstandingAdmissions,
  reconcileLastAdmission,
  rememberAdmission,
} from "./admission";
import { handleFrameUpdateTurn, registerSendHandlers } from "./handlers";
import { send } from "./send";
import {
  buildStepCard,
  renderStoredStep,
  searchResultHttpUrl,
  type Step,
} from "./step";
import { renderAttachmentProblems, renderRefProblems } from "./problems";

export {
  ADMISSION_GRACE_MS,
  ADMISSION_LEGACY_KEY,
  ADMISSION_PREFIX,
  admissionAge,
  admissionSettled,
  admissionWithinGrace,
  forgetAdmission,
  migrateAdmissions,
  outstandingAdmissions,
  reconcileLastAdmission,
  rememberAdmission,
  resetAdmissionRetries,
  scheduleAdmissionRetry,
} from "./admission";
export {
  applyCandidateResolution,
  applyFinalReviewStatus,
  candidateReplacementCommitted,
  candidateReplacementText,
  markCandidateReady,
  replaceMessageAnswer,
  reviewStatusFrom,
  reviewTruthFrom,
  setReviewBadge,
} from "./candidate";
export type { CandidateResolution } from "./candidate";
export {
  handleEnvironmentReadinessTerminal,
  isEnvironmentReadinessError,
  refreshEnvironmentStatus,
  renderEnvironmentReadinessBanner,
  unavailableReadinessSnapshot,
} from "./environment";
export { handleFrameUpdateTurn, registerSendHandlers } from "./handlers";
export { callLane, hostFn, setCancelHidden } from "./host";
export { icon, iconEl } from "./icon";
export {
  defaultRememberScope,
  markPermCard,
  permActionLine,
  permScopeCn,
  renderPermissionCard,
  resolvePermissionCard,
} from "./permission";
export {
  PLAN_SETTLED_STEP_STATUSES,
  approvePlan,
  discardPlan,
  planConfLevel,
  planStepIcon,
  planStepSettled,
  renderPlanCard,
  resumePlan,
  revisePlan,
  showPlanApproval,
  updatePlanProgress,
} from "./plan";
export { renderAttachmentProblems, renderRefProblems } from "./problems";
export { annotAttachment, bindComposer, send } from "./send";
export {
  applyStepState,
  addLiveStep,
  baseName,
  binElide,
  buildStepCard,
  clipPre,
  codeBlock,
  langOf,
  outputBlock,
  renderStoredStep,
  searchResultHttpUrl,
  stepBody,
  stepIcon,
  updateLiveStep,
} from "./step";
export type { Step, StepHandle } from "./step";
export {
  acceptTurnTicket,
  activateTurnTicket,
  closeTurnTicket,
  commitTurnTicket,
  openTurnTicket,
  ownsTurnTicket,
  resumeWatch,
  retireTurnTicket,
} from "./ticket";
export {
  failureCodeHint,
  failureHint,
  failureMeta,
  lastTerminalFailure,
  turnDone,
} from "./turn";

export type SendTarget = Record<string, unknown>;

const SEND_CONTRACT_NAMES = [
  "send",
  "buildStepCard",
  "renderAttachmentProblems",
  "renderRefProblems",
  "searchResultHttpUrl",
  "admissionSettled",
  "forgetAdmission",
  "outstandingAdmissions",
  "reconcileLastAdmission",
  "rememberAdmission",
] as const;

export { SEND_CONTRACT_NAMES };

/**
 * Assign F-11 contract names, register this lane's WS types, and inject the
 * `frame_update` turn body. Safe to call more than once: WS handlers use
 * `registerUnlessPresent`. DOM-free by design: this runs at module import,
 * before Shell has rendered `#composer`, so `main.tsx` calls `bindComposer()`
 * after `render(<App/>)` instead (the F-17 `bootArtifacts` /
 * `finishArtifactsBoot` split).
 */
export function installSend(
  target: SendTarget = globalThis as unknown as SendTarget,
): void {
  registerSendHandlers();
  setFrameUpdateTurnHandler(handleFrameUpdateTurn);
  setRenderStoredStepImpl((step, host) => renderStoredStep(step as Step, host));
  // Explicit `target.name =` lines (not a loop) so the duplicate-name
  // scan `target.[A-Za-z_]* =` can see every assignment.
  target.send = send;
  target.buildStepCard = buildStepCard;
  target.renderAttachmentProblems = renderAttachmentProblems;
  target.renderRefProblems = renderRefProblems;
  target.searchResultHttpUrl = searchResultHttpUrl;
  target.admissionSettled = admissionSettled;
  target.forgetAdmission = forgetAdmission;
  target.outstandingAdmissions = outstandingAdmissions;
  target.reconcileLastAdmission = reconcileLastAdmission;
  target.rememberAdmission = rememberAdmission;
}

const hostWindow = (globalThis as unknown as { window?: SendTarget }).window;
if (hostWindow) installSend(hostWindow);

/** Capability check for this lane's window names. Uses `isReady`, not typeof. */
export function sendReady(
  target: SendTarget = globalThis as unknown as SendTarget,
): boolean {
  return isReady(target.send) && isReady(target.outstandingAdmissions);
}
