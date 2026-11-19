/**
 * Small DOM helpers used by the F-20 chrome lane.
 * `el` / `$` match app.js:3-4; `ago` 12918; `hint` 12920; `grow` 12941.
 * Icon paths come from the shared table in features/icons/paths.ts.
 */

import { copyLookup } from "../../i18n/copy";
import { iconSvg } from "../icons/paths";

/** Relative time in the UI language: "刚刚 / 3 分钟前" read "just now / 3m" in Chinese. */
const AGO = copyLookup({
  zh: { "ago.now": "刚刚", "ago.m": "{0} 分钟前", "ago.h": "{0} 小时前", "ago.d": "{0} 天前" },
  en: { "ago.now": "just now", "ago.m": "{0}m", "ago.h": "{0}h", "ago.d": "{0}d" },
});

/** "just now" / "3m" / "2h" / "5d" for a time in ms; "" for none. */
export function agoMs(ms: number, now = Date.now()): string {
  if (!ms) return "";
  const d = (now - ms) / 1000;
  if (d < 60) return AGO("ago.now");
  if (d < 3600) return AGO("ago.m", (d / 60) | 0);
  if (d < 86400) return AGO("ago.h", (d / 3600) | 0);
  return AGO("ago.d", (d / 86400) | 0);
}

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
  return agoMs(ts);
}

const pad2 = (value: number) => String(value).padStart(2, "0");

/**
 * Fixed-width local date and time (`2026-10-30 14:02`): a read or observation
 * time is a fact to compare, not a locale string. A zero or missing time is "".
 */
export function localDateTime(ms: number | null | undefined): string {
  if (!ms) return "";
  const date = new Date(ms);
  return (
    `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ` +
    `${pad2(date.getHours())}:${pad2(date.getMinutes())}`
  );
}

/** Fixed-width local clock time (`14:02:07`), for a time read during this visit. */
export function localClock(ms: number | null | undefined): string {
  if (!ms) return "";
  const date = new Date(ms);
  return `${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`;
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
