/**
 * Local copies of app.js DOM helpers used by the Timeline island.
 * Icons are a closed allowlist (app.js:7-77); innerHTML is the original
 * SVG injection, not a template compiler.
 */

const ICONS: Record<string, string> = {
  x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  sliders:
    '<line x1="21" x2="14" y1="4" y2="4"/><line x1="10" x2="3" y1="4" y2="4"/><line x1="21" x2="12" y1="12" y2="12"/><line x1="8" x2="3" y1="12" y2="12"/><line x1="21" x2="16" y1="20" y2="20"/><line x1="12" x2="3" y1="20" y2="20"/><line x1="14" x2="14" y1="2" y2="6"/><line x1="8" x2="8" y1="10" y2="14"/><line x1="16" x2="16" y1="18" y2="22"/>',
  refresh:
    '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
  stop: '<circle cx="12" cy="12" r="10"/><rect width="6" height="6" x="9" y="9" rx="1"/>',
  lock: '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  "message-square":
    '<path d="M22 17a2 2 0 0 1-2 2H6l-4 4V4a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  terminal:
    '<polyline points="4 17 10 11 4 5"/><line x1="12" x2="20" y1="19" y2="19"/>',
  users:
    '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
};

export function $(sel: string): HTMLElement | null {
  if (typeof document === "undefined") return null;
  return document.querySelector(sel);
}

export function el(
  tag: string,
  className?: string | null,
  text?: string | number | null,
): HTMLElement {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text != null) e.textContent = String(text);
  return e;
}

export function icon(name: string, size?: number, cls?: string): string {
  return `<svg class="ic-svg${cls ? " " + cls : ""}" width="${size || 16}" height="${size || 16}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ""}</svg>`;
}

export function iconEl(name: string, size?: number, cls?: string): Node {
  const s = el("span", "ic");
  s.innerHTML = icon(name, size, cls);
  return s.firstChild || s;
}

export function ghostIconBtn(name: string, title?: string): HTMLButtonElement {
  const b = el("button", "icon-ghost") as HTMLButtonElement;
  b.innerHTML = icon(name, 16);
  if (title) b.title = title;
  return b;
}

export function svgElement(
  name: string,
  attrs: Record<string, string | number> | null = null,
): SVGElement {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attrs || {}).forEach(([key, value]) =>
    node.setAttribute(key, String(value)),
  );
  return node;
}

export function bytes(b: number): string {
  b = b || 0;
  if (b < 1024) return b + " B";
  if (b < 1048576) return (b / 1024).toFixed(1) + " KB";
  return (b / 1048576).toFixed(1) + " MB";
}
