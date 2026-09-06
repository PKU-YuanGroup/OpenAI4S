/**
 * Turn teardown. Port of app.js:5799-5874.
 *
 * F-14 owns `_kc` invalidation: `notebookOnTurnDone()` is the hook it
 * published for this function. Do not register a second `frame_update`.
 */

import { t } from "../../i18n/runtime";
import { _liveCell, liveCells } from "../../stores/notebook";
import { currentId } from "../../stores/session";
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
import { $, el } from "../messages/dom";
import { flushRender, type LiveStream } from "../messages/stream";
import { notebookOnTurnDone } from "../notebook/kernel";
import { hint } from "../sessions/chrome";
import { enableComposer } from "../sessions/dom";
import { addMsgActions } from "../sessions/transcript";
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

export function failureCodeHint(code: unknown): string {
  const key = (
    {
      llm_request_burst: "turn.failure.llmRequestBurst",
      llm_rate_limited: "turn.failure.llmRateLimited",
      llm_upstream_overloaded: "turn.failure.llmUpstreamOverloaded",
    } as Record<string, string>
  )[String(code || "")];
  return key ? t(key) : "";
}

export function failureMeta(failure: {
  code?: unknown;
  output_committed?: unknown;
  request_id?: unknown;
}): HTMLElement {
  const box = el("div", "msg-failure-meta");
  const bits: string[] = [];
  const cause = failureCodeHint(failure.code);
  if (cause) bits.push(cause);
  if (failure.output_committed) bits.push(t("turn.failedCommitted"));
  if (failure.request_id) bits.push(t("turn.supportId", String(failure.request_id).slice(0, 96)));
  box.textContent = bits.join(" ");
  box.dataset.requestId = failure.request_id ? String(failure.request_id).slice(0, 96) : "";
  box.dataset.failureCode = failure.code ? String(failure.code).slice(0, 64) : "";
  if (failure.output_committed) box.dataset.committed = "1";
  return box;
}

export function failureHint(detail: unknown): string {
  const rec = detail && typeof detail === "object" ? (detail as Record<string, unknown>) : null;
  const committed = !!(rec && rec.output_committed);
  const cause = failureCodeHint(rec && rec.code);
  const base = committed
    ? [t("turn.failedCommitted"), cause].filter(Boolean).join(" ")
    : cause || t("turn.failed");
  const raw = (rec && rec.request_id) || pendingRequestId.value || "";
  const id = raw ? String(raw).slice(0, 96) : "";
  return id ? base + " " + t("turn.supportId", id) : base;
}

export function turnDone(status: string, detail?: unknown): void {
  running.value = false;
  enableComposer(true);
  setCancelHidden(true);
  clearTimeout(_resumeTimer.value as ReturnType<typeof setTimeout>);
  _resumeTok.value = (_resumeTok.value || 0) + 1;
  const st = liveStream.value as LiveStream | null;
  if (st) {
    flushRender(st, true);
    st.md.classList.remove("cursor");
    addMsgActions(st.wrap, st.full || st.text);
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
  liveCells.value = [];
  _liveCell.value = null;
  if (planReady.value && planStatus.value === "executing") {
    renderPlanCard(
      planReady.value,
      ["failed", "blocked_by_guardian"].includes(status) ? "failed" : "completed",
    );
  }
  if (planPending.value && status !== "failed") {
    planPending.value = false;
    if (!planReady.value) showPlanApproval();
  }
}
