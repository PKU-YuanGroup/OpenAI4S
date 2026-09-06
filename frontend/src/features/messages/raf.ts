/**
 * rAF with a setTimeout fallback so Vitest (node, no rAF) and the browser
 * share one scheduler. Scroll, markdown flush, and framed history all go
 * through here so they coalesce on the same frame.
 */

export type FrameFn = (time: number) => void;

export function scheduleFrame(cb: FrameFn): number {
  if (typeof requestAnimationFrame === "function") {
    return requestAnimationFrame(cb);
  }
  return setTimeout(() => cb(0), 16) as unknown as number;
}

export function cancelFrame(id: number | null | undefined): void {
  if (id == null) return;
  if (typeof cancelAnimationFrame === "function") {
    cancelAnimationFrame(id);
    return;
  }
  clearTimeout(id);
}
