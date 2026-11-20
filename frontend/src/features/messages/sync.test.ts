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
import { renderStoredStep } from "../send/step";
import { handleIncomingMessage } from "../ws/connect";
import { registerBuiltinHandlers } from "../ws/handlers";
import { onEvent, resetWsHandlers } from "../ws/registry";
import { registerMessageHandlers } from "./handlers";
import {
  interleaveHistory,
  prependHistory,
  renderMissingSession,
  renderStored,
  scheduleFramedRender,
  type HistoryItem,
} from "./list";
import { cancelFrame } from "./raf";
import { flushScrollNow, unbindMessageScroll, updateJumpPill } from "./scroll";
import type { LiveStream } from "./stream";
import { turnTextKey } from "./turnGroup";
import { turnDone } from "../send/turn";
import { _messagesFollow } from "../../stores/ui";

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
    // The fake's text view of rendered markup: the characters outside tags.
    // A scan, not a tag-stripping regex, which reads as an HTML sanitizer.
    let text = "";
    let inTag = false;
    for (const ch of String(value)) {
      if (ch === "<") inTag = true;
      else if (ch === ">" && inTag) inTag = false;
      else if (!inTag) text += ch;
    }
    this.textContent = text;
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
  // A streamed chunk schedules its markdown flush on a real 16 ms timer
  // (raf.ts falls back to setTimeout under node). Cancel it while the fake
  // document still exists: on a runner slow enough to reach it (CI, not a
  // laptop) it fired after the stub was gone, as "document is not defined".
  const live = stream.value as LiveStream | null;
  if (live && live._raf) {
    cancelFrame(live._raf);
    live._raf = null;
  }
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

// --------------------------------------------------------------------------
// SYNC-3: a reopened running session keeps the live order
// --------------------------------------------------------------------------

/** The live turn's children as kinds: prose segments, steps, approvals. */
function turnOutline(): string[] {
  const wrap = messages().querySelector(":scope > .msg.assistant") as HTMLElement | null;
  if (!wrap) return [];
  return Array.from(wrap.children).map((node) => {
    const el = node as HTMLElement;
    if (el.classList.contains("md")) return "md:" + (el.textContent || "").trim();
    if (el.classList.contains("step")) return "step";
    if (el.classList.contains("perm-card")) return "perm:" + (el.dataset.decisionId || "");
    return el.className;
  });
}

function chunk(text: string): Record<string, unknown> {
  return { type: "text_chunk", root_frame_id: "f-1", block_type: "text", chunk: text };
}

describe("reopening a running session", () => {
  it("places the stored steps back between the prose they came between", () => {
    // What a reload's REST read renders: the question and the turn's steps
    // so far (its prose is stored only when the turn ends).
    renderStored({ role: "user", content: "Fold change?", message_id: "m-1" });
    renderStoredStep({ step_id: "s-1", kind: "read", title: "Reading plate.csv", status: "done", created_at: 1 });
    renderStoredStep({ step_id: "s-2", kind: "write", title: "Writing fit.py", status: "running", created_at: 2 });
    // ...then the turn's replay, from its text_reset.
    onEvent({ type: "text_reset", root_frame_id: "f-1" });
    onEvent(chunk("Reading the plate first.\n\n"));
    onEvent({ type: "step", root_frame_id: "f-1", step_id: "s-1", kind: "read", title: "Reading plate.csv", status: "running" });
    onEvent({ type: "step_update", root_frame_id: "f-1", step_id: "s-1", status: "done" });
    onEvent(chunk("Now the fit.\n\n"));
    onEvent({ type: "step", root_frame_id: "f-1", step_id: "s-2", kind: "write", title: "Writing fit.py", status: "running" });
    onEvent(chunk("Running it."));

    const top = Array.from(messages().children).map((node) => (node as HTMLElement).className);
    expect(top).toEqual(["msg user", "msg assistant"]);
    expect(turnOutline()).toEqual([
      "md:Reading the plate first.",
      "step",
      "md:Now the fit.",
      "step",
      "md:",
    ]);
    expect((stream.value as LiveStream).text).toBe("Running it.");
    expect(messages().querySelectorAll(".step")).toHaveLength(2);
  });

  it("splits the prose at a live approval the way a step does", () => {
    onEvent({ type: "text_reset", root_frame_id: "f-1" });
    onEvent(chunk("I need to run a command.\n\n"));
    onEvent({ type: "await_permission", root_frame_id: "f-1", frame_id: "f-1", decision_id: "perm-a", tool: "bash", title: "Run" });
    onEvent(chunk("Approved, continuing."));
    expect(turnOutline()).toEqual(["md:I need to run a command.", "perm:perm-a", "md:"]);
    expect((stream.value as LiveStream).text).toBe("Approved, continuing.");
  });

  it("shows another tab's denial with the reason typed there", () => {
    onEvent({ type: "text_reset", root_frame_id: "f-1" });
    onEvent({ type: "await_permission", root_frame_id: "f-1", frame_id: "f-1", decision_id: "perm-c", tool: "bash", title: "Run" });
    onEvent({
      type: "permission_resolved",
      root_frame_id: "f-1",
      decision_id: "perm-c",
      allow: false,
      scope: "once",
      state: "denied",
      message: "use the scratch copy",
    });
    const card = messages().querySelector(".perm-card") as HTMLElement;
    expect(card.classList.contains("denied")).toBe(true);
    expect(card.querySelector(".perm-reason")?.textContent).toBe("Reason: use the scratch copy");
  });

  it("moves a stored approval into the turn when its event replays", () => {
    permCards.value = Object.create(null);
    renderStored({ role: "user", content: "Go", message_id: "m-1" });
    renderStoredPermission({ decision_id: "perm-b", frame_id: "f-1", tool: "bash", title: "Run", state: "allowed", scope: "once" });
    onEvent({ type: "text_reset", root_frame_id: "f-1" });
    onEvent(chunk("Asking first.\n\n"));
    onEvent({ type: "await_permission", root_frame_id: "f-1", frame_id: "f-1", decision_id: "perm-b", tool: "bash", title: "Run", seq: 3 });
    expect(turnOutline()).toEqual(["md:Asking first.", "perm:perm-b", "md:"]);
    // Still one card, still showing its stored decision.
    const cards = messages().querySelectorAll(".perm-card");
    expect(cards).toHaveLength(1);
    expect((cards[0] as HTMLElement).classList.contains("allowed")).toBe(true);
  });
});

