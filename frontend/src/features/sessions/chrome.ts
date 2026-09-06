/**
 * Hint, disconnect banner, context menu, and keyboard-activate helpers.
 *
 * hint — app.js:12920 with a11y: role=status aria-live=polite on #composer-hint,
 * and an i18n "错误：/Error: " prefix on the err branch (no new dictionary key).
 * Menu — app.js:7744-7763 plus Esc / role=menu / focus into the first item.
 * #conn-dot is dead (no such node). The banner + hint take over ws onclose/onopen.
 */

import { LANG } from "../../i18n";
import { _menu } from "../../stores/ui";
import { ws as wsSignal } from "../../stores/stream";
import { $, el } from "./dom";
import { icon, iconEl } from "./icon";
import { effect } from "@preact/signals";

const ACTIVATE_SEL = ".d-row, .run-card, .tile, .art, .t-close";

export function errorPrefix(lang: string = LANG): string {
  return lang === "en" ? "Error: " : "错误：";
}

export function hint(text?: string | null, err?: boolean, spin?: boolean): void {
  const h = $("#composer-hint");
  if (!h) return;
  h.setAttribute("role", "status");
  h.setAttribute("aria-live", "polite");
  h.innerHTML = "";
  if (!text) return;
  if (spin) {
    h.appendChild(iconEl("loader", 13, "spin"));
    h.appendChild(document.createTextNode(" "));
  }
  const shown = err ? errorPrefix() + text : text;
  const s = el("span", null, shown);
  if (err) s.style.color = "var(--danger)";
  h.appendChild(s);
}

export type MenuItem =
  | { sep: true; label?: undefined; icon?: undefined; danger?: undefined; onClick?: undefined }
  | { sep?: false; label: string; icon?: string; danger?: boolean; onClick?: () => void };

function menuOutside(e: MouseEvent): void {
  const menu = _menu.value as HTMLElement | null;
  if (menu && e.target instanceof Node && !menu.contains(e.target)) closeMenu();
}

function menuKeydown(e: KeyboardEvent): void {
  if (e.key === "Escape") {
    e.preventDefault();
    closeMenu();
  }
}

export function closeMenu(): void {
  const menu = _menu.value as HTMLElement | null;
  if (menu) {
    menu.remove();
    _menu.value = null;
    document.removeEventListener("mousedown", menuOutside);
    document.removeEventListener("keydown", menuKeydown);
  }
}

export function openMenu(anchor: Element, items: MenuItem[]): void {
  closeMenu();
  const m = el("div", "ctx-menu");
  m.setAttribute("role", "menu");
  items.forEach((it) => {
    if (it.sep) {
      m.appendChild(el("div", "ctx-sep"));
      return;
    }
    const b = el("button", "ctx-item" + (it.danger ? " danger" : ""));
    b.setAttribute("role", "menuitem");
    b.type = "button";
    if (it.icon) {
      const ic = el("span", "ic");
      ic.innerHTML = icon(it.icon, 16);
      b.appendChild(ic);
    }
    b.appendChild(el("span", null, it.label));
    b.onclick = (e) => {
      e.stopPropagation();
      closeMenu();
      if (it.onClick) it.onClick();
    };
    m.appendChild(b);
  });
  document.body.appendChild(m);
  _menu.value = m;
  const r = anchor.getBoundingClientRect();
  let top = r.bottom + 4;
  if (top + m.offsetHeight > window.innerHeight - 8) {
    top = Math.max(8, r.top - m.offsetHeight - 4);
  }
  m.style.top = top + "px";
  m.style.left = Math.max(8, Math.min(r.left, window.innerWidth - m.offsetWidth - 8)) + "px";
  const first = m.querySelector("button");
  if (first instanceof HTMLElement) first.focus();
  setTimeout(() => {
    document.addEventListener("mousedown", menuOutside);
    document.addEventListener("keydown", menuKeydown);
  }, 0);
}

