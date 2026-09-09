/**
 * Message page helpers. Port of app.js:6926-6961.
 *
 * F-10's openConversation loop (7166-7181) needs the newest page. F-13
 * (load-earlier / export) should import these rather than re-fetch.
 */

import { API } from "../ws/connect";
import { candidateIdentity } from "./identity";
import { ApiError } from "../sessions/api";

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
  if (!r.ok) throw new ApiError(body, r.status);
  return body;
}

export function messageIdentity(row: Record<string, unknown>): string {
  const identity = candidateIdentity(row);
  return identity.messageId ? `id:${identity.messageId}`
    : typeof row.seq === "number" && Number.isFinite(row.seq) ? `seq:${row.seq}` : "";
}
export function uniqueMessages(rows: Array<Record<string, unknown>>): Array<Record<string, unknown>> {
  const seen = new Set<string>();
  return rows.filter((row) => {
    const key = messageIdentity(row);
    if (!key) return true;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function recordRows(value: unknown, field: string): Array<Record<string, unknown>> {
  const record = value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : null;
  const rows = record?.[field];
  if (!Array.isArray(rows) || rows.some((row) => !row || typeof row !== "object" || Array.isArray(row))) {
    throw new ApiError({ error: `Invalid ${field} response`, code: "invalid_history_response" }, 200);
  }
  return rows;
}

function messagePage(value: unknown): MessagePage {
  const rows = recordRows(value, "messages");
  const record = value as Record<string, unknown>;
  if (rows.some((row) => typeof row.role !== "string" || !row.role ||
      !(typeof row.content === "string" || Array.isArray(row.content) &&
        row.content.every((block) => block && typeof block === "object" && !Array.isArray(block))))) {
    throw new ApiError({ error: "Invalid message record", code: "invalid_history_response" }, 200);
  }
  if (record.has_earlier != null && typeof record.has_earlier !== "boolean") {
    throw new ApiError({ error: "Invalid message paging response", code: "invalid_history_response" }, 200);
  }
  return { ...value as MessagePage, messages: [...rows].sort((a, b) => Number(a.seq || 0) - Number(b.seq || 0)) };
}

/** Newest page, then sorted back into reading order. app.js:6928-6932. */
export async function fetchRecentMessages(
  fid: string,
  limit: number = MESSAGE_PAGE_SIZE,
): Promise<MessagePage> {
  const data = (await apiGet(
    `/frames/${encodeURIComponent(fid)}/messages?newest_first=1&limit=${limit}`,
  )) as MessagePage | null;
  return messagePage(data);
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
  return messagePage(data);
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
