/**
 * The one clipboard write behind every Copy control.
 *
 * `navigator.clipboard` exists only in a secure context (https or
 * localhost). A LAN deployment served over plain http has none, and a
 * permission prompt can refuse it anywhere, yet every Copy button used to
 * report success regardless: one called `writeText` without awaiting it,
 * one wrapped the call in a try/catch that cannot see an async rejection,
 * and one detached the method from `navigator.clipboard`, which throws
 * "Illegal invocation" in every browser.
 *
 * `copyText` resolves `true` only when a write was confirmed. It calls the
 * async API as a method, and falls back to a selection copy, which still
 * works over http while the click's user activation lasts.
 */

import { LANG } from "../../i18n/runtime";

export async function copyText(text: string): Promise<boolean> {
  const clip = typeof navigator !== "undefined" ? navigator.clipboard : undefined;
  if (clip && typeof clip.writeText === "function") {
    try {
      await clip.writeText(text);
      return true;
    } catch {
      /* refused or unavailable: try the selection copy */
    }
  }
  return copyViaSelection(text);
}

function copyViaSelection(text: string): boolean {
  if (typeof document === "undefined" || !document.body || typeof document.execCommand !== "function") {
    return false;
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.setAttribute("aria-hidden", "true");
  area.style.position = "fixed";
  area.style.top = "0";
  area.style.left = "0";
  area.style.opacity = "0";
  area.style.pointerEvents = "none";
  const previous = document.activeElement as HTMLElement | null;
  document.body.appendChild(area);
  let ok = false;
  try {
    area.select();
    ok = document.execCommand("copy") === true;
  } catch {
    ok = false;
  } finally {
    area.remove();
    if (previous && typeof previous.focus === "function") previous.focus({ preventScroll: true });
  }
  return ok;
}

/** Shown when neither write path was confirmed. */
export function copyFailedText(): string {
  return LANG === "en"
    ? "Copy failed: the browser blocked clipboard access. Select the text and copy it manually."
    : "复制失败：浏览器拒绝访问剪贴板，请手动选中后复制。";
}
