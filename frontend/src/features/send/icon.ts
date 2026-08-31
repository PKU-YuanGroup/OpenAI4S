/**
 * Step / plan / permission icons. Sessions already paints a subset; this
 * file adds the names those menus never needed (globe, list-check, …)
 * rather than editing F-13's table.
 */

import { el } from "../messages/dom";
import { icon as sessionIcon } from "../sessions/icon";

const EXTRA: Record<string, string> = {
  globe:
    '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/>',
  package:
    '<path d="M11 21.73a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73z"/><path d="M12 22V12"/><polyline points="3.29 7 12 12 20.71 7"/><path d="m7.5 4.27 9 5.15"/>',
  "list-check":
    '<path d="M11 18H3"/><path d="M11 12H3"/><path d="M11 6H3"/><path d="m15 18 2 2 4-4"/><path d="m15 6 2 2 4-4"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
  lock: '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  circle: '<circle cx="12" cy="12" r="9"/>',
  file: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/>',
  "message-square":
    '<path d="M22 17a2 2 0 0 1-2 2H6l-4 4V4a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
};

export function icon(name: string, size?: number, cls?: string): string {
  const path = EXTRA[name];
  if (!path) return sessionIcon(name, size, cls);
  const s = size || 16;
  return (
    `<svg class="ic-svg${cls ? " " + cls : ""}" width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">` +
    path +
    "</svg>"
  );
}

export function iconEl(name: string, size?: number, cls?: string): Node {
  const wrap = el("span", "ic");
  wrap.innerHTML = icon(name, size, cls);
  return wrap.firstChild || wrap;
}
