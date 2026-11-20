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
import { keepStripsLast } from "../messages/dom";
import { sendCopy } from "../send/copy";
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

/** Queued follow-ups of the open session: what a Stop now hands the session to (not the held ones). */
export function queuedFollowUpCount(): number {
  return queuedAgentItems().filter((item: any) => !item.held).length;
}

/** The user bubble this tab shows for a queued execution, if it shows one. */
function bubbleFor(executionId: string): HTMLElement | null {
  if (!executionId || typeof document === "undefined") return null;
  return (
    ([...document.querySelectorAll("#messages .msg.user")] as HTMLElement[]).find(
      (node) => node.dataset.executionId === executionId,
    ) || null
  );
}

/**
 * Executions this tab saw start (`island.ts` `rememberExecutionState`). An
 * item that leaves the queue because it started is not a dropped one, and
 * the queue event that removes it can come before its `user_message`.
 */
const startedExecutions = new Set<string>();

export function noteExecutionStarted(executionId: unknown): void {
  if (typeof executionId !== "string" || !executionId) return;
  startedExecutions.add(executionId);
  // Bounded: only the last few can still be on screen as queued.
  if (startedExecutions.size > 500) {
    const oldest = startedExecutions.values().next().value;
    if (oldest) startedExecutions.delete(oldest);
  }
}

/** The bubble this tab drew for a message it sent, before the 202 named its execution. */
function ownBubble(requestId: string): HTMLElement | null {
  if (!requestId || typeof document === "undefined") return null;
  return (
    ([...document.querySelectorAll("#messages .msg.user")] as HTMLElement[]).find(
      (node) => node.dataset.requestId === requestId && !node.dataset.executionId,
    ) || null
  );
}

/** A queued message this tab did not send, as the queue describes it (its first 160 characters). */
function previewBubble(item: any): HTMLElement {
  const meta = item.metadata || {};
  const row = el("div", "msg user queued");
  row.dataset.executionId = String(item.execution_id || "");
  if (meta.request_id) row.dataset.requestId = String(meta.request_id);
  // Replaced by the whole message when its turn starts (messages/list.ts).
  row.dataset.queuePreview = "1";
  row.appendChild(el("div", "bubble", meta.preview || t("queue.noPreview")));
  row.appendChild(el("span", "msg-queued", item.held ? queueT("queueRow.heldBadge") : sendCopy("queuedBadge")));
  return row;
}

function hintText(): string {
  return typeof document === "undefined" ? "" : document.getElementById("composer-hint")?.textContent || "";
}

/**
 * The conversation's queued bubbles, in step with the queue every tab shares.
 *
 * A queued message was a bubble only in the tab that typed it (another tab,
 * or the same tab after a reload, had only the strip: M-21), and a message
 * dropped in another tab still looked queued there (SYNC-1). Every queued
 * item now has a bubble in every tab (the sender's own, else one drawn from
 * the queue's preview), and a bubble whose execution left the queue without
 * starting is marked dropped, wherever the ✕ was pressed.
 */
export function syncQueuedBubbles(): void {
  const host = typeof document === "undefined" ? null : ($("#messages") as HTMLElement | null);
  if (!host) return;
  const items = queuedAgentItems();
  const queued = new Set(items.map((item: any) => String(item.execution_id || "")));
  const owner = String(((S.executionQueue || {}).owner || {}).execution_id || "");
  let dropped = false;
  const rows = Array.from(host.children || []) as HTMLElement[];
  rows.forEach((node) => {
    const id = (node.classList.contains("user") && node.classList.contains("queued") && node.dataset.executionId) || "";
    if (!id || node.classList.contains("cancelled")) return;
    if (queued.has(id) || id === owner || startedExecutions.has(id)) return;
    node.classList.add("cancelled");
    dropped = true;
  });
  let drew = false;
  items.forEach((item: any) => {
    const id = String(item.execution_id || "");
    // On hold after a failed turn, or waiting its turn: the badge says which.
    const badge = bubbleFor(id)?.querySelector(".msg-queued");
    if (badge) badge.textContent = item.held ? queueT("queueRow.heldBadge") : sendCopy("queuedBadge");
    // Taken back by this tab (✕ or Edit): its bubble is already handled.
    if (!id || bubbleFor(id) || queueCancelPending.has(id)) return;
    const own = ownBubble(String((item.metadata || {}).request_id || ""));
    if (own) {
      own.dataset.executionId = id;
      return;
    }
    rows.find((node) => node.classList.contains("empty-session"))?.remove();
    host.appendChild(previewBubble(item));
    drew = true;
  });
  if (drew) keepStripsLast(host);
  // "Queued — it will run after the current turn" is no longer true of a message dropped elsewhere.
  if (dropped && hintText() === t("queue.accepted")) hint(t("queue.cancelled"));
}

/**
 * The text a queued message can be edited from. The sending tab's bubble has
 * all of it; the broadcast preview is one collapsed line of at most 160
 * characters, complete only when it was not cut.
 */
function queuedText(item: any): { text: string; complete: boolean } {
  const bubble = bubbleFor(String(item.execution_id || ""));
  // A bubble drawn from the preview holds no more than the preview does.
  const own = bubble && !bubble.dataset.queuePreview ? bubble : null;
  const full = own ? String(own.querySelector(".bubble")?.textContent || "") : "";
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
  syncQueuedBubbles();
  if (!queue.length) return;
  const head = el("div", "queue-head");
  const held = queue.filter((item: any) => item.held).length;
  if (held) {
    // M-31: the turn they were typed behind failed, and they usually build
    // on its result, so they wait for the user instead of starting.
    head.classList.add("queue-held");
    head.appendChild(el("span", "queue-head-t", queueT("queueRow.held", held)));
    const run = el(
      "button",
      "outline-btn small queue-resume",
      queueT(held === 1 ? "queueRow.resumeOne" : "queueRow.resume"),
    ) as HTMLButtonElement;
    run.type = "button";
    run.title = queueT("queueRow.resumeTitle");
    run.onclick = () => {
      if (run.disabled) return;
      run.disabled = true;
      void resumeHeldQueue(frameId, run);
    };
    head.appendChild(run);
  } else {
    // Says what Stop does to these before anyone presses it: Stop ends only the
    // running turn, and the first queued message starts right after.
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
  }
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

/** "Run them": the messages held after a failed turn run, in their order (M-31). */
async function resumeHeldQueue(fid: string | null, button: HTMLButtonElement): Promise<void> {
  if (!fid) return;
  try {
    const r = (await api(`/frames/${fid}/execution-queue/resume`, { method: "POST", body: "{}" })) as {
      ok?: boolean;
    };
    // The queue event that follows repaints the strip; a refusal leaves it as it was.
    if (!r || r.ok !== true) button.disabled = false;
  } catch (e) {
    button.disabled = false;
    hint(queueT("queueRow.resumeFailed", apiErrorText(e)), true);
  }
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
