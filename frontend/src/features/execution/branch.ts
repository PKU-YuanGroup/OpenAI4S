/**
 * Recovery / branch control REST. F-15 already paints the Timeline panels
 * (sanitize* 3032-3149 + island buttons). This lane owns the 409 presentation
 * for fork-without-checkpoint: one POST, surface the server sentence, never
 * retry, never rewrite as success.
 *
 * Routes (unchanged):
 *   POST /frames/{id}/branches/fork
 *   POST /frames/{id}/branches/checkpoints
 *   POST /frames/{id}/recovery/actions/{restore|retry|restart_fresh}
 */

import { currentId } from "../../stores/session";
import { branchState, workbenchErrors } from "../../stores/timeline";
import { isReady } from "../../compat/stub";
import { LANG, t } from "../../i18n/runtime";
import { askConfirm } from "../chrome/dialog";
import { historyT } from "../messages/copy";
import { publicText } from "../scrub/scrub";
import { api, ApiError } from "./api";
import { branchT } from "./copy";
import {
  forkErrorDisplay,
  forkOnce,
  presentForkError,
  type ForkPresentation,
} from "./conflict";

function hint(msg: string, err?: boolean): void {
  const fn = (globalThis as unknown as { hint?: unknown }).hint;
  if (isReady(fn)) (fn as (m: string, e?: boolean) => void)(msg, err);
}

/** Write the honest server sentence onto the Timeline branch-error banner. */
export function applyForkPresentation(presentation: ForkPresentation): void {
  // A new object, so a subscriber to workbenchErrors sees the banner change.
  workbenchErrors.value = {
    ...(workbenchErrors.value || {}),
    branchAction: forkErrorDisplay(presentation),
  };
  hint(t("branch.actionFailed", forkErrorDisplay(presentation)), true);
}

export async function forkFromCell(frameId: string, cellId: string): Promise<ForkPresentation | null> {
  const attempt = await forkOnce(() =>
    api(`/frames/${encodeURIComponent(frameId)}/branches/fork`, {
      method: "POST",
      body: JSON.stringify({ from_cell_id: cellId }),
    }),
  );
  if (attempt.ok) return null;
  applyForkPresentation(attempt.presentation);
  return attempt.presentation;
}

export type ForkResult =
  | { ok: true; branch_id: string; name: string; activated?: true }
  | { ok: false; presentation: ForkPresentation };

/** One in-flight fork per message. A second click must not POST again. */
const messageForkInFlight = new Set<string>();

/**
 * Panel refresh after a message fork. `list.ts` wires this to the workbench
 * refresh. `branch_created` also refreshes. There is no dynamic import of the
 * notebook kernel: that import was static everywhere else, so it never became
 * its own chunk and only warned at build time.
 */
let messageForkRefresh: (() => void) | null = null;

export function setMessageForkRefresh(refresh: (() => void) | null): void {
  messageForkRefresh = refresh;
}

function refreshAfterMessageFork(): void {
  const refresh = messageForkRefresh;
  if (!refresh) return;
  try {
    refresh();
  } catch {
    // The branch already exists. A failed panel refresh must not look like
    // a failed fork, and must not be retried as a second POST.
  }
}

function stillOnFrame(frameId: string): boolean {
  return currentId.value === frameId;
}

function messageForkKey(frameId: string, messageId: string): string {
  return frameId + "\0" + messageId;
}

/**
 * Whether this message's fork is still in flight. A history repaint gives the
 * row a fresh button while the first POST is pending; that button asks here
 * before it shows a busy state the first request would never settle.
 */
export function messageForkPending(frameId: string, messageId: string): boolean {
  return messageForkInFlight.has(messageForkKey(frameId, messageId));
}

function branchLabel(result: unknown): { branch_id: string; name: string } {
  const rec = result && typeof result === "object" ? (result as Record<string, unknown>) : {};
  const branchId = publicText(rec.branch_id, 96);
  const named = publicText(rec.name, 120);
  return { branch_id: branchId, name: named || branchId };
}

