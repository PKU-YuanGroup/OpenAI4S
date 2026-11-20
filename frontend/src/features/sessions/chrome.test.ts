import { afterEach, describe, expect, it, vi } from "vitest";
import { LANG } from "../../i18n";
import { errorPrefix, reportFailure } from "./chrome";
import { actionFailedCopy } from "./copy";

describe("hint error prefix", () => {
  it("uses the zh/en literals without a new i18n key", () => {
    expect(errorPrefix("zh")).toBe("错误：");
    expect(errorPrefix("en")).toBe("Error: ");
  });
});

describe("watchActivateKeys", () => {
  afterEach(() => vi.unstubAllGlobals());

  class FakeEl {
    tabIndex = -1;
    dataset: Record<string, string> = {};
    attrs: Record<string, string> = {};
    constructor(readonly tagName: string, readonly classes: string[] = [], readonly children: FakeEl[] = []) {}
    getAttribute(name: string) { return this.attrs[name] ?? null; }
    setAttribute(name: string, value: string) { this.attrs[name] = value; }
    addEventListener() {}
    matches(selector: string) {
      return selector.split(",").some((part) => this.classes.includes(part.trim().replace(/^\./, "")));
    }
    querySelectorAll(selector: string): FakeEl[] {
      return this.children.flatMap((child) => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]);
    }
  }

  it("watches the hosts later lanes fill rather than the whole body, and keys their tiles", async () => {
    vi.resetModules();
    const observed: unknown[] = [];
    let deliver: (records: Array<{ addedNodes: FakeEl[] }>) => void = () => undefined;
    class FakeObserver {
      constructor(callback: (records: Array<{ addedNodes: FakeEl[] }>) => void) { deliver = callback; }
      observe(target: unknown) { observed.push(target); }
    }
    class FakeDocument {
      body = new FakeEl("BODY");
      hosts: Record<string, FakeEl> = {
        "#dock-tabs": new FakeEl("DIV"), "#dock-files": new FakeEl("DIV"), "#messages": new FakeEl("DIV"),
      };
      querySelector(selector: string) { return this.hosts[selector] ?? null; }
      querySelectorAll() { return []; }
    }
    vi.stubGlobal("MutationObserver", FakeObserver);
    vi.stubGlobal("HTMLElement", FakeEl);
    vi.stubGlobal("Document", FakeDocument);
    const doc = new FakeDocument();
    const { watchActivateKeys: watch } = await import("./chrome");
    watch(doc as unknown as ParentNode);
    const named = (target: unknown) =>
      target === doc.body ? "body" : Object.keys(doc.hosts).find((key) => doc.hosts[key] === target) ?? "other";
    expect(observed.map(named)).toEqual(["#dock-tabs", "#dock-files", "#messages"]);

    // An artifact strip lands in the transcript; its "more" row grows later.
    const tile = new FakeEl("DIV", ["tile"]);
    const row = new FakeEl("DIV", ["gen-tiles"], [tile]);
    deliver([{ addedNodes: [new FakeEl("DIV", ["generated"], [row])] }]);
    expect(tile.tabIndex).toBe(0);
    expect(tile.attrs.role).toBe("button");
    expect(observed.at(-1) === row).toBe(true);
  });
});

describe("reportFailure", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows the failure, with the error's own text, as an error hint", () => {
    const shown: string[] = [];
    const hintHost = { setAttribute() {}, innerHTML: "", appendChild: (node: { textContent: string }) => shown.push(node.textContent) };
    vi.stubGlobal("document", {
      querySelector: (sel: string) => (sel === "#composer-hint" ? hintHost : null),
      createElement: () => ({ textContent: "", style: {} }),
    });
    reportFailure(new Error("module refused"));
    expect(shown).toEqual([errorPrefix(LANG) + actionFailedCopy("module refused")]);
  });
});

