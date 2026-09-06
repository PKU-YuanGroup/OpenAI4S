/**
 * Kernel cache, chips-side fetch, stop/start/restart, env switch, REPL execute.
 *
 * `_kc` lives in the F-05 notebook store. Invalidate timings (verbatim):
 * kernel_status, turnDone, nbSwitchEnv (app.js:5352, 5854, 10060).
 */

import { signal } from "@preact/signals";
import { isReady } from "../../compat/stub";
import {
  _kc,
  _replDrafts,
  _replLanguage,
  artifactWorkbench,
  pendingReplIdentity,
} from "../../stores/notebook";
import { currentId } from "../../stores/session";
import { running } from "../../stores/stream";
import {
  actionTimeline,
  branchState,
  executionQueue,
  recoveryActions,
  recoveryState,
  securityState,
} from "../../stores/timeline";
import { activeTab, dock } from "../../stores/ui";
import { t } from "../../i18n/runtime";
import { publicText } from "../scrub/scrub";
import type { WsMessage } from "../ws/types";
import {
  loadExecutionLog,
  nbCellKey,
  notebookDisplayEntries,
  notebookFetch,
  notifyLoadArtifacts,
} from "./cells";
import { kernelIdFromEnv, kernelLabel } from "./labels";
import { nbRender } from "./scroll";
import type { KernelEnvRow, KernelStatus, NotebookCell } from "./types";

export { kernelIdFromEnv, kernelLabel } from "./labels";

/** Local paint epoch so Preact headers see in-place `_kc` mutations. */
export const kernelEpoch = signal(0);

function bumpKernelEpoch(): void {
  kernelEpoch.value++;
}

function dockIsNotebook(): boolean {
  const d = dock.value as { open?: boolean } | null;
  return !!(d && d.open && activeTab.value === "notebook");
}

function hint(msg: string, err?: boolean): void {
  const fn = (globalThis as unknown as { hint?: unknown }).hint;
  if (isReady(fn)) (fn as (m: string, e?: boolean) => void)(msg, err);
}

function apiErrorText(e: unknown): string {
  const err = e as { message?: string; requestId?: string } | null;
  const msg = err && err.message ? String(err.message) : String(e);
  return err && err.requestId ? `${msg} [${err.requestId}]` : msg;
}

/** app.js:9955. Does not reset stBusy / envBusy. */
export function invalidateKernelCache(): void {
  const kc = _kc.value;
  kc.id = null;
  kc.st = null;
  kc.stAt = 0;
  kc.envs = null;
  kc.cur = null;
  kc.envAt = 0;
  bumpKernelEpoch();
}

/**
 * Notebook slice of turnDone (app.js:5854). F-11 must call this from turnDone;
 * this lane must not register a second `frame_update` handler.
 */
export function notebookOnTurnDone(): void {
  invalidateKernelCache();
  if (dockIsNotebook()) nbRender();
}

export function kernelStatusOf(value: unknown): KernelStatus {
  return value && typeof value === "object" ? (value as KernelStatus) : {};
}

export function replEnabledNow(): boolean {
  const st = kernelStatusOf(_kc.value.st);
  return !!(st.repl_enabled && !(st.view_only && st.trust_state === "quarantined"));
}

type Identity = { execution_id: string; owner: { kind: string; id: string } };

/** app.js:3006-3009, enough for REPL busy + owner chips. */
export function identityForOwner(queue: unknown, ownerKind: string | null): Identity | null {
  const safe = (queue && typeof queue === "object" ? queue : {}) as {
    owner?: { execution_id?: string; owner?: { kind?: string; id?: string } };
    queue?: Array<{ execution_id?: string; owner?: { kind?: string; id?: string } }>;
  };
  const candidates = [safe.owner].concat(safe.queue || []).filter(Boolean);
  const ticket = ownerKind
    ? candidates.find((item) => item && item.owner && item.owner.kind === ownerKind)
    : safe.owner;
  return ticket &&
    ticket.execution_id &&
    ticket.owner &&
    ticket.owner.kind &&
    ticket.owner.id
    ? {
        execution_id: ticket.execution_id,
        owner: { kind: ticket.owner.kind, id: ticket.owner.id },
      }
    : null;
}

function latestCellForLanguage(language: string): NotebookCell | null {
  const list = notebookDisplayEntries().filter((cell) =>
    String(cell.language || cell.kernel_id || "python")
      .toLowerCase()
      .startsWith(language),
  );
  return list[list.length - 1] || null;
}

