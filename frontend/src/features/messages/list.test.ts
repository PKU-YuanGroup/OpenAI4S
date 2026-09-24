import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Shell } from "../../components/dashboard/Shell";
import { setLang } from "../../i18n/runtime";
import { copyFailedText } from "../chrome/clipboard";
import { renderStored as renderOlderPage } from "../sessions/transcript";
import * as messageComponents from "./components";
import { currentId, _openGen, historyLoad } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";

vi.mock("preact/hooks", async (original) => ({
  ...await original<typeof import("preact/hooks")>(),
  useEffect: vi.fn(),
}));
import {
  INITIAL_RENDER_BATCH,
  cancelFramedRender,
  nextBatchEnd,
  renderEmptySession,
  renderStored as renderFirstPage,
  scheduleFramedRender,
} from "./list";

afterEach(() => {
  cancelFramedRender();
  vi.unstubAllGlobals();
});

describe("framed initial render batches", () => {
  it("uses 40 items per frame (inside the 30-50 window)", () => {
    expect(INITIAL_RENDER_BATCH).toBeGreaterThanOrEqual(30);
    expect(INITIAL_RENDER_BATCH).toBeLessThanOrEqual(50);
    expect(INITIAL_RENDER_BATCH).toBe(40);
  });

  it("splits a 640-row session into 16 frames", () => {
    const total = 640;
    const ends: number[] = [];
    let start = 0;
    while (start < total) {
      const end = nextBatchEnd(start, total);
      expect(end - start).toBeLessThanOrEqual(INITIAL_RENDER_BATCH);
      expect(end).toBeGreaterThan(start);
      ends.push(end);
      start = end;
    }
    expect(ends).toHaveLength(16);
    expect(ends[ends.length - 1]).toBe(640);
  });

  it("last batch may be shorter than the frame size", () => {
    expect(nextBatchEnd(280, 300)).toBe(300);
    expect(nextBatchEnd(0, 10)).toBe(10);
  });

  it("settles a framed render when a session switch cancels it", async () => {
    const frames = new Map<number, FrameRequestCallback>();
    let nextFrame = 1;
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
      const id = nextFrame++;
      frames.set(id, cb);
      return id;
    });
    vi.stubGlobal("cancelAnimationFrame", (id: number) => {
      frames.delete(id);
    });
    const onDone = vi.fn();
    const settled = new Promise<"cancelled">((resolve) => {
      scheduleFramedRender([], {
        host: { appendChild: (node: Node) => node } as unknown as ParentNode,
        onDone,
        onCancel: () => resolve("cancelled"),
      });
    });

    cancelFramedRender();

    await expect(settled).resolves.toBe("cancelled");
    expect(onDone).not.toHaveBeenCalled();
    expect(frames.size).toBe(0);
  });
});

describe("insertMessageByTime", () => {
  it("inserts before the first later timestamp and skips #msgs-earlier", async () => {
    const { insertMessageByTime } = await import("./list");
    const kids: Array<{ id: string; dataset: { ts?: string } }> = [];
    const host = {
      children: kids,
      insertBefore(node: (typeof kids)[0], ref: (typeof kids)[0]) {
        kids.splice(kids.indexOf(ref), 0, node);
        return node;
      },
      appendChild(node: (typeof kids)[0]) {
        kids.push(node);
        return node;
      },
    };
    const earlier = { id: "msgs-earlier", dataset: {} };
    const a = { id: "a", dataset: { ts: "100" } };
    const c = { id: "c", dataset: { ts: "300" } };
    kids.push(earlier, a, c);
    const b = { id: "b", dataset: { ts: "200" } };
    insertMessageByTime(
      b as unknown as HTMLElement,
      host as unknown as ParentNode,
    );
    expect(kids.map((k) => k.id)).toEqual(["msgs-earlier", "a", "b", "c"]);
  });
});


type MessageVNode = { type?: unknown; props?: Record<string, unknown> & { children?: unknown } };
function messageVNodes(value: unknown): MessageVNode[] {
  if (Array.isArray(value)) return value.flatMap(messageVNodes);
  if (!value || typeof value !== "object") return [];
  const node = value as MessageVNode;
  return [node, ...messageVNodes(node.props?.children)];
}

