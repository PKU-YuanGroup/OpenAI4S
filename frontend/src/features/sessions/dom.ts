/** Tiny DOM helpers ported from app.js:3-4, 81, 2678-2706, 12918, 12927-12941. */

import { t } from "../../i18n";
import { currentId } from "../../stores/session";
import { _messagesFollow } from "../../stores/ui";
import { callLane } from "./lane";

export function $(sel: string): HTMLElement | null {
  if (typeof document === "undefined") return null;
  return document.querySelector(sel);
}

export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string | null,
  text?: string | null,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

export function ago(iso: string | undefined | null): string {
  if (!iso) return "";
  const ts = new Date(iso).getTime();
  if (isNaN(ts)) return "";
  const d = (Date.now() - ts) / 1000;
  if (d < 60) return "just now";
  if (d < 3600) return (d / 60 | 0) + "m";
  if (d < 86400) return (d / 3600 | 0) + "h";
  return (d / 86400 | 0) + "d";
}

export function framePath(fid: string, pid?: string | null): string {
  return `/projects/${encodeURIComponent(pid || "default")}/frames/${encodeURIComponent(fid)}`;
}

export function navURL(path: string, replace?: boolean): void {
  try {
    if (typeof location !== "undefined" && path === location.pathname) return;
    history[replace ? "replaceState" : "pushState"]({ path }, "", path);
  } catch {
    /* history API unavailable */
  }
}

export function setTitle(name: string | null | undefined): void {
  const ct = $("#conv-title") as HTMLInputElement | null;
  if (!ct) return;
  const value = name || t("conv.title.default");
  ct.value = value;
  ct.size = Math.max(6, Math.min(40, value.length + 1));
}

export function enableComposer(on: boolean): void {
  const c = $("#composer") as HTMLTextAreaElement | null;
  if (!c) return;
  c.disabled = false;
  c.classList.toggle("queueing", !on);
  c.placeholder = t(on ? "composer.placeholder" : "composer.placeholderQueue");
  callLane("renderQueueStrip");
}

export function grow(): void {
  const box = $("#composer") as HTMLTextAreaElement | null;
  if (!box) return;
  box.style.height = "auto";
  box.style.height = Math.min(220, box.scrollHeight) + "px";
}

export function showConv(): void {
  const view = $("#conv-view");
  if (view) view.classList.remove("hidden");
}

export function setSidebar(collapsed: boolean): void {
  if (typeof document === "undefined") return;
  document.body.classList.toggle("sidebar-collapsed", collapsed);
  const reopen = $("#sidebar-reopen");
  if (reopen) reopen.classList.toggle("hidden", !collapsed);
  let scrim = document.getElementById("mobile-scrim");
  if (!scrim) {
    scrim = el("div");
    scrim.id = "mobile-scrim";
    scrim.className = "mobile-scrim hidden";
    scrim.onclick = () => setSidebar(true);
    document.body.appendChild(scrim);
  }
  const mobile = typeof window !== "undefined" && window.matchMedia("(max-width: 900px)").matches;
  scrim.classList.toggle("hidden", collapsed || !mobile);
}

export function syncMobileChrome(resetDesktop: boolean): void {
  if (typeof window === "undefined") return;
  const mobile = window.matchMedia("(max-width: 900px)").matches;
  if (mobile) setSidebar(true);
  else if (resetDesktop) setSidebar(false);
}

export function isMobile(): boolean {
  if (typeof window === "undefined") return false;
  return window.matchMedia("(max-width: 900px)").matches;
}

export function messagesAtBottom(m: HTMLElement | null, pad?: number): boolean {
  return !m || m.scrollHeight - m.scrollTop - m.clientHeight < (pad || 80);
}

export function paintJumpPill(): void {
  const m = $("#messages");
  const pill = $("#jump-pill");
  if (!m || !pill) return;
  pill.classList.toggle("hidden", messagesAtBottom(m, 60));
}

export function down(force?: boolean): void {
  const m = $("#messages");
  if (!m) return;
  if (force || _messagesFollow.value !== false) {
    m.scrollTop = m.scrollHeight;
    _messagesFollow.value = true;
  }
  paintJumpPill();
}

export function updateJumpPill(): void {
  const m = $("#messages");
  if (!m) return;
  _messagesFollow.value = messagesAtBottom(m);
  paintJumpPill();
}

export function openModalEl(modal: HTMLElement | null): void {
  if (!modal) return;
  modal.classList.remove("hidden");
}

export function closeModalEl(modal: HTMLElement | null): void {
  if (!modal || modal.classList.contains("hidden")) return;
  modal.classList.add("hidden");
}

/** Clear the open-conversation chrome when no session remains. */
export function clearConversationChrome(): void {
  currentId.value = null;
  const messages = $("#messages");
  if (messages) messages.innerHTML = "";
  setTitle(t("conv.title.default"));
}
