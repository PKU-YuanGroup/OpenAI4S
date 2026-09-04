/**
 * Imperative Action Timeline island.
 * Port of app.js:2920-5154: queue, workbench load, overview SVG, virtualized
 * ledger (46px / overscan / signature reuse / translateY), inspector, and the
 * branch / recovery / context / security / delegation / compute panels.
 *
 * Components only supply #dock-timeline and a mount/unmount. This module owns
 * `_timelineView` identity and the window-exported function names.
 */

import { LANG, t, tOptional } from "../../i18n/runtime";
import { publicText } from "../scrub/scrub";
import { _kc, cells, liveCells, pendingReplIdentity } from "../../stores/notebook";
import {
  ACTION_TIMELINE_OVERSCAN,
  ACTION_TIMELINE_OVERVIEW_WIDTH,
  ACTION_TIMELINE_PAGE_SIZE,
  ACTION_TIMELINE_ROW_HEIGHT,
} from "../../stores/timeline";
import { api, apiErrorText, hint, laneCall, optionalApi } from "./api";
import { $, bytes, el, ghostIconBtn, iconEl, svgElement } from "./dom";
import {
  ACTION_TIMELINE_BOTTOM_THRESHOLD,
  ACTION_TIMELINE_OVERVIEW_HEIGHT,
  ACTION_TIMELINE_OVERVIEW_HOVER_DELAY,
  ACTION_TIMELINE_TOP_THRESHOLD,
  actionTimelineEntryKey,
  actionTimelineLedgerEntries,
  actionTimelineOverviewModel,
  actionTimelineOverviewVisualExtent,
  actionTimelineSelectionOverlaps,
  latestActionTimelineAttempt,
  timelineCost,
  timelineDuration,
  timelineEpochMs,
  timelineOverviewExactDuration,
  timelineOverviewExactTime,
  timelineOverviewItemPaths,
  timelineOverviewTimeToX,
  timelineOverviewXToDomainTime,
  timelineOverviewXToTime,
  timelineTokenTotal,
} from "./model";
import { S } from "./s";
import {
  branchUndoFromProjection,
  mergeActionTimelines,
  RECOVERY_ACTION_IDS,
  sanitizeActionTimeline,
  sanitizeBranches,
  sanitizeComputeTasks,
  sanitizeContext,
  sanitizeDelegations,
  sanitizeExecutionQueue,
  sanitizeRecovery,
  sanitizeRecoveryActions,
  sanitizeRevertMutationResult,
  sanitizeRevertPreview,
  sanitizeSecurity,
  timelineOrdinal,
} from "./sanitize";
import type { ActionTimeline, DelegationState, TimelineGroup } from "./types";

type View = any;
type Group = TimelineGroup & Record<string, any>;

function invalidateKernelCache(): void {
  const cache = _kc.value;
  cache.id = null;
  cache.st = null;
  cache.stAt = 0;
  cache.envs = null;
  cache.cur = null;
  cache.envAt = 0;
}

function nbCellKey(cell: any): string {
  if (cell && (cell.producing_cell_id || cell.cell_id))
    return String(cell.producing_cell_id || cell.cell_id);
  return (
    "legacy:" +
    String((cell && cell.kernel_id) || "python") +
    ":" +
    String(cell && cell.cell_index != null ? cell.cell_index : "?")
  );
}

function nbFindCell(producingCellId: unknown): any {
  const key = String(producingCellId || "");
  const live = (liveCells.value || []) as any[];
  const stored = (cells.value || []) as any[];
  return (
    live.find((cell) => nbCellKey(cell) === key) ||
    stored.find((cell) => nbCellKey(cell) === key) ||
    null
  );
}

export function timelineKind(group: any): string {
  const kind = String((group && group.kind) || "").toLowerCase();
  const eventKinds = ((group && group.events) || [])
    .map((event: any) => String(event.type || "").toLowerCase())
    .join(" ");
  const latestAttempt = latestActionTimelineAttempt(group),
    linkedCell = latestAttempt && nbFindCell(latestAttempt.producing_cell_id);
  const language = String(
    group.language || (linkedCell && linkedCell.language) || "",
  ).toLowerCase();
  if (/final/.test(kind + " " + eventKinds)) return "finalize";
  if (/permission|approval/.test(kind + " " + eventKinds)) return "permission";
  if (/recover|restore|bootstrap/.test(kind + " " + eventKinds)) return "recovery";
  if (/delegat|subagent/.test(kind + " " + eventKinds)) return "delegate";
  if (/background|remote|compute|job/.test(kind + " " + eventKinds)) return "background";
  if (/dynamic/.test(kind + " " + eventKinds)) return "dynamic_tool";
  if (language === "r" || /\br\b|r_cell|rcode/.test(kind)) return "r";
  if (/code|python|cell/.test(kind)) return "python";
  if (/tool/.test(kind + " " + eventKinds)) return "native_tool";
  return "action";
}

function timelineKindIcon(kind: string): string {
  if (kind === "delegate") return "users";
  if (kind === "permission") return "lock";
  if (kind === "recovery") return "refresh";
  if (kind === "finalize") return "check";
  if (kind === "native_tool" || kind === "dynamic_tool") return "sliders";
  return "terminal";
}

export function shortRuntime(value: unknown): string {
  const text = publicText(value, 96);
  return text ? (text.length > 12 ? text.slice(0, 8) + "…" : text) : t("runtime.none");
}

function queueRowLabel(item: any): string {
  const meta = item.metadata || {};
  const bits: string[] = [];
  if (meta.model_profile_id)
    bits.push(
      t(
        "queue.underProfile",
        meta.model_profile_id,
        meta.model_profile_revision == null ? "?" : meta.model_profile_revision,
      ),
    );
  if (item.branch_id) bits.push(t("queue.onBranch", item.branch_id));
  bits.push(item.execution_id);
  return bits.join(" · ");
}

export function renderQueueStrip(): void {
  const box = $("#queue-strip");
  if (!box) return;
  const queue = ((S.executionQueue || {}).queue || []).filter(
    (item: any) => (item.owner || {}).kind === "agent",
  );
  box.innerHTML = "";
  box.classList.toggle("hidden", !queue.length);
  if (!queue.length) return;
  box.appendChild(el("div", "queue-head", t("queue.waiting", queue.length)));
  queue.forEach((item: any) => {
    const row = el("div", "queue-row");
    row.appendChild(
      el(
        "span",
        "queue-pos",
        "#" + (item.queue_position == null ? "?" : item.queue_position),
      ),
    );
    row.appendChild(
      el("span", "queue-preview", item.metadata.preview || t("queue.noPreview")),
    );
    const meta = el("span", "queue-meta", queueRowLabel(item));
    meta.title = queueRowLabel(item);
    row.appendChild(meta);
    const drop = el("button", "icon-ghost queue-cancel") as HTMLButtonElement;
    drop.title = t("queue.cancelOne");
    drop.appendChild(iconEl("x", 13) as Node);
    drop.onclick = () => {
      void cancelQueuedExecution(item);
    };
    row.appendChild(drop);
    box.appendChild(row);
  });
}

async function cancelQueuedExecution(item: any): Promise<void> {
  const fid = S.currentId;
  if (!fid || !item || !item.execution_id || !(item.owner || {}).id) return;
  try {
    const r = (await api(`/frames/${fid}/cancel`, {
      method: "POST",
      body: JSON.stringify({
        execution_id: item.execution_id,
        owner: { kind: item.owner.kind, id: item.owner.id },
        reason: "queued follow-up dropped by user",
      }),
    })) as { ok?: boolean; reason?: string };
    if (!r || r.ok !== true) {
      hint(t("queue.cancelFailed", (r && r.reason) || ""), true);
      return;
    }
    const bubble = [...document.querySelectorAll(".msg.user")].find(
      (n) => (n as HTMLElement).dataset.executionId === item.execution_id,
    );
    if (bubble) bubble.classList.add("cancelled");
    hint(t("queue.cancelled"));
  } catch (e) {
    hint(t("queue.cancelFailed", apiErrorText(e)), true);
  }
}

export function rememberExecutionQueue(payload: unknown): any {
  S.executionQueue = sanitizeExecutionQueue(payload);
  const ticket = S.executionQueue.owner;
  S.executionIdentity =
    ticket && ticket.execution_id && ticket.owner && ticket.owner.kind && ticket.owner.id
      ? { execution_id: ticket.execution_id, owner: { kind: ticket.owner.kind, id: ticket.owner.id } }
      : null;
  renderQueueStrip();
  return S.executionQueue;
}

export function rememberExecutionState(event: any): void {
  const status = String((event && event.status) || "").toLowerCase();
  const identity =
    event && event.execution_id && event.owner && event.owner.kind && event.owner.id
      ? {
          execution_id: publicText(event.execution_id, 96),
          owner: {
            kind: publicText(event.owner.kind, 48),
            id: publicText(event.owner.id, 96),
          },
        }
      : null;
  if (identity && ["running", "finalizing"].includes(status)) S.executionIdentity = identity;
  else if (identity && status === "queued" && !S.executionIdentity)
    S.executionIdentity = identity;
  if (
    S.executionIdentity &&
    event &&
    event.execution_id === S.executionIdentity.execution_id &&
    ["completed", "failed", "cancelled"].includes(status)
  )
    S.executionIdentity = null;
  const pending = pendingReplIdentity.value as any;
  if (
    pending &&
    event &&
    event.execution_id === pending.execution_id &&
    ["completed", "failed", "cancelled"].includes(status)
  ) {
    const frameId = pending.frame_id;
    pendingReplIdentity.value = null;
    invalidateKernelCache();
    if (S.currentId === frameId) {
      const loadLog = laneCall("loadExecutionLog", frameId);
      if (loadLog && typeof (loadLog as Promise<unknown>).catch === "function") {
        (loadLog as Promise<unknown>).catch(() => {});
      }
      laneCall("loadArtifacts", frameId);
      scheduleWorkbenchRefresh();
      if (S.dock.open && S.activeTab === "notebook") laneCall("scheduleNotebookRender");
    }
  }
}

export function identityForOwner(queue: any, ownerKind: string | null | undefined): any {
  const safe = queue || sanitizeExecutionQueue({}),
    candidates = [safe.owner].concat(safe.queue || []).filter(Boolean);
  const ticket = ownerKind
    ? candidates.find((item: any) => item.owner && item.owner.kind === ownerKind)
    : safe.owner;
  return ticket && ticket.execution_id && ticket.owner && ticket.owner.kind && ticket.owner.id
    ? { execution_id: ticket.execution_id, owner: ticket.owner }
    : null;
}

export function mergeDelegationChildEvent(m: any): void {
  const child = m && m.child && typeof m.child === "object" ? m.child : null;
  if (!child || !child.child_id) return;
  const clean = sanitizeDelegations({ children: [child] }).children[0];
  if (!clean) return;
  const state: DelegationState =
    S.delegationState &&
    typeof S.delegationState === "object" &&
    Array.isArray(S.delegationState.children)
      ? S.delegationState
      : {
          root_frame_id: publicText(m.root_frame_id, 96),
          initialized: true,
          budget: null,
          stats: { total: 0, pending: 0, running: 0, done: 0, failed: 0, stopped: 0 },
          children: [],
        };
  const at = state.children.findIndex((item) => item.child_id === clean.child_id);
  if (at >= 0)
    state.children[at] = Object.assign({}, state.children[at], clean);
  else state.children.push(clean);
  const stats: DelegationState["stats"] = {
    total: state.children.length,
    pending: 0,
    running: 0,
    done: 0,
    failed: 0,
    stopped: 0,
  };
  state.children.forEach((item) => {
    const key = String(item.status || "");
    const bag = stats as Record<string, number>;
    if (Object.prototype.hasOwnProperty.call(bag, key)) bag[key] = (bag[key] || 0) + 1;
  });
  state.stats = stats;
  S.delegationState = state;
}

export function actionTimelineBranchScope(
  timeline: any = S.actionTimeline,
  groups: any[] | null = null,
): string {
  const items = groups || ((timeline && timeline.groups) || []);
  return publicText(
    (timeline && timeline.branch_id) || (items[0] && items[0].branch_id) || S.currentId,
    96,
  );
}

export function actionTimelineRootScope(timeline: any = S.actionTimeline): string {
  return publicText((timeline && timeline.root_frame_id) || S.currentId, 96);
}

function actionTimelineHistoryIsLoading(timeline: any = S.actionTimeline): boolean {
  const loading = S._timelineHistoryLoading;
  return (
    !!loading &&
    loading.frameId === S.currentId &&
    loading.branchId === actionTimelineBranchScope(timeline)
  );
}

export async function loadEarlierActionTimeline(): Promise<void> {
  const id = S.currentId,
    timeline = S.actionTimeline;
  const branchId = actionTimelineBranchScope(timeline);
  if (!id || !timeline || !branchId || !timeline.has_more_before || S._timelineHistoryLoading)
    return;
  const first = timelineOrdinal(timeline.first_ordinal);
  if (first == null || first < 0) return;
  const request = (S._timelineHistoryReq = (S._timelineHistoryReq || 0) + 1);
  const loading = { frameId: id, branchId, firstOrdinal: first };
  S._timelineHistoryLoading = loading;
  delete S.workbenchErrors.timelineHistory;
  if (S.activeTab === "timeline") syncActionTimelineHistoryState();
  try {
    const page = await api(
      `/frames/${encodeURIComponent(id)}/action-timeline?before_ordinal=${first}&limit=${ACTION_TIMELINE_PAGE_SIZE}&branch_id=${encodeURIComponent(branchId)}`,
    );
    const current = S.actionTimeline,
      incoming = sanitizeActionTimeline(page);
    if (
      request !== S._timelineHistoryReq ||
      id !== S.currentId ||
      actionTimelineBranchScope(current) !== branchId ||
      (incoming.branch_id && incoming.branch_id !== branchId)
    )
      return;
    const view = S._timelineView;
    const matchingView =
      view &&
      view.scroll &&
      view.rootFrameId === actionTimelineRootScope(current) &&
      view.branchId === branchId;
    const prependSnapshot =
      S.activeTab === "timeline" && matchingView
        ? {
            node: view.scroll,
            scrollHeight: view.scroll.scrollHeight,
            scrollTop: view.scroll.scrollTop,
            followTail:
              actionTimelineBottomDistance(view) <= ACTION_TIMELINE_BOTTOM_THRESHOLD,
          }
        : null;
    const pendingPrependRestore =
      S.activeTab !== "timeline" && matchingView
        ? actionTimelineFilterScrollSnapshot(view)
        : null;
    S.actionTimeline = mergeActionTimelines(current, incoming, "before");
    if (S.activeTab === "timeline")
      updateActionTimelineLedger({ direction: "before", prependSnapshot });
    else if (pendingPrependRestore) view.pendingPrependRestore = pendingPrependRestore;
  } catch (error) {
    if (
      request === S._timelineHistoryReq &&
      id === S.currentId &&
      actionTimelineBranchScope() === branchId
    ) {
      S.workbenchErrors.timelineHistory = publicText(
        (error as { message?: string }) && (error as { message?: string }).message,
        240,
      );
    }
  } finally {
    if (
      request === S._timelineHistoryReq &&
      id === S.currentId &&
      S._timelineHistoryLoading === loading
    ) {
      S._timelineHistoryLoading = null;
      if (S.activeTab === "timeline") syncActionTimelineHistoryState();
    }
  }
}

export async function loadWorkbenchState(id: string | null, force = false): Promise<void> {
  if (!id || id !== S.currentId) return;
  if (!force && S._workbenchLoading === id) return;
  const request = (S._workbenchReq = (S._workbenchReq || 0) + 1);
  S._workbenchLoading = id;
  const base = `/frames/${id}`;
  const [
    timeline,
    execution,
    branches,
    context,
    security,
    delegation,
    recovery,
    recoveryActions,
    computeTasks,
  ] = await Promise.all([
    optionalApi([base + `/action-timeline?limit=${ACTION_TIMELINE_PAGE_SIZE}`]),
    optionalApi([base + "/execution-queue", base + "/execution"]),
    optionalApi([base + "/branches"]),
    optionalApi([base + "/context"]),
    optionalApi([base + "/security"]),
    optionalApi([base + "/delegations"]),
    optionalApi([base + "/recovery"]),
    optionalApi([base + "/recovery/actions"]),
    optionalApi([base + "/compute/tasks"]),
  ]);
  if (request !== S._workbenchReq || id !== S.currentId) return;
  S._workbenchLoading = null;
  if (timeline)
    S.actionTimeline = mergeActionTimelines(
      S.actionTimeline,
      sanitizeActionTimeline(timeline),
      "latest",
    );
  if (execution) rememberExecutionQueue(execution);
  if (branches) {
    S.branchState = sanitizeBranches(branches);
    S.branchUndo = branchUndoFromProjection(S.branchState);
  }
  if (context) S.contextState = sanitizeContext(context);
  if (security) S.securityState = sanitizeSecurity(security);
  if (delegation) S.delegationState = sanitizeDelegations(delegation);
  if (recovery) S.recoveryState = sanitizeRecovery(recovery);
  if (recoveryActions) S.recoveryActions = sanitizeRecoveryActions(recoveryActions);
  if (computeTasks) S.computeTasks = sanitizeComputeTasks(computeTasks);
  if (S.activeTab === "timeline") renderActionTimeline();
  if (S.activeTab === "notebook") laneCall("renderNotebook");
}

export function scheduleWorkbenchRefresh(delay = 180): void {
  clearTimeout(S._workbenchTimer);
  S._workbenchTimer = setTimeout(() => loadWorkbenchState(S.currentId, true), delay);
}

export function scheduleConversationResync(fid: string, delay = 120): void {
  clearTimeout(S._branchConversationTimer);
  S._branchConversationTimer = setTimeout(() => {
    if (S.currentId === fid) laneCall("openConversation", fid, S.project);
  }, delay);
}

export function scheduleBranchConversationResync(fid: string, delay = 120): void {
  scheduleConversationResync(fid, delay);
}

function latestCellForLanguage(language: string): any {
  return (
    (S.cells || [])
      .concat(S.liveCells || [])
      .filter((cell: any) =>
        String(cell.language || cell.kernel_id || "python")
          .toLowerCase()
          .startsWith(language),
      )
      .slice(-1)[0] || null
  );
}