it("mounts the history status in the real Shell outside its imperative transcript", () => {
  resetStoreFields();
  const root = Shell();
  const nodes = messageVNodes(root);
  const messages = nodes.filter((node) => node.props?.id === "messages");
  expect(messages).toHaveLength(1);
  expect(messages[0]?.props?.class).toBe("messages");
  expect(messages[0]?.props?.children).toBeUndefined();
  const status = nodes.find((node) => typeof node.type === "function" && node.type.name === "HistoryLoadStatus");
  expect(status).toBeDefined();
  expect(status?.type).toBe((messageComponents as unknown as Record<string, unknown>).HistoryLoadStatus);
  const column = nodes.find((node) => node.props?.id === "conv-view");
  expect(column?.props?.children).toEqual(expect.arrayContaining([status, messages[0]]));
  expect(nodes.filter((node) => node.props?.id === "jump-pill")).toHaveLength(1);
});

it("shows scoped read errors and a retry button, hiding settled or obsolete history state", () => {
  resetStoreFields(); currentId.value = "f"; _openGen.value = 3;
  historyLoad.value = {
    fid: "f", generation: 3, status: "partial", messagesLoaded: true,
    stepsLoaded: false, runStateLoaded: true, superseded: false,
    errors: { steps: "HTTP 503" }, deferred: false,
  };
  const Status = (messageComponents as unknown as Record<string, unknown>).HistoryLoadStatus as (() => unknown);
  expect(Status).toBeTypeOf("function");
  const visible = messageVNodes(Status());
  expect(visible[0]?.props).toMatchObject({ role: "status", "aria-live": "polite", "data-history-state": "partial" });
  expect(visible.filter((node) => node.type === "button")).toHaveLength(1);
  expect(visible.some((node) => JSON.stringify(node.props?.children).includes("HTTP 503"))).toBe(true);
  historyLoad.value = { ...historyLoad.value, status: "loaded" };
  expect(Status()).toBeNull();
  historyLoad.value = { ...historyLoad.value, status: "error", generation: 2 };
  expect(Status()).toBeNull();
  historyLoad.value = { ...historyLoad.value, fid: "g", generation: 3 };
  expect(Status()).toBeNull();
});

/** Just enough DOM for one stored row and its action buttons (no jsdom here). */
class RowClassList {
  readonly tokens = new Set<string>();
  add(...names: string[]): void {
    for (const n of names) this.tokens.add(n);
  }
  remove(...names: string[]): void {
    for (const n of names) this.tokens.delete(n);
  }
  contains(name: string): boolean {
    return this.tokens.has(name);
  }
  toggle(name: string, force?: boolean): boolean {
    const on = force === undefined ? !this.tokens.has(name) : force;
    if (on) this.tokens.add(name);
    else this.tokens.delete(name);
    return on;
  }
}

