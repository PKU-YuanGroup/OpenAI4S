/**
 * Behaviour of the imperative Timeline island against a small fake DOM.
 *
 * Vitest runs in node, so this file carries its own element double: enough
 * of the DOM for the island to build, re-render and reconcile — a selector
 * matcher for the `#id` / `.class` / `tag` / `[attr]` / descendant forms the
 * island uses, focus that is lost when an element leaves the document (as a
 * browser blurs a detached node), and listener bookkeeping so leaks show up.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStoreFields } from "../../stores/signal-field";
import { onEvent } from "../ws/registry";
import { loadWorkbenchState, renderActionTimeline, renderBranchPanel } from "./island";
import { renderQueueStrip } from "./queue";
import { installTimeline } from "./index";
import { S } from "./s";
import { sanitizeActionTimeline } from "./sanitize";

type Listener = (event: FakeEvent) => void;
type FakeEvent = { type: string; target?: FakeElement; key?: string; [key: string]: unknown };

const kebab = (name: string) => name.replace(/[A-Z]/g, (c) => "-" + c.toLowerCase());

class FakeClassList {
  constructor(private owner: FakeElement) {}
  private get list(): string[] {
    return this.owner.className.split(/\s+/).filter(Boolean);
  }
  add(...names: string[]): void {
    const next = this.list;
    names.forEach((name) => {
      if (!next.includes(name)) next.push(name);
    });
    this.owner.className = next.join(" ");
  }
  remove(...names: string[]): void {
    this.owner.className = this.list.filter((name) => !names.includes(name)).join(" ");
  }
  contains(name: string): boolean {
    return this.list.includes(name);
  }
  toggle(name: string, force?: boolean): boolean {
    const on = force === undefined ? !this.contains(name) : force;
    if (on) this.add(name);
    else this.remove(name);
    return on;
  }
}

class FakeElement {
  [key: string]: unknown;
  tagName: string;
  id = "";
  className = "";
  attrs = new Map<string, string>();
  kids: FakeElement[] = [];
  parentNode: FakeElement | null = null;
  ownerDocument: FakeDocument;
  listeners = new Map<string, Set<Listener>>();
  classList = new FakeClassList(this);
  style: Record<string, unknown> & { setProperty: (k: string, v: string) => void; getPropertyValue: (k: string) => string };
  dataset: Record<string, string>;
  scrollTop = 0;
  scrollLeft = 0;
  scrollHeight = 0;
  clientHeight = 0;
  offsetHeight = 0;
  offsetWidth = 0;
  disabled = false;
  value = "";
  private text = "";

  constructor(doc: FakeDocument, tag: string) {
    this.ownerDocument = doc;
    this.tagName = tag.toUpperCase();
    const props: Record<string, string> = {};
    this.style = Object.assign(Object.create(null), {
      setProperty: (k: string, v: string) => {
        props[k] = v;
      },
      getPropertyValue: (k: string) => props[k] || "",
    });
    const attrs = this.attrs;
    this.dataset = new Proxy({} as Record<string, string>, {
      get: (_t, key) => (typeof key === "string" ? attrs.get("data-" + kebab(key)) : undefined),
      set: (_t, key, value) => {
        attrs.set("data-" + kebab(String(key)), String(value));
        return true;
      },
      deleteProperty: (_t, key) => {
        attrs.delete("data-" + kebab(String(key)));
        return true;
      },
      has: (_t, key) => typeof key === "string" && attrs.has("data-" + kebab(key)),
    });
  }

  get children(): FakeElement[] {
    return this.kids;
  }
  get childNodes(): FakeElement[] {
    return this.kids;
  }
  get firstChild(): FakeElement | null {
    return this.kids[0] || null;
  }
  get lastChild(): FakeElement | null {
    return this.kids[this.kids.length - 1] || null;
  }
  get firstElementChild(): FakeElement | null {
    return this.firstChild;
  }
  get lastElementChild(): FakeElement | null {
    return this.lastChild;
  }
  get parentElement(): FakeElement | null {
    return this.parentNode;
  }
  get isConnected(): boolean {
    let node: FakeElement | null = this;
    while (node) {
      if (node === this.ownerDocument.documentElement) return true;
      node = node.parentNode;
    }
    return false;
  }
  get textContent(): string {
    return this.kids.length ? this.kids.map((kid) => kid.textContent).join("") : this.text;
  }
  set textContent(value: string) {
    this.detachAll();
    this.text = value == null ? "" : String(value);
  }
  set innerHTML(value: string) {
    this.detachAll();
    this.text = "";
    this.html = String(value);
  }
  get innerHTML(): string {
    return String(this.html || "");
  }

  setAttribute(name: string, value: string): void {
    if (name === "class") this.className = String(value);
    else if (name === "id") this.id = String(value);
    else this.attrs.set(name, String(value));
  }
  getAttribute(name: string): string | null {
    if (name === "class") return this.className;
    if (name === "id") return this.id || null;
    return this.attrs.has(name) ? (this.attrs.get(name) as string) : null;
  }
  hasAttribute(name: string): boolean {
    return this.getAttribute(name) != null;
  }
  removeAttribute(name: string): void {
    this.attrs.delete(name);
  }

  private dropFocusWithin(node: FakeElement): void {
    // A browser blurs a focused element the moment it leaves the document,
    // even when it is only being moved; model that so moves are visible.
    const focused = this.ownerDocument.focused;
    if (focused && node.contains(focused)) this.ownerDocument.focused = null;
  }
  private detachAll(): void {
    this.kids.forEach((kid) => {
      this.dropFocusWithin(kid);
      kid.parentNode = null;
    });
    this.kids = [];
  }
  private adopt(node: FakeElement): FakeElement[] {
    if (node.tagName === "#FRAGMENT") {
      const moved = node.kids.slice();
      node.detachAll();
      return moved;
    }
    if (node.parentNode) node.remove();
    return [node];
  }
  appendChild<T extends FakeElement>(node: T): T {
    this.adopt(node).forEach((kid) => {
      kid.parentNode = this;
      this.kids.push(kid);
    });
    this.text = "";
    return node;
  }
  append(...nodes: FakeElement[]): void {
    nodes.forEach((node) => this.appendChild(node));
  }
  insertBefore<T extends FakeElement>(node: T, ref: FakeElement | null): T {
    if (!ref) return this.appendChild(node);
    const moved = this.adopt(node);
    const at = this.kids.indexOf(ref);
    moved.forEach((kid) => {
      kid.parentNode = this;
    });
    this.kids.splice(at < 0 ? this.kids.length : at, 0, ...moved);
    return node;
  }
  replaceChildren(...nodes: FakeElement[]): void {
    this.detachAll();
    this.text = "";
    nodes.forEach((node) => this.appendChild(node));
  }
  remove(): void {
    if (!this.parentNode) return;
    this.dropFocusWithin(this);
    const parent = this.parentNode;
    parent.kids = parent.kids.filter((kid) => kid !== this);
    this.parentNode = null;
  }
  replaceWith(node: FakeElement): void {
    const parent = this.parentNode;
    if (!parent) return;
    parent.insertBefore(node, this);
    this.remove();
  }
  contains(node: unknown): boolean {
    let current = node as FakeElement | null;
    while (current) {
      if (current === this) return true;
      current = current.parentNode;
    }
    return false;
  }
  matches(selector: string): boolean {
    return matchesSelector(this, selector);
  }
  closest(selector: string): FakeElement | null {
    let node: FakeElement | null = this;
    while (node) {
      if (node.tagName !== "#FRAGMENT" && node.matches(selector)) return node;
      node = node.parentNode;
    }
    return null;
  }
  querySelectorAll(selector: string): FakeElement[] {
    const found: FakeElement[] = [];
    const walk = (node: FakeElement) => {
      node.kids.forEach((kid) => {
        if (kid.matches(selector)) found.push(kid);
        walk(kid);
      });
    };
    walk(this);
    return found;
  }
  querySelector(selector: string): FakeElement | null {
    return this.querySelectorAll(selector)[0] || null;
  }
  addEventListener(type: string, listener: Listener): void {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(listener);
  }
  removeEventListener(type: string, listener: Listener): void {
    this.listeners.get(type)?.delete(listener);
  }
  dispatchEvent(event: FakeEvent): boolean {
    event.target = event.target || this;
    let node: FakeElement | null = this;
    while (node) {
      node.listeners.get(event.type)?.forEach((listener) => listener(event));
      const handler = node["on" + event.type];
      if (typeof handler === "function") (handler as (e: FakeEvent) => void).call(node, event);
      node = node.parentNode;
    }
    return true;
  }
  click(): void {
    if (this.disabled) return;
    this.dispatchEvent({ type: "click" });
  }
  focus(): void {
    if (this.disabled || !this.isConnected) return;
    this.ownerDocument.focused = this;
  }
  blur(): void {
    if (this.ownerDocument.focused === this) this.ownerDocument.focused = null;
  }
  getBoundingClientRect() {
    return { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 };
  }
  setSelectionRange(start: number, end: number): void {
    this.selectionStart = start;
    this.selectionEnd = end;
  }
}

function matchesCompound(node: FakeElement, compound: string): boolean {
  const parts = compound.match(/(^[a-zA-Z][\w-]*)|(#[\w-]+)|(\.[\w-]+)|(\[[^\]]+\])/g) || [];
  if (parts.join("") !== compound) throw new Error(`fake DOM cannot match selector part: ${compound}`);
  return parts.every((part) => {
    if (part[0] === "#") return node.id === part.slice(1);
    if (part[0] === ".") return node.classList.contains(part.slice(1));
    if (part[0] === "[") {
      const [, name, value] = part.match(/^\[([\w-]+)(?:=["']?([^"'\]]*)["']?)?\]$/) || [];
      if (!name) return false;
      const actual = node.getAttribute(name);
      return value === undefined ? actual != null : actual === value;
    }
    return node.tagName === part.toUpperCase();
  });
}

function matchesSelector(node: FakeElement, selector: string): boolean {
  const chain = selector.trim().split(/\s+/);
  if (!matchesCompound(node, chain[chain.length - 1] as string)) return false;
  let ancestor = node.parentNode;
  for (let index = chain.length - 2; index >= 0; index -= 1) {
    while (ancestor && !matchesCompound(ancestor, chain[index] as string)) ancestor = ancestor.parentNode;
    if (!ancestor) return false;
    ancestor = ancestor.parentNode;
  }
  return true;
}

class FakeDocument {
  focused: FakeElement | null = null;
  listeners = new Map<string, Set<Listener>>();
  documentElement: FakeElement;
  body: FakeElement;
  constructor() {
    this.documentElement = new FakeElement(this, "html");
    this.body = new FakeElement(this, "body");
    this.documentElement.appendChild(this.body);
  }
  get activeElement(): FakeElement {
    return this.focused && this.focused.isConnected ? this.focused : this.body;
  }
  createElement(tag: string): FakeElement {
    return new FakeElement(this, tag);
  }
  createElementNS(_ns: string, tag: string): FakeElement {
    return new FakeElement(this, tag);
  }
  createDocumentFragment(): FakeElement {
    return new FakeElement(this, "#fragment");
  }
  querySelectorAll(selector: string): FakeElement[] {
    return this.documentElement.querySelectorAll(selector);
  }
  querySelector(selector: string): FakeElement | null {
    return this.documentElement.querySelector(selector);
  }
  getElementById(id: string): FakeElement | null {
    return this.querySelector("#" + id);
  }
  addEventListener(type: string, listener: Listener): void {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(listener);
  }
  removeEventListener(type: string, listener: Listener): void {
    this.listeners.get(type)?.delete(listener);
  }
  listenerCount(type: string): number {
    return this.listeners.get(type)?.size || 0;
  }
}

function mountDocument(): FakeDocument {
  const doc = new FakeDocument();
  for (const id of ["dock-timeline", "queue-strip"]) {
    const node = doc.createElement("div");
    node.id = id;
    doc.body.appendChild(node);
  }
  vi.stubGlobal("document", doc);
  return doc;
}

type Route = { path: string; method: string; body: unknown };

function stubApi(respond: (path: string, method: string) => unknown = () => ({ ok: true })): Route[] {
  const calls: Route[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: { method?: string; body?: string }) => {
      const path = String(url).replace(/^.*\/api\/v1/, "");
      const method = init?.method || "GET";
      calls.push({ path, method, body: init?.body ? JSON.parse(init.body) : undefined });
      const payload = respond(path, method);
      return {
        ok: payload !== undefined,
        status: payload === undefined ? 404 : 200,
        text: async () => JSON.stringify(payload === undefined ? { error: "missing" } : payload),
      };
    }),
  );
  return calls;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

/** requestAnimationFrame that runs only when a test says a frame has passed. */
function stubFrames(): { run: () => void; pending: () => number } {
  let queue = new Map<number, () => void>();
  let next = 1;
  vi.stubGlobal("requestAnimationFrame", (callback: () => void) => {
    const id = next++;
    queue.set(id, callback);
    return id;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => {
    queue.delete(id);
  });
  return {
    run: () => {
      const due = queue;
      queue = new Map();
      due.forEach((callback) => callback());
    },
    pending: () => queue.size,
  };
}

type Observer = { observed: unknown[]; disconnected: boolean };

function stubResizeObserver(): Observer[] {
  const made: Observer[] = [];
  vi.stubGlobal(
    "ResizeObserver",
    class {
      record: Observer = { observed: [], disconnected: false };
      constructor() {
        made.push(this.record);
      }
      observe(node: unknown) {
        this.record.observed.push(node);
      }
      disconnect() {
        this.record.disconnected = true;
      }
    },
  );
  return made;
}

function group(id: string, ordinal: number, overrides: Record<string, unknown> = {}) {
  return {
    group_id: id,
    ordinal,
    turn_id: "turn-1",
    kind: "code",
    title: "Action " + ordinal,
    status: "completed",
    attempts: [
      { attempt_ordinal: 1, allocated_at: 1000 + ordinal, started_at: 1100 + ordinal, finished_at: 1200 + ordinal },
    ],
    events: [],
    ...overrides,
  };
}

function showTimeline(frameId: string, groups: unknown[], branchId = "branch-" + frameId): void {
  S.currentId = frameId;
  S.activeTab = "timeline";
  S.actionTimeline = sanitizeActionTimeline({ root_frame_id: frameId, branch_id: branchId, groups });
}

function queued(executionId: string, preview = "follow-up") {
  return {
    execution_id: executionId,
    status: "queued",
    owner: { kind: "agent", id: "turn-" + executionId },
    queue_position: 1,
    metadata: { preview },
  };
}

beforeEach(() => {
  resetStoreFields();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("queue strip", () => {
  it("is published for the session lane, which clears the previous session's strip", () => {
    const doc = mountDocument();
    const target: Record<string, unknown> = {};
    installTimeline(target);
    expect(typeof target.renderQueueStrip).toBe("function");

    S.currentId = "frame-a";
    S.executionQueue = { owner: null, queue: [queued("exec-a")] };
    renderQueueStrip();
    const strip = doc.getElementById("queue-strip")!;
    expect(strip.querySelectorAll(".queue-row")).toHaveLength(1);

    // What openConversation does on a switch: reset session state, then
    // enableComposer(true) -> callLane("renderQueueStrip").
    S.currentId = "frame-b";
    S.executionQueue = null;
    (target.renderQueueStrip as () => void)();
    expect(strip.querySelectorAll(".queue-row")).toHaveLength(0);
    expect(strip.classList.contains("hidden")).toBe(true);
  });

  it("cancels a queued row in the session that queued it, not the one now open", async () => {
    const doc = mountDocument();
    const calls = stubApi(() => ({ ok: true }));
    vi.stubGlobal("hint", vi.fn());
    S.currentId = "frame-a";
    S.executionQueue = { owner: null, queue: [queued("exec-a")] };
    renderQueueStrip();
    S.currentId = "frame-b";
    doc.querySelector("#queue-strip .queue-cancel")!.click();
    await flush();
    expect(calls.map((call) => call.path)).toEqual(["/frames/frame-a/cancel"]);
  });

  it("sends one cancel however often the ✕ is pressed while it is pending", async () => {
    const doc = mountDocument();
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const posts: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        posts.push(String(url));
        await gate;
        return { ok: true, status: 200, text: async () => JSON.stringify({ ok: true }) };
      }),
    );
    vi.stubGlobal("hint", vi.fn());
    S.currentId = "frame-c";
    S.executionQueue = { owner: null, queue: [queued("exec-c")] };
    renderQueueStrip();
    const first = doc.querySelector("#queue-strip .queue-cancel")!;
    first.click();
    first.click();
    // A queue update re-renders the row while the request is still out.
    renderQueueStrip();
    const again = doc.querySelector("#queue-strip .queue-cancel")!;
    expect(again.disabled).toBe(true);
    (again.onclick as () => void)();
    release();
    await flush();
    await flush();
    expect(posts).toHaveLength(1);
    // Accepted: until the queue stops listing it, the row cannot be sent again.
    expect(doc.querySelector("#queue-strip .queue-cancel")!.disabled).toBe(true);
  });

  it("offers the ✕ again when the cancel was refused", async () => {
    const doc = mountDocument();
    const calls = stubApi(() => ({ ok: false, reason: "not queued" }));
    vi.stubGlobal("hint", vi.fn());
    S.currentId = "frame-d";
    S.executionQueue = { owner: null, queue: [queued("exec-d")] };
    renderQueueStrip();
    doc.querySelector("#queue-strip .queue-cancel")!.click();
    await flush();
    await flush();
    const retry = doc.querySelector("#queue-strip .queue-cancel")!;
    expect(retry.disabled).toBe(false);
    retry.click();
    await flush();
    expect(calls).toHaveLength(2);
  });
});