function runtimeSummary(): any {
  const queue = S.executionQueue || {};
  const ownerTicket = queue.owner || null;
  const owner = (ownerTicket && ownerTicket.owner) || {};
  const recovery = S.recoveryState || {};
  const recoveryStatus = String(recovery.status || "").toLowerCase();
  const trustState = publicText(
    recovery.trust_state ||
      (S.recoveryActions || {}).trust_state ||
      (_kc.value.st || ({} as any)).trust_state,
    32,
  );
  const explicitRecoveryRequired =
    recovery.explicit_recovery_required === true ||
    (S.recoveryActions || {}).explicit_recovery_required === true ||
    (_kc.value.st || ({} as any)).explicit_recovery_required === true;
  const viewOnly =
    explicitRecoveryRequired ||
    recovery.view_only === true ||
    (S.recoveryActions || {}).view_only === true ||
    (_kc.value.st || ({} as any)).view_only === true;
  let status = "ended";
  if (/fail|error/.test(recoveryStatus)) status = "failed";
  else if (/partial/.test(recoveryStatus)) status = "partial";
  else if (/restor|recover|bootstrap|validat/.test(recoveryStatus)) status = "restoring";
  else if (ownerTicket || S.running || (_kc.value.st && (_kc.value.st as any).turn_running))
    status = "busy";
  else if (_kc.value.st && (_kc.value.st as any).alive) status = "live";
  const pythonCell = latestCellForLanguage("python"),
    rCell = latestCellForLanguage("r");
  const branch =
    (S.branchState && S.branchState.branch_id) ||
    (S.actionTimeline && S.actionTimeline.branch_id) ||
    (recovery && recovery.branch_id) ||
    S.currentId;
  const stateRevision =
    recovery.state_revision != null
      ? recovery.state_revision
      : Math.max(
          0,
          ...((S.cells || []) as any[])
            .concat(S.liveCells || [])
            .map((cell: any) => Number(cell.state_revision) || 0),
        );
  const pyGeneration =
    recovery.python_generation_id ||
    (_kc.value.st &&
      ((_kc.value.st as any).python_generation_id || (_kc.value.st as any).generation_id)) ||
    (pythonCell && pythonCell.generation_id);
  const rGeneration = recovery.r_generation_id || (rCell && rCell.generation_id);
  return {
    status,
    branch: publicText(branch, 96),
    python: publicText(pyGeneration, 96),
    r: publicText(rGeneration, 96),
    viewOnly,
    trustState,
    revision: stateRevision || null,
    owner: publicText(owner.kind || (ownerTicket && ownerTicket.owner_kind), 48),
    ownerId: publicText(owner.id || (ownerTicket && ownerTicket.owner_id), 96),
    queue: Number(queue.queued_count || (queue.queue || []).length || 0),
  };
}

function runtimeSummaryNode(compact = false): HTMLElement {
  const runtime = runtimeSummary();
  const root = el("div", "runtime-summary" + (compact ? " compact" : ""));
  const state = el("span", "runtime-state " + runtime.status, t("runtime.status." + runtime.status));
  root.appendChild(state);
  const item = (key: string, value: string, title?: string) => {
    const chip = el("span", "runtime-chip");
    chip.appendChild(el("span", "runtime-key", t(key)));
    const val = el("span", "runtime-val", value);
    if (title) val.title = publicText(title, 160);
    chip.appendChild(val);
    root.appendChild(chip);
  };
  item("runtime.branch", shortRuntime(runtime.branch), runtime.branch);
  item("runtime.python", shortRuntime(runtime.python), runtime.python);
  item("runtime.r", shortRuntime(runtime.r), runtime.r);
  item(
    "runtime.revision",
    runtime.revision == null ? t("runtime.none") : "S" + runtime.revision,
  );
  item(
    "runtime.owner",
    runtime.owner
      ? runtime.owner + (runtime.ownerId ? " · " + shortRuntime(runtime.ownerId) : "")
      : t("runtime.none"),
    runtime.ownerId,
  );
  item("runtime.queue", String(runtime.queue));
  if (runtime.viewOnly && runtime.trustState === "quarantined")
    item("runtime.trust", t("runtime.trust.quarantined"));
  return root;
}

function timelineMeta(label: string, value: any): HTMLElement | null {
  if (value == null || value === "" || (Array.isArray(value) && !value.length)) return null;
  const row = el("div", "timeline-meta");
  row.appendChild(el("span", "timeline-meta-key", label));
  const values = Array.isArray(value) ? value : [value];
  const body = el("span", "timeline-meta-value");
  values.slice(0, 24).forEach((item) =>
    body.appendChild(el("span", "timeline-pill", publicText(item, 160))),
  );
  row.appendChild(body);
  return row;
}

function actionTimelineDetails(group: Group): any {
  const latest = latestActionTimelineAttempt(group);
  const resources: string[] = [],
    artifacts: string[] = [];
  (group.events || []).forEach((event: any) => {
    (event.resource_keys || []).forEach((value: string) => {
      if (!resources.includes(value)) resources.push(value);
    });
    (event.artifacts || []).forEach((value: string) => {
      if (!artifacts.includes(value)) artifacts.push(value);
    });
  });
  return {
    latest,
    resources,
    artifacts,
    owner: group.owner || "",
    permission:
      group.permission ||
      (group.events || []).map((event: any) => event.side_effect_class).filter(Boolean),
    replay: group.replay_policy || (latest && latest.replayed_from_cell_id ? "replayed" : "original"),
    duration: timelineDuration(latest),
    tokens: t(
      "timeline.tokensValue",
      (group.usage || {}).input_tokens || 0,
      (group.usage || {}).output_tokens || 0,
    ),
    cost: timelineCost(group.cost),
  };
}

function appendActionTimelineDetails(container: HTMLElement, group: Group): any {
  const details = actionTimelineDetails(group);
  [
    timelineMeta(t("timeline.owner"), details.owner),
    timelineMeta(t("timeline.permission"), details.permission),
    timelineMeta(t("timeline.resources"), details.resources),
    timelineMeta(t("timeline.artifacts"), details.artifacts),
    timelineMeta(t("timeline.generation"), details.latest && details.latest.generation_id),
    timelineMeta(t("timeline.replay"), details.replay),
    timelineMeta(t("timeline.duration"), details.duration),
    timelineMeta(t("timeline.tokens"), details.tokens),
    timelineMeta(t("timeline.cost"), details.cost),
  ]
    .filter(Boolean)
    .forEach((node) => container.appendChild(node as Node));
  if (details.latest && details.latest.error)
    container.appendChild(el("div", "timeline-error", details.latest.error));
  return details;
}

function actionTimelineInspector(group: Group): HTMLElement {
  const kind = timelineKind(group),
    status = String(group.status || "completed").toLowerCase();
  const panel = el("section", "timeline-inspector kind-" + kind + " status-" + status);
  panel.id = "timeline-action-inspector";
  panel.dataset.groupId = group.group_id;
  panel.setAttribute("role", "region");
  panel.setAttribute("aria-labelledby", "timeline-inspector-label");
  const inspectorHead = el("div", "timeline-inspector-head");
  const inspectorLabel = el("div", "timeline-inspector-label", t("timeline.inspector"));
  inspectorLabel.id = "timeline-inspector-label";
  inspectorHead.appendChild(inspectorLabel);
  const close = ghostIconBtn("x", t("timeline.inspector.close"));
  close.setAttribute("aria-label", t("timeline.inspector.close"));
  close.onclick = (event) => {
    event.stopPropagation();
    S._timelineRestoreFocusGroupId = group.group_id;
    S.actionTimelineSelectedGroupId = null;
    S.actionTimelineSelectedBranchId = null;
    updateActionTimelineLedger();
  };
  inspectorHead.appendChild(close);
  panel.appendChild(inspectorHead);
  const cardHead = el("div", "timeline-card-head");
  const kindLabel = el("span", "timeline-kind");
  kindLabel.appendChild(iconEl(timelineKindIcon(kind), 14));
  kindLabel.appendChild(el("span", null, t("timeline.kind." + kind)));
  cardHead.appendChild(kindLabel);
  cardHead.appendChild(
    el("span", "timeline-status " + status, publicText(status || "completed", 32)),
  );
  panel.appendChild(cardHead);
  panel.appendChild(
    el("div", "timeline-card-title", group.title || t("timeline.kind." + kind)),
  );
  appendActionTimelineDetails(panel, group);
  appendActionTimelineTimingDetails(panel, group);
  (panel as any)._timelineGroup = group;
  (panel as any)._timelineLanguage = LANG;
  return panel;
}

function appendActionTimelineTimingDetails(container: HTMLElement, group: Group): void {
  const attempt = latestActionTimelineAttempt(group);
  if (!attempt || timelineEpochMs(attempt.allocated_at) == null) return;
  [
    timelineMeta(t("timeline.overview.allocated"), timelineOverviewExactTime(attempt.allocated_at)),
    timelineMeta(t("timeline.overview.started"), timelineOverviewExactTime(attempt.started_at)),
    timelineMeta(t("timeline.overview.response"), timelineOverviewExactTime(attempt.response_at)),
    timelineMeta(t("timeline.overview.finished"), timelineOverviewExactTime(attempt.finished_at)),
    timelineMeta(
      t("timeline.overview.queue"),
      timelineOverviewExactDuration(attempt.allocated_at, attempt.started_at),
    ),
    timelineMeta(
      t("timeline.overview.ttft"),
      timelineOverviewExactDuration(attempt.started_at, attempt.response_at),
    ),
    timelineMeta(
      t("timeline.overview.decode"),
      timelineOverviewExactDuration(attempt.response_at, attempt.finished_at),
    ),
  ]
    .filter(Boolean)
    .forEach((node) => container.appendChild(node as Node));
}

function normalizeActionTimelineSearch(value: unknown): string {
  return String(value || "").trim().toLocaleLowerCase();
}

function actionTimelineSearchDocument(group: Group): string {
  const kind = timelineKind(group || {});
  const fields = [group && group.title, group && group.kind, kind, t("timeline.kind." + kind)];
  ((group && group.events) || []).forEach((event: any) => {
    (event.resource_keys || []).forEach((value: string) => fields.push(value));
    (event.artifacts || []).forEach((value: string) => fields.push(value));
  });
  return fields
    .filter((value) => value != null && value !== "")
    .map((value) => String(value).toLocaleLowerCase())
    .join("\u0000");
}

function syncActionTimelineSearchIndex(view: View, groups: Group[]): void {
  const previous = view.searchIndex || new Map(),
    next = new Map();
  groups.forEach((group) => {
    const cached = previous.get(group.group_id);
    next.set(
      group.group_id,
      cached && cached.group === group && cached.lang === LANG
        ? cached
        : { group, lang: LANG, text: actionTimelineSearchDocument(group) },
    );
  });
  view.searchIndex = next;
}

function searchActionTimelineGroups(view: View, groups: Group[]): Group[] {
  syncActionTimelineSearchIndex(view, groups);
  if (!view.searchNeedle) return groups;
  return groups.filter((group) => {
    const indexed = view.searchIndex.get(group.group_id);
    return !!indexed && indexed.text.includes(view.searchNeedle);
  });
}

function filteredActionTimelineGroups(view: View, groups: Group[]): Group[] {
  const selection = view && view.overview && view.overview.selection;
  if (!selection) return groups;
  return groups.filter((group) =>
    actionTimelineSelectionOverlaps(
      view.overview.model.byId.get(group.group_id),
      selection,
      group,
    ),
  );
}

function actionTimelineLiveScrollTop(view: View): number {
  return view.scroll.clientHeight > 0 ? view.scroll.scrollTop : view.scrollTop;
}

function actionTimelineHeaderHeight(view: View): number {
  const measured = view.thead.offsetHeight;
  if (measured > 0) {
    view.headerHeight = measured;
    return measured;
  }
  return view.headerHeight || 30;
}

function actionTimelineFilterScrollSnapshot(view: View): any {
  const headerHeight = actionTimelineHeaderHeight(view),
    scrollTop = actionTimelineLiveScrollTop(view);
  const entries = view.entries || [],
    index = Math.max(
      0,
      Math.min(
        entries.length - 1,
        Math.floor(Math.max(0, scrollTop - headerHeight) / ACTION_TIMELINE_ROW_HEIGHT),
      ),
    );
  const entry = entries[index];
  return {
    entryKey: actionTimelineEntryKey(entry),
    groupId: entry && entry.type === "group" ? entry.group.group_id : null,
    turnId: entry && (entry.turnId || (entry.group && entry.group.turn_id)),
    offset: scrollTop - (headerHeight + index * ACTION_TIMELINE_ROW_HEIGHT),
    followTail: view.followTail,
  };
}

function clearActionTimelineOverviewHover(view: View): void {
  const overview = view && view.overview;
  if (!overview) return;
  if (overview.hoverTimer) clearTimeout(overview.hoverTimer);
  if (overview.hoverLeaveTimer) clearTimeout(overview.hoverLeaveTimer);
  overview.hoverTimer = 0;
  overview.hoverLeaveTimer = 0;
  overview.tooltipHovered = false;
  overview.hoverCandidateId = null;
  overview.hoverGroupId = null;
  overview.tooltip.replaceChildren();
  overview.tooltip.classList.add("hidden");
  overview.tooltip.setAttribute("aria-hidden", "true");
  overview.hoverPath.setAttribute("d", "");
}

function cancelActionTimelineOverviewHoverClear(view: View): void {
  const overview = view && view.overview;
  if (!overview || !overview.hoverLeaveTimer) return;
  clearTimeout(overview.hoverLeaveTimer);
  overview.hoverLeaveTimer = 0;
}

function scheduleActionTimelineOverviewHoverClear(view: View): void {
  const overview = view && view.overview;
  if (!overview) return;
  cancelActionTimelineOverviewHoverClear(view);
  overview.hoverLeaveTimer = setTimeout(() => {
    overview.hoverLeaveTimer = 0;
    if (!overview.tooltipHovered) clearActionTimelineOverviewHover(view);
  }, 150);
}

function positionActionTimelineOverviewTooltip(
  overview: any,
  clientX: number,
  clientY: number,
): void {
  const rect = overview.shell.getBoundingClientRect();
  const width = overview.tooltip.offsetWidth || 220,
    height = overview.tooltip.offsetHeight || 120;
  const pointerX = clientX - rect.left,
    pointerY = clientY - rect.top;
  const left = Math.max(8, Math.min(Math.max(8, rect.width - width - 8), pointerX - width / 2));
  let top = pointerY + 8;
  if (top + height > rect.height - 8) top = Math.max(8, pointerY - height - 8);
  overview.tooltip.style.left = left + "px";
  overview.tooltip.style.top = top + "px";
}

function showActionTimelineOverviewTooltip(
  view: View,
  groupId: string,
  clientX: number,
  clientY: number,
): void {
  const overview = view && view.overview,
    item = overview && overview.model && overview.model.byId.get(groupId);
  if (!overview || !item || overview.hoverCandidateId !== groupId) return;
  const ordinal = timelineOrdinal(item.group.ordinal),
    title = item.group.title || t("timeline.kind." + timelineKind(item.group));
  const head = el(
    "div",
    "timeline-overview-tooltip-title",
    (ordinal == null ? "" : "#" + ordinal + " · ") + title,
  );
  const body = el("div", "timeline-overview-tooltip-grid");
  const row = (label: string, value: string) => {
    body.appendChild(el("span", "timeline-overview-tooltip-key", label));
    body.appendChild(el("code", null, value));
  };
  row(t("timeline.overview.allocated"), timelineOverviewExactTime(item.times.allocated));
  row(t("timeline.overview.started"), timelineOverviewExactTime(item.times.started));
  row(t("timeline.overview.response"), timelineOverviewExactTime(item.times.response));
  row(t("timeline.overview.finished"), timelineOverviewExactTime(item.times.finished));
  row(
    t("timeline.overview.queue"),
    timelineOverviewExactDuration(item.times.allocated, item.times.started),
  );
  row(
    t("timeline.overview.ttft"),
    timelineOverviewExactDuration(item.times.started, item.times.response),
  );
  row(
    t("timeline.overview.decode"),
    timelineOverviewExactDuration(item.times.response, item.times.finished),
  );
  if (item.running)
    body.appendChild(el("span", "timeline-overview-running", t("timeline.overview.running")));
  overview.tooltip.replaceChildren(head, body);
  overview.tooltip.classList.remove("hidden");
  overview.tooltip.setAttribute("aria-hidden", "false");
  overview.hoverGroupId = groupId;
  positionActionTimelineOverviewTooltip(overview, clientX, clientY);
  overview.hoverPath.setAttribute("d", timelineOverviewItemPaths(overview, item).highlight);
}

function actionTimelineOverviewHit(view: View, event: PointerEvent): any {
  const overview = view && view.overview,
    model = overview && overview.model;
  if (!overview || !model || !model.items.length) return null;
  const rect = overview.svg.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  const x = Math.max(
    0,
    Math.min(
      ACTION_TIMELINE_OVERVIEW_WIDTH,
      ((event.clientX - rect.left) / rect.width) * ACTION_TIMELINE_OVERVIEW_WIDTH,
    ),
  );
  const y = Math.max(
    0,
    Math.min(
      ACTION_TIMELINE_OVERVIEW_HEIGHT - 0.0001,
      ((event.clientY - rect.top) / rect.height) * ACTION_TIMELINE_OVERVIEW_HEIGHT,
    ),
  );
  const rank = Math.floor((y / ACTION_TIMELINE_OVERVIEW_HEIGHT) * model.laneCount);
  const radius = Math.min(256, Math.max(2, Math.ceil((5 / rect.height) * model.laneCount)));
  const tolerance = (6 / rect.width) * ACTION_TIMELINE_OVERVIEW_WIDTH;
  let best = null,
    bestDistance = Infinity;
  for (
    let candidateRank = Math.max(0, rank - radius);
    candidateRank <= Math.min(model.laneCount - 1, rank + radius);
    candidateRank += 1
  ) {
    const item = model.items[candidateRank];
    if (!item) continue;
    const markerAt = item.markerAt != null ? item.markerAt : item.pointAt;
    const markerX = markerAt == null ? null : timelineOverviewTimeToX(overview, markerAt);
    const onMarker = markerX != null && Math.abs(markerX - x) <= tolerance;
    const onSegment = item.segments.some((segment: any) => {
      const x1 = timelineOverviewTimeToX(overview, segment.start),
        x2 = timelineOverviewTimeToX(overview, segment.end);
      return (
        x1 != null &&
        x2 != null &&
        x >= Math.min(x1, x2) - tolerance &&
        x <= Math.max(x1, x2) + tolerance
      );
    });
    const distance = Math.abs(candidateRank - rank);
    if ((onMarker || onSegment) && distance < bestDistance) {
      best = item;
      bestDistance = distance;
    }
  }
  return best;
}

