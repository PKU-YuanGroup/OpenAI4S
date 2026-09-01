/**
 * Right-dock artifact-editor autocomplete. Port of app.js:13137-13229 plus
 * the per-editor wiring at 9481-9499.
 *
 * Keywords come from F-08 `editorKeywords(ext)` — no private EDKW table.
 * Inserts via execCommand('insertText') so native undo survives. One
 * controller, torn down by `edacTeardown` (F-10 / F-13 already callLane it).
 */

import { t } from "../../i18n/runtime";
import { _editing, _editorAC, artifacts } from "../../stores/artifacts";
import { editorKeywords } from "../md/highlight";
import { el } from "../sessions/dom";
import { edacDetectFrom, edacExt } from "./detect";
import {
  harvestBufferIdentifiers,
  rankEditorItems,
  type AcItem,
} from "./rank";

export type EditorController = {
  open: boolean;
  items: AcItem[];
  idx: number;
  start: number;
  composing: boolean;
  dead: boolean;
  justPicked: boolean;
  a: { filename?: string | null };
  ta: HTMLTextAreaElement;
  pop: HTMLElement;
  deb: ReturnType<typeof setTimeout> | 0;
};

let edMirror: HTMLElement | null = null;

export function edacDetect(ta: HTMLTextAreaElement): ReturnType<typeof edacDetectFrom> {
  return edacDetectFrom(ta.value, ta.selectionStart, ta.selectionEnd);
}

export function edacItems(
  a: { filename?: string | null },
  ta: HTMLTextAreaElement,
  q: string,
): AcItem[] {
  const keywords = editorKeywords(edacExt(a.filename));
  const buffer = harvestBufferIdentifiers(ta.value);
  return rankEditorItems(keywords, buffer, q, t("edac.keyword"));
}

function edacCaretXY(ta: HTMLTextAreaElement): { x: number; y: number; lh: number } {
  if (typeof document === "undefined") return { x: 0, y: 0, lh: 16 };
  if (!edMirror) {
    edMirror = el("div", "ed-mirror");
    document.body.appendChild(edMirror);
  }
  const m = edMirror;
  const cs = getComputedStyle(ta);
  (
    [
      "fontFamily",
      "fontSize",
      "fontWeight",
      "fontStyle",
      "letterSpacing",
      "lineHeight",
      "textTransform",
      "tabSize",
      "paddingTop",
      "paddingRight",
      "paddingBottom",
      "paddingLeft",
      "borderTopWidth",
      "borderRightWidth",
      "borderBottomWidth",
      "borderLeftWidth",
      "boxSizing",
      "whiteSpace",
      "wordWrap",
      "overflowWrap",
      "direction",
    ] as const
  ).forEach((p) => {
    const styles = m.style as unknown as Record<string, string>;
    const computed = cs as unknown as Record<string, string>;
    styles[p] = computed[p] || "";
  });
  m.style.width = ta.clientWidth + "px";
  m.textContent = ta.value.slice(0, ta.selectionStart);
  const mark = el("span");
  mark.textContent = "​";
  m.appendChild(mark);
  const mr = m.getBoundingClientRect();
  const sr = mark.getBoundingClientRect();
  const tr = ta.getBoundingClientRect();
  const lh = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.4;
  return {
    x: tr.left + (sr.left - mr.left) - ta.scrollLeft,
    y: tr.top + (sr.top - mr.top) - ta.scrollTop,
    lh,
  };
}

export function edacClose(ec: EditorController | null | undefined): void {
  if (!ec) return;
  ec.open = false;
  ec.items = [];
  if (ec.pop) ec.pop.classList.add("hidden");
}

export function edacRender(ec: EditorController): void {
  const box = ec.pop;
  box.innerHTML = "";
  ec.items.forEach((it, i) => {
    const row = el("div", "ac-item" + (i === ec.idx ? " on" : ""));
    row.appendChild(el("span", "ac-lbl", it.label));
    if (it.sub) row.appendChild(el("span", "ac-sub", it.sub));
    row.onmousedown = (e) => {
      e.preventDefault();
      edacPick(ec, i);
    };
    box.appendChild(row);
  });
  box.classList.remove("hidden");
  const on = box.querySelector(".ac-item.on");
  if (on) on.scrollIntoView({ block: "nearest" });
}

export function edacPosition(ec: EditorController): void {
  const c = edacCaretXY(ec.ta);
  const pop = ec.pop;
  pop.style.left = "0px";
  pop.style.top = "0px";
  const pw = pop.offsetWidth || 200;
  const ph = pop.offsetHeight || 120;
  let left = c.x;
  let top = c.y + c.lh;
  if (typeof window !== "undefined") {
    if (left + pw > window.innerWidth - 8) left = Math.max(8, window.innerWidth - 8 - pw);
    if (top + ph > window.innerHeight - 8) top = Math.max(8, c.y - ph);
  }
  pop.style.left = Math.round(left) + "px";
  pop.style.top = Math.round(top) + "px";
}

