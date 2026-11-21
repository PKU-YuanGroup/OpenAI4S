/**
 * Queued follow-up strip above the composer (`#queue-strip`). Port of
 * app.js:2947-2997, split out of island.ts, which repaints it whenever the
 * execution queue is remembered.
 *
 * A row shows what the user wrote, never the ticket's internal identity: the
 * branch and execution ids it used to print ("分支 f-… · exec-…") mean
 * nothing to the person who typed the message. Each row can be taken back
 * into the composer to edit, or dropped.
 */

import { t } from "../../i18n/runtime";
import { api, apiErrorText, hint } from "./api";
import { queueT } from "./copy";
import { $, el, iconEl } from "./dom";
import { S } from "./s";

/** `gateway.QUEUE_PREVIEW_CHARS`: a preview this long was cut short. */
const PREVIEW_CHARS = 160;

/**
 * Queue ✕ requests in flight or already accepted, by execution id. A second
 * click (or the same row re-rendered by a queue update) must not POST again:
 * the first answer said "cancelled" and the duplicate then said "failed".
 * An id is forgotten once the queue stops listing it, or when its request fails.
 */
const queueCancelPending = new Set<string>();

function queuedAgentItems(): any[] {
  return ((S.executionQueue || {}).queue || []).filter(
    (item: any) => (item.owner || {}).kind === "agent",
  );
}

/** Queued follow-ups of the open session: what a Stop now hands the session to. */
export function queuedFollowUpCount(): number {
  return queuedAgentItems().length;
}

/** The user bubble this tab drew for a queued execution, if it drew one. */
function bubbleFor(executionId: string): HTMLElement | null {
  if (!executionId || typeof document === "undefined") return null;
  return (
    ([...document.querySelectorAll("#messages .msg.user")] as HTMLElement[]).find(
      (node) => node.dataset.executionId === executionId,
    ) || null
  );
}

/**
 * The text a queued message can be edited from. The sending tab's bubble has
 * all of it; the broadcast preview is one collapsed line of at most 160
 * characters, complete only when it was not cut.
 */
function queuedText(item: any): { text: string; complete: boolean } {
  const bubble = bubbleFor(String(item.execution_id || ""));
  const full = bubble ? String(bubble.querySelector(".bubble")?.textContent || "") : "";
  if (full.trim()) return { text: full, complete: true };
  const preview = String((item.metadata || {}).preview || "");
  const cut = preview.length >= PREVIEW_CHARS && preview.endsWith("…");
  return { text: preview, complete: !!preview && !cut };
}

export function renderQueueStrip(): void {
  const box = $("#queue-strip");
  if (!box) return;
  // The queue belongs to the session it was read for; a ✕ pressed on a strip
  // that outlived a session switch must still name that session.
  const frameId = S.currentId;
  const queue = queuedAgentItems();
  queueCancelPending.forEach((id) => {
    if (!queue.some((item: any) => item.execution_id === id)) queueCancelPending.delete(id);
  });
  box.innerHTML = "";
  box.classList.toggle("hidden", !queue.length);
  if (!queue.length) return;
  // Says what Stop does to these before anyone presses it: Stop ends only the
  // running turn, and the first queued message starts right after.
  const head = el("div", "queue-head");
  head.appendChild(
    el("span", "queue-head-t", t("queue.waiting", queue.length) + " · " + queueT("queueRow.headNote")),
  );
  // For a correction that cannot wait: the existing Stop, whose turn's end
  // starts the first queued message at once. Pressing the Stop button itself
  // keeps one stop path (and its hints). Stop is hidden while a stop is in
  // flight, and a second one would land on the message that just started.
  const now = el("button", "outline-btn small queue-send-now", queueT("queueRow.sendNow")) as HTMLButtonElement;
  now.type = "button";
  now.title = queueT("queueRow.sendNowTitle");
  now.onclick = () => {
    const stop = document.getElementById("cancel-btn") as HTMLButtonElement | null;
    if (now.disabled || !stop || stop.classList.contains("hidden")) return;
    now.disabled = true;
    stop.click();
  };
  head.appendChild(now);
  box.appendChild(head);
  queue.forEach((item: any) => {
    const row = el("div", "queue-row");
    row.dataset.executionId = String(item.execution_id || "");
    row.appendChild(
      el(
        "span",
        "queue-pos",
        "#" + (item.queue_position == null ? "?" : item.queue_position),
      ),
    );
    row.appendChild(
      el("span", "queue-preview", (item.metadata || {}).preview || t("queue.noPreview")),
    );
    const pending = queueCancelPending.has(item.execution_id);
    const source = queuedText(item);
    const edit = el("button", "outline-btn small queue-edit", queueT("queueRow.edit")) as HTMLButtonElement;
    edit.type = "button";
    edit.title = source.complete ? queueT("queueRow.editTitle") : queueT("queueRow.editUnavailable");
    edit.disabled = pending || !source.complete;
    edit.onclick = () => {
      edit.disabled = drop.disabled = true;
      void editQueuedExecution(item, frameId);
    };
    row.appendChild(edit);
    const drop = el("button", "icon-ghost queue-cancel") as HTMLButtonElement;
    drop.type = "button";
    drop.title = t("queue.cancelOne");
    drop.setAttribute("aria-label", t("queue.cancelOne"));
    drop.appendChild(iconEl("x", 13) as Node);
    drop.disabled = pending;
    drop.onclick = () => {
      edit.disabled = drop.disabled = true;
      void cancelQueuedExecution(item, frameId);
    };
    row.appendChild(drop);
    box.appendChild(row);
  });
}