function actionTimelineOverviewPointerMove(view: View, event: PointerEvent): void {
  const overview = view && view.overview;
  if (!overview) return;
  cancelActionTimelineOverviewHoverClear(view);
  if (overview.gesture) {
    moveActionTimelineOverviewGesture(view, event);
    return;
  }
  const hit = actionTimelineOverviewHit(view, event),
    groupId = hit && hit.groupId;
  if (overview.hoverGroupId) {
    if (groupId === overview.hoverGroupId) return;
    scheduleActionTimelineOverviewHoverClear(view);
    return;
  }
  if (!groupId) {
    clearActionTimelineOverviewHover(view);
    return;
  }
  if (overview.hoverCandidateId === groupId) {
    overview.hoverPoint = { clientX: event.clientX, clientY: event.clientY };
    return;
  }
  clearActionTimelineOverviewHover(view);
  overview.hoverCandidateId = groupId;
  overview.hoverPoint = { clientX: event.clientX, clientY: event.clientY };
  overview.hoverTimer = setTimeout(() => {
    overview.hoverTimer = 0;
    const point = overview.hoverPoint || { clientX: event.clientX, clientY: event.clientY };
    showActionTimelineOverviewTooltip(view, groupId, point.clientX, point.clientY);
  }, ACTION_TIMELINE_OVERVIEW_HOVER_DELAY);
}

function timelineOverviewEventX(overview: any, event: PointerEvent): number {
  const rect = overview.svg.getBoundingClientRect();
  if (!rect.width) return 0;
  return Math.max(
    0,
    Math.min(
      ACTION_TIMELINE_OVERVIEW_WIDTH,
      ((event.clientX - rect.left) / rect.width) * ACTION_TIMELINE_OVERVIEW_WIDTH,
    ),
  );
}

function syncActionTimelineOverviewDecorations(view: View): void {
  const overview = view.overview,
    activeSelection = overview.draftSelection || overview.selection;
  const selected = overview.model.byId.get(S.actionTimelineSelectedGroupId);
  overview.selectedPath.setAttribute(
    "d",
    selected ? timelineOverviewItemPaths(overview, selected).highlight : "",
  );
  if (activeSelection && overview.viewStart != null && overview.viewEnd != null) {
    const x1 = timelineOverviewTimeToX(
      overview,
      Math.min(activeSelection.start, activeSelection.end),
    );
    const x2 = timelineOverviewTimeToX(
      overview,
      Math.max(activeSelection.start, activeSelection.end),
    );
    const left = Math.max(0, Math.min(ACTION_TIMELINE_OVERVIEW_WIDTH, x1 as number));
    const right = Math.max(0, Math.min(ACTION_TIMELINE_OVERVIEW_WIDTH, x2 as number));
    overview.selectionRect.setAttribute("x", String(Math.min(left, right)));
    overview.selectionRect.setAttribute("width", String(Math.abs(right - left)));
    overview.selectionRect.classList.remove("hidden");
  } else {
    overview.selectionRect.classList.add("hidden");
    overview.selectionRect.setAttribute("width", "0");
  }
  if (overview.selection) {
    overview.selectionStatus.textContent = t(
      "timeline.overview.selection",
      timelineOverviewExactTime(overview.selection.start),
      timelineOverviewExactTime(overview.selection.end),
    );
    overview.clearButton.classList.remove("hidden");
    overview.clearButton.disabled = false;
  } else {
    overview.selectionStatus.textContent = "";
    overview.clearButton.classList.add("hidden");
    overview.clearButton.disabled = true;
  }
}

function syncActionTimelineOverviewControls(view: View): void {
  const overview = view && view.overview,
    timeline = S.actionTimeline || {};
  if (!overview) return;
  const span =
    overview.viewStart != null && overview.viewEnd != null
      ? Math.max(0, overview.viewEnd - overview.viewStart)
      : 0;
  const tolerance = span / ACTION_TIMELINE_OVERVIEW_WIDTH;
  const includesLoadedStart =
    overview.dataStart != null &&
    overview.viewStart != null &&
    overview.viewEnd != null &&
    overview.viewStart <= overview.dataStart + tolerance &&
    overview.viewEnd >= overview.dataStart - tolerance;
  const visible = !!timeline.has_more_before && (overview.dataStart == null || includesLoadedStart);
  const loading = actionTimelineHistoryIsLoading(timeline);
  const restoreFocus = !visible && document.activeElement === overview.prefixButton;
  const restoreAfterLoad = !!overview.restoreFocusAfterPrefix && !loading;
  overview.prefixButton.classList.toggle("hidden", !visible);
  overview.prefixButton.disabled = !visible || loading;
  overview.prefixButton.setAttribute("aria-busy", loading ? "true" : "false");
  overview.prefixButton.setAttribute(
    "aria-label",
    t(loading ? "timeline.overview.omittedLoading" : "timeline.overview.omitted"),
  );
  const x = overview.dataStart == null ? 0 : timelineOverviewTimeToX(overview, overview.dataStart);
  overview.prefixButton.style.left =
    Math.max(0, Math.min(100, ((x == null ? 0 : x) / ACTION_TIMELINE_OVERVIEW_WIDTH) * 100)) +
    "%";
  if (restoreFocus || restoreAfterLoad) {
    overview.restoreFocusAfterPrefix = false;
    const target = visible ? overview.prefixButton : overview.shell;
    try {
      target.focus({ preventScroll: true });
    } catch {
      target.focus();
    }
  }
}

function clearActionTimelineOverviewSelection(view: View, options: any = {}): boolean {
  const overview = view && view.overview;
  if (!overview || (!overview.selection && !overview.draftSelection)) return false;
  const restoreControlFocus = document.activeElement === overview.clearButton;
  overview.selection = null;
  overview.draftSelection = null;
  const restore = options.restore !== false && !view.searchNeedle ? view.preFilterScroll : null;
  if (!view.searchNeedle) view.preFilterScroll = null;
  view.autoLoadArmed = false;
  clearActionTimelineOverviewHover(view);
  if (options.update !== false)
    updateActionTimelineLedger({
      direction: "filter",
      filterChanged: true,
      filterRestore: restore,
    });
  else syncActionTimelineOverviewDecorations(view);
  if (restoreControlFocus) {
    try {
      overview.shell.focus({ preventScroll: true });
    } catch {
      overview.shell.focus();
    }
  }
  return true;
}

export function commitActionTimelineOverviewSelection(
  view: View,
  start: number | null,
  end: number | null,
): void {
  const overview = view && view.overview;
  if (!overview || start == null || end == null) return;
  if (!overview.selection && !view.searchNeedle)
    view.preFilterScroll = actionTimelineFilterScrollSnapshot(view);
  overview.selection = {
    start: Math.floor(Math.min(start, end)),
    end: Math.ceil(Math.max(start, end)),
  };
  overview.draftSelection = null;
  view.autoLoadArmed = false;
  clearActionTimelineOverviewHover(view);
  updateActionTimelineLedger({ direction: "filter", filterChanged: true });
}

function selectActionTimelineGroup(
  groupId: string,
  branchScope: string,
  fromOverview = false,
): void {
  const view = S._timelineView;
  const targetGroup = view && view.allGroups.find((group: Group) => group.group_id === groupId);
  if (!view || !groupId || !targetGroup) return;
  let filterChanged = false,
    filterRestore = null as any;
  if (
    fromOverview &&
    view.overview.selection &&
    !view.groups.some((group: Group) => group.group_id === groupId)
  ) {
    filterChanged = clearActionTimelineOverviewSelection(view, {
      update: false,
      restore: false,
    });
  }
  if (
    fromOverview &&
    targetGroup.turn_id &&
    view.collapsedTurns.has(targetGroup.turn_id) &&
    !view.searchNeedle
  ) {
    filterRestore = actionTimelineFilterScrollSnapshot(view);
    view.collapsedTurns.delete(targetGroup.turn_id);
    filterChanged = true;
  }
  S._timelineRestoreFocusGroupId = groupId;
  S.actionTimelineSelectedGroupId = groupId;
  S.actionTimelineSelectedBranchId = branchScope;
  updateActionTimelineLedger({ direction: "selection", filterChanged, filterRestore });
  if (fromOverview) {
    const current = S._timelineView,
      index = current && current.groups.findIndex((group: Group) => group.group_id === groupId);
    if (current && index >= 0) focusActionTimelineGroup(current, index);
  } else {
    const current = S._timelineView;
    revealActionTimelineOverviewGroup(current, groupId);
    const close = current && current.inspectorHost.querySelector(".timeline-inspector button");
    if (close) {
      try {
        close.focus({ preventScroll: true });
      } catch {
        close.focus();
      }
    }
  }
}

function revealActionTimelineOverviewGroup(view: View, groupId: string): void {
  const overview = view && view.overview,
    item = overview && overview.model.byId.get(groupId);
  if (
    !item ||
    overview.viewStart == null ||
    overview.viewEnd == null ||
    overview.dataStart == null ||
    overview.dataEnd == null
  )
    return;
  const extent = actionTimelineOverviewVisualExtent(item);
  if (!extent) return;
  const itemStart = extent.start,
    itemEnd = extent.end;
  if (itemEnd >= overview.viewStart && itemStart <= overview.viewEnd) return;
  const domainSpan = overview.dataEnd - overview.dataStart,
    currentSpan = overview.viewEnd - overview.viewStart;
  if (domainSpan <= 0 || currentSpan <= 0) return;
  const span = Math.min(domainSpan, Math.max(currentSpan, itemEnd - itemStart));
  const center = itemStart + (itemEnd - itemStart) / 2;
  const start = Math.max(overview.dataStart, Math.min(center - span / 2, overview.dataEnd - span));
  overview.viewStart = start;
  overview.viewEnd = start + span;
  renderActionTimelineOverviewPaths(view);
}

function beginActionTimelineOverviewGesture(view: View, event: PointerEvent): void {
  const overview = view && view.overview;
  const button = event.button === 2 || (event.button === 0 && event.ctrlKey) ? 2 : event.button;
  if (!overview || ![0, 2].includes(button) || overview.viewStart == null || overview.viewEnd == null)
    return;
  clearActionTimelineOverviewHover(view);
  const x = timelineOverviewEventX(overview, event),
    time = timelineOverviewXToTime(overview, x);
  const hit = button === 0 ? actionTimelineOverviewHit(view, event) : null;
  overview.gesture = {
    pointerId: event.pointerId,
    button,
    startClientX: event.clientX,
    startX: x,
    startTime: time,
    lastTime: time,
    startViewStart: overview.viewStart,
    startViewEnd: overview.viewEnd,
    dragging: false,
    hitGroupId: hit && hit.groupId,
  };
  try {
    overview.svg.setPointerCapture(event.pointerId);
  } catch {
    /* capture optional */
  }
  event.preventDefault();
}

function moveActionTimelineOverviewGesture(view: View, event: PointerEvent): void {
  const overview = view && view.overview,
    gesture = overview && overview.gesture;
  if (!gesture || gesture.pointerId !== event.pointerId) return;
  const x = timelineOverviewEventX(overview, event);
  const time = timelineOverviewXToDomainTime(gesture.startViewStart, gesture.startViewEnd, x);
  if (!gesture.dragging && Math.abs(event.clientX - gesture.startClientX) >= 4)
    gesture.dragging = true;
  gesture.lastTime = time;
  if (gesture.dragging && gesture.button === 0) {
    const a = Number(gesture.startTime),
      b = Number(time);
    overview.draftSelection = {
      start: Math.floor(Math.min(a, b)),
      end: Math.ceil(Math.max(a, b)),
    };
    syncActionTimelineOverviewDecorations(view);
  } else if (gesture.dragging && gesture.button === 2) {
    const rect = overview.svg.getBoundingClientRect(),
      span = gesture.startViewEnd - gesture.startViewStart,
      domainSpan = overview.dataEnd - overview.dataStart;
    if (rect.width && span > 0 && span < domainSpan) {
      const shifted =
        gesture.startViewStart - ((event.clientX - gesture.startClientX) / rect.width) * span;
      overview.viewStart = Math.max(overview.dataStart, Math.min(shifted, overview.dataEnd - span));
      overview.viewEnd = overview.viewStart + span;
      scheduleActionTimelineOverviewPaths(view);
    }
  }
  event.preventDefault();
}

function finishActionTimelineOverviewGesture(view: View, event: PointerEvent): void {
  const overview = view && view.overview,
    gesture = overview && overview.gesture;
  if (!gesture || gesture.pointerId !== event.pointerId) return;
  overview.gesture = null;
  try {
    overview.svg.releasePointerCapture(event.pointerId);
  } catch {
    /* ignore */
  }
  if (gesture.button === 0 && gesture.dragging)
    commitActionTimelineOverviewSelection(view, gesture.startTime, gesture.lastTime);
  else if (gesture.button === 0 && gesture.hitGroupId)
    selectActionTimelineGroup(gesture.hitGroupId, view.branchId, true);
  else if (gesture.button === 2 && !gesture.dragging) clearActionTimelineOverviewSelection(view);
  else {
    overview.draftSelection = null;
    syncActionTimelineOverviewDecorations(view);
  }
  event.preventDefault();
}

function cancelActionTimelineOverviewGesture(view: View, event?: PointerEvent): void {
  const overview = view && view.overview,
    gesture = overview && overview.gesture;
  if (!gesture || (event && gesture.pointerId !== event.pointerId)) return;
  overview.gesture = null;
  overview.draftSelection = null;
  syncActionTimelineOverviewDecorations(view);
}

function renderActionTimelineOverviewPaths(view: View): void {
  const overview = view.overview,
    model = overview.model;
  if (!overview || !model) return;
  const aggregate: Record<string, string> = {
    queue: "",
    ttft: "",
    decode: "",
    marker: "",
    point: "",
  };
  model.items.forEach((item: any) => {
    const paths = timelineOverviewItemPaths(overview, item);
    Object.keys(aggregate).forEach((key) => {
      aggregate[key] += paths[key] || "";
    });
  });
  overview.queuePath.setAttribute("d", aggregate.queue);
  overview.ttftPath.setAttribute("d", aggregate.ttft);
  overview.decodePath.setAttribute("d", aggregate.decode);
  overview.markerPath.setAttribute("d", aggregate.marker);
  overview.pointPath.setAttribute("d", aggregate.point);
  syncActionTimelineOverviewDecorations(view);
  if (overview.hoverGroupId && !model.byId.has(overview.hoverGroupId))
    clearActionTimelineOverviewHover(view);
  else if (overview.hoverGroupId) {
    const point = overview.hoverPoint;
    if (point) showActionTimelineOverviewTooltip(view, overview.hoverGroupId, point.clientX, point.clientY);
    else
      overview.hoverPath.setAttribute(
        "d",
        timelineOverviewItemPaths(overview, model.byId.get(overview.hoverGroupId)).highlight,
      );
  }
  const axisTime = (value: number | null) =>
    value == null ? "—" : new Date(Math.round(value)).toISOString();
  const startText = axisTime(overview.viewStart),
    endText = axisTime(overview.viewEnd);
  overview.axisStart.textContent = startText === "—" ? startText : startText.slice(11, 23);
  overview.axisStart.title = startText;
  overview.axisEnd.textContent = endText === "—" ? endText : endText.slice(11, 23);
  overview.axisEnd.title = endText;
  overview.shell.dataset.viewStart =
    overview.viewStart == null ? "" : String(Math.round(overview.viewStart));
  overview.shell.dataset.viewEnd =
    overview.viewEnd == null ? "" : String(Math.round(overview.viewEnd));
  syncActionTimelineOverviewControls(view);
}

function scheduleActionTimelineOverviewPaths(view: View): void {
  const overview = view && view.overview;
  if (!overview || view !== S._timelineView || overview.raf) return;
  overview.raf = requestAnimationFrame(() => {
    overview.raf = 0;
    if (view === S._timelineView && overview.shell.isConnected)
      renderActionTimelineOverviewPaths(view);
  });
}

function actionTimelineOverviewZoomAt(view: View, factor: number, anchorRatio: number): boolean {
  const overview = view && view.overview;
  if (
    !overview ||
    overview.dataStart == null ||
    overview.dataEnd == null ||
    overview.viewStart == null ||
    overview.viewEnd == null
  )
    return false;
  const domainSpan = overview.dataEnd - overview.dataStart,
    currentSpan = overview.viewEnd - overview.viewStart;
  if (domainSpan <= 0 || currentSpan <= 0 || !Number.isFinite(factor) || factor <= 0) return false;
  const minSpan = Math.max(1, domainSpan / 1000),
    nextSpan = Math.max(minSpan, Math.min(domainSpan, currentSpan * factor));
  if (Math.abs(nextSpan - currentSpan) < 0.001) return false;
  const ratio = Math.max(0, Math.min(1, anchorRatio)),
    anchor = overview.viewStart + currentSpan * ratio;
  let start = anchor - nextSpan * ratio;
  start = Math.max(overview.dataStart, Math.min(start, overview.dataEnd - nextSpan));
  overview.viewStart = start;
  overview.viewEnd = start + nextSpan;
  clearActionTimelineOverviewHover(view);
  scheduleActionTimelineOverviewPaths(view);
  return true;
}

function actionTimelineOverviewPanBy(view: View, delta: number): boolean {
  const overview = view && view.overview;
  if (
    !overview ||
    overview.dataStart == null ||
    overview.dataEnd == null ||
    overview.viewStart == null ||
    overview.viewEnd == null
  )
    return false;
  const span = overview.viewEnd - overview.viewStart,
    domainSpan = overview.dataEnd - overview.dataStart;
  if (span <= 0 || span >= domainSpan || !Number.isFinite(delta)) return false;
  const start = Math.max(
    overview.dataStart,
    Math.min(overview.viewStart + delta, overview.dataEnd - span),
  );
  if (Math.abs(start - overview.viewStart) < 0.001) return false;
  overview.viewStart = start;
  overview.viewEnd = start + span;
  clearActionTimelineOverviewHover(view);
  scheduleActionTimelineOverviewPaths(view);
  return true;
}

