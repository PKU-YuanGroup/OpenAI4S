/**
 * Generation-scoped history reads. Navigation isolates sessions; background
 * reloads retain confirmed content until an off-DOM, framed render is ready.
 * Recovery is GET-only and reports messages, steps and run state separately.
 */

import { isReady } from "../../compat/stub";
import { historyT as t } from "./copy";
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
  historyLoad,
  historyContent,
  historyMutation,
  historyUnconfirmed,
  resetHistorySubmissions,
  type HistoryLoadResult,
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
  _seqSeen,
  _streamEpoch,
  _replayGap,
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
import { apiGet, fetchRecentMessages, recordRows, uniqueMessages, MESSAGE_PAGE_SIZE } from "./fetch";
import { ensureMessageDom, messagesHost } from "./dom";
import {
  cancelFramedRender,
  interleaveHistory,
  renderEmptySession,
  renderHistoryItem,
  scheduleFramedRender,
  type StoredMessage,
} from "./list";
import { ApiError } from "../sessions/api";
import { down, updateJumpPill } from "./scroll";
import { flushRender, type LiveStream } from "./stream";

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

const incomplete = (): HistoryLoadResult => ({
  messagesLoaded: false, stepsLoaded: false, runStateLoaded: false, superseded: false,
});
const obsolete = (): HistoryLoadResult => ({ ...incomplete(), superseded: true });
const current = (fid: string, gen: number): boolean => currentId.value === fid && _openGen.value === gen;

async function fetchSteps(fid: string): Promise<Array<Record<string, unknown>>> {
  const rows = recordRows(await apiGet(`/frames/${encodeURIComponent(fid)}/steps`), "steps");
  // list_steps projects these identity/order fields from every stored row.
  // Nullable descriptive fields and unknown kind/status values remain valid
  // for old records and extensions; input/output keep their existing payloads.
  if (rows.some((row) => typeof row.step_id !== "string" || typeof row.kind !== "string" ||
      !Number.isInteger(row.seq) || !Number.isInteger(row.created_at) ||
      ["title", "summary", "status"].some((key) => row[key] != null && typeof row[key] !== "string"))) {
    throw new ApiError({ error: "Invalid step record", code: "invalid_history_response" }, 200);
  }
  return rows;
}
async function fetchRunState(fid: string): Promise<{ running: boolean; status?: string }> {
  const raw = await apiGet(`/frames/${encodeURIComponent(fid)}/status`);
  if (!raw || typeof raw !== "object" || Array.isArray(raw) ||
      typeof (raw as { running?: unknown }).running !== "boolean" ||
      ("status" in raw && typeof raw.status !== "string")) {
    throw new ApiError({ error: "Invalid running state", code: "invalid_history_response" }, 200);
  }
  return raw as { running: boolean; status?: string };
}
function readError(error: unknown, fallback = "history.networkFailed"): string {
  const status = error instanceof ApiError ? error.status : 0;
  const key = status === 401 || status === 403 ? "history.authFailed"
    : status >= 500 ? "history.serviceUnavailable"
    : error instanceof ApiError ? "history.invalidResponse" : fallback;
  const detail = error instanceof Error && typeof error.message === "string"
    ? error.message.replace(/[\r\n]+/g, " ").slice(0, 180) : "";
  const id = error instanceof ApiError ? error.requestId.slice(0, 96) : "";
  return [t(key), detail, id ? `[${id}]` : ""].filter(Boolean).join(" ");
}

/** A previous successful empty read is not evidence for the new read. */
function removeEmptyHistoryDecoration(): void {
  messagesHost()?.querySelector?.(":scope > .empty-session")?.remove();
}

