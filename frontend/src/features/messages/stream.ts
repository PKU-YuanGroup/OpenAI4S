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
import { cancelFrame, scheduleFrame } from "./raf";
import { down } from "./scroll";

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
  if (st.sealed && st.tail) return;
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

/** app.js:5403-5426. Dual-node: sealed rewritten only when the cut advances. */
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
  if (shouldAdvanceSealed(cut, st._stableAt || 0) && st.sealed) {
    st._stableAt = cut;
    st.sealed.innerHTML = renderMd(text.slice(0, cut));
  }
  if (st.tail) {
    if (st._stableAt && text.length > st._stableAt) {
      st.tail.innerHTML = renderMd(text.slice(st._stableAt));
    } else {
      st.tail.innerHTML = renderMd(text);
    }
  }
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
  stepEls.value = Object.create(null);
  liveCells.value = [];
  _liveCell.value = null;
  down();
  return st;
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
      ic.setAttribute("data-icon", "check");
      ic.setAttribute("data-icon-size", "16");
      h.appendChild(ic);
      h.appendChild(el("span", "lbl", label));
      const meta = el("span", "meta", "");
      h.appendChild(meta);
      const chev = el("span", "chev-t");
      chev.setAttribute("data-icon", "chevron-down");
      chev.setAttribute("data-icon-size", "14");
      h.appendChild(chev);
      const { pre, handle } = newToolPre();
      handle.append(raw + "\n");
      card.appendChild(h);
      card.appendChild(pre);
      h.onclick = () => card.classList.toggle("open");
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