// --------------------------------------------------------------------------
// CHAT-2: one turn is one container, live and reopened
// --------------------------------------------------------------------------

/** Every top-level transcript node: a user row, or a turn with its children's kinds. */
function transcriptShape(): string[] {
  return Array.from(messages().children).map((node) => {
    const row = node as HTMLElement;
    if (!row.classList.contains("assistant")) return row.className;
    const kinds = Array.from(row.children).map((kid) => {
      const el = kid as HTMLElement;
      for (const [name, kind] of [["md", "md"], ["step", "step"], ["perm-card", "perm"], ["msg-actions", "actions"], ["msg-stopped", "stopped"]] as const) {
        if (el.classList.contains(name)) return kind;
      }
      return el.className;
    });
    return "turn:" + kinds.join(",");
  });
}

function turnTexts(): string[] {
  return (Array.from(messages().querySelectorAll(":scope > .msg.assistant")) as Array<HTMLElement & { _messageText?: string }>)
    .map((wrap) => turnTextKey(wrap._messageText || ""));
}

function at(seconds: number): string {
  return new Date(Date.UTC(2026, 10, 18, 0, 0, seconds)).toISOString();
}

function paintHistory(items: HistoryItem[], openTail = false): Promise<void> {
  return new Promise((resolve) => scheduleFramedRender(items, { host: messages(), openTail, onDone: resolve }));
}

