/**
 * Follow-scroll + jump pill, coalesced onto one rAF.
 *
 * Port of app.js `messagesAtBottom` / `paintJumpPill` / `down` / `updateJumpPill`
 * (12934-12942) and the unthrottled `$("#messages").onscroll` (13384). `down()`
 * no longer writes `scrollTop` synchronously (that was the forced layout at
 * 5509).
 */

import { t } from "../../i18n/runtime";
import { _messagesFollow } from "../../stores/ui";
import {
  approvalCardInView,
  approvalCopy,
  pendingApprovalCard,
  revealPendingApproval,
  setApprovalPillPainter,
} from "./approval";
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
  // An open approval card the user cannot see turns the pill into a pointer
  // to it: the turn is blocked on that card, not on the newest row.
  const card = pendingApprovalCard();
  const toApproval = !!card && !approvalCardInView(card, m);
  paintPillMode(pill, toApproval);
  pill.classList.toggle("hidden", !toApproval && messagesAtBottom(m, 60));
}

/**
 * Swap the pill's label between "Latest" and "Approval needed". The static
 * labels carry `data-i18n` for a language switch's repaint; while the pill
 * points at a card those attributes are parked, so a repaint cannot put
 * "Latest" back over it.
 */
let pillToApproval = false;

function paintPillMode(pill: HTMLElement, toApproval: boolean): void {
  if (!toApproval && !pillToApproval) return;
  pillToApproval = toApproval;
  const label = pill.querySelector(".jp-lbl") as HTMLElement | null;
  pill.classList.toggle("approval", toApproval);
  if (toApproval) {
    if (label) {
      if (label.dataset.i18n) {
        label.dataset.i18nParked = label.dataset.i18n;
        delete label.dataset.i18n;
      }
      label.textContent = approvalCopy("pill");
    }
    if (pill.dataset.i18nTitle) {
      pill.dataset.i18nTitleParked = pill.dataset.i18nTitle;
      delete pill.dataset.i18nTitle;
    }
    pill.title = approvalCopy("pillTitle");
    return;
  }
  if (label) {
    if (label.dataset.i18nParked) {
      label.dataset.i18n = label.dataset.i18nParked;
      delete label.dataset.i18nParked;
    }
    label.textContent = t("conv.jumpLastLabel");
  }
  if (pill.dataset.i18nTitleParked) {
    pill.dataset.i18nTitle = pill.dataset.i18nTitleParked;
    delete pill.dataset.i18nTitleParked;
  }
  pill.title = t("conv.jumpLast");
}

/** Where the last flush left the view, to tell a reader's move from growth. */
let lastScrollTop = 0;

/**
 * When the reader last moved the transcript by hand -- a wheel, a touch, a
 * scrolling key, or a press on the scroll box (its scrollbar). The browser
 * lowers `scrollTop` by itself too: scroll anchoring holds the view when
 * content above it shrinks (a stored card moving into a replayed turn, a
 * collapsed card replacing its placeholder size). In the frame after a
 * reopened running session jumps to its end, that drop and the turn's growth
 * below read as the reader scrolling up, and the view stopped following.
 */
let readerInputAt = Number.NEGATIVE_INFINITY;
let readerPressing = false;
const READER_INPUT_MS = 1000;
const SCROLL_KEYS = new Set(["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "]);
const READER_EVENTS = ["wheel", "touchstart", "touchmove"] as const;
let boundDoc: Document | null = null;

function onReaderInput(): void {
  readerInputAt = Date.now();
}

function onReaderPress(): void {
  readerPressing = true;
  onReaderInput();
}

function onReaderRelease(): void {
  if (!readerPressing) return;
  readerPressing = false;
  onReaderInput();
}

function onReaderKey(event: Event): void {
  if (SCROLL_KEYS.has((event as KeyboardEvent).key)) onReaderInput();
}

function readerMoving(): boolean {
  return readerPressing || Date.now() - readerInputAt < READER_INPUT_MS;
}

