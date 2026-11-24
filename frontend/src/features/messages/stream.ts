/**
 * Live assistant stream: dual-node markdown + StreamingPre tool output.
 *
 * Ports app.js:5403-5510 (`flushRender` / `scheduleRender` / `sealText` /
 * `startStream` / `ensure` / `feed`) with:
 *   - sealed prefix + live tail nodes instead of whole-`innerHTML` rewrite
 *   - `_mdStableCut` incremental scan
 *   - tool output `textNode.appendData(delta)`
 *   - `down()` via the shared rAF (no sync `scrollTop`)
 */

import { t } from "../../i18n/runtime";
import { renderMd } from "../md/render";
import { liveCells, _liveCell } from "../../stores/notebook";
import { stream as liveStream, stepEls } from "../../stores/stream";
import type { WsMessage } from "../ws/types";
import { $, el, keepStripsLast, messagesHost } from "./dom";
import {
  _mdStableCut,
  emptyMdCutState,
  mdStableCut,
  shouldAdvanceSealed,
  type MdCutState,
} from "./cut";
import { toolMetaLabel, type StreamingPreHandle } from "./delta";
import { buildActivityCard, cellCardOnScreen, resetActivityCard } from "./cellCard";
import {
  rememberCandidateIdentity,
  setLiveReviewBadge,
} from "./identity";
import { markCardRunning } from "./cardState";
import { cancelFrame, scheduleFrame } from "./raf";
import { down } from "./scroll";
import { appendLiveStoppedMarker, cancelledIdentity } from "./stopped";
import { embedArtifactFigures } from "./figures";
import { resayProgress } from "./progress";
import { _messagesFollow } from "../../stores/ui";
import { dropTurnGroupIfEmpty, openTurnGroup, placeCardInTurn } from "./turnGroup";

export const TOOL_LABELS: Record<string, string> = {
  run_python: "toolLabel.runPython",
  run_bash: "toolLabel.runBash",
  search_skills: "toolLabel.searchSkills",
  read_skill: "toolLabel.readSkill",
  write_file: "toolLabel.writeFile",
  read_file: "toolLabel.readFile",
  list_files: "toolLabel.listFiles",
  delegate: "toolLabel.delegate",
};

export type LiveStream = {
  wrap: HTMLElement;
  md: HTMLElement;
  sealed: HTMLElement | null;
  tail: HTMLElement | null;
  text: string;
  full: string;
  toolPre: HTMLElement | null;
  toolCard: HTMLElement | null;
  toolMeta: HTMLElement | null;
  toolHandle: StreamingPreHandle | null;
  _stableAt: number;
  _mdCut: MdCutState;
  _dirty: boolean;
  _raf: number | null;
  _lastFlush: number;
};

function currentStream(): LiveStream | null {
  return (liveStream.value as LiveStream | null) || null;
}

function ensureDual(st: LiveStream): void {
  // A replaced `st.md` (a reviewed answer swapped in by candidate.ts) leaves
  // sealed/tail and the cut state describing text that is no longer shown.
  if (st.sealed && st.tail && st.sealed.parentNode === st.md) return;
  resetMdState(st);
  st.md.innerHTML = "";
  st.sealed = el("div", "md-sealed");
  st.tail = el("div", "md-tail");
  st.md.appendChild(st.sealed);
  st.md.appendChild(st.tail);
}

function resetMdState(st: LiveStream): void {
  st._stableAt = 0;
  st._mdCut = emptyMdCutState();
  st.sealed = null;
  st.tail = null;
}

/** A list item, or an indented line that may continue one. */
const LIST_TAIL = /^(?:\s*(?:[-*+]|\d+[.)])[ \t]|\s+\S)/;

/**
 * Whether `renderMd` of the text up to `cut` is final no matter what is
 * appended. A stable cut sits after a blank line or a closing fence, and every
 * block `renderMd` knows ends there -- except a list, which continues across
 * blank lines when another item follows. So a region whose last line is
 * list-ish stays in the tail until a later cut settles it.
 */
function settledAt(text: string, from: number, cut: number): boolean {
  const lines = text.slice(from, cut).split("\n");
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i] || "";
    if (line.trim()) return !LIST_TAIL.test(line);
  }
  return true;
}

/**
 * app.js:5403-5426, dual-node. Settled text is rendered once, into chunks
 * appended to `sealed`; only the tail after it re-renders each flush.
 * Re-rendering the whole sealed prefix whenever the cut advanced rendered a
 * 60KB answer ~86 times over while it streamed.
 */
