import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));

const FOCUSABLE_SEL =
  'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])';

class FakeClassList {
  readonly tokens = new Set<string>();
  constructor(
    private readonly owner: FakeEl,
    initial?: string,
  ) {
    if (initial) for (const t of initial.split(/\s+/).filter(Boolean)) this.tokens.add(t);
  }
  add(...names: string[]): void {
    for (const n of names) this.tokens.add(n);
    this.owner.syncHidden();
  }
  remove(...names: string[]): void {
    for (const n of names) this.tokens.delete(n);
    this.owner.syncHidden();
  }
  contains(name: string): boolean {
    return this.tokens.has(name);
  }
  toggle(name: string, force?: boolean): boolean {
    if (force === true) this.add(name);
    else if (force === false) this.remove(name);
    else if (this.tokens.has(name)) this.remove(name);
    else this.add(name);
    return this.tokens.has(name);
  }
  get value(): string {
    return [...this.tokens].join(" ");
  }
}

class FakeEl {
  tagName: string;
  id = "";
  classList: FakeClassList;
  children: FakeEl[] = [];
  parent: FakeEl | null = null;
  attrs = new Map<string, string>();
  listeners = new Map<string, Array<(e: unknown) => void>>();
  style: Record<string, string> = {};
  dataset: Record<string, string> = {};
  disabled = false;
  href = "";
  _text = "";
  _html = "";
  offsetParent: FakeEl | null;
  ownerDocument: FakeDoc;

  constructor(tag: string, doc: FakeDoc) {
    this.tagName = tag.toUpperCase();
    this.ownerDocument = doc;
    this.classList = new FakeClassList(this);
    this.offsetParent = this;
  }

  get className(): string {
    return this.classList.value;
  }
  set className(value: string) {
    this.classList = new FakeClassList(this, value);
    this.syncHidden();
  }

  get textContent(): string {
    if (this.children.length) return this.children.map((c) => c.textContent).join("");
    return this._text;
  }
  set textContent(value: string) {
    this.children = [];
    this._text = value == null ? "" : String(value);
  }

  get innerHTML(): string {
    return this._html || this.textContent;
  }
  set innerHTML(value: string) {
    this._html = value;
    this.children = [];
    // To a fixed point: one pass leaves `<<b>script>` as `<script>`, so a
    // test asserting on `.textContent` would read markup this double claims
    // to have stripped. Nothing here is a sanitiser -- it only has to not
    // lie about what it removed.
    let text = value,
      previous: string;
    do {
      previous = text;
      text = text.replace(/<[^>]+>/g, "");
    } while (text !== previous);
    this._text = text;
  }

  syncHidden(): void {
    this.offsetParent = this.classList.contains("hidden") ? null : this;
  }

  hasAttribute(name: string): boolean {
    if (name === "disabled") return this.disabled || this.attrs.has("disabled");
    return this.attrs.has(name);
  }
  setAttribute(name: string, value: string): void {
    this.attrs.set(name, value);
    if (name === "id") {
      this.id = value;
      this.ownerDocument.registerId(value, this);
    }
    if (name === "href") this.href = value;
    if (name === "disabled") this.disabled = true;
    if (name.startsWith("data-")) {
      const key = name
        .slice(5)
        .replace(/-([a-z])/g, (_, c: string) => c.toUpperCase());
      this.dataset[key] = value;
    }
  }
  getAttribute(name: string): string | null {
    if (name === "href") return this.href || this.attrs.get("href") || null;
    return this.attrs.has(name) ? (this.attrs.get(name) as string) : null;
  }
  removeAttribute(name: string): void {
    this.attrs.delete(name);
    if (name === "disabled") this.disabled = false;
  }

  appendChild(child: FakeEl | { data?: string; textContent?: string }): FakeEl {
    if (child instanceof FakeEl) {
      child.parent = this;
      this.children.push(child);
      if (child.id) this.ownerDocument.registerId(child.id, child);
      return child;
    }
    const text = elText(this.ownerDocument);
    text._text = String((child as { data?: string }).data ?? (child as { textContent?: string }).textContent ?? "");
    text.parent = this;
    this.children.push(text);
    return text;
  }

  remove(): void {
    if (!this.parent) return;
    this.parent.children = this.parent.children.filter((c) => c !== this);
    this.parent = null;
  }

  contains(node: FakeEl | null): boolean {
    if (!node) return false;
    if (node === this) return true;
    return this.children.some((c) => c.contains(node));
  }

  focus(_opts?: { preventScroll?: boolean }): void {
    this.ownerDocument.activeElement = this;
  }

