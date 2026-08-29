/**
 * F-10 message stream. Importing this module assigns the lane's contract
 * names onto `window` (the F-06 `bootWs()` / F-07 `t()` pattern) and
 * registers `text_reset` / `text_chunk`.
 *
 * Do not import `compat/window-exports` from here — that module installs
 * the S Proxy as a side effect and would clobber a test's own `window.S`.
 */

import { isReady } from "../../compat/stub";
import "./messages.css";
import { ensureMessageDom } from "./dom";
import {
  fetchAllMessages,
  fetchOlderMessages,
  fetchRecentMessages,
} from "./fetch";
import { registerMessageHandlers } from "./handlers";
import { insertMessageByTime, renderStored } from "./list";
import { openConversation } from "./open";
import { bindMessageScroll, down, updateJumpPill } from "./scroll";
import { feed, flushRender, startStream, _mdStableCut } from "./stream";

export type { MdCutState, MdFence } from "./cut";
export {
  _mdStableCut,
  emptyMdCutState,
  mdStableCut,
  shouldAdvanceSealed,
} from "./cut";
export {
  bindStreamingPre,
  countNewlines,
  liveOutputDelta,
  toolMetaLabel,
} from "./delta";
export type { LiveOutputDelta, StreamingPreHandle } from "./delta";
export { $, el, ensureMessageDom, messagesHost } from "./dom";
export {
  MESSAGE_PAGE_SIZE,
  MESSAGE_WALK_MAX_PAGES,
  apiGet,
  fetchAllMessages,
  fetchOlderMessages,
  fetchRecentMessages,
} from "./fetch";
export { registerMessageHandlers } from "./handlers";
export {
  INITIAL_RENDER_BATCH,
  cancelFramedRender,
  insertMessageByTime,
  interleaveHistory,
  nextBatchEnd,
  renderEmptySession,
  renderStored,
  scheduleFramedRender,
  setRenderStoredStepImpl,
} from "./list";
export type { HistoryItem, StoredMessage } from "./list";
export { openConversation } from "./open";
export {
  bindMessageScroll,
  down,
  flushScrollNow,
  messagesAtBottom,
  paintJumpPill,
  unbindMessageScroll,
  updateJumpPill,
} from "./scroll";
export {
  TOOL_LABELS,
  ensure,
  feed,
  flushRender,
  scheduleRender,
  sealText,
  setNbLiveAppendImpl,
  setNbLiveStartImpl,
  startStream,
} from "./stream";
export type { LiveStream } from "./stream";
export { MessageList, StreamingPre } from "./components";

export type MessagesTarget = Record<string, unknown>;

/**
 * Assign F-10 contract names and bind scroll. Safe to call more than once:
 * WS handlers use `registerUnlessPresent`.
 */
export function installMessages(
  target: MessagesTarget = globalThis as unknown as MessagesTarget,
): void {
  registerMessageHandlers();
  target.openConversation = openConversation;
  target.fetchRecentMessages = fetchRecentMessages;
  target.fetchOlderMessages = fetchOlderMessages;
  target.fetchAllMessages = fetchAllMessages;
  target.down = down;
  target.updateJumpPill = updateJumpPill;
  target.insertMessageByTime = insertMessageByTime;
  target.renderStored = renderStored;
  target.feed = feed;
  target.startStream = startStream;
  target.flushRender = flushRender;
  target._mdStableCut = _mdStableCut;
  if (typeof document !== "undefined") {
    ensureMessageDom();
    bindMessageScroll();
  }
}

const hostWindow = (globalThis as unknown as { window?: MessagesTarget }).window;
if (hostWindow) installMessages(hostWindow);

/** Capability check for this lane's window names. Uses `isReady`, not typeof. */
export function messagesReady(target: MessagesTarget = globalThis as unknown as MessagesTarget): boolean {
  return isReady(target.openConversation) && isReady(target.down);
}