export function flushRender(st: LiveStream | null, finalRender?: boolean): void {
  if (!st) return;
  if (st._raf) {
    cancelFrame(st._raf);
    st._raf = null;
  }
  if (!st.md || (!st._dirty && !finalRender)) return;
  st._dirty = false;
  const text = st.text || "";
  st._lastFlush = performance.now();
  if (finalRender) {
    st.md.innerHTML = renderMd(text);
    embedArtifactFigures(st.md);
    resetMdState(st);
    return;
  }
  ensureDual(st);
  const cutState = mdStableCut(text, st._mdCut);
  st._mdCut = cutState;
  const cut = cutState.stable;
  const settled = st._stableAt || 0;
  if (st.sealed && shouldAdvanceSealed(cut, settled) && settledAt(text, settled, cut)) {
    st.sealed.insertAdjacentHTML("beforeend", renderMd(text.slice(settled, cut)));
    st._stableAt = cut;
  }
  if (st.tail) st.tail.innerHTML = renderMd(text.slice(st._stableAt || 0));
}

/** Past 600 chars a flush is due at most every 48ms (app.js: ~20/s). */
const FLUSH_GAP_MS = 48;
/** Unsettled tail chars per extra ms of gap. */
const TAIL_CHARS_PER_MS = 200;

/**
 * The tail re-renders whole on every flush, and a tail that cannot settle --
 * an unclosed code fence, a long loose list -- keeps growing: at 20 flushes a
 * second a long code block was re-highlighted from its first line twenty
 * times a second. The gap grows with the tail instead (1ms per 200 chars
 * past ~10K), which keeps the re-render work per second flat.
 */
function flushGap(st: LiveStream): number {
  const text = st.text || "";
  if (text.length <= 600 || !st._lastFlush) return 0;
  return Math.max(FLUSH_GAP_MS, (text.length - (st._stableAt || 0)) / TAIL_CHARS_PER_MS);
}

function renderFrame(st: LiveStream, deferred: boolean): void {
  st._raf = null;
  const gap = flushGap(st);
  // Within the gap: wait one frame, as app.js did; only a large tail waits
  // its whole gap out.
  if (performance.now() - st._lastFlush < gap && (!deferred || gap > FLUSH_GAP_MS)) {
    st._raf = scheduleFrame(() => renderFrame(st, true));
    return;
  }
  flushRender(st);
  down();
}

/** app.js:5427-5440. ~20/s cap on long streams; `down()` is rAF-coalesced. */
export function scheduleRender(st: LiveStream): void {
  st._dirty = true;
  if (st._raf) return;
  st._raf = scheduleFrame(() => renderFrame(st, false));
}

/** app.js:5445-5451. */
export function sealText(st: LiveStream | null): void {
  if (!st || !st.md) return;
  flushRender(st, true);
  st.md.classList.remove("cursor");
  resetMdState(st);
}

/** app.js:5452-5460. */
export function startStream(): LiveStream | null {
  const empty = $(".empty-session");
  if (empty) empty.remove();
  const host = messagesHost();
  if (!host) return null;
  const { wrap, md } = openTurnGroup();
  md.classList.add("cursor");
  host.appendChild(wrap);
  // The session's file strips stay under the turn, both of them.
  keepStripsLast(host);
  const st: LiveStream = {
    wrap,
    md,
    sealed: null,
    tail: null,
    text: "",
    full: "",
    toolPre: null,
    toolCard: null,
    toolMeta: null,
    toolHandle: null,
    _stableAt: 0,
    _mdCut: emptyMdCutState(),
    _dirty: false,
    _raf: null,
    _lastFlush: 0,
  };
  liveStream.value = st;
  stepEls.value = stepsOnScreen(stepEls.value);
  liveCells.value = [];
  _liveCell.value = null;
  down();
  return st;
}

/**
 * The step registry, minus cards no longer in the document. Opening a running
 * session renders its stored steps (registering them) and then replays the
 * turn, which starts with text_reset: wiping the registry here made every
 * replayed step a second card while the stored one stayed "running". Step
 * ids are unique, so keeping the cards on screen cannot capture another
 * turn's step; openConversation still starts each session with a fresh one.
 */
function stepsOnScreen(current: unknown): Record<string, unknown> {
  const kept = Object.create(null) as Record<string, unknown>;
  if (!current || typeof current !== "object") return kept;
  for (const [id, handle] of Object.entries(current as Record<string, unknown>)) {
    const card = handle && typeof handle === "object" ? (handle as { card?: { isConnected?: boolean } }).card : null;
    if (card && card.isConnected) kept[id] = handle;
  }
  return kept;
}

export function ensure(): LiveStream | null {
  const cur = currentStream();
  if (cur) return cur;
  return startStream();
}

/**
 * Append a card (a step, an approval) at the turn's current position: the
 * prose so far is sealed above it and later prose starts below it, which is
 * the order the stored transcript has.
 */
export function placeInLiveStream(card: HTMLElement, st: LiveStream): void {
  sealText(st);
  placeCardInTurn(st, card);
  st.text = "";
}

