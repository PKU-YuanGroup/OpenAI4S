/**
 * What the running turn is doing, in place, at the end of the conversation.
 *
 * Waiting on the model was most of a turn's wall time (single calls of a
 * minute or two) and the conversation showed nothing meanwhile: a small
 * "Running…" under the composer, and the cell being drafted only in the
 * Notebook tab. One row after the live turn now says which of five things is
 * going on -- thinking, writing a cell (N lines), running it, waiting for a
 * tool, waiting for the user's approval -- and for how long.
 *
 * The phase is derived, never stored: from `running`, the Notebook's live
 * cell, the open approval card, and the step cards still running inside the
 * live turn. So a missed event cannot leave it stuck on a phase the screen
 * contradicts, and the row is gone the moment the turn is.
 */

import { effect, untracked } from "@preact/signals";
import { isReady } from "../../compat/stub";
import { LANG, t } from "../../i18n/runtime";
import { _liveCell } from "../../stores/notebook";
import { currentId } from "../../stores/session";
import { running, stepEls, stream as liveStream } from "../../stores/stream";
import { approvalPendingFor } from "./approval";
import { el, messagesHost } from "./dom";
import { down } from "./scroll";

const COPY: Record<"en" | "zh", Record<string, string>> = {
  en: {
    thinking: "Thinking",
    writing: "Writing a cell",
    running: "Running the cell",
    tool: "Waiting for the tool",
    approval: "Waiting for your approval",
    lines: "{0} lines so far",
    seconds: "{0} s",
    minutes: "{0} min {1} s",
  },
  zh: {
    thinking: "正在思考",
    writing: "正在编写单元",
    running: "正在运行单元",
    tool: "等待工具返回",
    approval: "等待你的审批",
    lines: "已 {0} 行",
    seconds: "已 {0} 秒",
    minutes: "已 {0} 分 {1} 秒",
  },
};

function copy(key: string, ...args: Array<string | number>): string {
  let text = (COPY[LANG === "zh" ? "zh" : "en"] || COPY.en)[key] || key;
  args.forEach((value, index) => {
    text = text.split("{" + index + "}").join(String(value));
  });
  return text;
}

export type TurnPhase = "thinking" | "writing" | "running" | "tool" | "approval";

type LiveCell = { draft?: unknown; status?: unknown; source?: unknown; origin?: unknown };
type StepHandle = { card?: Node; step?: { status?: unknown } };

/** A step card still running inside the live turn (not an older turn's leftover). */
function toolRunning(): boolean {
  const wrap = (liveStream.value as { wrap?: HTMLElement } | null)?.wrap;
  if (!wrap) return false;
  return Object.values((stepEls.value || {}) as Record<string, StepHandle | undefined>).some(
    (handle) => !!handle && !!handle.card && handle.step?.status === "running" && wrap.contains(handle.card),
  );
}

/** The phase the screen's own state implies, and the cell lines drafted so far. */
export function turnPhase(): { phase: TurnPhase; lines: number } | null {
  if (!running.value) return null;
  const cell = (_liveCell.value as LiveCell | null) || null;
  const lines = cell && cell.draft && typeof cell.source === "string" && cell.source ? cell.source.split("\n").length : 0;
  if (approvalPendingFor.value && approvalPendingFor.value === currentId.value) return { phase: "approval", lines: 0 };
  if (toolRunning()) return { phase: "tool", lines: 0 };
  if (cell && !cell.draft && cell.status === "running" && cell.origin !== "notebook") return { phase: "running", lines: 0 };
  if (cell && cell.draft) return { phase: "writing", lines };
  return { phase: "thinking", lines: 0 };
}

function elapsedText(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  return minutes ? copy("minutes", minutes, total % 60) : copy("seconds", total);
}

let row: HTMLElement | null = null;
let label: HTMLElement | null = null;
let detail: HTMLElement | null = null;
let shown: TurnPhase | null = null;
let since = 0;
let timer: ReturnType<typeof setInterval> | null = null;

