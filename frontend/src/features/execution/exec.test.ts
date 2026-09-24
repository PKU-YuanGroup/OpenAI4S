import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { currentId } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { setExecutionFetch } from "./api";
import { paintExecutionChrome } from "./boot";
import { execSourcesState } from "./exec";

vi.mock("../notebook/Notebook", () => ({
  renderNotebook: vi.fn(),
  cellNode: vi.fn(() => document.createElement("div")),
}));

/** Just enough element for the dock painters: tree edits and class lookups. */
class Node {
  tagName: string;
  className = "";
  textContent = "";
  children: Node[] = [];
  parentElement: Node | null = null;
  attributes: Record<string, string> = {};
  style = { setProperty: vi.fn() };
  onclick: (() => void) | null = null;
  onchange: (() => void) | null = null;
  value = "";
  disabled = false;
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase();
  }
  setAttribute(key: string, value: string) {
    this.attributes[key] = value;
  }
  // HTMLElement is stubbed as this class, so chai formats failures as elements.
  getAttributeNames() {
    return Object.keys(this.attributes);
  }
  getAttribute(key: string) {
    return this.attributes[key] ?? null;
  }
  appendChild(child: Node) {
    child.remove();
    child.parentElement = this;
    this.children.push(child);
    return child;
  }
  replaceChildren(...nodes: Node[]) {
    this.children.forEach((child) => (child.parentElement = null));
    this.children = [];
    nodes.forEach((node) => this.appendChild(node));
  }
  remove() {
    const parent = this.parentElement;
    if (!parent) return;
    parent.children = parent.children.filter((child) => child !== this);
    this.parentElement = null;
  }
  replaceWith(node: Node) {
    const parent = this.parentElement;
    if (!parent) return;
    node.remove();
    parent.children.splice(parent.children.indexOf(this), 1, node);
    node.parentElement = parent;
    this.parentElement = null;
  }
  querySelectorAll(selector: string): Node[] {
    const cls = selector.replace(/^\./, "");
    return this.children.flatMap((child) => [
      ...(child.className.split(/\s+/).includes(cls) ? [child] : []),
      ...child.querySelectorAll(selector),
    ]);
  }
  querySelector(selector: string): Node | null {
    return this.querySelectorAll(selector)[0] || null;
  }
}

let dock: Node;

beforeEach(() => {
  resetStoreFields();
  currentId.value = "frame-x";
  dock = new Node();
  vi.stubGlobal("HTMLElement", Node);
  vi.stubGlobal("document", {
    createElement: (tag: string) => new Node(tag),
    getElementById: (id: string) => (id === "dock-notebook" ? dock : null),
  });
});

afterEach(() => {
  setExecutionFetch(null);
  vi.unstubAllGlobals();
});

describe("notebook dock chrome", () => {
  it("opening executed code removes the variable inspector the closed view appended", () => {
    paintExecutionChrome();
    expect(dock.querySelectorAll(".nb-variables")).toHaveLength(1);
    const st = execSourcesState();
    st.open = true;
    paintExecutionChrome();
    expect(dock.querySelectorAll(".nb-variables")).toHaveLength(0);
    expect(dock.querySelectorAll(".nb-exec")).toHaveLength(1);
  });
});