/** POST the exact cancel for one queued execution. True when it was accepted. */
async function withdrawQueued(item: any, fid: string | null, reason: string): Promise<boolean> {
  if (!fid || !item || !item.execution_id || !(item.owner || {}).id) return false;
  const executionId = item.execution_id;
  if (queueCancelPending.has(executionId)) return false;
  queueCancelPending.add(executionId);
  try {
    const r = (await api(`/frames/${fid}/cancel`, {
      method: "POST",
      body: JSON.stringify({
        execution_id: executionId,
        owner: { kind: item.owner.kind, id: item.owner.id },
        reason,
      }),
    })) as { ok?: boolean; reason?: string };
    if (!r || r.ok !== true) {
      queueCancelPending.delete(executionId);
      hint(t("queue.cancelFailed", (r && r.reason) || ""), true);
      return false;
    }
    return true;
  } catch (e) {
    queueCancelPending.delete(executionId);
    hint(t("queue.cancelFailed", apiErrorText(e)), true);
    return false;
  }
}

async function cancelQueuedExecution(item: any, fid: string | null): Promise<void> {
  try {
    if (!(await withdrawQueued(item, fid, "queued follow-up dropped by user"))) return;
    const bubble = bubbleFor(item.execution_id);
    if (bubble) bubble.classList.add("cancelled");
    hint(t("queue.cancelled"));
  } finally {
    renderQueueStrip();
  }
}

/**
 * Edit = take the message out of the queue and put its text back in the
 * composer. The server stores a user message only when its turn starts, so a
 * withdrawn one leaves nothing behind: its bubble goes too, and sending the
 * edited text queues it again at the end.
 */
async function editQueuedExecution(item: any, fid: string | null): Promise<void> {
  try {
    const composer = $("#composer") as HTMLTextAreaElement | null;
    if (!composer) return;
    if (composer.value.trim()) {
      hint(queueT("queueRow.editBusy"), true);
      return;
    }
    const source = queuedText(item);
    if (!source.complete) {
      hint(queueT("queueRow.editUnavailable"), true);
      return;
    }
    if (!(await withdrawQueued(item, fid, "queued follow-up withdrawn for editing"))) return;
    bubbleFor(item.execution_id)?.remove();
    // Anything typed while the withdrawal was in flight is kept after it.
    const typed = composer.value.trim();
    composer.value = typed ? source.text + "\n\n" + composer.value : source.text;
    // sessions/dom.ts `grow`, inlined: timeline imports nothing from sessions,
    // and an "input" event would also open the @/slash autocomplete.
    composer.style.height = "auto";
    composer.style.height = Math.min(220, composer.scrollHeight) + "px";
    composer.focus();
    composer.setSelectionRange(composer.value.length, composer.value.length);
    hint(queueT("queueRow.editing"));
  } finally {
    renderQueueStrip();
  }
}

/**
 * A `processing` event for an execution this tab queued: say that it started.
 * A Stop with follow-ups waiting hands the session straight to the next one,
 * and with nothing on screen saying so the Stop looked like it had not worked.
 */
export function noteQueuedTurnStarted(executionId: unknown, prefix = ""): boolean {
  const bubble = bubbleFor(typeof executionId === "string" ? executionId : "");
  if (!bubble) return false;
  const line = String(bubble.querySelector(".bubble")?.textContent || "")
    .replace(/\s+/g, " ")
    .trim();
  const started = queueT("queueRow.started", line.length > 60 ? line.slice(0, 59) + "…" : line);
  // `prefix`: what the Stop that handed over to this message also did (the
  // background cells it stopped), which this line would otherwise replace.
  hint(prefix ? prefix + "；" + started : started, false, true);
  return true;
}
