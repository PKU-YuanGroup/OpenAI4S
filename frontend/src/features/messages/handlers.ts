/**
 * F-10 WS types: `text_reset` / `text_chunk`.
 *
 * Port of app.js:5220 and 5270-5276. Later lanes must not re-register these.
 * `step` / `plan_*` / `await_permission` stay F-11; `notebook_cell_*` stay F-14.
 */

import { eventFrameId, isStaleTurnEvent, mine } from "../ws/guards";
import { hasWsHandler, registerWsHandler } from "../ws/registry";
import type { WsMessage } from "../ws/types";
import { storedCandidateOwnsChunk } from "./identity";
import { feed, startStream } from "./stream";

function handleTextReset(m: WsMessage): void {
  const fid = eventFrameId(m);
  if (mine(fid) && !isStaleTurnEvent(m)) startStream();
}

function handleTextChunk(m: WsMessage): void {
  const fid = eventFrameId(m);
  if (!mine(fid) || isStaleTurnEvent(m)) return;
  feed(
    String(m.block_type || "text"),
    String(m.chunk || ""),
    m,
    storedCandidateOwnsChunk(m),
  );
}

function registerUnlessPresent(type: string, handler: (m: WsMessage) => void): void {
  if (!hasWsHandler(type)) registerWsHandler(type, handler);
}

export function registerMessageHandlers(): void {
  registerUnlessPresent("text_reset", handleTextReset);
  registerUnlessPresent("text_chunk", handleTextChunk);
}
