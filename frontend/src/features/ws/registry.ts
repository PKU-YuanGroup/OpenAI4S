import type { WsHandler, WsMessage } from "./types";

const handlers = new Map<string, WsHandler>();

/**
 * Exactly one handler per event type. The legacy if/else chain had one
 * branch per type; the cursor advances only after onEvent returns, so a
 * second handler would let a crash skip an event that only some handlers
 * applied, and resume would then drop it for good.
 */
export function registerWsHandler(type: string, handler: WsHandler): void {
  if (typeof type !== "string" || type === "") {
    throw new Error("WS handler type must be a non-empty string");
  }
  if (typeof handler !== "function") {
    throw new Error(`WS handler for type ${type} must be a function`);
  }
  if (handlers.has(type)) {
    throw new Error(`duplicate WS handler for type: ${type}`);
  }
  handlers.set(type, handler);
}

export function hasWsHandler(type: string): boolean {
  return handlers.has(type);
}

export function resetWsHandlers(): void {
  handlers.clear();
}

/**
 * Inner dispatcher. Window `onEvent` is this function — E2E calls it
 * directly, and connectWS calls it *before* advancing `_seqSeen`.
 */
export function onEvent(m: WsMessage | null | undefined): void {
  if (!m || typeof m !== "object") return;
  const type = m.type;
  if (typeof type !== "string" || type === "") return;
  const handler = handlers.get(type);
  // A Map lookup cannot reach Object.prototype the way `obj[type]` can, so a
  // crafted `type` finds nothing rather than `toString`. The callable check
  // says that in the code instead of leaving it to the reader.
  if (typeof handler !== "function") return;
  handler(m);
}