function shortRuntime(value: unknown): string {
  const text = publicText(value, 96);
  return text ? (text.length > 12 ? text.slice(0, 8) + "…" : text) : t("runtime.none");
}

/** app.js:3398-3424. Compact port for the notebook badge. */
export function runtimeSummary(): {
  status: string;
  branch: string;
  python: string;
  r: string;
  viewOnly: boolean;
  trustState: string;
  revision: number | null;
  owner: string;
  ownerId: string;
  queue: number;
} {
  const queue = (executionQueue.value || {}) as {
    owner?: { owner?: { kind?: string; id?: string }; owner_kind?: string; owner_id?: string };
    queued_count?: number;
    queue?: unknown[];
  };
  const ownerTicket = queue.owner || null;
  const owner = (ownerTicket && ownerTicket.owner) || {};
  const recovery = (recoveryState.value || {}) as Record<string, unknown>;
  const actions = (recoveryActions.value || {}) as Record<string, unknown>;
  const kcSt = kernelStatusOf(_kc.value.st);
  const recoveryStatus = String(recovery.status || "").toLowerCase();
  const trustState = publicText(
    recovery.trust_state || actions.trust_state || kcSt.trust_state,
    32,
  );
  const explicitRecoveryRequired =
    recovery.explicit_recovery_required === true ||
    actions.explicit_recovery_required === true ||
    kcSt.explicit_recovery_required === true;
  const viewOnly =
    explicitRecoveryRequired ||
    recovery.view_only === true ||
    actions.view_only === true ||
    kcSt.view_only === true;
  let status = "ended";
  if (/fail|error/.test(recoveryStatus)) status = "failed";
  else if (/partial/.test(recoveryStatus)) status = "partial";
  else if (/restor|recover|bootstrap|validat/.test(recoveryStatus)) status = "restoring";
  else if (ownerTicket || running.value || kcSt.turn_running) status = "busy";
  else if (kcSt.alive) status = "live";
  const pythonCell = latestCellForLanguage("python");
  const rCell = latestCellForLanguage("r");
  const branchObj = branchState.value as { branch_id?: string } | null;
  const timeline = actionTimeline.value as { branch_id?: string } | null;
  const branch =
    (branchObj && branchObj.branch_id) ||
    (timeline && timeline.branch_id) ||
    (recovery && recovery.branch_id) ||
    currentId.value;
  const stateRevision =
    recovery.state_revision != null
      ? Number(recovery.state_revision)
      : Math.max(
          0,
          ...notebookDisplayEntries().map((cell) => Number(cell.state_revision) || 0),
        );
  const pyGeneration =
    recovery.python_generation_id ||
    kcSt.python_generation_id ||
    kcSt.generation_id ||
    (pythonCell && pythonCell.generation_id);
  const rGeneration = recovery.r_generation_id || (rCell && rCell.generation_id);
  return {
    status,
    branch: publicText(branch, 96),
    python: publicText(pyGeneration, 96),
    r: publicText(rGeneration, 96),
    viewOnly: !!viewOnly,
    trustState,
    revision: stateRevision || null,
    owner: publicText(owner.kind || (ownerTicket && ownerTicket.owner_kind), 48),
    ownerId: publicText(owner.id || (ownerTicket && ownerTicket.owner_id), 96),
    queue: Number(queue.queued_count || (queue.queue || []).length || 0),
  };
}

export { shortRuntime };

export function branchCapability(name: string): boolean {
  const st = branchState.value as { capabilities?: Record<string, unknown> } | null;
  return !!(st && st.capabilities && st.capabilities[name]);
}

/** app.js:9911-9918 */
export async function kernelCtl(action: string): Promise<void> {
  if (!currentId.value) return;
  if (action === "restart" && !globalThis.confirm(t("nb.kernel.restartConfirm"))) return;
  if (action === "stop" && !globalThis.confirm(t("nb.kernel.stopConfirm"))) return;
  try {
    await notebookFetch(`/frames/${currentId.value}/kernel/${action}`, { method: "POST" });
  } catch (e) {
    hint(t("nb.kernel.opFailed", apiErrorText(e)), true);
  }
  invalidateKernelCache();
  if (dockIsNotebook()) nbRender();
}

type ReplControls = {
  runButton?: { disabled: boolean };
  input?: { disabled: boolean };
  stop?: { classList: { remove: (c: string) => void; add: (c: string) => void } };
};

