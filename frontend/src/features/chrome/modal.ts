/**
 * Modal focus trap. Verbatim port of app.js:11059-11120.
 *
 * Stack / Tab cycle / Escape / focus restore are the kernel. Team modals
 * (app.js:13491, 13613) used to add/remove `.hidden` and skip this trap;
 * F-20 routes them through `openModalEl` / `closeModalEl`.
 */

import { $ } from "./dom";
import { chromeHost } from "./host";

export type ModalFocusEntry = { el: HTMLElement; prev: Element | null };

/** app.js:11060 — the open-modal stack. Exported so tests can inspect it. */
export const _modalFocus: { stack: ModalFocusEntry[] } = { stack: [] };

/**
 * Selectors used when the stack is empty but a modal is still visible.
 * Original list was `#cust,#modal,#proj-modal` (11101). Team modals are
 * added because they now participate in the trap.
 */
export const FALLBACK_MODAL_SELECTORS = [
  "#cust",
  "#modal",
  "#proj-modal",
  "#team-admin-modal",
  "#team-files-modal",
] as const;

const FOCUSABLE_SEL =
  'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])';

type EscapeBlocker = () => boolean;
const escapeBlockers: EscapeBlocker[] = [];

/** Palette (and later autocomplete) register so Esc is not stolen. */
export function addModalEscapeBlocker(fn: EscapeBlocker): void {
  escapeBlockers.push(fn);
}

export function resetModalTrap(): void {
  _modalFocus.stack.length = 0;
}

export type ModalKeyEvent = {
  key: string;
  shiftKey?: boolean;
  preventDefault: () => void;
};

/** app.js:11061-11065 */
export function _focusables(root: Element | null): HTMLElement[] {
  if (!root) return [];
  return [...root.querySelectorAll(FOCUSABLE_SEL)].filter((n) => {
    const node = n as HTMLElement;
    return (
      !node.hasAttribute("disabled") &&
      node.offsetParent !== null &&
      !node.classList.contains("hidden")
    );
  }) as HTMLElement[];
}

function focusNode(node: HTMLElement): void {
  try {
    node.focus({ preventScroll: true });
  } catch {
    try {
      node.focus();
    } catch {
      /* detached */
    }
  }
}

/** app.js:11066-11081 */
export function openModalEl(modal: HTMLElement | null): void {
  if (!modal) return;
  const wasHidden = modal.classList.contains("hidden");
  modal.classList.remove("hidden");
  if (wasHidden) {
    _modalFocus.stack.push({
      el: modal,
      prev: document.activeElement,
    });
    // Defer focus until content paints (cust tabs fill async)
    requestAnimationFrame(() => {
      const box = (modal.querySelector(".modal-box") as HTMLElement | null) || modal;
      if (box && !box.hasAttribute("tabindex")) box.setAttribute("tabindex", "-1");
      const list = _focusables(box);
      const marked = modal.querySelector("[data-autofocus]") as HTMLElement | null;
      const prefer =
        marked || list.find((n) => !n.classList.contains("icon-ghost")) || list[0];
      focusNode(prefer || box);
    });
  }
}

/** app.js:11082-11094 */
export function closeModalEl(modal: HTMLElement | null): void {
  if (!modal || modal.classList.contains("hidden")) return;
  modal.classList.add("hidden");
  // Pop matching stack entry (or top if this is the topmost)
  let entry: ModalFocusEntry | null = null;
  for (let i = _modalFocus.stack.length - 1; i >= 0; i--) {
    const item = _modalFocus.stack[i];
    if (item && item.el === modal) {
      entry = _modalFocus.stack.splice(i, 1)[0] || null;
      break;
    }
  }
  const prev = entry && entry.prev;
  if (
    prev &&
    typeof (prev as HTMLElement).focus === "function" &&
    document.contains(prev)
  ) {
    focusNode(prev as HTMLElement);
  }
}

function autocompleteOpen(): boolean {
  const ac = chromeHost().ac;
  return !!(ac && ac.open);
}

function escapeBlocked(): boolean {
  for (const fn of escapeBlockers) {
    if (fn()) return true;
  }
  return autocompleteOpen();
}

function visibleFallbackModal(): HTMLElement | null {
  for (const sel of FALLBACK_MODAL_SELECTORS) {
    const node = $(sel);
    if (node && !node.classList.contains("hidden")) return node;
  }
  return null;
}

/** app.js:11095-11120 */
export function trapModalKeydown(e: ModalKeyEvent | KeyboardEvent): void {
  if (e.key !== "Tab" && e.key !== "Escape") return;
  // topmost open modal (stack) or first visible modal
  let modal: HTMLElement | null = null;
  if (_modalFocus.stack.length) {
    const top = _modalFocus.stack[_modalFocus.stack.length - 1];
    modal = top ? top.el : null;
  }
  if (!modal || modal.classList.contains("hidden")) {
    modal = visibleFallbackModal();
  }
  if (!modal) return;
  if (e.key === "Escape") {
    // Don't steal Escape from nested popovers / composer autocomplete
    if (escapeBlocked()) return;
    e.preventDefault();
    closeModalEl(modal);
    return;
  }
  // Tab cycle within the modal
  const box = (modal.querySelector(".modal-box") as HTMLElement | null) || modal;
  const list = _focusables(box);
  if (!list.length) return;
  const first = list[0];
  const last = list[list.length - 1];
  if (!first || !last) return;
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
  } else if (!box.contains(document.activeElement)) {
    e.preventDefault();
    first.focus();
  }
}

/** Overlay click (target === modal) + close button. app.js:13387-13391. */
export function bindModalDismiss(modal: HTMLElement | null, closeBtn?: HTMLElement | null): void {
  if (!modal) return;
  if (closeBtn) closeBtn.addEventListener("click", () => closeModalEl(modal));
  modal.addEventListener("click", (e) => {
    if (e.target === modal) closeModalEl(modal);
  });
}

export function anyModalOpen(): boolean {
  if (_modalFocus.stack.length) {
    const top = _modalFocus.stack[_modalFocus.stack.length - 1];
    if (top && !top.el.classList.contains("hidden")) return true;
  }
  return visibleFallbackModal() !== null;
}
