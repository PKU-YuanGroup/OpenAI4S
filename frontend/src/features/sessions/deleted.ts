/**
 * The session on screen was deleted in another tab or window (SYNC-17).
 *
 * The `session_status` `deleted` directory event reached every sidebar, which
 * dropped the row, but the tab showing that session kept it open: a usable
 * composer, the old title, no notice. A message sent from there was accepted
 * and ran as an orphan turn. The server now refuses it (404); this leaves the
 * session as soon as the event says it is gone.
 */

import { LANG } from "../../i18n/runtime";
import { currentId, project, sessions } from "../../stores/session";
import { hint } from "./chrome";

const COPY: Record<"en" | "zh", string> = {
  en: "This session was deleted in another window or tab. Nothing more can be sent to it; anything you typed is still in the message box.",
  zh: "该会话已在另一个窗口或标签页中删除，无法再向它发送消息；你输入的内容仍保留在输入框里。",
};

export function sessionDeletedElsewhereText(): string {
  return COPY[LANG === "en" ? "en" : "zh"];
}

/**
 * Deletions this tab started. Its own `deleteSession` moves on to the next
 * session, and the event for that deletion -- which can arrive before the
 * DELETE's response -- must not also say it happened somewhere else.
 */
const ownDeletions = new Set<string>();

export function expectOwnDeletion(fid: string): void {
  ownDeletions.add(fid);
}

export function forgetOwnDeletion(fid: string): void {
  ownDeletions.delete(fid);
}

type OpenConversation = (fid: string, pid?: string | null) => unknown;

/**
 * Leave a session another tab deleted. Re-opening it lands on the "Session
 * not found" view (its read is a 404 now), which disables the composer
 * without clearing it and offers the way back; the hint says why.
 */
export async function leaveDeletedSession(fid: string, projectId?: string | null): Promise<void> {
  if (!fid || ownDeletions.delete(fid)) return;
  if (currentId.value !== fid) return;
  // Out of the list first: an open finds the row there and would read the
  // deleted session's (empty) history instead of asking whether it exists.
  const rows = sessions.value as Array<{ id?: unknown }>;
  if (Array.isArray(rows) && rows.some((row) => row?.id === fid)) {
    sessions.value = rows.filter((row) => row?.id !== fid);
  }
  const open = (globalThis as Record<string, unknown>).openConversation;
  if (typeof open !== "function") return;
  try {
    await (open as OpenConversation)(fid, projectId || project.value || null);
  } catch {
    /* the notice below is still the one thing worth showing */
  }
  if (currentId.value === fid) hint(sessionDeletedElsewhereText(), true);
}
