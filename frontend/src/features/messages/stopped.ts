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

import { isReady } from "../../compat/stub";
import { LANG } from "../../i18n/runtime";
import { currentId } from "../../stores/session";
import { hint } from "../sessions/chrome";
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
    "turn.continue": "Continue",
    "turn.continue.title": "Ask the agent to carry on from where it stopped",
    "turn.continue.message": "Continue",
    "turn.continue.draft": "Send or clear the draft in the composer first: this button sends its own message.",
  },
  zh: {
    "turn.stopped.user": "已由用户停止",
    "turn.stopped.autoBudget": "已停止：已达到自动模式预算",
    "turn.continue": "继续",
    "turn.continue.title": "让智能体从停下的地方接着做",
    "turn.continue.message": "继续",
    "turn.continue.draft": "请先发送或清空输入框里的草稿：这个按钮会发送它自己的消息。",
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

/**
 * CHAT-8: after a Stop the user had to type "继续" to carry on. The marker
 * offers it: one click sends "Continue" in the UI language to this session
 * through the composer's own send, which queues it behind a running turn as
 * it would any message. Only the newest row may continue: once a newer
 * message follows the stopped turn the button is hidden (messages.css), and a
 * click on an older one retires it. A draft in the composer is the user's, so
 * the button refuses while there is one (as failure.ts's Continue does).
 */
function continueButton(box: HTMLElement): HTMLButtonElement {
  const frame = currentId.value;
  const button = el("button", "outline-btn small msg-continue", stoppedT("turn.continue")) as HTMLButtonElement;
  button.type = "button";
  button.title = stoppedT("turn.continue.title");
  button.onclick = async () => {
    if (button.disabled) return;
    const rows = document.getElementById("messages")?.querySelectorAll(".msg");
    const last = rows && rows.length ? rows[rows.length - 1] : null;
    const stale = !frame || currentId.value !== frame || !last || last.querySelector(".msg-stopped") !== box;
    if (stale) {
      button.remove();
      return;
    }
    const composer = document.getElementById("composer") as HTMLTextAreaElement | null;
    if (composer?.value.trim()) {
      composer.focus();
      hint(stoppedT("turn.continue.draft"), false);
      return;
    }
    const send = (globalThis as Record<string, unknown>).send;
    if (!isReady(send)) return;
    button.disabled = true;
    try {
      await (send as (text: string) => unknown)(stoppedT("turn.continue.message"));
    } finally {
      button.disabled = false;
    }
  };
  return button;
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
  box.appendChild(continueButton(box));
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
