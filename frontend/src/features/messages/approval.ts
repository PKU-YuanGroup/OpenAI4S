/**
 * A turn waiting for the user's approval is its own state, not "running".
 *
 * While an approval card was open every surface still said "running": the
 * sidebar row, the tab title, the composer's status line. The card was often
 * below the fold with the "Latest" pill over it, and a reload opened the
 * session at its newest row with the card off screen. Here the open card of
 * the session on screen drives:
 *
 * - the composer status line ("waiting for your approval"),
 * - the tab title prefix,
 * - the session's sidebar row badge (other sessions' rows come from the
 *   server's `awaiting_approval`, `sessions/load.ts` `sessionRow`),
 * - the jump pill, which points at the card while it is out of view
 *   (`scroll.ts`, which registers its painter here when it binds),
 * - and bringing the card into view when it appears or a session opens on it.
 *
 * Deliberately a leaf: it imports nothing from `sessions/` or `scroll.ts`,
 * because `sessions/chrome` → `sessions/dom` → `scroll.ts` already imports it.
 */

import { signal } from "@preact/signals";
import { isReady } from "../../compat/stub";
import { LANG, t } from "../../i18n/runtime";
import { currentId } from "../../stores/session";
import { permCards, running } from "../../stores/stream";
import { _messagesFollow } from "../../stores/ui";
import { messagesHost } from "./dom";
import { scheduleFrame } from "./raf";

const COPY: Record<"en" | "zh", Record<string, string>> = {
  en: {
    waiting: "Waiting for your approval: choose Allow or Deny on the request in the conversation",
    pill: "Approval needed",
    pillTitle: "Go to the request waiting for your approval",
    title: "(Approval needed) ",
    badge: "Approval",
    badgeTip: "Waiting for your approval. Click to open",
  },
  zh: {
    waiting: "等待你批准：请在对话中的请求卡片上选择「允许」或「拒绝」",
    pill: "待批准",
    pillTitle: "跳到等待你批准的请求",
    title: "（待批准）",
    badge: "待批准",
    badgeTip: "等待你批准，点击打开",
  },
};

export function approvalCopy(key: string): string {
  return (COPY[LANG === "zh" ? "zh" : "en"] || COPY.en)[key] || key;
}

/** The session whose open approval card is on screen, or null. */
export const approvalPendingFor = signal<string | null>(null);

type Handle = { card?: HTMLElement; resolved?: boolean };

/** The first open (unanswered) approval card in the transcript, or null. */
export function pendingApprovalCard(): HTMLElement | null {
  const reg = (permCards.value || {}) as Record<string, Handle | undefined>;
  for (const id of Object.keys(reg)) {
    const handle = reg[id];
    if (handle && !handle.resolved && handle.card && handle.card.isConnected) return handle.card;
  }
  return null;
}

/** The band above the composer the jump pill floats in. */
const PILL_BAND = 64;

/** Whether the card's buttons (its bottom edge) are on screen. */
export function approvalCardInView(card: HTMLElement, host: HTMLElement): boolean {
  if (typeof card.getBoundingClientRect !== "function" || typeof host.getBoundingClientRect !== "function") {
    return true;
  }
  const r = card.getBoundingClientRect();
  const v = host.getBoundingClientRect();
  if (!v.height) return true;
  return r.bottom <= v.bottom + 1 && r.bottom >= v.top + 40;
}

/**
 * Scroll the open card's buttons on screen, above the pill band where the
 * transcript can scroll that far. A card taller than the view keeps its
 * buttons on screen rather than its top. True when one exists.
 */
export function revealPendingApproval(): boolean {
  const card = pendingApprovalCard();
  const host = messagesHost();
  if (!card || !host) return false;
  if (!approvalCardInView(card, host) && typeof card.getBoundingClientRect === "function") {
    const r = card.getBoundingClientRect();
    const v = host.getBoundingClientRect();
    const move = r.bottom - (v.bottom - PILL_BAND);
    host.scrollTop += move;
    // Moved up to the card on purpose: newer rows must not pull the view back
    // under it. (scroll.ts counts only the reader's own input as a move up.)
    if (move < 0) _messagesFollow.value = false;
  }
  return true;
}