/** Prepare away from the live transcript; commit only a quiet, current read. */
async function loadHistory(fid: string, gen: number): Promise<HistoryLoadResult> {
  if (!current(fid, gen)) return obsolete();
  removeEmptyHistoryDecoration();
  const previous = historyLoad.value;
  const wasDeferred = previous?.fid === fid && previous.deferred;
  historyLoad.value = { ...incomplete(), fid, generation: gen, status: "loading", errors: {}, deferred: !!wasDeferred };
  const mutation = historyMutation.value;
  const seen = _seqSeen.value[fid];
  const epoch = _streamEpoch.value;
  const unchanged = (): boolean => current(fid, gen) && historyMutation.value === mutation &&
    _seqSeen.value[fid] === seen && _streamEpoch.value === epoch;
  const streamAtRead = liveStream.value;
  // Live notebook state at read time. A stopped read that still finds any of
  // it has missed the turn's terminal events, so it must finish their work.
  const liveAtRead = !!streamAtRead || (liveCells.value as unknown[]).length > 0 || !!_liveCell.value;
  const runStateRead = fetchRunState(fid);
  // A residual stream may have missed its terminal event. Read its status
  // first, then fetch a fresh transcript after the authoritative stopped
  // response; a parallel transcript could still precede the final write.
  const historyReady = streamAtRead ? runStateRead.then(() => {}, () => {}) : null;
  const [page, steps, runState] = await Promise.allSettled([
    historyReady ? historyReady.then(() => fetchRecentMessages(fid, MESSAGE_PAGE_SIZE)) : fetchRecentMessages(fid, MESSAGE_PAGE_SIZE),
    historyReady ? historyReady.then(() => fetchSteps(fid)) : fetchSteps(fid),
    runStateRead,
  ]);
  if (!current(fid, gen)) return obsolete();
  const result = {
    messagesLoaded: page.status === "fulfilled",
    stepsLoaded: steps.status === "fulfilled",
    runStateLoaded: runState.status === "fulfilled",
    superseded: false,
  };
  const errors: Record<string, string> = {};
  if (page.status === "rejected") errors.messages = readError(page.reason);
  if (steps.status === "rejected") errors.steps = readError(steps.reason);
  if (runState.status === "rejected") errors.runState = readError(runState.reason);
  const cached = historyContent.value?.fid === fid ? historyContent.value : null;
  // Without identities ordinary WS text cannot be joined to a REST tail.
  // Keep the confirmed DOM and the live stream until a stopped, unchanged GET.
  const stopped = runState.status === "fulfilled" && runState.value.running === false;
  const orphanedStream = (): boolean => !!streamAtRead && liveStream.value === streamAtRead && stopped;
  const busy = (): boolean => historyUnconfirmed.value > 0 || (!!liveStream.value && !orphanedStream());
  // A previously deferred transcript stays deferred while the turn it waited
  // for is still running. When the run state could not be read at all, the
  // client's own belief decides: with nothing running, the successful page
  // is the best evidence there is and must not be discarded.
  const runUnknown = runState.status !== "fulfilled" && !running.value;
  let deferred = !unchanged() || busy() || (!!wasDeferred && !stopped && !runUnknown);
  const retireOrphanedStream = (): void => {
    if (orphanedStream() && unchanged()) {
      flushRender(streamAtRead as LiveStream, true);
      liveStream.value = null;
    }
  };
  if (page.status === "fulfilled" && !deferred) {
    const newest = uniqueMessages(page.value.messages);
    const lower = Math.min(...newest.map((row) => typeof row.seq === "number" ? row.seq : Infinity));
    const cachedBelow = page.value.has_earlier !== false && Number.isFinite(lower)
      ? cached?.messages.filter((row) => typeof row.seq === "number" && row.seq < lower) || [] : [];
    // Reuse loaded earlier pages only when they reach the newest page: they
    // overlap it, or (seq is dense per frame) end exactly one row below it.
    // A cached window that stops short would be spliced under a hole.
    const overlap = cachedBelow.length > 0 && !!cached?.messages.some((row) => typeof row.seq === "number" && row.seq >= lower);
    const abuts = cachedBelow.length > 0 && Math.max(...cachedBelow.map((row) => row.seq as number)) + 1 === lower;
    const prefix = overlap || abuts ? cachedBelow : [];
    const messages = [...prefix, ...newest];
    const earlierCursor = prefix.length ? msgCursor.value : page.value.next_before_seq ?? null;
    const hasEarlier = prefix.length ? msgHasEarlier.value : !!page.value.has_earlier;
    const stepRows = steps.status === "fulfilled" ? steps.value : cached?.steps || [];
    const stage = typeof document !== "undefined" ? document.createDocumentFragment() : null;
    const host = messagesHost();
    const stagedSteps: Record<string, unknown> = Object.create(null);
    await new Promise<void>((resolve, reject) => {
      scheduleFramedRender(interleaveHistory(messages as StoredMessage[], stepRows), {
        host: stage,
        stillCurrent: unchanged,
        onCancel: resolve,
        onDone: resolve,
        onError: reject,
        renderItem: (item, target) => {
          // The step renderer's compatibility registry must keep pointing at
          // live cards while an off-DOM batch is being prepared.
          const liveSteps = stepEls.value;
          stepEls.value = stagedSteps;
          try { renderHistoryItem(item, target); }
          finally { stepEls.value = liveSteps; }
        },
      });
    });
    if (!current(fid, gen)) return obsolete();
    deferred = !unchanged() || busy();
    if (!deferred) {
      retireOrphanedStream();
      if (host && stage) {
        // Only history items and the retired stream are replaced. Artifact
        // tile strips, plan cards and other transcript-hosted UI keep their
        // nodes: the GET-only paths (turn end, replay gap, Retry) never
        // repaint them, unlike a full openConversation.
        const keep = (host.children ? Array.from(host.children) : []).filter((node) =>
          !node.classList?.contains("msg") && !node.classList?.contains("step") &&
          node.id !== "msgs-earlier" && !node.classList?.contains("empty-session") &&
          node !== (streamAtRead as LiveStream | null)?.wrap);
        host.replaceChildren(stage, ...keep);
      }
      stepEls.value = stagedSteps;
      historyContent.value = { fid, messages, steps: stepRows };
      msgCursor.value = earlierCursor;
      msgHasEarlier.value = hasEarlier;
      callLane("paintEarlierControl");
      // Failed auxiliary history is not evidence for a truly empty session.
      if (!messages.length && result.stepsLoaded && result.runStateLoaded && stopped) renderEmptySession();
      down();
    }
  }
  if (deferred) {
    result.messagesLoaded = false;
    errors.messages = t(historyUnconfirmed.value > 0 ? "history.submissionPending" : "history.livePending");
  } else if (page.status !== "fulfilled") {
    // The transcript could not be re-read, but the run is over: the stream
    // it belonged to still gets its cursor removed and message actions.
    retireOrphanedStream();
  }
  if (!current(fid, gen)) return obsolete();
  // Status failure never implies task completion or unlocks a running composer.
  if (runState.status === "fulfilled" && unchanged() && (runState.value.running || !busy())) {
    running.value = runState.value.running;
    enableComposer(!runState.value.running);
    if (runState.value.running) {
      const btn = typeof document !== "undefined" && document.getElementById("cancel-btn");
      if (btn) btn.classList.remove("hidden");
      hint(t("conv.resuming.hint"), false, true);
      resumeWatch(fid, gen);
    } else {
      closeTurnTicket(); hideCancel();
      // Mirrors turnDone: the "Stopping…"/"Resuming…" spinner belongs to the
      // turn this read just found finished, whichever path reached here.
      const last = runState.value.status === "failed" ? lastTerminalFailure() : null;
      hint(last ? failureHint(last) : "", !!last);
      if (liveAtRead) {
        // The terminal events were missed (replay gap, watchdog): finish
        // what they would have done for the Notebook and the Files pane.
        liveCells.value = [];
        _liveCell.value = null;
        callLane("loadExecutionLog", fid);
        callLane("loadArtifacts", fid);
      }
    }
  }
  const complete = result.messagesLoaded && result.stepsLoaded && result.runStateLoaded;
  historyLoad.value = { ...result, fid, generation: gen,
    // A deferred read failed nothing; it is waiting, which is "partial".
    status: complete ? "loaded" : deferred || result.messagesLoaded || cached ? "partial" : "error",
    errors, deferred };
  if (complete && _replayGap.value === fid) _replayGap.value = null;
  updateJumpPill();
  return result;
}

