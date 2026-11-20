/**
 * SYNC-24: a composer draft belongs to its session.
 *
 * The composer is one textarea shared by every session, so a half-written
 * question typed in A was still there after New or a click on B -- and was
 * easy to send to the wrong session -- while a reload emptied it. The draft
 * is now kept per session: switching saves the one on screen and puts back
 * the next session's (with its `@` pins), a reload restores it, and sending
 * clears it.
 *
 * Kept in memory for switching and in `localStorage` (`os-composer-drafts`)
 * for a reload: at most `MAX_DRAFTS` sessions, a draft longer than
 * `MAX_DRAFT_CHARS` is not written there, and every storage access is in a
 * try/catch -- blocked site data only costs the reload copy.
 *
 * A session created from this composer (the first upload or Send, while no
 * session was open) takes the draft along: `carryDraftTo` is called before
 * the new id is published, so the switch does not empty the composer under
 * the text being sent.
 */

import { effect, untracked } from "@preact/signals";
import { currentId } from "../../stores/session";
import { composerMentions, restoreMentions, clearMentions, type ComposerMention } from "../autocomplete/mentions";
import { $, grow } from "./dom";

export const DRAFT_STORAGE_KEY = "os-composer-drafts";
export const MAX_DRAFTS = 50;
export const MAX_DRAFT_CHARS = 20_000;

type Draft = { text: string; at: number; pins?: ComposerMention[] };

const drafts = new Map<string, Draft>();
/** The session whose draft the composer holds; null for no session. */
let shown: string | null = null;
/** The composer of no session yet. */
let unsessioned: Draft = { text: "", at: 0 };
/** A session created from this composer, to receive its draft. */
let carry: string | null = null;
let loaded = false;
let stopFollowing: (() => void) | null = null;

function composer(): HTMLTextAreaElement | null {
  return $("#composer") as HTMLTextAreaElement | null;
}

function load(): void {
  if (loaded) return;
  loaded = true;
  let raw: string | null = null;
  try {
    raw = globalThis.localStorage ? globalThis.localStorage.getItem(DRAFT_STORAGE_KEY) : null;
  } catch {
    return;
  }
  if (!raw) return;
  try {
    const parsed = JSON.parse(raw) as Record<string, { t?: unknown; at?: unknown }>;
    for (const [fid, entry] of Object.entries(parsed || {})) {
      if (!fid || !entry || typeof entry.t !== "string" || !entry.t) continue;
      if (!drafts.has(fid)) drafts.set(fid, { text: entry.t, at: Number(entry.at) || 0 });
    }
  } catch {
    /* an unreadable copy is no draft */
  }
}

function persist(): void {
  const kept = [...drafts.entries()]
    .filter(([, d]) => d.text && d.text.length <= MAX_DRAFT_CHARS)
    .sort((a, b) => b[1].at - a[1].at)
    .slice(0, MAX_DRAFTS);
  const out: Record<string, { t: string; at: number }> = {};
  for (const [fid, d] of kept) out[fid] = { t: d.text, at: d.at };
  try {
    if (!globalThis.localStorage) return;
    if (kept.length) globalThis.localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(out));
    else globalThis.localStorage.removeItem(DRAFT_STORAGE_KEY);
  } catch {
    /* blocked or full storage: the in-memory draft still switches */
  }
}

/** Keep `text` as `fid`'s draft (an empty text forgets it). */
export function saveDraft(fid: string | null, text: string): void {
  load();
  const draft: Draft = { text, at: Date.now(), pins: composerMentions(text) };
  if (!fid) {
    unsessioned = draft;
    return;
  }
  if (text) drafts.set(fid, draft);
  else drafts.delete(fid);
  persist();
}

/** `fid`'s saved draft, or "". */
export function draftFor(fid: string | null): string {
  load();
  if (!fid) return unsessioned.text;
  return drafts.get(fid)?.text || "";
}

/** The message was sent: its session has no draft any more. */
export function clearDraft(fid: string | null): void {
  load();
  if (!fid) {
    unsessioned = { text: "", at: 0 };
    return;
  }
  if (drafts.delete(fid)) persist();
}

/** A session is being created from this composer; it takes the draft along. */
export function carryDraftTo(fid: string): void {
  carry = fid;
}

/** The composer now belongs to `next`: save the draft on screen, show `next`'s. */
export function switchDraft(next: string | null): void {
  if (next === shown) return;
  const box = composer();
  const previous = shown;
  shown = next;
  if (!box) return;
  if (!previous && next && carry === next) {
    carry = null;
    saveDraft(next, box.value);
    return;
  }
  saveDraft(previous, box.value);
  load();
  const draft = next ? drafts.get(next) : unsessioned;
  clearMentions();
  box.value = (draft && draft.text) || "";
  if (draft && draft.pins && draft.pins.length) restoreMentions(draft.pins);
  grow();
  const refs = (globalThis as { renderComposerRefChips?: unknown }).renderComposerRefChips;
  if (typeof refs === "function") (refs as () => void)();
}

/** Typing saves the draft; leaving the page saves it; the open session decides whose it is. */
export function bootDrafts(): void {
  shown = currentId.value || null;
  const box = composer();
  if (box) {
    box.addEventListener("input", () => saveDraft(shown, box.value));
    if (shown && !box.value) box.value = draftFor(shown);
  }
  if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
    window.addEventListener("pagehide", () => {
      const b = composer();
      if (b) saveDraft(shown, b.value);
    });
  }
  if (stopFollowing) stopFollowing();
  stopFollowing = effect(() => {
    const next = currentId.value || null;
    untracked(() => switchDraft(next));
  });
}

/** Tests: forget everything, as a fresh page load would. */
export function _resetDrafts(): void {
  if (stopFollowing) stopFollowing();
  stopFollowing = null;
  drafts.clear();
  shown = null;
  unsessioned = { text: "", at: 0 };
  carry = null;
  loaded = false;
}
