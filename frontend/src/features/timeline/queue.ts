/**
 * Queued follow-up strip above the composer (`#queue-strip`). Port of
 * app.js:2947-2997, split out of island.ts, which repaints it whenever the
 * execution queue is remembered.
 */

import { t } from "../../i18n/runtime";
import { api, apiErrorText, hint } from "./api";
import { $, el, iconEl } from "./dom";
import { S } from "./s";

function queueRowLabel(item: any): string {
  const meta = item.metadata || {};
  const bits: string[] = [];
  if (meta.model_profile_id)
    bits.push(
      t(
        "queue.underProfile",
        meta.model_profile_id,
        meta.model_profile_revision == null ? "?" : meta.model_profile_revision,
      ),
    );
  if (item.branch_id) bits.push(t("queue.onBranch", item.branch_id));
  bits.push(item.execution_id);
  return bits.join(" · ");
}

/**
 * Queue ✕ requests in flight or already accepted, by execution id. A second
 * click (or the same row re-rendered by a queue update) must not POST again:
 * the first answer said "cancelled" and the duplicate then said "failed".
 * An id is forgotten once the queue stops listing it, or when its request fails.
 */
const queueCancelPending = new Set<string>();

export function renderQueueStrip(): void {
  const box = $("#queue-strip");
  if (!box) return;
  // The queue belongs to the session it was read for; a ✕ pressed on a strip
  // that outlived a session switch must still name that session.
  const frameId = S.currentId;
  const queue = ((S.executionQueue || {}).queue || []).filter(
    (item: any) => (item.owner || {}).kind === "agent",
  );
  queueCancelPending.forEach((id) => {
    if (!queue.some((item: any) => item.execution_id === id)) queueCancelPending.delete(id);
  });
  box.innerHTML = "";
  box.classList.toggle("hidden", !queue.length);
  if (!queue.length) return;
  box.appendChild(el("div", "queue-head", t("queue.waiting", queue.length)));
  queue.forEach((item: any) => {
    const row = el("div", "queue-row");
    row.appendChild(
      el(
        "span",
        "queue-pos",
        "#" + (item.queue_position == null ? "?" : item.queue_position),
      ),
    );
    row.appendChild(
      el("span", "queue-preview", item.metadata.preview || t("queue.noPreview")),
    );
    const meta = el("span", "queue-meta", queueRowLabel(item));
    meta.title = queueRowLabel(item);
    row.appendChild(meta);
    const drop = el("button", "icon-ghost queue-cancel") as HTMLButtonElement;
    drop.title = t("queue.cancelOne");
    drop.appendChild(iconEl("x", 13) as Node);
    drop.disabled = queueCancelPending.has(item.execution_id);
    drop.onclick = () => {
      drop.disabled = true;
      void cancelQueuedExecution(item, frameId);
    };
    row.appendChild(drop);
    box.appendChild(row);
  });
}

async function cancelQueuedExecution(item: any, fid: string | null): Promise<void> {
  if (!fid || !item || !item.execution_id || !(item.owner || {}).id) return;
  const executionId = item.execution_id;
  if (queueCancelPending.has(executionId)) return;
  queueCancelPending.add(executionId);
  try {
    const r = (await api(`/frames/${fid}/cancel`, {
      method: "POST",
      body: JSON.stringify({
        execution_id: executionId,
        owner: { kind: item.owner.kind, id: item.owner.id },
        reason: "queued follow-up dropped by user",
      }),
    })) as { ok?: boolean; reason?: string };
    if (!r || r.ok !== true) {
      queueCancelPending.delete(executionId);
      hint(t("queue.cancelFailed", (r && r.reason) || ""), true);
      return;
    }
    const bubble = [...document.querySelectorAll(".msg.user")].find(
      (n) => (n as HTMLElement).dataset.executionId === executionId,
    );
    if (bubble) bubble.classList.add("cancelled");
    hint(t("queue.cancelled"));
  } catch (e) {
    queueCancelPending.delete(executionId);
    hint(t("queue.cancelFailed", apiErrorText(e)), true);
  } finally {
    renderQueueStrip();
  }
}