let recovery: { fid: string; gen: number; promise: Promise<HistoryLoadResult> } | null = null;
let terminalAlignment: { fid: string; gen: number } | null = null;
/** Automatic re-reads left for a conversation deferred while nothing is running. */
let idleRetries: { fid: string; gen: number; left: number } | null = null;
const IDLE_RETRIES = 2;

const idle = (): boolean => !liveStream.value && historyUnconfirmed.value === 0 && !running.value;

/** One terminal event can request one fresh read after an in-flight GET settles. */
export function alignHistoryAfterTurn(fid: string, gen = _openGen.value): void {
  if (!current(fid, gen) || !(historyLoad.value?.deferred || historyLoad.value?.status === "loading")) return;
  if (recovery?.fid === fid && recovery.gen === gen) terminalAlignment = { fid, gen };
  else void recoverConversation(fid, gen);
}

/** GET-only retry, coalesced for one conversation/opening generation. */
export function recoverConversation(fid: string, gen = _openGen.value): Promise<HistoryLoadResult> {
  if (!current(fid, gen)) return Promise.resolve(obsolete());
  if (recovery?.fid === fid && recovery.gen === gen) return recovery.promise;
  const promise = loadHistory(fid, gen).catch((error: unknown) => {
    if (!current(fid, gen)) return obsolete();
    const result = incomplete();
    historyLoad.value = { ...result, fid, generation: gen,
      status: historyContent.value?.fid === fid ? "partial" : "error",
      errors: { messages: readError(error, "history.renderFailed") }, deferred: false };
    return result;
  });
  const own = { fid, gen, promise };
  recovery = own;
  void promise.then(() => {
    if (recovery !== own) return;
    recovery = null;
    const stillDeferred = current(fid, gen) && !!historyLoad.value?.deferred && idle();
    if (!historyLoad.value?.deferred) idleRetries = null;
    if (terminalAlignment?.fid === fid && terminalAlignment.gen === gen) {
      terminalAlignment = null;
      if (stillDeferred) void recoverConversation(fid, gen);
      return;
    }
    // Every frame event carries a seq (kernel status, queue, background
    // artifacts), so a read can be deferred on a session where no turn will
    // ever end to realign it. Re-read a bounded number of times; the banner
    // and its Retry button remain for anything that keeps moving.
    if (stillDeferred) {
      if (idleRetries?.fid !== fid || idleRetries.gen !== gen) idleRetries = { fid, gen, left: IDLE_RETRIES };
      if (idleRetries.left > 0) {
        idleRetries.left -= 1;
        void recoverConversation(fid, gen);
      }
    }
  });
  return promise;
}

