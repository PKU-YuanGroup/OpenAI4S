/**
 * F-10 WS types: `text_reset` / `text_chunk` / `user_message`.
 *
 * Port of app.js:5220 and 5270-5276. Later lanes must not re-register these.
 * `step` / `plan_*` / `await_permission` stay F-11; `notebook_cell_*` stay F-14.
 */

import { noteHistoryMutation } from "../../stores/session";
import { eventFrameId, isStaleTurnEvent, mine } from "../ws/guards";
import { hasWsHandler, registerWsHandler } from "../ws/registry";
import type { WsMessage } from "../ws/types";
import { storedCandidateOwnsChunk } from "./identity";
import { showLiveUserMessage } from "./list";
import { down } from "./scroll";
import { feed, startStream } from "./stream";

/** The turn's request, broadcast to every tab viewing the session. */
function handleUserMessage(m: WsMessage): void {
  const fid = eventFrameId(m);
  if (!mine(fid)) return;
  // null: this transcript already shows the row (a REST read, a replay).
  if (!showLiveUserMessage(m as Record<string, unknown>)) return;
  noteHistoryMutation();
  down();
}

function handleTextReset(m: WsMessage): void {
  const fid = eventFrameId(m);
  if (mine(fid) && !isStaleTurnEvent(m)) { noteHistoryMutation(); startStream(); }
}

function handleTextChunk(m: WsMessage): void {
  const fid = eventFrameId(m);
  if (!mine(fid) || isStaleTurnEvent(m)) return;
  noteHistoryMutation();
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
  registerUnlessPresent("user_message", handleUserMessage);
}