/** sessionRow 7030-7032: role=button + tabIndex + Enter/Space. */
export function bindActivate(node: HTMLElement, activate: () => void): void {
  if (node.tagName !== "BUTTON" && node.tagName !== "A") node.setAttribute("role", "button");
  node.tabIndex = 0;
  node.addEventListener("keydown", (e) => {
    if (e.target === node && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      activate();
    }
  });
  node.addEventListener("click", activate);
}

/**
 * Add Enter/Space without replacing an existing click handler.
 * Used on dashboard rows we create and, via the observer, on artifact tiles
 * / dock close buttons later lanes insert (`.tile` / `.art` / `.t-close`).
 */
export function ensureActivateKeys(node: HTMLElement): void {
  if (node.dataset.a11yActivate === "1") return;
  node.dataset.a11yActivate = "1";
  if (node.tagName !== "BUTTON" && node.tagName !== "A" && !node.getAttribute("role")) {
    node.setAttribute("role", "button");
  }
  if (node.tabIndex < 0) node.tabIndex = 0;
  node.addEventListener("keydown", (e) => {
    if (e.target === node && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      node.click();
    }
  });
}

export function bindArtifactTile(node: HTMLElement, activate: () => void): void {
  bindActivate(node, activate);
}

export function bindCloseTab(node: HTMLElement, activate: () => void): void {
  bindActivate(node, activate);
}

let activateObserver: MutationObserver | null = null;

export function watchActivateKeys(root: ParentNode = document): void {
  root.querySelectorAll(ACTIVATE_SEL).forEach((n) => ensureActivateKeys(n as HTMLElement));
  if (activateObserver || typeof MutationObserver === "undefined") return;
  activateObserver = new MutationObserver((records) => {
    for (const rec of records) {
      rec.addedNodes.forEach((n) => {
        if (!(n instanceof HTMLElement)) return;
        if (n.matches(ACTIVATE_SEL)) ensureActivateKeys(n);
        n.querySelectorAll(ACTIVATE_SEL).forEach((child) => ensureActivateKeys(child as HTMLElement));
      });
    }
  });
  activateObserver.observe(root instanceof Document ? root.body : (root as Element), {
    childList: true,
    subtree: true,
  });
}

function disconnectCopy(): string {
  return LANG === "en" ? "Disconnected, reconnecting…" : "连接已断开，正在重连…";
}

let disconnectHintShown = false;
let watchingSocket: unknown = null;
let disconnectWatchStarted = false;

function bannerEl(): HTMLElement | null {
  return $("#conn-banner");
}

export function showDisconnectBanner(): void {
  const b = bannerEl();
  if (b) {
    b.textContent = disconnectCopy();
    b.classList.add("visible");
    b.removeAttribute("hidden");
  }
  hint(disconnectCopy());
  disconnectHintShown = true;
}

export function hideDisconnectBanner(): void {
  const b = bannerEl();
  if (b) {
    b.classList.remove("visible");
    b.setAttribute("hidden", "");
    b.textContent = "";
  }
  if (disconnectHintShown) {
    hint("");
    disconnectHintShown = false;
  }
}

type SocketLike = {
  onopen: ((ev?: unknown) => void) | null;
  onclose: ((ev?: unknown) => void) | null;
};

function wrapSocket(socket: SocketLike): void {
  const prevOpen = socket.onopen;
  const prevClose = socket.onclose;
  socket.onopen = (ev?: unknown) => {
    hideDisconnectBanner();
    if (prevOpen) prevOpen(ev);
  };
  socket.onclose = (ev?: unknown) => {
    showDisconnectBanner();
    if (prevClose) prevClose(ev);
  };
}

/** Wrap F-06's socket handlers after they are assigned (ws signal fires first). */
export function watchDisconnect(): void {
  if (disconnectWatchStarted) return;
  disconnectWatchStarted = true;
  effect(() => {
    const socket = wsSignal.value as SocketLike | null;
    if (!socket || socket === watchingSocket) return;
    watchingSocket = socket;
    queueMicrotask(() => wrapSocket(socket));
  });
}
