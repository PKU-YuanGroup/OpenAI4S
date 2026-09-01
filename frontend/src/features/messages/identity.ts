/**
 * Candidate identity stamping used at the message-stream boundary.
 *
 * Port of app.js:5511-5605 (the pieces `feed` / `renderStored` / `text_chunk`
 * call). The rest of the candidate/review gate (markCandidateReady /
 * applyCandidateResolution) is F-11.
 */

import { t } from "../../i18n/runtime";
import { stream as liveStream } from "../../stores/stream";
import { $ } from "./dom";

export function candidateIdentityText(value: unknown): string {
  return value == null ? "" : String(value).trim().slice(0, 192);
}

export type CandidateIdentity = {
  messageId: string;
  turnId: string;
  executionId: string;
};

export function candidateIdentity(value: unknown): CandidateIdentity {
  const raw =
    value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  const review =
    raw.review_status && typeof raw.review_status === "object"
      ? (raw.review_status as Record<string, unknown>)
      : {};
  const meta =
    raw.metadata && typeof raw.metadata === "object"
      ? (raw.metadata as Record<string, unknown>)
      : {};
  return {
    messageId: candidateIdentityText(
      raw.message_id ||
        raw.candidate_message_id ||
        raw.replacement_message_id ||
        review.message_id ||
        meta.message_id,
    ),
    turnId: candidateIdentityText(
      raw.turn_id || raw.candidate_turn_id || review.turn_id || meta.turn_id,
    ),
    executionId: candidateIdentityText(
      raw.execution_id || review.execution_id || meta.execution_id,
    ),
  };
}

export function rememberCandidateIdentity(
  node: HTMLElement | null | undefined,
  value: unknown,
): CandidateIdentity {
  const identity = candidateIdentity(value);
  if (!node || !node.dataset) return identity;
  const bind = (key: string, next: string): void => {
    if (next && (!node.dataset[key] || node.dataset[key] === next)) {
      node.dataset[key] = next;
    }
  };
  bind("messageId", identity.messageId);
  bind("turnId", identity.turnId);
  bind("executionId", identity.executionId);
  return identity;
}

export function candidateNodeMatches(
  node: HTMLElement | null | undefined,
  identity: CandidateIdentity | null | undefined,
): boolean {
  if (!node || !node.dataset || !identity) return false;
  if (identity.messageId) return node.dataset.messageId === identity.messageId;
  if (identity.turnId) {
    return (
      node.dataset.turnId === identity.turnId &&
      (!identity.executionId ||
        !node.dataset.executionId ||
        node.dataset.executionId === identity.executionId)
    );
  }
  return !!identity.executionId && node.dataset.executionId === identity.executionId;
}

export function candidateMessageNode(value: unknown): HTMLElement | null {
  const identity = candidateIdentity(value);
  const host = $("#messages");
  const nodes = host
    ? Array.from(host.querySelectorAll(".msg.assistant"))
    : [];
  if (identity.messageId) {
    const hit = nodes.find(
      (node) =>
        node instanceof HTMLElement &&
        node.dataset &&
        node.dataset.messageId === identity.messageId,
    );
    return hit instanceof HTMLElement ? hit : null;
  }
  const hit = nodes.find(
    (node) => node instanceof HTMLElement && candidateNodeMatches(node, identity),
  );
  return hit instanceof HTMLElement ? hit : null;
}

export function reviewStatusFrom(value: unknown): string {
  const raw =
    value && typeof value === "object"
      ? (value as { review_status?: unknown }).review_status
      : undefined;
  return candidateIdentityText(
    raw && typeof raw === "object"
      ? (raw as { status?: unknown }).status
      : raw,
  );
}

export function setMessageReviewBadge(
  node: HTMLElement | null | undefined,
  status: string,
  userTruth?: unknown,
): boolean {
  if (!node || !status) return false;
  let badge = node.querySelector(":scope > .review-badge") as HTMLElement | null;
  if (!badge) {
    badge = document.createElement("div");
    badge.className = "review-badge";
    const actions = node.querySelector(":scope > .msg-actions");
    if (actions) node.insertBefore(badge, actions);
    else node.appendChild(badge);
  }
  badge.className =
    "review-badge review-badge-" + String(status).replace(/[^a-z_]/g, "");
  badge.textContent = candidateIdentityText(userTruth) || t("review.badge." + status);
  if (node.dataset) node.dataset.reviewStatus = status;
  return true;
}

export function setLiveReviewBadge(status: string, userTruth?: unknown): boolean {
  const st = liveStream.value as { wrap?: HTMLElement } | null;
  return !!(st && st.wrap && setMessageReviewBadge(st.wrap, status, userTruth));
}

function discardDuplicateLiveCandidate(
  stored: HTMLElement,
  value: unknown,
): void {
  const live = liveStream.value as { wrap?: HTMLElement } | null;
  const wrap = live && live.wrap;
  if (!stored || !wrap || wrap === stored) return;
  const identity = candidateIdentity(value);
  rememberCandidateIdentity(wrap, {
    turn_id: identity.turnId,
    execution_id: identity.executionId,
  });
  if (!candidateNodeMatches(wrap, { ...identity, messageId: "" })) return;
  wrap.remove();
  liveStream.value = null;
}

/**
 * app.js:5598. A persist-first gated turn already rendered the candidate
 * from REST; replaying the same provisional bytes would duplicate it.
 */
export function storedCandidateOwnsChunk(value: unknown): boolean {
  const raw =
    value && typeof value === "object" ? (value as Record<string, unknown>) : null;
  if (
    !raw ||
    (raw.block_type || "text") !== "text" ||
    !(raw.provisional || reviewStatusFrom(raw) === "candidate")
  ) {
    return false;
  }
  const target = candidateMessageNode(raw);
  const live = liveStream.value as { wrap?: HTMLElement } | null;
  if (
    !target ||
    (live && target === live.wrap) ||
    !target.dataset ||
    !target.dataset.messageId
  ) {
    return false;
  }
  discardDuplicateLiveCandidate(target, raw);
  if (!target.dataset.reviewStatus || target.dataset.reviewStatus === "candidate") {
    setMessageReviewBadge(target, "candidate", raw.user_truth);
  }
  return true;
}
