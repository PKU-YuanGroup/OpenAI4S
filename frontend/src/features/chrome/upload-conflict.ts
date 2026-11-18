/**
 * UP-3: an upload whose name is already taken in the session asks first.
 *
 * Uploads are sent with `if_exists: "error"`; a 409 `artifact_exists` lands
 * here. The person picks "keep both" (the server stores the next free
 * `name-2.ext`), "replace" (a new version; the old bytes stay in version
 * history) or "cancel". Dialogs for files of one batch are shown one at a time,
 * whatever order the uploads run in, and "do the same for the rest" answers
 * the remaining conflicts of that batch -- so a queue that serializes the
 * POSTs composes with this unchanged.
 */

import { copyLookup, type CopyTable } from "../../i18n/copy";
import { closeModalEl, openModalEl } from "./modal";

export type UploadConflictChoice = "rename" | "version" | "cancel";

/** Per-selection memory for "do the same for the other files in this batch". */
export type UploadConflictBatch = { remembered: UploadConflictChoice | null; size: number };

const COPY: CopyTable = {
  zh: {
    "upload.conflict.title": "已有同名文件",
    "upload.conflict.body": "此会话中已有「{0}」。要怎样处理新上传的文件？",
    "upload.conflict.keep": "保留两者",
    "upload.conflict.keepNote": "新文件另存为带编号的名字，例如 {0}",
    "upload.conflict.replace": "替换为新版本",
    "upload.conflict.replaceNote": "旧内容保留在版本历史中，可以恢复",
    "upload.conflict.cancel": "取消上传",
    "upload.conflict.remember": "对本批其余同名文件执行相同操作",
    "upload.renamed": "已上传：{0}（另存为 {1}）",
    "upload.versioned": "已上传：{0}（新版本，旧版本可在版本历史中恢复）",
    "upload.cancelled": "已取消上传：{0}",
  },
  en: {
    "upload.conflict.title": "A file with this name exists",
    "upload.conflict.body": "This session already has “{0}”. What should happen to the new upload?",
    "upload.conflict.keep": "Keep both",
    "upload.conflict.keepNote": "The new file is saved under a numbered name, such as {0}",
    "upload.conflict.replace": "Replace as a new version",
    "upload.conflict.replaceNote": "The old content stays in version history and can be restored",
    "upload.conflict.cancel": "Cancel upload",
    "upload.conflict.remember": "Do the same for the other files in this batch",
    "upload.renamed": "Uploaded {0} (saved as {1})",
    "upload.versioned": "Uploaded {0} as a new version (the old one is in version history)",
    "upload.cancelled": "Upload cancelled: {0}",
  },
};

export const uploadT = copyLookup(COPY);

/** `data.csv` → `data-2.csv`, keeping a compression suffix last (mirrors the server). */
export function numberedUploadName(name: string, n: number): string {
  const dot = name.lastIndexOf(".");
  let stem = dot > 0 ? name.slice(0, dot) : name;
  let suffix = dot > 0 ? name.slice(dot) : "";
  if (/^\.(gz|bz2|xz|zst|zip)$/i.test(suffix)) {
    const inner = stem.lastIndexOf(".");
    if (inner > 0) {
      suffix = stem.slice(inner) + suffix;
      stem = stem.slice(0, inner);
    }
  }
  return `${stem}-${n}${suffix}`;
}

export type ConflictAnswer = { choice: UploadConflictChoice; remember: boolean };
export type ConflictAsker = (filename: string, offerRemember: boolean) => Promise<ConflictAnswer>;

let queue: Promise<unknown> = Promise.resolve();

/** Ask about one taken name; concurrent files of any batch wait their turn. */
export function askUploadConflict(
  filename: string,
  batch: UploadConflictBatch,
  ask: ConflictAsker = showUploadConflictDialog,
): Promise<UploadConflictChoice> {
  const turn = queue.then(async (): Promise<UploadConflictChoice> => {
    if (batch.remembered) return batch.remembered;
    let answer: ConflictAnswer;
    try {
      answer = await ask(filename, batch.size > 1);
    } catch {
      answer = { choice: "cancel", remember: false };
    }
    if (answer.remember) batch.remembered = answer.choice;
    return answer.choice;
  });
  queue = turn.catch(() => undefined);
  return turn;
}

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

/** The modal itself: built on demand, removed when answered. */
export function showUploadConflictDialog(filename: string, offerRemember: boolean): Promise<ConflictAnswer> {
  return new Promise<ConflictAnswer>((resolve) => {
    if (typeof document === "undefined" || !document.body) {
      resolve({ choice: "cancel", remember: false });
      return;
    }
    const modal = node("div", "modal hidden upload-conflict-modal");
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-labelledby", "upload-conflict-title");
    const box = node("div", "modal-box small-box");
    const head = node("div", "modal-head");
    const title = node("span", undefined, uploadT("upload.conflict.title"));
    title.id = "upload-conflict-title";
    head.appendChild(title);
    box.appendChild(head);
    const form = node("div", "form upload-conflict");
    form.appendChild(node("p", "upload-conflict-body", uploadT("upload.conflict.body", filename)));
    const option = (choice: UploadConflictChoice, label: string, note: string, primary: boolean) => {
      const button = node("button", primary ? "solid-btn upload-conflict-option" : "outline-btn upload-conflict-option");
      button.type = "button";
      button.dataset.choice = choice;
      button.appendChild(node("strong", undefined, label));
      button.appendChild(node("span", "upload-conflict-note", note));
      if (primary) button.setAttribute("data-autofocus", "");
      return button;
    };
    const keep = option(
      "rename",
      uploadT("upload.conflict.keep"),
      uploadT("upload.conflict.keepNote", numberedUploadName(filename, 2)),
      true,
    );
    const replace = option("version", uploadT("upload.conflict.replace"), uploadT("upload.conflict.replaceNote"), false);
    form.appendChild(keep);
    form.appendChild(replace);
    let remember: HTMLInputElement | null = null;
    if (offerRemember) {
      const label = node("label", "upload-conflict-remember");
      remember = node("input");
      remember.type = "checkbox";
      label.appendChild(remember);
      label.appendChild(document.createTextNode(uploadT("upload.conflict.remember")));
      form.appendChild(label);
    }
    const actions = node("div", "form-actions");
    const cancel = node("button", "outline-btn small", uploadT("upload.conflict.cancel"));
    cancel.type = "button";
    actions.appendChild(cancel);
    form.appendChild(actions);
    box.appendChild(form);
    modal.appendChild(box);
    document.body.appendChild(modal);

    let settled = false;
    let observer: MutationObserver | null = null;
    const finish = (choice: UploadConflictChoice): void => {
      if (settled) return;
      settled = true;
      observer?.disconnect();
      const answer = { choice, remember: !!(remember && remember.checked) };
      closeModalEl(modal);
      modal.remove();
      resolve(answer);
    };
    keep.onclick = () => finish("rename");
    replace.onclick = () => finish("version");
    cancel.onclick = () => finish("cancel");
    // Escape here, before the document-level trap could pick another modal.
    modal.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      finish("cancel");
    });
    modal.addEventListener("click", (event) => {
      if (event.target === modal) finish("cancel");
    });
    // Any other close path (the shared modal trap) is a cancel, never a hang.
    if (typeof MutationObserver === "function") {
      observer = new MutationObserver(() => {
        if (modal.classList.contains("hidden") && !settled) finish("cancel");
      });
    }
    openModalEl(modal);
    observer?.observe(modal, { attributes: true, attributeFilter: ["class"] });
  });
}