function flushScroll(): void {
  scrollRaf = 0;
  const m = messagesHost();
  if (!m) return;
  if (pendingMeasure) {
    // Following stops only when the reader moves up. Content that grew below
    // the view (a gallery's images arriving, a step card opening) leaves the
    // view short of the bottom without anyone scrolling; measuring "at the
    // bottom?" then dropped follow, and the stream stopped following until
    // the user pressed "Latest" again. A move up the reader did not make is
    // the browser's own (see `readerInputAt`).
    if (messagesAtBottom(m)) _messagesFollow.value = true;
    else if (m.scrollTop < lastScrollTop - 1 && readerMoving()) _messagesFollow.value = false;
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
  lastScrollTop = m.scrollTop;
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
  if (pillToApproval && revealPendingApproval()) {
    paintJumpPill();
    return;
  }
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
  // An open approval card repaints the pill (approval.ts cannot import this
  // module: sessions/chrome -> sessions/dom -> here already imports it).
  setApprovalPillPainter(paintJumpPill);
  const m = host || messagesHost();
  const pill = $("#jump-pill");
  if (m) {
    m.addEventListener("scroll", onMessagesScroll, { passive: true });
    for (const type of READER_EVENTS) m.addEventListener(type, onReaderInput, { passive: true });
    m.addEventListener("pointerdown", onReaderPress, { passive: true });
    const doc = typeof document !== "undefined" ? document : null;
    if (doc && typeof doc.addEventListener === "function") {
      doc.addEventListener("pointerup", onReaderRelease, true);
      doc.addEventListener("pointercancel", onReaderRelease, true);
      doc.addEventListener("keydown", onReaderKey, true);
      boundDoc = doc;
    }
    boundHost = m;
    lastScrollTop = m.scrollTop || 0;
    watchGrowth(m);
  }
  if (pill) {
    pill.addEventListener("click", onJumpPillClick);
    boundPill = pill;
  }
}

let growthObserver: ResizeObserver | null = null;
let childObserver: MutationObserver | null = null;

/** While following, a transcript row that grows keeps the view at the bottom. */
function keepFollowing(): void {
  if (_messagesFollow.value !== false) down();
}

/**
 * Content that grows without a new chunk -- a figure loading, a gallery
 * filling in, a step card opening -- left a following view short of the
 * bottom. Every row of the transcript is watched for a size change, and rows
 * added later are watched as they arrive.
 */
function watchGrowth(m: HTMLElement): void {
  if (typeof ResizeObserver !== "function") return;
  const resize = new ResizeObserver(keepFollowing);
  growthObserver = resize;
  const watch = (node: Node): void => {
    if (node && node.nodeType === 1) resize.observe(node as Element);
  };
  const kids = (m as { children?: ArrayLike<Node> }).children;
  if (kids) Array.from(kids).forEach(watch);
  if (typeof MutationObserver !== "function") return;
  childObserver = new MutationObserver((records) => {
    for (const record of records) {
      record.addedNodes.forEach(watch);
      record.removedNodes.forEach((node) => {
        if (node.nodeType === 1) resize.unobserve(node as Element);
      });
    }
  });
  childObserver.observe(m, { childList: true });
}

export function unbindMessageScroll(): void {
  if (boundHost) {
    boundHost.removeEventListener("scroll", onMessagesScroll);
    for (const type of READER_EVENTS) boundHost.removeEventListener(type, onReaderInput);
    boundHost.removeEventListener("pointerdown", onReaderPress);
    boundHost = null;
  }
  if (boundDoc) {
    boundDoc.removeEventListener("pointerup", onReaderRelease, true);
    boundDoc.removeEventListener("pointercancel", onReaderRelease, true);
    boundDoc.removeEventListener("keydown", onReaderKey, true);
    boundDoc = null;
  }
  readerInputAt = Number.NEGATIVE_INFINITY;
  readerPressing = false;
  if (boundPill) {
    boundPill.removeEventListener("click", onJumpPillClick);
    boundPill = null;
  }
  growthObserver?.disconnect();
  growthObserver = null;
  childObserver?.disconnect();
  childObserver = null;
  cancelFrame(scrollRaf);
  scrollRaf = 0;
  pendingDown = false;
  pendingForce = false;
  pendingMeasure = false;
  pillToApproval = false;
}

/** Flush a pending rAF immediately (tests). */
export function flushScrollNow(): void {
  cancelFrame(scrollRaf);
  flushScroll();
}
