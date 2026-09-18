/**
 * Small DOM helpers used by the F-20 chrome lane.
 * `el` / `$` match app.js:3-4; `ago` 12918; `hint` 12920; `grow` 12941.
 * Icon paths come from the shared table in features/icons/paths.ts.
 */

import { iconSvg } from "../icons/paths";

export function $(sel: string): HTMLElement | null {
  if (typeof document === "undefined") return null;
  return document.querySelector(sel);
}

export function byId(id: string): HTMLElement | null {
  if (typeof document === "undefined") return null;
  return document.getElementById(id);
}

export function el(tag: string, className?: string | null, text?: string | null): HTMLElement {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

export function icon(name: string, size?: number, cls?: string): string {
  return iconSvg(name, size, cls);
}

export function iconEl(name: string, size?: number, cls?: string): Node {
  const wrap = el("span", "ic");
  wrap.innerHTML = icon(name, size, cls);
  return wrap.firstChild || wrap;
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "";
  const ts = new Date(iso).getTime();
  if (isNaN(ts)) return "";
  const d = (Date.now() - ts) / 1000;
  if (d < 60) return "just now";
  if (d < 3600) return (d / 60 | 0) + "m";
  if (d < 86400) return (d / 3600 | 0) + "h";
  return (d / 86400 | 0) + "d";
}

export function hint(message: string, err?: boolean, spin?: boolean): void {
  const h = $("#composer-hint");
  if (!h) return;
  h.innerHTML = "";
  if (!message) return;
  if (spin) {
    h.appendChild(iconEl("loader", 13, "spin"));
    h.appendChild(document.createTextNode(" "));
  }
  const s = el("span", null, message);
  if (err) s.style.color = "var(--danger)";
  h.appendChild(s);
}

export function grow(): void {
  const box = $("#composer") as HTMLTextAreaElement | null;
  if (!box) return;
  box.style.height = "auto";
  box.style.height = Math.min(220, box.scrollHeight) + "px";
}
