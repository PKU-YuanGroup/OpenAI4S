import { contractStub } from "./stub";
export { contractStub, isContractStub, isReady } from "./stub";

import {
  ACTION_TIMELINE_OVERSCAN,
  ACTION_TIMELINE_OVERVIEW_WIDTH,
  ACTION_TIMELINE_PAGE_SIZE,
  ACTION_TIMELINE_ROW_HEIGHT,
} from "../stores/timeline";
import { createSProxy } from "../stores/registry";

/**
 * F-01 bare window globals (`tests/webui-contract.md` §1), sorted by name.
 * `migration.test.ts` diffs this list against the contract — do not invent names.
 */
export const CONTRACT_GLOBAL_NAMES = [
  "ACTION_TIMELINE_OVERSCAN",
  "ACTION_TIMELINE_OVERVIEW_WIDTH",
  "ACTION_TIMELINE_PAGE_SIZE",
  "ACTION_TIMELINE_ROW_HEIGHT",
  "S",
  "actionTimelineEntryKey",
  "actionTimelineOverviewVisualExtent",
  "actionTimelineSelectionOverlaps",
  "actionTimelineSpan",
  "admissionSettled",
  "annotationIsHeld",
  "annotationStatus",
  "buildExecutedCodeView",
  "buildStepCard",
  "commitActionTimelineOverviewSelection",
  "custTab",
  "execSourcesState",
  "fetchAllMessages",
  "fetchOlderMessages",
  "fetchRecentMessages",
  "forgetAdmission",
  "highlightTraceback",
  "loadAnnotations",
  "loadEarlierActionTimeline",
  "mergeDelegationChildEvent",
  "notebookExportLink",
  "onEvent",
  "openAnnotations",
  "openConversation",
  "openCust",
  "openPinPop",
  "outstandingAdmissions",
  "parseTable",
  "reconcileLastAdmission",
  "rememberAdmission",
  "renderActionTimeline",
  "renderAttachmentProblems",
  "renderComposerRefChips",
  "renderDelegationPanel",
  "renderMd",
  "renderMessageRefChips",
  "renderPins",
  "renderRefProblems",
  "renderSheet",
  "sanitizeActionTimeline",
  "searchResultHttpUrl",
  "selectExecFrame",
  "send",
  "setActiveTab",
  "steerDelegationChild",
  "t",
  "telemetryRow",
  "timelineOverviewTimeToX",
  "toggleActionTimelineTurn",
  "updateActionTimelineLedger",
] as const;

export type ContractGlobalName = (typeof CONTRACT_GLOBAL_NAMES)[number];

export type WindowExportsTarget = Record<string, unknown>;

const TIMELINE_CONSTANTS: Record<string, number> = {
  ACTION_TIMELINE_OVERSCAN,
  ACTION_TIMELINE_OVERVIEW_WIDTH,
  ACTION_TIMELINE_PAGE_SIZE,
  ACTION_TIMELINE_ROW_HEIGHT,
};

/**
 * Install the F-01 window export surface onto `target` (browser `window`,
 * or a fresh object in Vitest). Always writes `S` and the four
 * ACTION_TIMELINE_* constants. Function names are filled only when missing
 * so a later lane that already assigned a real implementation is kept.
 */
export function installWindowExports(
  target: WindowExportsTarget = globalThis as unknown as WindowExportsTarget,
): WindowExportsTarget {
  target.S = createSProxy();
  for (const [name, value] of Object.entries(TIMELINE_CONSTANTS)) {
    target[name] = value;
  }
  for (const name of CONTRACT_GLOBAL_NAMES) {
    if (name === "S" || name in TIMELINE_CONSTANTS) continue;
    if (target[name] == null) target[name] = contractStub(name);
  }
  return target;
}

const browserWindow = (globalThis as unknown as { window?: WindowExportsTarget }).window;
if (browserWindow) installWindowExports(browserWindow);

export {
  ACTION_TIMELINE_OVERSCAN,
  ACTION_TIMELINE_OVERVIEW_WIDTH,
  ACTION_TIMELINE_PAGE_SIZE,
  ACTION_TIMELINE_ROW_HEIGHT,
};

// === lane additions ===
// Later F-series lanes may append one window export assignment per line below.
// Do not edit anything above this marker.
// F-06: window.onEvent is assigned by bootWs() in features/ws (imported from main.tsx).
// F-07: window.t / window.tOptional are assigned by the i18n module.
// F-10: window.openConversation / down assigned by features/messages.
// F-13: fetchAllMessages / fetchOlderMessages / fetchRecentMessages /
//   renderMessageRefChips / renderComposerRefChips / hint assigned by
//   features/sessions. Both lanes ported openConversation and the fetch
//   trio independently; integration split them so each name has one owner
//   (main.tsx imports sessions before messages, so a shared name would
//   have been silently overwritten rather than reported).
// F-14: window.highlightTraceback / window.notebookExportLink are assigned by features/notebook (imported from main.tsx).
// F-15: timeline contract names plus loadWorkbenchState are assigned by
// features/timeline (imported from main.tsx).
// F-17: window.parseTable / window.renderSheet are assigned by bootArtifacts() in features/artifacts.
// F-19: window.openCust / window.custTab / window.telemetryRow are assigned by bootCustomize() in features/customize.
// F-20: window.openModalEl / closeModalEl / trapModalKeydown / openPalette /
// closePalette / applyLayout / setLayout / uploadFiles / micDictate / loadNotes
// are assigned by bootChrome() in features/chrome (imported from main.tsx).
// F-12: window.ac / edacTeardown / bindEditorAutocomplete are assigned by
// features/autocomplete (imported from main.tsx).
// F-11: window.send / buildStepCard / renderAttachmentProblems /
//   renderRefProblems / searchResultHttpUrl / admissionSettled /
//   forgetAdmission / outstandingAdmissions / reconcileLastAdmission /
//   rememberAdmission are assigned by features/send (imported from main.tsx).
// F-16: window.buildExecutedCodeView / execSourcesState / selectExecFrame /
//   toggleExecutedCode / showProvenance / renderProvenanceInto /
//   renderNotebook are assigned by bootExecution() in features/execution.
// M-01: first-run wizard is mounted by bootOnboarding() in features/onboarding.
// F-18: window.molecule / renderAnnotatableImage / annotationStatus /
//   annotationIsHeld / openAnnotations / loadAnnotations / renderPins /
//   openPinPop / openKetcher / renderLocatorComments assigned by
//   bootIslands() in islands (imported from main.tsx).
