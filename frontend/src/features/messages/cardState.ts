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

import { LANG, t } from "../../i18n/runtime";
import { paintIcon } from "../icons/paths";
import { nbT } from "../notebook/copy";
import type { WsMessage } from "../ws/types";
import { declinedCell, declinedMeta, type Declined } from "./declined";
import { messagesHost } from "./dom";
import { noteTurnActivity } from "./turnStatus";

/** Feature-local copy: en.ts / zh.ts are generated extracts. */
type CardCopyKey = "cardTitle" | "runningTitle" | "stopped" | "failed" | "noR" | "rJsonlite";
const COPY: Record<"en" | "zh", Record<CardCopyKey, string>> = {
  en: {
    cardTitle: "Analysis · cell {0}",
    runningTitle: "Running analysis · cell {0}",
    stopped: "Stopped",
    failed: "Failed",
    noR:
      "No R is available to this session. Choose one under {0}, build the prebuilt R environment with `openai4s setup`, or install R so Rscript is on PATH.",
    rJsonlite:
      "This R does not have the jsonlite package the R kernel needs. Choose an R that has it under {0}, or run install.packages(\"jsonlite\") in this R.",
  },
  zh: {
    cardTitle: "分析 · 单元 {0}",
    runningTitle: "正在分析 · 单元 {0}",
    stopped: "已停止",
    failed: "失败",
    noR: "本会话没有可用的 R。可以在 {0} 里选择一个 R，用 `openai4s setup` 构建预置的 R 环境，或安装 R 并让 Rscript 位于 PATH 上。",
    rJsonlite: "这个 R 缺少 R 内核需要的 jsonlite 包。可以在 {0} 里换一个装有它的 R，或在这个 R 中运行 install.packages(\"jsonlite\")。",
  },
};

function copy(key: CardCopyKey): string {
  return (COPY[LANG === "zh" ? "zh" : "en"] || COPY.en)[key];
}

/** `cell_run.activity_title` for a cell with no leading comment of its own. */
const DEFAULT_CELL_TITLE = /^Running analysis · cell (\d+)$/;

/**
 * The cell number of a card whose title the server generated, or null when
 * the title is the cell's own. The running title is shown in the UI language
 * (I18N-1: "Running analysis · cell 1" in a Chinese UI until the cell ended),
 * so the number is kept on the card rather than read back from the label.
 */
function generatedCellNumber(card: HTMLElement, label: HTMLElement | null): string | null {
  if (card.dataset.generatedCell) return card.dataset.generatedCell;
  const match = label ? DEFAULT_CELL_TITLE.exec(String(label.textContent || "").trim()) : null;
  return match ? match[1] || null : null;
}

/** Every settled state a card can reach; "running" is the only unsettled one. */
const SETTLED = new Set(["ok", "failed", "stopped", "ended", "declined"]);

function glyphOf(card: HTMLElement): HTMLElement | null {
  return card.querySelector(".ic") as HTMLElement | null;
}

/**
 * The generated "Running …" title stops being true once the cell ends. A title
 * the cell's leading comment supplied is the author's and stays.
 */
function retitle(card: HTMLElement): void {
  const label = card.querySelector(".lbl") as HTMLElement | null;
  const cell = generatedCellNumber(card, label);
  if (label && cell != null) label.textContent = copy("cardTitle").replace("{0}", cell);
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
  paintElapsed(card);
  // When it ended: the recorded end for a Cell the execution log has, so a
  // reopened turn's model wait counts from it (CHAT-7, `turnStatus.ts`).
  const recordedStart = Number(card.dataset.recordedStart);
  const recordedMs = card.dataset.recordedMs ? Number(card.dataset.recordedMs) : NaN;
  noteTurnActivity(recordedStart > 0 && Number.isFinite(recordedMs) ? recordedStart + recordedMs : Date.now());
  card.classList.remove("running");
  card.classList.add(state);
  card.dataset.state = state;
  const glyph = glyphOf(card);
  if (glyph) paintIcon(glyph, icon, 16);
  retitle(card);
}

/**
 * A running cell's elapsed time on its card. A long cell with no output used
 * to show one still glyph for minutes; the time keeps moving while it runs and
 * stays as the cell's duration once it ends. Shown from two seconds on.
 */
function paintElapsed(card: HTMLElement, now: number = Date.now()): void {
  // NB-4: a Cell the execution log already recorded shows its recorded
  // duration. Reopening a running session replays the turn into the card the
  // reload drew, and the replayed finish lands a moment after the replayed
  // start: timed on this page, "0:03" became nothing.
  const recorded = card.dataset.recordedMs ? Number(card.dataset.recordedMs) : NaN;
  if (Number.isFinite(recorded)) {
    paintDuration(card, recorded);
    return;
  }
  const started = Number(card.dataset.startedAt || 0);
  if (!started) return;
  paintDuration(card, Math.max(0, now - started));
}

function paintDuration(card: HTMLElement, ms: number): void {
  const head = card.querySelector(".a-head") as HTMLElement | null;
  if (!head) return;
  let label = head.querySelector(".a-time") as HTMLElement | null;
  if (ms < 2000) return;
  if (!label) {
    label = document.createElement("span");
    label.className = "a-time";
    head.appendChild(label);
  }
  label.textContent = formatElapsed(ms);
}

