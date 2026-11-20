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

import { effect } from "@preact/signals";
import { isReady } from "../../compat/stub";
import { t } from "../../i18n/runtime";
import { artifacts } from "../../stores/artifacts";
import { currentId, feedback as feedbackSignal } from "../../stores/session";
import { permCards, running, stepEls, stream as liveStream } from "../../stores/stream";
import { branchState } from "../../stores/timeline";
import { stripRefVersions } from "../autocomplete/mentions";
import { copyFailedText, copyText } from "../chrome/clipboard";
import {
  forkFromMessage,
  messageForkPending,
  setMessageForkRefresh,
  type ForkResult,
} from "../execution/branch";
import { scheduleWorkbenchRefresh } from "../notebook/kernel";
import { paintIcon } from "../icons/paths";
import { renderMd } from "../md/render";
import { publicText } from "../scrub/scrub";
import { api } from "../sessions/api";
import { hint } from "../sessions/chrome";
import { grow } from "../sessions/dom";
import { iconEl } from "../sessions/icon";
import { historyT } from "./copy";
import { el, messagesHost } from "./dom";
import { continueMarker, failureMeta, isContinuePrompt } from "./failure";
import { embedArtifactFigures } from "./figures";
import { rememberCandidateIdentity, setMessageReviewBadge } from "./identity";
import { cancelFrame, scheduleFrame } from "./raf";
import { planModeRequestText, planSeed, planSeedMarker } from "./planPrompt";
import { cancelledIdentity, stoppedMarker } from "./stopped";
import { cellCardOnScreen, chatCells, renderStoredCell, type StoredCell } from "./cellCard";
import { dropEmptyProse, openTurnGroup, placeCardInTurn, turnTextKey, type TurnGroup } from "./turnGroup";

export const INITIAL_RENDER_BATCH = 40;

export type HistoryItem = {
  t: number;
  seq: number;
  kind: "msg" | "step" | "perm" | "cell";
  v: unknown;
};

export type StoredMessage = {
  role?: string;
  content?: unknown;
  created_at?: unknown;
  message_id?: string;
  fork_checkpoint_id?: string | null;
  artifact_refs?: unknown;
  /** The pins the message was sent with (SYNC-5). */
  annotations?: unknown;
  failure?: { request_id?: unknown; code?: unknown; output_committed?: unknown } | null;
  cancelled?: { request_id?: unknown; execution_id?: unknown; reason?: unknown } | null;
  review_status?: unknown;
  metadata?: { review_status?: unknown };
  [key: string]: unknown;
};

/** Exact cursor plus a message id. Capability is a separate, later signal. */
export function canForkFromMessage(m: {
  message_id?: unknown;
  fork_checkpoint_id?: unknown;
}): boolean {
  return (
    typeof m.message_id === "string" &&
    typeof m.fork_checkpoint_id === "string" &&
    m.fork_checkpoint_id !== ""
  );
}

/** `branchState.capabilities.fork_from_message` after sanitize. Strict true. */
export function forkFromMessageCapability(): boolean {
  const state = branchState.value as {
    capabilities?: { fork_from_message?: unknown };
  } | null;
  return !!state && !!state.capabilities && state.capabilities.fork_from_message === true;
}

type StepRenderer = (step: unknown, target?: ParentNode | null) => Node | null | void;

let renderStoredStepImpl: StepRenderer | null = null;
let renderStoredPermissionImpl: StepRenderer | null = null;
let framedRaf = 0;
let framedOnCancel: (() => void) | null = null;

/** F-11 assigns `renderStoredStep`. Until then steps in the interleaved list are skipped. */
export function setRenderStoredStepImpl(fn: StepRenderer | null): void {
  renderStoredStepImpl = fn;
}

