/**
 * Stored-message rendering, time-order insert, and framed initial paint.
 *
 * Port of app.js `renderStored` (7234-7260), `insertMessageByTime` (7263-7274),
 * `renderEmptySession` (7226-7232), `addMsgActions` (7809-7830), the message
 * @-ref chips (7766-7787), and the openConversation 300-item sync loop
 * (7177-7181) rewritten as 40 items per rAF + one fragment insert.
 *
 * The only implementation of a stored row: `sessions/transcript.ts`
 * ("load earlier") re-exports these, and the live turn calls
 * `addMsgActions` here too.
 */

import { isReady } from "../../compat/stub";
import { t } from "../../i18n/runtime";
import { artifacts } from "../../stores/artifacts";
import { currentId, feedback as feedbackSignal } from "../../stores/session";
import { copyFailedText, copyText } from "../chrome/clipboard";
import { paintIcon } from "../icons/paths";
import { renderMd } from "../md/render";
import { publicText } from "../scrub/scrub";
import { api } from "../sessions/api";
import { hint } from "../sessions/chrome";
import { grow } from "../sessions/dom";
import { iconEl } from "../sessions/icon";
import { el, messagesHost } from "./dom";
import { failureMeta } from "./failure";
import { rememberCandidateIdentity, setMessageReviewBadge } from "./identity";
import { cancelFrame, scheduleFrame } from "./raf";
import { planModeRequestText, planSeed, planSeedMarker } from "./planPrompt";
import { cancelledIdentity, stoppedMarker } from "./stopped";

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
  failure?: { request_id?: unknown; code?: unknown; output_committed?: unknown } | null;
  cancelled?: { request_id?: unknown; execution_id?: unknown; reason?: unknown } | null;
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

function fbKey(text: string): string {
  let h = 0;
  const s = (text || "").slice(0, 400);
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return "m" + (h >>> 0).toString(36);
}

function feedbackBag(): Record<string, unknown> {
  const cur = feedbackSignal.value;
  if (cur && typeof cur === "object") return cur as Record<string, unknown>;
  const next = Object.create(null) as Record<string, unknown>;
  feedbackSignal.value = next;
  return next;
}

function sendFeedback(key: string, rating: string | null): void {
  if (!currentId.value) return;
  const bag = feedbackBag();
  if (rating) bag[key] = rating;
  else delete bag[key];
  api("/frames/" + currentId.value + "/feedback", {
    method: "POST",
    body: JSON.stringify({ key, rating }),
  }).catch(() => {});
  hint(
    rating === "up"
      ? t("toast.feedbackUp")
      : rating === "down"
        ? t("toast.feedbackDown")
        : t("toast.feedbackCancelled"),
  );
}

/**
 * app.js:7809-7830. The one action row for a finished answer: the first
 * page, "load earlier" and the live turn all call this. The first page had
 * its own copy whose 👍/👎 had no handler and never showed a saved rating.
 */