/** VIEW-2: how many characters of the question name the branch it opens. */
export const QUESTION_BRANCH_NAME_CHARS = 20;

/** "RNA-seq 质控与差异表达分析，样本表在…": the question's start, on one line. */
export function questionBranchName(question: string): string {
  const text = String(question || "").split(/\s+/).filter(Boolean).join(" ");
  const chars = Array.from(text);
  return chars.length <= QUESTION_BRANCH_NAME_CHARS
    ? text
    : chars.slice(0, QUESTION_BRANCH_NAME_CHARS).join("").trimEnd() + "…";
}

type ActivateBranch = (
  branchId: string,
  options: { confirmed: true; name: string },
) => Promise<boolean> | boolean | void;

/**
 * The confirmed activation the sidebar and the branch panel run
 * (`timeline/island.ts` `activateSessionBranch`, published on the window), or
 * null when it is not loaded or the session cannot switch branches now.
 */
function branchActivation(): ActivateBranch | null {
  const fn = (globalThis as unknown as { activateSessionBranch?: unknown }).activateSessionBranch;
  if (!isReady(fn)) return null;
  const state = branchState.value as { capabilities?: Record<string, unknown> } | null;
  if (!state || !state.capabilities || !state.capabilities.activate) return null;
  return fn as ActivateBranch;
}

/** The question goes back into the composer, ahead of anything already typed there. */
function prefillQuestion(question: string): void {
  const doc = (globalThis as { document?: { getElementById?: (id: string) => unknown } }).document;
  const composer =
    doc && typeof doc.getElementById === "function"
      ? (doc.getElementById("composer") as HTMLTextAreaElement | null)
      : null;
  if (!composer || !question) return;
  composer.value = composer.value.trim() ? question + "\n\n" + composer.value : question;
  composer.dispatchEvent(new Event("input", { bubbles: true }));
  composer.focus();
}

export type MessageForkOptions = {
  /** The question as the bubble shows it: names the branch and is prefilled. */
  question?: string;
  /** Called once the request is really going out (after the confirm). */
  onCreating?: () => void;
};

/**
 * Fork from one stored user message. One POST with `{from_message_id}` (and
 * the branch's `name` when the question is known). A 409 is the server's
 * sentence, not a retry and not a fork of the latest state.
 *
 * VIEW-2 (owner decision 35): where the session can switch branches, one
 * click asks the switch's own confirm first (the kernel restarts), then
 * creates the branch, switches to it and puts the question back in the
 * composer to edit; a cancel creates nothing. Where it cannot switch, the
 * branch is created and stays inactive, as before.
 */
