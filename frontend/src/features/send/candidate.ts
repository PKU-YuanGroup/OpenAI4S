/**
 * Candidate / review gate. Port of app.js:5511-5666 (the three-state
 * timing: markCandidateReady → applyCandidateResolution → applyFinalReviewStatus).
 *
 * Identity helpers at the stream boundary live in F-10 `messages/identity.ts`.
 * This module owns the live gate, including `S.reviewGate`.
 */

import { reviewGate, stream as liveStream } from "../../stores/stream";
import { renderMd } from "../md/render";
import {
  candidateIdentity,
  candidateIdentityText,
  candidateMessageNode,
  candidateNodeMatches,
  rememberCandidateIdentity,
  reviewStatusFrom,
  setMessageReviewBadge,
} from "../messages/identity";
import { el } from "../messages/dom";
import { addMsgActions } from "../sessions/transcript";
import { scheduleConversationResync } from "../timeline/island";

export { reviewStatusFrom };

type LiveWrap = {
  wrap?: HTMLElement;
  md?: HTMLElement;
  text?: string;
  full?: string;
  _stableAt?: number;
  _stableHtml?: string;
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

export function reviewTruthFrom(value: unknown): string {
  const raw = asRecord(value);
  const review = raw.review_status;
  return candidateIdentityText(
    raw.user_truth ||
      (review && typeof review === "object"
        ? (review as { user_truth?: unknown }).user_truth
        : ""),
  );
}

/**
 * F-10's badge painter plus `S.reviewGate`. The live counterpart of the
 * badge `renderStored` puts on a reopened message. One node, replaced in
 * place, so candidate -> verified never stacks two.
 */
export function setReviewBadge(
  node: HTMLElement | null | undefined,
  status: string,
  userTruth?: unknown,
): boolean {
  const ok = setMessageReviewBadge(node, status, userTruth);
  if (!ok || !node) return false;
  const badge = node.querySelector(":scope > .review-badge");
  reviewGate.value = {
    status,
    user_truth: badge ? badge.textContent : "",
  };
  return true;
}

function setLiveReviewBadge(status: string, userTruth?: unknown): boolean {
  const st = liveStream.value as LiveWrap | null;
  return !!(st && st.wrap && setReviewBadge(st.wrap, status, userTruth));
}

export function markCandidateReady(value: unknown): boolean {
  const rec = asRecord(value);
  const target = candidateMessageNode(value);
  if (target) {
    rememberCandidateIdentity(target, value);
    // A stale replay must never demote a row REST already projected as final.
    if (!target.dataset.reviewStatus || target.dataset.reviewStatus === "candidate") {
      setReviewBadge(target, "candidate", rec.user_truth);
    }
    return true;
  }
  const st = liveStream.value as LiveWrap | null;
  if (st && st.wrap) rememberCandidateIdentity(st.wrap, value);
  return setLiveReviewBadge("candidate", rec.user_truth);
}

function discardDuplicateLiveCandidate(stored: HTMLElement, value: unknown): void {
  const live = liveStream.value as LiveWrap | null;
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

export function candidateReplacementText(value: unknown): string {
  const rec = asRecord(value);
  if (typeof rec.text === "string") return rec.text;
  return typeof rec.final_answer === "string" ? rec.final_answer : "";
}

export function candidateReplacementCommitted(value: unknown): boolean {
  const rec = asRecord(value);
  const identity = candidateIdentity(value);
  const text = candidateReplacementText(value);
  return !!(
    rec.replaced === true &&
    rec.delivered === true &&
    rec.durable === true &&
    identity.messageId &&
    text &&
    (rec.persisted == null || rec.persisted === true) &&
    (rec.promotion_committed == null || rec.promotion_committed === true)
  );
}

/**
 * Replace one identified answer, whether it is the current live wrapper or a
 * canonical assistant row already rendered from REST. Message actions capture
 * their text in closures, so refresh them as part of the replacement too.
 */
export function replaceMessageAnswer(node: HTMLElement | null | undefined, text: string): boolean {
  if (
    !node ||
    !node.classList ||
    !node.classList.contains("assistant") ||
    !String(text || "").trim()
  ) {
    return false;
  }
  const hadActions = !!node.querySelector(":scope > .msg-actions");
  node.querySelectorAll(":scope > .md").forEach((item) => item.remove());
  const md = el("div", "md");
  md.innerHTML = renderMd(text);
  const st = liveStream.value as LiveWrap | null;
  if (st && st.wrap === node) {
    const badge = node.querySelector(":scope > .review-badge");
    if (badge) node.insertBefore(md, badge);
    else node.appendChild(md);
    st.md = md;
    st.text = text;
    st.full = text;
    st._stableAt = 0;
    st._stableHtml = "";
  } else if (node.firstChild) node.insertBefore(md, node.firstChild);
  else node.appendChild(md);
  (node as HTMLElement & { _messageText?: string })._messageText = text;
  if (hadActions) {
    const actions = node.querySelector(":scope > .msg-actions");
    if (actions) actions.remove();
    addMsgActions(node, text);
  }
  return true;
}

function resyncFid(fid: unknown): void {
  const id = typeof fid === "string" ? fid : fid != null ? String(fid) : "";
  if (id) scheduleConversationResync(id);
}

export type CandidateResolution = {
  targetFound: boolean;
  replacementApplied: boolean;
  badgeApplied: boolean;
};

export function applyCandidateResolution(value: unknown, fid?: unknown): CandidateResolution {
  const rec = asRecord(value);
  const target = candidateMessageNode(value);
  const status = reviewStatusFrom(value);
  const truth = reviewTruthFrom(value);
  const replacementWanted = rec.replaced === true;
  let replacementApplied = false;
  if (target) {
    discardDuplicateLiveCandidate(target, value);
    rememberCandidateIdentity(target, value);
  }
  if (replacementWanted && candidateReplacementCommitted(value) && target) {
    replacementApplied = replaceMessageAnswer(target, candidateReplacementText(value));
    if (replacementApplied && target.dataset) target.dataset.candidateResolved = "true";
  }
  if (replacementWanted && !replacementApplied) resyncFid(fid);
  // Never put Verified on bytes that an advertised replacement failed to reach,
  // or on any answer the receipt itself says was not delivered.
  const mayApplyBadge = !!(
    status &&
    (status !== "verified" ||
      (rec.delivered === true &&
        rec.durable === true &&
        (!replacementWanted || replacementApplied)))
  );
  if (mayApplyBadge && target) {
    setReviewBadge(target, status, truth);
    if (rec.delivered === true && rec.durable === true && target.dataset) {
      target.dataset.candidateResolved = "true";
    }
  } else if (status && !target) resyncFid(fid);
  else if (status === "verified" && !mayApplyBadge) resyncFid(fid);
  return {
    targetFound: !!target,
    replacementApplied,
    badgeApplied: !!(mayApplyBadge && target),
  };
}

export function applyFinalReviewStatus(value: unknown, fid?: unknown): boolean {
  const rec = asRecord(value);
  const status = reviewStatusFrom(value);
  if (!status) return false;
  if (rec.replaced === true) return applyCandidateResolution(value, fid).badgeApplied;
  const target = candidateMessageNode(value);
  // A previously applied candidate_resolved receipt is sufficient. Otherwise a
  // Verified terminal must itself say the durable answer was delivered.
  const mayVerify =
    status !== "verified" ||
    (rec.delivered === true && rec.durable === true) ||
    !!(target && target.dataset && target.dataset.candidateResolved === "true");
  if (target && mayVerify) {
    return setReviewBadge(target, status, reviewTruthFrom(value));
  }
  resyncFid(fid);
  return false;
}
