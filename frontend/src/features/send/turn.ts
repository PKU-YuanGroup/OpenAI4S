/**
 * Turn teardown. Port of app.js:5799-5874.
 *
 * F-14 owns `_kc` invalidation: `notebookOnTurnDone()` is the hook it
 * published for this function. Do not register a second `frame_update`.
 */

import { t } from "../../i18n/runtime";
import { _liveCell, liveCells } from "../../stores/notebook";
import { _openGen, currentId, historyLoad, noteHistoryMutation } from "../../stores/session";
import {
  _resumeTimer,
  _resumeTok,
  pendingRequestId,
  planPending,
  planPendingTurn,
  planReady,
  planStatus,
  running,
  stream as liveStream,
} from "../../stores/stream";
import { loadArtifacts } from "../artifacts/load";
import { syncPendingApproval } from "../messages/approval";
import { settleRunningCards } from "../messages/cardState";
import { $ } from "../messages/dom";
import {
  canContinueFailure,
  canRetryFailure,
  failureCodeHint,
  failureMeta,
  needsModelSettings,
} from "../messages/failure";
export { failureCodeHint, failureMeta } from "../messages/failure";
import { finishStoppedStream } from "../messages/stopped";
import { flushRender, type LiveStream } from "../messages/stream";
import { notebookOnTurnDone } from "../notebook/kernel";
import { hint } from "../sessions/chrome";
import { enableComposer, grow } from "../sessions/dom";
import { finishTurnGroup } from "../messages/list";
import { turnTextKey } from "../messages/turnGroup";
import { callLane, setCancelHidden } from "./host";
import { renderPlanCard, showPlanApproval } from "./plan";
import { closeTurnTicket } from "./ticket";

export function lastTerminalFailure(): {
  request_id: string;
  code: string;
  output_committed: boolean;
} | null {
  if (typeof document === "undefined") return null;
  const rows = [...document.querySelectorAll("#messages .msg")];
  const last = rows[rows.length - 1];
  if (!last) return null;
  const box = last.querySelector(".msg-failure-meta") as HTMLElement | null;
  return box
    ? {
        request_id: box.dataset.requestId || "",
        code: box.dataset.failureCode || "",
        output_committed: box.dataset.committed === "1",
      }
    : null;
}

/**
 * The line under the composer for a failed turn. `shown`: the failed turn's
 * bubble already carries it (its sentence, its support ID and its button), so
 * the line does not say it a second time in other words (M-37): a failure
 * fixed in Settings, or a stop that Continue picks up (SYNC-16: "Stopped
 * repeating actions" was said twice, worded two ways), leaves it empty; any
 * other keeps a short pointer without the ID.
 */
export function failureHint(detail: unknown, shown = false): string {
  const rec = detail && typeof detail === "object" ? (detail as Record<string, unknown>) : null;
  if (shown) return needsModelSettings(rec?.code) || canContinueFailure(rec?.code) ? "" : t("turn.failed");
  const committed = !!(rec && rec.output_committed) && !canContinueFailure(rec?.code);
  const cause = failureCodeHint(rec && rec.code);
  const base = committed
    ? [t("turn.failedCommitted"), cause].filter(Boolean).join(" ")
    : cause || t("turn.failed");
  const raw = (rec && rec.request_id) || pendingRequestId.value || "";
  const id = raw ? String(raw).slice(0, 96) : "";
  return id ? base + " " + t("turn.supportId", id) : base;
}

/**
 * M-37: a turn that failed on the model's setup (no key, a rejected key, no
 * quota, no such model) before anything ran left its question only in the
 * bubble; after fixing Settings the user had to type it again. It goes back
 * into an empty composer. A lost connection has Retry instead, and a turn
 * that already ran a step, a cell or an approval is not offered again as is.
 */
function restoreUnansweredQuestion(wrap: HTMLElement, detail: unknown): void {
  const code = detail && typeof detail === "object" ? (detail as { code?: unknown }).code : null;
  if (!needsModelSettings(code) || canRetryFailure(code)) return;
  if ([".step", ".activity", ".perm-card"].some((kind) => wrap.querySelector(kind))) return;
  const asked = [...($("#messages")?.querySelectorAll(".msg.user") || [])].pop() as HTMLElement | undefined;
  const text = asked?.dataset.sentText || "";
  const composer = $("#composer") as HTMLTextAreaElement | null;
  if (!text || !composer || composer.value.trim()) return;
  composer.value = text;
  grow();
}

/** Terminal statuses of a turn that ran to its end (not failed, stopped or blocked). */
const PLAN_TURN_FINISHED = ["completed", "success", "done", "ready"];

/**
 * What the Stop that ends this turn wants the status line to keep saying (the
 * background cells it stopped too, the queued message that runs next). The
 * accepted cancel usually answers before the `cancelled` event, whose
 * teardown used to clear that line at once.
 */
