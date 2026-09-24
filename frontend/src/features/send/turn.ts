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
  planReady,
  planStatus,
  running,
  stream as liveStream,
} from "../../stores/stream";
import { loadArtifacts } from "../artifacts/load";
import { settleRunningCards } from "../messages/cardState";
import { $ } from "../messages/dom";
import { canContinueFailure, failureCodeHint, failureMeta } from "../messages/failure";
export { failureCodeHint, failureMeta } from "../messages/failure";
import { finishStoppedStream } from "../messages/stopped";
import { flushRender, type LiveStream } from "../messages/stream";
import { notebookOnTurnDone } from "../notebook/kernel";
import { hint } from "../sessions/chrome";
import { enableComposer } from "../sessions/dom";
import { addMsgActions } from "../messages/list";
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

export function failureHint(detail: unknown): string {
  const rec = detail && typeof detail === "object" ? (detail as Record<string, unknown>) : null;
  const committed = !!(rec && rec.output_committed) && !canContinueFailure(rec?.code);
  const cause = failureCodeHint(rec && rec.code);
  const base = committed
    ? [t("turn.failedCommitted"), cause].filter(Boolean).join(" ")
    : cause || t("turn.failed");
  const raw = (rec && rec.request_id) || pendingRequestId.value || "";
  const id = raw ? String(raw).slice(0, 96) : "";
  return id ? base + " " + t("turn.supportId", id) : base;
}

/** Terminal statuses of a turn that ran to its end (not failed, stopped or blocked). */
const PLAN_TURN_FINISHED = ["completed", "success", "done", "ready"];

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
    addMsgActions(st.wrap, st.full || st.text);
    if (status === "failed" && detail && typeof detail === "object" && !st.wrap.querySelector(".msg-failure-meta")) {
      st.wrap.appendChild(failureMeta(detail));
    }
  }
  const mm = $("#messages");
  if (mm) mm.querySelectorAll(".md.cursor").forEach((n) => n.classList.remove("cursor"));
  hint(status === "failed" ? failureHint(detail) : "", status === "failed");
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
  }
  liveCells.value = [];
  _liveCell.value = null;
  if (planReady.value && planStatus.value === "executing") {
    renderPlanCard(
      planReady.value,
      ["failed", "blocked_by_guardian"].includes(status) ? "failed" : "completed",
    );
  }
  if (planPending.value) {
    // The flag belongs to the plan-mode turn that just ended, however it
    // ended; left set by a failure, the next ordinary turn offered to approve
    // a plan. Only a turn that finished produced something to approve --
    // a stopped or guardian-blocked one did not.
    planPending.value = false;
    if (PLAN_TURN_FINISHED.includes(status) && !planReady.value) showPlanApproval();
  }
}
