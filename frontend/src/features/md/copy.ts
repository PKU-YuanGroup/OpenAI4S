/**
 * The Copy button on every rendered code block (`mdCodeBlock`).
 *
 * One delegated click listener, as app.js had (13872-13885): code blocks are
 * produced as HTML strings by `renderMd`, in chat answers, step bodies and
 * anywhere else markdown is shown, so no per-node binding can reach them.
 * The port dropped the listener, and the button did nothing.
 *
 * The label says "Copied" only for a confirmed write (`copyText`). A refused
 * copy says so and selects the code, so Ctrl/Cmd+C is one keystroke away.
 */

import { LANG, t } from "../../i18n/runtime";
import { copyFailedText, copyText } from "../chrome/clipboard";

/** Feature-local copy: `i18n/en.ts` / `zh.ts` are generated extracts of app.js. */
const FAILED_LABEL: Record<"en" | "zh", string> = { en: "Copy failed", zh: "复制失败" };

const COPIED_MS = 1400;
const FAILED_MS = 4000;

type Closest = { closest?: (selector: string) => Element | null };

const resets = new WeakMap<Element, ReturnType<typeof setTimeout>>();

function selectForManualCopy(node: Element): void {
  try {
    const selection = typeof window !== "undefined" ? window.getSelection() : null;
    if (!selection || typeof document === "undefined" || typeof document.createRange !== "function") return;
    const range = document.createRange();
    range.selectNodeContents(node);
    selection.removeAllRanges();
    selection.addRange(range);
  } catch {
    /* selection is a convenience */
  }
}

/** Copy the code of the block `button` belongs to, and say how it went. */
export async function copyCodeBlock(button: Element): Promise<boolean> {
  const block = button.closest(".codeblock");
  const code = block ? block.querySelector("pre code") : null;
  const ok = await copyText(code ? code.textContent || "" : "");
  const label = button.querySelector(".cb-copy-t");
  if (label && !label.getAttribute("data-o")) label.setAttribute("data-o", label.textContent || "");
  const previous = resets.get(button);
  if (previous) clearTimeout(previous);
  button.classList.toggle("copied", ok);
  if (label) label.textContent = ok ? t("code.copied") : FAILED_LABEL[LANG === "zh" ? "zh" : "en"];
  (button as HTMLElement).title = ok ? t("code.copy.title") : copyFailedText();
  if (!ok && code) selectForManualCopy(code);
  resets.set(
    button,
    setTimeout(
      () => {
        resets.delete(button);
        button.classList.remove("copied");
        (button as HTMLElement).title = t("code.copy.title");
        if (label) label.textContent = label.getAttribute("data-o") || t("msgAction.copy");
      },
      ok ? COPIED_MS : FAILED_MS,
    ),
  );
  return ok;
}

let bound = false;

/** Idempotent. `md/index.ts` binds `document` at import; no node is needed yet. */
export function bindCodeCopy(root: Pick<Document, "addEventListener">): void {
  if (bound) return;
  bound = true;
  root.addEventListener("click", (event) => {
    const target = event.target as Closest | null;
    const button = target && typeof target.closest === "function" ? target.closest(".cb-copy") : null;
    if (button) void copyCodeBlock(button);
  });
}

/** Test seam: forget the one-time bind. */
export function resetCodeCopyBinding(): void {
  bound = false;
}