describe("context menu keyboard (SYNC-9, SYNC-10)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  class Node {
    children: Node[] = [];
    parent: Node | null = null;
    attrs: Record<string, string> = {};
    className = "";
    textContent = "";
    innerHTML = "";
    type = "";
    disabled = false;
    style: Record<string, string> = {};
    classList = { add: (name: string) => (this.className += " " + name) };
    onclick: ((e: { stopPropagation: () => void }) => void) | null = null;
    offsetHeight = 100;
    offsetWidth = 100;
    isConnected = true;
    constructor(readonly tagName: string, readonly doc: FakeDoc) {}
    setAttribute(name: string, value: string) { this.attrs[name] = value; }
    appendChild(child: Node) { child.parent = this; this.children.push(child); return child; }
    remove() {
      if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this);
      this.parent = null;
      this.isConnected = false;
    }
    walk(): Node[] { return this.children.flatMap((c) => [c, ...c.walk()]); }
    contains(node: unknown) { return node === this || this.walk().includes(node as Node); }
    querySelector(sel: string) { return sel === "button" ? this.walk().find((n) => n.tagName === "button") ?? null : null; }
    querySelectorAll(sel: string) { return sel === '[role="menuitem"]' ? this.walk().filter((n) => n.attrs.role === "menuitem") : []; }
    focus() { this.doc.activeElement = this; }
    getBoundingClientRect() { return { top: 10, bottom: 30, left: 10 }; }
  }
  class FakeDoc {
    activeElement: unknown = null;
    listeners: Record<string, Array<(e: unknown) => void>> = {};
    body = new Node("body", this);
    createElement = (tag: string) => new Node(tag, this);
    addEventListener(type: string, fn: (e: unknown) => void) { (this.listeners[type] ||= []).push(fn); }
    removeEventListener(type: string, fn: (e: unknown) => void) {
      this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn);
    }
    key(key: string) {
      const event = { key, defaultPrevented: false, preventDefault() { event.defaultPrevented = true; } };
      for (const fn of [...(this.listeners.keydown || [])]) fn(event);
      return event;
    }
  }

  async function open() {
    vi.useFakeTimers();
    const doc = new FakeDoc();
    vi.stubGlobal("document", doc);
    vi.stubGlobal("window", { innerHeight: 800, innerWidth: 1200 });
    vi.stubGlobal("HTMLElement", Node);
    const anchor = new Node("button", doc);
    doc.body.appendChild(anchor);
    anchor.focus();
    const chosen: string[] = [];
    const { openMenu } = await import("./chrome");
    const pick = (label: string) => () =>
      void chosen.push(label + " with focus on " + (doc.activeElement === anchor ? "anchor" : "other"));
    openMenu(anchor as unknown as Element, [
      { label: "Rename", onClick: pick("Rename") },
      { label: "Export", onClick: pick("Export") },
      { sep: true },
      { label: "Delete", onClick: pick("Delete") },
    ]);
    vi.runAllTimers();
    const menu = doc.body.children.find((n) => n.attrs.role === "menu")!;
    const items = menu.children.filter((n) => n.attrs.role === "menuitem");
    const label = () => (doc.activeElement as Node | null)?.children.map((c) => c.textContent).join("") ?? "";
    return { doc, anchor, menu, items, label, chosen };
  }

  it("arrow keys, Home and End move between the items and wrap", async () => {
    const { doc, label } = await open();
    expect(label()).toBe("Rename");
    expect(doc.key("ArrowDown").defaultPrevented).toBe(true);
    expect(label()).toBe("Export");
    doc.key("ArrowDown");
    expect(label()).toBe("Delete");
    doc.key("ArrowDown");
    expect(label()).toBe("Rename");
    doc.key("ArrowUp");
    expect(label()).toBe("Delete");
    doc.key("Home");
    expect(label()).toBe("Rename");
    doc.key("End");
    expect(label()).toBe("Delete");
  });

  it("Escape closes the menu and gives focus back to the button that opened it", async () => {
    const { doc, anchor, menu } = await open();
    doc.key("ArrowDown");
    doc.key("Escape");
    expect(menu.isConnected).toBe(false);
    expect(doc.activeElement).toBe(anchor);
  });

  it("a chosen item runs with focus back on the opener, so a dialog it opens returns there", async () => {
    const { items, chosen, menu } = await open();
    items[1]!.onclick!({ stopPropagation() {} });
    expect(menu.isConnected).toBe(false);
    expect(chosen).toEqual(["Export with focus on anchor"]);
  });

  it("arrow keys do nothing once focus has left the menu", async () => {
    const { doc, anchor } = await open();
    anchor.focus();
    expect(doc.key("ArrowDown").defaultPrevented).toBe(false);
    expect(doc.activeElement).toBe(anchor);
  });
});