describe("a turn is one container, live and reopened", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async () => json({}, 404)));
  });

  it("gives the same structure and the same copied text for the same turn", async () => {
    // Live: narration, a step, two narration rows in a row, an approval, a
    // second step, the answer.
    renderStored({ role: "user", content: "Fold change?", message_id: "m-1" });
    onEvent({ type: "text_reset", root_frame_id: "f-1" });
    onEvent(chunk("Reading the plate first.\n"));
    onEvent({ type: "step", root_frame_id: "f-1", step_id: "s-1", kind: "read", title: "Reading plate.csv", status: "running" });
    onEvent({ type: "step_update", root_frame_id: "f-1", step_id: "s-1", status: "done" });
    onEvent(chunk("Now the fit.\n"));
    onEvent(chunk("It needs a package.\n"));
    onEvent({ type: "await_permission", root_frame_id: "f-1", frame_id: "f-1", decision_id: "perm-a", tool: "env_setup", title: "Install" });
    onEvent({ type: "permission_resolved", root_frame_id: "f-1", decision_id: "perm-a", allow: true, scope: "once" });
    onEvent({ type: "step", root_frame_id: "f-1", step_id: "s-2", kind: "code", title: "Fitting", status: "running" });
    onEvent({ type: "step_update", root_frame_id: "f-1", step_id: "s-2", status: "done" });
    onEvent(chunk("The slope is 2.1.\n"));
    turnDone("completed", { type: "frame_update", status: "completed" });
    const live = transcriptShape();
    const liveText = turnTexts();
    expect(live).toEqual(["msg user", "turn:md,step,md,perm,step,md,actions"]);
    expect(liveText).toEqual(["Reading the plate first. Now the fit. It needs a package. The slope is 2.1."]);

    // Reopened: the rows that turn stored, through the history paint.
    messages().replaceChildren();
    permCards.value = Object.create(null);
    await paintHistory(interleaveHistory(
      [
        { role: "user", content: "Fold change?", message_id: "m-1", created_at: at(0) },
        { role: "assistant", content: "Reading the plate first.", message_id: "m-2", created_at: at(1) },
        { role: "assistant", content: "Now the fit.", message_id: "m-3", created_at: at(3) },
        { role: "assistant", content: "It needs a package.", message_id: "m-4", created_at: at(4) },
        { role: "assistant", content: "The slope is 2.1.", message_id: "m-5", created_at: at(8) },
      ],
      [
        { step_id: "s-1", kind: "read", title: "Reading plate.csv", status: "done", created_at: Date.parse(at(2)), seq: 1 },
        { step_id: "s-2", kind: "code", title: "Fitting", status: "done", created_at: Date.parse(at(6)), seq: 2 },
      ],
      [{ decision_id: "perm-a", frame_id: "f-1", tool: "env_setup", title: "Install", state: "allowed", scope: "once", created_at: at(5) }],
    ));
    expect(transcriptShape()).toEqual(live);
    expect(turnTexts()).toEqual(liveText);
    // One action row for the turn, not one per stored paragraph.
    expect(messages().querySelectorAll(".msg-actions")).toHaveLength(1);
  });

  it("closes a turn at the next question, and ends a stopped one with its marker", async () => {
    await paintHistory(interleaveHistory(
      [
        { role: "user", content: "First?", created_at: at(0) },
        { role: "assistant", content: "Looking.", created_at: at(1) },
        { role: "assistant", content: "_Stopped by user._", created_at: at(3), cancelled: { reason: "user", request_id: "web-1" } },
        { role: "user", content: "Second?", created_at: at(4) },
        { role: "assistant", content: "Answer.", created_at: at(5), failure: { request_id: "web-2", code: "max_turns" } },
      ],
      [{ step_id: "s-1", kind: "read", title: "Reading", status: "done", created_at: Date.parse(at(2)), seq: 1 }],
    ));
    expect(transcriptShape()).toEqual([
      "msg user",
      "turn:md,step,stopped,actions",
      "msg user",
      "turn:md,msg-failure-meta,actions",
    ]);
    const stopped = messages().querySelectorAll(":scope > .msg.assistant")[0] as HTMLElement;
    expect(stopped.classList.contains("turn-stopped")).toBe(true);
    // The marker row is not part of what Copy copies.
    expect(turnTexts()).toEqual(["Looking.", "Answer."]);
  });

  it("leaves a running turn's stored cards unfinished, and its replay takes them over", async () => {
    await paintHistory(
      interleaveHistory(
        [{ role: "user", content: "Fold change?", message_id: "m-1", created_at: at(0) }],
        [
          { step_id: "s-1", kind: "read", title: "Reading plate.csv", status: "done", created_at: Date.parse(at(1)), seq: 1 },
          { step_id: "s-2", kind: "write", title: "Writing fit.py", status: "running", created_at: Date.parse(at(2)), seq: 2 },
        ],
      ),
      true,
    );
    // No action row on a turn that has not ended.
    expect(transcriptShape()).toEqual(["msg user", "turn:step,step"]);
    onEvent({ type: "text_reset", root_frame_id: "f-1" });
    onEvent(chunk("Reading the plate first.\n\n"));
    onEvent({ type: "step", root_frame_id: "f-1", step_id: "s-1", kind: "read", title: "Reading plate.csv", status: "running" });
    onEvent(chunk("Now the fit.\n\n"));
    onEvent({ type: "step", root_frame_id: "f-1", step_id: "s-2", kind: "write", title: "Writing fit.py", status: "running" });
    // The stored turn is gone with its last card: one container for the turn.
    expect(transcriptShape()).toEqual(["msg user", "turn:md,step,md,step,md"]);
    expect(messages().querySelectorAll(".step")).toHaveLength(2);
  });

  it("keeps following when a replay moves stored cards from above the view", async () => {
    const host = messages() as HTMLElement;
    await paintHistory(
      interleaveHistory(
        [{ role: "user", content: "Go", message_id: "m-1", created_at: at(0) }],
        [{ step_id: "s-1", kind: "read", title: "Reading", status: "done", created_at: Date.parse(at(1)), seq: 1 }],
      ),
      true,
    );
    Object.assign(host, { scrollHeight: 1000, clientHeight: 400, scrollTop: 600 });
    _messagesFollow.value = true;
    flushScrollNow();
    onEvent({ type: "text_reset", root_frame_id: "f-1" });
    onEvent({ type: "step", root_frame_id: "f-1", step_id: "s-1", kind: "read", title: "Reading", status: "running" });
    // What the browser does when a card above the view moves to the end: it
    // lowers scrollTop to hold the view, and the scroll event is measured.
    host.scrollTop = 450;
    updateJumpPill();
    flushScrollNow();
    expect(host.scrollTop).toBe(1000);
    expect(_messagesFollow.value).toBe(true);
    unbindMessageScroll();
  });

  it("puts an older page into the turns it belongs to", async () => {
    const held = {
      messages: [
        { role: "assistant", content: "Found it.", message_id: "m-3", created_at: at(4) },
        { role: "user", content: "Next?", message_id: "m-4", created_at: at(5) },
        { role: "assistant", content: "Answer.", message_id: "m-5", created_at: at(6) },
      ],
      steps: [{ step_id: "s-1", kind: "read", title: "Reading", status: "done", created_at: Date.parse(at(3)), seq: 1 }],
      approvals: [],
    };
    await paintHistory(interleaveHistory(held.messages, held.steps));
    // The first page starts inside a turn whose beginning was not loaded.
    expect(transcriptShape()).toEqual(["turn:step,md,actions", "msg user", "turn:md,actions"]);
    const below = messages().children[2];
    prependHistory(
      messages(),
      [
        { role: "user", content: "First?", message_id: "m-1", created_at: at(1) },
        { role: "assistant", content: "Looking.", message_id: "m-2", created_at: at(2) },
      ],
      held,
    );
    expect(transcriptShape()).toEqual(["msg user", "turn:md,step,md,actions", "msg user", "turn:md,actions"]);
    expect(turnTexts()).toEqual(["Looking. Found it.", "Answer."]);
    expect(messages().querySelectorAll(".step")).toHaveLength(1);
    // Nothing from the first loaded question down was rebuilt.
    expect(messages().children[3]).toBe(below);
  });
});