function actionTimelineOverviewWheel(view: View, event: WheelEvent): void {
  const overview = view && view.overview;
  if (!overview || !event.deltaY) return;
  const rect = overview.svg.getBoundingClientRect();
  if (!rect.width) return;
  const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? rect.height : 1;
  const factor = Math.exp(event.deltaY * unit * 0.0015),
    ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
  if (actionTimelineOverviewZoomAt(view, factor, ratio)) event.preventDefault();
}

function showActionTimelineOverviewKeyboardItem(view: View, groupId: string): boolean {
  const overview = view && view.overview,
    item = overview && overview.model.byId.get(groupId);
  if (!item) return false;
  revealActionTimelineOverviewGroup(view, groupId);
  clearActionTimelineOverviewHover(view);
  overview.keyboardGroupId = groupId;
  overview.hoverCandidateId = groupId;
  const rect = overview.svg.getBoundingClientRect();
  const extent = actionTimelineOverviewVisualExtent(item);
  const anchorTime = extent ? extent.start + (extent.end - extent.start) / 2 : item.start;
  const rawX = timelineOverviewTimeToX(overview, anchorTime);
  const point = {
    clientX:
      rect.left +
      (Math.max(0, Math.min(ACTION_TIMELINE_OVERVIEW_WIDTH, rawX == null ? 0 : rawX)) /
        ACTION_TIMELINE_OVERVIEW_WIDTH) *
        rect.width,
    clientY: rect.top + ((item.rank + 0.5) / item.laneCount) * rect.height,
  };
  overview.hoverPoint = point;
  showActionTimelineOverviewTooltip(view, groupId, point.clientX, point.clientY);
  return true;
}