export function addMsgActions(wrap: HTMLElement, text: string): void {
  if (!wrap || wrap.querySelector(".msg-actions")) return;
  const row = el("div", "msg-actions");
  const copy = el("button");
  (copy as HTMLButtonElement).type = "button";
  copy.title = t("msgAction.copy");
  paintIcon(copy, "copy");
  copy.onclick = async () => {
    // The check is shown only for a confirmed write: a try/catch cannot see
    // the async rejection, so a blocked or absent clipboard used to tick too.
    if (!(await copyText(text || ""))) {
      hint(copyFailedText(), true);
      return;
    }
    paintIcon(copy, "check");
    setTimeout(() => paintIcon(copy, "copy"), 1200);
  };
  const key = fbKey(text);
  const cur = feedbackBag()[key] || null;
  const tup = el("button", cur === "up" ? "on" : null);
  (tup as HTMLButtonElement).type = "button";
  tup.title = t("msgAction.thumbsUp");
  paintIcon(tup, "thumbs-up");
  const tdn = el("button", cur === "down" ? "on" : null);
  (tdn as HTMLButtonElement).type = "button";
  tdn.title = t("msgAction.thumbsDown");
  paintIcon(tdn, "thumbs-down");
  tup.onclick = () => {
    const on = !tup.classList.contains("on");
    tup.classList.toggle("on", on);
    tdn.classList.remove("on");
    sendFeedback(key, on ? "up" : null);
  };
  tdn.onclick = () => {
    const on = !tdn.classList.contains("on");
    tdn.classList.toggle("on", on);
    tup.classList.remove("on");
    sendFeedback(key, on ? "down" : null);
  };
  const edit = el("button");
  (edit as HTMLButtonElement).type = "button";
  edit.title = t("common.edit");
  paintIcon(edit, "pencil");
  edit.onclick = () => {
    const c = document.getElementById("composer") as HTMLTextAreaElement | null;
    if (!c) return;
    c.value = text || "";
    grow();
    c.focus();
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
  const stopped = m.role !== "user" ? cancelledIdentity(m.cancelled) : null;
  if (stopped) {
    // The stopped marker row renders as the marker the live stream showed.
    const marker = el("div", "msg assistant turn-stopped");
    marker.dataset.turnStatus = "cancelled";
    marker.appendChild(stoppedMarker(stopped));
    marker.dataset.ts = String(new Date(String(m.created_at || "")).getTime() || 0);
    (target || messagesHost())?.appendChild(marker);
    return marker;
  }
  const seed = m.role === "user" ? planSeed(text) : null;
  if (seed) {
    // A plan's execution seed is the server's instruction, not the user's message.
    const marker = el("div", "msg plan-seed");
    marker.appendChild(planSeedMarker(seed));
    marker.dataset.ts = String(new Date(String(m.created_at || "")).getTime() || 0);
    (target || messagesHost())?.appendChild(marker);
    return marker;
  }
  const w = el("div", "msg " + (m.role === "user" ? "user" : "assistant"));
  rememberCandidateIdentity(w, m);
  (w as HTMLElement & { _messageText?: string })._messageText = text;
  if (m.role === "user") {
    const b = el("div", "bubble");
    b.textContent = planModeRequestText(text);
    w.appendChild(b);
    renderMessageRefChips(w, m.artifact_refs);
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

/** app.js:7766-7787. The @-refs a user message pinned, as chips under its bubble. */
export function renderMessageRefChips(host: HTMLElement, refs: unknown): void {
  if (!Array.isArray(refs) || !refs.length) return;
  const row = el("div", "msg-refs");
  refs.slice(0, 8).forEach((raw) => {
    const r = raw as Record<string, unknown>;
    const name = String((r && r.display_name) || "");
    if (!name) return;
    const chip = el("span", "msg-ref-chip");
    chip.appendChild(iconEl("file-text", 11));
    chip.appendChild(el("span", null, publicText(name, 60)));
    const parts = [String(r.version_id || "")];
    if (r.sha256) parts.push("sha256:" + String(r.sha256).slice(0, 12));
    if (r.materialized_target) parts.push("↗ " + String(r.source_session || "").slice(0, 12));
    chip.title = parts.filter(Boolean).join(" · ");
    const pool = (artifacts.value || []) as Array<Record<string, unknown>>;
    const full = pool.find((x) => (x.artifact_id || x.id) === r.artifact_id);
    if (full) {
      chip.classList.add("clickable");
      chip.onclick = () => {
        callWindow("openViewer", full);
      };
    }
    row.appendChild(chip);
  });
  if (row.children.length) host.appendChild(row);
}

/** app.js:7263-7274. The earlier-control stays pinned to the top. */
export function insertMessageByTime(
  node: HTMLElement | null,
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
    onError?: (error: unknown) => void;
    batch?: number;
    onBatch?: () => void;
    renderItem?: (item: HistoryItem, target: ParentNode) => void;
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
    try {
      for (; i < end; i++) {
        const item = items[i];
        if (!item) continue;
        (opts.renderItem || renderHistoryItem)(item, sink);
      }
    } catch (error) {
      framedOnCancel = null;
      if (opts.onError) opts.onError(error);
      else throw error;
      return;
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
    (chip as HTMLButtonElement).type = "button";
    chip.appendChild(el("div", "es-chip-t", s.title));
    chip.appendChild(el("div", "es-chip-p", s.prompt));
    chip.onclick = () => {
      const c = document.getElementById("composer") as HTMLTextAreaElement | null;
      if (!c) return;
      c.value = s.prompt;
      // Imported: no lane assigns a window `grow`, so the filled composer
      // never grew to fit the starter prompt.
      grow();
      c.focus();
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
