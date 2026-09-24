import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cells } from "../../stores/notebook";
import { currentId } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { setExecutionFetch } from "./api";
import { paintExecutionChrome } from "./boot";
import { execSourcesState, toggleExecutedCode } from "./exec";

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

describe("executed-code snapshot", () => {
  let log: Array<Record<string, unknown>>;
  const cell = (index: number) => ({ producing_cell_id: "cell-" + index, cell_index: index, status: "ok" });
  const settle = async () => {
    for (let i = 0; i < 6; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
  };

  beforeEach(() => {
    log = [cell(1)];
    setExecutionFetch(async (url) => {
      if (url.endsWith("/frames/frame-x/execution-sources"))
        return new Response(JSON.stringify({ frames: [{ frame_id: "frame-x" }] }));
      if (url.endsWith("/frames/frame-x/execution-log"))
        return new Response(JSON.stringify({ entries: log.slice() }));
      return new Response("{}", { status: 404 });
    });
  });

  it("is read again each time the view is opened", async () => {
    toggleExecutedCode();
    await settle();
    expect(execSourcesState().cells["frame-x"]).toHaveLength(1);
    toggleExecutedCode();
    log.push(cell(2));
    toggleExecutedCode();
    await settle();
    expect(execSourcesState().cells["frame-x"]).toHaveLength(2);
  });

  it("is read again when a cell finishes while it is open", async () => {
    cells.value = [cell(1)];
    toggleExecutedCode();
    await settle();
    log.push(cell(2));
    // notebook_cell_finished -> loadExecutionLog -> the dock repaints.
    cells.value = [cell(1), cell(2)];
    paintExecutionChrome();
    await settle();
    expect(execSourcesState().cells["frame-x"]).toHaveLength(2);
  });
});