export function edacUpdate(ec: EditorController): void {
  if (ec.dead || ec.composing || ec.ta.disabled) return;
  const d = edacDetect(ec.ta);
  if (!d) {
    edacClose(ec);
    return;
  }
  const items = edacItems(ec.a, ec.ta, d.query);
  if (!items.length) {
    edacClose(ec);
    return;
  }
  ec.open = true;
  ec.items = items;
  ec.idx = 0;
  ec.start = d.start;
  edacRender(ec);
  edacPosition(ec);
}

export function edacPick(ec: EditorController, i: number): void {
  const it = ec.items[i];
  if (!it) return;
  const ta = ec.ta;
  const d = edacDetect(ta);
  if (!d || d.start !== ec.start) {
    edacClose(ec);
    return;
  }
  const v = ta.value;
  let end = ta.selectionStart;
  while (end < v.length && /[\w$]/.test(v[end] || "")) end++;
  ta.focus();
  ta.setSelectionRange(d.start, end);
  ec.justPicked = true;
  let ok = false;
  try {
    ok = document.execCommand("insertText", false, it.label);
  } catch {
    ok = false;
  }
  if (!ok) {
    ta.setRangeText(it.label, d.start, end, "end");
    ta.dispatchEvent(new Event("input", { bubbles: true }));
  }
  ec.justPicked = false;
  edacClose(ec);
}

export function edacTeardown(): void {
  const ec = _editorAC.value as EditorController | null;
  if (!ec) return;
  ec.dead = true;
  if (ec.deb) clearTimeout(ec.deb);
  ec.open = false;
  _editorAC.value = null;
}

function artifactForEditor(): { filename?: string | null } {
  const id = _editing.value;
  const list = (artifacts.value || []) as Array<{ id?: string; filename?: string }>;
  const hit = list.find((a) => a && a.id === id);
  return hit || { filename: "" };
}

export function bindEditorAutocomplete(
  ta: HTMLTextAreaElement,
  a: { filename?: string | null } = artifactForEditor(),
): EditorController {
  edacTeardown();
  ta.dataset.edacBound = "1";
  let pop = ta.parentElement
    ? (ta.parentElement.querySelector(".edit-ac") as HTMLElement | null)
    : null;
  if (!pop) {
    pop = el("div", "edit-ac hidden");
    if (ta.parentNode) ta.parentNode.insertBefore(pop, ta.nextSibling);
    else if (typeof document !== "undefined") document.body.appendChild(pop);
  }
  const ec: EditorController = {
    open: false,
    items: [],
    idx: 0,
    start: 0,
    composing: false,
    dead: false,
    justPicked: false,
    a,
    ta,
    pop,
    deb: 0,
  };
  _editorAC.value = ec;
  ta.addEventListener("input", () => {
    if (ec.composing || ec.justPicked) return;
    if (ec.deb) clearTimeout(ec.deb);
    ec.deb = setTimeout(() => {
      if (!ec.dead) edacUpdate(ec);
    }, 90);
  });
  ta.addEventListener("keydown", (e) => {
    if (e.isComposing || e.keyCode === 229 || ec.composing) return;
    if (!ec.open) return;
    if (
      e.key === "ArrowLeft" ||
      e.key === "ArrowRight" ||
      e.key === "Home" ||
      e.key === "End" ||
      e.key === "PageUp" ||
      e.key === "PageDown"
    ) {
      edacClose(ec);
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      ec.idx = (ec.idx + 1) % ec.items.length;
      edacRender(ec);
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      ec.idx = (ec.idx - 1 + ec.items.length) % ec.items.length;
      edacRender(ec);
      return;
    }
    if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      edacPick(ec, ec.idx);
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      edacClose(ec);
    }
  });
  ta.addEventListener("compositionstart", () => {
    ec.composing = true;
    edacClose(ec);
  });
  ta.addEventListener("compositionend", () => {
    ec.composing = false;
    requestAnimationFrame(() => {
      if (!ec.dead) edacUpdate(ec);
    });
  });
  ta.addEventListener("blur", () =>
    setTimeout(() => {
      if (!ec.dead) edacClose(ec);
    }, 120),
  );
  ta.addEventListener("scroll", () => edacClose(ec));
  ta.addEventListener("click", () => edacClose(ec));
  return ec;
}

let watching = false;

export function watchEditAreas(): void {
  if (typeof document === "undefined" || watching) return;
  watching = true;
  const scan = (): void => {
    document.querySelectorAll("textarea.edit-area").forEach((node) => {
      const ta = node as HTMLTextAreaElement;
      if (ta.dataset.edacBound) return;
      bindEditorAutocomplete(ta, artifactForEditor());
    });
  };
  scan();
  if (typeof MutationObserver === "function") {
    const ob = new MutationObserver(scan);
    ob.observe(document.documentElement || document.body, {
      childList: true,
      subtree: true,
    });
  }
}
