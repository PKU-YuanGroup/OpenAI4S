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

import { isReady } from "../../compat/stub";
import { t } from "../../i18n/runtime";
import { paintIcon } from "../icons/paths";
import { renderMd } from "../md/render";
import { liveCells, _liveCell } from "../../stores/notebook";
import { stream as liveStream, stepEls } from "../../stores/stream";
import type { WsMessage } from "../ws/types";
import { $, el, messagesHost } from "./dom";
import {
  _mdStableCut,
  emptyMdCutState,
  mdStableCut,
  shouldAdvanceSealed,
  type MdCutState,
} from "./cut";
import { bindStreamingPre, toolMetaLabel, type StreamingPreHandle } from "./delta";
import {
  rememberCandidateIdentity,
  setLiveReviewBadge,
} from "./identity";
import { markCardRunning } from "./cardState";
import { cancelFrame, scheduleFrame } from "./raf";
import { down } from "./scroll";
import { appendLiveStoppedMarker, cancelledIdentity } from "./stopped";

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

type NbLiveStart = (
  tool: string,
  raw: string,
  kernelId: unknown,
  cellIndex: unknown,
  language: unknown,
) => void;
type NbLiveAppend = (txt: string) => void;

let nbLiveStartImpl: NbLiveStart | null = null;
let nbLiveAppendImpl: NbLiveAppend | null = null;

/** F-14 owns notebook live cells. Until then these are no-ops. */
export function setNbLiveStartImpl(fn: NbLiveStart | null): void {
  nbLiveStartImpl = fn;
}
export function setNbLiveAppendImpl(fn: NbLiveAppend | null): void {
  nbLiveAppendImpl = fn;
}

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

/** app.js:5427-5440. ~20/s cap on long streams; `down()` is rAF-coalesced. */
export function scheduleRender(st: LiveStream): void {
  st._dirty = true;
  if (st._raf) return;
  st._raf = scheduleFrame(() => {
    st._raf = null;
    const now = performance.now();
    if (
      st.text &&
      st.text.length > 600 &&
      st._lastFlush &&
      now - st._lastFlush < 48
    ) {
      st._raf = scheduleFrame(() => {
        st._raf = null;
        flushRender(st);
        down();
      });
      return;
    }
    flushRender(st);
    down();
  });
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
  const generated = $(".generated");
  if (generated) generated.remove();
  const empty = $(".empty-session");
  if (empty) empty.remove();
  const host = messagesHost();
  if (!host) return null;
  const wrap = el("div", "msg assistant");
  const md = el("div", "md cursor");
  wrap.appendChild(md);
  host.appendChild(wrap);
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

function callWindow(name: string, ...args: unknown[]): void {
  const fn = (globalThis as Record<string, unknown>)[name];
  if (!isReady(fn)) return;
  (fn as (...a: unknown[]) => unknown)(...args);
}

function newToolPre(): { pre: HTMLElement; handle: StreamingPreHandle } {
  const pre = el("pre");
  const textNode = document.createTextNode("");
  pre.appendChild(textNode);
  const handle = bindStreamingPre(textNode, "");
  return { pre, handle };
}

function paintToolMeta(st: LiveStream): void {
  if (!st.toolMeta || !st.toolHandle) return;
  st.toolMeta.textContent = toolMetaLabel(st.toolHandle.newlines);
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
  const structuredCellId =
    event && (event.producing_cell_id || event.cell_id);
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
      const card = el("div", "activity" + (suba ? " subagent" : ""));
      const h = el("div", "a-head");
      const ic = el("span", "ic");
      paintIcon(ic, "check", 16);
      h.appendChild(ic);
      h.appendChild(el("span", "lbl", label));
      const meta = el("span", "meta", "");
      h.appendChild(meta);
      const chev = el("span", "chev-t");
      paintIcon(chev, "chevron-down", 14);
      h.appendChild(chev);
      const { pre, handle } = newToolPre();
      handle.append(raw + "\n");
      card.appendChild(h);
      card.appendChild(pre);
      h.onclick = () => card.classList.toggle("open");
      // Not a success check until the cell says so (cardState.ts).
      if (!suba) markCardRunning(card, event);
      sealText(st);
      st.wrap.appendChild(card);
      st.toolPre = pre;
      st.toolHandle = handle;
      st.toolMeta = meta;
      if (!suba) {
        st.toolCard = card;
        (card as HTMLElement & { _demoted?: boolean })._demoted = false;
      }
      st.md = el("div", "md");
      st.wrap.appendChild(st.md);
      st.text = "";
      resetMdState(st);
      st._lastFlush = 0;
      if (!suba && !structuredCellId) {
        if (nbLiveStartImpl) {
          nbLiveStartImpl(
            tool,
            raw,
            event && event.kernel_id,
            event && event.cell_index,
            event && event.language,
          );
        } else {
          callWindow(
            "nbLiveStart",
            tool,
            raw,
            event && event.kernel_id,
            event && event.cell_index,
            event && event.language,
          );
        }
      }
    } else if (st.toolHandle) {
      const add = chunk.replace(/^↳\s*/, "");
      st.toolHandle.append(add);
      paintToolMeta(st);
      if (!structuredCellId) {
        if (nbLiveAppendImpl) nbLiveAppendImpl(add);
        else callWindow("nbLiveAppend", add);
      }
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
    st.text += chunk;
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