export async function forkFromMessage(
  frameId: string,
  messageId: string,
  options: MessageForkOptions = {},
): Promise<ForkResult | null> {
  if (!frameId || !messageId) return null;
  const key = messageForkKey(frameId, messageId);
  if (messageForkInFlight.has(key)) return null;
  messageForkInFlight.add(key);
  try {
    const question = String(options.question || "").trim();
    const name = question ? questionBranchName(question) : "";
    const activate = branchActivation();
    if (activate) {
      // The switch's confirm, before anything exists: a cancel sends nothing.
      if (!(await askConfirm(branchT("branch.activateConfirm", name || historyT("history.forkMessage.label")))))
        return null;
      if (!stillOnFrame(frameId)) return null;
    }
    if (options.onCreating) options.onCreating();
    const body: Record<string, string | boolean> = { from_message_id: messageId };
    if (name) body.name = name;
    // VIEW-2b: the question goes back into the composer to be asked again, so
    // the branch ends just before it rather than already holding it.
    if (activate && question) body.before_source = true;
    const attempt = await forkOnce(() =>
      api(`/frames/${encodeURIComponent(frameId)}/branches/fork`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    );
    if (!attempt.ok) {
      const presentation = attempt.presentation.message
        ? attempt.presentation
        : { ...attempt.presentation, message: historyT("history.forkMessage.failed") };
      if (stillOnFrame(frameId)) applyForkPresentation(presentation);
      return { ok: false, presentation };
    }
    const created = branchLabel(attempt.result);
    if (!stillOnFrame(frameId)) return { ok: true, ...created };
    refreshAfterMessageFork();
    if (activate && (await activate(created.branch_id, { confirmed: true, name: created.name })) === true) {
      if (stillOnFrame(frameId)) prefillQuestion(question);
      return { ok: true, ...created, activated: true };
    }
    // Not switched (no activation here, or it was refused): the branch exists.
    if (stillOnFrame(frameId)) hint(historyT("history.forkMessage.created", created.name));
    return { ok: true, ...created };
  } finally {
    messageForkInFlight.delete(key);
  }
}

export async function forkFromCheckpoint(
  frameId: string,
  checkpointId: string,
  name?: string,
): Promise<ForkPresentation | null> {
  const body: Record<string, string> = { from_checkpoint_id: checkpointId };
  if (name) body.name = name;
  const attempt = await forkOnce(() =>
    api(`/frames/${encodeURIComponent(frameId)}/branches/fork`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  );
  if (attempt.ok) return null;
  applyForkPresentation(attempt.presentation);
  return attempt.presentation;
}

/** What an activation or a Restore left the kernel with (M-73). */
export type KernelOutcome = {
  /** active | partial | failed | cancelled, as the server said. */
  status: string;
  /** Variables the checkpoint had that the new kernel does not. */
  missing: string[];
  /** Why it stopped, when it failed outright. */
  message: string;
};

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function issueLists(result: Record<string, unknown>): unknown[] {
  const lists: unknown[] = [record(record(result.dimensions).namespace).issues];
  for (const holder of [result, record(result.recovery)]) {
    if (Array.isArray(holder.results)) holder.results.forEach((item) => lists.push(record(item).issues));
  }
  return lists.flatMap((list) => (Array.isArray(list) ? list : []));
}

/**
 * Read an activation result (`dimensions.namespace`, `recovery.results`) or a
 * recovery result (`results[].issues`) as one outcome: which variables did
 * not come back, and the first error when nothing ran.
 */
export function kernelOutcome(result: unknown): KernelOutcome {
  const rec = record(result);
  const status = publicText(rec.status, 32).toLowerCase() || "failed";
  const missing: string[] = [];
  let message = "";
  for (const raw of issueLists(rec)) {
    const issue = record(raw);
    if (issue.type === "missing_symbols" && Array.isArray(issue.names)) {
      for (const name of issue.names) {
        const text = publicText(name, 80);
        if (text && !missing.includes(text)) missing.push(text);
      }
    } else if (!message && issue.error) {
      message = publicText(issue.error, 240);
    }
  }
  return { status, missing, message };
}

/** The names a sentence lists: the first eight, then an ellipsis. */
export function missingNames(outcome: KernelOutcome): string {
  const shown = outcome.missing.slice(0, 8).join(LANG === "zh" ? "、" : ", ");
  return outcome.missing.length > 8 ? shown + " …" : shown;
}

/**
 * A branch's name; the session's own first line (its id is the session's) is
 * the main branch; any other unnamed one shows its shortened id.
 */
export function branchDisplayName(branch: unknown, rootFrameId?: string | null): string {
  const rec = record(branch);
  const named = publicText(rec.name, 120);
  if (named) return named;
  const id = publicText(rec.branch_id, 96);
  if (id && id === rootFrameId) return branchT("branch.main");
  return id.length > 12 ? id.slice(0, 8) + "…" : id;
}

/** The hint after an activation: never an error, since the switch happened. */
export function switchedText(name: string, outcome: KernelOutcome): string {
  if (outcome.status === "active") return branchT("branch.switched", name);
  return outcome.missing.length
    ? branchT("branch.switchedGap", name, missingNames(outcome))
    : branchT("branch.switchedGapAll", name);
}

/** What the branch panel says next to "Restart kernel and restore". */
export function kernelGapText(outcome: KernelOutcome): string {
  if (outcome.status === "active") return branchT("branch.restored");
  if (outcome.status === "failed" && outcome.message && !outcome.missing.length)
    return branchT("branch.restoreFailed", outcome.message);
  return outcome.missing.length
    ? branchT("branch.kernelGap", missingNames(outcome))
    : branchT("branch.kernelGapAll");
}

/**
 * M-64: what a recovery action does, or why it cannot run, in one sentence.
 * The card printed the server's own words ("checkpoint has an invalid python
 * bootstrap manifest") beside "Ready"; those are grouped here by what the
 * reader can do about them, and never shown raw.
 */
export function recoveryActionWords(action: { id: string; enabled: boolean; reason?: string }): string {
  if (action.enabled) return branchT("recovery.what." + action.id);
  const reason = String(action.reason || "").toLowerCase();
  const why = /already running/.test(reason)
    ? "busy"
    : /already active/.test(reason)
      ? "active"
      : /not partial or failed/.test(reason)
        ? "notRetryable"
        : /revert/.test(reason)
          ? "revertFirst"
          : /no checkpoint exists/.test(reason)
            ? "noCheckpoint"
            : /checkpoint/.test(reason)
              ? "checkpoint"
              : "other";
  return branchT("recovery.why." + why);
}

/** M-64: a checkpoint row says when and why it was taken, not "cp-33f1… cursor_cell". */
export function checkpointLabel(checkpoint: unknown): string {
  const cp = record(checkpoint);
  const at = Number(cp.created_at);
  const when =
    Number.isFinite(at) && at > 0
      ? new Date(at).toLocaleTimeString(LANG === "zh" ? "zh-CN" : "en-US", { hour12: false })
      : "";
  const reason = publicText(cp.reason, 80);
  // VIEW-2b: the boundary a question branch starts from, taken with the question.
  if (reason === "cursor_message_before") return branchT("branch.cp.beforeMessage", when);
  if (reason === "cursor_message" || cp.source_kind === "message") return branchT("branch.cp.message", when);
  if (reason === "cursor_cell" || cp.source_kind === "cell") return branchT("branch.cp.cell", when);
  return when ? branchT("branch.cp.other", reason || "checkpoint", when) : reason || "checkpoint";
}

/**
 * "Restart kernel and restore" (M-73): the Restore recovery action on the
 * active branch. A partial or failed restore answers 409 with the same
 * report as a 200, so both are read as an outcome rather than an error.
 */
export async function restoreBranchKernel(frameId: string, branchId: string): Promise<KernelOutcome> {
  try {
    const result = await api(
      `/frames/${encodeURIComponent(frameId)}/recovery/actions/restore`,
      { method: "POST", body: JSON.stringify({ branch_id: branchId, confirm: false }) },
    );
    return kernelOutcome(result);
  } catch (error) {
    const body = error instanceof ApiError ? record(error.body) : {};
    if (typeof body.status === "string") return kernelOutcome(body);
    return { status: "failed", missing: [], message: publicText((error as Error).message, 240) };
  }
}

export async function postRecoveryAction(
  frameId: string,
  actionId: string,
  branchId: string,
  confirm: boolean,
): Promise<{ ok: true } | { ok: false; message: string }> {
  try {
    await api(`/frames/${encodeURIComponent(frameId)}/recovery/actions/${encodeURIComponent(actionId)}`, {
      method: "POST",
      body: JSON.stringify({ branch_id: branchId, confirm }),
    });
    return { ok: true };
  } catch (error) {
    const presented = presentForkError(error);
    return { ok: false, message: publicText(presented.message, 240) };
  }
}
