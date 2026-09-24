/**
 * How Customize closes. `customizeOpen` is the one owner of whether it is
 * open; `#cust`'s `.hidden` class only follows it.
 *
 * Chrome's modal trap closes the topmost modal on Escape by adding `.hidden`
 * to it directly. Pressed inside a nested editor, that hid the whole of `#cust`
 * while `customizeOpen` stayed true: the tab stayed mounted and kept polling,
 * and Preact never took `.hidden` back off, because the class string it
 * renders had not changed. So chrome leaves Escape to Customize while `#cust`
 * is the modal on top, and anything else that hides `#cust` (a direct
 * `closeModalEl`) is followed by `closeCust()`. Chrome no longer binds
 * `#cust`'s backdrop or ×: Customize renders both and closes through
 * `closeCust()`.
 */
import { addModalEscapeBlocker, topModal } from "../chrome/modal";
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
  return topModal() === cust;
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

type BackdropEvent = { target: EventTarget | null; currentTarget: EventTarget | null };

const pressedInside = new WeakMap<EventTarget, boolean>();

/** `onPointerDown` of a backdrop: remember whether the press began inside the dialog. */
export function notePress(e: BackdropEvent): void {
  if (e.currentTarget) pressedInside.set(e.currentTarget, e.target !== e.currentTarget);
}

/**
 * A click on the backdrop itself whose press did not begin inside the dialog.
 * Selecting text in a field and letting go over the backdrop reports the
 * backdrop, their common ancestor, as the click's target; that used to close
 * the dialog and drop what was typed.
 */
export function backdropClicked(e: BackdropEvent): boolean {
  const backdrop = e.currentTarget;
  if (!backdrop) return false;
  const inside = pressedInside.get(backdrop) === true;
  pressedInside.delete(backdrop);
  return e.target === backdrop && !inside;
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
