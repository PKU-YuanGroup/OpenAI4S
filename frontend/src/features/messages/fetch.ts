/**
 * Message page helpers. Port of app.js:6926-6961.
 *
 * F-10's openConversation loop (7166-7181) needs the newest page. F-13
 * (load-earlier / export) should import these rather than re-fetch.
 */

import { API } from "../ws/connect";

export const MESSAGE_PAGE_SIZE = 300;
export const MESSAGE_WALK_MAX_PAGES = 200;

export type MessagePage = {
  messages: Array<Record<string, unknown>>;
  next_before_seq?: unknown;
  has_earlier?: unknown;
  complete?: boolean;
  [key: string]: unknown;
};

function assertApiPath(p: string): void {
  if (typeof p !== "string" || p[0] !== "/" || p[1] === "/") {
    throw new Error("invalid api path");
  }
}

export async function apiGet(p: string): Promise<unknown> {
  assertApiPath(p);
  const r = await fetch(API + p, {
    headers: { "content-type": "application/json" },
  });
  const raw = await r.text();
  let body: unknown = null;
  try {
    body = raw ? JSON.parse(raw) : null;
  } catch {
    body = raw;
  }
  if (!r.ok) {
    const err =
      body && typeof body === "object" && "error" in body
        ? String((body as { error: unknown }).error)
        : "HTTP " + r.status;
    throw new Error(err);
  }
  return body;
}

function sortMessages(
  rows: Array<Record<string, unknown>>,
): Array<Record<string, unknown>> {
  rows.sort((a, b) => Number(a.seq || 0) - Number(b.seq || 0));
  return rows;
}

/** Newest page, then sorted back into reading order. app.js:6928-6932. */
export async function fetchRecentMessages(
  fid: string,
  limit: number = MESSAGE_PAGE_SIZE,
): Promise<MessagePage> {
  const data = (await apiGet(
    `/frames/${encodeURIComponent(fid)}/messages?newest_first=1&limit=${limit}`,
  )) as MessagePage | null;
  const rows = (data && data.messages) || [];
  return { ...(data || {}), messages: sortMessages(rows) };
}

/** One page older than `beforeSeq`. app.js:6939-6943. */
export async function fetchOlderMessages(
  fid: string,
  beforeSeq: unknown,
  limit: number = MESSAGE_PAGE_SIZE,
): Promise<MessagePage> {
  const data = (await apiGet(
    `/frames/${encodeURIComponent(fid)}/messages?limit=${limit}&before_seq=${encodeURIComponent(String(beforeSeq))}`,
  )) as MessagePage | null;
  const rows = (data && data.messages) || [];
  return { ...(data || {}), messages: sortMessages(rows) };
}

/** Whole conversation, newest-page-first walk, oldest-first result. app.js:6952-6961. */
export async function fetchAllMessages(
  fid: string,
): Promise<{ messages: Array<Record<string, unknown>>; complete: boolean }> {
  const first = await fetchRecentMessages(fid, MESSAGE_PAGE_SIZE);
  let rows = first.messages || [];
  let cursor = first.next_before_seq;
  let earlier = !!first.has_earlier;
  let pages = 1;
  while (earlier && cursor != null && pages < MESSAGE_WALK_MAX_PAGES) {
    const older = await fetchOlderMessages(fid, cursor, MESSAGE_PAGE_SIZE);
    rows = (older.messages || []).concat(rows);
    cursor = older.next_before_seq;
    earlier = !!older.has_earlier;
    pages += 1;
  }
  return { messages: rows, complete: !earlier };
}