let stopNote = "";
/** The part worth repeating when a queued message starts right after the Stop. */
let stopCarry = "";
let carried = { text: "", at: 0 };

export function noteForStop(text: string, carry = ""): void {
  if (running.value) {
    stopNote = text;
    stopCarry = carry;
  } else if (carry) {
    // The turn already ended (its `cancelled` event beat the answer).
    carried = { text: carry, at: Date.now() };
  }
}

/** What the last Stop said that the next queued turn's start should keep saying. */
export function takeStopCarry(withinMs = 15000): string {
  const text = Date.now() - carried.at <= withinMs ? carried.text : "";
  carried = { text: "", at: 0 };
  return text;
}

export function turnDone(status: string, detail?: unknown): void {
  noteHistoryMutation();
  running.value = false;
  enableComposer(true);
  setCancelHidden(true);
  clearTimeout(_resumeTimer.value as ReturnType<typeof setTimeout>);
  _resumeTok.value = (_resumeTok.value || 0) + 1;
  const st = liveStream.value as LiveStream | null;
  if (st && status === "cancelled") finishStoppedStream(st, detail);
  settleRunningCards();
  if (st) {
    flushRender(st, true);
    st.md.classList.remove("cursor");
    // The same ending a stored turn gets on reopen: one action row for the turn.
    finishTurnGroup(st.wrap, st.full || st.text);
    if (status === "failed" && detail && typeof detail === "object" && !st.wrap.querySelector(".msg-failure-meta")) {
      st.wrap.appendChild(failureMeta(detail));
    }
  }
  const mm = $("#messages");
  if (mm) mm.querySelectorAll(".md.cursor").forEach((n) => n.classList.remove("cursor"));
  const note = status === "cancelled" ? stopNote : "";
  if (status === "cancelled" && stopCarry) carried = { text: stopCarry, at: Date.now() };
  stopNote = "";
  stopCarry = "";
  const shown = status === "failed" && !!st && !!st.wrap.querySelector(".msg-failure-meta");
  const failedLine = status === "failed" ? failureHint(detail, shown) : "";
  hint(status === "failed" ? failedLine : note, status === "failed" && !!failedLine);
  if (status === "failed" && st) restoreUnansweredQuestion(st.wrap, detail);
  // A turn that ended took its approval wait with it (an open card left over
  // is answered by `reconcilePermissionCards`); the title and row follow.
  syncPendingApproval();
  closeTurnTicket();
  notebookOnTurnDone();
  if (currentId.value) {
    void loadArtifacts(currentId.value);
    callLane("loadExecutionLog", currentId.value);
  }
  liveStream.value = null;
  if (currentId.value && (historyLoad.value?.deferred || historyLoad.value?.status === "loading")) {
    const fid = currentId.value;
    const gen = _openGen.value;
    // Defer past the synchronous WS dispatcher so its cursor is committed.
    void Promise.resolve().then(() => {
      if (currentId.value === fid && _openGen.value === gen) callLane("alignHistoryAfterTurn", fid, gen);
    });
  } else if (st && currentId.value) {
    // SYNC-13: what streamed but was not stored (a retry notice, a sentence a
    // Stop cut off) goes, so the turn reads as a reload shows it.
    const fid = currentId.value;
    const gen = _openGen.value;
    const liveKey = turnTextKey(st.full || st.text);
    void Promise.resolve().then(() => {
      if (currentId.value === fid && _openGen.value === gen) callLane("reconcileFinishedTurn", fid, gen, liveKey);
    });
  }
  liveCells.value = [];
  _liveCell.value = null;
  if (planReady.value && planStatus.value === "executing") {
    renderPlanCard(
      planReady.value,
      ["failed", "blocked_by_guardian"].includes(status) ? "failed" : "completed",
    );
  }
  if (planPending.value && !endedAnotherTurn(detail)) {
    // The flag belongs to the plan-mode turn that just ended, however it
    // ended; left set by a failure, the next ordinary turn offered to approve
    // a plan. Only a turn that finished produced something to approve --
    // a stopped or guardian-blocked one did not.
    planPending.value = false;
    planPendingTurn.value = null;
    if (PLAN_TURN_FINISHED.includes(status) && !planReady.value) showPlanApproval();
  }
}

/**
 * Whether the turn that just ended is not the plan-mode turn `planPending`
 * waits on. Once the server named the plan turn, only its own execution
 * ends it (an end with no id is a REPL cell's). Queued and not yet named,
 * an execution that ended with an id is the turn it waited behind. Sent
 * with nothing running, the turn that ends is the plan turn itself.
 */
function endedAnotherTurn(detail: unknown): boolean {
  const turn = planPendingTurn.value;
  if (!turn) return false;
  const raw = detail && typeof detail === "object" ? (detail as { execution_id?: unknown }).execution_id : null;
  const ended = typeof raw === "string" && raw ? raw : null;
  if (turn.executionId) return ended !== turn.executionId;
  return turn.queued && !!ended;
}
