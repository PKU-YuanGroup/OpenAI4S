/**
 * openConversation — F-10 slice.
 *
 * The full function at app.js:7121-7218 also tears down timeline / notebook /
 * customize UI. This module:
 *   - resets the session-scoped store fields (imported, not edited)
 *   - clears `#messages` and the live stream
 *   - fetches the newest message page
 *   - paints it in rAF batches of 40 (replacing the 7166-7181 sync loop)
 *   - calls later-lane window exports through `isReady` (never `typeof fn`)
 */

import { isReady } from "../../compat/stub";
import { t } from "../../i18n/runtime";
import { _tbl, dockArtifact, _projArtFor, _editing } from "../../stores/artifacts";
import {
  _liveCell,
  cells,
  execSources,
  kernelFilter,
  kernels,
  lineage,
  liveCells,
  _lineageFor,
  variableInspector,
} from "../../stores/notebook";
import {
  _openGen,
  _msgEarlierLoading,
  _titleName,
  annotations,
  currentId,
  feedback,
  msgCursor,
  msgHasEarlier,
  project,
  sessions,
} from "../../stores/session";
import {
  permCards,
  planPending,
  planReady,
  planStatus,
  running,
  stepEls,
  stream as liveStream,
  _resumeTimer,
} from "../../stores/stream";
import {
  _branchActionLoading,
  _branchConversationTimer,
  _recoveryActionLoading,
  _timelineHistoryLoading,
  _timelineHistoryReq,
  _timelineRestoreFocusGroupId,
  _timelineView,
  _workbenchLoading,
  _workbenchReq,
  _workbenchTimer,
  actionTimeline,
  actionTimelineSelectedBranchId,
  actionTimelineSelectedGroupId,
  branchState,
  branchUndo,
  computeStatus,
  contextState,
  delegationState,
  executionIdentity,
  executionQueue,
  recoveryActions,
  recoveryState,
  securityState,
  workbenchErrors,
} from "../../stores/timeline";
import { activeTab, openTabs, provMode } from "../../stores/ui";
import { renderDockTabs, showDockPane } from "../artifacts/ui";
import { resetNotebookCellCaches } from "../notebook/chrome";
import { invalidateKernelCache } from "../notebook/kernel";
import { renderPlanCard } from "../send/plan";
import { closeTurnTicket, resumeWatch } from "../send/ticket";
import { failureHint, lastTerminalFailure } from "../send/turn";
import { hint } from "../sessions/chrome";
import { showWorkspace } from "../sessions/dashboard";
import {
  enableComposer,
  framePath,
  isMobile,
  navURL,
  setSidebar,
  setTitle,
  showConv,
} from "../sessions/dom";
import { loadSessions, renderSessions } from "../sessions/load";
import { renderProjMenu } from "../sessions/projects";
import { sub, unsub } from "../ws/connect";
import { apiGet, fetchRecentMessages, MESSAGE_PAGE_SIZE } from "./fetch";
import { ensureMessageDom, messagesHost } from "./dom";
import {
  cancelFramedRender,
  interleaveHistory,
  renderEmptySession,
  scheduleFramedRender,
  type StoredMessage,
} from "./list";
import { down, updateJumpPill } from "./scroll";

function callLane(name: string, ...args: unknown[]): unknown {
  const fn = (globalThis as Record<string, unknown>)[name];
  if (!isReady(fn)) return undefined;
  return (fn as (...a: unknown[]) => unknown)(...args);
}

function hideCancel(): void {
  try {
    const btn = document.getElementById("cancel-btn");
    if (btn) btn.classList.add("hidden");
  } catch {
    /* tests have no document */
  }
}

