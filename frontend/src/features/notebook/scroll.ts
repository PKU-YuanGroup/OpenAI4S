/**
 * Notebook scroll-follow and reading-delay. Verbatim semantics of
 * app.js:10339-10350 (measure + listener) and app.js:9900-9908 (nbRender gate).
 *
 * Threshold is 120px from the bottom. While a turn runs and the user has
 * scrolled up, structural re-renders pause (`_nbDirty`); returning to the
 * bottom flushes. Chunks still update the matching cell output signal.
 */

import { _nbDirty, _nbReading, _nbSched } from "../../stores/notebook";
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

/**
 * app.js:10344-10350. Bind once (`_nbScrollBound`). Tracks `_nbReading` and
 * flushes a deferred render when the user returns to the bottom.
 */
export function onNotebookScroll(body: ScrollBox): void {
  const atBottom = isNearBottom(body);
  _nbReading.value = !atBottom;
  if (atBottom && _nbDirty.value) {
    _nbDirty.value = false;
    nbRender();
  }
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
      body.scrollTop = body.scrollHeight;
    });
  }
}
