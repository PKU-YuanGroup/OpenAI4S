/**
 * Scoped execution interrupts. Port of app.js:3021-3041
 * (`exactExecutionIdentity` + `scopedExecutionRequest`).
 *
 * This lane owned every piece of the mechanism -- `identityForOwner`,
 * `rememberExecutionQueue`, `sanitizeExecutionQueue`, `optionalApi` -- but not
 * the function that uses them, so the workbench had the parts and not the
 * whole. Both callers reached for a lane name (`callLane("scopedExecutionRequest",
 * ...)`) that nothing ever assigned, and `callLane` returns `undefined` for a
 * missing name rather than throwing: pressing Stop resolved to `undefined`,
 * no POST was ever sent, and no error appeared. Cancel was inert.
 *
 * It lives here rather than in features/sessions because every dependency is
 * here and `features/timeline` imports nothing from `features/sessions` -- so
 * sessions -> timeline is a new one-way edge and not a cycle.
 *
 * The point of the whole thing is the word *exact*: an interrupt names the
 * execution it means to stop, so a Stop pressed while the turn is handing over
 * to the next execution cannot kill whatever happens to be running instead.
 */

import { pendingReplIdentity } from "../../stores/notebook";
import { t } from "../../i18n/runtime";
import { api, hint, optionalApi } from "./api";
import { identityForOwner, rememberExecutionQueue } from "./island";
import { S } from "./s";
import { sanitizeExecutionQueue } from "./sanitize";

export interface ExecutionIdentity {
  execution_id: string;
  owner: { kind: string; id: string };
}

export interface ScopedExecutionResult {
  ok?: boolean;
  reason?: string;
  [key: string]: unknown;
}

/**
 * Resolve the exact execution to interrupt, or null.
 *
 * The cached paths are deliberately guarded on `frameId === S.currentId`: a
 * cache describes the open conversation, and answering a request for another
 * frame from it would name the wrong execution -- which is the failure this
 * function exists to prevent.
 */
export async function exactExecutionIdentity(
  frameId: string,
  ownerKind?: string | null,
): Promise<ExecutionIdentity | null> {
  const replPending = pendingReplIdentity.value as
    | (ExecutionIdentity & { frame_id?: string })
    | null;
  const pending =
    ownerKind === "user_repl" &&
    frameId === S.currentId &&
    replPending &&
    replPending.frame_id === frameId
      ? replPending
      : null;
  if (pending && pending.owner && pending.owner.kind === ownerKind) return pending;
  if (frameId === S.currentId) {
    const cached = identityForOwner(S.executionQueue, ownerKind) as ExecutionIdentity | null;
    if (cached) return cached;
    if (!ownerKind && S.executionIdentity) return S.executionIdentity as ExecutionIdentity;
  }
  const snapshot = await optionalApi([
    `/frames/${frameId}/execution-queue`,
    `/frames/${frameId}/execution`,
  ]);
  if (!snapshot) return null;
  const safe = sanitizeExecutionQueue(snapshot);
  if (frameId === S.currentId) rememberExecutionQueue(snapshot);
  return identityForOwner(safe, ownerKind) as ExecutionIdentity | null;
}

/**
 * POST an interrupt naming the exact execution it means to stop.
 *
 * With no resolvable owner this refuses and says so, rather than sending an
 * unscoped interrupt -- an interrupt that names nothing can land on whatever
 * started after the one the user meant.
 */
export async function scopedExecutionRequest(
  frameId: string,
  endpoint: string,
  reason: string,
  ownerKind?: string | null,
): Promise<ScopedExecutionResult> {
  const identity = await exactExecutionIdentity(frameId, ownerKind);
  if (!identity) {
    hint(t("nb.interrupt.noOwner"), true);
    return { ok: false, reason: "no_exact_owner" };
  }
  return (await api(`/frames/${frameId}/${endpoint}`, {
    method: "POST",
    body: JSON.stringify({
      execution_id: identity.execution_id,
      owner: identity.owner,
      owner_id: identity.owner.id,
      reason,
    }),
  })) as ScopedExecutionResult;
}
