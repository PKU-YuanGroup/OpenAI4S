import type { WindowExportsTarget } from "../../compat/window-exports";
import { connectWS } from "./connect";
import { registerBuiltinHandlers } from "./handlers";
import { onEvent } from "./registry";

export type { WsHandler, WsMessage } from "./types";
export { eventFrameId, isStaleTurnEvent, mine, tryLane } from "./guards";
export {
  onEvent,
  registerWsHandler,
  hasWsHandler,
  resetWsHandlers,
} from "./registry";
export {
  API,
  WS_PING_MS,
  WS_RECONNECT_MS,
  clearWsPing,
  connectWS,
  handleIncomingMessage,
  setWebSocketImpl,
  sub,
  unsub,
  wsUrl,
} from "./connect";
export type { WsConstructor, WsSocket } from "./connect";
export {
  LOAD_ARTIFACTS_DEBOUNCE_MS,
  LOAD_SESSIONS_DEBOUNCE_MS,
  clearWsDebouncers,
  patchSessionFromFrameUpdate,
  registerBuiltinHandlers,
  scheduleLoadArtifacts,
  scheduleLoadSessions,
  setArtifactCreatedSideEffects,
  setFrameUpdateTurnHandler,
  setLoadArtifactsImpl,
  setLoadSessionsImpl,
  upsertArtifactFromEvent,
} from "./handlers";

/**
 * Register the WS-owned handlers and export `onEvent` onto `target`
 * (browser `window`, or a Vitest object). Does not open a socket — call
 * `connectWS` from the boot path when WebSocket exists.
 */
export function installWs(
  target: WindowExportsTarget = globalThis as unknown as WindowExportsTarget,
): void {
  registerBuiltinHandlers();
  target.onEvent = onEvent;
}

/** F-06 boot: registry + window.onEvent, then connect when WebSocket exists. */
export function bootWs(
  target: WindowExportsTarget = globalThis as unknown as WindowExportsTarget,
): void {
  installWs(target);
  if (typeof WebSocket === "function" && import.meta.env.MODE !== "test") {
    connectWS();
  }
}
