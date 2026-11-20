/**
 * Notebook scroll-follow and reading-delay. Verbatim semantics of
 * app.js:10339-10350 (measure + listener) and app.js:9900-9908 (nbRender gate).
 *
 * Threshold is 120px from the bottom. While a turn runs and the user has
 * scrolled up, structural re-renders pause (`_nbDirty`); returning to the
 * bottom flushes. Chunks still update the matching cell output signal.
 */

import { effect } from "@preact/signals";
import { _nbDirty, _nbReading, _nbSched } from "../../stores/notebook";
import { currentId } from "../../stores/session";
import { running } from "../../stores/stream";
import { activeTab, dock } from "../../stores/ui";
import type { ScrollBox } from "./types";

export const NB_FOLLOW_PX = 120;

export function isNearBottom(body: ScrollBox, threshold = NB_FOLLOW_PX): boolean {
  return body.scrollHeight - body.scrollTop - body.clientHeight < threshold;
}

/** app.js:10340 — measured BEFORE tearing the pane down. */
export function measureNotebookFollow(body: ScrollBox | null | undefined): boolean {
  return !body || isNearBottom(body);
}

function notebookOnScreen(): boolean {
  const d = dock.value as { open?: boolean } | null;
  return !!(d && d.open && activeTab.value === "notebook");
}

/**
 * app.js:10344-10350. Bind once (`_nbScrollBound`). Tracks `_nbReading` and
 * flushes a deferred render when the user returns to the bottom.
 *
 * The body that scrolls is `.dock-body`, shared by every dock pane, so
 * scrolling the Timeline or Files says nothing about the Notebook: while it
 * is off screen it is not being read. Otherwise opening the Notebook during
 * a turn after scrolling another pane showed a held, stale cell list.
 */
export function onNotebookScroll(body: ScrollBox): void {
  if (!notebookOnScreen()) {
    _nbReading.value = false;
    return;
  }
  // Our own move to the end (opening, following output) is not the reader
  // scrolling away, even if more output landed before the event arrived.
  const own = selfScrolled;
  selfScrolled = false;
  const atBottom = own || isNearBottom(body);
  _nbReading.value = !atBottom;
  if (atBottom && bottomHook) bottomHook();
  if (atBottom && _nbDirty.value) {
    _nbDirty.value = false;
    nbRender();
  }
}

let bottomHook: (() => void) | null = null;

/** cells.ts notes what the reader has seen each time the view is at the bottom (NB-2). */
export function setNotebookBottomHook(fn: (() => void) | null): void {
  bottomHook = fn;
}

export function bindNotebookScroll(body: ScrollBox | null | undefined): void {
  if (!body || body._nbScrollBound) return;
  body._nbScrollBound = true;
  if (typeof body.addEventListener === "function") {
    body.addEventListener(
      "scroll",
      () => {
        onNotebookScroll(body);
      },
      { passive: true },
    );
  }
}

let visibilityWatch: (() => void) | null = null;

/**
 * M-54: the Notebook opened at cell 1 and stayed there, so the cell being
 * run was a long scroll away and new cells arrived out of sight. One that
 * comes on screen, or shows another session, starts at its latest cell.
 */
let jumpPending = true;
let selfScrolled = false;

/**
 * Clear `_nbReading` whenever the Notebook leaves the screen, so it comes
 * back painting the current list (install.ts), and open it at its latest
 * cell. One watch per page.
 */
export function watchNotebookVisibility(): void {
  if (visibilityWatch) return;
  let shown = false;
  let session: unknown = null;
  visibilityWatch = effect(() => {
    const onScreen = notebookOnScreen();
    const sid = currentId.value;
    if (!onScreen) _nbReading.value = false;
    else if (!shown || sid !== session) jumpPending = true;
    shown = onScreen;
    session = sid;
  });
}

/**
 * Whether this paint should open the Notebook at its latest cell. Taken only
 * when there is a cell to show: a session's list arrives after it opens.
 */
export function takeJumpToLatest(hasCells: boolean): boolean {
  if (!jumpPending || !hasCells) return false;
  jumpPending = false;
  return true;
}

/** Move the dock body to its end, as our own scroll rather than the reader's. */
export function scrollNotebookToEnd(body: ScrollBox | null | undefined): void {
  if (!body) return;
  if (body.scrollHeight - body.scrollTop - body.clientHeight <= 1) return;
  selfScrolled = true;
  body.scrollTop = body.scrollHeight;
}

/**
 * Whether the reader is following the run: on screen, a turn running, and
 * not scrolled away from the end since. Growing output used to push the view
 * off the end by itself, after which no new cell was followed (M-57).
 */
export function followingRun(): boolean {
  return running.value && !_nbReading.value && notebookOnScreen();
}

let outputFollow = false;

/** Keep a following reader at the end while a cell's output streams in. */
export function followStreamingOutput(): void {
  if (outputFollow || !followingRun() || typeof document === "undefined") return;
  outputFollow = true;
  const raf =
    typeof requestAnimationFrame === "function"
      ? requestAnimationFrame
      : (cb: () => void) => setTimeout(cb, 0);
  raf(() => {
    outputFollow = false;
    if (!followingRun()) return;
    const nb = document.getElementById("dock-notebook");
    scrollNotebookToEnd(nb ? (nb.parentElement as unknown as ScrollBox | null) : null);
  });
}

let renderImpl: (() => void) | null = null;

/** Notebook.tsx assigns the Preact mount. Tests may stub this. */
export function setNotebookRenderImpl(fn: (() => void) | null): void {
  renderImpl = fn;
}

/** app.js:9900-9908 */
export function nbRender(): void {
  const d = dock.value as { open?: boolean } | null;
  if (!(d && d.open && activeTab.value === "notebook")) return;
  if (running.value && _nbReading.value) {
    _nbDirty.value = true;
    return;
  }
  if (_nbSched.value) return;
  _nbSched.value = true;
  const raf =
    typeof requestAnimationFrame === "function"
      ? requestAnimationFrame
      : (cb: () => void) => setTimeout(cb, 0);
  raf(() => {
    _nbSched.value = false;
    if (renderImpl) renderImpl();
  });
}

export function followLiveOutput(body: ScrollBox | null | undefined, follow: boolean): void {
  if (running.value && follow && body) {
    const raf =
      typeof requestAnimationFrame === "function"
        ? requestAnimationFrame
        : (cb: () => void) => setTimeout(cb, 0);
    raf(() => {
      scrollNotebookToEnd(body);
    });
  }
}
