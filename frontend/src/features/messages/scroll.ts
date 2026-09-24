/**
 * Follow-scroll + jump pill, coalesced onto one rAF.
 *
 * Port of app.js `messagesAtBottom` / `paintJumpPill` / `down` / `updateJumpPill`
 * (12934-12942) and the unthrottled `$("#messages").onscroll` (13384). `down()`
 * no longer writes `scrollTop` synchronously (that was the forced layout at
 * 5509).
 */

import { _messagesFollow } from "../../stores/ui";
import { $, messagesHost } from "./dom";
import { cancelFrame, scheduleFrame } from "./raf";

let scrollRaf = 0;
let pendingDown = false;
let pendingForce = false;
let pendingMeasure = false;
let boundHost: HTMLElement | null = null;
let boundPill: HTMLElement | null = null;

export function messagesAtBottom(
  m: HTMLElement | null | undefined,
  pad?: number,
): boolean {
  return !m || m.scrollHeight - m.scrollTop - m.clientHeight < (pad || 80);
}

export function paintJumpPill(): void {
  const m = messagesHost();
  const pill = $("#jump-pill");
  if (!m || !pill) return;
  pill.classList.toggle("hidden", messagesAtBottom(m, 60));
}

function flushScroll(): void {
  scrollRaf = 0;
  const m = messagesHost();
  if (!m) return;
  if (pendingMeasure) {
    _messagesFollow.value = messagesAtBottom(m);
    pendingMeasure = false;
  }
  const follow = pendingDown;
  const force = pendingForce;
  pendingDown = false;
  pendingForce = false;
  // Only down() scrolls. A scroll event (or updateJumpPill) measures and
  // paints the pill, as app.js's onscroll did: scrolling there too snapped a
  // reader who had moved up by less than the 80px pad straight back down.
  if (follow && (force || _messagesFollow.value !== false)) {
    m.scrollTop = m.scrollHeight;
    _messagesFollow.value = true;
  }
  paintJumpPill();
}

function scheduleScroll(): void {
  if (scrollRaf) return;
  scrollRaf = scheduleFrame(() => {
    flushScroll();
  });
}

/** app.js:12936. `force` pins follow and jumps even if the user had scrolled up. */
export function down(force?: boolean): void {
  pendingDown = true;
  if (force) pendingForce = true;
  scheduleScroll();
}

/** app.js:12942. Scroll-listener entry: sample follow, then paint the pill. */
export function updateJumpPill(): void {
  pendingMeasure = true;
  scheduleScroll();
}

function onMessagesScroll(): void {
  pendingMeasure = true;
  scheduleScroll();
}

function onJumpPillClick(): void {
  down(true);
}

/**
 * Bind the throttled scroll listener and the jump pill. Call it after the
 * Shell has rendered `#messages` (the workbench bind, not module import): a
 * host created before `render()` is reused by Preact for the Shell's first
 * `<div>`, which is how this listener once ended up on `#conn-banner`.
 */
export function bindMessageScroll(host?: HTMLElement | null): void {
  unbindMessageScroll();
  const m = host || messagesHost();
  const pill = $("#jump-pill");
  if (m) {
    m.addEventListener("scroll", onMessagesScroll, { passive: true });
    boundHost = m;
  }
  if (pill) {
    pill.addEventListener("click", onJumpPillClick);
    boundPill = pill;
  }
}

export function unbindMessageScroll(): void {
  if (boundHost) {
    boundHost.removeEventListener("scroll", onMessagesScroll);
    boundHost = null;
  }
  if (boundPill) {
    boundPill.removeEventListener("click", onJumpPillClick);
    boundPill = null;
  }
  cancelFrame(scrollRaf);
  scrollRaf = 0;
  pendingDown = false;
  pendingForce = false;
  pendingMeasure = false;
}

/** Flush a pending rAF immediately (tests). */
export function flushScrollNow(): void {
  cancelFrame(scrollRaf);
  flushScroll();
}