/** F-11 assigns the approval-card renderer for stored decisions. */
export function setRenderStoredPermissionImpl(fn: StepRenderer | null): void {
  renderStoredPermissionImpl = fn;
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
  // Keyed on the turn's words, not on how they were chunked: the live stream
  // and the stored rows of one turn must name the same rating. A rating saved
  // under the older raw-text key is still shown.
  const key = fbKey(turnTextKey(text));
  const cur = feedbackBag()[key] || feedbackBag()[fbKey(text)] || null;
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

/**
 * End a turn (`turnGroup.ts`): drop the prose blocks nothing was written to
 * and give the turn its one action row, whose Copy and rating cover all of
 * the turn's prose. A turn that said nothing (only cards) gets no row: there
 * is nothing to copy, and every such turn would share one rating.
 */
export function finishTurnGroup(wrap: HTMLElement, text: string): void {
  dropEmptyProse(wrap);
  (wrap as HTMLElement & { _messageText?: string })._messageText = text;
  if (turnTextKey(text)) addMsgActions(wrap, text);
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

/**
 * One subscription for every stored row, including rows still inside the
 * off-document fragment `loadHistory` paints before `replaceChildren`.
 * A per-node scan misses that fragment, so the flag lives on the document
 * element and the stylesheet shows `.msg-fork` only while it is `"on"`.
 */
function syncForkMessageVisibility(show: boolean): void {
  const doc = globalThis.document as { documentElement?: HTMLElement } | undefined;
  const root = doc && doc.documentElement;
  if (!root || !root.dataset) return;
  root.dataset.forkFromMessage = show ? "on" : "off";
}

let forkMessageVisibilityInstalled = false;

function installForkMessageVisibility(): void {
  if (forkMessageVisibilityInstalled) return;
  forkMessageVisibilityInstalled = true;
  effect(() => {
    syncForkMessageVisibility(forkFromMessageCapability());
  });
}

/**
 * Which fork owns the busy sentence in `#composer-hint`. Every session shows
 * the same sentence, so the text alone cannot tell a late settlement from
 * another session apart from this session's own pending fork.
 */
let busyHintOwner = "";

function clearBusyHint(owner: string, busy: string): void {
  if (busyHintOwner !== owner) return;
  busyHintOwner = "";
  const doc = globalThis.document as { getElementById?: (id: string) => HTMLElement | null } | undefined;
  if (!doc || typeof doc.getElementById !== "function") return;
  const host = doc.getElementById("composer-hint");
  if (host && (host.textContent || "").includes(busy)) hint();
}

function settleForkButton(
  button: HTMLButtonElement,
  frameId: string,
  owner: string,
  busy: string,
  label: string,
  result: ForkResult | null,
): void {
  if (currentId.value !== frameId) {
    clearBusyHint(owner, busy);
    return;
  }
  if (busyHintOwner === owner) busyHintOwner = "";
  if (result && result.ok === false && result.presentation.noCheckpoint) {
    const reason = result.presentation.message || historyT("history.forkMessage.failed");
    button.textContent = reason;
    button.title = reason;
    button.removeAttribute("aria-busy");
    return;
  }
  button.textContent = label;
  button.title = label;
  button.removeAttribute("aria-disabled");
  button.removeAttribute("aria-busy");
}

function forkMessageControl(messageId: string): HTMLElement {
  const row = el("div", "msg-fork");
  const button = el("button", "msg-fork-btn") as HTMLButtonElement;
  button.type = "button";
  const label = historyT("history.forkMessage.label");
  button.textContent = label;
  button.title = label;
  button.setAttribute("data-fork-message-id", messageId);
  button.dataset.forkMessageId = messageId;
  button.onclick = () => {
    if (button.getAttribute("aria-disabled") === "true") return;
    const frameId = currentId.value;
    if (typeof frameId !== "string" || !frameId) return;
    // A repaint while the POST is pending: the first button still owns it.
    if (messageForkPending(frameId, messageId)) return;
    const busy = historyT("history.forkMessage.busy");
    const owner = frameId + "\0" + messageId;
    button.setAttribute("aria-disabled", "true");
    button.setAttribute("aria-busy", "true");
    button.textContent = busy;
    button.title = busy;
    hint(busy, false, true);
    busyHintOwner = owner;
    return forkFromMessage(frameId, messageId).then(
      (result) => {
        settleForkButton(button, frameId, owner, busy, label, result);
        return result;
      },
      (error: unknown) => {
        settleForkButton(button, frameId, owner, busy, label, null);
        throw error;
      },
    );
  };
  row.appendChild(button);
  return row;
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
  if (m.role === "user" && isContinuePrompt(text)) {
    // Continue's canned instruction is the platform's line, not the user's
    // words. Still a user row, so the live event finds and adopts it.
    const marker = el("div", "msg user continue-seed");
    rememberCandidateIdentity(marker, m);
    marker.appendChild(continueMarker());
    marker.dataset.ts = String(new Date(String(m.created_at || "")).getTime() || 0);
    (target || messagesHost())?.appendChild(marker);
    return marker;
  }
  const w = el("div", "msg " + (m.role === "user" ? "user" : "assistant"));
  rememberCandidateIdentity(w, m);
  (w as HTMLElement & { _messageText?: string })._messageText = text;
  if (m.role === "user") {
    const b = el("div", "bubble");
    // A text-form `@name#v-<id>` reads as `@name`; the chip below names the
    // version (ART-16). Rows stored before SYNC-5 also hide the product's
    // annotation instructions. The stored row is unchanged.
    b.textContent = stripProjectRefSyntax(stripRefVersions(userBubbleText(planModeRequestText(text))));
    w.appendChild(b);
    renderMessageRefChips(w, m.artifact_refs);
    renderProjectRefChips(w, text);
    renderAnnotationChip(w, m.annotations);
    if (canForkFromMessage(m) && typeof m.message_id === "string") {
      w.appendChild(forkMessageControl(m.message_id));
    }
  } else {
    const md = el("div", "md");
    md.innerHTML = renderMd(text);
    embedArtifactFigures(md);
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

/**
 * `@project:<path>`: a file in the session's project folder, as the `@` menu
 * writes it (`autocomplete/composer.ts`; the server reads the same token,
 * `server/artifact_refs.py` `PROJECT_REF`). The prefix is reference syntax,
 * not the user's words.
 */
const PROJECT_REF = /(^|\s)@project:([^\s，。、；：！？（）【】「」]+)/g;
const PROJECT_REF_TRAILING = /[，。、；：！？,.;:!?)）\]】"']+$/;

/** The distinct project-folder paths a message names. */
export function projectRefs(text: string): string[] {
  const paths: string[] = [];
  for (const match of String(text || "").matchAll(PROJECT_REF)) {
    const path = (match[2] || "").replace(PROJECT_REF_TRAILING, "");
    if (path && !path.split("/").includes("..") && !paths.includes(path)) paths.push(path);
  }
  return paths;
}

/** A user bubble's text with `@project:<path>` read as `@<path>`. */
export function stripProjectRefSyntax(text: string): string {
  return String(text || "").replace(PROJECT_REF, "$1@$2");
}

/** The project-folder files a user message names, as chips under its bubble. */
export function renderProjectRefChips(host: HTMLElement, text: string): void {
  const paths = projectRefs(text);
  if (!paths.length) return;
  let row = host.querySelector(":scope > .msg-refs") as HTMLElement | null;
  if (!row) {
    row = el("div", "msg-refs");
    host.appendChild(row);
  }
  for (const path of paths.slice(0, 8)) {
    const chip = el("span", "msg-ref-chip project");
    chip.appendChild(iconEl("folder", 11));
    chip.appendChild(el("span", null, publicText(path.split("/").pop() || path, 60)));
    chip.title = historyT("history.projectRef", path);
    row.appendChild(chip);
  }
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

export type HistoryTurns = {
  /** Render one stored item; `target` receives the top-level nodes only. */
  add(item: HistoryItem, target: ParentNode): void;
  /** `open`: the last turn is still running, so it is left unfinished. */
  end(open?: boolean): void;
};

/**
 * Stored rows assembled into turns, the way the live stream shows them
 * (`turnGroup.ts`): a user row is its own node, and everything the assistant
 * did until the next one -- narration rows, steps, approvals -- is one
 * `.msg.assistant` with one action row. A reload used to render each stored
 * narration paragraph as a message with its own Copy and rating, the cards
 * as its siblings.
 */
export function historyTurns(): HistoryTurns {
  let group: TurnGroup | null = null;
  /** Narration rows since the last card: one prose block, as live shows them. */
  let run: string[] = [];
  let prose: string[] = [];
  let failure: StoredMessage["failure"] = null;
  let review: { status: string; truth: unknown } | null = null;

  const flush = (): void => {
    if (!group || !run.length) return;
    group.md.innerHTML = renderMd(run.join("\n\n"));
    embedArtifactFigures(group.md);
    run = [];
  };
  /** The turn in progress, handed to `target` when its first card or prose arrives. */
  const turn = (target: ParentNode, ts: number): TurnGroup => {
    if (!group) {
      group = openTurnGroup();
      group.wrap.dataset.ts = String(ts || 0);
      target.appendChild(group.wrap);
    }
    return group;
  };
  const end = (open = false): void => {
    if (!group) return;
    flush();
    const wrap = group.wrap;
    if (failure && failure.request_id) wrap.appendChild(failureMeta(failure));
    if (review) {
      setMessageReviewBadge(wrap, review.status, review.truth);
      if (review.status !== "candidate") wrap.dataset.candidateResolved = "true";
    }
    // A running turn's stored cards wait for its replay, which takes each one
    // into the live turn (`adoptIntoLiveStream`); it has no action row yet.
    if (open) dropEmptyProse(wrap);
    else finishTurnGroup(wrap, prose.join("\n\n"));
    group = null;
    prose = [];
    failure = null;
    review = null;
  };
  /** A card renderer appends to its target: here, at the turn's current position. */
  const cardSink = (target: ParentNode, ts: number): ParentNode =>
    ({
      // The turn a card would join: a plan update repaints that turn's plan
      // card instead of appending another (`send/step.ts`).
      turn: group ? group.wrap : null,
      appendChild(node: Node): Node {
        const g = turn(target, ts);
        flush();
        placeCardInTurn(g, node as HTMLElement);
        return node;
      },
    }) as unknown as ParentNode;

  const add = (item: HistoryItem, target: ParentNode): void => {
    if (item.kind === "cell") {
      renderStoredCell(item.v as StoredCell, cardSink(target, item.t));
      return;
    }
    if (item.kind !== "msg") {
      const render = item.kind === "perm" ? renderStoredPermissionImpl : renderStoredStepImpl;
      if (render) render(item.v, cardSink(target, item.t));
      // As live: a step after a Cell's card makes that card its quieter head
      // (`send/step.ts` `demoteToolCard`).
      if (item.kind === "step" && group) {
        const cells = group.wrap.querySelectorAll(":scope > .activity");
        cells[cells.length - 1]?.classList.add("has-steps");
      }
      return;
    }
    const m = item.v as StoredMessage;
    const text = messageText(m);
    if (!text.trim()) return;
    if (m.role === "user") {
      end();
      renderStored(m, target);
      return;
    }
    const g = turn(target, item.t);
    const stopped = cancelledIdentity(m.cancelled);
    if (stopped) {
      // As the live stream ends a stopped turn: the marker closes its container.
      flush();
      g.wrap.classList.add("turn-stopped");
      g.wrap.dataset.turnStatus = "cancelled";
      g.wrap.appendChild(stoppedMarker(stopped));
      end();
      return;
    }
    const status = reviewStatusOf(m);
    // A reviewed answer is the row a later `candidate_resolved` names.
    if (status) delete g.wrap.dataset.messageId;
    rememberCandidateIdentity(g.wrap, m);
    if (status) {
      const raw = m.review_status || (m.metadata && m.metadata.review_status);
      const truth =
        raw && typeof raw === "object" && "user_truth" in raw
          ? (raw as { user_truth?: unknown }).user_truth
          : undefined;
      review = { status: String(status), truth };
    }
    if (m.failure && m.failure.request_id) failure = m.failure;
    run.push(text);
    prose.push(text);
  };
  return { add, end };
}

/**
 * A Notebook REPL cell's persisted step (`input.origin === "notebook"`). The
 * live chat never showed it -- a manual cell is not an agent turn -- so the
 * reopened transcript leaves it out too, rather than showing it alone above
 * the empty-session welcome.
 */
export function chatStep(row: Record<string, unknown>): boolean {
  const input = row.input;
  return !(input && typeof input === "object" && (input as { origin?: unknown }).origin === "notebook");
}

function historyNode(node: Element): boolean {
  return ["msg", "step", "perm-card"].some((name) => node.classList.contains(name));
}

/**
 * "Load earlier". Everything above the first user row on screen is the tail
 * of a turn whose beginning was not loaded, with every older step and
 * approval; the older page's rows belong among those, in time order. So that
 * region is rebuilt, as turns, from the rows it now has; nothing from the
 * first user row down is touched (the live turn, open cards, expanded steps).
 */
export function prependHistory(
  host: HTMLElement,
  older: StoredMessage[],
  held: {
    messages: StoredMessage[];
    steps: Array<Record<string, unknown>>;
    approvals?: Array<Record<string, unknown>>;
    cells?: StoredCell[];
  },
): void {
  const live = (liveStream.value as { wrap?: HTMLElement } | null)?.wrap || null;
  const kids = Array.from(host.children) as HTMLElement[];
  const userRow = (node: HTMLElement): boolean =>
    node.classList.contains("msg") && (node.classList.contains("user") || node.classList.contains("plan-seed"));
  const cut = kids.findIndex((node) => userRow(node) || node === live);
  const boundary = cut < 0 ? null : (kids[cut] as HTMLElement);
  const head = (cut < 0 ? kids : kids.slice(0, cut)).filter(historyNode);
  const lastHead = head[head.length - 1];
  const anchor = boundary || (lastHead ? lastHead.nextSibling : null);
  // The same rows, in the same order, the first page was painted from.
  const firstUser = held.messages.find((m) => m.role === "user" && messageText(m).trim());
  const inLive = (item: HistoryItem): boolean => {
    if (!live || item.kind === "msg") return false;
    if (item.kind === "cell") {
      const card = cellCardOnScreen((item.v as StoredCell).producing_cell_id);
      return !!card && live.contains(card);
    }
    const row = item.v as { step_id?: unknown; decision_id?: unknown };
    const registry = (item.kind === "perm" ? permCards.value : stepEls.value) as Record<string, { card?: Node } | undefined>;
    const card = registry[String(item.kind === "perm" ? row.decision_id : row.step_id)]?.card;
    return !!card && live.contains(card);
  };
  const items: HistoryItem[] = [];
  for (const item of interleaveHistory(
    [...older, ...held.messages],
    held.steps.filter(chatStep),
    held.approvals,
    held.cells,
  )) {
    if (firstUser && item.v === firstUser) break;
    if (!inLive(item)) items.push(item);
  }
  head.forEach((node) => node.remove());
  const stage = document.createDocumentFragment();
  const turns = historyTurns();
  items.forEach((item) => turns.add(item, stage));
  // With no user row below it, this region ends in the turn still running.
  turns.end(!!running.value && !(boundary && userRow(boundary)));
  host.insertBefore(stage, anchor);
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
    /** Runs every paint step (a caller that swaps registries around it). */
    around?: (paint: () => void) => void;
    /** The session is running: its last turn is not finished by this paint. */
    openTail?: boolean;
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
  // One grouping for the whole paint: a turn spans batches.
  const turns = historyTurns();
  const paint = opts.around || ((step: () => void) => step());
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
        paint(() => turns.add(item, sink));
      }
      if (i >= items.length) paint(() => turns.end(!!opts.openTail));
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

/**
 * A link to a session that does not exist (or that this login may not see).
 * The workbench used to open as if it did -- an empty transcript, a usable
 * composer, and "Invalid running state" in the history banner -- with no way
 * back but the browser's.
 */
export function renderMissingSession(
  host: ParentNode | null,
  back: { project?: (() => void) | null; home: () => void },
): void {
  if (!host) return;
  const wrap = el("div", "empty-session missing-session");
  wrap.setAttribute("role", "alert");
  wrap.appendChild(el("div", "es-title", historyT("history.missing.title")));
  wrap.appendChild(el("div", "es-sub", historyT("history.missing.sub")));
  const actions = el("div", "missing-actions");
  const button = (label: string, onClick: () => void): void => {
    const b = el("button", "outline-btn", label) as HTMLButtonElement;
    b.type = "button";
    b.onclick = onClick;
    actions.appendChild(b);
  };
  if (back.project) button(historyT("history.missing.project"), back.project);
  button(historyT("history.missing.home"), back.home);
  wrap.appendChild(actions);
  host.appendChild(wrap);
}

export function interleaveHistory(
  msgs: StoredMessage[],
  steps: Array<{ created_at?: number; seq?: number; [key: string]: unknown }>,
  approvals: Array<{ created_at?: unknown; [key: string]: unknown }> = [],
  cells: StoredCell[] = [],
): HistoryItem[] {
  const items: HistoryItem[] = [];
  // A Cell's card appears when it starts: before the steps it makes (a tie
  // with one of them goes to the Cell) and after the prose that announced it.
  for (const cell of chatCells(cells)) {
    items.push({ t: Number(cell.started_at), seq: -1, kind: "cell", v: cell });
  }
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
  // An approval is asked while the step that needs it is already on screen,
  // so on a millisecond tie it follows the step and precedes the prose.
  for (const a of approvals) {
    items.push({
      t: new Date(String(a.created_at || "")).getTime() || 0,
      seq: 5e14,
      kind: "perm",
      v: a,
    });
  }
  items.sort((a, b) => a.t - b.t || a.seq - b.seq);
  return items;
}

/**
 * A `user_message` event: the request a turn starts from, whichever tab sent
 * it. The sending tab adopts its own optimistic bubble (matched by the
 * `X-Request-Id` it sent, or the execution the 202 named); a transcript that
 * already shows the stored row (a reload's REST read, a replay) keeps it and
 * gets null back; any other tab shows the question it would otherwise never
 * have seen.
 */
export function showLiveUserMessage(
  m: Record<string, unknown>,
  host: ParentNode | null = messagesHost(),
): HTMLElement | null {
  if (!host) return null;
  const id = typeof m.message_id === "string" ? m.message_id : "";
  const requestId = typeof m.request_id === "string" ? m.request_id : "";
  const executionId = typeof m.execution_id === "string" ? m.execution_id : "";
  const rows = Array.from(
    (host as ParentNode & { querySelectorAll?: (s: string) => ArrayLike<Element> })
      .querySelectorAll?.(":scope > .msg.user") || [],
  ) as HTMLElement[];
  // Already on screen: nothing changes, and the caller is told so (null).
  if (id && rows.some((row) => row.dataset.messageId === id)) return null;
  const own = rows.find((row) =>
    !row.dataset.messageId &&
    ((requestId && row.dataset.requestId === requestId) ||
      (executionId && row.dataset.executionId === executionId)));
  if (own) {
    if (id) own.dataset.messageId = id;
    // Its turn is running now; it is no longer waiting in the queue.
    own.classList.remove("queued");
    return own;
  }
  (host as ParentNode).querySelector?.(":scope > .empty-session")?.remove();
  return renderStored(
    {
      role: "user",
      content: m.content,
      created_at: m.created_at,
      message_id: id || undefined,
      artifact_refs: m.artifact_refs,
      annotations: m.annotations,
    },
    host,
  );
}

/**
 * Rows stored before SYNC-5 carry the product's annotation instructions after
 * the user's words ("【图像标注反馈】…"); the bubble shows only the user's part.
 */
const INJECTED_ANNOTATION_BLOCK = /(?:^|\n\n)【(?:图像|Workbench )标注反馈】[\s\S]*$/;

export function userBubbleText(text: string): string {
  const own = text.replace(INJECTED_ANNOTATION_BLOCK, "").trim();
  return own || (INJECTED_ANNOTATION_BLOCK.test(text) ? t("send.imageAnnotationFallback") : text);
}

/** The pins a stored user message was sent with: the chip the live bubble shows. */
function renderAnnotationChip(w: HTMLElement, raw: unknown): void {
  if (!Array.isArray(raw) || !raw.length) return;
  const box = el("div", "annot-attach");
  box.appendChild(iconEl("message-square", 13));
  box.appendChild(el("span", "annot-attach-t", t("annot.attachCount", raw.length)));
  const list = el("div", "annot-attach-list");
  for (const item of raw) {
    const an = (item || {}) as { number?: unknown; artifact_name?: unknown; body?: unknown };
    const row = el("div", "annot-attach-row");
    row.appendChild(el("span", "annot-attach-pin", an.number == null ? "" : String(an.number)));
    row.appendChild(el("span", "annot-attach-file", String(an.artifact_name || "artifact")));
    row.appendChild(el("span", "annot-attach-body", "· " + String(an.body || "")));
    list.appendChild(row);
  }
  box.appendChild(list);
  w.appendChild(box);
}

setMessageForkRefresh(() => {
  scheduleWorkbenchRefresh();
});

installForkMessageVisibility();
