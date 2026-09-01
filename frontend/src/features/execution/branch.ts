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

import { workbenchErrors } from "../../stores/timeline";
import { isReady } from "../../compat/stub";
import { t } from "../../i18n/runtime";
import { publicText } from "../scrub/scrub";
import { api } from "./api";
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
  const errors = workbenchErrors.value || {};
  errors.branchAction = forkErrorDisplay(presentation);
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