/** app.js:9920-9947 */
export async function executeNotebookCode(
  code: string,
  language: string,
  controls?: ReplControls,
): Promise<boolean> {
  code = String(code || "");
  language = String(language || "python").toLowerCase() === "r" ? "r" : "python";
  if (!code.trim() || !currentId.value) return false;
  const runButton = controls && controls.runButton;
  const input = controls && controls.input;
  const stop = controls && controls.stop;
  const cryptoObj = globalThis.crypto;
  const randomId =
    cryptoObj && typeof cryptoObj.randomUUID === "function"
      ? cryptoObj.randomUUID()
      : Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
  const executionId = "repl-" + randomId;
  const frameId = currentId.value;
  pendingReplIdentity.value = {
    frame_id: frameId,
    execution_id: executionId,
    owner: { kind: "user_repl", id: executionId },
  };
  if (runButton) runButton.disabled = true;
  if (input) input.disabled = true;
  if (stop) stop.classList.remove("hidden");
  let accepted = false;
  try {
    const response = await notebookFetch(`/frames/${frameId}/kernel/execute`, {
      method: "POST",
      body: JSON.stringify({ code, language, execution_id: executionId }),
    });
    accepted = !!(response && response.status === "accepted");
    const pending = pendingReplIdentity.value as {
      execution_id?: string;
      owner?: { kind?: string; id?: string };
    } | null;
    if (accepted && pending && pending.execution_id === executionId) {
      const owner = response && (response.owner as { kind?: string; id?: string } | undefined);
      pending.owner =
        owner && owner.kind && owner.id ? owner : pending.owner;
    }
    hint(t("nb.action.queued", language === "r" ? "R" : "Python"));
    if (!accepted && currentId.value === frameId) {
      invalidateKernelCache();
      await loadExecutionLog(frameId);
      notifyLoadArtifacts(frameId);
    }
    return true;
  } catch (error) {
    hint(t("nb.repl.execFailed", apiErrorText(error)), true);
    return false;
  } finally {
    const pending = pendingReplIdentity.value as { execution_id?: string } | null;
    if (!accepted && pending && pending.execution_id === executionId) {
      pendingReplIdentity.value = null;
    }
    if (!accepted) {
      if (runButton) runButton.disabled = false;
      if (input) input.disabled = false;
      if (stop) stop.classList.add("hidden");
    }
    if (currentId.value === frameId && dockIsNotebook()) nbRender();
  }
}

export type KernelPaintEls = {
  state?: HTMLElement | null;
  bStop?: { disabled: boolean } | null;
  bStart?: { disabled: boolean } | null;
  title?: HTMLElement | null;
  revive?: HTMLElement | null;
  strip?: { line?: HTMLElement | null };
  badge?: { root?: HTMLElement | null; label?: HTMLElement | null };
};

function paintKernel(els: KernelPaintEls, st: KernelStatus): void {
  const label = st.turn_running
    ? t("dash.badge.running")
    : ({ running: t("nb.kernel.stateActive"), stopped: t("nb.kernel.stateStopped"), none: t("nb.kernel.stateNone") }[
        st.state || ""
      ] || st.state);
  if (els.state) {
    els.state.textContent = String(label || "") + (st.generation ? t("nb.kernel.generation", st.generation) : "");
    els.state.className = "kstate " + (st.turn_running ? "run" : st.state);
  }
  const env = st.env || {};
  if (els.title) {
    els.title.textContent =
      kernelLabel(kernelIdFromEnv(env)) +
      " kernel · " +
      t("nb.kernel.shared") +
      (st.generation_id ? " · " + t("nb.owner.generation", shortRuntime(st.generation_id)) : "") +
      (env.pending ? t("nb.kernel.pendingSwitch", env.pending) : "");
  }
  if (els.badge && els.badge.root && els.badge.label) {
    const mode = runtimeSummary().status;
    ["live", "busy", "ended", "restoring", "partial", "failed", "ready", "idle"].forEach((name) => {
      const root = els.badge && els.badge.root;
      if (root) root.classList.toggle(name, name === mode);
    });
    els.badge.label.textContent = t("runtime.status." + mode);
  }
  const quarantined = st.view_only === true && st.trust_state === "quarantined";
  if (els.bStop) els.bStop.disabled = !st.alive;
  if (els.bStart) els.bStart.disabled = !!(st.alive || quarantined);
  if (els.revive) {
    els.revive.classList.toggle("hidden", !!(st.alive || st.turn_running || quarantined));
    els.revive.title = quarantined ? t("runtime.quarantineHint") : "";
  }
  if (els.strip && els.strip.line) {
    const rt = kernelLabel(kernelIdFromEnv(env)) + (env.python_version ? " " + env.python_version : "");
    const live = !!st.turn_running;
    const ready = !live && !!st.alive;
    els.strip.line.textContent = live
      ? t("nb.status.live", rt)
      : ready
        ? t("nb.status.ready", rt)
        : t("nb.status.ended", rt);
    els.strip.line.className = "nb-status-line " + (live ? "live" : ready ? "ready" : "ended");
  }
}