/** Open another conversation, or reload this one while preserving confirmed content. */
export async function openConversation(
  fid: string, pid?: string | null, options?: { resetHistory?: boolean },
): Promise<HistoryLoadResult> {
  if (_branchConversationTimer.value != null) clearTimeout(_branchConversationTimer.value as ReturnType<typeof setTimeout>);
  const previousFid = currentId.value;
  const switching = previousFid !== fid;
  if (previousFid && switching) unsub(previousFid);
  if (switching) resetNotebookCellCaches(previousFid, fid);
  if (pid && pid !== project.value) { project.value = pid; _projArtFor.value = null; }
  const found = (sessions.value as Array<{ id?: string; project_id?: string }>).find((x) => x?.id === fid);
  navURL(framePath(fid, pid || project.value || found?.project_id));
  showWorkspace(); showConv(); renderProjMenu();
  if (isMobile()) setSidebar(true);
  ensureMessageDom();
  currentId.value = fid;
  cancelFramedRender();
  const gen = (_openGen.value || 0) + 1;
  _openGen.value = gen;
  // The previous generation's paging request can no longer publish or clear
  // its loading flag, including a background reopen of this same session.
  _msgEarlierLoading.value = false;
  removeEmptyHistoryDecoration();
  historyLoad.value = { ...incomplete(), fid, generation: gen, status: "loading", errors: {},
    deferred: !switching && !options?.resetHistory && !!historyLoad.value?.deferred };
  if (options?.resetHistory && !switching) {
    // Branch activation/revert changes which records are visible within the
    // same frame: messages, but also cells, artifacts, plan and dock state.
    // Its previous transcript cannot seed the replacement page, and the
    // execution-log merge keeps any local cell the server no longer lists.
    const host = messagesHost();
    if (host) host.innerHTML = "";
    if (liveStream.value) flushRender(liveStream.value as LiveStream, true);
    resetSessionScoped();
    historyContent.value = null;
    historyMutation.value += 1;
  }
  if (switching) {
    const host = messagesHost();
    if (host) host.innerHTML = "";
    closeTurnTicket(); resetSessionScoped();
    historyContent.value = null; historyMutation.value = 0; resetHistorySubmissions();
    enableComposer(true); hideCancel(); hint("");
    if (_resumeTimer.value != null) clearTimeout(_resumeTimer.value as ReturnType<typeof setTimeout>);
    callLane("destroyActionTimelineView"); showDockPane("notebook"); invalidateKernelCache();
    if (typeof document !== "undefined") {
      document.getElementById("compute-badge")?.remove();
      document.getElementById("compute-lost")?.remove();
      const viewer = document.getElementById("dock-viewer");
      if (viewer) viewer.innerHTML = "";
    }
    callLane("closeAnnotDraft"); callLane("closeAnnotPop"); callLane("updateAnnotBadge");
    callLane("edacTeardown"); callLane("_molTeardown"); renderDockTabs();
  }
  callLane("refreshComputeStatus", fid);
  if (!sessions.value.length) {
    try { await loadSessions(); } catch { /* history has its own independently reported reads */ }
    if (!current(fid, gen)) return obsolete();
  } else renderSessions();
  const row = (sessions.value as Array<{ id?: string; name?: string; task_summary?: string }>).find((x) => x?.id === fid);
  _titleName.value = row?.name || row?.task_summary || t("conv.title.default");
  setTitle(_titleName.value);
  try {
    const fb = await apiGet(`/frames/${encodeURIComponent(fid)}/feedback`) as { feedback?: Record<string, unknown> };
    if (!current(fid, gen)) return obsolete();
    feedback.value = fb?.feedback || Object.create(null);
  } catch {
    if (!current(fid, gen)) return obsolete();
    if (switching) feedback.value = Object.create(null);
  }
  const result = await recoverConversation(fid, gen);
  if (!current(fid, gen)) return obsolete();
  callLane("loadArtifacts", fid); callLane("loadExecutionLog", fid); callLane("loadWorkbenchState", fid);
  void (async () => {
    try {
      await Promise.resolve(callLane("loadAnnotations", fid));
      if (current(fid, gen)) await Promise.resolve(callLane("reconcileLastAdmission", fid));
    } catch { /* annotation restoration is optional */ }
  })();
  try {
    const pj = await apiGet(`/frames/${encodeURIComponent(fid)}/plan`) as { plan?: unknown; status?: string };
    if (!current(fid, gen)) return obsolete();
    if (pj?.plan && pj.status && pj.status !== "discarded") renderPlanCard(pj.plan, pj.status);
  } catch {
    if (!current(fid, gen)) return obsolete();
  }
  if (!current(fid, gen)) return obsolete();
  try { sub(fid); } catch { /* no socket yet */ }
  return result;
}