  addEventListener(type: string, fn: (e: unknown) => void): void {
    const list = this.listeners.get(type) || [];
    list.push(fn);
    this.listeners.set(type, list);
  }

  querySelector(sel: string): FakeEl | null {
    return walkFind(this, sel);
  }
  querySelectorAll(sel: string): FakeEl[] {
    const out: FakeEl[] = [];
    walkCollect(this, sel, out, true);
    return out;
  }
}

function elText(doc: FakeDoc): FakeEl {
  const n = new FakeEl("#text", doc);
  n.offsetParent = null;
  return n;
}

function matchSel(node: FakeEl, sel: string): boolean {
  if (node.tagName === "#TEXT") return false;
  if (sel === FOCUSABLE_SEL) return isFocusable(node);
  if (sel.startsWith("#")) return node.id === sel.slice(1);
  if (sel.startsWith(".")) return node.classList.contains(sel.slice(1));
  if (sel === "[data-autofocus]") return node.hasAttribute("data-autofocus");
  if (sel === ".modal-box") return node.classList.contains("modal-box");
  if (sel === ".palette-item") return node.classList.contains("palette-item");
  if (sel === ".col-resizer-side") return node.classList.contains("col-resizer-side");
  if (sel === ".col-resizer-dock") return node.classList.contains("col-resizer-dock");
  if (sel.includes(",")) return sel.split(",").some((part) => matchSel(node, part.trim()));
  return node.tagName === sel.toUpperCase();
}

function isFocusable(node: FakeEl): boolean {
  if (node.tagName === "#TEXT") return false;
  const disabled = node.disabled || node.hasAttribute("disabled");
  if (disabled) return false;
  if (node.classList.contains("hidden")) return false;
  if (node.offsetParent === null) return false;
  const tag = node.tagName.toLowerCase();
  if (tag === "a" && (node.href || node.hasAttribute("href"))) return true;
  if (tag === "button" || tag === "textarea" || tag === "input" || tag === "select") return true;
  const ti = node.getAttribute("tabindex");
  if (ti !== null && ti !== "-1") return true;
  return false;
}

function walkFind(root: FakeEl, sel: string): FakeEl | null {
  for (const c of root.children) {
    if (matchSel(c, sel)) return c;
    const inner = walkFind(c, sel);
    if (inner) return inner;
  }
  return null;
}

function walkCollect(root: FakeEl, sel: string, out: FakeEl[], skipRoot: boolean): void {
  if (!skipRoot && matchSel(root, sel)) out.push(root);
  for (const c of root.children) walkCollect(c, sel, out, false);
}

class FakeDoc {
  body: FakeEl;
  documentElement: FakeEl;
  activeElement: FakeEl | null = null;
  ids = new Map<string, FakeEl>();

  constructor() {
    this.documentElement = new FakeEl("html", this);
    this.body = new FakeEl("body", this);
    this.body.id = "body";
    this.documentElement.appendChild(this.body);
  }

  registerId(id: string, el: FakeEl): void {
    this.ids.set(id, el);
  }

  getElementById(id: string): FakeEl | null {
    return this.ids.get(id) || null;
  }

  createElement(tag: string): FakeEl {
    return new FakeEl(tag, this);
  }

  createTextNode(data: string): FakeEl {
    const n = elText(this);
    n._text = data;
    return n;
  }

  querySelector(sel: string): FakeEl | null {
    if (sel.startsWith("#")) return this.getElementById(sel.slice(1));
    return walkFind(this.documentElement, sel);
  }

  querySelectorAll(sel: string): FakeEl[] {
    const out: FakeEl[] = [];
    walkCollect(this.documentElement, sel, out, true);
    return out;
  }

  contains(node: FakeEl | null): boolean {
    if (!node) return false;
    return this.documentElement.contains(node);
  }
}

function makeModal(doc: FakeDoc, id: string, opts?: { extraButtons?: number; autofocus?: boolean }): FakeEl {
  const modal = doc.createElement("div");
  modal.className = "modal hidden";
  modal.id = id;
  const box = doc.createElement("div");
  box.className = "modal-box";
  const close = doc.createElement("button");
  close.className = "icon-ghost";
  close.id = id + "-close";
  box.appendChild(close);
  if (opts?.autofocus) {
    const marked = doc.createElement("button");
    marked.setAttribute("data-autofocus", "");
    marked.id = id + "-auto";
    box.appendChild(marked);
  }
  const extra = opts?.extraButtons ?? 1;
  for (let i = 0; i < extra; i++) {
    const b = doc.createElement("button");
    b.id = id + "-btn-" + i;
    box.appendChild(b);
  }
  modal.appendChild(box);
  doc.body.appendChild(modal);
  return modal;
}

