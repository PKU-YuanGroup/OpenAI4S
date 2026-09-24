/**
 * Kernel cache, chips-side fetch, stop/start/restart, env switch, REPL execute.
 *
 * `_kc` lives in the F-05 notebook store. Invalidate timings (verbatim):
 * kernel_status, turnDone, nbSwitchEnv (app.js:5352, 5854, 10060).
 */

import { isReady } from "../../compat/stub";
import {
  _kc,
  _replDrafts,
  _replLanguage,
  artifactWorkbench,
  pendingReplIdentity,
  type KernelCache,
} from "../../stores/notebook";
import { currentId } from "../../stores/session";
import { field } from "../../stores/signal-field";
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
import { nbRender } from "./scroll";
import type { KernelEnvRow, KernelStatus, NotebookCell } from "./types";

export { kernelIdFromEnv, kernelLabel } from "./labels";

/**
 * The kernel facts last read for a session, one immutable value the dock
 * renders from. It replaces paintKernel()'s writes into element refs and the
 * kernelEpoch counter that told Preact about in-place `_kc` edits.
 * Invalidation clears the `_kc` read cache, not this: what was last read
 * stays on screen until the next read replaces it, so the status line does
 * not flash "…" and the REPL panel does not unmount after every turn.
 */
export type KernelView = {
  sid: string;
  st: KernelStatus | null;
  envs: KernelEnvRow[] | null;
  cur: string | null;
};

export const kernelView = field<KernelView | null>(() => null);

/** The environment picked in the select, shown until a read for that session answers. */
export const envChoice = field<{ sid: string; name: string; posting: boolean } | null>(() => null);

function viewFor(sid: string | null | undefined): KernelView | null {
  const view = kernelView.value;
  return view && sid && view.sid === sid ? view : null;
}

/** The open session's last-read kernel status, or null before its first read. */
export function currentKernelStatus(): KernelStatus | null {
  const view = viewFor(currentId.value);
  return view ? view.st : null;
}

/** The open session's last-read environments. */
export function currentKernelEnvs(): { envs: KernelEnvRow[] | null; cur: string | null } {
  const view = viewFor(currentId.value);
  return { envs: view ? view.envs : null, cur: view ? view.cur : null };
}