/**
 * Move a card already on screen into the live turn, at the position its
 * replayed event names. Reopening or reloading a running session renders the
 * turn's stored steps and approvals from REST, then replays the turn: the
 * replay updated those cards where REST had put them, so every card stacked
 * above the turn and all of its prose collected into one paragraph below
 * them. Never starts a stream; false when there is none or the card is in it.
 */
export function adoptIntoLiveStream(card: HTMLElement | null | undefined): boolean {
  const st = currentStream();
  if (!st || !st.wrap || !card || st.wrap.contains(card)) return false;
  const from = card.parentElement;
  const following = _messagesFollow.value !== false;
  placeInLiveStream(card, st);
  // The stored turn the card came from is gone once its last card has moved.
  dropTurnGroupIfEmpty(from);
  // Moving a card from above the view to the end makes the browser lower
  // `scrollTop` to keep what is on screen in place, which reads as the reader
  // scrolling up: a reloaded running session then stopped following and was
  // left at the top of its turn. A view that was following still is.
  if (following) down(true);
  return true;
}

function paintToolMeta(st: LiveStream): void {
  if (!st.toolMeta || !st.toolHandle) return;
  // meta → head → card, as feed() builds it.
  const head = st.toolMeta.parentNode as HTMLElement | null;
  const card = head ? (head.parentNode as HTMLElement | null) : null;
  const running = !!(card && card.dataset && card.dataset.state === "running");
  st.toolMeta.textContent = toolMetaLabel(st.toolHandle.outputLines, running);
}

/**
 * app.js:5462-5510. `storedOwnsChunk` skips a chunk REST already rendered.
 */
export function feed(
  kind: string,
  chunk: string,
  event?: WsMessage | null,
  storedOwnsChunk = false,
): void {
  if (storedOwnsChunk) return;
  const st = ensure();
  if (!st) return;
  rememberCandidateIdentity(st.wrap, event);
  if (kind === "tool") {
    const cellHeader = !!(event && event.cell_index != null);
    const subagentHeader = !cellHeader && chunk.startsWith("◆");
    const legacyCellHeader =
      !cellHeader && !st.toolPre && chunk.startsWith("⚙");
    if (cellHeader || subagentHeader || legacyCellHeader) {
      const suba = subagentHeader;
      const raw = chunk.replace(/[⚙◆\n]/g, "").trim();
      const tm = raw.match(/^([a-z_]+)/);
      const tool = tm && tm[1] ? tm[1] : "";
      const labelKey = TOOL_LABELS[tool];
      const label = suba ? raw : labelKey ? t(labelKey) : raw;
      // A reopened running session rendered this Cell's card from history;
      // its replay streams into that card, where the event puts it, rather
      // than adding a second one (as replayed steps do).
      const stored = cellHeader && !suba ? cellCardOnScreen(event && event.producing_cell_id) : null;
      const { card, meta, pre, handle } = stored
        ? resetActivityCard(stored, label)
        : buildActivityCard(label, suba);
      // The header is the card's title, already on its head and in the UI
      // language there; echoed as the body's first line it read "Running
      // analysis · cell 2" in English above the code (CHAT-6).
      // Not a success check until the cell says so (cardState.ts).
      if (!suba) markCardRunning(card, event);
      if (!stored || !adoptIntoLiveStream(card)) {
        sealText(st);
        placeCardInTurn(st, card);
      }
      st.toolPre = pre;
      st.toolHandle = handle;
      st.toolMeta = meta;
      if (!suba) {
        st.toolCard = card;
        (card as HTMLElement & { _demoted?: boolean })._demoted = false;
      }
      st.text = "";
      resetMdState(st);
      st._lastFlush = 0;
    } else if (st.toolHandle) {
      const add = chunk.replace(/^↳\s*/, "");
      st.toolHandle.append(add);
      paintToolMeta(st);
    }
  } else {
    // The stopped marker is rendered as a marker, not appended as prose, so
    // live matches the reopened transcript and follows the UI language.
    const stopped = event ? cancelledIdentity(event.cancelled) : null;
    if (stopped) {
      appendLiveStoppedMarker(st, stopped);
      down();
      return;
    }
    // Decision 30: a server progress line arrives as one chunk, in the
    // turn's language; it is shown in the UI language. `full` keeps what was
    // sent, as the stored row does (Copy, the rating key, the reconcile).
    st.text += resayProgress(chunk);
    st.full += chunk;
    st.md.classList.add("cursor");
    if (
      event &&
      (event.provisional || event.review_status === "candidate")
    ) {
      setLiveReviewBadge("candidate");
    }
    scheduleRender(st);
    return;
  }
  down();
}

export { _mdStableCut, mdStableCut, shouldAdvanceSealed };