/** app.js:9993-10018 */
export async function refreshKernelState(els?: KernelPaintEls): Promise<void> {
  const paintEls = els || {};
  if (!currentId.value) {
    if (paintEls.state) paintEls.state.textContent = t("nb.kernel.noSession");
    return;
  }
  const kc = _kc.value;
  if (kc.id === currentId.value && kc.st) paintKernel(paintEls, kernelStatusOf(kc.st));
  if (kc.stBusy) return;
  if (kc.id === currentId.value && kc.st && Date.now() - kc.stAt < 800) return;
  const sid = currentId.value;
  kc.stBusy = true;
  let st: Record<string, unknown> | null;
  try {
    st = await notebookFetch(`/frames/${sid}/kernel`);
  } catch {
    kc.stBusy = false;
    return;
  }
  kc.stBusy = false;
  if (sid !== currentId.value) return;
  const prev = kernelStatusOf(kc.st);
  const previousRuntimeKey = kc.st
    ? [prev.state, prev.alive, prev.turn_running, prev.generation_id, prev.generation, prev.view_only, prev.trust_state].join(
        ":",
      )
    : "";
  if (kc.id !== sid) {
    kc.id = sid;
    kc.envs = null;
  }
  kc.st = st;
  kc.stAt = Date.now();
  artifactWorkbench.value = !!(st && st.artifact_workbench);
  bumpKernelEpoch();
  paintKernel(paintEls, kernelStatusOf(st));
  const next = kernelStatusOf(st);
  const modeChanged = (!!next.repl_enabled && !!paintEls.strip) || (!next.repl_enabled && !!paintEls.state);
  const runtimeKey = [
    next.state,
    next.alive,
    next.turn_running,
    next.generation_id,
    next.generation,
    next.view_only,
    next.trust_state,
  ].join(":");
  if ((modeChanged || runtimeKey !== previousRuntimeKey) && dockIsNotebook()) {
    const raf =
      typeof requestAnimationFrame === "function"
        ? requestAnimationFrame
        : (cb: () => void) => setTimeout(cb, 0);
    raf(() => nbRender());
  }
}

/** app.js:10020-10047 */
export async function nbPopulateEnvSelect(envSel: HTMLSelectElement | null): Promise<void> {
  if (!currentId.value || !envSel) return;
  const fill = (envs: KernelEnvRow[], cur: unknown) => {
    envSel.innerHTML = "";
    (envs || []).forEach((e) => {
      const notable = e.notable && e.notable.length ? " — " + e.notable.slice(0, 4).join("/") : "";
      const o = document.createElement("option");
      o.textContent = e.name + (e.runnable ? "" : " · R") + notable;
      o.value = e.name;
      if (!e.runnable) o.disabled = true;
      o.title = e.description || "";
      envSel.appendChild(o);
    });
    if (cur) envSel.value = String(cur);
  };
  const kc = _kc.value;
  if (kc.id === currentId.value && kc.envs) fill(kc.envs as KernelEnvRow[], kc.cur);
  if (kc.envBusy) return;
  if (kc.id === currentId.value && kc.envs && Date.now() - kc.envAt < 8000) return;
  const sid = currentId.value;
  kc.envBusy = true;
  let data: Record<string, unknown> | null;
  try {
    data = await notebookFetch(`/frames/${sid}/environments`);
  } catch {
    kc.envBusy = false;
    return;
  }
  kc.envBusy = false;
  if (sid !== currentId.value) return;
  if (kc.id !== sid) {
    kc.id = sid;
    kc.st = null;
  }
  kc.envs = (data && data.environments) || [];
  kc.cur = data && data.current;
  kc.envAt = Date.now();
  bumpKernelEpoch();
  fill(kc.envs as KernelEnvRow[], kc.cur);
}