describe("timeline view lifetime", () => {
  it("is destroyed through the window lane after the session reset cleared S._timelineView", () => {
    const doc = mountDocument();
    stubFrames();
    const observers = stubResizeObserver();
    const target: Record<string, unknown> = {};
    installTimeline(target);
    showTimeline("frame-a", [group("g-1", 1)]);
    renderActionTimeline();
    expect(doc.listenerCount("keydown")).toBe(1);
    expect(observers).toHaveLength(1);

    // messages/open.ts: resetSessionScoped() nulls the view, then
    // callLane("destroyActionTimelineView").
    S._timelineView = null;
    expect(typeof target.destroyActionTimelineView).toBe("function");
    (target.destroyActionTimelineView as () => void)();
    expect(doc.listenerCount("keydown")).toBe(0);
    expect(observers[0]!.disconnected).toBe(true);
  });

  it("releases the previous view when a new one replaces it, even if nobody destroyed it", () => {
    const doc = mountDocument();
    stubFrames();
    const observers = stubResizeObserver();
    for (const frame of ["frame-a", "frame-b", "frame-c"]) {
      S._timelineView = null;
      showTimeline(frame, [group("g-" + frame, 1)]);
      renderActionTimeline();
    }
    expect(doc.listenerCount("keydown")).toBe(1);
    expect(observers.map((observer) => observer.disconnected)).toEqual([true, true, false]);
  });
});

