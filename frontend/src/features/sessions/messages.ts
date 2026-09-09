/** Newest-first message paging. app.js:6914-6962, 7282-7316. */

import { t } from "../../i18n";
import {
  _msgEarlierLoading,
  _openGen,
  historyContent,
  noteHistoryMutation,
  currentId,
  msgCursor,
  msgHasEarlier,
} from "../../stores/session";
import { apiErrorText } from "./api";
import { hint } from "./chrome";
import { $, el } from "./dom";
import { fetchOlderMessages, messageIdentity, uniqueMessages, MESSAGE_PAGE_SIZE } from "../messages/fetch";
import { insertMessageByTime, renderStored } from "./transcript";

export type ChatMessage = {
  role?: string;
  content?: unknown;
  created_at?: string;
  seq?: number;
  failure?: { request_id?: string } | null;
  review_status?: unknown;
  metadata?: Record<string, unknown>;
  artifact_refs?: unknown[];
};

// One validator serves initial history, load-earlier and transcript export.
export { fetchRecentMessages, fetchOlderMessages, fetchAllMessages } from "../messages/fetch";
export type { MessagePage } from "../messages/fetch";

export function paintEarlierControl(): void {
  const host = $("#messages");
  if (!host) return;
  let bar = document.getElementById("msgs-earlier");
  if (!msgHasEarlier.value) {
    if (bar) bar.remove();
    return;
  }
  if (!bar) {
    bar = el("div", "msgs-earlier");
    bar.id = "msgs-earlier";
    bar.style.textAlign = "center";
    bar.style.padding = "8px 0";
    const btn = el("button", "outline-btn small", t("conv.loadEarlier"));
    btn.type = "button";
    btn.onclick = () => {
      void loadEarlierMessages();
    };
    bar.appendChild(btn);
  }
  const btn = bar.querySelector("button");
  if (btn) {
    (btn as HTMLButtonElement).disabled = !!_msgEarlierLoading.value;
    btn.textContent = _msgEarlierLoading.value ? t("common.loading") : t("conv.loadEarlier");
  }
  if (host.firstChild !== bar) host.insertBefore(bar, host.firstChild);
}

export async function loadEarlierMessages(): Promise<void> {
  if (!currentId.value || !msgHasEarlier.value || msgCursor.value == null || _msgEarlierLoading.value) {
    return;
  }
  const host = $("#messages");
  if (!host) return;
  const fid = currentId.value;
  const gen = _openGen.value;
  _msgEarlierLoading.value = true;
  paintEarlierControl();
  try {
    const data = await fetchOlderMessages(fid, msgCursor.value, MESSAGE_PAGE_SIZE);
    if (gen !== _openGen.value || currentId.value !== fid) return;
    const beforeHeight = host.scrollHeight;
    const beforeTop = host.scrollTop;
    const holder = document.createDocumentFragment();
    const held = historyContent.value?.fid === fid ? historyContent.value : null;
    const known = new Set((held?.messages || []).map(messageIdentity).filter(Boolean));
    const incoming = uniqueMessages(data.messages).filter((row) => {
      const key = messageIdentity(row);
      return !key || !known.has(key);
    });
    incoming.forEach((mm) => insertMessageByTime(renderStored(mm, holder)));
    if (held) historyContent.value = { ...held, messages: [...incoming, ...held.messages] };
    noteHistoryMutation();
    host.scrollTop = beforeTop + (host.scrollHeight - beforeHeight);
    msgCursor.value = data.next_before_seq != null ? data.next_before_seq : null;
    msgHasEarlier.value = !!data.has_earlier;
  } catch (e) {
    if (gen !== _openGen.value || currentId.value !== fid) return;
    hint(t("conv.loadEarlierFailed", apiErrorText(e)), true);
  } finally {
    if (gen === _openGen.value && currentId.value === fid) {
      _msgEarlierLoading.value = false;
      paintEarlierControl();
    }
  }
}