function actionTimelineOverviewKeydown(view: View, event: KeyboardEvent): void {
  const overview = view && view.overview;
  if (!overview) return;
  if (
    event.target !== overview.shell &&
    (event.target as HTMLElement).closest &&
    (event.target as HTMLElement).closest("button")
  )
    return;
  if (event.key === "Escape") {
    if (overview.selection) clearActionTimelineOverviewSelection(view);
    else clearActionTimelineOverviewHover(view);
    event.preventDefault();
    return;
  }
  const items = overview.model.items;
  if (["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key) && items.length) {
    const current = Math.max(
      0,
      items.findIndex((item: any) => item.groupId === overview.keyboardGroupId),
    );
    const next =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? items.length - 1
          : Math.max(0, Math.min(items.length - 1, current + (event.key === "ArrowUp" ? -1 : 1)));
    event.preventDefault();
    showActionTimelineOverviewKeyboardItem(view, items[next].groupId);
    return;
  }
  if (event.key === "Enter" && overview.keyboardGroupId) {
    const item = overview.model.byId.get(overview.keyboardGroupId);
    if (!item) return;
    event.preventDefault();
    if (event.shiftKey) commitActionTimelineOverviewSelection(view, item.start, item.end);
    else {
      selectActionTimelineGroup(item.groupId, view.branchId, true);
      const close = view.inspectorHost.querySelector(".timeline-inspector button");
      if (close) {
        try {
          close.focus({ preventScroll: true });
        } catch {
          close.focus();
        }
      }
    }
    return;
  }
  if (event.key === "+" || event.key === "=") {
    if (actionTimelineOverviewZoomAt(view, 0.75, 0.5)) event.preventDefault();
    return;
  }
  if (event.key === "-") {
    if (actionTimelineOverviewZoomAt(view, 4 / 3, 0.5)) event.preventDefault();
    return;
  }
  const span =
    overview.viewEnd != null && overview.viewStart != null
      ? overview.viewEnd - overview.viewStart
      : 0;
  if (event.key === "ArrowLeft" && actionTimelineOverviewPanBy(view, -span * 0.1))
    event.preventDefault();
  else if (event.key === "ArrowRight" && actionTimelineOverviewPanBy(view, span * 0.1))
    event.preventDefault();
}

function drawActionTimelineOverview(
  view: View,
  groups: Group[],
  force = false,
  domainGroups: Group[] = groups,
): void {
  const overview = view.overview,
    model = actionTimelineOverviewModel(groups, domainGroups);
  const previousStart = overview.dataStart,
    previousEnd = overview.dataEnd;
  const wasFull =
    !overview.initialized ||
    (overview.viewStart === previousStart && overview.viewEnd === previousEnd);
  overview.model = model;
  overview.dataStart = model.dataStart;
  overview.dataEnd = model.dataEnd;
  if (!overview.gesture && (!overview.initialized || wasFull)) {
    overview.viewStart = model.dataStart;
    overview.viewEnd = model.dataEnd;
    overview.initialized = model.dataStart != null;
  } else if (!overview.gesture && model.dataStart != null && model.dataEnd != null) {
    const span = Math.max(0, overview.viewEnd - overview.viewStart);
    overview.viewStart = Math.max(
      model.dataStart,
      Math.min(overview.viewStart, model.dataEnd - span),
    );
    overview.viewEnd = Math.min(model.dataEnd, overview.viewStart + span);
  }
  renderActionTimelineOverviewPaths(view);
  overview.shell.dataset.itemCount = String(model.items.length);
  overview.label.textContent = t("timeline.overview");
  overview.svg.setAttribute("aria-label", t("timeline.overview"));
  overview.help.textContent = t("timeline.overview.help");
  overview.keyboardHelp.textContent = t("timeline.overview.keyboard");
  overview.clearButton.textContent = t("timeline.overview.clear");
  overview.clearButton.setAttribute("aria-label", t("timeline.overview.clear"));
  (
    [
      [overview.zoomInButton, "timeline.overview.zoomIn"],
      [overview.zoomOutButton, "timeline.overview.zoomOut"],
      [overview.panEarlierButton, "timeline.overview.panEarlier"],
      [overview.panLaterButton, "timeline.overview.panLater"],
    ] as Array<[HTMLButtonElement, string]>
  ).forEach(([button, key]) => {
    button.title = t(key);
    button.setAttribute("aria-label", t(key));
  });
  overview.legendQueue.lastChild.textContent = t("timeline.overview.queue");
  overview.legendTtft.lastChild.textContent = t("timeline.overview.ttft");
  overview.legendDecode.lastChild.textContent = t("timeline.overview.decode");
  overview.shell.classList.toggle("timeline-overview-empty", !model.items.length);
  if (
    (overview.hoverGroupId && !model.byId.has(overview.hoverGroupId)) ||
    (overview.hoverCandidateId && !model.byId.has(overview.hoverCandidateId))
  )
    clearActionTimelineOverviewHover(view);
  if (overview.keyboardGroupId && !model.byId.has(overview.keyboardGroupId))
    overview.keyboardGroupId = null;
  if (force) clearActionTimelineOverviewHover(view);
}

function createActionTimelineOverview(): any {
  const shell = el("section", "timeline-overview");
  shell.tabIndex = 0;
  shell.setAttribute("aria-labelledby", "timeline-overview-label");
  shell.setAttribute(
    "aria-describedby",
    "timeline-overview-help timeline-overview-keyboard-help timeline-overview-tooltip",
  );
  const head = el("div", "timeline-overview-head"),
    label = el("div", "timeline-overview-label", t("timeline.overview"));
  label.id = "timeline-overview-label";
  head.appendChild(label);
  const legend = el("div", "timeline-overview-legend");
  const legendItem = (className: string, text: string) => {
    const item = el("span", "timeline-overview-legend-item " + className);
    item.appendChild(el("i"));
    item.appendChild(el("span", null, text));
    legend.appendChild(item);
    return item;
  };
  const legendQueue = legendItem("queue", t("timeline.overview.queue"));
  const legendTtft = legendItem("ttft", t("timeline.overview.ttft"));
  const legendDecode = legendItem("decode", t("timeline.overview.decode"));
  head.appendChild(legend);
  const clearButton = el(
    "button",
    "timeline-overview-clear hidden",
    t("timeline.overview.clear"),
  ) as HTMLButtonElement;
  clearButton.type = "button";
  clearButton.disabled = true;
  clearButton.setAttribute("aria-label", t("timeline.overview.clear"));
  head.appendChild(clearButton);
  shell.appendChild(head);
  const help = el("div", "timeline-overview-help", t("timeline.overview.help"));
  help.id = "timeline-overview-help";
  shell.appendChild(help);
  const keyboardHelp = el(
    "div",
    "timeline-overview-keyboard-help",
    t("timeline.overview.keyboard"),
  );
  keyboardHelp.id = "timeline-overview-keyboard-help";
  shell.appendChild(keyboardHelp);
  const selectionStatus = el("div", "timeline-overview-selection-status");
  selectionStatus.setAttribute("aria-live", "polite");
  shell.appendChild(selectionStatus);
  const plot = el("div", "timeline-overview-plot");
  const svg = svgElement("svg", {
    viewBox: `0 0 ${ACTION_TIMELINE_OVERVIEW_WIDTH} ${ACTION_TIMELINE_OVERVIEW_HEIGHT}`,
    preserveAspectRatio: "none",
    role: "img",
    "aria-label": t("timeline.overview"),
  });
  svg.appendChild(
    svgElement("rect", {
      class: "timeline-overview-background",
      x: 0,
      y: 0,
      width: ACTION_TIMELINE_OVERVIEW_WIDTH,
      height: ACTION_TIMELINE_OVERVIEW_HEIGHT,
    }),
  );
  const queuePath = svgElement("path", { class: "timeline-overview-phase queue" });
  const ttftPath = svgElement("path", { class: "timeline-overview-phase ttft" });
  const decodePath = svgElement("path", { class: "timeline-overview-phase decode" });
  const pointPath = svgElement("path", { class: "timeline-overview-point" });
  const markerPath = svgElement("path", { class: "timeline-overview-running" });
  const selectionRect = svgElement("rect", {
    class: "timeline-overview-selection hidden",
    x: 0,
    y: 0,
    width: 0,
    height: ACTION_TIMELINE_OVERVIEW_HEIGHT,
  });
  const selectedPath = svgElement("path", { class: "timeline-overview-highlight selected" });
  const hoverPath = svgElement("path", { class: "timeline-overview-highlight hover" });
  [queuePath, ttftPath, decodePath, pointPath, markerPath, selectionRect, selectedPath, hoverPath].forEach(
    (node) => svg.appendChild(node),
  );
  plot.appendChild(svg);
  const prefixButton = el("button", "timeline-overview-prefix hidden", "…") as HTMLButtonElement;
  prefixButton.type = "button";
  prefixButton.setAttribute("data-action", "load-omitted-timeline");
  prefixButton.setAttribute("aria-label", t("timeline.overview.omitted"));
  prefixButton.setAttribute("aria-busy", "false");
  plot.appendChild(prefixButton);
  shell.appendChild(plot);
  const tooltip = el("div", "timeline-overview-tooltip hidden");
  tooltip.id = "timeline-overview-tooltip";
  tooltip.setAttribute("role", "tooltip");
  tooltip.setAttribute("aria-live", "polite");
  tooltip.setAttribute("aria-hidden", "true");
  shell.appendChild(tooltip);
  const axis = el("div", "timeline-overview-axis"),
    axisStart = el("time"),
    axisEnd = el("time"),
    nav = el("div", "timeline-overview-nav");
  const navButton = (text: string, key: string) => {
    const button = el("button", "timeline-overview-nav-button", text) as HTMLButtonElement;
    button.type = "button";
    button.title = t(key);
    button.setAttribute("aria-label", t(key));
    nav.appendChild(button);
    return button;
  };
  const panEarlierButton = navButton("←", "timeline.overview.panEarlier"),
    zoomOutButton = navButton("−", "timeline.overview.zoomOut");
  const zoomInButton = navButton("+", "timeline.overview.zoomIn"),
    panLaterButton = navButton("→", "timeline.overview.panLater");
  axis.appendChild(axisStart);
  axis.appendChild(nav);
  axis.appendChild(axisEnd);
  shell.appendChild(axis);
  return {
    shell,
    head,
    label,
    help,
    keyboardHelp,
    selectionStatus,
    clearButton,
    plot,
    svg,
    prefixButton,
    tooltip,
    axis,
    axisStart,
    axisEnd,
    nav,
    zoomInButton,
    zoomOutButton,
    panEarlierButton,
    panLaterButton,
    queuePath,
    ttftPath,
    decodePath,
    pointPath,
    markerPath,
    selectionRect,
    selectedPath,
    hoverPath,
    legendQueue,
    legendTtft,
    legendDecode,
    model: actionTimelineOverviewModel([]),
    dataStart: null,
    dataEnd: null,
    viewStart: null,
    viewEnd: null,
    initialized: false,
    selection: null,
    draftSelection: null,
    gesture: null,
    hoverTimer: 0,
    hoverLeaveTimer: 0,
    tooltipHovered: false,
    hoverCandidateId: null,
    hoverGroupId: null,
    hoverPoint: null,
    keyboardGroupId: null,
    restoreFocusAfterPrefix: false,
    raf: 0,
  };
}

function actionTimelineTurnToggle(
  turnId: string,
  stats: any,
  expanded: boolean,
  view: View,
): HTMLButtonElement {
  const label = t(
    expanded ? "timeline.turn.collapse" : "timeline.turn.expand",
    shortRuntime(turnId),
    stats.count,
    stats.duration,
  );
  const button = el("button", "timeline-turn-toggle") as HTMLButtonElement;
  button.type = "button";
  button.dataset.turnId = turnId;
  button.setAttribute("aria-expanded", expanded ? "true" : "false");
  button.setAttribute("aria-label", label);
  button.title = label;
  const chevron = el("span", "timeline-turn-chevron", expanded ? "▾" : "▸");
  chevron.setAttribute("aria-hidden", "true");
  button.appendChild(chevron);
  button.appendChild(el("span", "timeline-turn-marker", t("timeline.turnBoundary")));
  button.onclick = (event) => {
    event.stopPropagation();
    toggleActionTimelineTurn(view, turnId);
  };
  return button;
}

function actionTimelineLedgerRow(
  group: Group,
  reusableRow: HTMLTableRowElement | null,
  turnBoundary: boolean,
  selected: boolean,
  branchScope: string,
  firstRow = false,
  options: any = {},
): HTMLTableRowElement {
  const kind = timelineKind(group),
    status = String(group.status || "completed").toLowerCase();
  const statusClass = status.replace(/[^a-z0-9_-]/g, "-") || "completed";
  const row = (reusableRow || el("tr", "timeline-ledger-row")) as HTMLTableRowElement;
  row.className =
    "timeline-ledger-row kind-" +
    kind +
    " status-" +
    statusClass +
    (turnBoundary ? " turn-boundary" : "") +
    (selected ? " selected" : "") +
    (firstRow ? " timeline-first-row" : "") +
    (options.searchMatch ? " search-match" : "");
  row.setAttribute("role", "row");
  row.dataset.groupId = group.group_id;
  row.dataset.turnId = group.turn_id || "";
  row.dataset.actionKind = kind;
  row.dataset.status = statusClass;
  const ordinal = timelineOrdinal(group.ordinal),
    ordinalText = ordinal == null ? "—" : String(ordinal);
  row.replaceChildren();
  const cell = (className: string) => {
    const node = el("td", className);
    node.setAttribute("role", "cell");
    return node;
  };
  const ordinalCell = cell("timeline-ledger-ordinal");
  if (options.turnStart) {
    if (options.foldable && options.stats)
      ordinalCell.appendChild(
        actionTimelineTurnToggle(options.turnId, options.stats, true, S._timelineView),
      );
    else ordinalCell.appendChild(el("span", "timeline-turn-marker", t("timeline.turnBoundary")));
  }
  ordinalCell.appendChild(el("span", "timeline-ordinal-value", "#" + ordinalText));
  row.appendChild(ordinalCell);
  const kindCell = cell("timeline-ledger-kind");
  const kindIcon = el("span", "timeline-kind-icon");
  kindIcon.title = t("timeline.kind." + kind);
  kindIcon.setAttribute("aria-hidden", "true");
  kindIcon.appendChild(iconEl(timelineKindIcon(kind), 15));
  kindCell.appendChild(kindIcon);
  kindCell.appendChild(el("span", "timeline-kind-label", t("timeline.kind." + kind)));
  row.appendChild(kindCell);
  const title = group.title || t("timeline.kind." + kind),
    titleCell = cell("timeline-ledger-title");
  const statusText = publicText(status || "completed", 32);
  const statusNoteworthy = statusClass !== "completed" && statusClass !== "recorded";
  if (statusNoteworthy)
    titleCell.appendChild(
      el("span", "timeline-ledger-status timeline-status " + statusClass, statusText),
    );
  const titleButton = el("button", "timeline-row-button", title) as HTMLButtonElement;
  titleButton.type = "button";
  const latest = latestActionTimelineAttempt(group),
    rowError = latest && latest.error;
  titleButton.title = rowError ? title + " — " + rowError : title;
  titleButton.setAttribute(
    "aria-label",
    statusNoteworthy
      ? t("timeline.row.open", ordinalText, title) +
          " · " +
          statusText +
          (rowError ? " · " + rowError : "")
      : t("timeline.row.open", ordinalText, title),
  );
  titleButton.setAttribute("aria-expanded", selected ? "true" : "false");
  if (selected) titleButton.setAttribute("aria-controls", "timeline-action-inspector");
  titleCell.appendChild(titleButton);
  row.appendChild(titleCell);
  const durationCell = cell("timeline-ledger-duration");
  durationCell.textContent = timelineDuration(latest) || "—";
  row.appendChild(durationCell);
  const tokens = cell("timeline-ledger-tokens");
  tokens.textContent = group.usage ? String(timelineTokenTotal(group.usage)) : "—";
  if (group.usage)
    tokens.title = t(
      "timeline.tokensValue",
      (group.usage || {}).input_tokens || 0,
      (group.usage || {}).output_tokens || 0,
    );
  row.appendChild(tokens);
  const groupId = group.group_id;
  row.onclick = () => selectActionTimelineGroup(groupId, branchScope, false);
  const bag = row as any;
  bag._timelineGroup = group;
  bag._timelineTurnBoundary = turnBoundary;
  bag._timelineSelected = selected;
  bag._timelineBranchScope = branchScope;
  bag._timelineLanguage = LANG;
  bag._timelineFirstRow = firstRow;
  bag._timelineTurnStart = !!options.turnStart;
  bag._timelineTurnSignature = options.stats ? options.stats.count + ":" + options.stats.duration : "";
  bag._timelineFoldable = !!options.foldable;
  bag._timelineSearchMatch = !!options.searchMatch;
  return row;
}

function actionTimelineTurnSummaryRow(
  entry: any,
  reusableRow: HTMLTableRowElement | null,
  branchScope: string,
  firstRow = false,
): HTMLTableRowElement {
  const row = (reusableRow ||
    el("tr", "timeline-ledger-row timeline-turn-summary")) as HTMLTableRowElement;
  row.className =
    "timeline-ledger-row timeline-turn-summary" +
    (entry.turnBoundary ? " turn-boundary" : "") +
    (firstRow ? " timeline-first-row" : "");
  row.setAttribute("role", "row");
  delete row.dataset.groupId;
  delete row.dataset.actionKind;
  delete row.dataset.status;
  row.dataset.turnId = entry.turnId;
  row.replaceChildren();
  const cell = (className: string, text?: string) => {
    const node = el("td", className, text);
    node.setAttribute("role", "cell");
    return node;
  };
  const ordinalCell = cell("timeline-ledger-ordinal");
  ordinalCell.appendChild(
    actionTimelineTurnToggle(entry.turnId, entry.stats, false, S._timelineView),
  );
  row.appendChild(ordinalCell);
  const kindCell = cell("timeline-ledger-kind");
  const chevron = el("span", "timeline-turn-summary-icon", "↳");
  chevron.setAttribute("aria-hidden", "true");
  kindCell.appendChild(chevron);
  row.appendChild(kindCell);
  row.appendChild(cell("timeline-ledger-title", t("timeline.turn.summary", entry.stats.count)));
  row.appendChild(cell("timeline-ledger-duration", entry.stats.duration));
  row.appendChild(cell("timeline-ledger-tokens", "—"));
  row.onclick = () => toggleActionTimelineTurn(S._timelineView, entry.turnId);
  const bag = row as any;
  bag._timelineTurnId = entry.turnId;
  bag._timelineTurnBoundary = entry.turnBoundary;
  bag._timelineTurnSignature = entry.stats.count + ":" + entry.stats.duration;
  bag._timelineBranchScope = branchScope;
  bag._timelineLanguage = LANG;
  bag._timelineFirstRow = firstRow;
  return row;
}

function actionTimelineEntryIndexForGroup(view: View, groupId: string): number {
  return (view.entries || []).findIndex((entry: any) =>
    entry.type === "group"
      ? entry.group.group_id === groupId
      : entry.groups.some((group: Group) => group.group_id === groupId),
  );
}

function focusActionTimelineEntry(view: View, index: number): void {
  if (!view || !view.entries.length) return;
  const targetIndex = Math.max(0, Math.min(view.entries.length - 1, index)),
    entry = view.entries[targetIndex];
  const viewportTop = targetIndex * ACTION_TIMELINE_ROW_HEIGHT;
  const headerHeight = actionTimelineHeaderHeight(view);
  const viewportBottom = viewportTop + ACTION_TIMELINE_ROW_HEIGHT + headerHeight;
  if (viewportTop < view.scroll.scrollTop) view.scroll.scrollTop = viewportTop;
  else if (viewportBottom > view.scroll.scrollTop + view.scroll.clientHeight)
    view.scroll.scrollTop = viewportBottom - view.scroll.clientHeight;
  view.scrollTop = view.scroll.scrollTop;
  if (entry.type === "group") S._timelineRestoreFocusGroupId = entry.group.group_id;
  else view.restoreFocusTurnId = entry.turnId;
  reconcileActionTimelineWindow(view);
}

function focusActionTimelineGroup(view: View, index: number): void {
  if (!view || !view.groups.length) return;
  const group = view.groups[Math.max(0, Math.min(view.groups.length - 1, index))];
  const entryIndex = group && actionTimelineEntryIndexForGroup(view, group.group_id);
  if (entryIndex >= 0) focusActionTimelineEntry(view, entryIndex);
}

function actionTimelineLedgerKeydown(view: View, event: KeyboardEvent): void {
  const target = event.target as HTMLElement | null;
  const row = target && target.closest ? target.closest(".timeline-ledger-row") : null;
  if (!row || !view || view !== S._timelineView) return;
  const index = view.entries.findIndex((entry: any) =>
    entry.type === "group"
      ? entry.group.group_id === (row as HTMLElement).dataset.groupId
      : entry.turnId === (row as HTMLElement).dataset.turnId,
  );
  if (index < 0) return;
  const page = Math.max(1, Math.floor(view.scroll.clientHeight / ACTION_TIMELINE_ROW_HEIGHT) - 1);
  const targets: Record<string, number> = {
    ArrowUp: index - 1,
    ArrowDown: index + 1,
    PageUp: index - page,
    PageDown: index + page,
    Home: 0,
    End: view.entries.length - 1,
  };
  if (!(event.key in targets)) return;
  event.preventDefault();
  focusActionTimelineEntry(view, targets[event.key]!);
}

export function sortedActionTimelineGroups(timeline: ActionTimeline | null = S.actionTimeline): Group[] {
  return ((timeline && timeline.groups) || [])
    .filter((group) => !!group.group_id)
    .slice()
    .sort((left, right) => {
      const leftOrdinal = timelineOrdinal(left.ordinal),
        rightOrdinal = timelineOrdinal(right.ordinal);
      if (leftOrdinal != null && rightOrdinal != null && leftOrdinal !== rightOrdinal)
        return leftOrdinal - rightOrdinal;
      if (leftOrdinal != null && rightOrdinal == null) return -1;
      if (leftOrdinal == null && rightOrdinal != null) return 1;
      const created = (+(left.created_at as number) || 0) - (+(right.created_at as number) || 0);
      return created || String(left.group_id).localeCompare(String(right.group_id));
    }) as Group[];
}

export function destroyActionTimelineView(view: View = S._timelineView): void {
  if (!view) return;
  if (view.raf) cancelAnimationFrame(view.raf);
  if (view.overview && view.overview.raf) cancelAnimationFrame(view.overview.raf);
  if (view.overview && view.overview.dismissKeydown)
    document.removeEventListener("keydown", view.overview.dismissKeydown);
  clearActionTimelineOverviewHover(view);
  if (view.resizeObserver) view.resizeObserver.disconnect();
  if (S._timelineView === view) S._timelineView = null;
}

function actionTimelineViewMatches(view: View, rootFrameId: string, branchId: string): boolean {
  return !!view && view.rootFrameId === rootFrameId && view.branchId === branchId;
}

function actionTimelineBottomDistance(view: View): number {
  return Math.max(0, view.scroll.scrollHeight - view.scroll.clientHeight - view.scroll.scrollTop);
}

function scheduleActionTimelineWindow(view: View): void {
  if (!view || view !== S._timelineView || view.raf) return;
  view.raf = requestAnimationFrame(() => {
    view.raf = 0;
    if (view === S._timelineView && view.region.isConnected) reconcileActionTimelineWindow(view);
  });
}

function actionTimelineViewportScrolled(view: View): void {
  if (!view || view !== S._timelineView) return;
  view.scrollTop = view.scroll.scrollTop;
  view.scrollLeft = view.scroll.scrollLeft;
  view.followTail = actionTimelineBottomDistance(view) <= ACTION_TIMELINE_BOTTOM_THRESHOLD;
  if (view.scroll.scrollTop > ACTION_TIMELINE_TOP_THRESHOLD) {
    view.autoLoadArmed = true;
    view.autoLoadCursor = null;
  }
  scheduleActionTimelineWindow(view);
  const timeline = S.actionTimeline || {},
    first = timelineOrdinal(timeline.first_ordinal);
  if (
    !view.overview.selection &&
    !view.searchNeedle &&
    view.scroll.scrollTop <= ACTION_TIMELINE_TOP_THRESHOLD &&
    timeline.has_more_before &&
    !S._timelineHistoryLoading &&
    view.autoLoadArmed &&
    first != null &&
    view.autoLoadCursor !== first
  ) {
    view.autoLoadArmed = false;
    view.autoLoadCursor = first;
    void loadEarlierActionTimeline();
  }
}

function createActionTimelineToolbar(): any {
  const shell = el("form", "timeline-toolbar") as HTMLFormElement;
  shell.setAttribute("role", "search");
  shell.onsubmit = (event) => event.preventDefault();
  const field = el("div", "timeline-search-field");
  const label = el("label", "timeline-search-label", t("timeline.search.label")) as HTMLLabelElement;
  label.htmlFor = "timeline-action-search";
  field.appendChild(label);
  const controls = el("div", "timeline-search-controls");
  const input = el("input", "timeline-search-input") as HTMLInputElement;
  input.id = "timeline-action-search";
  input.type = "search";
  input.maxLength = 256;
  input.autocomplete = "off";
  input.spellcheck = false;
  input.placeholder = t("timeline.search.placeholder");
  input.setAttribute("aria-describedby", "timeline-search-scope timeline-search-status");
  controls.appendChild(input);
  const clearButton = el(
    "button",
    "timeline-search-clear hidden",
    t("timeline.search.clear"),
  ) as HTMLButtonElement;
  clearButton.type = "button";
  clearButton.disabled = true;
  clearButton.setAttribute("aria-label", t("timeline.search.clear"));
  controls.appendChild(clearButton);
  field.appendChild(controls);
  shell.appendChild(field);
  const meta = el("div", "timeline-search-meta");
  const status = el("span", "timeline-search-status", t("timeline.search.loaded", 0));
  status.id = "timeline-search-status";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  status.setAttribute("aria-atomic", "true");
  meta.appendChild(status);
  const scope = el("span", "timeline-search-scope", t("timeline.search.scope", 0));
  scope.id = "timeline-search-scope";
  meta.appendChild(scope);
  shell.appendChild(meta);
  return { shell, label, input, clearButton, status, scope };
}

function syncActionTimelineSearchToolbar(
  view: View,
  loadedCount: number,
  matchCount: number,
  searchMatchCount = matchCount,
  force = false,
): void {
  const search = view.search,
    active = !!view.searchNeedle;
  if (search.input.value !== view.searchQuery) search.input.value = view.searchQuery;
  search.shell.dataset.matchCount = String(matchCount);
  search.shell.dataset.searchMatchCount = String(searchMatchCount);
  search.shell.dataset.loadedCount = String(loadedCount);
  search.status.textContent =
    active && view.overview.selection
      ? t("timeline.search.matchesInSelection", matchCount, searchMatchCount, loadedCount)
      : t(
          active ? "timeline.search.matches" : "timeline.search.loaded",
          active ? matchCount : loadedCount,
          loadedCount,
        );
  search.scope.textContent = t("timeline.search.scope", loadedCount);
  search.clearButton.classList.toggle("hidden", !active);
  search.clearButton.disabled = !active;
  if (force) {
    search.label.textContent = t("timeline.search.label");
    search.input.placeholder = t("timeline.search.placeholder");
    search.clearButton.textContent = t("timeline.search.clear");
    search.clearButton.setAttribute("aria-label", t("timeline.search.clear"));
  }
}

function changeActionTimelineSearch(view: View, rawQuery: string): void {
  if (!view || view !== S._timelineView) return;
  const query = String(rawQuery || "").slice(0, 256),
    needle = normalizeActionTimelineSearch(query);
  if (query === view.searchQuery && needle === view.searchNeedle) return;
  const wasFiltered = !!view.searchNeedle || !!view.overview.selection;
  if (!wasFiltered && needle) view.preFilterScroll = actionTimelineFilterScrollSnapshot(view);
  view.searchQuery = query;
  view.searchNeedle = needle;
  view.autoLoadArmed = false;
  let restore = null as any;
  if (!needle && !view.overview.selection) {
    restore = view.preFilterScroll;
    view.preFilterScroll = null;
  }
  clearActionTimelineOverviewHover(view);
  updateActionTimelineLedger({ direction: "search", filterChanged: true, filterRestore: restore });
}

export function toggleActionTimelineTurn(view: View, turnId: string): void {
  if (!view || view !== S._timelineView || !turnId || view.searchNeedle) return;
  const snapshot = actionTimelineFilterScrollSnapshot(view),
    collapsing = !view.collapsedTurns.has(turnId);
  if (collapsing) view.collapsedTurns.add(turnId);
  else view.collapsedTurns.delete(turnId);
  if (collapsing) {
    const selected = view.groups.find(
      (group: Group) => group.group_id === S.actionTimelineSelectedGroupId,
    );
    if (selected && selected.turn_id === turnId) {
      S.actionTimelineSelectedGroupId = null;
      S.actionTimelineSelectedBranchId = null;
    }
  }
  view.restoreFocusTurnId = turnId;
  view.autoLoadArmed = false;
  updateActionTimelineLedger({ direction: "fold", filterChanged: true, filterRestore: snapshot });
}

function createActionTimelineView(rootFrameId: string, branchId: string): View {
  const region = el("div", "timeline-ledger-region");
  region.dataset.rootFrameId = rootFrameId;
  region.dataset.branchId = branchId;
  region.style.setProperty("--timeline-row-height", ACTION_TIMELINE_ROW_HEIGHT + "px");
  const search = createActionTimelineToolbar();
  region.appendChild(search.shell);
  const overview = createActionTimelineOverview();
  region.appendChild(overview.shell);
  const inspectorHost = el("div", "timeline-inspector-host hidden");
  region.appendChild(inspectorHost);
  const filterEmpty = el(
    "div",
    "workbench-empty timeline-filter-empty hidden",
    t("timeline.overview.emptySelection"),
  );
  region.appendChild(filterEmpty);
  const ledgerHelp = el("div", "timeline-ledger-keyboard-help", t("timeline.ledger.keyboard"));
  ledgerHelp.id = "timeline-ledger-keyboard-help";
  region.appendChild(ledgerHelp);
  const scroll = el("div", "timeline-ledger-scroll");
  scroll.tabIndex = 0;
  scroll.setAttribute("role", "region");
  scroll.setAttribute("aria-label", t("timeline.title"));
  scroll.setAttribute("aria-describedby", ledgerHelp.id);
  const table = el("table", "timeline-ledger") as HTMLTableElement;
  table.setAttribute("role", "table");
  table.setAttribute("aria-colcount", "5");
  const thead = el("thead"),
    header = el("tr");
  thead.setAttribute("role", "rowgroup");
  header.setAttribute("role", "row");
  header.setAttribute("aria-rowindex", "1");
  const headerColumns: Array<[string, string]> = [
    ["timeline.column.ordinal", "timeline-ledger-ordinal"],
    ["timeline.column.kind", "timeline-ledger-kind"],
    ["timeline.column.action", "timeline-ledger-title"],
    ["timeline.duration", "timeline-ledger-duration"],
    ["timeline.tokens", "timeline-ledger-tokens"],
  ];
  headerColumns.forEach(([key, className], column) => {
    const th = el("th", className, t(key)) as HTMLTableCellElement;
    th.scope = "col";
    th.dataset.i18nKey = key;
    th.setAttribute("role", "columnheader");
    th.setAttribute("aria-colindex", String(column + 1));
    header.appendChild(th);
  });
  thead.appendChild(header);
  table.appendChild(thead);
  const tbody = el("tbody", "timeline-ledger-body");
  tbody.setAttribute("role", "rowgroup");
  table.appendChild(tbody);
  scroll.appendChild(table);
  region.appendChild(scroll);
  const view: View = {
    rootFrameId,
    branchId,
    region,
    search,
    overview,
    inspectorHost,
    filterEmpty,
    ledgerHelp,
    scroll,
    table,
    thead,
    tbody,
    allGroups: [],
    groups: [],
    entries: [],
    initialized: false,
    followTail: true,
    scrollTop: 0,
    scrollLeft: 0,
    headerHeight: 0,
    start: 0,
    end: 0,
    autoLoadArmed: true,
    autoLoadCursor: null,
    raf: 0,
    resizeObserver: null,
    language: LANG,
    searchQuery: "",
    searchNeedle: "",
    searchIndex: new Map(),
    collapsedTurns: new Set(),
    preFilterScroll: null,
    pendingPrependRestore: null,
    restoreFocusTurnId: null,
  };
  search.input.oninput = () => changeActionTimelineSearch(view, search.input.value);
  search.input.onkeydown = (event: KeyboardEvent) => {
    if (event.key === "Escape" && view.searchNeedle) {
      event.preventDefault();
      changeActionTimelineSearch(view, "");
    }
  };
  search.clearButton.onclick = (event: Event) => {
    event.preventDefault();
    search.input.focus();
    changeActionTimelineSearch(view, "");
  };
  overview.svg.addEventListener("pointermove", (event: PointerEvent) =>
    actionTimelineOverviewPointerMove(view, event),
  );
  overview.svg.addEventListener("pointerdown", (event: PointerEvent) =>
    beginActionTimelineOverviewGesture(view, event),
  );
  overview.svg.addEventListener("pointerup", (event: PointerEvent) =>
    finishActionTimelineOverviewGesture(view, event),
  );
  overview.svg.addEventListener("pointercancel", (event: PointerEvent) =>
    cancelActionTimelineOverviewGesture(view, event),
  );
  overview.svg.addEventListener("lostpointercapture", (event: PointerEvent) =>
    cancelActionTimelineOverviewGesture(view, event),
  );
  overview.svg.addEventListener("pointerleave", (event: PointerEvent) => {
    if (overview.gesture) return;
    if (
      event.relatedTarget === overview.tooltip ||
      overview.tooltip.contains(event.relatedTarget as Node)
    )
      return;
    scheduleActionTimelineOverviewHoverClear(view);
  });
  overview.tooltip.addEventListener("pointerenter", () => {
    overview.tooltipHovered = true;
    cancelActionTimelineOverviewHoverClear(view);
  });
  overview.tooltip.addEventListener("pointerleave", (event: PointerEvent) => {
    overview.tooltipHovered = false;
    if (event.relatedTarget === overview.svg || overview.svg.contains(event.relatedTarget as Node))
      return;
    scheduleActionTimelineOverviewHoverClear(view);
  });
  overview.svg.addEventListener(
    "wheel",
    (event: WheelEvent) => actionTimelineOverviewWheel(view, event),
    { passive: false },
  );
  overview.svg.addEventListener("contextmenu", (event: Event) => event.preventDefault());
  overview.shell.addEventListener("keydown", (event: KeyboardEvent) =>
    actionTimelineOverviewKeydown(view, event),
  );
  overview.shell.addEventListener("focus", () => {
    const groupId =
      overview.keyboardGroupId ||
      (overview.model.byId.has(S.actionTimelineSelectedGroupId)
        ? S.actionTimelineSelectedGroupId
        : (overview.model.items[0] || {}).groupId);
    if (groupId) showActionTimelineOverviewKeyboardItem(view, groupId);
  });
  overview.shell.addEventListener("focusout", (event: FocusEvent) => {
    if (!overview.shell.contains(event.relatedTarget as Node))
      clearActionTimelineOverviewHover(view);
  });
  overview.dismissKeydown = (event: KeyboardEvent) => {
    if (
      event.key === "Escape" &&
      view === S._timelineView &&
      (overview.hoverTimer || overview.hoverGroupId)
    ) {
      clearActionTimelineOverviewHover(view);
      event.preventDefault();
    }
  };
  document.addEventListener("keydown", overview.dismissKeydown);
  overview.clearButton.onclick = (event: Event) => {
    event.stopPropagation();
    clearActionTimelineOverviewSelection(view);
  };
  overview.zoomInButton.onclick = (event: Event) => {
    event.stopPropagation();
    actionTimelineOverviewZoomAt(view, 0.75, 0.5);
  };
  overview.zoomOutButton.onclick = (event: Event) => {
    event.stopPropagation();
    actionTimelineOverviewZoomAt(view, 4 / 3, 0.5);
  };
  overview.panEarlierButton.onclick = (event: Event) => {
    event.stopPropagation();
    const span = overview.viewEnd - overview.viewStart;
    actionTimelineOverviewPanBy(view, -span * 0.1);
  };
  overview.panLaterButton.onclick = (event: Event) => {
    event.stopPropagation();
    const span = overview.viewEnd - overview.viewStart;
    actionTimelineOverviewPanBy(view, span * 0.1);
  };
  overview.prefixButton.addEventListener("pointerdown", (event: Event) => event.stopPropagation());
  overview.prefixButton.onclick = (event: Event) => {
    event.stopPropagation();
    void loadEarlierActionTimeline();
    overview.restoreFocusAfterPrefix = !!S._timelineHistoryLoading;
  };
  scroll.addEventListener("scroll", () => actionTimelineViewportScrolled(view), { passive: true });
  scroll.addEventListener("keydown", (event: KeyboardEvent) => {
    if (
      event.target !== scroll ||
      !view.entries.length ||
      !["Enter", "ArrowDown", "Home"].includes(event.key)
    )
      return;
    const headerHeight = actionTimelineHeaderHeight(view);
    const firstVisible = Math.max(
      0,
      Math.min(
        view.entries.length - 1,
        Math.floor(Math.max(0, view.scroll.scrollTop - headerHeight) / ACTION_TIMELINE_ROW_HEIGHT),
      ),
    );
    event.preventDefault();
    focusActionTimelineEntry(view, event.key === "Home" ? 0 : firstVisible);
  });
  table.addEventListener("keydown", (event: KeyboardEvent) =>
    actionTimelineLedgerKeydown(view, event),
  );
  if (typeof ResizeObserver !== "undefined") {
    view.resizeObserver = new ResizeObserver(() => scheduleActionTimelineWindow(view));
    view.resizeObserver.observe(scroll);
  }
  S._timelineView = view;
  return view;
}

function actionTimelineLedger(
  _groups: Group[],
  branchScope: string,
  rootFrameScope: string,
): HTMLElement {
  let view = S._timelineView;
  if (!actionTimelineViewMatches(view, rootFrameScope, branchScope)) {
    destroyActionTimelineView(view);
    view = createActionTimelineView(rootFrameScope, branchScope);
  }
  return view.region;
}

function syncActionTimelineInspector(view: View, force = false): void {
  const selected =
    view.groups.find((group: Group) => group.group_id === S.actionTimelineSelectedGroupId) || null;
  const current = view.inspectorHost.firstElementChild;
  if (!selected) {
    view.inspectorHost.replaceChildren();
    view.inspectorHost.classList.add("hidden");
    return;
  }
  view.inspectorHost.classList.remove("hidden");
  if (
    force ||
    !current ||
    current._timelineGroup !== selected ||
    current._timelineLanguage !== LANG
  ) {
    const restoreCloseFocus = !!(current && current.contains(document.activeElement));
    const next = actionTimelineInspector(selected);
    view.inspectorHost.replaceChildren(next);
    if (restoreCloseFocus) {
      const close = next.querySelector("button");
      if (close) {
        try {
          close.focus({ preventScroll: true });
        } catch {
          close.focus();
        }
      }
    }
  }
}

function reconcileActionTimelineWindow(view: View, force = false): void {
  if (!view || view !== S._timelineView) return;
  const entries = view.entries,
    scroll = view.scroll;
  const viewportHeight = scroll.clientHeight || ACTION_TIMELINE_ROW_HEIGHT * 12;
  const headerHeight = actionTimelineHeaderHeight(view);
  const viewportStart = Math.max(0, scroll.scrollTop - headerHeight);
  const viewportEnd = Math.max(0, scroll.scrollTop + viewportHeight - headerHeight);
  const start = Math.max(
    0,
    Math.floor(viewportStart / ACTION_TIMELINE_ROW_HEIGHT) - ACTION_TIMELINE_OVERSCAN,
  );
  const end = Math.min(
    entries.length,
    Math.ceil(viewportEnd / ACTION_TIMELINE_ROW_HEIGHT) + ACTION_TIMELINE_OVERSCAN,
  );
  const activeEl = document.activeElement as HTMLElement | null;
  const activeRow =
    activeEl && activeEl.closest ? activeEl.closest(".timeline-ledger-row") : null;
  const activeGroupId = activeRow && (activeRow as HTMLElement).dataset.groupId,
    activeTurnId = activeRow && !activeGroupId && (activeRow as HTMLElement).dataset.turnId;
  const requestedFocusGroupId = S._timelineRestoreFocusGroupId;
  S._timelineRestoreFocusGroupId = null;
  const requestedFocusTurnId = view.restoreFocusTurnId;
  view.restoreFocusTurnId = null;
  const reusableRows = new Map<string, HTMLTableRowElement>(),
    reusableTurns = new Map<string, HTMLTableRowElement>();
  view.tbody
    .querySelectorAll(".timeline-ledger-row[data-group-id]")
    .forEach((row: HTMLTableRowElement) => {
      if (row.dataset.groupId) reusableRows.set(row.dataset.groupId, row);
    });
  view.tbody
    .querySelectorAll(".timeline-turn-summary[data-turn-id]")
    .forEach((row: HTMLTableRowElement) => {
      if (row.dataset.turnId) reusableTurns.set(row.dataset.turnId, row);
    });
  const fragment = document.createDocumentFragment();
  entries.slice(start, end).forEach((entry: any, offset: number) => {
    const index = start + offset,
      firstRow = index === 0;
    let row: HTMLTableRowElement;
    if (entry.type === "turn") {
      row = reusableTurns.get(entry.turnId)!;
      const signature = entry.stats.count + ":" + entry.stats.duration;
      if (
        force ||
        !row ||
        (row as any)._timelineTurnBoundary !== entry.turnBoundary ||
        (row as any)._timelineTurnSignature !== signature ||
        (row as any)._timelineBranchScope !== view.branchId ||
        (row as any)._timelineLanguage !== LANG ||
        (row as any)._timelineFirstRow !== firstRow
      ) {
        row = actionTimelineTurnSummaryRow(entry, row, view.branchId, firstRow);
      }
    } else {
      const group = entry.group,
        selected = group.group_id === S.actionTimelineSelectedGroupId;
      row = reusableRows.get(group.group_id)!;
      const signature = entry.stats ? entry.stats.count + ":" + entry.stats.duration : "";
      if (
        force ||
        !row ||
        (row as any)._timelineGroup !== group ||
        (row as any)._timelineTurnBoundary !== entry.turnBoundary ||
        (row as any)._timelineSelected !== selected ||
        (row as any)._timelineBranchScope !== view.branchId ||
        (row as any)._timelineLanguage !== LANG ||
        (row as any)._timelineFirstRow !== firstRow ||
        (row as any)._timelineTurnStart !== entry.turnStart ||
        (row as any)._timelineTurnSignature !== signature ||
        (row as any)._timelineFoldable !== entry.foldable ||
        (row as any)._timelineSearchMatch !== !!view.searchNeedle
      ) {
        row = actionTimelineLedgerRow(
          group,
          row,
          entry.turnBoundary,
          selected,
          view.branchId,
          firstRow,
          {
            turnStart: entry.turnStart,
            turnId: entry.turnId,
            stats: entry.stats,
            foldable: entry.foldable,
            searchMatch: !!view.searchNeedle,
          },
        );
      }
    }
    row.style.transform = `translateY(${index * ACTION_TIMELINE_ROW_HEIGHT}px)`;
    row.setAttribute("aria-rowindex", String(index + 2));
    fragment.appendChild(row);
  });
  view.tbody.replaceChildren(fragment);
  view.start = start;
  view.end = end;
  const focusGroupId = requestedFocusGroupId || activeGroupId,
    focusTurnId = requestedFocusTurnId || activeTurnId;
  if (focusGroupId) {
    const focusRow = (
      Array.from(
        view.tbody.querySelectorAll(".timeline-ledger-row[data-group-id]"),
      ) as HTMLElement[]
    ).find((row) => row.dataset.groupId === focusGroupId);
    const group = view.groups.find((item: Group) => item.group_id === focusGroupId);
    const summaryRow =
      !focusRow && group
        ? (
            Array.from(
              view.tbody.querySelectorAll(".timeline-turn-summary[data-turn-id]"),
            ) as HTMLElement[]
          ).find((row) => row.dataset.turnId === group.turn_id)
        : null;
    const focusTarget = focusRow
      ? focusRow.querySelector(".timeline-row-button")
      : summaryRow && summaryRow.querySelector(".timeline-turn-toggle");
    if (focusTarget && document.activeElement !== focusTarget) {
      try {
        (focusTarget as HTMLElement).focus({ preventScroll: true });
      } catch {
        (focusTarget as HTMLElement).focus();
      }
    }
  } else if (focusTurnId) {
    const turnRow = (
      Array.from(
        view.tbody.querySelectorAll(".timeline-ledger-row[data-turn-id]"),
      ) as HTMLElement[]
    ).find((row) => row.dataset.turnId === focusTurnId);
    const focusTarget =
      turnRow &&
      (turnRow.querySelector(".timeline-turn-toggle") ||
        turnRow.querySelector(".timeline-row-button"));
    if (focusTarget && document.activeElement !== focusTarget) {
      try {
        (focusTarget as HTMLElement).focus({ preventScroll: true });
      } catch {
        (focusTarget as HTMLElement).focus();
      }
    }
  }
}

export function updateActionTimelineLedger(options: any = {}): void {
  if (S.activeTab !== "timeline") return;
  const timeline = S.actionTimeline || {},
    allGroups = sortedActionTimelineGroups(timeline);
  const rootFrameScope = actionTimelineRootScope(timeline),
    branchScope = actionTimelineBranchScope(timeline, allGroups);
  const view = S._timelineView;
  if (
    !allGroups.length ||
    !actionTimelineViewMatches(view, rootFrameScope, branchScope) ||
    !view.region.isConnected
  ) {
    renderActionTimeline();
    return;
  }
  const previousVisibleGroups = view.groups;
  const force = view.language !== LANG;
  view.allGroups = allGroups;
  view.language = LANG;
  const searchGroups = searchActionTimelineGroups(view, allGroups);
  drawActionTimelineOverview(view, searchGroups, force, allGroups);
  const groups = filteredActionTimelineGroups(view, searchGroups),
    entries = actionTimelineLedgerEntries(view, groups);
  const selectedHasActionRow = entries.some(
    (entry) => entry.type === "group" && entry.group.group_id === S.actionTimelineSelectedGroupId,
  );
  if (
    S.actionTimelineSelectedBranchId !== branchScope ||
    !groups.some((group: Group) => group.group_id === S.actionTimelineSelectedGroupId) ||
    !selectedHasActionRow
  ) {
    S.actionTimelineSelectedGroupId = null;
    S.actionTimelineSelectedBranchId = null;
  }
  const previousVisibleIds = new Set(
    previousVisibleGroups.map((group: Group) => group.group_id),
  );
  const tailAdded =
    !options.filterChanged && groups.some((group: Group) => !previousVisibleIds.has(group.group_id));
  view.groups = groups;
  view.entries = entries;
  syncActionTimelineOverviewDecorations(view);
  syncActionTimelineSearchToolbar(
    view,
    allGroups.length,
    groups.length,
    searchGroups.length,
    force,
  );
  view.search.shell.dataset.visibleCount = String(groups.length);
  view.table.setAttribute("aria-rowcount", String(entries.length + 1));
  view.table.setAttribute("aria-label", t("timeline.title"));
  view.scroll.setAttribute("aria-label", t("timeline.title"));
  view.tbody.style.height = entries.length * ACTION_TIMELINE_ROW_HEIGHT + "px";
  const filterEmpty = (view.overview.selection || view.searchNeedle) && !groups.length;
  view.filterEmpty.classList.toggle("hidden", !filterEmpty);
  if (filterEmpty)
    view.filterEmpty.textContent = t(
      view.searchNeedle
        ? view.overview.selection && searchGroups.length
          ? "timeline.search.emptySelection"
          : "timeline.search.empty"
        : "timeline.overview.emptySelection",
      searchGroups.length,
    );
  view.overview.selectionStatus.dataset.matchCount = String(groups.length);
  if (force) {
    view.thead.querySelectorAll("th[data-i18n-key]").forEach((th: HTMLElement) => {
      th.textContent = t(th.dataset.i18nKey || "");
    });
    view.ledgerHelp.textContent = t("timeline.ledger.keyboard");
    if (!filterEmpty) view.filterEmpty.textContent = t("timeline.overview.emptySelection");
  }
  syncActionTimelineInspector(view, force);
  const snapshot = options.prependSnapshot,
    pendingPrependRestore = view.pendingPrependRestore;
  view.pendingPrependRestore = null;
  if (snapshot && snapshot.node === view.scroll) {
    const delta = view.scroll.scrollHeight - snapshot.scrollHeight;
    view.scroll.scrollTop = snapshot.scrollTop + delta;
    view.followTail =
      !!snapshot.followTail &&
      actionTimelineBottomDistance(view) <= ACTION_TIMELINE_BOTTOM_THRESHOLD;
  } else if (options.filterChanged || pendingPrependRestore) {
    const restore = options.filterRestore || pendingPrependRestore;
    if (restore && restore.followTail)
      view.scroll.scrollTop = Math.max(0, view.scroll.scrollHeight - view.scroll.clientHeight);
    else if (restore && (restore.entryKey || restore.groupId || restore.turnId)) {
      let index = entries.findIndex((entry) => actionTimelineEntryKey(entry) === restore.entryKey);
      if (index < 0 && restore.groupId)
        index = actionTimelineEntryIndexForGroup(view, restore.groupId);
      if (index < 0 && restore.turnId)
        index = entries.findIndex((entry) => (entry as any).turnId === restore.turnId);
      const headerHeight = actionTimelineHeaderHeight(view);
      view.scroll.scrollTop =
        index >= 0
          ? Math.max(0, headerHeight + index * ACTION_TIMELINE_ROW_HEIGHT + restore.offset)
          : 0;
    } else view.scroll.scrollTop = 0;
    view.followTail = !!(restore && restore.followTail);
    view.initialized = true;
  } else if (!view.initialized && entries.length && view.scroll.clientHeight > 0) {
    view.scroll.scrollTop = Math.max(0, view.scroll.scrollHeight - view.scroll.clientHeight);
    view.initialized = true;
    view.followTail = true;
  } else if (view.initialized && view.followTail && tailAdded) {
    view.scroll.scrollTop = Math.max(0, view.scroll.scrollHeight - view.scroll.clientHeight);
  } else if (view.initialized) {
    view.scroll.scrollTop = view.scrollTop;
  }
  view.scroll.scrollLeft = view.scrollLeft;
  if (view.scroll.clientHeight > 0) {
    view.scrollTop = view.scroll.scrollTop;
    view.scrollLeft = view.scroll.scrollLeft;
  }
  reconcileActionTimelineWindow(view, force);
  syncActionTimelineHistoryState();
  if (!view.initialized && entries.length) scheduleActionTimelineWindow(view);
}

function recoveryIsCurrentBranch(actions: any): boolean {
  if (!actions || !S.currentId) return false;
  const projectedBranch = publicText(S.branchState && S.branchState.branch_id, 96) || S.currentId;
  return actions.root_frame_id === S.currentId && actions.branch_id === projectedBranch;
}

async function executeRecoveryAction(actionId: string): Promise<void> {
  const projection = S.recoveryActions;
  const action = projection && (projection.actions || []).find((item: any) => item.id === actionId);
  if (
    !(RECOVERY_ACTION_IDS as readonly string[]).includes(actionId) ||
    !action ||
    !action.enabled ||
    !recoveryIsCurrentBranch(projection) ||
    S._recoveryActionLoading
  )
    return;
  if (actionId === "restart_fresh" && !confirm(t("recovery.freshConfirm"))) return;
  const frameId = S.currentId;
  S._recoveryActionLoading = actionId;
  delete S.workbenchErrors.recoveryAction;
  renderActionTimeline();
  try {
    await api(`/frames/${frameId}/recovery/actions/${actionId}`, {
      method: "POST",
      body: JSON.stringify({
        branch_id: projection.branch_id,
        confirm: actionId === "restart_fresh",
      }),
    });
    await Promise.all([
      loadWorkbenchState(frameId, true),
      Promise.resolve(laneCall("loadExecutionLog", frameId)),
    ]);
    if (S.currentId === frameId) hint(t("recovery.action.done"));
  } catch (error) {
    if (S.currentId === frameId)
      S.workbenchErrors.recoveryAction = publicText((error as Error).message, 240);
  } finally {
    if (S.currentId === frameId && S._recoveryActionLoading === actionId) {
      S._recoveryActionLoading = null;
      renderActionTimeline();
    }
  }
}

function recoveryTimelineCard(state: any, actionsState: any): HTMLElement {
  const hasActionsProjection = !!actionsState;
  state = state || {};
  actionsState = actionsState || sanitizeRecoveryActions({});
  const status = publicText(state.status || actionsState.state || "none", 32);
  const statusClass = String(status).toLowerCase().replace(/[^a-z0-9_-]+/g, "-");
  const card = el("article", "timeline-card kind-recovery status-" + statusClass);
  card.setAttribute("data-action-kind", "recovery");
  const head = el("div", "timeline-card-head"),
    kind = el("span", "timeline-kind");
  kind.appendChild(iconEl("refresh", 14));
  kind.appendChild(el("span", null, t("recovery.title")));
  head.appendChild(kind);
  head.appendChild(el("span", "timeline-status", status));
  card.appendChild(head);
  if (state.message) card.appendChild(el("div", "timeline-card-title", state.message));
  if (actionsState.checkpoint_id)
    card.appendChild(
      el(
        "div",
        "recovery-checkpoint",
        t("recovery.checkpoint", shortRuntime(actionsState.checkpoint_id)),
      ),
    );
  if (state.progress != null) {
    const track = el("div", "recovery-progress");
    const bar = el("span");
    bar.style.width = Math.round(state.progress * 100) + "%";
    track.appendChild(bar);
    card.appendChild(track);
  }
  const currentBranch = recoveryIsCurrentBranch(actionsState),
    list = el("div", "recovery-action-list");
  (actionsState.actions || []).forEach((action: any) => {
    const row = el("div", "recovery-action-row");
    const loading = S._recoveryActionLoading === action.id;
    const reason = !hasActionsProjection
      ? t("recovery.action.unavailable")
      : !currentBranch
        ? t("recovery.action.currentOnly")
        : action.reason || t("recovery.action.ready");
    row.appendChild(
      disabledWorkbenchButton(
        loading ? t("recovery.action.loading") : t("recovery.action." + action.id),
        !!(currentBranch && action.enabled && !S._recoveryActionLoading),
        () => executeRecoveryAction(action.id),
        reason,
      ),
    );
    row.appendChild(el("span", "recovery-action-reason", reason));
    list.appendChild(row);
  });
  card.appendChild(list);
  if (S.workbenchErrors.recoveryAction)
    card.appendChild(
      el("div", "timeline-error", t("recovery.action.failed", S.workbenchErrors.recoveryAction)),
    );
  (state.log || []).slice(-12).forEach((entry: any) => {
    const row = el("div", "recovery-log-row");
    row.appendChild(el("span", "timeline-pill", entry.status || "event"));
    row.appendChild(el("span", "recovery-log-message", entry.message || ""));
    card.appendChild(row);
  });
  return card;
}

function panelShell(title: string, className: string): HTMLElement {
  const panel = el("section", "workbench-panel " + className);
  panel.appendChild(el("div", "workbench-panel-title", title));
  return panel;
}

function branchCapability(name: string): boolean {
  return !!(S.branchState && S.branchState.capabilities && S.branchState.capabilities[name]);
}

function branchCapabilityReason(name: string): string {
  return publicText(
    S.branchState && S.branchState.capability_reasons && S.branchState.capability_reasons[name],
    200,
  );
}

function disabledWorkbenchButton(
  label: string,
  enabled: boolean,
  action: () => void,
  disabledReason?: string,
): HTMLButtonElement {
  const button = el("button", "outline-btn small", label) as HTMLButtonElement;
  button.disabled = !enabled;
  button.title = enabled ? label : disabledReason || t("nb.action.unavailable");
  if (enabled) button.onclick = action;
  return button;
}

async function createSessionCheckpoint(): Promise<void> {
  if (!S.currentId || !branchCapability("checkpoint") || S._branchActionLoading) return;
  const frameId = S.currentId;
  S._branchActionLoading = "checkpoint";
  delete S.workbenchErrors.branchAction;
  renderActionTimeline();
  try {
    await api(`/frames/${frameId}/branches/checkpoints`, {
      method: "POST",
      body: JSON.stringify({ branch_id: (S.branchState || {}).branch_id }),
    });
    await loadWorkbenchState(frameId, true);
  } catch (error) {
    if (S.currentId === frameId)
      S.workbenchErrors.branchAction = publicText((error as Error).message, 240);
  } finally {
    if (S.currentId === frameId) {
      S._branchActionLoading = null;
      renderActionTimeline();
    }
  }
}

async function forkSessionCheckpoint(checkpointId: string): Promise<void> {
  if (!S.currentId || !branchCapability("fork") || !checkpointId || S._branchActionLoading) return;
  checkpointId = publicText(checkpointId, 96);
  const requestedName = prompt(t("branch.forkName"), t("branch.forkDefault", shortRuntime(checkpointId)));
  if (requestedName === null) return;
  const name = publicText(String(requestedName).trim(), 120),
    frameId = S.currentId;
  S._branchActionLoading = "fork:" + checkpointId;
  delete S.workbenchErrors.branchAction;
  renderActionTimeline();
  try {
    const body: Record<string, string> = { from_checkpoint_id: checkpointId };
    if (name) body.name = name;
    await api(`/frames/${frameId}/branches/fork`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    await loadWorkbenchState(frameId, true);
    if (S.currentId === frameId) hint(t("branch.forked", shortRuntime(checkpointId)));
  } catch (error) {
    if (S.currentId === frameId)
      S.workbenchErrors.branchAction = publicText((error as Error).message, 240);
  } finally {
    if (S.currentId === frameId) {
      S._branchActionLoading = null;
      renderActionTimeline();
    }
  }
}

async function activateSessionBranch(branchId: string): Promise<void> {
  branchId = publicText(branchId, 96);
  if (!S.currentId || !branchId || !branchCapability("activate") || S._branchActionLoading) return;
  const frameId = S.currentId;
  S._branchActionLoading = "activate:" + branchId;
  delete S.workbenchErrors.branchAction;
  renderActionTimeline();
  try {
    const result = (await api(
      `/frames/${encodeURIComponent(frameId)}/branches/${encodeURIComponent(branchId)}/activate`,
      { method: "POST", body: "{}" },
    )) as { status?: string };
    invalidateKernelCache();
    S.cells = [];
    S.liveCells = [];
    S._liveCell = null;
    pendingReplIdentity.value = null;
    await Promise.resolve(laneCall("openConversation", frameId, S.project));
    if (S.currentId === frameId) {
      const partial = String((result && result.status) || "").toLowerCase() !== "active";
      hint(t(partial ? "branch.activatedPartial" : "branch.activated", shortRuntime(branchId)), partial);
    }
  } catch (error) {
    if (S.currentId === frameId)
      S.workbenchErrors.branchAction = publicText((error as Error).message, 240);
  } finally {
    if (S.currentId === frameId) {
      S._branchActionLoading = null;
      renderActionTimeline();
      laneCall("renderNotebook");
    }
  }
}

async function previewSessionRevert(checkpointId: string): Promise<void> {
  if (!S.currentId || !branchCapability("revert_preview") || S._branchActionLoading) return;
  const frameId = S.currentId;
  S._branchActionLoading = "preview:" + checkpointId;
  delete S.workbenchErrors.branchAction;
  renderActionTimeline();
  try {
    const preview = (await api(`/frames/${frameId}/branches/revert-preview`, {
      method: "POST",
      body: JSON.stringify({
        branch_id: (S.branchState || {}).branch_id,
        target_checkpoint_id: checkpointId,
      }),
    })) as { preview?: unknown };
    if (S.currentId === frameId && S.branchState)
      S.branchState.revert_preview = sanitizeRevertPreview(preview.preview || preview);
  } catch (error) {
    if (S.currentId === frameId)
      S.workbenchErrors.branchAction = publicText((error as Error).message, 240);
  } finally {
    if (S.currentId === frameId) {
      S._branchActionLoading = null;
      renderActionTimeline();
    }
  }
}

async function applySessionRevert(): Promise<void> {
  const preview = S.branchState && S.branchState.revert_preview;
  if (!preview || !preview.can_apply || !branchCapability("revert") || S._branchActionLoading)
    return;
  const frameId = S.currentId;
  S._branchActionLoading = "revert";
  delete S.workbenchErrors.branchAction;
  renderActionTimeline();
  try {
    const response = await api(`/frames/${frameId}/branches/revert`, {
      method: "POST",
      body: JSON.stringify({
        branch_id: preview.branch_id,
        target_checkpoint_id: preview.target_checkpoint_id,
      }),
    });
    const safe = sanitizeRevertMutationResult(response);
    const undo =
      safe.ok && safe.revert_checkpoint_id
        ? {
            branch_id: safe.branch_id || preview.branch_id,
            revert_checkpoint_id: safe.revert_checkpoint_id,
          }
        : null;
    await Promise.resolve(laneCall("openConversation", frameId, S.project));
    if (
      S.currentId === frameId &&
      undo &&
      undo.branch_id === (S.branchState || {}).branch_id
    ) {
      S.branchUndo = undo;
      renderActionTimeline();
    }
  } catch (error) {
    if (S.currentId === frameId)
      S.workbenchErrors.branchAction = publicText((error as Error).message, 240);
  } finally {
    if (S.currentId === frameId) {
      S._branchActionLoading = null;
      renderActionTimeline();
    }
  }
}

async function undoSessionRevert(): Promise<void> {
  const undo = S.branchUndo;
  if (
    !S.currentId ||
    !undo ||
    undo.branch_id !== (S.branchState || {}).branch_id ||
    !undo.revert_checkpoint_id ||
    S._branchActionLoading
  )
    return;
  const frameId = S.currentId;
  S._branchActionLoading = "undo";
  delete S.workbenchErrors.branchAction;
  renderActionTimeline();
  try {
    await api(`/frames/${frameId}/revert/undo`, {
      method: "POST",
      body: JSON.stringify({
        branch_id: undo.branch_id,
        revert_checkpoint_id: undo.revert_checkpoint_id,
      }),
    });
    S.branchUndo = null;
    await Promise.resolve(laneCall("openConversation", frameId, S.project));
    if (S.currentId === frameId) hint(t("branch.undone"));
  } catch (error) {
    if (S.currentId === frameId)
      S.workbenchErrors.branchAction = publicText((error as Error).message, 240);
  } finally {
    if (S.currentId === frameId) {
      S._branchActionLoading = null;
      renderActionTimeline();
    }
  }
}

export function renderBranchPanel(): HTMLElement {
  const panel = panelShell(t("timeline.panel.branches"), "branch-panel"),
    state = S.branchState;
  const busy = !!S._branchActionLoading,
    controls = el("div", "workbench-controls");
  controls.appendChild(
    disabledWorkbenchButton(
      S._branchActionLoading === "checkpoint" ? t("common.loading") : t("branch.checkpoint"),
      !!(branchCapability("checkpoint") && !busy),
      () => {
        void createSessionCheckpoint();
      },
      branchCapabilityReason("checkpoint"),
    ),
  );
  if (S.branchUndo && S.branchUndo.branch_id === (state || {}).branch_id)
    controls.appendChild(
      disabledWorkbenchButton(
        S._branchActionLoading === "undo" ? t("common.loading") : t("branch.undo"),
        !busy,
        () => {
          void undoSessionRevert();
        },
      ),
    );
  panel.appendChild(controls);
  if (state && state.branch_id)
    panel.appendChild(
      el("div", "branch-current-summary", t("branch.currentSummary", shortRuntime(state.branch_id))),
    );
  if (S.workbenchErrors.branchAction)
    panel.appendChild(
      el("div", "timeline-error", t("branch.actionFailed", S.workbenchErrors.branchAction)),
    );
  if (!state || !(state.branches || []).length) {
    panel.appendChild(el("div", "workbench-empty", t("timeline.noBranch")));
    return panel;
  }
  (state.branches || []).forEach((branch: any) => {
    const row = el("div", "branch-row" + (branch.branch_id === state.branch_id ? " current" : ""));
    const head = el("div", "branch-head");
    head.appendChild(el("span", "branch-name", branch.name || shortRuntime(branch.branch_id)));
    if (branch.branch_id === state.branch_id)
      head.appendChild(el("span", "timeline-pill", t("branch.current")));
    else {
      head.appendChild(el("span", "timeline-pill", t("branch.viewOnly")));
      head.appendChild(
        disabledWorkbenchButton(
          S._branchActionLoading === "activate:" + branch.branch_id
            ? t("branch.activating")
            : t("branch.activate"),
          !!(branch.activatable && branchCapability("activate") && !busy),
          () => activateSessionBranch(branch.branch_id),
          branchCapabilityReason("activate"),
        ),
      );
    }
    if (branch.head_checkpoint_id)
      head.appendChild(
        el("span", "branch-head-id", t("branch.head", shortRuntime(branch.head_checkpoint_id))),
      );
    row.appendChild(head);
    const cps = el("div", "checkpoint-list"),
      allCheckpoints = branch.checkpoints || [];
    const checkpointRow = (cp: any) => {
      const cpRow = el("div", "checkpoint-row");
      cpRow.appendChild(el("span", "checkpoint-id", shortRuntime(cp.checkpoint_id)));
      cpRow.appendChild(el("span", "checkpoint-reason", cp.reason || "checkpoint"));
      const actions = el("span", "checkpoint-actions");
      actions.appendChild(
        disabledWorkbenchButton(
          S._branchActionLoading === "fork:" + cp.checkpoint_id
            ? t("common.loading")
            : t("branch.fork"),
          !!(branchCapability("fork") && !busy),
          () => forkSessionCheckpoint(cp.checkpoint_id),
          branchCapabilityReason("fork"),
        ),
      );
      actions.appendChild(
        disabledWorkbenchButton(
          S._branchActionLoading === "preview:" + cp.checkpoint_id
            ? t("common.loading")
            : t("branch.preview"),
          !!(branchCapability("revert_preview") && !busy),
          () => previewSessionRevert(cp.checkpoint_id),
          branchCapabilityReason("revert_preview"),
        ),
      );
      cpRow.appendChild(actions);
      return cpRow;
    };
    allCheckpoints
      .filter((cp: any) => !cp.internal)
      .slice(0, 8)
      .forEach((cp: any) => cps.appendChild(checkpointRow(cp)));
    const internalCheckpoints = allCheckpoints.filter((cp: any) => cp.internal);
    if (internalCheckpoints.length) {
      const collapsed = el("details", "internal-checkpoints");
      collapsed.appendChild(
        el("summary", null, t("branch.internalCheckpoints", internalCheckpoints.length)),
      );
      const internalList = el("div", "checkpoint-list");
      internalCheckpoints
        .slice(0, 20)
        .forEach((cp: any) => internalList.appendChild(checkpointRow(cp)));
      collapsed.appendChild(internalList);
      cps.appendChild(collapsed);
    }
    row.appendChild(cps);
    panel.appendChild(row);
  });
  const preview = state.revert_preview;
  if (preview) {
    const box = el("div", "revert-preview");
    box.appendChild(
      el(
        "div",
        "revert-preview-title",
        t("branch.previewTitle") + " · " + shortRuntime(preview.target_checkpoint_id),
      ),
    );
    const arts = preview.artifacts || {},
      ws = preview.workspace || {};
    box.appendChild(
      el(
        "div",
        "revert-diff",
        t(
          "branch.diff",
          (preview.messages || {}).delta || 0,
          (preview.notebook || {}).delta || 0,
          ws.writes_count || 0,
          ws.deletes_count || 0,
          arts.added_count || 0,
          arts.removed_count || 0,
        ),
      ),
    );
    if (ws.conflicts_count) box.appendChild(el("div", "timeline-error", t("branch.conflict")));
    box.appendChild(
      disabledWorkbenchButton(
        S._branchActionLoading === "revert" ? t("common.loading") : t("branch.revert"),
        !!(preview.can_apply && branchCapability("revert") && !busy),
        () => {
          void applySessionRevert();
        },
        branchCapabilityReason("revert"),
      ),
    );
    panel.appendChild(box);
  }
  return panel;
}

export function renderContextPanel(): HTMLElement {
  const panel = panelShell(t("timeline.panel.context"), "context-panel"),
    state = S.contextState;
  if (!state || !(state.layers || []).length) {
    panel.appendChild(el("div", "workbench-empty", t("timeline.noContext")));
    return panel;
  }
  const summary = el("div", "context-summary");
  if (state.token_count != null)
    summary.appendChild(
      el(
        "span",
        "timeline-pill",
        t("context.tokens", state.token_count) +
          (state.token_limit ? " / " + state.token_limit : ""),
      ),
    );
  if (state.output_reserve)
    summary.appendChild(
      el("span", "timeline-pill", t("context.outputReserve", state.output_reserve)),
    );
  if (state.message_count != null)
    summary.appendChild(el("span", "timeline-pill", t("context.messages", state.message_count)));
  if (state.compressed) summary.appendChild(el("span", "timeline-pill", t("context.compressed")));
  if (state.handoff) summary.appendChild(el("span", "timeline-pill", t("context.handoff")));
  panel.appendChild(summary);
  state.layers.forEach((layer: any) => {
    const row = el("div", "context-layer");
    row.appendChild(el("span", "context-layer-name", layer.name || layer.kind || "context"));
    if (layer.token_count != null)
      row.appendChild(el("span", "context-layer-tokens", t("context.tokens", layer.token_count)));
    if (layer.status) row.appendChild(el("span", "timeline-pill", layer.status));
    panel.appendChild(row);
  });
  (state.omitted || []).forEach((item: any) => {
    const row = el("div", "context-layer context-omitted");
    row.appendChild(
      el("span", "context-layer-name", tOptional("context.omitted." + item.kind) || item.kind),
    );
    row.appendChild(el("span", "context-layer-tokens", t("context.omittedCount", item.count)));
    (item.reasons || []).forEach((r: any) =>
      row.appendChild(
        el(
          "span",
          "timeline-pill",
          (tOptional("context.reason." + r.reason) || r.reason) + " ×" + r.count,
        ),
      ),
    );
    panel.appendChild(row);
  });
  if ((state.compaction_history || []).length) {
    const history = el("details", "context-history");
    history.appendChild(
      el(
        "summary",
        null,
        t("context.history", state.compaction_count || state.compaction_history.length),
      ),
    );
    state.compaction_history.forEach((item: any) => {
      const row = el("div", "context-history-row");
      row.appendChild(
        el("span", "context-history-id", shortRuntime(item.archive_id) || t("context.compaction")),
      );
      row.appendChild(
        el(
          "span",
          "context-layer-tokens",
          t("context.savings", item.tokens_before, item.tokens_after),
        ),
      );
      if (item.message_count)
        row.appendChild(el("span", "timeline-pill", t("context.messages", item.message_count)));
      if (item.artifact_count)
        row.appendChild(el("span", "timeline-pill", t("context.artifacts", item.artifact_count)));
      history.appendChild(row);
    });
    panel.appendChild(history);
  }
  return panel;
}

export function renderSecurityPanel(): HTMLElement {
  const panel = panelShell(t("timeline.panel.security"), "security-panel"),
    state = S.securityState;
  if (!state) {
    panel.appendChild(el("div", "workbench-empty", t("timeline.noSecurity")));
    return panel;
  }
  const sandbox = state.sandbox || {},
    permission = state.permission || {};
  const row = (label: string, values: unknown[], stateClass?: string) => {
    const line = el("div", "security-row " + (stateClass || ""));
    line.appendChild(el("span", "security-label", label));
    values
      .filter(Boolean)
      .forEach((value) => line.appendChild(el("span", "timeline-pill", String(value))));
    panel.appendChild(line);
  };
  row(
    t("security.sandbox"),
    [sandbox.state || sandbox.mode || "unknown", sandbox.backend, sandbox.enforced ? "enforced" : "not enforced"],
    sandbox.enforced ? "ok" : "warn",
  );
  row(
    t("security.selfTest"),
    [sandbox.self_test_passed ? "passed" : "not passed"],
    sandbox.self_test_passed ? "ok" : "warn",
  );
  row(t("security.network"), [sandbox.network_policy || "unknown"]);
  (sandbox.runtimes || [])
    .filter((runtime: any) => runtime.generation_ended)
    .forEach((runtime: any) => {
      row(t("security.generation"), [
        t(
          "security.generationEnded",
          runtime.language || "kernel",
          runtime.generation_ended_reason || runtime.generation_state || "ended",
        ),
      ]);
    });
  row(t("security.permission"), [
    permission.mode || "unknown",
    permission.pending_count ? t("security.pending", permission.pending_count) : "",
  ]);
  if (sandbox.detail) panel.appendChild(el("div", "security-detail", sandbox.detail));
  return panel;
}

async function refreshComputeTask(jobId: string, button: HTMLButtonElement): Promise<void> {
  const id = S.currentId;
  if (!id || !jobId) return;
  button.disabled = true;
  try {
    await api(`/frames/${id}/compute/tasks/${encodeURIComponent(jobId)}/refresh`, {
      method: "POST",
      body: "{}",
    });
    await loadWorkbenchState(id, true);
  } catch (e) {
    hint(t("compute.refreshFailed") + " — " + apiErrorText(e), true);
  } finally {
    button.disabled = false;
  }
}

const computeCancelInFlight = new Set<string>();

const COMPUTE_MAY_STILL_BILL =
  "The remote job may still be running and billing.";

async function cancelComputeTask(jobId: string, button: HTMLButtonElement): Promise<void> {
  const id = S.currentId;
  if (!id || !jobId) return;
  if (computeCancelInFlight.has(jobId)) return;
  const promptText =
    tOptional("compute.cancelConfirm") ||
    "Cancel this remote job? If the remote does not confirm, it may still be running and billing.";
  if (typeof globalThis.confirm === "function" && !globalThis.confirm(promptText)) return;
  computeCancelInFlight.add(jobId);
  button.disabled = true;
  try {
    const result = (await api(
      `/frames/${id}/compute/tasks/${encodeURIComponent(jobId)}/cancel`,
      { method: "POST", body: JSON.stringify({ confirm: true }) },
    )) as {
      outcome?: string;
      hint?: string;
      task?: { status?: string };
    };
    // `already_terminal` arrives as HTTP 409, which `api()` throws; it is
    // handled in the catch below, not here.
    if (result && result.outcome === "cancel_indeterminate") {
      hint(result.hint || COMPUTE_MAY_STILL_BILL, true);
    }
    await loadWorkbenchState(id, true);
  } catch (e) {
    const body = (e as { body?: { outcome?: string; status?: string; task?: { status?: string } } })
      .body;
    if (body && body.outcome === "already_terminal") {
      hint(
        "The job already ended as " +
          String((body.task && body.task.status) || body.status || "terminal") +
          ".",
      );
      await loadWorkbenchState(id, true);
    } else {
      hint(
        (tOptional("compute.cancelFailed") || "Cancel failed") + " — " + apiErrorText(e),
        true,
      );
    }
  } finally {
    computeCancelInFlight.delete(jobId);
    button.disabled = false;
  }
}

export function renderComputeTasksPanel(): HTMLElement {
  const panel = panelShell(t("timeline.panel.compute"), "compute-panel"),
    state = S.computeTasks;
  if (!state || !(state.tasks || []).length) {
    panel.appendChild(el("div", "workbench-empty", t("compute.none")));
    return panel;
  }
  const summary = el("div", "compute-summary");
  summary.appendChild(el("span", "timeline-pill", t("compute.live", state.live_count)));
  summary.appendChild(
    el("span", "timeline-pill", state.polled ? t("compute.checked") : t("compute.fromRecord")),
  );
  panel.appendChild(summary);
  state.tasks.forEach((task: any) => {
    const row = el("div", "compute-task status-" + String(task.status).toLowerCase());
    const head = el("div", "compute-task-head");
    head.appendChild(el("span", "compute-task-id", shortRuntime(task.job_id) || task.job_id));
    head.appendChild(
      el(
        "span",
        "timeline-status " + String(task.status).toLowerCase(),
        tOptional("compute.status." + task.status) || task.status,
      ),
    );
    if (task.provider) head.appendChild(el("span", "timeline-pill", task.provider));
    row.appendChild(head);
    if (task.outputs.file_count)
      row.appendChild(
        el(
          "div",
          "compute-task-outputs",
          t("compute.outputs", task.outputs.file_count, bytes(task.outputs.total_bytes)),
        ),
      );
    if (task.reason) row.appendChild(el("div", "compute-task-message", task.reason));
    if (task.live) {
      const refresh = ghostIconBtn("refresh", t("compute.refresh"));
      refresh.onclick = () => refreshComputeTask(task.job_id, refresh);
      row.appendChild(refresh);
      const cancel = ghostIconBtn("stop", tOptional("compute.cancel") || "Cancel remote job");
      cancel.className += " compute-task-cancel";
      cancel.onclick = () => cancelComputeTask(task.job_id, cancel);
      row.appendChild(cancel);
    }
    panel.appendChild(row);
  });
  return panel;
}

async function stopDelegationChild(childId: string, button: HTMLButtonElement): Promise<void> {
  const id = S.currentId;
  if (!id || !childId) return;
  button.disabled = true;
  try {
    await api(`/frames/${id}/delegations/${encodeURIComponent(childId)}/stop`, {
      method: "POST",
      body: "{}",
    });
    await loadWorkbenchState(id, true);
  } catch (e) {
    hint(t("delegation.stopFailed") + " — " + apiErrorText(e), true);
    await loadWorkbenchState(id, true);
  } finally {
    button.disabled = false;
  }
}

export async function steerDelegationChild(
  childId: string,
  button: HTMLButtonElement,
): Promise<void> {
  const id = S.currentId;
  if (!id || !childId) return;
  const message = prompt(t("delegation.steerPrompt"));
  if (!message || !message.trim()) return;
  button.disabled = true;
  try {
    await api(`/frames/${id}/delegations/${encodeURIComponent(childId)}/steer`, {
      method: "POST",
      body: JSON.stringify({ message }),
    });
    hint(t("delegation.steerQueued"));
    await loadWorkbenchState(id, true);
  } catch (e) {
    hint(t("delegation.steerFailed") + " — " + apiErrorText(e), true);
    await loadWorkbenchState(id, true);
  } finally {
    button.disabled = false;
  }
}

function delegateTaskChip(view: any): HTMLElement {
  const ts = view && view.task_status;
  let cls = "neutral",
    key: string | null = null;
  if (ts === "completed") {
    cls = "completed";
    key = "step.delegate.status.completed";
  } else if (ts === "partial") {
    cls = "warning";
    key = "step.delegate.status.partial";
  } else if (ts === "blocked") {
    cls = "warning";
    key = "step.delegate.status.blocked";
  } else if (ts === "failed") {
    cls = "failed";
    key = "step.delegate.status.failed";
  } else if (view && ["stopped", "cancelled"].includes(view.stop_reason)) {
    cls = "warning";
    key = "step.delegate.status.stopped";
  } else if (view && ["pending", "running"].includes(view.status)) {
    key = "step.delegate.status.pending";
  }
  return el(
    "span",
    "dlg-chip " + cls,
    key ? t(key) : publicText(ts || (view && view.status) || "?", 32),
  );
}

export function renderDelegationPanel(): HTMLElement {
  const panel = panelShell(t("timeline.panel.delegation"), "delegation-panel"),
    state = S.delegationState;
  if (!state || !(state.children || []).length) {
    panel.appendChild(el("div", "workbench-empty", t("timeline.noDelegation")));
    return panel;
  }
  const summary = el("div", "delegation-summary"),
    budget = state.budget || {};
  if (state.budget)
    summary.appendChild(
      el("span", "timeline-pill", t("delegation.budget", budget.spawned || 0, budget.limit || 0)),
    );
  summary.appendChild(
    el(
      "span",
      "timeline-pill",
      t("delegation.active", budget.active || (state.stats || {}).running || 0),
    ),
  );
  panel.appendChild(summary);
  (state.children || []).forEach((child: any) => {
    const row = el("div", "delegation-child status-" + String(child.status || "unknown").toLowerCase());
    row.style.setProperty("--delegation-indent", Math.min(child.depth || 0, 4) * 10 + "px");
    const head = el("div", "delegation-child-head");
    head.appendChild(
      el("span", "delegation-child-name", child.name || shortRuntime(child.child_id)),
    );
    if (child.task_status) head.appendChild(delegateTaskChip(child));
    head.appendChild(
      el(
        "span",
        "timeline-status " + String(child.status || "unknown").toLowerCase(),
        child.status || "unknown",
      ),
    );
    row.appendChild(head);
    const details = el("div", "delegation-child-details");
    if (child.progress && child.progress.max_turns)
      details.appendChild(
        el(
          "span",
          "timeline-pill",
          t("delegation.turns", child.progress.turn_boundary || 0, child.progress.max_turns),
        ),
      );
    if (child.overrides && child.overrides.model)
      details.appendChild(el("span", "timeline-pill", child.overrides.model));
    if (child.overrides && child.overrides.steps)
      details.appendChild(el("span", "timeline-pill", "steps " + child.overrides.steps));
    if (child.steering && (child.steering.queued || child.steering.delivered))
      details.appendChild(
        el(
          "span",
          "timeline-pill",
          t("delegation.steering", child.steering.queued || 0, child.steering.delivered || 0),
        ),
      );
    if (child.frame_id) {
      const ref = el(
        "span",
        "timeline-pill dlg-frame-ref",
        t("delegation.childFrame", shortRuntime(child.frame_id)),
      );
      ref.title = child.frame_id;
      details.appendChild(ref);
    }
    row.appendChild(details);
    if (child.error || child.stop_reason)
      row.appendChild(el("div", "delegation-child-message", child.error || child.stop_reason));
    if (["running", "pending"].includes(String(child.status || "").toLowerCase())) {
      const controls = el("div", "delegation-child-controls");
      const stop = ghostIconBtn("stop", t("delegation.stop"));
      stop.onclick = () => stopDelegationChild(child.child_id, stop);
      controls.appendChild(stop);
      const steer = ghostIconBtn("message-square", t("delegation.steer"));
      steer.onclick = () => steerDelegationChild(child.child_id, steer);
      controls.appendChild(steer);
      row.appendChild(controls);
    }
    panel.appendChild(row);
  });
  return panel;
}

export function syncActionTimelineHistoryState(host: HTMLElement | null = null): void {
  if (S._timelineView) syncActionTimelineOverviewControls(S._timelineView);
  const root = $("#dock-timeline");
  const target = host || (root && root.querySelector(".timeline-history-state"));
  if (!target) return;
  const previousHeight = (target as HTMLElement).getBoundingClientRect().height;
  const timeline = S.actionTimeline || {};
  (target as HTMLElement).replaceChildren();
  (target as HTMLElement).style.minHeight = "";
  if (timeline.has_more_before) {
    const controls = el("div", "workbench-controls timeline-history-controls");
    const loading = actionTimelineHistoryIsLoading(timeline);
    const earlier = el(
      "button",
      "outline-btn small",
      t(loading ? "timeline.loadingEarlier" : "timeline.loadEarlier"),
    ) as HTMLButtonElement;
    earlier.disabled = loading;
    earlier.setAttribute("data-action", "load-earlier-timeline");
    earlier.setAttribute("aria-busy", loading ? "true" : "false");
    earlier.onclick = () => {
      void loadEarlierActionTimeline();
    };
    controls.appendChild(earlier);
    target.appendChild(controls);
  }
  if (S.workbenchErrors.timelineHistory)
    target.appendChild(
      el(
        "div",
        "timeline-error",
        t("timeline.loadEarlierFailed", S.workbenchErrors.timelineHistory),
      ),
    );
  const preserveEmptyHeight = !target.children.length && previousHeight > 0 && !!S._timelineView;
  if (preserveEmptyHeight) (target as HTMLElement).style.minHeight = previousHeight + "px";
  target.classList.toggle("hidden", !target.children.length && !preserveEmptyHeight);
}

export function renderActionTimeline(): void {
  const root = $("#dock-timeline");
  if (!root) return;
  const timeline = S.actionTimeline || {};
  const groups = sortedActionTimelineGroups(timeline);
  const rootFrameScope = actionTimelineRootScope(timeline),
    branchScope = actionTimelineBranchScope(timeline, groups);
  const previousView = S._timelineView;
  const activeEl = document.activeElement as HTMLElement | null;
  const activeRow =
    activeEl && activeEl.closest ? activeEl.closest(".timeline-ledger-row") : null;
  const restoreInspectorFocus = !!(
    previousView && previousView.inspectorHost.contains(document.activeElement)
  );
  const restoreSearchFocus = !!(
    previousView &&
    previousView.search &&
    previousView.search.shell.contains(document.activeElement)
  );
  const searchSelection = restoreSearchFocus
    ? [previousView.search.input.selectionStart, previousView.search.input.selectionEnd]
    : null;
  if (activeRow && actionTimelineViewMatches(previousView, rootFrameScope, branchScope)) {
    if ((activeRow as HTMLElement).dataset.groupId)
      S._timelineRestoreFocusGroupId = (activeRow as HTMLElement).dataset.groupId;
    else previousView.restoreFocusTurnId = (activeRow as HTMLElement).dataset.turnId || null;
  }
  if (actionTimelineViewMatches(previousView, rootFrameScope, branchScope)) {
    if (previousView.scroll.clientHeight > 0) {
      previousView.scrollTop = previousView.scroll.scrollTop;
      previousView.scrollLeft = previousView.scroll.scrollLeft;
    }
  } else {
    destroyActionTimelineView(previousView);
    S._timelineRestoreFocusGroupId = null;
  }
  if (
    S.actionTimelineSelectedBranchId !== branchScope ||
    !groups.some((group) => group.group_id === S.actionTimelineSelectedGroupId)
  ) {
    S.actionTimelineSelectedGroupId = null;
    S.actionTimelineSelectedBranchId = null;
  }
  root.replaceChildren();
  root.dataset.timelineBranch = branchScope;
  const top = el("div", "timeline-top");
  const heading = el("div");
  heading.appendChild(el("div", "timeline-title", t("timeline.title")));
  heading.appendChild(el("div", "timeline-subtitle", t("timeline.subtitle")));
  top.appendChild(heading);
  const refresh = ghostIconBtn("refresh", t("timeline.refresh"));
  refresh.onclick = () => {
    void loadWorkbenchState(S.currentId, true);
  };
  top.appendChild(refresh);
  root.appendChild(top);
  root.appendChild(runtimeSummaryNode(false));
  const layout = el("div", "workbench-layout"),
    side = el("div", "workbench-side"),
    actions = el("section", "timeline-actions");
  side.appendChild(renderBranchPanel());
  side.appendChild(renderDelegationPanel());
  side.appendChild(renderComputeTasksPanel());
  side.appendChild(renderContextPanel());
  side.appendChild(renderSecurityPanel());
  layout.appendChild(side);
  const historyState = el("div", "timeline-history-state hidden");
  actions.appendChild(historyState);
  syncActionTimelineHistoryState(historyState);
  const hasRecovery = !!(
    S.recoveryActions ||
    (S.recoveryState && (S.recoveryState.status || (S.recoveryState.log || []).length))
  );
  if (hasRecovery) actions.appendChild(recoveryTimelineCard(S.recoveryState, S.recoveryActions));
  if (groups.length) actions.appendChild(actionTimelineLedger(groups, branchScope, rootFrameScope));
  else {
    destroyActionTimelineView();
    if (!timeline.has_more_before && !S.workbenchErrors.timelineHistory && !hasRecovery) {
      actions.appendChild(
        el(
          "div",
          "workbench-empty timeline-empty",
          S._workbenchLoading ? t("timeline.loading") : t("timeline.empty"),
        ),
      );
    }
  }
  layout.appendChild(actions);
  root.appendChild(layout);
  if (groups.length) updateActionTimelineLedger({ direction: "render" });
  else S._timelineRestoreFocusGroupId = null;
  if (restoreInspectorFocus && S._timelineView) {
    const close = S._timelineView.inspectorHost.querySelector("button");
    if (close) {
      try {
        close.focus({ preventScroll: true });
      } catch {
        close.focus();
      }
    }
  } else if (restoreSearchFocus && S._timelineView) {
    const input = S._timelineView.search.input;
    try {
      input.focus({ preventScroll: true });
    } catch {
      input.focus();
    }
    if (searchSelection && searchSelection[0] != null)
      try {
        input.setSelectionRange(searchSelection[0], searchSelection[1]);
      } catch {
        /* ignore */
      }
  }
}