function formatElapsed(ms: number): string {
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const ss = String(total % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

let elapsedTimer: ReturnType<typeof setInterval> | null = null;

function tickElapsed(): void {
  const running = liveCards().filter((card) => card.dataset.state === "running");
  if (!running.length && elapsedTimer) {
    clearInterval(elapsedTimer);
    elapsedTimer = null;
    return;
  }
  const now = Date.now();
  running.forEach((card) => paintElapsed(card, now));
}

/**
 * A new Cell card: a progress glyph, not a success check, and the identity of
 * the cell it belongs to so a later outcome repaints this card and no other.
 */
export function markCardRunning(card: HTMLElement, event?: WsMessage | null): void {
  card.classList.add("running");
  card.dataset.state = "running";
  const label = card.querySelector(".lbl") as HTMLElement | null;
  const cell = generatedCellNumber(card, label);
  if (label && cell != null) {
    card.dataset.generatedCell = cell;
    label.textContent = copy("runningTitle").replace("{0}", cell);
  }
  const id = event && (event.producing_cell_id || event.cell_id);
  if (id) card.dataset.cellId = String(id).slice(0, 128);
  if (event && event.cell_index != null) card.dataset.cellIndex = String(event.cell_index).slice(0, 32);
  const glyph = glyphOf(card);
  if (glyph) paintIcon(glyph, "loader", 16);
  // A recorded Cell replayed into its reopened card counts from when it
  // really started, not from the replay (NB-4). A Cell still running at the
  // reopen has no recorded row yet; its start event carries the server's
  // start time instead.
  const recordedStart = Number(card.dataset.recordedStart);
  const eventStart = Number(event && event.started_at);
  const started = recordedStart > 0 ? recordedStart : eventStart > 0 ? eventStart : Date.now();
  card.dataset.startedAt = String(started);
  if (!elapsedTimer && typeof setInterval === "function") elapsedTimer = setInterval(tickElapsed, 1000);
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

/**
 * The cell ended because a Host call in it was refused (denied, or refused by
 * a rule): a lock and a neutral bar, "You declined · not run", not the failed
 * card's red ✗.
 */
export function markCardDeclined(card: HTMLElement, declined: Declined): void {
  if (cardSettled(card)) return;
  settle(card, "declined", "lock");
  prefixMeta(card, declinedMeta(declined));
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
  applyCellOutcome(card, String(event.status || (event.error ? "error" : "ok")), event.error);
}

/**
 * M-58: the R kernel's own refusals reach the card as the server's English
 * text ("no R interpreter available: …", "openai4s R worker requires the
 * 'jsonlite' package …"), and the raw output is not translated. A failed card
 * that carries one gets a line in the UI language saying where to choose
 * another R, live and on reopen alike.
 */
const RUNTIME_HELP: Array<[RegExp, CardCopyKey]> = [
  [/no R interpreter available/, "noR"],
  [/R worker requires the 'jsonlite' package/, "rJsonlite"],
];

function noteRuntimeHelp(card: HTMLElement, error: unknown): void {
  const text = typeof error === "string" ? error : "";
  const hit = RUNTIME_HELP.find(([pattern]) => pattern.test(text));
  if (!hit || typeof document === "undefined") return;
  if (Array.from(card.children || []).some((node) => node.classList?.contains("cell-help"))) return;
  const where = t("composer.sessionOptions") + " → " + nbT("nb.env.menuItem");
  const line = document.createElement("div");
  line.className = "cell-help";
  line.textContent = copy(hit[1]).replace("{0}", where);
  card.appendChild(line);
}

function applyCellOutcome(card: HTMLElement, status: string, error: unknown): void {
  // A cell that raised because its Host call was refused, or cancelled by a
  // Stop while it waited for approval, did not fail.
  const declined = status === "error" ? declinedCell(error) : null;
  if (declined && declined.kind === "stopped") markCardStopped(card);
  else if (declined) markCardDeclined(card, declined);
  else if (status === "error") {
    markCardFailed(card);
    noteRuntimeHelp(card, error);
  } else if (status === "interrupted") markCardStopped(card);
  else if (status === "ok") markCardSucceeded(card);
}

/**
 * A reopened Cell's card (`cellCard.ts`): the identity, outcome and duration
 * its execution-log record names, painted as the live card ended up -- the
 * same glyph, "Failed · N output lines" meta and "Analysis · cell N" title.
 */
export function settleStoredCellCard(
  card: HTMLElement,
  cell: {
    producing_cell_id?: unknown;
    cell_index?: unknown;
    status?: unknown;
    error?: unknown;
    started_at?: unknown;
    finished_at?: unknown;
  },
): void {
  if (typeof cell.producing_cell_id === "string") card.dataset.cellId = cell.producing_cell_id.slice(0, 128);
  if (cell.cell_index != null) card.dataset.cellIndex = String(cell.cell_index).slice(0, 32);
  const started = Number(cell.started_at) || 0;
  const finished = Number(cell.finished_at) || 0;
  // Kept on the card for a replay of this Cell (`resetActivityCard` leaves
  // them): the duration it shows is the recorded one (NB-4).
  if (started) card.dataset.recordedStart = String(started);
  if (started && finished >= started) {
    card.dataset.recordedMs = String(finished - started);
    paintDuration(card, finished - started);
  }
  applyCellOutcome(card, String(cell.status || (cell.error ? "error" : "ok")), cell.error);
  // A record whose outcome none of the above names claims nothing.
  if (!cardSettled(card)) markCardEnded(card);
}

/** Terminal fallback: no card on screen outlives its turn as "running". */
export function settleRunningCards(): void {
  for (const card of liveCards()) {
    if (card.dataset.state === "running") markCardEnded(card);
  }
}
