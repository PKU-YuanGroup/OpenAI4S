/**
 * The stopped-turn marker, live and on reopen.
 *
 * A cancelled turn ends with one durable assistant row whose metadata carries
 * `cancelled: {request_id, execution_id, reason}`. The server streams the same
 * row as a text chunk carrying that object and repeats it on the terminal
 * `frame_update`, and `GET /frames/{fid}/messages` projects it. Rendering all
 * three as this element -- instead of as prose -- keeps live and reopen
 * identical and in the UI's language.
 *
 * Without it a turn stopped mid-Cell kept its "Running analysis" card and
 * reopened with only the in-progress narration, i.e. a claim that the stopped
 * cell was still running.
 */

import { LANG } from "../../i18n/runtime";
import { markCardStopped } from "./cardState";
import { el } from "./dom";
import type { LiveStream } from "./stream";

/** The `stop` glyph from sessions/icon, inlined to keep this lane's import graph. */
const STOP_ICON =
  '<svg class="ic-svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><rect width="6" height="6" x="9" y="9" rx="1"/></svg>';

/** Feature-local copy: en.ts / zh.ts are generated extracts. */
const COPY: Record<"en" | "zh", Record<string, string>> = {
  en: {
    "turn.stopped.user": "Stopped by user",
    "turn.stopped.autoBudget": "Stopped: the Auto Mode budget was reached",
  },
  zh: {
    "turn.stopped.user": "已由用户停止",
    "turn.stopped.autoBudget": "已停止：已达到自动模式预算",
  },
};

export function stoppedT(key: string): string {
  return (COPY[LANG] || COPY.en)[key] ?? COPY.en[key] ?? key;
}

export type StoppedIdentity = {
  reason: "user" | "auto_budget";
  request_id?: string;
  execution_id?: string;
};

/** Accept only the allowlisted shape; anything else is not a marker. */
export function cancelledIdentity(value: unknown): StoppedIdentity | null {
  if (!value || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  const reason = raw.reason;
  if (reason !== "user" && reason !== "auto_budget") return null;
  const out: StoppedIdentity = { reason };
  if (typeof raw.request_id === "string" && raw.request_id) {
    out.request_id = raw.request_id.slice(0, 96);
  }
  if (typeof raw.execution_id === "string" && raw.execution_id) {
    out.execution_id = raw.execution_id.slice(0, 96);
  }
  return out;
}

export function stoppedMarker(identity: StoppedIdentity): HTMLElement {
  const box = el("div", "msg-stopped");
  box.dataset.stopReason = identity.reason;
  if (identity.request_id) box.dataset.requestId = identity.request_id;
  if (identity.execution_id) box.dataset.executionId = identity.execution_id;
  const ic = el("span", "ic");
  ic.innerHTML = STOP_ICON;
  box.appendChild(ic);
  box.appendChild(
    el(
      "span",
      "lbl",
      stoppedT(identity.reason === "auto_budget" ? "turn.stopped.autoBudget" : "turn.stopped.user"),
    ),
  );
  return box;
}

type StoppableStream = LiveStream & { stopped?: boolean };

/**
 * Close a live stream as stopped: the activity card that was still the last
 * thing on screen is marked stopped, then the marker is appended. A card with
 * narration after it finished before the Stop (the outcome narration is only
 * published for a cell that was not cancelled), so it keeps its state, and so
 * does a card whose own outcome already arrived (cardState.ts).
 */
export function appendLiveStoppedMarker(st: LiveStream, identity: StoppedIdentity): void {
  const live = st as StoppableStream;
  if (live.stopped) return;
  live.stopped = true;
  if (st.toolCard && !String(st.text || "").trim()) markCardStopped(st.toolCard);
  st.wrap.classList.add("turn-stopped");
  st.wrap.dataset.turnStatus = "cancelled";
  st.wrap.appendChild(stoppedMarker(identity));
}

/**
 * Terminal fallback: a `cancelled` terminal whose marker chunk this client
 * never saw (an older daemon, a socket that reconnected past it) still ends
 * the live block with a marker rather than with a "running" card.
 */
export function finishStoppedStream(st: LiveStream | null, detail: unknown): void {
  if (!st) return;
  const rec = detail && typeof detail === "object" ? (detail as Record<string, unknown>) : null;
  appendLiveStoppedMarker(st, cancelledIdentity(rec && rec.cancelled) || { reason: "user" });
}
