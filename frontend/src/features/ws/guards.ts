import { currentId } from "../../stores/session";
import { pendingExecutionId, pendingRequestId } from "../../stores/stream";
import type { WsMessage } from "./types";

/** `m.root_frame_id || m.frame_id` — app.js:5185. */
export function eventFrameId(m: WsMessage): unknown {
  return m.root_frame_id || m.frame_id;
}

/** app.js:5358 — this event belongs to the session on screen. */
export function mine(f: unknown): boolean {
  return Boolean(f && currentId.value && f === currentId.value);
}

/**
 * Is this event the tail of a turn that is no longer the one on screen?
 *
 * The ordering is real and reproducible: `processing(A)`, `processing(B)`,
 * then `failed(A)` -- A fails inside the turn, persists its row, and only
 * finishes unwinding after B has been promoted out of the queue. Acting on
 * A's terminal there closes B's turn, unlocks the composer under a running
 * turn, and prints A's error into B's transcript.
 *
 * Filtered on the EXECUTION, not the request: a client may reuse
 * `X-Request-Id`, so A and B can legitimately share one. Request id is the
 * fallback for a daemon old enough not to send an execution id, and when
 * neither side offers any identity at all the event is treated as current --
 * the pre-identity behaviour, which is the only safe default for a client
 * talking to an older server.
 *
 * Port of app.js:5755-5761.
 */
export function isStaleTurnEvent(event: WsMessage | null | undefined): boolean {
  const incomingExec = (event && event.execution_id) || "";
  if (incomingExec && pendingExecutionId.value) {
    return incomingExec !== pendingExecutionId.value;
  }
  if (incomingExec || pendingExecutionId.value) return false;
  const incomingReq = (event && event.request_id) || "";
  if (incomingReq && pendingRequestId.value) {
    return incomingReq !== pendingRequestId.value;
  }
  return false;
}

function isF05Stub(fn: (...args: never[]) => unknown): boolean {
  try {
    return Function.prototype.toString.call(fn).includes("F-05 stub");
  } catch {
    return false;
  }
}

/** Call a later-lane window export; skip F-05 stubs so they cannot eat a cursor. */
export function tryLane(name: string, ...args: unknown[]): void {
  const fn = (globalThis as Record<string, unknown>)[name];
  if (typeof fn !== "function") return;
  if (isF05Stub(fn as (...args: never[]) => unknown)) return;
  (fn as (...a: unknown[]) => unknown)(...args);
}
