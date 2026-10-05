import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cells, execSources } from "../../stores/notebook";
import { currentId } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { setExecutionFetch } from "./api";
import { paintExecutionChrome } from "./boot";
import { execSourcesState, loadExecutionSources, selectExecFrame, toggleExecutedCode } from "./exec";

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


describe("execution response ownership", () => {
  function deferred() {
    let resolve!: (response: Response) => void;
    let reject!: (error: Error) => void;
    const promise = new Promise<Response>((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
  }
  const response = (body: unknown) => new Response(JSON.stringify(body));

  it("keeps the newer same-frame log when responses finish in reverse order", async () => {
    const old = deferred();
    const fresh = deferred();
    setExecutionFetch(vi.fn().mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise));
    const first = selectExecFrame("frame-x");
    const second = selectExecFrame("frame-x");
    fresh.resolve(response({ entries: [{ cell_index: 2 }] }));
    await second;
    old.resolve(response({ entries: [{ cell_index: 1 }] }));
    await first;
    expect(execSourcesState().cells["frame-x"]).toEqual([{ cell_index: 2 }]);
  });
  it("ignores an older same-frame failure after a newer success", async () => {
    const old = deferred();
    setExecutionFetch(vi.fn().mockReturnValueOnce(old.promise).mockResolvedValue(response({ entries: [2] })));
    const first = selectExecFrame("frame-x");
    await selectExecFrame("frame-x");
    old.reject(new Error("old failure"));
    await first;
    expect(execSourcesState().cells["frame-x"]).toEqual([2]);
    expect(execSourcesState().error).toBe("");
  });

  it("lets independent frame reads fill their own cache slots", async () => {
    const first = deferred();
    setExecutionFetch(vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue(response({ entries: [2] })));
    const pending = selectExecFrame("frame-x");
    await selectExecFrame("child");
    first.resolve(response({ entries: [1] }));
    await pending;
    expect(execSourcesState().cells).toEqual({ "frame-x": [1], child: [2] });
    expect(execSourcesState().selected).toBe("child");
  });

  it("does not let a pending frame error replace a cached selection's banner", async () => {
    const old = deferred();
    const st = execSourcesState();
    st.cells.child = [2];
    setExecutionFetch(() => old.promise);
    const pending = selectExecFrame("frame-x");
    await selectExecFrame("child");
    old.reject(new Error("unselected failure"));
    await pending;
    expect(st.error).toBe("");
  });

  it("does not cache failed reads and retries on the next selection", async () => {
    setExecutionFetch(vi.fn().mockRejectedValueOnce(new Error("failed"))
      .mockResolvedValueOnce(response({ entries: [2] })));
    await selectExecFrame("frame-x");
    expect(execSourcesState().cells["frame-x"]).toBeUndefined();
    expect(execSourcesState().error).not.toBe("");
    await selectExecFrame("frame-x");
    expect(execSourcesState().cells["frame-x"]).toEqual([2]);
    expect(execSourcesState().error).toBe("");
  });

  it.each(["session", "state"])("discards log reads after %s replacement", async (replacement) => {
    const old = deferred();
    setExecutionFetch(() => old.promise);
    const st = execSourcesState();
    const pending = selectExecFrame("frame-x");
    if (replacement === "session") currentId.value = "other-session";
    else execSources.value = null;
    old.resolve(response({ entries: [1] }));
    await pending;
    expect(st.cells).toEqual({});
  });

  it("invalidates old logs as soon as a new snapshot starts", async () => {
    const old = deferred();
    const snapshot = deferred();
    setExecutionFetch(vi.fn().mockReturnValueOnce(old.promise).mockReturnValueOnce(snapshot.promise)
      .mockResolvedValue(response({ entries: [2] })));
    const st = execSourcesState();
    const log = selectExecFrame("frame-x");
    const load = loadExecutionSources();
    old.resolve(response({ entries: [1] }));
    await log;
    expect(st.cells).toEqual({});
    snapshot.resolve(response({ frames: [{ frame_id: "frame-x" }] }));
    await load;
  });

  it("an obsolete snapshot failure cannot end the latest loading state or start a log read", async () => {
    const old = deferred();
    const fresh = deferred();
    const fetch = vi.fn().mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise);
    setExecutionFetch(fetch);
    const st = execSourcesState();
    st.data = { frames: [{ frame_id: "frame-x" }] };
    st.selected = "frame-x";
    const first = loadExecutionSources();
    const second = loadExecutionSources();
    old.reject(new Error("old failure"));
    await first;
    expect(st.loading).toBe(true);
    expect(st.error).toBe("");
    expect(fetch).toHaveBeenCalledTimes(2);
    fresh.reject(new Error("current failure"));
    await second;
    expect(st.loading).toBe(false);
    expect(st.error).toContain("current failure");
  });

  it("close and reopen starts a fresh snapshot and discards the closed request", async () => {
    const old = deferred();
    const fresh = deferred();
    const fetch = vi.fn().mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise);
    setExecutionFetch(fetch);
    toggleExecutedCode();
    toggleExecutedCode();
    toggleExecutedCode();
    expect(fetch).toHaveBeenCalledTimes(2);
    old.resolve(response({ frames: [{ frame_id: "obsolete" }] }));
    for (let i = 0; i < 10; i++) await Promise.resolve();
    expect(execSourcesState().data).toBeNull();
    expect(execSourcesState().loading).toBe(true);
    fresh.reject(new Error("current failure"));
    for (let i = 0; i < 10; i++) await Promise.resolve();
    expect(fetch.mock.calls.every(([url, init]) => url.endsWith("/execution-sources") && !init?.method)).toBe(true);
  });


  it("retires reads started during snapshot loading when the new snapshot arrives", async () => {
    const snapshot = deferred();
    const oldLog = deferred();
    const newLog = deferred();
    setExecutionFetch(vi.fn().mockReturnValueOnce(snapshot.promise)
      .mockReturnValueOnce(oldLog.promise).mockReturnValueOnce(newLog.promise));
    const st = execSourcesState();
    st.cells["frame-x"] = [0];
    st.selected = "frame-x";
    const load = loadExecutionSources();
    const pending = selectExecFrame("child");
    await selectExecFrame("frame-x");
    snapshot.resolve(response({ frames: [{ frame_id: "frame-x" }, { frame_id: "child" }] }));
    await load;
    oldLog.resolve(response({ entries: [1] }));
    await pending;
    expect(st.cells.child).toBeUndefined();
    expect(st.cells["frame-x"]).toEqual([0]);
    newLog.resolve(response({ entries: [2] }));
    for (let i = 0; i < 10; i++) await Promise.resolve();
    expect(st.cells["frame-x"]).toEqual([2]);
  });

  it("ignores an old snapshot failure after a newer snapshot succeeds", async () => {
    const old = deferred();
    setExecutionFetch(vi.fn().mockReturnValueOnce(old.promise)
      .mockResolvedValueOnce(response({ frames: [{ frame_id: "frame-x" }] }))
      .mockResolvedValue(response({ entries: [2] })));
    const first = loadExecutionSources();
    await loadExecutionSources();
    old.reject(new Error("old failure"));
    await first;
    expect(execSourcesState().error).toBe("");
    expect(execSourcesState().loading).toBe(false);
    expect(execSourcesState().data).toEqual({ frames: [{ frame_id: "frame-x" }] });
  });


  it.each(["resolve", "reject"])("ignores a log that settles after close: %s", async (outcome) => {
    const old = deferred();
    const st = execSourcesState();
    st.open = true;
    setExecutionFetch(() => old.promise);
    const pending = selectExecFrame("frame-x");
    toggleExecutedCode();
    if (outcome === "resolve") old.resolve(response({ entries: [1] }));
    else old.reject(new Error("closed failure"));
    await pending;
    expect(st.cells).toEqual({});
    expect(st.error).toBe("");
  });

  it.each(["session", "state"])("ignores snapshot cleanup after %s replacement", async (replacement) => {
    const old = deferred();
    setExecutionFetch(() => old.promise);
    const st = execSourcesState();
    const pending = loadExecutionSources();
    if (replacement === "session") currentId.value = "other-session";
    else execSources.value = null;
    old.reject(new Error("obsolete snapshot failure"));
    await pending;
    expect(st.error).toBe("");
    expect(st.loading).toBe(true);
  });


  it("keeps a newer failure retryable when an older successful log arrives", async () => {
    const old = deferred();
    setExecutionFetch(vi.fn().mockReturnValueOnce(old.promise)
      .mockRejectedValueOnce(new Error("new failure"))
      .mockResolvedValueOnce(response({ entries: [3] })));
    const pending = selectExecFrame("frame-x");
    await selectExecFrame("frame-x");
    const error = execSourcesState().error;
    expect(error).not.toBe("");
    old.resolve(response({ entries: [1] }));
    await pending;
    expect(execSourcesState().cells["frame-x"]).toBeUndefined();
    expect(execSourcesState().error).toBe(error);
    await selectExecFrame("frame-x");
    expect(execSourcesState().cells["frame-x"]).toEqual([3]);
    expect(execSourcesState().error).toBe("");
  });

});