class RowEl {
  tagName: string;
  id = "";
  title = "";
  type = "";
  value = "";
  classList = new RowClassList();
  children: RowEl[] = [];
  parentNode: RowEl | null = null;
  dataset: Record<string, string> = {};
  style: Record<string, string> = {};
  attrs: Record<string, string> = {};
  onclick: (() => unknown) | null = null;
  scrollHeight = 64;
  focused = 0;
  private text = "";
  constructor(tag: string) {
    this.tagName = tag.toUpperCase();
  }
  get className(): string {
    return [...this.classList.tokens].join(" ");
  }
  set className(value: string) {
    this.classList = new RowClassList();
    for (const t of String(value).split(/\s+/).filter(Boolean)) this.classList.add(t);
  }
  get textContent(): string {
    return this.children.length ? this.children.map((c) => c.textContent).join("") : this.text;
  }
  set textContent(value: string) {
    this.children = [];
    this.text = value == null ? "" : String(value);
  }
  get innerHTML(): string {
    return this.text;
  }
  set innerHTML(value: string) {
    this.children = [];
    this.text = String(value).replace(/<[^>]*>/g, "");
  }
  get firstChild(): RowEl | null {
    return this.children[0] ?? null;
  }
  setAttribute(name: string, value: string): void {
    this.attrs[name] = String(value);
  }
  getAttribute(name: string): string | null {
    return this.attrs[name] ?? null;
  }
  appendChild<T extends RowEl>(child: T): T {
    child.parentNode?.removeChild(child);
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  insertBefore<T extends RowEl>(child: T, ref: RowEl | null): T {
    if (!ref) return this.appendChild(child);
    child.parentNode?.removeChild(child);
    child.parentNode = this;
    this.children.splice(this.children.indexOf(ref), 0, child);
    return child;
  }
  removeChild(child: RowEl): void {
    this.children = this.children.filter((c) => c !== child);
    child.parentNode = null;
  }
  remove(): void {
    this.parentNode?.removeChild(this);
  }
  focus(): void {
    this.focused += 1;
  }
  querySelector(sel: string): RowEl | null {
    return this.querySelectorAll(sel)[0] ?? null;
  }
  querySelectorAll(sel: string): RowEl[] {
    const direct = sel.startsWith(":scope > ");
    const want = direct ? sel.slice(":scope > ".length) : sel;
    const out: RowEl[] = [];
    const walk = (node: RowEl): void => {
      for (const child of node.children) {
        if (rowMatches(child, want)) out.push(child);
        if (!direct) walk(child);
      }
    };
    walk(this);
    return out;
  }
}

function rowMatches(node: RowEl, sel: string): boolean {
  if (sel.startsWith("#")) return node.id === sel.slice(1);
  if (sel.startsWith(".")) return sel.slice(1).split(".").every((c) => node.classList.contains(c));
  return node.tagName === sel.toUpperCase();
}

class RowDoc {
  body = new RowEl("body");
  messages = new RowEl("div");
  composer = new RowEl("textarea");
  hint = new RowEl("div");
  constructor() {
    this.messages.id = "messages";
    this.composer.id = "composer";
    this.hint.id = "composer-hint";
    this.body.appendChild(this.messages);
    this.body.appendChild(this.composer);
    this.body.appendChild(this.hint);
  }
  createElement(tag: string): RowEl {
    return new RowEl(tag);
  }
  createTextNode(text: string): RowEl {
    const node = new RowEl("#text");
    node.textContent = text;
    return node;
  }
  getElementById(id: string): RowEl | null {
    return this.body.querySelector("#" + id);
  }
  querySelector(sel: string): RowEl | null {
    return this.body.querySelector(sel);
  }
  querySelectorAll(sel: string): RowEl[] {
    return this.body.querySelectorAll(sel);
  }
}

/** The two stored-row entry points: the first page and "load earlier". */
type RowRenderer = (m: Record<string, unknown>) => HTMLElement | null;
const ROW_RENDERERS: ReadonlyArray<readonly [string, RowRenderer]> = [
  ["first page", renderFirstPage as RowRenderer],
  ["older page", renderOlderPage as RowRenderer],
];

describe("stored rows, first page and older page alike", () => {
  let doc: RowDoc;
  beforeEach(async () => {
    await setLang("en");
    resetStoreFields();
    doc = new RowDoc();
    vi.stubGlobal("document", doc);
  });

  it.each(ROW_RENDERERS)("%s: a reviewed answer keeps its badge and candidate identity", (_name, render) => {
    const row = render({
      role: "assistant",
      content: "The fit converged.",
      message_id: "msg-7",
      turn_id: "turn-7",
      review_status: { status: "verified", user_truth: "" },
    }) as unknown as RowEl;
    const badge = row.querySelector(":scope > .review-badge");
    expect(badge?.classList.contains("review-badge-verified")).toBe(true);
    expect(row.dataset.reviewStatus).toBe("verified");
    expect(row.dataset.candidateResolved).toBe("true");
    // A later candidate_resolved / review event finds the row by identity.
    expect(row.dataset.messageId).toBe("msg-7");
    expect(row.dataset.turnId).toBe("turn-7");
    expect(doc.messages.children).toContain(row);
  });

  it.each(ROW_RENDERERS)("%s: 👍/👎 post the rating, toggle, and show a saved one", (_name, render) => {
    currentId.value = "frame-1";
    const posts: Array<{ url: string; body: { key?: string; rating?: unknown } }> = [];
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
      posts.push({ url: String(url), body: JSON.parse(String(init?.body || "{}")) });
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve("{}") });
    });
    const row = render({ role: "assistant", content: "Answer A." }) as unknown as RowEl;
    const [, up, down] = row.querySelector(".msg-actions")!.children as [RowEl, RowEl, RowEl];
    expect(up.onclick).toBeTypeOf("function");
    expect(down.onclick).toBeTypeOf("function");

