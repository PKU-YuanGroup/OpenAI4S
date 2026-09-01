/**
 * Stored-message rendering, time-order insert, and framed initial paint.
 *
 * Port of app.js `renderStored` (7234-7260), `insertMessageByTime` (7263-7274),
 * `renderEmptySession` (7226-7232), and the openConversation 300-item sync
 * loop (7177-7181) rewritten as 40 items per rAF + one fragment insert.
 */

import { isReady } from "../../compat/stub";
import { t } from "../../i18n/runtime";
import { renderMd } from "../md/render";
import { el, messagesHost } from "./dom";
import { rememberCandidateIdentity, setMessageReviewBadge } from "./identity";
import { cancelFrame, scheduleFrame } from "./raf";

export const INITIAL_RENDER_BATCH = 40;

export type HistoryItem = {
  t: number;
  seq: number;
  kind: "msg" | "step";
  v: unknown;
};

export type StoredMessage = {
  role?: string;
  content?: unknown;
  created_at?: unknown;
  artifact_refs?: unknown;
  failure?: { request_id?: unknown; code?: unknown; output_committed?: unknown };
  review_status?: unknown;
  metadata?: { review_status?: unknown };
  [key: string]: unknown;
};

type StepRenderer = (step: unknown, target?: ParentNode | null) => Node | null | void;

let renderStoredStepImpl: StepRenderer | null = null;
let framedRaf = 0;
let framedOnCancel: (() => void) | null = null;

/** F-11 assigns `renderStoredStep`. Until then steps in the interleaved list are skipped. */
export function setRenderStoredStepImpl(fn: StepRenderer | null): void {
  renderStoredStepImpl = fn;
}

export function nextBatchEnd(
  start: number,
  total: number,
  batch = INITIAL_RENDER_BATCH,
): number {
  return Math.min(start + batch, total);
}

function callWindow(name: string, ...args: unknown[]): void {
  const fn = (globalThis as Record<string, unknown>)[name];
  if (!isReady(fn)) return;
  (fn as (...a: unknown[]) => unknown)(...args);
}

function failureMeta(failure: NonNullable<StoredMessage["failure"]>): HTMLElement {
  const box = el("div", "msg-failure-meta");
  const bits: string[] = [];
  const code = String(failure.code || "");
  const causeKey =
    code === "llm_request_burst"
      ? "turn.failure.llmRequestBurst"
      : code === "llm_rate_limited"
        ? "turn.failure.llmRateLimited"
        : code === "llm_upstream_overloaded"
          ? "turn.failure.llmUpstreamOverloaded"
          : "";
  if (causeKey) bits.push(t(causeKey));
  if (failure.output_committed) bits.push(t("turn.failedCommitted"));
  if (failure.request_id) {
    bits.push(t("turn.supportId", String(failure.request_id).slice(0, 96)));
  }
  box.textContent = bits.join(" ");
  box.dataset.requestId = failure.request_id
    ? String(failure.request_id).slice(0, 96)
    : "";
  box.dataset.failureCode = failure.code ? String(failure.code).slice(0, 64) : "";
  if (failure.output_committed) box.dataset.committed = "1";
  return box;
}

function addMsgActions(wrap: HTMLElement, text: string): void {
  if (!wrap || wrap.querySelector(".msg-actions")) return;
  const row = el("div", "msg-actions");
  const copy = el("button");
  copy.title = t("msgAction.copy");
  copy.setAttribute("data-icon", "copy");
  copy.onclick = () => {
    try {
      if (navigator.clipboard) void navigator.clipboard.writeText(text || "");
    } catch {
      /* clipboard blocked */
    }
  };
  const tup = el("button");
  tup.title = t("msgAction.thumbsUp");
  tup.setAttribute("data-icon", "thumbs-up");
  const tdn = el("button");
  tdn.title = t("msgAction.thumbsDown");
  tdn.setAttribute("data-icon", "thumbs-down");
  const edit = el("button");
  edit.title = t("common.edit");
  edit.setAttribute("data-icon", "pencil");
  edit.onclick = () => {
    const c = document.getElementById("composer") as HTMLTextAreaElement | null;
    if (!c) return;
    c.value = text || "";
    c.focus();
    callWindow("grow");
  };
  row.appendChild(copy);
  row.appendChild(tup);
  row.appendChild(tdn);
  row.appendChild(edit);
  wrap.appendChild(row);
}