// --------------------------------------------------------------------------
// SYNC-8: a language switch relabels what is already on screen
// --------------------------------------------------------------------------

describe("a language switch", () => {
  afterEach(async () => {
    await setLang("en");
  });

  it("relabels decided and open approval cards in place", async () => {
    permCards.value = Object.create(null);
    renderStoredPermission({ decision_id: "p-done", frame_id: "f-1", tool: "bash", title: "Run", state: "allowed", scope: "once" });
    renderStoredPermission({
      decision_id: "p-denied", frame_id: "f-1", tool: "bash", title: "Run", state: "denied", scope: "once", message: "no",
    });
    renderStoredPermission({ decision_id: "p-open", frame_id: "f-1", tool: "bash", title: "Run", state: "pending" });
    const [done, denied, open] = Array.from(messages().querySelectorAll(".perm-card")) as HTMLElement[];
    const reason = open?.querySelector(".perm-fb") as HTMLInputElement;
    reason.value = "typed before the switch";
    expect(done?.querySelector(".perm-status")?.textContent).toBe("Allowed");

    await setLang("zh");
    expect(done?.querySelector(".perm-status")?.textContent).toBe("已允许");
    expect(denied?.querySelector(".perm-reason")?.textContent).toBe("原因：no");
    expect(open?.querySelector(".perm-allow")?.textContent).toBe("允许");
    expect(reason.placeholder).toBe("（可选）拒绝原因，会反馈给智能体");
    // Relabelled, not rebuilt: what the user typed is still there.
    expect(reason.value).toBe("typed before the switch");
    expect(open?.querySelector(".perm-fb")).toBe(reason);
  });
});

// --------------------------------------------------------------------------
// SYNC-10: a link to a session that does not exist
// --------------------------------------------------------------------------

describe("a missing session", () => {
  it("says so and offers the way back to the project and Home", () => {
    const toProject = vi.fn();
    const toHome = vi.fn();
    renderMissingSession(messages(), { project: toProject, home: toHome });
    const panel = messages().querySelector(".missing-session") as HTMLElement;
    expect(panel.getAttribute("role")).toBe("alert");
    expect(panel.querySelector(".es-title")?.textContent).toBe("Session not found");
    const buttons = Array.from(panel.querySelectorAll("button")) as HTMLElement[];
    expect(buttons.map((b) => b.textContent)).toEqual(["Back to the project", "Back to Home"]);
    (buttons[0] as unknown as { onclick: () => void }).onclick();
    (buttons[1] as unknown as { onclick: () => void }).onclick();
    expect(toProject).toHaveBeenCalledTimes(1);
    expect(toHome).toHaveBeenCalledTimes(1);
  });

  it("offers only Home when the link's project is not one either", () => {
    renderMissingSession(messages(), { project: null, home: vi.fn() });
    expect(Array.from(messages().querySelectorAll("button")).map((b) => b.textContent)).toEqual(["Back to Home"]);
  });
});
