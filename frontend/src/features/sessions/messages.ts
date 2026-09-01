/** Newest-first message paging. app.js:6914-6962, 7282-7316. */

import { t } from "../../i18n";
import {
  _msgEarlierLoading,
  _openGen,
  currentId,
  msgCursor,
  msgHasEarlier,
} from "../../stores/session";
import { api, apiErrorText } from "./api";
import { hint } from "./chrome";
import { $, el } from "./dom";
import {
  MESSAGE_PAGE_SIZE,
  MESSAGE_WALK_MAX_PAGES,
  prependOlderMessages,
  shouldWalkEarlier,
  sortMessagesBySeq,
} from "./paging";
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

export type MessagePage = {
  messages: ChatMessage[];
  next_before_seq?: unknown;
  has_earlier?: boolean;
  complete?: boolean;
};

export async function fetchRecentMessages(fid: string, limit: number): Promise<MessagePage> {
  const data = (await api(
    `/frames/${encodeURIComponent(fid)}/messages?newest_first=1&limit=${limit}`,
  )) as MessagePage | null;
  const rows = (data && data.messages) || [];
  sortMessagesBySeq(rows);
  return { ...(data || { messages: [] }), messages: rows };
}

export async function fetchOlderMessages(
  fid: string,
  beforeSeq: unknown,
  limit: number,
): Promise<MessagePage> {
  const data = (await api(
    `/frames/${encodeURIComponent(fid)}/messages?limit=${limit}&before_seq=${encodeURIComponent(String(beforeSeq))}`,
  )) as MessagePage | null;
  const rows = (data && data.messages) || [];
  sortMessagesBySeq(rows);
  return { ...(data || { messages: [] }), messages: rows };
}

export async function fetchAllMessages(fid: string): Promise<MessagePage> {
  const first = await fetchRecentMessages(fid, MESSAGE_PAGE_SIZE);
  let rows = first.messages || [];
  let cursor = first.next_before_seq;
  let earlier = !!first.has_earlier;
  let pages = 1;
  while (shouldWalkEarlier(earlier, cursor, pages)) {
    const older = await fetchOlderMessages(fid, cursor, MESSAGE_PAGE_SIZE);
    rows = prependOlderMessages(older.messages || [], rows);
    cursor = older.next_before_seq;
    earlier = !!older.has_earlier;
    pages += 1;
    if (pages >= MESSAGE_WALK_MAX_PAGES) break;
  }
  return { messages: rows, complete: !earlier };
}

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
    if (gen !== _openGen.value) return;
    const beforeHeight = host.scrollHeight;
    const beforeTop = host.scrollTop;
    const holder = document.createDocumentFragment();
    (data.messages || []).forEach((mm) => insertMessageByTime(renderStored(mm, holder)));
    host.scrollTop = beforeTop + (host.scrollHeight - beforeHeight);
    msgCursor.value = data.next_before_seq != null ? data.next_before_seq : null;
    msgHasEarlier.value = !!data.has_earlier;
  } catch (e) {
    hint(t("conv.loadEarlierFailed", apiErrorText(e)), true);
  } finally {
    _msgEarlierLoading.value = false;
    paintEarlierControl();
  }
}