function resetSessionScoped(): void {
  liveStream.value = null;
  running.value = false;
  msgCursor.value = null;
  msgHasEarlier.value = false;
  _msgEarlierLoading.value = false;
  cells.value = [];
  kernels.value = [];
  liveCells.value = [];
  _liveCell.value = null;
  dockArtifact.value = null;
  kernelFilter.value = null;
  actionTimeline.value = null;
  actionTimelineSelectedGroupId.value = null;
  actionTimelineSelectedBranchId.value = null;
  executionQueue.value = null;
  executionIdentity.value = null;
  recoveryState.value = null;
  recoveryActions.value = null;
  delegationState.value = null;
  execSources.value = null;
  branchState.value = null;
  branchUndo.value = null;
  contextState.value = null;
  securityState.value = null;
  workbenchErrors.value = {};
  _timelineHistoryReq.value = (_timelineHistoryReq.value || 0) + 1;
  _timelineHistoryLoading.value = null;
  _recoveryActionLoading.value = null;
  _branchActionLoading.value = null;
  _timelineRestoreFocusGroupId.value = null;
  variableInspector.value = {
    language: "python",
    results: {},
    loading: null,
    error: "",
    request: 0,
  };
  if (_workbenchTimer.value != null) {
    clearTimeout(_workbenchTimer.value as ReturnType<typeof setTimeout>);
  }
  _workbenchReq.value = (_workbenchReq.value || 0) + 1;
  _workbenchLoading.value = null;
  _tbl.value = {};
  openTabs.value = [];
  activeTab.value = "notebook";
  provMode.value = false;
  lineage.value = null;
  _lineageFor.value = null;
  stepEls.value = Object.create(null);
  permCards.value = Object.create(null);
  planReady.value = null;
  planStatus.value = null;
  planPending.value = false;
  computeStatus.value = null;
  annotations.value = [];
  _editing.value = null;
  _timelineView.value = null;
}

async function fetchSteps(
  fid: string,
): Promise<Array<{ created_at?: number; seq?: number }>> {
  try {
    const sd = (await apiGet(`/frames/${encodeURIComponent(fid)}/steps`)) as {
      steps?: Array<{ created_at?: number; seq?: number }>;
    };
    return (sd && sd.steps) || [];
  } catch {
    return [];
  }
}

/**
 * app.js:7121. Returns a promise so E2E `await openConversation(fid)` still
 * waits for the first fetch; framed paint continues on later animation frames.
 */