    down.onclick!();
    expect(down.classList.contains("on")).toBe(true);
    expect(up.classList.contains("on")).toBe(false);
    expect(posts.at(-1)?.url).toBe("/api/v1/frames/frame-1/feedback");
    expect(posts.at(-1)?.body.rating).toBe("down");
    const key = posts.at(-1)?.body.key;
    expect(key).toBeTruthy();

    up.onclick!();
    expect(up.classList.contains("on")).toBe(true);
    expect(down.classList.contains("on")).toBe(false);
    expect(posts.at(-1)?.body).toEqual({ key, rating: "up" });

    // The saved rating is what a reopened answer shows.
    const again = render({ role: "assistant", content: "Answer A." }) as unknown as RowEl;
    const [, savedUp, savedDown] = again.querySelector(".msg-actions")!.children as [RowEl, RowEl, RowEl];
    expect(savedUp.classList.contains("on")).toBe(true);
    expect(savedDown.classList.contains("on")).toBe(false);

    // Clicking the active one withdraws it.
    savedUp.onclick!();
    expect(savedUp.classList.contains("on")).toBe(false);
    expect(posts.at(-1)?.body).toEqual({ key, rating: null });
  });

  it.each(ROW_RENDERERS)("%s: Copy ticks only for a confirmed clipboard write", async (_name, render) => {
    const writeText = vi.fn(() => Promise.resolve());
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    const row = render({ role: "assistant", content: "Copy me." }) as unknown as RowEl;
    const copy = row.querySelector(".msg-actions")!.children[0]!;
    expect(copy.attrs["data-icon"]).toBe("copy");
    await copy.onclick!();
    expect(writeText).toHaveBeenCalledWith("Copy me.");
    expect(copy.attrs["data-icon"]).toBe("check");

    // Refused (a permission prompt, or plain-http LAN with no clipboard API
    // and no selection fallback): no tick, and the failure is said.
    vi.stubGlobal("navigator", { clipboard: { writeText: () => Promise.reject(new Error("denied")) } });
    const refused = render({ role: "assistant", content: "Copy me too." }) as unknown as RowEl;
    const refusedCopy = refused.querySelector(".msg-actions")!.children[0]!;
    await refusedCopy.onclick!();
    expect(refusedCopy.attrs["data-icon"]).toBe("copy");
    expect(doc.hint.textContent).toContain(copyFailedText());
  });

  it.each(ROW_RENDERERS)("%s: Edit fills the composer and grows it to fit", (_name, render) => {
    const row = render({ role: "assistant", content: "Edit me." }) as unknown as RowEl;
    const edit = row.querySelector(".msg-actions")!.children[3]!;
    edit.onclick!();
    expect(doc.composer.value).toBe("Edit me.");
    expect(doc.composer.style.height).toBe("64px");
    expect(doc.composer.focused).toBe(1);
  });

  it("a starter chip fills the composer and grows it to fit", () => {
    renderEmptySession();
    const chip = doc.messages.querySelector(".es-chip")!;
    chip.onclick!();
    expect(doc.composer.value).toBe(doc.messages.querySelector(".es-chip-p")!.textContent);
    expect(doc.composer.value).not.toBe("");
    expect(doc.composer.style.height).toBe("64px");
    expect(doc.composer.focused).toBe(1);
  });
});
