/** openConversation, newSession, resumeWatch, routing. app.js:7087-7219, 2678-2706, 13231-13248. */

import { t } from "../../i18n";
import { dockArtifact, _projArtFor, _tbl } from "../../stores/artifacts";
import { defaultModelName } from "../../stores/customize";
import {
  _lineageFor,
  cells,
  execSources,
  kernelFilter,
  kernels,
  lineage,
  liveCells,
  _liveCell,
  variableInspector,
} from "../../stores/notebook";
import {
  _msgEarlierLoading,
  _openGen,
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
  _resumeTimer,
  _resumeTok,
  pendingExecutionId,
  pendingRequestId,
  permCards,
  stepEls,
  planPending,
  planReady,
  planStatus,
  running,
  stream,
  turnTicket,
} from "../../stores/stream";
import {
  _branchActionLoading,
  _branchConversationTimer,
  _recoveryActionLoading,
  _timelineHistoryLoading,
  _timelineHistoryReq,
  _timelineRestoreFocusGroupId,
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
import { resetNotebookCellCaches } from "../notebook/chrome";
import { sub, unsub } from "../ws/connect";
import { api, apiErrorText } from "./api";
import { binds } from "./binds";
import { hint } from "./chrome";
import { showDashboard, showWorkspace } from "./dashboard";
import {
  $,
  down,
  enableComposer,
  framePath,
  isMobile,
  navURL,
  setSidebar,
  setTitle,
  showConv,
  updateJumpPill,
} from "./dom";
import { callLane } from "./lane";
import { loadProjects, loadSessions, renderSessions } from "./load";
import { fetchRecentMessages, paintEarlierControl } from "./messages";
import { MESSAGE_PAGE_SIZE, type SessionLike } from "./paging";
import { renderProjMenu } from "./projects";
import { renderEmptySession, renderStored } from "./transcript";

function showDockPane(pane: string): void {
  ["viewer", "notebook", "timeline", "files"].forEach((p) => {
    const n = $("#dock-" + p);
    if (n) n.classList.toggle("hidden", p !== pane);
  });
}

export function resumeWatch(fid: string, gen: number): void {
  clearTimeout(_resumeTimer.value as ReturnType<typeof setTimeout>);
  const tok = (_resumeTok.value || 0) + 1;
  _resumeTok.value = tok;
  const stale = () =>
    tok !== _resumeTok.value || gen !== _openGen.value || currentId.value !== fid || !running.value;
  const tick = async () => {
    if (stale()) return;
    let still = true;
    try {
      const stt = (await api(`/frames/${fid}/status`)) as { running?: boolean };
      still = !!(stt && stt.running);
    } catch {
      still = true;
    }
    if (stale()) return;
    if (!still) {
      void openConversation(fid, project.value);
      return;
    }
    _resumeTimer.value = setTimeout(tick, 2000);
  };
  _resumeTimer.value = setTimeout(tick, 2000);
}

export async function newSession(): Promise<void> {
  try {
    const f = (await api("/frames", {
      method: "POST",
      body: JSON.stringify({
        project_id: project.value || undefined,
        model: defaultModelName.value,
      }),
    })) as { id: string };
    await loadSessions();
    await openConversation(f.id, project.value);
    $("#composer")?.focus();
  } catch (e) {
    hint(t("folder.create.failed", apiErrorText(e)), true);
  }
}

export async function openConversation(fid: string, pid?: string | null): Promise<void> {
  clearTimeout(_branchConversationTimer.value as ReturnType<typeof setTimeout>);
  const previousFid = currentId.value;
  if (previousFid && previousFid !== fid) unsub(previousFid);
  resetNotebookCellCaches(previousFid, fid);
  if (pid && pid !== project.value) {
    project.value = pid;
    _projArtFor.value = null;
  }
  const found = (sessions.value as SessionLike[]).find((x) => x.id === fid);
  navURL(framePath(fid, pid || project.value || found?.project_id));
  showWorkspace();
  showConv();
  renderProjMenu();
  if (isMobile()) setSidebar(true);
  currentId.value = fid;
  const messages = $("#messages");
  if (messages) messages.innerHTML = "";
  stream.value = null;
  callLane("closeTurnTicket");
  msgCursor.value = null;
  msgHasEarlier.value = false;
  _msgEarlierLoading.value = false;
  running.value = false;
  enableComposer(true);
  $("#cancel-btn")?.classList.add("hidden");
  clearTimeout(_resumeTimer.value as ReturnType<typeof setTimeout>);
  const gen = (_openGen.value || 0) + 1;
  _openGen.value = gen;
  cells.value = [];
  kernels.value = [];
  liveCells.value = [];
  _liveCell.value = null;
  dockArtifact.value = null;
  kernelFilter.value = null;
  callLane("destroyActionTimelineView");
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
    results: Object.create(null) as Record<string, unknown>,
    loading: null,
    error: "",
    request: 0,
  };
  clearTimeout(_workbenchTimer.value as ReturnType<typeof setTimeout>);
  _workbenchReq.value = (_workbenchReq.value || 0) + 1;
  _workbenchLoading.value = null;
  _tbl.value = {};
  callLane("invalidateKernelCache");
  openTabs.value = [];
  activeTab.value = "notebook";
  provMode.value = false;
  lineage.value = null;
  _lineageFor.value = null;
  showDockPane("notebook");
  stepEls.value = Object.create(null) as Record<string, unknown>;
  callLane("renderDockTabs");
  pendingRequestId.value = null;
  pendingExecutionId.value = null;
  turnTicket.value = 0;
  permCards.value = Object.create(null) as Record<string, unknown>;
  planReady.value = null;
  planStatus.value = null;
  planPending.value = false;
  computeStatus.value = null;
  $("#compute-badge")?.remove();
  $("#compute-lost")?.remove();
  callLane("refreshComputeStatus", fid);
  annotations.value = [];
  callLane("closeAnnotDraft");
  callLane("closeAnnotPop");
  callLane("updateAnnotBadge");
  callLane("edacTeardown");
  callLane("_molTeardown");
  const viewer = $("#dock-viewer");
  if (viewer) viewer.innerHTML = "";
  if (!(sessions.value as unknown[]).length) await loadSessions();
  else renderSessions();
  const f = (sessions.value as SessionLike[]).find((x) => x.id === fid);
  _titleName.value = (f && (f.name || f.task_summary)) || t("conv.title.default");
  setTitle(_titleName.value);
  try {
    const fb = (await api(`/frames/${fid}/feedback`)) as { feedback?: Record<string, unknown> };
    feedback.value = (fb && fb.feedback) || Object.create(null);
  } catch {
    feedback.value = Object.create(null);
  }
  let msgCount = 0;
  try {
    const [d, sd] = await Promise.all([
      fetchRecentMessages(fid, MESSAGE_PAGE_SIZE),
      api(`/frames/${fid}/steps`).catch(() => ({ steps: [] })),
    ]);
    if (gen !== _openGen.value) return;
    const msgs = (d && d.messages) || [];
    msgCount = msgs.length;
    msgCursor.value = d && d.next_before_seq != null ? d.next_before_seq : null;
    msgHasEarlier.value = !!(d && d.has_earlier);
    const steps = ((sd as { steps?: unknown[] }) && (sd as { steps?: unknown[] }).steps) || [];
    const items: Array<{ t: number; seq: number; kind: string; v: unknown }> = [];
    msgs.forEach((mm) =>
      items.push({
        t: new Date(mm.created_at || "").getTime() || 0,
        seq: 1e15,
        kind: "msg",
        v: mm,
      }),
    );
    steps.forEach((s) => {
      const rec = s as { created_at?: number; seq?: number };
      items.push({ t: rec.created_at || 0, seq: rec.seq || 0, kind: "step", v: s });
    });
    items.sort((a, b) => a.t - b.t || a.seq - b.seq);
    items.forEach((it) => {
      if (it.kind === "msg") renderStored(it.v as Parameters<typeof renderStored>[0]);
      else callLane("renderStoredStep", it.v);
    });
    paintEarlierControl();
  } catch {
    /* history load is best-effort */
  }
  if (gen !== _openGen.value) return;
  if (!msgCount) renderEmptySession();
  callLane("loadArtifacts", fid);
  callLane("loadExecutionLog", fid);
  callLane("loadWorkbenchState", fid);
  down(true);
  updateJumpPill();
  void (async () => {
    await callLane("loadAnnotations", fid);
    await callLane("reconcileLastAdmission", fid);
  })();
  try {
    const stt = (await api(`/frames/${fid}/status`)) as { running?: boolean; status?: string };
    if (gen !== _openGen.value) return;
    if (stt && stt.running) {
      running.value = true;
      enableComposer(false);
      $("#cancel-btn")?.classList.remove("hidden");
      hint(t("conv.resuming.hint"), false, true);
      resumeWatch(fid, gen);
    } else if (stt && stt.status === "failed") {
      const last = callLane("lastTerminalFailure");
      if (last) hint(String(callLane("failureHint", last) || last), true);
    }
  } catch {
    /* status is optional */
  }
  try {
    const pj = (await api(`/frames/${fid}/plan`)) as {
      plan?: unknown;
      status?: string;
    };
    if (gen !== _openGen.value) return;
    if (pj && pj.plan && pj.status && pj.status !== "discarded") {
      callLane("renderPlanCard", pj.plan, pj.status);
    }
  } catch {
    /* plan card is optional */
  }
  sub(fid);
}

export async function routeInitialView(): Promise<void> {
  const path = (typeof location !== "undefined" && location.pathname) || "/";
  const fm = path.match(/^\/projects\/([^/]+)\/frames\/([^/]+)/);
  if (fm) {
    const pid = decodeURIComponent(fm[1] || "");
    const fid = decodeURIComponent(fm[2] || "");
    await loadProjects();
    project.value = pid;
    showWorkspace();
    await loadSessions();
    renderProjMenu();
    await openConversation(fid, pid);
    return;
  }
  const pm = path.match(/^\/projects\/([^/]+)\/?$/);
  if (pm) {
    const pid = decodeURIComponent(pm[1] || "");
    const { openProject } = await import("./projects");
    await openProject(pid);
    return;
  }
  showDashboard();
}

binds.openConversation = openConversation;
binds.newSession = newSession;
