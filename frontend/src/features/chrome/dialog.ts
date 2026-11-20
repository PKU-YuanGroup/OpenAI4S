/**
 * M-45: the workbench's own confirm / text / notice dialogs.
 *
 * The browser's native prompt() / confirm() / alert() ignore the theme and
 * the UI language, block the page, and in an embedded WebView may not appear
 * at all -- "+ New folder and move here" then did nothing. These build a small
 * modal on demand on the shared trap (Tab cycles inside, Escape or the scrim
 * cancels, focus returns to the control that opened it) and resolve once
 * answered. Where no document exists (tests, a closed page) they resolve as a
 * cancel, never a hang and never a silent yes.
 */

import { copyLookup, type CopyTable } from "../../i18n/copy";
import { closeModalEl, openModalEl } from "./modal";

const COPY: CopyTable = {
  zh: { "dialog.ok": "确定", "dialog.cancel": "取消" },
  en: { "dialog.ok": "OK", "dialog.cancel": "Cancel" },
};

const dialogT = copyLookup(COPY);

export type DialogOptions = {
  /** Heading above the message; none by default. */
  title?: string;
  /** The confirming button's label (default 确定 / OK). */
  okLabel?: string;
  /** The cancelling button's label (default 取消 / Cancel). */
  cancelLabel?: string;
  /** A destructive confirm: the button reads as one, and Enter does not land on it first. */
  danger?: boolean;
};

type Kind = "confirm" | "text" | "notice";

let seq = 0;

function node<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  cls?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const out = document.createElement(tag);
  if (cls) out.className = cls;
  if (text != null) out.textContent = text;
  return out;
}

/** The one modal behind all three helpers; resolves the text (or "") on OK, null otherwise. */
function showDialog(kind: Kind, message: string, opts: DialogOptions, initial = ""): Promise<string | null> {
  return new Promise<string | null>((resolve) => {
    if (typeof document === "undefined" || !document.body) {
      resolve(null);
      return;
    }
    const id = `app-dialog-${++seq}`;
    const modal = node("div", "modal hidden app-dialog-modal");
    modal.setAttribute("role", kind === "text" ? "dialog" : "alertdialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-describedby", `${id}-msg`);
    const box = node("div", "modal-box small-box");
    if (opts.title) {
      const head = node("div", "modal-head");
      const title = node("span", undefined, opts.title);
      title.id = `${id}-title`;
      head.appendChild(title);
      box.appendChild(head);
      modal.setAttribute("aria-labelledby", title.id);
    }
    const form = node("div", "form app-dialog");
    const text = node(kind === "text" ? "label" : "p", "app-dialog-msg", message);
    text.id = `${id}-msg`;
    form.appendChild(text);
    let input: HTMLInputElement | null = null;
    if (kind === "text") {
      input = node("input", "app-dialog-input");
      input.type = "text";
      input.id = `${id}-input`;
      input.value = initial;
      input.setAttribute("data-autofocus", "");
      input.addEventListener("focus", () => input?.select(), { once: true });
      text.setAttribute("for", input.id);
      form.appendChild(input);
    }
    const actions = node("div", "form-actions");
    let cancel: HTMLButtonElement | null = null;
    if (kind !== "notice") {
      cancel = node("button", "outline-btn app-dialog-cancel", opts.cancelLabel || dialogT("dialog.cancel"));
      cancel.type = "button";
      actions.appendChild(cancel);
    }
    const ok = node(
      "button",
      "solid-btn app-dialog-ok" + (opts.danger ? " danger" : ""),
      opts.okLabel || dialogT("dialog.ok"),
    );
    ok.type = "button";
    actions.appendChild(ok);
    if (!input) (opts.danger && cancel ? cancel : ok).setAttribute("data-autofocus", "");
    form.appendChild(actions);
    box.appendChild(form);
    modal.appendChild(box);
    document.body.appendChild(modal);

    let settled = false;
    let observer: MutationObserver | null = null;
    const finish = (value: string | null): void => {
      if (settled) return;
      settled = true;
      observer?.disconnect();
      closeModalEl(modal);
      modal.remove();
      resolve(value);
    };
    const accept = (): void => finish(input ? input.value : "");
    ok.addEventListener("click", accept);
    cancel?.addEventListener("click", () => finish(null));
    modal.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        // Answered here, before the document-level trap or a host view's own
        // Escape (Settings closes on it) could act on another modal.
        event.preventDefault();
        event.stopPropagation();
        finish(null);
      } else if (event.key === "Enter" && input && event.target === input && !event.isComposing) {
        event.preventDefault();
        accept();
      }
    });
    let pressedOnScrim = false;
    modal.addEventListener("pointerdown", (event) => {
      pressedOnScrim = event.target === modal;
    });
    modal.addEventListener("click", (event) => {
      const fromScrim = pressedOnScrim;
      pressedOnScrim = false;
      if (fromScrim && event.target === modal) finish(null);
    });
    // Any other close path (the shared trap) is a cancel, never a hang.
    if (typeof MutationObserver === "function") {
      observer = new MutationObserver(() => {
        if (modal.classList.contains("hidden") && !settled) finish(null);
      });
    }
    openModalEl(modal);
    observer?.observe(modal, { attributes: true, attributeFilter: ["class"] });
  });
}

/** In-app confirm(): true only when the person pressed the confirming button. */
export async function askConfirm(message: string, opts: DialogOptions = {}): Promise<boolean> {
  return (await showDialog("confirm", message, opts)) !== null;
}

/** In-app prompt(): the text as typed, or null when cancelled. */
export function askText(message: string, initial = "", opts: DialogOptions = {}): Promise<string | null> {
  return showDialog("text", message, opts, initial);
}

/** In-app alert(): resolves when dismissed. */
export async function showNotice(message: string, opts: DialogOptions = {}): Promise<void> {
  await showDialog("notice", message, opts);
}