function paintTitle(pending: boolean): void {
  if (typeof document === "undefined") return;
  let base = String(document.title || "");
  // Strip either language's prefix, so a language switch never stacks them.
  for (const prefix of [COPY.en.title || "", COPY.zh.title || ""]) {
    if (prefix && base.startsWith(prefix)) base = base.slice(prefix.length);
  }
  document.title = pending ? approvalCopy("title") + base : base;
}

/** Badge the open session's sidebar row in place (a full repaint reads the signal). */
function paintSessionRow(fid: string | null, pending: boolean): void {
  if (typeof document === "undefined" || !fid) return;
  const list = document.querySelector("#session-list");
  const rows = list ? (Array.from(list.querySelectorAll(".session")) as HTMLElement[]) : [];
  const row = rows.find((node) => node.dataset.frameId === fid);
  if (!row) return;
  row.classList.toggle("awaiting", pending);
  if (pending) row.classList.add("running");
  const badge = row.querySelector(".s-badge") as HTMLElement | null;
  if (pending) {
    const node = badge || document.createElement("span");
    node.className = "s-badge run approval";
    node.textContent = approvalCopy("badge");
    node.title = approvalCopy("badgeTip");
    if (!badge) row.insertBefore(node, row.querySelector(".s-menu"));
  } else if (badge && badge.classList.contains("approval")) {
    // Back to what the row said before: the turn is still running.
    badge.className = "s-badge run";
    badge.textContent = t("dash.badge.running");
    badge.title = t("session.badge.runningTip");
  }
}

/** `sessions/chrome` `hint`, reached through its window name (see the header). */
function hint(text: string, spin = false): void {
  const fn = (globalThis as { hint?: unknown }).hint;
  if (isReady(fn)) (fn as (m: string, e?: boolean, s?: boolean) => void)(text, false, spin);
}

let wasPending = false;
let revealToken = 0;
let paintPill: () => void = () => {};

/** `scroll.ts` registers its pill painter when it binds to the transcript. */
export function setApprovalPillPainter(fn: () => void): void {
  paintPill = fn;
}

/**
 * Bring every surface in line with whether the open session has an open
 * approval card. `reveal` also scrolls the card into view, after the frame in
 * which a pending `down()` scrolls to the newest row.
 */
export function syncPendingApproval(opts: { reveal?: boolean } = {}): void {
  const card = pendingApprovalCard();
  const fid = currentId.value || null;
  const pending = !!card && !!fid;
  const before = approvalPendingFor.value;
  approvalPendingFor.value = pending ? fid : null;
  paintTitle(pending);
  if (before !== approvalPendingFor.value) {
    paintSessionRow(before, false);
    paintSessionRow(approvalPendingFor.value, true);
  }
  if (pending !== wasPending) {
    wasPending = pending;
    if (pending) hint(approvalCopy("waiting"));
    else if (running.value) hint(t("toast.running"), true);
  }
  if (pending && opts.reveal) {
    const token = ++revealToken;
    scheduleFrame(() => {
      if (token !== revealToken) return;
      // Following is left to the reader's own scrolling (scroll.ts): a card
      // already in view needs no scroll, and the stream keeps following.
      revealPendingApproval();
      paintPill();
    });
  } else paintPill();
}

/**
 * Open a session from somewhere else (the home page's "Approve") and bring
 * its open card into view once the transcript has painted it.
 */
export function revealApprovalWhenReady(fid: string, timeoutMs = 6000): void {
  const started = Date.now();
  const tick = (): void => {
    if (currentId.value !== fid) return;
    if (pendingApprovalCard()) {
      syncPendingApproval({ reveal: true });
      return;
    }
    if (Date.now() - started < timeoutMs) setTimeout(tick, 150);
  };
  setTimeout(tick, 0);
}

/** Test seam. */
export function resetApprovalState(): void {
  wasPending = false;
  revealToken += 1;
  approvalPendingFor.value = null;
}