function messageText(m: StoredMessage): string {
  if (Array.isArray(m.content)) {
    return m.content
      .map((b) => {
        if (b && typeof b === "object" && "text" in b) {
          return String((b as { text?: unknown }).text || "");
        }
        return "";
      })
      .join("");
  }
  return String(m.content || "");
}

function reviewStatusOf(m: StoredMessage): unknown {
  const review = m.review_status || (m.metadata && m.metadata.review_status);
  if (review && typeof review === "object" && review !== null && "status" in review) {
    return (review as { status?: unknown }).status || review;
  }
  return review;
}

/** app.js:7234-7260. `target` is a fragment during framed paint. */
export function renderStored(
  m: StoredMessage,
  target?: ParentNode | null,
): HTMLElement | null {
  const text = messageText(m);
  if (!text.trim()) return null;
  const w = el("div", "msg " + (m.role === "user" ? "user" : "assistant"));
  rememberCandidateIdentity(w, m);
  (w as HTMLElement & { _messageText?: string })._messageText = text;
  if (m.role === "user") {
    const b = el("div", "bubble");
    b.textContent = text;
    w.appendChild(b);
    callWindow("renderMessageRefChips", w, m.artifact_refs);
  } else {
    const md = el("div", "md");
    md.innerHTML = renderMd(text);
    w.appendChild(md);
    if (m.failure && m.failure.request_id) w.appendChild(failureMeta(m.failure));
    const review = m.review_status || (m.metadata && m.metadata.review_status);
    const reviewStatus = reviewStatusOf(m);
    if (reviewStatus) {
      const truth =
        review && typeof review === "object" && review !== null && "user_truth" in review
          ? (review as { user_truth?: unknown }).user_truth
          : undefined;
      setMessageReviewBadge(w, String(reviewStatus), truth);
      if (String(reviewStatus) !== "candidate" && w.dataset) {
        w.dataset.candidateResolved = "true";
      }
    }
  }
  w.dataset.ts = String(new Date(String(m.created_at || "")).getTime() || 0);
  (target || messagesHost())?.appendChild(w);
  if (m.role !== "user") addMsgActions(w, text);
  return w;
}

/** app.js:7263-7274. The earlier-control stays pinned to the top. */
export function insertMessageByTime(
  node: HTMLElement,
  host: ParentNode | null = messagesHost(),
): void {
  if (!host || !node) return;
  const ts = Number(node.dataset.ts || 0);
  const kids = (host as ParentNode & { children: HTMLCollection }).children;
  if (!kids) {
    host.appendChild(node);
    return;
  }
  for (let i = 0; i < kids.length; i++) {
    const kid = kids[i] as HTMLElement;
    if (kid.id === "msgs-earlier") continue;
    const kidTs = Number(kid.dataset && kid.dataset.ts);
    if (Number.isFinite(kidTs) && kidTs > ts) {
      host.insertBefore(node, kid);
      return;
    }
  }
  host.appendChild(node);
}

function appendBatch(host: ParentNode, nodes: Node[]): void {
  if (typeof document !== "undefined" && document.createDocumentFragment) {
    const frag = document.createDocumentFragment();
    for (const node of nodes) frag.appendChild(node);
    host.appendChild(frag);
    return;
  }
  for (const node of nodes) host.appendChild(node);
}

export function renderHistoryItem(
  item: HistoryItem,
  target: ParentNode,
): Node | null {
  if (item.kind === "msg") {
    return renderStored(item.v as StoredMessage, target);
  }
  if (renderStoredStepImpl) {
    const node = renderStoredStepImpl(item.v, target);
    return node instanceof Node ? node : null;
  }
  callWindow("renderStoredStep", item.v, target);
  return null;
}

