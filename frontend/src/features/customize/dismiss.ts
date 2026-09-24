/**
 * How Customize closes. `customizeOpen` is the one owner of whether it is
 * open; `#cust`'s `.hidden` class only follows it.
 *
 * Chrome's modal trap closes the topmost modal on Escape by adding `.hidden`
 * to it directly. Pressed inside a nested editor, that hid the whole of `#cust`
 * while `customizeOpen` stayed true: the tab stayed mounted and kept polling,
 * and Preact never took `.hidden` back off, because the class string it
 * renders had not changed. So chrome leaves Escape to Customize while `#cust`
 * is the modal on top, and anything else that hides `#cust` (chrome's
 * backdrop / × binding, a direct `closeModalEl`) is followed by `closeCust()`.
 */
import { FALLBACK_MODAL_SELECTORS, _modalFocus, addModalEscapeBlocker } from "../chrome/modal";
import { closeCust } from "./actions";
import { customizeOpen, nestedEditor } from "./state";

type EscapeEvent = {
  key: string;
  defaultPrevented?: boolean;
  isComposing?: boolean;
  preventDefault: () => void;
};

/** `#cust` is the modal chrome's trap would act on (its stack, then its fallback order). */
export function customizeOnTop(): boolean {
  if (typeof document === "undefined" || !customizeOpen.value) return false;
  const cust = document.getElementById("cust");
  if (!cust || cust.classList.contains("hidden")) return false;
  const top = _modalFocus.stack[_modalFocus.stack.length - 1];
  if (top && !top.el.classList.contains("hidden")) return top.el === cust;
  for (const sel of FALLBACK_MODAL_SELECTORS) {
    const node = document.querySelector(sel);
    if (node && !node.classList.contains("hidden")) return node === cust;
  }
  return false;
}

let escapeInstalled = false;

/** Chrome's trap leaves Escape to `onCustomizeKeydown` while Customize is on top. */
export function installCustomizeEscape(): void {
  if (escapeInstalled) return;
  escapeInstalled = true;
  addModalEscapeBlocker(customizeOnTop);
}

/**
 * Escape closes the nested editor first, then Customize. A key another
 * handler already took (the palette, a modal above `#cust`) or one that ends
 * an IME composition is left alone.
 */
export function onCustomizeKeydown(e: EscapeEvent): void {
  if (e.key !== "Escape" || e.defaultPrevented || e.isComposing) return;
  if (!customizeOnTop()) return;
  e.preventDefault();
  if (nestedEditor.value) {
    nestedEditor.value = null;
    return;
  }
  closeCust();
}

/** Close Customize when something other than `closeCust()` hides `#cust`. */
export function followCustomizeDom(modal: HTMLElement): () => void {
  if (typeof MutationObserver === "undefined") return () => {};
  const observer = new MutationObserver(() => {
    if (customizeOpen.value && modal.classList.contains("hidden")) closeCust();
  });
  observer.observe(modal, { attributes: true, attributeFilter: ["class"] });
  return () => observer.disconnect();
}
