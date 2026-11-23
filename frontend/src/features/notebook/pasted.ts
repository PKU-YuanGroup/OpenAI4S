/**
 * SYNC-15 (owner decision 22): code pasted into the chat composer can be run
 * in the Notebook instead of being sent to the agent.
 *
 * Pasting a fenced block into the composer had one way out: Send, which asked
 * the agent about the code rather than running it. While manual Notebook
 * cells are on for the session (`replEnabledNow`, the same signal the
 * Notebook's own input follows: off by default, never in a quarantined copy)
 * and the composer is usable, a "Run in Notebook" button sits above the
 * composer's text. It runs the first Python or R block (a bare fence is
 * Python) through the Notebook's own REPL path, `executeNotebookCode`, and
 * opens the Notebook. Once the run is accepted the block leaves the message,
 * so the next Send does not hand the agent code that already ran; any prose
 * around it stays. A refused run leaves the composer as it was, and the
 * Notebook, opened either way, says why beside its input.
 */

import { effect, untracked } from "@preact/signals";
import { languageRevision } from "../../i18n/runtime";
import { _kc } from "../../stores/notebook";
import { currentId } from "../../stores/session";
import { iconEl } from "./chrome";
import { nbT } from "./copy";
import { executeNotebookCode, refreshKernelState, replBusy, replEnabled, replEnabledNow } from "./kernel";

/** A runnable fenced block in the composer's text, and where it sits. */
export type ComposerCode = { language: "python" | "r"; code: string; start: number; end: number };

/** A fence on its own line, its info string, its body, and a matching closing fence. */
const FENCE = /(^|\n)[ \t]*(`{3,})[ \t]*([^\s`]*)[^\n`]*\n([\s\S]*?)\n[ \t]*\2[ \t]*(?=\n|$)/g;

function fenceLanguage(tag: string): "python" | "r" | null {
  const name = tag.toLowerCase();
  if (!name || name === "python" || name === "py" || name === "python3") return "python";
  if (name === "r") return "r";
  return null;
}

/** The first closed Python or R block (a bare fence is Python) with code in it. */
export function composerCodeBlock(text: string): ComposerCode | null {
  for (const m of String(text || "").matchAll(FENCE)) {
    const language = fenceLanguage(m[3] || "");
    const code = m[4] || "";
    if (!language || !code.trim()) continue;
    const start = (m.index || 0) + (m[1] || "").length;
    return { language, code, start, end: (m.index || 0) + m[0].length };
  }
  return null;
}

/** The composer's text with that block taken out; the prose around it stays. */
export function withoutCodeBlock(text: string, block: ComposerCode): string {
  return (text.slice(0, block.start) + text.slice(block.end)).replace(/\n{3,}/g, "\n\n").trim();
}

/** A node by id; null with no document (or a test's partial one). */
function byId(id: string): HTMLElement | null {
  if (typeof document === "undefined" || typeof document.getElementById !== "function") return null;
  return document.getElementById(id);
}

function composerBox(): HTMLTextAreaElement | null {
  return byId("composer") as HTMLTextAreaElement | null;
}

/**
 * The session's kernel status is read by the Notebook while it is on screen;
 * a block pasted with the Notebook closed reads it once, so the button can
 * follow the session's manual-cells switch. Read again only after an
 * invalidation (a turn's end, a switch change), not on every keystroke.
 */
function ensureKernelRead(sid: string): void {
  const kc = _kc.value;
  if (kc.id === sid && kc.st) return;
  void refreshKernelState();
}

/** Paint `#composer-run` for the composer's text and the session's switch. */
export function renderComposerRun(): void {
  const host = byId("composer-run");
  const box = composerBox();
  if (!host || !box) return;
  const sid = currentId.value;
  const block = composerCodeBlock(box.value || "");
  if (block && sid) ensureKernelRead(sid);
  const offer = block && sid && !box.disabled && replEnabledNow() ? block : null;
  host.innerHTML = "";
  if (!offer) {
    host.classList.add("hidden");
    return;
  }
  host.classList.remove("hidden");
  const label = offer.language === "r" ? "R" : "Python";
  const busy = replBusy.value;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "outline-btn small composer-run-btn";
  button.dataset.language = offer.language;
  button.appendChild(iconEl("notebook", 12));
  button.appendChild(document.createTextNode(" " + nbT("nb.composerRun.label", label)));
  button.title = busy ? nbT("nb.action.replBusy") : nbT("nb.composerRun.title", label);
  button.disabled = busy;
  button.onclick = (event) => {
    event.preventDefault();
    void runComposerCode();
  };
  host.appendChild(button);
}

function openNotebookDock(): void {
  void import("../artifacts/ui").then((ui) => ui.setActiveTab("notebook"));
}

/**
 * Run the composer's first block as a manual Notebook cell and open the
 * Notebook. True when the run was accepted (the block then leaves the
 * message, if the composer still holds the text it was read from).
 */
export async function runComposerCode(openNotebook: () => void = openNotebookDock): Promise<boolean> {
  const box = composerBox();
  if (!box || box.disabled || !replEnabledNow()) return false;
  const text = box.value || "";
  const block = composerCodeBlock(text);
  if (!block) return false;
  const ran = await executeNotebookCode(block.code, block.language);
  if (ran && box.value === text) {
    box.value = withoutCodeBlock(text, block);
    // The composer's listeners (height, @-chips, this button, the saved
    // draft) follow the new text as they follow typing.
    box.dispatchEvent(new Event("input", { bubbles: true }));
  }
  openNotebook();
  renderComposerRun();
  return ran;
}

let watching: (() => void) | null = null;

/**
 * Repaint when the switch, the REPL's busy state, the session or the language
 * changes. Text changes repaint through `renderComposerRefChips`, which every
 * composer edit (typing, a send, a draft switch) already calls.
 */
export function watchComposerRun(): void {
  if (watching) return;
  watching = effect(() => {
    void replEnabled.value;
    void replBusy.value;
    void currentId.value;
    void languageRevision.value;
    untracked(renderComposerRun);
  });
}
