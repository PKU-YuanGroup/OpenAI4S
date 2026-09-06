/**
 * A turn's request ticket, guarded by a generation. Port of app.js:5679-5784
 * plus resumeWatch (7103-7120).
 *
 * `openTurnTicket` takes the generation before the POST; `closeTurnTicket`
 * invalidates it (turn end, session switch); `commitTurnTicket` writes only if
 * the generation is still the one it took AND the turn is still running.
 */

import { _openGen, currentId, project } from "../../stores/session";
import {
  _resumeTimer,
  _resumeTok,
  pendingExecutionId,
  pendingRequestId,
  running,
  turnTicket,
} from "../../stores/stream";
import { api } from "../sessions/api";
import { callLane } from "./host";

export function openTurnTicket(): number {
  turnTicket.value = (turnTicket.value || 0) + 1;
  return turnTicket.value;
}

export function commitTurnTicket(
  token: number | null | undefined,
  accepted: { request_id?: unknown; execution_id?: unknown } | null | undefined,
): boolean {
  if (!accepted || !accepted.request_id) return false;
  if (token !== turnTicket.value) return false;
  if (!running.value) return false;
  pendingRequestId.value = String(accepted.request_id);
  if (accepted.execution_id) pendingExecutionId.value = String(accepted.execution_id);
  return true;
}

export function ownsTurnTicket(token: number | null | undefined): boolean {
  return token != null && token === turnTicket.value;
}

/**
 * Claim the slot for this submission, if the server says it is the one running.
 * `queue_position === 0` is the ONLY proof of that.
 */
export function acceptTurnTicket(
  token: number | null | undefined,
  accepted: {
    request_id?: unknown;
    execution_id?: unknown;
    queue_position?: unknown;
  } | null | undefined,
): boolean {
  if (!accepted || !accepted.request_id) return false;
  if (accepted.queue_position !== 0) return false;
  return commitTurnTicket(token, accepted);
}

export function retireTurnTicket(token: number | null | undefined): boolean {
  if (!ownsTurnTicket(token)) return false;
  turnTicket.value = (turnTicket.value || 0) + 1;
  return true;
}

export function activateTurnTicket(
  requestId: unknown,
  executionId: unknown,
): number {
  // The generation always advances: another turn is running now, so every
  // ticket in flight is stale whether or not this event named itself.
  turnTicket.value = (turnTicket.value || 0) + 1;
  // The identities are only *overwritten* by an event that carries them. An
  // older daemon sends `processing` with neither, and clearing on that would
  // throw away the ids the 202 had already given us.
  if (requestId) pendingRequestId.value = String(requestId).slice(0, 96);
  if (executionId) pendingExecutionId.value = String(executionId).slice(0, 96);
  return turnTicket.value;
}

export function closeTurnTicket(): void {
  turnTicket.value = (turnTicket.value || 0) + 1;
  pendingRequestId.value = null;
  pendingExecutionId.value = null;
}

/**
 * Watchdog for a missed terminal WS event after reconnects. app.js:7103.
 * Reloads through `openConversation` (F-10, gated with isReady) rather than
 * F-13's synchronous copy.
 */
export function resumeWatch(fid: string, gen: number): void {
  clearTimeout(_resumeTimer.value as ReturnType<typeof setTimeout>);
  const tok = (_resumeTok.value || 0) + 1;
  _resumeTok.value = tok;
  const stale = (): boolean =>
    tok !== _resumeTok.value ||
    gen !== _openGen.value ||
    currentId.value !== fid ||
    !running.value;
  const tick = async (): Promise<void> => {
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
      callLane("openConversation", fid, project.value);
      return;
    }
    _resumeTimer.value = setTimeout(tick, 2000);
  };
  _resumeTimer.value = setTimeout(tick, 2000);
}
