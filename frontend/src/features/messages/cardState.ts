/**
 * The live activity card's state, from "running" to the cell's own outcome.
 *
 * `stream.ts` builds one `.activity` card per Cell from the `⚙` header chunk.
 * It used to draw the success check at creation, over the green bar
 * (style.css `.activity`) and the server title "Running analysis · cell N",
 * and only a Stop repainted it. A cell that raised therefore kept "✓ Running
 * analysis · cell 1" directly above "This cell failed: ZeroDivisionError" for
 * as long as the page stayed open.
 *
 * The server already says how every Notebook cell ended:
 * `notebook_cell_finished` carries `producing_cell_id` and
 * `status: ok | error | interrupted`. The Notebook's handler forwards it here
 * (one WS handler per type), and `turnDone` settles any card whose outcome
 * never arrived (a completion-only cell, a socket that reconnected past the
 * event) so no card is left claiming it is still running.
 */

import { LANG } from "../../i18n/runtime";
import { paintIcon } from "../icons/paths";
import type { WsMessage } from "../ws/types";
import { messagesHost } from "./dom";

/** Feature-local copy: en.ts / zh.ts are generated extracts. */
const COPY: Record<"en" | "zh", Record<"cardTitle" | "stopped" | "failed", string>> = {
  en: { cardTitle: "Analysis · cell {0}", stopped: "Stopped", failed: "Failed" },
  zh: { cardTitle: "分析 · 单元 {0}", stopped: "已停止", failed: "失败" },
};

function copy(key: "cardTitle" | "stopped" | "failed"): string {
  return (COPY[LANG === "zh" ? "zh" : "en"] || COPY.en)[key];
}

/** `cell_run.activity_title` for a cell with no leading comment of its own. */
const DEFAULT_CELL_TITLE = /^Running analysis · cell (\d+)$/;

/** Every settled state a card can reach; "running" is the only unsettled one. */
const SETTLED = new Set(["ok", "failed", "stopped", "ended"]);

function glyphOf(card: HTMLElement): HTMLElement | null {
  return card.querySelector(".ic") as HTMLElement | null;
}

/**
 * The generated "Running …" title stops being true once the cell ends. A title
 * the cell's leading comment supplied is the author's and stays.
 */
function retitle(card: HTMLElement): void {
  const label = card.querySelector(".lbl") as HTMLElement | null;
  const generated = label ? DEFAULT_CELL_TITLE.exec(String(label.textContent || "").trim()) : null;
  if (label && generated) label.textContent = copy("cardTitle").replace("{0}", generated[1] || "");
}

function prefixMeta(card: HTMLElement, word: string): void {
  const meta = card.querySelector(".meta") as HTMLElement | null;
  if (!meta) return;
  const lines = String(meta.textContent || "").trim();
  meta.textContent = word + (lines ? " · " + lines : "");
}

/** True once a card carries an outcome; a legacy card with no state is not. */
export function cardSettled(card: HTMLElement): boolean {
  return SETTLED.has(String(card.dataset.state || ""));
}

function settle(card: HTMLElement, state: string, icon: string): void {
  card.classList.remove("running");
  card.classList.add(state);
  card.dataset.state = state;
  const glyph = glyphOf(card);
  if (glyph) paintIcon(glyph, icon, 16);
  retitle(card);
}

/**
 * A new Cell card: a progress glyph, not a success check, and the identity of
 * the cell it belongs to so a later outcome repaints this card and no other.
 */
export function markCardRunning(card: HTMLElement, event?: WsMessage | null): void {
  card.classList.add("running");
  card.dataset.state = "running";
  const id = event && (event.producing_cell_id || event.cell_id);
  if (id) card.dataset.cellId = String(id).slice(0, 128);
  if (event && event.cell_index != null) card.dataset.cellIndex = String(event.cell_index).slice(0, 32);
  const glyph = glyphOf(card);
  if (glyph) paintIcon(glyph, "loader", 16);
}

/** Stop glyph, neutral bar, "Stopped · N lines". Idempotent; an outcome wins. */
export function markCardStopped(card: HTMLElement): void {
  if (cardSettled(card)) return;
  settle(card, "stopped", "stop");
  prefixMeta(card, copy("stopped"));
}

export function markCardFailed(card: HTMLElement): void {
  if (cardSettled(card)) return;
  settle(card, "failed", "x");
  prefixMeta(card, copy("failed"));
}

export function markCardSucceeded(card: HTMLElement): void {
  if (cardSettled(card)) return;
  settle(card, "ok", "check");
}

/** The turn ended and this card's outcome never arrived: claim nothing. */
export function markCardEnded(card: HTMLElement): void {
  if (cardSettled(card)) return;
  settle(card, "ended", "circle-dot");
}

function liveCards(): HTMLElement[] {
  const host = messagesHost();
  return host ? ([...host.querySelectorAll(".activity")] as HTMLElement[]) : [];
}

/**
 * Apply one `notebook_cell_finished` to the card of the cell it names. Matched
 * by `producing_cell_id`; the cell index is only a fallback for a running card
 * that carries no id, so a later cell's card is never repainted for an earlier
 * cell's outcome.
 */
export function settleLiveCellCard(event: WsMessage | null | undefined): void {
  if (!event) return;
  const id = String(event.producing_cell_id || event.cell_id || "");
  const index = event.cell_index != null ? String(event.cell_index) : "";
  const running = liveCards().filter((card) => card.dataset.state === "running");
  const card =
    (id && running.find((c) => c.dataset.cellId === id)) ||
    (index && running.find((c) => !c.dataset.cellId && c.dataset.cellIndex === index)) ||
    null;
  if (!card) return;
  const status = String(event.status || (event.error ? "error" : "ok"));
  if (status === "error") markCardFailed(card);
  else if (status === "interrupted") markCardStopped(card);
  else if (status === "ok") markCardSucceeded(card);
}

/** Terminal fallback: no card on screen outlives its turn as "running". */
export function settleRunningCards(): void {
  for (const card of liveCards()) {
    if (card.dataset.state === "running") markCardEnded(card);
  }
}
