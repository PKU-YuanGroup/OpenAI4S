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
 *
 * CHAT-7: "Thinking · 2 min 0 s" said nothing about a two-minute wait, and a
 * reload started it again at 0 beside a turn clock that kept the true time.
 * The model phase now says which of three things it is -- waiting for the
 * model's first words, the model writing, or a request being retried (the
 * server streams a notice for each retry, `completions.llm_retry_notice`) --
 * and counts from the later of the turn's server start and the end of the
 * turn's last cell or tool step, whose recorded times a reopen restores.
 */

import { effect, untracked } from "@preact/signals";
import { isReady } from "../../compat/stub";
import { LANG, onLanguageChange, t } from "../../i18n/runtime";
import { _liveCell } from "../../stores/notebook";
import { S } from "../timeline/s";
import { currentId } from "../../stores/session";
import { running, stepEls, stream as liveStream } from "../../stores/stream";
import { onSessionEvent } from "../ws/connect";
import { approvalPendingFor } from "./approval";
import { el, messagesHost } from "./dom";
import { isProgressLine } from "./progress";
import { down } from "./scroll";

const COPY: Record<"en" | "zh", Record<string, string>> = {
  en: {
    thinking: "Waiting for the model",
    generating: "The model is writing",
    retrying: "Retrying (attempt {0})",
    writing: "Writing a cell",
    running: "Running the cell",
    tool: "Waiting for the tool",
    approval: "Waiting for your approval",
    line: "{0} line so far",
    lines: "{0} lines so far",
    seconds: "{0} s",
    minutes: "{0} min {1} s",
  },
  zh: {
    thinking: "等待模型响应",
    generating: "正在生成",
    retrying: "正在重试（第 {0} 次）",
    writing: "正在编写单元",
    running: "正在运行单元",
    tool: "等待工具返回",
    approval: "等待你的审批",
    line: "已 {0} 行",
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
let shownWords = "";
let since = 0;
let timer: ReturnType<typeof setInterval> | null = null;

/** When the running turn began on this page (the server's start is preferred). */
let localTurnStart = 0;
/** The latest end of a cell or tool step of the open session, in ms. */
let lastActivityEnd = 0;
/** Event order: the model's own text, and everything else the turn did. */
let order = 0;
let textOrder = 0;
let activityOrder = 0;
let retry: { attempt: number; order: number } | null = null;

/** `completions.llm_retry_notice`, in either language: the attempt about to be made. */
const RETRY_NOTICE = /\(attempt (\d+)\)…_|（第 (\d+) 次）…_/;

/**
 * A chunk of the turn's prose arrived (`stream.ts` `feed`). The model's own
 * words mean it is writing; a retry notice that a request is being sent
 * again; a server progress line is the turn acting, not the model writing.
 */
export function noteModelText(chunk: string): void {
  const text = String(chunk || "");
  if (!text.trim()) return;
  order += 1;
  const notice = RETRY_NOTICE.exec(text);
  if (notice) retry = { attempt: Number(notice[1] || notice[2]) || 2, order };
  else if (isProgressLine(text)) activityOrder = order;
  else textOrder = order;
  if (shown) syncTurnStatus();
}

/**
 * A cell or tool step ended at `at` (ms): the model is asked again from
 * then on. A recorded card passes its recorded time, so a reopened running
 * session counts from when that really happened, not from the reload.
 */
export function noteTurnActivity(at: number = Date.now()): void {
  activityOrder = ++order;
  if (Number.isFinite(at) && at > lastActivityEnd) lastActivityEnd = at;
  if (shown) syncTurnStatus();
}

/** The running agent execution's own start, when the queue snapshot names it (`send/progress.ts`). */
function executionStart(): number | null {
  const owner = ((S.executionQueue || {}) as { owner?: { owner?: { kind?: unknown }; started_at?: unknown } }).owner;
  const startedAt = owner && owner.owner && owner.owner.kind === "agent" ? Number(owner.started_at) : NaN;
  return Number.isFinite(startedAt) && startedAt > 0 ? startedAt * 1000 : null;
}

/** When the model phase began: after the turn started and after its last cell or step ended. */
function modelPhaseSince(now: number): number {
  const turn = executionStart() ?? (localTurnStart || now);
  return Math.min(now, Math.max(turn, lastActivityEnd));
}

/** What the model phase is: waiting, writing, or retrying a request. */
function modelPhaseWords(): string {
  if (retry && retry.order > Math.max(textOrder, activityOrder)) return copy("retrying", retry.attempt);
  return textOrder > activityOrder ? copy("generating") : copy("thinking");
}

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

/**
 * "Still running in the background, resuming…" is true until the stream is
 * back, and that is the first event of the open session that arrives: it
 * stayed for the whole turn while the step cards were visibly updating
 * (SYNC-3). Without any event (a long model wait) the passing-line timer
 * still retires it.
 */
let resuming = "";

/** Call right after painting the resume line (messages/open.ts). */
export function noteResumingHint(): void {
  notePassingHint();
  resuming = passing ? passing.text : "";
}

/** A live event of the open session arrived (ws/connect.ts): the resume line has done its job. */
export function noteSessionEvent(): void {
  if (!resuming) return;
  const text = resuming;
  resuming = "";
  if (!running.value || hintText() !== text) return;
  if (passing && passing.text === text) passing = null;
  setHint(t("toast.running"));
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
  shownWords = "";
  passing = null;
  resuming = "";
  localTurnStart = 0;
  retry = null;
}

/** Another session opened: what this one's cards ended at says nothing about it. */
function forgetSessionActivity(): void {
  lastActivityEnd = 0;
  textOrder = activityOrder = 0;
  retry = null;
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
  if (!localTurnStart) localTurnStart = now;
  if (state.phase !== shown) {
    // The first phase is the turn starting; any later one is the turn moving on.
    if (shown) {
      retirePassingHint();
      activityOrder = ++order;
    }
    shown = state.phase;
    since = now;
  } else if (passing && now - passing.at >= PASSING_MS) retirePassingHint();
  const model = state.phase === "thinking";
  const words = model ? modelPhaseWords() : copy(state.phase);
  // Rewritten only when it changes: the row is a polite live region.
  if (label && words !== shownWords) {
    label.textContent = words;
    shownWords = words;
  }
  if (detail) {
    const lines = state.lines === 1 ? "line" : "lines";
    const from = model ? modelPhaseSince(now) : since;
    const parts = [state.lines ? copy(lines, state.lines) : "", elapsedText(now - from)];
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
  onSessionEvent(noteSessionEvent);
  let openId: unknown = currentId.peek();
  bound = [
    effect(() => {
      const id = currentId.value;
      untracked(() => {
        if (id !== openId) {
          openId = id;
          forgetSessionActivity();
          // The row's counts restart for the session now on screen.
          shown = null;
          localTurnStart = 0;
        }
      });
    }),
    effect(() => {
      void running.value;
      void _liveCell.value;
      void approvalPendingFor.value;
      void currentId.value;
      void liveStream.value;
      untracked(() => syncTurnStatus());
    }),
    // I18N-1: its words in the language just applied, at once.
    onLanguageChange(() => {
      if (shown) syncTurnStatus();
    }),
  ];
}

/** Test seam, and the page's teardown: no timer or effect outlives it. */
export function resetTurnStatus(): void {
  stop();
  forgetSessionActivity();
  onSessionEvent(null);
  bound?.forEach((dispose) => dispose());
  bound = null;
  row = null;
  label = null;
  detail = null;
}