type ModalApi = typeof import("./modal");

describe("F-20 modal focus trap", () => {
  let doc: FakeDoc;
  let raf: FrameRequestCallback[];
  let api: ModalApi;
  let outside: FakeEl;

  beforeEach(async () => {
    vi.resetModules();
    doc = new FakeDoc();
    raf = [];
    outside = doc.createElement("button");
    outside.id = "outside";
    doc.body.appendChild(outside);
    doc.activeElement = outside;

    vi.stubGlobal("document", doc);
    vi.stubGlobal("window", { document: doc, ac: { open: false } });
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
      raf.push(cb);
      return raf.length;
    });
    api = await import("./modal");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  function flush(): void {
    const queued = raf.splice(0);
    for (const cb of queued) cb(0);
  }

  function key(name: string, shift = false): { key: string; shiftKey: boolean; preventDefault: ReturnType<typeof vi.fn> } {
    return { key: name, shiftKey: shift, preventDefault: vi.fn() };
  }

  it("pushes a stack entry only when the modal was hidden", () => {
    const modal = makeModal(doc, "cust");
    api.openModalEl(modal as unknown as HTMLElement);
    expect(modal.classList.contains("hidden")).toBe(false);
    expect(api._modalFocus.stack.length).toBe(1);
    expect(api._modalFocus.stack[0]?.prev).toBe(outside);
    api.openModalEl(modal as unknown as HTMLElement);
    expect(api._modalFocus.stack.length).toBe(1);
  });

  it("restores previous focus on close when the node is still in the document", () => {
    const modal = makeModal(doc, "cust");
    api.openModalEl(modal as unknown as HTMLElement);
    flush();
    expect(doc.activeElement).not.toBe(outside);
    api.closeModalEl(modal as unknown as HTMLElement);
    expect(modal.classList.contains("hidden")).toBe(true);
    expect(api._modalFocus.stack.length).toBe(0);
    expect(doc.activeElement).toBe(outside);
  });

  it("close of an already-hidden modal is a no-op", () => {
    const modal = makeModal(doc, "cust");
    api.closeModalEl(modal as unknown as HTMLElement);
    expect(api._modalFocus.stack.length).toBe(0);
    expect(doc.activeElement).toBe(outside);
  });

  it("nests: inner close restores the outer's focused node; outer close restores the original", () => {
    const a = makeModal(doc, "cust");
    const b = makeModal(doc, "modal");
    api.openModalEl(a as unknown as HTMLElement);
    flush();
    const aFocus = doc.activeElement;
    api.openModalEl(b as unknown as HTMLElement);
    expect(api._modalFocus.stack.length).toBe(2);
    flush();
    api.closeModalEl(b as unknown as HTMLElement);
    expect(api._modalFocus.stack.length).toBe(1);
    expect(doc.activeElement).toBe(aFocus);
    api.closeModalEl(a as unknown as HTMLElement);
    expect(doc.activeElement).toBe(outside);
    expect(api._modalFocus.stack.length).toBe(0);
  });

  it("closing a non-top stack entry splices that entry only (verbatim search-from-top)", () => {
    const a = makeModal(doc, "cust");
    const b = makeModal(doc, "modal");
    api.openModalEl(a as unknown as HTMLElement);
    api.openModalEl(b as unknown as HTMLElement);
    api.closeModalEl(a as unknown as HTMLElement);
    expect(api._modalFocus.stack.length).toBe(1);
    expect(api._modalFocus.stack[0]?.el).toBe(b);
    expect(a.classList.contains("hidden")).toBe(true);
    expect(b.classList.contains("hidden")).toBe(false);
  });

  it("does not restore focus when the previous node has been removed", () => {
    const modal = makeModal(doc, "cust");
    api.openModalEl(modal as unknown as HTMLElement);
    flush();
    expect(doc.activeElement).not.toBe(outside);
    const focused = doc.activeElement;
    outside.remove();
    expect(doc.contains(outside)).toBe(false);
    api.closeModalEl(modal as unknown as HTMLElement);
    expect(doc.activeElement).toBe(focused);
    expect(doc.activeElement).not.toBe(outside);
  });

  it("prefers [data-autofocus], else first non-icon-ghost control", () => {
    const modal = makeModal(doc, "cust", { autofocus: true, extraButtons: 1 });
    api.openModalEl(modal as unknown as HTMLElement);
    flush();
    expect(doc.activeElement?.id).toBe("cust-auto");
  });

  it("Tab from last cycles to first; Shift+Tab from first cycles to last", () => {
    const modal = makeModal(doc, "cust", { extraButtons: 2 });
    api.openModalEl(modal as unknown as HTMLElement);
    flush();
    const list = api._focusables(modal.querySelector(".modal-box") as unknown as Element);
    expect(list.length).toBeGreaterThanOrEqual(2);
    const first = list[0];
    const last = list[list.length - 1];
    doc.activeElement = last as unknown as FakeEl;
    const tab = key("Tab");
    api.trapModalKeydown(tab);
    expect(tab.preventDefault).toHaveBeenCalled();
    expect(doc.activeElement).toBe(first);

    doc.activeElement = first as unknown as FakeEl;
    const shift = key("Tab", true);
    api.trapModalKeydown(shift);
    expect(shift.preventDefault).toHaveBeenCalled();
    expect(doc.activeElement).toBe(last);
  });

  it("Tab when focus is outside the box moves to the first focusable", () => {
    const modal = makeModal(doc, "cust", { extraButtons: 1 });
    api.openModalEl(modal as unknown as HTMLElement);
    flush();
    doc.activeElement = outside;
    const tab = key("Tab");
    api.trapModalKeydown(tab);
    expect(tab.preventDefault).toHaveBeenCalled();
    const list = api._focusables(modal.querySelector(".modal-box") as unknown as Element);
    expect(doc.activeElement).toBe(list[0]);
  });

  it("Escape closes the topmost stack modal and restores focus", () => {
    const modal = makeModal(doc, "cust");
    api.openModalEl(modal as unknown as HTMLElement);
    flush();
    const esc = key("Escape");
    api.trapModalKeydown(esc);
    expect(esc.preventDefault).toHaveBeenCalled();
    expect(modal.classList.contains("hidden")).toBe(true);
    expect(doc.activeElement).toBe(outside);
  });

  it("Escape is not stolen when a blocker (palette) is registered", () => {
    const modal = makeModal(doc, "cust");
    api.openModalEl(modal as unknown as HTMLElement);
    api.addModalEscapeBlocker(() => true);
    const esc = key("Escape");
    api.trapModalKeydown(esc);
    expect(esc.preventDefault).not.toHaveBeenCalled();
    expect(modal.classList.contains("hidden")).toBe(false);
  });

  it("Escape is not stolen when window.ac.open is true", () => {
    (window as unknown as { ac: { open: boolean } }).ac.open = true;
    const modal = makeModal(doc, "cust");
    api.openModalEl(modal as unknown as HTMLElement);
    const esc = key("Escape");
    api.trapModalKeydown(esc);
    expect(esc.preventDefault).not.toHaveBeenCalled();
    expect(modal.classList.contains("hidden")).toBe(false);
  });

  it("falls back to a visible team modal when the stack is empty (the old bypass path)", () => {
    const team = makeModal(doc, "team-admin-modal");
    team.classList.remove("hidden");
    expect(api._modalFocus.stack.length).toBe(0);
    const esc = key("Escape");
    api.trapModalKeydown(esc);
    expect(esc.preventDefault).toHaveBeenCalled();
    expect(team.classList.contains("hidden")).toBe(true);
  });

  it("fallback list includes team ids so a bypassed team modal is still trapped", () => {
    expect(api.FALLBACK_MODAL_SELECTORS).toEqual([
      "#cust",
      "#modal",
      "#proj-modal",
      "#team-admin-modal",
      "#team-files-modal",
    ]);
  });

  it("does not treat hidden focusables or disabled buttons as tab stops", () => {
    const modal = makeModal(doc, "cust", { extraButtons: 0 });
    const box = modal.querySelector(".modal-box") as FakeEl;
    const hidden = doc.createElement("button");
    hidden.classList.add("hidden");
    box.appendChild(hidden);
    const disabled = doc.createElement("button");
    disabled.setAttribute("disabled", "");
    box.appendChild(disabled);
    const list = api._focusables(box as unknown as Element);
    expect(list.some((n) => n === (hidden as unknown as HTMLElement))).toBe(false);
    expect(list.some((n) => n === (disabled as unknown as HTMLElement))).toBe(false);
  });

  it("source does not import window-exports (Proxy install is a side effect)", () => {
    const src = readFileSync(join(here, "modal.ts"), "utf8");
    expect(src).not.toContain("window-exports");
  });
});