describe("revert preview", () => {
  const branches = (head: string) => ({
    root_frame_id: "frame-r",
    branch_id: "main",
    capabilities: { revert: true, revert_preview: true },
    branches: [
      {
        branch_id: "main",
        head_checkpoint_id: head,
        checkpoints: [{ checkpoint_id: "cp-target" }, { checkpoint_id: "cp-head" }, { checkpoint_id: "cp-next" }],
      },
    ],
  });
  const preview = {
    branch_id: "main",
    current_checkpoint_id: "cp-head",
    target_checkpoint_id: "cp-target",
    can_apply: true,
    messages: { delta: -2 },
    notebook: { delta: -1 },
    workspace: { writes_count: 0, deletes_count: 0, conflicts_count: 0 },
    artifacts: { added_count: 0, removed_count: 0 },
  };
  const revertButton = () => renderBranchPanel().querySelector(".revert-preview button");

  function previewed(): void {
    mountDocument();
    S.currentId = "frame-r";
    S.activeTab = "notebook";
    S.branchState = { ...branches("cp-head"), revert_preview: preview };
  }

  it("survives a workbench refresh while the branch head has not moved", async () => {
    previewed();
    stubApi((path) => (path === "/frames/frame-r/branches" ? branches("cp-head") : undefined));
    await loadWorkbenchState("frame-r", true);
    expect(S.branchState.revert_preview).toEqual(preview);
    expect(revertButton()).not.toBeNull();
  });

  it("survives a branch projection pushed over the socket", () => {
    previewed();
    installTimeline({});
    onEvent({ type: "branch_state", frame_id: "frame-r", ...branches("cp-head") });
    expect(S.branchState.revert_preview).toEqual(preview);
  });

  it("is dropped once the head moved, because its diff no longer describes the branch", async () => {
    previewed();
    stubApi((path) => (path === "/frames/frame-r/branches" ? branches("cp-next") : undefined));
    await loadWorkbenchState("frame-r", true);
    expect(S.branchState.revert_preview).toBeNull();
    expect(revertButton()).toBeNull();
  });
});