/** A status-line text that describes a moment, not the turn: replaced once the turn moves on. */
let passing: { text: string; at: number } | null = null;
/** A passing line that nothing replaced is stale after this long. */
const PASSING_MS = 8000;

function hintText(): string {
  if (typeof document === "undefined") return "";
  return document.getElementById("composer-hint")?.textContent || "";
}

/** `sessions/chrome` `hint`, by its window name (`sessions/dom` imports `scroll.ts`). */
function setHint(text: string): void {
  const fn = (globalThis as { hint?: unknown }).hint;
  if (isReady(fn)) (fn as (m: string, e?: boolean, s?: boolean) => void)(text, false, true);
}

/**
 * Call right after painting a status line that only describes a moment
 * ("resuming…", "now running your queued message"): it stayed for minutes.
 */
export function notePassingHint(): void {
  const text = hintText();
  passing = text ? { text, at: Date.now() } : null;
}

/** Put "Running…" back if the moment's line is still the one on screen. */
function retirePassingHint(): void {
  const stale = passing;
  passing = null;
  if (stale && running.value && hintText() === stale.text) setHint(t("toast.running"));
}

function build(): void {
  row = el("div", "turn-status");
  // Announced when the phase changes; the counters beside it are not.
  row.setAttribute("role", "status");
  row.setAttribute("aria-live", "polite");
  const dot = el("span", "ts-dot");
  dot.setAttribute("aria-hidden", "true");
  label = el("span", "ts-label");
  detail = el("span", "ts-detail");
  detail.setAttribute("aria-hidden", "true");
  row.appendChild(dot);
  row.appendChild(label);
  row.appendChild(detail);
}

/** After the live turn, or at the end while its first event is still to come. */
function place(): void {
  const host = messagesHost();
  if (!host || !row) return;
  const wrap = (liveStream.value as { wrap?: HTMLElement } | null)?.wrap;
  const after = wrap && wrap.parentNode === host ? wrap.nextSibling : null;
  if (wrap && wrap.parentNode === host) {
    if (after !== row) host.insertBefore(row, after);
  } else if (row.parentNode !== host || row.nextSibling) host.appendChild(row);
}

function stop(): void {
  if (timer) clearInterval(timer);
  timer = null;
  row?.remove();
  shown = null;
  passing = null;
}

/** Bring the row in line with the turn. Cheap; called on every relevant event. */
export function syncTurnStatus(now: number = Date.now()): void {
  const state = turnPhase();
  if (!state || typeof document === "undefined" || !messagesHost()) {
    stop();
    return;
  }
  if (!row) build();
  const appeared = !row?.isConnected;
  if (state.phase !== shown) {
    // The first phase is the turn starting; any later one is the turn moving on.
    if (shown) retirePassingHint();
    shown = state.phase;
    since = now;
    if (label) label.textContent = copy(state.phase);
  } else if (passing && now - passing.at >= PASSING_MS) retirePassingHint();
  if (detail) {
    const parts = [state.lines ? copy("lines", state.lines) : "", elapsedText(now - since)];
    detail.textContent = parts.filter(Boolean).join(" · ");
  }
  place();
  if (appeared) down();
  if (!timer) timer = setInterval(() => syncTurnStatus(), 1000);
}

let bound: Array<() => void> | null = null;

/** Bound once after the Shell has rendered (send.ts `bindComposer`). */
export function bindTurnStatus(): void {
  if (bound) return;
  bound = [
    effect(() => {
      void running.value;
      void _liveCell.value;
      void approvalPendingFor.value;
      void currentId.value;
      void liveStream.value;
      untracked(() => syncTurnStatus());
    }),
  ];
}

/** Test seam, and the page's teardown: no timer or effect outlives it. */
export function resetTurnStatus(): void {
  stop();
  bound?.forEach((dispose) => dispose());
  bound = null;
  row = null;
  label = null;
  detail = null;
}