/**
 * Paint `items` in rAF batches of 30-50 (40). Each batch is one fragment
 * insert so the 300-row openConversation loop no longer blocks a frame.
 */
export function scheduleFramedRender(
  items: HistoryItem[],
  opts: {
    host?: ParentNode | null;
    stillCurrent?: () => boolean;
    onDone?: () => void;
    onCancel?: () => void;
    batch?: number;
    onBatch?: () => void;
  } = {},
): void {
  cancelFramedRender();
  framedOnCancel = opts.onCancel || null;
  const host = opts.host || messagesHost();
  if (!host) {
    framedOnCancel = null;
    if (opts.onDone) opts.onDone();
    return;
  }
  const batch = opts.batch ?? INITIAL_RENDER_BATCH;
  let i = 0;
  const tick = (): void => {
    framedRaf = 0;
    if (opts.stillCurrent && !opts.stillCurrent()) {
      const onCancel = framedOnCancel;
      framedOnCancel = null;
      if (onCancel) onCancel();
      return;
    }
    const nodes: Node[] = [];
    const end = nextBatchEnd(i, items.length, batch);
    const sink = {
      appendChild(node: Node): Node {
        nodes.push(node);
        return node;
      },
    } as unknown as ParentNode;
    for (; i < end; i++) {
      const item = items[i];
      if (!item) continue;
      renderHistoryItem(item, sink);
    }
    if (nodes.length) appendBatch(host, nodes);
    if (opts.onBatch) opts.onBatch();
    if (i < items.length) {
      framedRaf = scheduleFrame(tick);
    } else {
      framedOnCancel = null;
      if (opts.onDone) opts.onDone();
    }
  };
  framedRaf = scheduleFrame(tick);
}

export function cancelFramedRender(): void {
  cancelFrame(framedRaf);
  framedRaf = 0;
  const onCancel = framedOnCancel;
  framedOnCancel = null;
  if (onCancel) onCancel();
}

/** app.js:7226-7232. Starter chips use existing i18n keys (no new keys). */
export function renderEmptySession(host: ParentNode | null = messagesHost()): void {
  if (!host) return;
  const wrap = el("div", "empty-session");
  wrap.appendChild(el("div", "es-title", t("empty.title")));
  wrap.appendChild(el("div", "es-sub", t("empty.sub")));
  const chips = el("div", "es-chips");
  const starters = [
    { title: t("starter.litReview.title"), prompt: t("starter.litReview.prompt") },
    { title: t("starter.dataAnalysis.title"), prompt: t("starter.dataAnalysis.prompt") },
    { title: t("starter.proteinModel.title"), prompt: t("starter.proteinModel.prompt") },
    { title: t("starter.phylo.title"), prompt: t("starter.phylo.prompt") },
  ];
  for (const s of starters) {
    const chip = el("button", "es-chip");
    chip.appendChild(el("div", "es-chip-t", s.title));
    chip.appendChild(el("div", "es-chip-p", s.prompt));
    chip.onclick = () => {
      const c = document.getElementById("composer") as HTMLTextAreaElement | null;
      if (!c) return;
      c.value = s.prompt;
      c.focus();
      callWindow("grow");
    };
    chips.appendChild(chip);
  }
  wrap.appendChild(chips);
  host.appendChild(wrap);
}

export function interleaveHistory(
  msgs: StoredMessage[],
  steps: Array<{ created_at?: number; seq?: number; [key: string]: unknown }>,
): HistoryItem[] {
  const items: HistoryItem[] = [];
  for (const mm of msgs) {
    items.push({
      t: new Date(String(mm.created_at || "")).getTime() || 0,
      seq: 1e15,
      kind: "msg",
      v: mm,
    });
  }
  for (const s of steps) {
    items.push({
      t: Number(s.created_at || 0),
      seq: Number(s.seq || 0),
      kind: "step",
      v: s,
    });
  }
  items.sort((a, b) => a.t - b.t || a.seq - b.seq);
  return items;
}
