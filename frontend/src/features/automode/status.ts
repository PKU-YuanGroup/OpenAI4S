/**
 * The Auto Mode status of the open conversation, read only through
 * `GET /frames/{id}/auto-mode`.
 *
 * SQLite is the source of truth; this module never writes, never calls a
 * transition, and never builds a status from a WebSocket event. A read is
 * started by opening the session options menu, by an explicit retry, by a
 * canonical event hint, by a reconnect, or by the conversation being reopened
 * (a switch, a branch activation or revert, a resync). The menu decides which
 * of those happen while it is showing the block (see `menu.ts`).
 *
 * Which response wins:
 *
 * - A response for another conversation, or for an earlier opening of this one
 *   (`_openGen` moved: branch activation and revert both reopen the
 *   conversation), is dropped. That is what makes a branch change safe.
 * - A read *issued after* the shown read arrived replaces it whatever its event
 *   cursor says. The server answered it later, so its snapshot is at least as
 *   new -- and a revert can legitimately move a branch's cursor backwards.
 * - Reads that were in flight together are ordered by the durable cursor
 *   (`last_event_ordinal`) of the same branch, then by the frame selection's
 *   `revision` (a selection save emits no event, so the cursor alone would
 *   reject it), then by the order they were issued.
 */

import { _openGen, currentId } from "../../stores/session";
import { ApiError, api } from "../sessions/api";
import { sanitizeAutoModeView } from "./sanitize";
import type { AutoModeView } from "./types";

export type StatusFailure = "unavailable" | "not_found";

export type AutoModeStatus =
  | { phase: "idle" }
  | { phase: "loading"; frameId: string }
  | { phase: "ready"; frameId: string; view: AutoModeView }
  | { phase: "failed"; frameId: string; failure: StatusFailure };

type Shown = {
  seq: number;
  issuedTick: number;
  arrivedTick: number;
  ok: boolean;
  branchId: string | null;
  ordinal: number;
  revision: number;
};

let status: AutoModeStatus = { phase: "idle" };
let contextKey = "";
let epoch = 0;
let requestSeq = 0;
let tick = 0;
let shown: Shown | null = null;
const listeners = new Set<() => void>();

function keyNow(): string {
  return `${currentId.value || ""}\u0000${_openGen.value}`;
}

function notify(): void {
  for (const listener of [...listeners]) {
    try {
      listener();
    } catch {
      /* one painter cannot stop another, nor the read that called it */
    }
  }
}

export function autoModeStatus(): AutoModeStatus {
  return status;
}

export function onAutoModeStatus(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * Re-key on a different conversation or a new opening of the same one. Returns
 * whether it did: everything held so far describes something no longer open.
 */
export function syncAutoModeContext(): boolean {
  const key = keyNow();
  if (key === contextKey) return false;
  contextKey = key;
  epoch += 1;
  shown = null;
  const frameId = currentId.value;
  status = frameId ? { phase: "loading", frameId } : { phase: "idle" };
  notify();
  return true;
}

function failureOf(error: unknown): StatusFailure {
  // Only the route's own refusal means the session is gone. An old daemon
  // without the route also answers 404, and that is not "not found".
  if (error instanceof ApiError && error.status === 404 && error.code === "frame_not_found") return "not_found";
  return "unavailable";
}

function wins(candidate: Shown, prior: Shown | null): boolean {
  if (!prior) return true;
  if (candidate.issuedTick >= prior.arrivedTick) return true;
  if (candidate.ok && prior.ok && candidate.branchId === prior.branchId) {
    if (candidate.ordinal !== prior.ordinal) return candidate.ordinal > prior.ordinal;
    if (candidate.revision !== prior.revision) return candidate.revision > prior.revision;
  }
  return candidate.seq > prior.seq;
}

/** Read the open conversation's status. Never rejects; the outcome lands in `autoModeStatus()`. */
export async function refreshAutoModeStatus(): Promise<void> {
  syncAutoModeContext();
  const frameId = currentId.value;
  if (!frameId) return;
  const ownEpoch = epoch;
  const ownKey = contextKey;
  const seq = ++requestSeq;
  const issuedTick = tick;
  let raw: unknown = null;
  let error: unknown = null;
  try {
    raw = await api(`/frames/${encodeURIComponent(frameId)}/auto-mode`);
  } catch (caught) {
    error = caught;
  }
  if (ownEpoch !== epoch || ownKey !== keyNow()) return;
  const arrivedTick = ++tick;
  const view = error ? null : sanitizeAutoModeView(raw);
  // An unknown schema, an unknown closed value, or another conversation's
  // root is shown as unavailable rather than guessed at.
  const usable = view && view.root_frame_id === frameId ? view : null;
  const candidate: Shown = {
    seq,
    issuedTick,
    arrivedTick,
    ok: !!usable,
    branchId: usable ? usable.branch_id : null,
    ordinal: usable && usable.last_event_ordinal !== null ? usable.last_event_ordinal : -1,
    revision: usable ? usable.selection.revision : -1,
  };
  if (!wins(candidate, shown)) return;
  shown = candidate;
  status = usable
    ? { phase: "ready", frameId, view: usable }
    : { phase: "failed", frameId, failure: error ? failureOf(error) : "unavailable" };
  notify();
}

/**
 * The read behind opening the menu. Whatever this tab painted before is a
 * badge cached in browser memory, so the block shows "Loading…" until this
 * opening's own GET answers rather than the previous answer.
 */
export function beginAutoModeStatusRead(): Promise<void> {
  syncAutoModeContext();
  const frameId = currentId.value;
  if (frameId && status.phase !== "loading") {
    status = { phase: "loading", frameId };
    notify();
  }
  return refreshAutoModeStatus();
}

/** Test seam: forget everything, including the context key. */
export function resetAutoModeStatus(): void {
  status = { phase: "idle" };
  contextKey = "";
  epoch += 1;
  requestSeq = 0;
  tick = 0;
  shown = null;
  listeners.clear();
}