/** `_kc` is replaced, never edited in place, so a reader of the signal hears about it. */
function writeKc(patch: Partial<KernelCache>): void {
  _kc.value = { ..._kc.value, ...patch };
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

/**
 * The kernel reads in flight, one per session. `kc.stBusy` / `kc.envBusy`
 * were single flags that neither a session switch nor an invalidation reset:
 * B's read was skipped while A's was still out, A's answer was then dropped
 * as another session's, and B's status line stayed at "…".
 */
let statusRead: { sid: string } | null = null;
let envRead: { sid: string } | null = null;
/** Bumped by every invalidation; an answer from before one is shown but read again. */
let invalidations = 0;

/** app.js:9955. */
export function invalidateKernelCache(): void {
  writeKc({ id: null, st: null, stAt: 0, envs: null, cur: null, envAt: 0 });
  invalidations += 1;
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
  const st = kernelStatusOf(currentKernelStatus());
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

function latestCellForLanguage(entries: NotebookCell[], language: string): NotebookCell | null {
  const list = entries.filter((cell) =>
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
  const kcSt = kernelStatusOf(currentKernelStatus());
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
  // One projection for the whole summary; it used to be computed three times.
  const entries = notebookDisplayEntries();
  const pythonCell = latestCellForLanguage(entries, "python");
  const rCell = latestCellForLanguage(entries, "r");
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
      : Math.max(0, ...entries.map((cell) => Number(cell.state_revision) || 0));
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

/** app.js:9920-9947. The REPL's controls follow `pendingReplIdentity`, set here. */
export async function executeNotebookCode(code: string, language: string): Promise<boolean> {
  code = String(code || "");
  language = String(language || "python").toLowerCase() === "r" ? "r" : "python";
  if (!code.trim() || !currentId.value) return false;
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
      if (owner && owner.kind && owner.id) pendingReplIdentity.value = { ...pending, owner };
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
    if (currentId.value === frameId && dockIsNotebook()) nbRender();
  }
}

function runtimeKeyOf(st: KernelStatus | null): string {
  return st
    ? [st.state, st.alive, st.turn_running, st.generation_id, st.generation, st.view_only, st.trust_state].join(":")
    : "";
}

/** app.js:9993-10018. Reads /kernel when the cache is stale; the dock renders `kernelView`. */
export async function refreshKernelState(): Promise<void> {
  const sid = currentId.value;
  if (!sid) return;
  const kc = _kc.value;
  if (kc.id === sid && kc.st && Date.now() - kc.stAt < 800) return;
  if (statusRead && statusRead.sid === sid) return;
  const read = (statusRead = { sid });
  const generation = invalidations;
  let st: Record<string, unknown> | null;
  try {
    st = await notebookFetch(`/frames/${sid}/kernel`);
  } catch {
    return;
  } finally {
    if (statusRead === read) statusRead = null;
  }
  if (sid !== currentId.value) return;
  const held = _kc.value;
  const shown = viewFor(sid);
  const previousRuntimeKey = runtimeKeyOf(shown ? shown.st : null);
  const status = kernelStatusOf(st);
  writeKc({
    id: sid,
    envs: held.id === sid ? held.envs : null,
    st,
    // An invalidation while this read was out: show it, but read again.
    stAt: generation === invalidations ? Date.now() : 0,
  });
  kernelView.value = { sid, st: status, envs: shown ? shown.envs : null, cur: shown ? shown.cur : null };
  artifactWorkbench.value = !!(st && st.artifact_workbench);
  if (runtimeKeyOf(status) !== previousRuntimeKey && dockIsNotebook()) {
    const raf =
      typeof requestAnimationFrame === "function"
        ? requestAnimationFrame
        : (cb: () => void) => setTimeout(cb, 0);
    raf(() => nbRender());
  }
}

/** app.js:10020-10047. Reads /environments when stale; the select renders `kernelView`. */
export async function refreshKernelEnvs(): Promise<void> {
  const sid = currentId.value;
  if (!sid) return;
  const kc = _kc.value;
  if (kc.id === sid && kc.envs && Date.now() - kc.envAt < 8000) return;
  if (envRead && envRead.sid === sid) return;
  const read = (envRead = { sid });
  const generation = invalidations;
  let data: Record<string, unknown> | null;
  try {
    data = await notebookFetch(`/frames/${sid}/environments`);
  } catch {
    return;
  } finally {
    if (envRead === read) envRead = null;
  }
  if (sid !== currentId.value) return;
  const held = _kc.value;
  const envs = ((data && data.environments) || []) as KernelEnvRow[];
  const cur = data ? data.current : null;
  writeKc({
    id: sid,
    st: held.id === sid ? held.st : null,
    envs,
    cur,
    envAt: generation === invalidations ? Date.now() : 0,
  });
  const shown = viewFor(sid);
  kernelView.value = { sid, st: shown ? shown.st : null, envs, cur: cur == null ? null : String(cur) };
  const choice = envChoice.value;
  if (choice && choice.sid === sid && !choice.posting) envChoice.value = null;
}

/** app.js:10049-10061 — third `_kc` invalidate site. */
export async function nbSwitchEnv(name: string): Promise<void> {
  const sid = currentId.value;
  if (!sid || !name) return;
  envChoice.value = { sid, name, posting: true };
  try {
    const r = await notebookFetch(`/frames/${sid}/kernel/env`, {
      method: "POST",
      body: JSON.stringify({ env: name }),
    });
    if (r && r.error) hint(t("nb.kernel.envSwitchFailed", r.error), true);
    else hint(t("nb.kernel.envSwitched", name));
  } catch (e) {
    hint(t("nb.kernel.envSwitchFailed", apiErrorText(e)), true);
  }
  const choice = envChoice.value;
  if (choice && choice.sid === sid && choice.name === name) envChoice.value = { ...choice, posting: false };
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
