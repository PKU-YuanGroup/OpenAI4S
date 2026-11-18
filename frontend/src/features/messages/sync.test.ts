/**
 * One transcript across tabs, reloads and reconnects.
 *
 * A usability review held one session open in five tabs and ended a long turn
 * with five different transcripts: the question existed only in the tab that
 * sent it, approval decisions vanished on reload, a reconnect appended the
 * whole narration a second time, and reopening a running session regrouped it.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLang } from "../../i18n/runtime";
import { currentId } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { _replayGap, _seqSeen, _streamEpoch, permCards, stream } from "../../stores/stream";
import { installSend } from "../send";
import { resetSendHandlers } from "../send/handlers";
import { newRequestId } from "../send/send";
import { renderStoredPermission } from "../send/permission";
import { handleIncomingMessage } from "../ws/connect";
import { registerBuiltinHandlers } from "../ws/handlers";
import { onEvent, resetWsHandlers } from "../ws/registry";
import { registerMessageHandlers } from "./handlers";
import { interleaveHistory, renderStored } from "./list";
import type { LiveStream } from "./stream";

// --------------------------------------------------------------------------
// Just enough DOM for the transcript (Vitest runs in node, with no jsdom).
// --------------------------------------------------------------------------

type Compound = { tag: string; id: string; classes: string[] };
type Step = { combinator: " " | ">"; compound: Compound };

function parseCompound(text: string): Compound {
  const tag = (text.match(/^[a-zA-Z][\w-]*/) || [""])[0].toUpperCase();
  const id = (text.match(/#([\w-]+)/) || ["", ""])[1] || "";
  const classes = [...text.matchAll(/\.([\w-]+)/g)].map((m) => m[1] || "");
  return { tag, id, classes };
}

function parseSelector(selector: string): { scoped: boolean; steps: Step[] } {
  const tokens = selector.trim().replace(/>/g, " > ").split(/\s+/).filter(Boolean);
  let scoped = false;
  if (tokens[0] === ":scope") {
    scoped = true;
    tokens.shift();
  }
  const steps: Step[] = [];
  let combinator: " " | ">" = " ";
  for (const token of tokens) {
    if (token === ">") {
      combinator = ">";
      continue;
    }
    steps.push({ combinator, compound: parseCompound(token) });
    combinator = " ";
  }
  return { scoped, steps };
}

class FakeText {
  parentNode: FakeEl | null = null;
  constructor(public data: string) {}
  get textContent(): string {
    return this.data;
  }
  appendData(more: string): void {
    this.data += more;
  }
  remove(): void {
    this.parentNode?.removeChild(this);
  }
}

type FakeNode = FakeEl | FakeText;

class FakeEl {
  parentNode: FakeEl | null = null;
  childNodes: FakeNode[] = [];
  id = "";
  dataset: Record<string, string> = {};
  style: Record<string, string> = {};
  title = "";
  type = "";
  value = "";
  placeholder = "";
  disabled = false;
  hidden = false;
  scrollTop = 0;
  scrollHeight = 0;
  clientHeight = 0;
  onclick: unknown = null;
  private tokens = new Set<string>();
  private text = "";
  private attrs = new Map<string, string>();
  readonly classList = {
    add: (...names: string[]) => names.forEach((name) => this.tokens.add(name)),
    remove: (...names: string[]) => names.forEach((name) => this.tokens.delete(name)),
    contains: (name: string) => this.tokens.has(name),
    toggle: (name: string, force?: boolean) => {
      const on = force === undefined ? !this.tokens.has(name) : force;
      if (on) this.tokens.add(name);
      else this.tokens.delete(name);
      return on;
    },
  };
  constructor(public tagName: string) {
    this.tagName = tagName.toUpperCase();
  }
  get className(): string {
    return [...this.tokens].join(" ");
  }
  set className(value: string) {
    this.tokens = new Set(String(value).split(/\s+/).filter(Boolean));
  }
  get children(): FakeEl[] {
    return this.childNodes.filter((node): node is FakeEl => node instanceof FakeEl);
  }
  get parentElement(): FakeEl | null {
    return this.parentNode;
  }
  get firstChild(): FakeNode | null {
    return this.childNodes[0] || null;
  }
  get lastElementChild(): FakeEl | null {
    const kids = this.children;
    return kids[kids.length - 1] || null;
  }
  get textContent(): string {
    return this.childNodes.length ? this.childNodes.map((node) => node.textContent).join("") : this.text;
  }
  set textContent(value: string) {
    this.childNodes.forEach((node) => (node.parentNode = null));
    this.childNodes = [];
    this.text = value == null ? "" : String(value);
  }
  get innerHTML(): string {
    return this.text;
  }
  set innerHTML(value: string) {
    this.textContent = String(value).replace(/<[^>]*>/g, "");
  }
  get isConnected(): boolean {
    let node: FakeEl | null = this;
    while (node.parentNode) node = node.parentNode;
    return node === activeDoc?.documentElement;
  }
  setAttribute(name: string, value: string): void {
    if (name === "class") this.className = value;
    else if (name === "id") this.id = value;
    else this.attrs.set(name, String(value));
  }
  getAttribute(name: string): string | null {
    if (name === "class") return this.className;
    if (name === "id") return this.id || null;
    return this.attrs.has(name) ? (this.attrs.get(name) as string) : null;
  }
  hasAttribute(name: string): boolean {
    return this.getAttribute(name) !== null;
  }
  removeAttribute(name: string): void {
    this.attrs.delete(name);
  }
  private adopt(node: FakeNode): FakeNode[] {
    if (node instanceof FakeEl && node.tagName === "#FRAGMENT") {
      const moved = [...node.childNodes];
      node.childNodes = [];
      return moved;
    }
    node.parentNode?.removeChild(node);
    return [node];
  }
  appendChild<T extends FakeNode>(node: T): T {
    for (const child of this.adopt(node)) {
      child.parentNode = this;
      this.childNodes.push(child);
    }
    return node;
  }
  insertBefore<T extends FakeNode>(node: T, ref: FakeNode | null): T {
    if (!ref) return this.appendChild(node);
    const moved = this.adopt(node);
    const at = this.childNodes.indexOf(ref);
    moved.forEach((child) => (child.parentNode = this));
    this.childNodes.splice(at < 0 ? this.childNodes.length : at, 0, ...moved);
    return node;
  }
  removeChild<T extends FakeNode>(node: T): T {
    this.childNodes = this.childNodes.filter((child) => child !== node);
    node.parentNode = null;
    return node;
  }
  remove(): void {
    this.parentNode?.removeChild(this);
  }
  after(...nodes: FakeNode[]): void {
    const parent = this.parentNode;
    if (!parent) return;
    let ref: FakeNode | null = parent.childNodes[parent.childNodes.indexOf(this) + 1] || null;
    for (const node of nodes) {
      parent.insertBefore(node, ref);
      ref = parent.childNodes[parent.childNodes.indexOf(node) + 1] || null;
    }
  }
  replaceChildren(...nodes: FakeNode[]): void {
    this.textContent = "";
    nodes.forEach((node) => this.appendChild(node));
  }
  contains(node: FakeNode | null): boolean {
    let cur: FakeNode | FakeEl | null = node;
    while (cur) {
      if (cur === this) return true;
      cur = cur.parentNode;
    }
    return false;
  }
  focus(): void {}
  scrollIntoView(): void {}
  addEventListener(): void {}
  removeEventListener(): void {}
  getBoundingClientRect(): { top: number; bottom: number; left: number; right: number; height: number; width: number } {
    return { top: 0, bottom: 0, left: 0, right: 0, height: 0, width: 0 };
  }
  private matchesCompound(compound: Compound): boolean {
    if (compound.tag && compound.tag !== this.tagName) return false;
    if (compound.id && compound.id !== this.id) return false;
    return compound.classes.every((name) => this.tokens.has(name));
  }
  private matchesSteps(steps: Step[], scope: FakeEl | null): boolean {
    let index = steps.length - 1;
    const last = steps[index];
    if (!last || !this.matchesCompound(last.compound)) return false;
    let node: FakeEl | null = this;
    while (index > 0) {
      const combinator: " " | ">" = (steps[index] as Step).combinator;
      const want = (steps[index - 1] as Step).compound;
      if (combinator === ">") {
        node = node.parentNode;
        if (!node || node === scope || !node.matchesCompound(want)) return false;
      } else {
        node = node.parentNode;
        while (node && node !== scope && !node.matchesCompound(want)) node = node.parentNode;
        if (!node || node === scope) return false;
      }
      index -= 1;
    }
    if (scope) {
      const first = steps[0] as Step;
      return first.combinator === ">" ? node.parentNode === scope : scope.contains(node) && node !== scope;
    }
    return true;
  }
  matches(selector: string): boolean {
    return selector.split(",").some((part) => this.matchesSteps(parseSelector(part).steps, null));
  }
  closest(selector: string): FakeEl | null {
    let node: FakeEl | null = this;
    while (node && !node.matches(selector)) node = node.parentNode;
    return node;
  }
  querySelectorAll(selector: string): FakeEl[] {
    const parts = selector.split(",").map(parseSelector);
    const out: FakeEl[] = [];
    const walk = (node: FakeEl): void => {
      for (const child of node.children) {
        if (parts.some((part) => child.matchesSteps(part.steps, part.scoped ? this : null))) out.push(child);
        walk(child);
      }
    };
    walk(this);
    return out;
  }
  querySelector(selector: string): FakeEl | null {
    return this.querySelectorAll(selector)[0] || null;
  }
}

class FakeDoc {
  documentElement = new FakeEl("html");
  body = new FakeEl("body");
  constructor() {
    this.documentElement.appendChild(this.body);
  }
  createElement(tag: string): FakeEl {
    return new FakeEl(tag);
  }
  createTextNode(data: string): FakeText {
    return new FakeText(data);
  }
  createDocumentFragment(): FakeEl {
    return new FakeEl("#fragment");
  }
  getElementById(id: string): FakeEl | null {
    return this.documentElement.querySelector("#" + id);
  }
  querySelector(selector: string): FakeEl | null {
    return this.documentElement.querySelector(selector);
  }
  querySelectorAll(selector: string): FakeEl[] {
    return this.documentElement.querySelectorAll(selector);
  }
  addEventListener(): void {}
  removeEventListener(): void {}
}

let activeDoc: FakeDoc | null = null;

function mount(id: string): FakeEl {
  const node = (activeDoc as FakeDoc).createElement("div");
  node.id = id;
  (activeDoc as FakeDoc).body.appendChild(node);
  return node;
}

function messages(): HTMLElement {
  return document.getElementById("messages") as HTMLElement;
}

function bubble(text: string, className = "msg user"): HTMLElement {
  const row = document.createElement("div");
  row.className = className;
  const inner = document.createElement("div");
  inner.className = "bubble";
  inner.textContent = text;
  row.appendChild(inner);
  return row;
}

function userBubbles(): string[] {
  return Array.from(messages().querySelectorAll(":scope > .msg.user .bubble")).map(
    (node) => node.textContent || "",
  );
}

beforeEach(async () => {
  await setLang("en");
  resetStoreFields();
  resetWsHandlers();
  resetSendHandlers();
  registerBuiltinHandlers();
  registerMessageHandlers();
  installSend({});
  activeDoc = new FakeDoc();
  vi.stubGlobal("document", activeDoc);
  mount("messages");
  mount("composer-hint");
  currentId.value = "f-1";
});

afterEach(() => {
  resetWsHandlers();
  vi.unstubAllGlobals();
  activeDoc = null;
});

describe("the user's message reaches every tab", () => {
  it("shows another tab's question where its turn starts", () => {
    const welcome = document.createElement("div");
    welcome.className = "empty-session";
    messages().appendChild(welcome);
    onEvent({
      type: "user_message",
      root_frame_id: "f-1",
      frame_id: "f-1",
      message_id: "m-1",
      content: "Compute the fold change",
      created_at: "2026-11-18T04:06:00.000Z",
      request_id: "web-other-tab",
      seq: 2,
    });
    expect(userBubbles()).toEqual(["Compute the fold change"]);
    const row = messages().querySelector(".msg.user") as HTMLElement;
    expect(row.dataset.messageId).toBe("m-1");
    // A session that now has a question is no longer the empty welcome.
    expect(messages().querySelector(".empty-session")).toBeNull();
  });

  it("adopts the sending tab's own bubble instead of adding a second one", () => {
    const requestId = newRequestId();
    expect(requestId).toMatch(/^web-[0-9a-f]{24}$/);
    const own = bubble("Queued follow-up", "msg user queued");
    own.dataset.requestId = requestId;
    messages().appendChild(own);
    onEvent({
      type: "user_message",
      root_frame_id: "f-1",
      message_id: "m-2",
      content: "Queued follow-up",
      request_id: requestId,
      seq: 7,
    });
    expect(userBubbles()).toEqual(["Queued follow-up"]);
    expect(own.dataset.messageId).toBe("m-2");
    // Its turn runs now: no longer drawn as waiting in the queue.
    expect(own.classList.contains("queued")).toBe(false);
  });

  it("keeps the row a reload already rendered from REST", () => {
    renderStored({ role: "user", content: "Stored question", message_id: "m-3" });
    onEvent({
      type: "user_message",
      root_frame_id: "f-1",
      message_id: "m-3",
      content: "Stored question",
      request_id: "web-x",
      seq: 2,
    });
    expect(userBubbles()).toEqual(["Stored question"]);
  });

  it("ignores another session's question", () => {
    onEvent({ type: "user_message", root_frame_id: "f-2", message_id: "m-9", content: "x", seq: 1 });
    expect(userBubbles()).toEqual([]);
  });
});

describe("approval decisions are history", () => {
  it("interleaves stored approvals with messages and steps by time", () => {
    const items = interleaveHistory(
      [
        { role: "user", content: "q", created_at: "2026-11-18T00:00:00.000Z" },
        { role: "assistant", content: "a", created_at: "2026-11-18T00:00:03.000Z" },
      ],
      [{ step_id: "s-1", created_at: Date.parse("2026-11-18T00:00:01.000Z"), seq: 1 }],
      [{ decision_id: "perm-1", created_at: "2026-11-18T00:00:01.000Z" }],
    );
    expect(items.map((item) => item.kind)).toEqual(["msg", "step", "perm", "msg"]);
  });

  it("renders a stored denial with its reason, and keeps a pending one answerable", () => {
    permCards.value = Object.create(null);
    renderStoredPermission({
      decision_id: "perm-denied",
      frame_id: "f-1",
      tool: "bash",
      title: "Run command",
      input: { command: "rm -rf scratch" },
      state: "denied",
      scope: "once",
      message: "not on the shared drive",
      created_at: "2026-11-18T00:00:01.000Z",
    });
    renderStoredPermission({
      decision_id: "perm-late",
      frame_id: "f-1",
      tool: "bash",
      title: "Run command",
      state: "timed_out",
      created_at: "2026-11-18T00:00:02.000Z",
    });
    renderStoredPermission({
      decision_id: "perm-open",
      frame_id: "f-1",
      tool: "bash",
      title: "Run command",
      state: "pending",
      created_at: "2026-11-18T00:00:03.000Z",
    });
    const cards = Array.from(messages().querySelectorAll(".perm-card")) as HTMLElement[];
    expect(cards.map((card) => card.dataset.decisionId)).toEqual(["perm-denied", "perm-late", "perm-open"]);
    const [denied, late, open] = cards;
    expect(denied?.classList.contains("resolved")).toBe(true);
    expect(denied?.classList.contains("denied")).toBe(true);
    expect(denied?.querySelector(".perm-status")?.textContent).toBe("Denied");
    expect(denied?.querySelector(".perm-reason")?.textContent).toBe("Reason: not on the shared drive");
    expect(late?.querySelector(".perm-status")?.textContent).toBe("Timed out · not run");
    expect(open?.classList.contains("resolved")).toBe(false);
    expect((open?.querySelector(".perm-allow") as HTMLButtonElement).disabled).toBe(false);
    // The registry knows the history cards, so a later live event resolves one.
    onEvent({ type: "permission_resolved", root_frame_id: "f-1", decision_id: "perm-open", allow: true, scope: "once" });
    expect(open?.classList.contains("allowed")).toBe(true);
  });
});

// --------------------------------------------------------------------------
// SYNC-2: a reconnect resumes; it does not re-append
// --------------------------------------------------------------------------

function wire(event: Record<string, unknown>): void {
  handleIncomingMessage(JSON.stringify({ root_frame_id: "f-1", frame_id: "f-1", ...event }));
}

function narration(): string {
  return (stream.value as LiveStream | null)?.full || "";
}

function json(body: unknown, status = 200): Response {
  return { ok: status < 400, status, text: async () => JSON.stringify(body) } as Response;
}

describe("a reconnect after a gap", () => {
  it("keeps its cursor, so a replay of what is on screen is dropped", () => {
    _streamEpoch.value = "epoch-1";
    wire({ type: "text_reset", seq: 7 });
    wire({ type: "text_chunk", block_type: "text", chunk: "Reading the plate. ", seq: 8 });
    wire({ type: "text_chunk", block_type: "text", chunk: "Fitting. ", seq: 9 });
    expect(_seqSeen.value["f-1"]).toBe(9);

    // The first reconnect is declared a gap with nothing to replay: an
    // approval decided meanwhile (seq 10) was never buffered.
    wire({ type: "replay_begin", from_seq: 9, to_seq: 9, epoch: "epoch-1", gap: true });
    wire({ type: "replay_end", to_seq: 9 });
    expect(_replayGap.value).toBe("f-1");
    // Still the dedupe line: everything up to it is on screen.
    expect(_seqSeen.value["f-1"]).toBe(9);

    // A cursor reset to 0 made the next reconnect ask for the whole buffer,
    // and the server re-sent the turn from its text_reset. On screen once.
    wire({ type: "replay_begin", from_seq: 7, to_seq: 11, epoch: "epoch-1", gap: false });
    wire({ type: "text_reset", seq: 7 });
    wire({ type: "text_chunk", block_type: "text", chunk: "Reading the plate. ", seq: 8 });
    wire({ type: "text_chunk", block_type: "text", chunk: "Fitting. ", seq: 9 });
    wire({ type: "text_chunk", block_type: "text", chunk: "Plotting. ", seq: 11 });
    wire({ type: "replay_end", to_seq: 11 });
    expect(narration()).toBe("Reading the plate. Fitting. Plotting. ");
    expect(messages().querySelectorAll(":scope > .msg.assistant")).toHaveLength(1);
  });

  it("drops every cursor when the daemon restarted (a new epoch)", () => {
    _streamEpoch.value = "epoch-1";
    wire({ type: "text_chunk", block_type: "text", chunk: "old", seq: 40 });
    wire({ type: "replay_begin", from_seq: 40, to_seq: 40, epoch: "epoch-2", gap: true });
    wire({ type: "replay_end", to_seq: 40 });
    wire({ type: "text_chunk", block_type: "text", chunk: " new", seq: 1 });
    expect(narration()).toBe("old new");
  });

  it("settles an approval decided in another tab while this one was away", async () => {
    permCards.value = Object.create(null);
    renderStoredPermission({ decision_id: "perm-1", frame_id: "f-1", tool: "bash", title: "Run", state: "pending" });
    const fetcher = vi.fn(async (input: unknown) =>
      String(input).endsWith("/frames/f-1/approvals")
        ? json({ approvals: [{ decision_id: "perm-1", state: "allowed", scope: "once" }] })
        : json({}, 404),
    );
    vi.stubGlobal("fetch", fetcher);
    wire({ type: "replay_begin", from_seq: 0, to_seq: 0, epoch: "epoch-1", gap: false });
    wire({ type: "replay_end", to_seq: 0 });
    const card = messages().querySelector(".perm-card") as HTMLElement;
    await vi.waitFor(() => expect(card.classList.contains("allowed")).toBe(true));
    expect(card.querySelector(".perm-status")?.textContent).toBe("Allowed");
  });

  it("an Allow on a card decided elsewhere shows that decision instead of an error", async () => {
    permCards.value = Object.create(null);
    renderStoredPermission({ decision_id: "perm-2", frame_id: "f-1", tool: "bash", title: "Run", state: "pending" });
    const card = messages().querySelector(".perm-card") as HTMLElement;
    vi.stubGlobal("fetch", vi.fn(async (input: unknown) => {
      const path = String(input);
      if (path.endsWith("/decision")) {
        return json({ ok: false, error: "decision is already denied", code: "decision_already_resolved" }, 409);
      }
      return json({ approvals: [{ decision_id: "perm-2", state: "denied", scope: "once", message: "use the copy" }] });
    }));
    (card.querySelector(".perm-allow") as unknown as { onclick: () => void }).onclick();
    await vi.waitFor(() => expect(card.classList.contains("denied")).toBe(true));
    expect(card.querySelector(".perm-reason")?.textContent).toBe("Reason: use the copy");
    expect(document.getElementById("composer-hint")?.textContent).toContain("already decided elsewhere");
  });
});