export async function openConversation(
  fid: string,
  pid?: string | null,
): Promise<void> {
  if (_branchConversationTimer.value != null) {
    clearTimeout(_branchConversationTimer.value as ReturnType<typeof setTimeout>);
  }
  const previousFid = currentId.value;
  if (previousFid && previousFid !== fid) unsub(previousFid);
  resetNotebookCellCaches(previousFid, fid);
  if (pid && pid !== project.value) {
    project.value = pid;
    _projArtFor.value = null;
  }
  const found = (sessions.value as Array<{ id?: string; project_id?: string }>).find(
    (x) => x && x.id === fid,
  );
  navURL(framePath(fid, pid || project.value || found?.project_id));
  showWorkspace();
  showConv();
  renderProjMenu();
  if (isMobile()) setSidebar(true);

  ensureMessageDom();
  currentId.value = fid;
  const host = messagesHost();
  if (host) host.innerHTML = "";
  cancelFramedRender();
  closeTurnTicket();
  resetSessionScoped();
  enableComposer(true);
  hideCancel();
  // turnDone is the normal clear. resumeWatch resyncs through this opener when
  // the terminal WS event was missed, and must not leave markTurnStopping's
  // spinner up after the composer is already unlocked.
  hint("");
  if (_resumeTimer.value != null) {
    clearTimeout(_resumeTimer.value as ReturnType<typeof setTimeout>);
  }
  const gen = (_openGen.value || 0) + 1;
  _openGen.value = gen;
  callLane("destroyActionTimelineView");
  showDockPane("notebook");
  invalidateKernelCache();
  if (typeof document !== "undefined") {
    const badge = document.getElementById("compute-badge");
    if (badge) badge.remove();
    const banner = document.getElementById("compute-lost");
    if (banner) banner.remove();
  }
  callLane("refreshComputeStatus", fid);
  callLane("closeAnnotDraft");
  callLane("closeAnnotPop");
  callLane("updateAnnotBadge");
  callLane("edacTeardown");
  callLane("_molTeardown");
  try {
    const viewer = document.getElementById("dock-viewer");
    if (viewer) viewer.innerHTML = "";
  } catch {
    /* no document */
  }
  renderDockTabs();
  if (!sessions.value.length) {
    await loadSessions();
    if (gen !== _openGen.value) return;
  } else renderSessions();
  const row = (sessions.value as Array<{ id?: string; name?: string; task_summary?: string }>).find(
    (x) => x && x.id === fid,
  );
  _titleName.value =
    (row && (row.name || row.task_summary)) || t("conv.title.default");
  setTitle(_titleName.value);

  try {
    const fb = (await apiGet(`/frames/${encodeURIComponent(fid)}/feedback`)) as {
      feedback?: Record<string, unknown>;
    };
    if (gen !== _openGen.value) return;
    feedback.value = (fb && fb.feedback) || Object.create(null);
  } catch {
    if (gen !== _openGen.value) return;
    feedback.value = Object.create(null);
  }

  let msgCount = 0;
  try {
    const [d, steps] = await Promise.all([
      fetchRecentMessages(fid, MESSAGE_PAGE_SIZE),
      fetchSteps(fid),
    ]);
    if (gen !== _openGen.value) return;
    const msgs = ((d && d.messages) || []) as StoredMessage[];
    msgCount = msgs.length;
    msgCursor.value = d && d.next_before_seq != null ? d.next_before_seq : null;
    msgHasEarlier.value = !!(d && d.has_earlier);
    const items = interleaveHistory(msgs, steps);
    await new Promise<void>((resolve) => {
      scheduleFramedRender(items, {
        stillCurrent: () => gen === _openGen.value,
        onBatch: () => down(),
        onCancel: resolve,
        onDone: () => {
          callLane("paintEarlierControl");
          resolve();
        },
      });
    });
  } catch {
    /* a session must open even if history fails */
  }
  if (gen !== _openGen.value) return;
  if (!msgCount) renderEmptySession();
  callLane("loadArtifacts", fid);
  callLane("loadExecutionLog", fid);
  callLane("loadWorkbenchState", fid);
  down(true);
  updateJumpPill();
  void (async () => {
    try {
      await Promise.resolve(callLane("loadAnnotations", fid));
      if (gen !== _openGen.value) return;
      await Promise.resolve(callLane("reconcileLastAdmission", fid));
    } catch {
      /* annotation restoration is optional */
    }
  })();
  try {
    const stt = (await apiGet(`/frames/${encodeURIComponent(fid)}/status`)) as {
      running?: boolean;
      status?: string;
    };
    if (gen !== _openGen.value) return;
    if (stt && stt.running) {
      running.value = true;
      enableComposer(false);
      const btn = typeof document !== "undefined" && document.getElementById("cancel-btn");
      if (btn) btn.classList.remove("hidden");
      hint(t("conv.resuming.hint"), false, true);
      resumeWatch(fid, gen);
    } else if (stt && stt.status === "failed") {
      const last = lastTerminalFailure();
      if (last) hint(failureHint(last), true);
    }
  } catch {
    /* status is optional */
  }
  if (gen !== _openGen.value) return;
  try {
    const pj = (await apiGet(`/frames/${encodeURIComponent(fid)}/plan`)) as {
      plan?: unknown;
      status?: string;
    };
    if (gen !== _openGen.value) return;
    if (pj && pj.plan && pj.status && pj.status !== "discarded") {
      renderPlanCard(pj.plan, pj.status);
    }
  } catch {
    /* plan card is optional */
  }
  if (gen !== _openGen.value) return;
  try {
    sub(fid);
  } catch {
    /* no socket yet */
  }
}