/** app.js:10049-10061 — third `_kc` invalidate site. */
export async function nbSwitchEnv(name: string, envSel?: HTMLSelectElement | null): Promise<void> {
  if (!currentId.value || !name) return;
  if (envSel) envSel.disabled = true;
  try {
    const r = await notebookFetch(`/frames/${currentId.value}/kernel/env`, {
      method: "POST",
      body: JSON.stringify({ env: name }),
    });
    if (r && r.error) hint(t("nb.kernel.envSwitchFailed", r.error), true);
    else hint(t("nb.kernel.envSwitched", name));
  } catch (e) {
    hint(t("nb.kernel.envSwitchFailed", apiErrorText(e)), true);
  }
  if (envSel) envSel.disabled = false;
  invalidateKernelCache();
  if (dockIsNotebook()) nbRender();
}

/** kernel_status WS body. app.js:5347-5356 */
export function handleKernelStatus(m: WsMessage): void {
  if (m.status === "restarted") hint(t("kernel.restarted", m.generation || "?"));
  else if (m.status === "stopped") hint(t("kernel.stopped"));
  else if (m.status === "started") hint(t("kernel.started"));
  else if (m.status === "env_changed") {
    const env = m.env as { name?: string } | undefined;
    hint(t("kernel.envChanged", (env && env.name) || t("kernel.envChanged.default")));
  }
  invalidateKernelCache();
  if (m.sandbox) securityState.value = { sandbox: m.sandbox };
  scheduleWorkbenchRefresh();
  if (dockIsNotebook()) nbRender();
}

let scheduleWorkbenchRefreshFn: ((ms?: number) => void) | null = null;
export function setScheduleWorkbenchRefresh(fn: ((ms?: number) => void) | null): void {
  scheduleWorkbenchRefreshFn = fn;
}

export function scheduleWorkbenchRefresh(ms?: number): void {
  if (scheduleWorkbenchRefreshFn) scheduleWorkbenchRefreshFn(ms);
}

let scopedExecFn:
  | ((frameId: string, path: string, label: string, ownerKind?: string) => Promise<{ ok?: boolean } | null>)
  | null = null;

export function setScopedExecutionRequest(
  fn:
    | ((frameId: string, path: string, label: string, ownerKind?: string) => Promise<{ ok?: boolean } | null>)
    | null,
): void {
  scopedExecFn = fn;
}

export async function interruptRepl(): Promise<void> {
  if (!currentId.value) return;
  try {
    if (scopedExecFn) {
      const result = await scopedExecFn(currentId.value, "kernel/interrupt", "notebook interrupt", "user_repl");
      if (result && result.ok) hint(t("nb.repl.interruptSent"));
      return;
    }
    await notebookFetch(`/frames/${currentId.value}/kernel/interrupt`, { method: "POST" });
    hint(t("nb.repl.interruptSent"));
  } catch (error) {
    hint(t("nb.action.failed", apiErrorText(error)), true);
  }
}

export async function copyNotebookCell(source: string): Promise<void> {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(String(source || ""));
    } else {
      throw new Error("clipboard unavailable");
    }
    hint(t("nb.action.copied"));
  } catch {
    const language = _replLanguage.value === "r" ? "r" : "python";
    const drafts = _replDrafts.value || { python: "", r: "" };
    drafts[language] = String(source || "");
    _replDrafts.value = drafts;
    hint(t("nb.action.copied"));
  }
}

export async function forkNotebookCell(cell: NotebookCell): Promise<void> {
  const checkpointId = publicText(cell && cell.fork_checkpoint_id, 96);
  if (!currentId.value || !branchCapability("fork_from_cell") || !checkpointId) return;
  try {
    await notebookFetch(`/frames/${currentId.value}/branches/fork`, {
      method: "POST",
      body: JSON.stringify({ from_cell_id: nbCellKey(cell) }),
    });
  } catch (error) {
    hint(t("nb.action.failed", apiErrorText(error)), true);
  }
}

export async function promoteNotebookCell(cell: NotebookCell): Promise<void> {
  if (!currentId.value || !branchCapability("promote")) return;
  try {
    const art = await notebookFetch(`/frames/${currentId.value}/artifacts/promote`, {
      method: "POST",
      body: JSON.stringify({ cell_id: nbCellKey(cell) }),
    });
    notifyLoadArtifacts(currentId.value);
    hint(t("nb.action.promoted", (art && art.filename) || ""));
  } catch (error) {
    hint(t("nb.action.failed", apiErrorText(error)), true);
  }
}
