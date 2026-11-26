import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Shell } from "../../components/dashboard/Shell";
import { setLang } from "../../i18n/runtime";
import { copyFailedText } from "../chrome/clipboard";
import * as transcript from "../sessions/transcript";
import { renderStored as renderOlderPage } from "../sessions/transcript";
import * as messageComponents from "./components";
import { currentId, _openGen, historyContent, historyLoad, msgCursor, msgHasEarlier, project } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { branchState } from "../../stores/timeline";
import { loadEarlierMessages } from "../sessions/messages";
import { historyT } from "./copy";
import { FORK_NO_CHECKPOINT_MESSAGE } from "../execution/conflict";
import { hint } from "../sessions/chrome";

vi.mock("preact/hooks", async (original) => ({
  ...await original<typeof import("preact/hooks")>(),
  useEffect: vi.fn(),
}));
import {
  INITIAL_RENDER_BATCH,
  addMsgActions,
  cancelFramedRender,
  insertMessageByTime,
  nextBatchEnd,
  renderEmptySession,
  renderMessageRefChips,
  renderStored as renderFirstPage,
  scheduleFramedRender,
  type HistoryItem,
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
  hidden = false;
  disabled = false;
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
    // Strip to a fixed point, as the other test doubles do: a single pass over
    // `<<b>script>` leaves `<script>`. Not a sanitiser.
    let text = String(value),
      previous: string;
    do {
      previous = text;
      text = text.replace(/<[^>]*>/g, "");
    } while (text !== previous);
    this.text = text;
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
  removeAttribute(name: string): void {
    delete this.attrs[name];
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
  const attr = /^\[([^\]=~|^$*]+)(?:([~|^$*]?=)"([^"]*)")?\]$/.exec(sel);
  if (attr) {
    const have = node.getAttribute(attr[1]!);
    if (have == null) return false;
    return attr[3] === undefined || have === attr[3];
  }
  return node.tagName === sel.toUpperCase();
}

class RowDoc {
  documentElement = new RowEl("html");
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
  createDocumentFragment(): RowEl {
    return new RowEl("#fragment");
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

  it.each(ROW_RENDERERS)("%s: an annotated message shows the user's words and its pins, not instructions (SYNC-5)", (_name, render) => {
    const row = render({
      role: "user",
      content: "Look at this plot",
      message_id: "msg-pins",
      annotations: [{ number: 1, kind: "image", artifact_name: "volcano.png", body: "which gene is this?" }],
    }) as unknown as RowEl;
    expect(row.querySelector(".bubble")?.textContent).toBe("Look at this plot");
    const chip = row.querySelector(".annot-attach");
    expect(chip).not.toBeNull();
    expect(chip!.textContent).toContain("volcano.png");
    expect(chip!.textContent).toContain("which gene is this?");
  });

  it.each(ROW_RENDERERS)("%s: a row stored before SYNC-5 hides the injected annotation instructions", (_name, render) => {
    const row = render({
      role: "user",
      content: "Look at this plot\n\n【图像标注反馈】用户直接在生成的图像上用图钉标注了修改意见。\n3) 重新运行绘图代码，覆盖写回同名图像文件",
      message_id: "msg-legacy",
    }) as unknown as RowEl;
    expect(row.querySelector(".bubble")?.textContent).toBe("Look at this plot");
    const onlyBlock = render({
      role: "user",
      content: "【图像标注反馈】用户直接在生成的图像上用图钉标注了修改意见。",
      message_id: "msg-legacy-2",
    }) as unknown as RowEl;
    expect(onlyBlock.querySelector(".bubble")?.textContent).not.toContain("【图像标注反馈】");
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

  it.each(ROW_RENDERERS)("%s: an answer offers Copy and a rating, not Edit (M-71, SYNC-26)", (_name, render) => {
    doc.composer.value = "my draft";
    const row = render({ role: "assistant", content: "An answer." }) as unknown as RowEl;
    const buttons = row.querySelector(".msg-actions")!.children;
    expect(buttons.map((b) => b.attrs["data-icon"])).toEqual(["copy", "thumbs-up", "thumbs-down"]);
    expect(buttons.map((b) => b.attrs["aria-label"])).toEqual(buttons.map((b) => b.title));
    expect(buttons.slice(1).map((b) => b.attrs["aria-pressed"])).toEqual(["false", "false"]);
    // Nothing on the row writes into the composer.
    expect(doc.composer.value).toBe("my draft");
  });

  it.each(ROW_RENDERERS)("%s: a 👎 asks why, optionally, and says the feedback stays on this computer (M-72)", (_name, render) => {
    currentId.value = "frame-1";
    const posts: Array<{ key?: string; rating?: unknown; reason?: unknown }> = [];
    vi.stubGlobal("fetch", (_url: string, init?: RequestInit) => {
      posts.push(JSON.parse(String(init?.body || "{}")));
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve("{}") });
    });
    const row = render({ role: "assistant", content: "Answer B." }) as unknown as RowEl;
    const [, up, down] = row.querySelector(".msg-actions")!.children as [RowEl, RowEl, RowEl];
    down.onclick!();
    expect(down.attrs["aria-pressed"]).toBe("true");
    expect(doc.hint.textContent).toContain("Saved on this computer");
    const box = row.querySelector(".msg-feedback")!;
    expect(box.querySelector(".mf-note")!.textContent).toContain("not sent anywhere");
    (box.querySelector(".mf-reason") as unknown as { value: string }).value = "  used the wrong column ";
    box.querySelector(".outline-btn")!.onclick!();
    expect(posts.at(-1)).toEqual({ key: posts[0]!.key, rating: "down", reason: "used the wrong column" });
    expect(row.querySelector(".msg-feedback")).toBeNull();
    // A 👍 instead closes the question; the rating is all it sends.
    down.onclick!();
    up.onclick!();
    expect(row.querySelector(".msg-feedback")).toBeNull();
    expect(posts.at(-1)).toEqual({ key: posts[0]!.key, rating: "up" });
    expect(up.attrs["aria-pressed"]).toBe("true");
    expect(down.attrs["aria-pressed"]).toBe("false");
  });

  it("the first page, load-earlier and the live turn share one row implementation", () => {
    expect(transcript.renderStored).toBe(renderFirstPage);
    expect(transcript.addMsgActions).toBe(addMsgActions);
    expect(transcript.insertMessageByTime).toBe(insertMessageByTime);
    expect(transcript.renderEmptySession).toBe(renderEmptySession);
    expect(transcript.renderMessageRefChips).toBe(renderMessageRefChips);
  });

  it.each(ROW_RENDERERS)("%s: a user row shows its pinned @-refs without a window lookup", (_name, render) => {
    // No `window.renderMessageRefChips` here: the first page used to reach
    // the chips only through that late-bound name.
    const row = render({
      role: "user",
      content: "Plot @growth.csv",
      artifact_refs: [{ display_name: "growth.csv", version_id: "v-1", sha256: "abcdef0123456789" }],
    }) as unknown as RowEl;
    const chips = row.querySelectorAll(".msg-ref-chip");
    expect(chips).toHaveLength(1);
    expect(chips[0]!.textContent).toContain("growth.csv");
    expect(chips[0]!.title).toBe("v-1 · sha256:abcdef012345");
  });

  it.each(ROW_RENDERERS)("%s: a text-form @name#v-<id> reopens without the hash, the chip keeps it", (_name, render) => {
    // Written before ART-16, when the `@` menu put the version into the text.
    const row = render({
      role: "user",
      content: "@cohort_clinical_markers.csv#v-9f5c06efc35e 这个文件一共多少行？",
      artifact_refs: [{ display_name: "cohort_clinical_markers.csv", version_id: "v-9f5c06efc35e" }],
    }) as unknown as RowEl;
    expect(row.querySelector(".bubble")!.textContent).toBe("@cohort_clinical_markers.csv 这个文件一共多少行？");
    expect(row.querySelectorAll(".msg-ref-chip")[0]!.title).toBe("v-9f5c06efc35e");
  });

  function forkButton(row: RowEl | null): RowEl | null {
    return row?.querySelector("[data-fork-message-id]") ?? null;
  }

  function forkRoot(): string {
    return doc.documentElement.dataset.forkFromMessage || "";
  }

  function underForkRow(button: RowEl | null): boolean {
    let node = button?.parentNode ?? null;
    while (node) {
      if (node.classList.contains("msg-fork")) return true;
      node = node.parentNode;
    }
    return false;
  }

  /** Shown only when the root flag is on and the control sits under `.msg-fork`. */
  function forkShown(button: RowEl | null): boolean {
    return forkRoot() === "on" && underForkRow(button);
  }

  const forkable = {
    role: "user",
    content: "Why does the control stay dark?",
    message_id: "msg-exact",
    fork_checkpoint_id: "ckpt-exact",
  };

  it.each(ROW_RENDERERS)("%s: a checkpointed user message shows a fork control for that message", (_name, render) => {
    branchState.value = { capabilities: { fork_from_message: true } };
    const row = render(forkable) as unknown as RowEl;
    const button = forkButton(row);
    expect(button).not.toBeNull();
    expect(forkShown(button)).toBe(true);
    expect(button!.hidden).toBe(false);
    expect(button!.getAttribute("data-fork-message-id")).toBe("msg-exact");
    expect(button!.textContent).toBe(historyT("history.forkMessage.label"));
    expect(button!.getAttribute("aria-label")).toBeNull();
    expect(row.querySelectorAll("[data-fork-message-id]")).toHaveLength(1);
  });

  it.each(ROW_RENDERERS)("%s: the fork control stays hidden while fork_from_message is false or missing", (_name, render) => {
    branchState.value = { capabilities: { fork_from_message: true } };
    branchState.value = null;
    const missing = render(forkable) as unknown as RowEl;
    const missingButton = forkButton(missing)!;
    expect(forkRoot()).toBe("off");
    expect(underForkRow(missingButton)).toBe(true);
    expect(forkShown(missingButton)).toBe(false);
    expect(missingButton.hidden).toBe(false);

    branchState.value = {
      capabilities: { fork_from_message: false },
      capability_reasons: { fork_from_message: "workspace revert recovery must complete" },
    };
    const refused = render(forkable) as unknown as RowEl;
    const button = forkButton(refused)!;
    expect(forkRoot()).toBe("off");
    expect(underForkRow(button)).toBe(true);
    expect(forkShown(button)).toBe(false);
    expect(button.disabled).toBeFalsy();
  });

  it.each(ROW_RENDERERS)("%s: a fork control painted before branch state appears when the capability turns on", (_name, render) => {
    branchState.value = { capabilities: { fork_from_message: false } };
    const row = render(forkable) as unknown as RowEl;
    const button = forkButton(row)!;
    expect(forkShown(button)).toBe(false);
    expect(forkRoot()).toBe("off");
    branchState.value = { capabilities: { fork_from_message: true } };
    expect(forkShown(button)).toBe(true);
    expect(button.getAttribute("data-fork-message-id")).toBe("msg-exact");
  });

  it("hides when the capability goes from true to false, and stays hidden when the projection is cleared", () => {
    branchState.value = { capabilities: { fork_from_message: true } };
    const row = renderFirstPage(forkable) as unknown as RowEl;
    const button = forkButton(row)!;
    expect(forkShown(button)).toBe(true);
    branchState.value = { capabilities: { fork_from_message: false } };
    expect(forkRoot()).toBe("off");
    expect(underForkRow(button)).toBe(true);
    expect(forkShown(button)).toBe(false);
    branchState.value = null;
    expect(forkRoot()).toBe("off");
    expect(underForkRow(button)).toBe(true);
    expect(forkShown(button)).toBe(false);
  });

  it("hides when a true capability is replaced by a missing projection", () => {
    branchState.value = { capabilities: { fork_from_message: true } };
    const row = renderFirstPage(forkable) as unknown as RowEl;
    expect(forkShown(forkButton(row))).toBe(true);
    branchState.value = null;
    expect(forkRoot()).toBe("off");
    expect(underForkRow(forkButton(row))).toBe(true);
    expect(forkShown(forkButton(row))).toBe(false);
  });

  function frameQueue(): { flush: () => void; pending: () => number } {
    const frames = new Map<number, FrameRequestCallback>();
    let next = 1;
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
      const id = next++;
      frames.set(id, cb);
      return id;
    });
    vi.stubGlobal("cancelAnimationFrame", (id: number) => {
      frames.delete(id);
    });
    return {
      pending: () => frames.size,
      flush() {
        const id = Math.min(...frames.keys());
        const cb = frames.get(id);
        frames.delete(id);
        cb?.(0);
      },
    };
  }

  function stagedForkItems(): HistoryItem[] {
    return [0, 1].map((n) => ({
      t: n,
      seq: n,
      kind: "msg",
      v: {
        ...forkable,
        message_id: `msg-${n}`,
        content: `Question ${n} stays checkpointed`,
      },
    }));
  }

  it("a capability that turns off while rows are in a fragment stays off after mount", () => {
    const frames = frameQueue();
    branchState.value = { capabilities: { fork_from_message: true } };
    expect(forkRoot()).toBe("on");
    const stage = doc.createDocumentFragment();
    scheduleFramedRender(stagedForkItems(), { host: stage as unknown as ParentNode, batch: 1 });
    frames.flush();
    expect(doc.querySelector("[data-fork-message-id]")).toBeNull();
    expect(stage.querySelector("[data-fork-message-id]")).not.toBeNull();
    expect(underForkRow(stage.querySelector("[data-fork-message-id]"))).toBe(true);
    branchState.value = { capabilities: { fork_from_message: false } };
    expect(forkRoot()).toBe("off");
    frames.flush();
    expect(frames.pending()).toBe(0);
    doc.messages.appendChild(stage);
    const buttons = doc.querySelectorAll("[data-fork-message-id]");
    expect(buttons).toHaveLength(2);
    for (const button of buttons) {
      expect(underForkRow(button)).toBe(true);
      expect(forkShown(button)).toBe(false);
    }
    branchState.value = { capabilities: { fork_from_message: true } };
    expect(forkRoot()).toBe("on");
    expect(doc.querySelectorAll("[data-fork-message-id]")).toHaveLength(2);
    for (const button of doc.querySelectorAll("[data-fork-message-id]")) {
      expect(forkShown(button)).toBe(true);
    }
  });

  it("a capability that turns on while rows are in a fragment shows them once mounted", () => {
    const frames = frameQueue();
    branchState.value = { capabilities: { fork_from_message: false } };
    expect(forkRoot()).toBe("off");
    const stage = doc.createDocumentFragment();
    scheduleFramedRender(stagedForkItems(), { host: stage as unknown as ParentNode, batch: 1 });
    frames.flush();
    expect(doc.querySelector("[data-fork-message-id]")).toBeNull();
    const staged = stage.querySelector("[data-fork-message-id]");
    expect(underForkRow(staged)).toBe(true);
    expect(forkShown(staged)).toBe(false);
    branchState.value = { capabilities: { fork_from_message: true } };
    frames.flush();
    expect(frames.pending()).toBe(0);
    doc.messages.appendChild(stage);
    const buttons = doc.querySelectorAll("[data-fork-message-id]");
    expect(buttons).toHaveLength(2);
    for (const button of buttons) expect(forkShown(button)).toBe(true);
    branchState.value = null;
    expect(forkRoot()).toBe("off");
    for (const button of doc.querySelectorAll("[data-fork-message-id]")) {
      expect(underForkRow(button)).toBe(true);
      expect(forkShown(button)).toBe(false);
    }
  });

  it.each(ROW_RENDERERS)("%s: no fork control without an exact checkpoint, or on rows that are not that question", (_name, render) => {
    branchState.value = { capabilities: { fork_from_message: true } };
    const cases: Record<string, unknown>[] = [
      { role: "user", content: "No snapshot", message_id: "msg-1", fork_checkpoint_id: null },
      { role: "user", content: "Empty snapshot", message_id: "msg-2", fork_checkpoint_id: "" },
      { role: "user", content: "Live bubble", fork_checkpoint_id: "ckpt-live" },
      { role: "assistant", content: "Answer", message_id: "msg-a", fork_checkpoint_id: "ckpt-a" },
      {
        role: "user",
        content: 'Plan "Demo" is approved; start executing it automatically now.',
        message_id: "msg-seed",
        fork_checkpoint_id: "ckpt-seed",
      },
      {
        role: "assistant",
        content: "Stopped mid-way",
        message_id: "msg-stop",
        fork_checkpoint_id: "ckpt-stop",
        cancelled: { reason: "user", request_id: "req-1", execution_id: "exec-1" },
      },
    ];
    for (const message of cases) {
      const row = render(message) as unknown as RowEl;
      expect(forkButton(row)).toBeNull();
    }
  });

  it("load earlier keeps the same fork rules, including a capability that arrives late", async () => {
    currentId.value = "frame-1";
    _openGen.value = 1;
    msgHasEarlier.value = true;
    msgCursor.value = 20;
    historyContent.value = null;
    branchState.value = { capabilities: { fork_from_message: false } };
    vi.stubGlobal("fetch", () => Promise.resolve({
      ok: true,
      status: 200,
      text: () => Promise.resolve(JSON.stringify({
        messages: [
          { ...forkable, seq: 4, created_at: "2026-09-01T00:00:00Z" },
          {
            role: "assistant",
            content: "An earlier answer",
            seq: 5,
            message_id: "msg-asst",
            fork_checkpoint_id: "ckpt-asst",
          },
          {
            role: "user",
            content: "No snapshot yet",
            seq: 2,
            message_id: "msg-open",
            fork_checkpoint_id: null,
          },
        ],
        has_earlier: false,
        next_before_seq: null,
      })),
    }));
    await loadEarlierMessages();
    const user = doc.messages.querySelectorAll(".msg.user");
    expect(user).toHaveLength(2);
    const forked = user.find((row) => forkButton(row));
    const plain = user.find((row) => !forkButton(row));
    expect(forkRoot()).toBe("off");
    expect(underForkRow(forkButton(forked!))).toBe(true);
    expect(forkShown(forkButton(forked!))).toBe(false);
    expect(forkButton(forked!)!.getAttribute("data-fork-message-id")).toBe("msg-exact");
    expect(forkButton(plain!)).toBeNull();
    expect(doc.messages.querySelector(".msg.assistant")!.querySelector("[data-fork-message-id]")).toBeNull();
    branchState.value = { capabilities: { fork_from_message: true } };
    expect(forkShown(forkButton(forked!))).toBe(true);
  });

  it("a second click while a message fork is in flight does not post again", async () => {
    branchState.value = { capabilities: { fork_from_message: true } };
    currentId.value = "frame-1";
    const posts: Array<{ url: string; body: unknown }> = [];
    let release: (response: Response) => void = () => undefined;
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
      posts.push({ url: String(url), body: JSON.parse(String(init?.body || "{}")) });
      return new Promise<Response>((resolve) => {
        release = resolve;
      });
    });
    vi.stubGlobal("hint", hint);
    const row = renderFirstPage(forkable) as unknown as RowEl;
    const button = forkButton(row)!;
    button.focus();
    const first = button.onclick!();
    const second = button.onclick!();
    expect(button.disabled).toBe(false);
    expect(button.focused).toBe(1);
    expect(button.getAttribute("aria-disabled")).toBe("true");
    expect(button.getAttribute("aria-busy")).toBe("true");
    expect(button.textContent).toBe(historyT("history.forkMessage.busy"));
    expect(button.getAttribute("aria-label")).toBeNull();
    expect(doc.hint.getAttribute("aria-live")).toBe("polite");
    expect(doc.hint.textContent).toContain(historyT("history.forkMessage.busy"));
    // VIEW-2: the branch is named with the question's first 20 characters.
    expect(posts).toEqual([{
      url: "/api/v1/frames/frame-1/branches/fork",
      body: { from_message_id: "msg-exact", name: "Why does the control…" },
    }]);
    release(new Response(JSON.stringify({ branch_id: "br-new", name: "alt" }), { status: 200 }));
    await first;
    await second;
    expect(posts).toHaveLength(1);
    expect(button.disabled).toBe(false);
    expect(button.getAttribute("aria-disabled")).toBeNull();
    expect(button.getAttribute("aria-busy")).toBeNull();
    expect(button.textContent).toBe(historyT("history.forkMessage.label"));
    expect(button.focused).toBe(1);
  });

  it("a no-checkpoint 409 leaves the button unavailable and says why", async () => {
    branchState.value = { capabilities: { fork_from_message: true } };
    currentId.value = "frame-1";
    vi.stubGlobal("hint", hint);
    const posts: unknown[] = [];
    vi.stubGlobal("fetch", (_url: string, init?: RequestInit) => {
      posts.push(JSON.parse(String(init?.body || "{}")));
      return Promise.resolve(new Response(
        JSON.stringify({ error: FORK_NO_CHECKPOINT_MESSAGE, code: "conflict" }),
        { status: 409 },
      ));
    });
    const row = renderFirstPage(forkable) as unknown as RowEl;
    const button = forkButton(row)!;
    await button.onclick!();
    expect(posts).toHaveLength(1);
    expect(button.getAttribute("aria-disabled")).toBe("true");
    expect(button.getAttribute("aria-busy")).toBeNull();
    expect(button.disabled).toBe(false);
    expect(button.textContent).toBe(FORK_NO_CHECKPOINT_MESSAGE);
    expect(button.getAttribute("aria-label")).toBeNull();
    expect(doc.hint.getAttribute("aria-live")).toBe("polite");
    expect(doc.hint.textContent).toContain(FORK_NO_CHECKPOINT_MESSAGE);
    await button.onclick!();
    expect(posts).toHaveLength(1);
  });

  it("leaving the session drops the busy announcement and does not invite another fork", async () => {
    branchState.value = { capabilities: { fork_from_message: true } };
    currentId.value = "frame-1";
    vi.stubGlobal("hint", hint);
    let release: (response: Response) => void = () => undefined;
    vi.stubGlobal("fetch", () => new Promise<Response>((resolve) => {
      release = resolve;
    }));
    const row = renderFirstPage(forkable) as unknown as RowEl;
    const button = forkButton(row)!;
    const pending = button.onclick!();
    expect(doc.hint.textContent).toContain(historyT("history.forkMessage.busy"));
    currentId.value = "frame-2";
    release(new Response(JSON.stringify({ branch_id: "br-left", name: "elsewhere" }), { status: 200 }));
    await pending;
    expect(doc.hint.textContent || "").not.toContain("elsewhere");
    expect(doc.hint.textContent || "").not.toContain(historyT("history.forkMessage.busy"));
    expect(button.getAttribute("aria-disabled")).toBe("true");
    expect(button.textContent).not.toBe(historyT("history.forkMessage.label"));
  });

  it("a late fork from another session leaves this session's busy announcement", async () => {
    branchState.value = { capabilities: { fork_from_message: true } };
    vi.stubGlobal("hint", hint);
    const releases: Array<(response: Response) => void> = [];
    vi.stubGlobal("fetch", () => new Promise<Response>((resolve) => {
      releases.push(resolve);
    }));
    currentId.value = "frame-1";
    const left = forkButton(renderFirstPage(forkable) as unknown as RowEl)!;
    const pendingLeft = left.onclick!();
    currentId.value = "frame-2";
    const here = forkButton(renderFirstPage(forkable) as unknown as RowEl)!;
    const pendingHere = here.onclick!();
    expect(releases).toHaveLength(2);
    releases[0]!(new Response(JSON.stringify({ branch_id: "br-left", name: "left" }), { status: 200 }));
    await pendingLeft;
    expect(doc.hint.textContent).toContain(historyT("history.forkMessage.busy"));
    expect(here.getAttribute("aria-busy")).toBe("true");
    releases[1]!(new Response(JSON.stringify({ branch_id: "br-here", name: "here" }), { status: 200 }));
    await pendingHere;
    expect(doc.hint.textContent).toContain(historyT("history.forkMessage.created", "here"));
  });

  it("a row repainted while its fork is pending does not start a second busy state", async () => {
    branchState.value = { capabilities: { fork_from_message: true } };
    currentId.value = "frame-1";
    vi.stubGlobal("hint", hint);
    const posts: unknown[] = [];
    let release: (response: Response) => void = () => undefined;
    vi.stubGlobal("fetch", (_url: string, init?: RequestInit) => {
      posts.push(JSON.parse(String(init?.body || "{}")));
      return new Promise<Response>((resolve) => {
        release = resolve;
      });
    });
    const original = forkButton(renderFirstPage(forkable) as unknown as RowEl)!;
    const pending = original.onclick!();
    hint("The turn finished.");
    const repainted = forkButton(renderFirstPage(forkable) as unknown as RowEl)!;
    await repainted.onclick!();
    expect(posts).toHaveLength(1);
    expect(repainted.getAttribute("aria-busy")).toBeNull();
    expect(repainted.getAttribute("aria-disabled")).toBeNull();
    expect(repainted.textContent).toBe(historyT("history.forkMessage.label"));
    expect(doc.hint.textContent).toContain("The turn finished.");
    release(new Response(JSON.stringify({ branch_id: "br-new", name: "alt" }), { status: 200 }));
    await pending;
    expect(posts).toHaveLength(1);
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

  it("示例项目's empty session offers the example until it has been run, and opens it once started (ONB-4)", async () => {
    const opened: unknown[][] = [];
    vi.stubGlobal("openConversation", (...args: unknown[]) => {
      opened.push(args);
    });
    let seeded = false;
    const posts: unknown[] = [];
    vi.stubGlobal("fetch", async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        posts.push(JSON.parse(String(init.body || "{}")));
        return new Response(JSON.stringify({ seeded: false, running: true, frame_id: "f-example", project_id: "proj_example" }));
      }
      return new Response(JSON.stringify({ seeded, running: false, frame_id: seeded ? "f-example" : null }));
    });
    const settle = async () => {
      for (let i = 0; i < 10; i++) await Promise.resolve();
      await new Promise((resolve) => setTimeout(resolve, 0));
    };
    // Another project: no example card.
    project.value = "p-other";
    renderEmptySession();
    await settle();
    expect(doc.messages.querySelector(".es-example")).toBeNull();
    // 示例项目, not run yet: the card, which starts it and opens its session.
    doc.messages.children.length = 0;
    project.value = "proj_example";
    renderEmptySession();
    await settle();
    const card = doc.messages.querySelector(".es-example")!;
    expect(card).not.toBeNull();
    const button = card.querySelector(".solid-btn")!;
    button.onclick!();
    await settle();
    expect(posts).toEqual([expect.objectContaining({ confirm: true })]);
    expect(opened).toEqual([["f-example", "proj_example"]]);
    // Already run: the empty session is the generic one.
    seeded = true;
    doc.messages.children.length = 0;
    renderEmptySession();
    await settle();
    expect(doc.messages.querySelector(".es-example")).toBeNull();
  });

  it("the literature starter does not name an internal tool (SET-4)", async () => {
    for (const lang of ["zh", "en"] as const) {
      await setLang(lang);
      doc.messages.children.length = 0;
      renderEmptySession();
      const prompts = doc.messages.querySelectorAll(".es-chip-p").map((node) => node.textContent);
      expect(prompts.some((text) => text.includes("web_search"))).toBe(false);
      expect(prompts[0]).toBe(historyT("starter.litReview.prompt"));
      expect(prompts[0]).toMatch(lang === "zh" ? /^在网上检索/ : /^Search the web for/);
    }
  });

  it("offers no starters under a read-only imported copy's banner (M-66)", () => {
    const banner = new RowEl("div");
    banner.id = "imported-banner";
    doc.body.appendChild(banner);
    renderEmptySession();
    expect(doc.messages.querySelector(".es-chip")).toBeNull();
    const note = doc.messages.querySelector(".empty-session")!;
    expect(note.textContent).toContain(historyT("history.importedEmpty.title"));
    expect(note.textContent).toContain(historyT("history.importedEmpty.sub"));
  });
});

describe("NV-02 a message's @-ref chip opens the version it recorded", () => {
  type Versions = Record<string, { status?: number; body: unknown }>;
  const HISTORY: Versions = {
    "a-growth": {
      body: {
        versions: [
          { version_id: "v-2", filename: "growth.csv", is_latest: true, ordinal: 2 },
          { version_id: "v-1", filename: "growth.csv", is_latest: false, ordinal: 1 },
        ],
      },
    },
    "a-other": { body: { versions: [{ version_id: "x-1", filename: "other.csv", is_latest: true }] } },
    "a-local": { body: { versions: [{ version_id: "v-local", filename: "shared.csv", is_latest: true }] } },
    "a-private": { status: 403, body: { error: "artifact belongs to another session" } },
  };
  let doc: RowDoc;
  let requested: string[];
  // Loaded here so this block stays self-contained at the end of the file.
  let stores: typeof import("../../stores/artifacts");
  let artifactsApi: typeof import("../artifacts/api");
  let jsonResponse: typeof import("../artifacts/http-stub").jsonResponse;
  let viewerVersionState: typeof import("../artifacts/state").viewerVersionState;

  /** The server's `/artifacts/{id}/versions`, with optional held answers. */
  function serveVersions(held: Record<string, Promise<void>> = {}): void {
    artifactsApi.setArtifactsFetch(async (url: string) => {
      requested.push(url);
      const id = decodeURIComponent((/\/artifacts\/([^/]+)\/versions$/.exec(url) || [])[1] || "");
      if (held[id]) await held[id];
      const row = HISTORY[id];
      return jsonResponse(row ? row.body : { versions: [] }, row?.status ?? 200);
    });
  }

  async function drain(): Promise<void> {
    for (let i = 0; i < 5; i++) await new Promise((resolve) => setTimeout(resolve, 0));
  }

  function docked(): Record<string, unknown> | null {
    return stores.dockArtifact.value as Record<string, unknown> | null;
  }

  function chipsFor(refs: unknown[]): RowEl[] {
    const host = new RowEl("div");
    renderMessageRefChips(host as unknown as HTMLElement, refs);
    return host.querySelectorAll(".msg-ref-chip");
  }

  beforeEach(async () => {
    stores = await import("../../stores/artifacts");
    artifactsApi = await import("../artifacts/api");
    ({ jsonResponse } = await import("../artifacts/http-stub"));
    ({ viewerVersionState } = await import("../artifacts/state"));
    await setLang("en");
    resetStoreFields();
    doc = new RowDoc();
    vi.stubGlobal("document", doc);
    viewerVersionState.value = null;
    requested = [];
    serveVersions();
  });

  afterEach(() => {
    artifactsApi.setArtifactsFetch(null);
  });

  it.each(ROW_RENDERERS)("%s: a reopened chip opens V1 while this session lists the file at V2", async (_name, render) => {
    // The session's file list, as the Files panel has it: the head, V2.
    stores.artifacts.value = [{ id: "a-growth", artifact_id: "a-growth", version_id: "v-2", filename: "growth.csv" }];
    const row = render({
      role: "user",
      content: "Plot @growth.csv",
      artifact_refs: [{ display_name: "growth.csv", artifact_id: "a-growth", version_id: "v-1", sha256: "abcdef0123456789" }],
    }) as unknown as RowEl;
    const chip = row.querySelector(".msg-ref-chip")!;
    expect(chip.classList.contains("clickable")).toBe(true);
    expect(chip.title).toBe("v-1 · sha256:abcdef012345");
    chip.onclick!();
    await drain();
    expect(docked()).toMatchObject({ id: "a-growth", version_id: "v-1", filename: "growth.csv", _exactVersion: true });
    expect(viewerVersionState.value?.status).toBe("exact");
  });

  it("the live bubble's chip opens a pinned file from another session it has never listed", async () => {
    stores.artifacts.value = [];
    // send.ts draws these from the mentions it sent.
    const [chip] = chipsFor([{ display_name: "other.csv", version_id: "x-1", artifact_id: "a-other" }]);
    expect(chip!.classList.contains("clickable")).toBe(true);
    chip!.onclick!();
    await drain();
    expect(requested).toEqual(["/api/v1/artifacts/a-other/versions"]);
    expect(docked()).toMatchObject({ id: "a-other", version_id: "x-1", _exactVersion: true });
  });

  it("a copied-in file opens the local copy it recorded; its source session is provenance only", async () => {
    const [chip] = chipsFor([
      {
        display_name: "shared.csv",
        artifact_id: "a-local",
        version_id: "v-local",
        source_session: "f-sibling-session",
        materialized_target: "v-local",
      },
    ]);
    expect(chip!.title).toBe("v-local · ↗ f-sibling-se");
    chip!.onclick!();
    await drain();
    expect(requested).toEqual(["/api/v1/artifacts/a-local/versions"]);
    expect(docked()).toMatchObject({ id: "a-local", version_id: "v-local", _exactVersion: true });
  });

  it("a ref with no recorded version says so and opens nothing", async () => {
    stores.artifacts.value = [{ id: "a-growth", artifact_id: "a-growth", version_id: "v-2", filename: "growth.csv" }];
    const [chip] = chipsFor([{ display_name: "growth.csv", artifact_id: "a-growth" }]);
    expect(chip!.title).toBe("Historical version not recorded");
    chip!.onclick!();
    await drain();
    expect(doc.hint.textContent).toBe("Historical version not recorded");
    expect(requested).toEqual([]);
    expect(docked()).toBeNull();

    await setLang("zh");
    const [zh] = chipsFor([{ display_name: "growth.csv", artifact_id: "a-growth" }]);
    zh!.onclick!();
    expect(doc.hint.textContent).toBe("历史版本未记录");
  });

  it("a gone version, or one this reader may not see, gets one missing-version answer", async () => {
    for (const artifactId of ["a-gone", "a-private"]) {
      const [chip] = chipsFor([{ display_name: "x.csv", artifact_id: artifactId, version_id: "v-x" }]);
      chip!.onclick!();
      await drain();
      expect(viewerVersionState.value).toMatchObject({ status: "not-found", artifactId, versionId: "v-x" });
      expect(docked()).toEqual({ id: artifactId, artifact_id: artifactId });
    }
  });

  it("a late answer for an earlier chip does not replace the newer view", async () => {
    let release!: () => void;
    serveVersions({ "a-growth": new Promise<void>((resolve) => (release = resolve)) });
    const [first, second] = chipsFor([
      { display_name: "growth.csv", artifact_id: "a-growth", version_id: "v-1" },
      { display_name: "other.csv", artifact_id: "a-other", version_id: "x-1" },
    ]);
    first!.onclick!();
    await drain();
    second!.onclick!();
    await drain();
    expect(docked()).toMatchObject({ id: "a-other", version_id: "x-1" });
    release();
    await drain();
    expect(docked()).toMatchObject({ id: "a-other", version_id: "x-1" });
    expect(viewerVersionState.value).toMatchObject({ status: "exact", versionId: "x-1" });
  });
});

// --- NV-05: a model failure's fix entry, live and on reopen -----------------
//
// The gateway names five fixable model failures by what it can prove (401,
// 403, a listed 404 code, any other 404, a refusal before sending). The
// failure row offers 「打开模型设置」/"Open model settings" for each, the same
// way whether the turn just failed in front of the user or the session was
// reopened later. Settings is mocked: what it reads on open is covered by the
// gateway tests and the Models tab's own.

import { turnDone } from "../send/turn";
import { feed } from "./stream";

const nv05 = vi.hoisted(() => ({ openCustomize: vi.fn(() => Promise.resolve()) }));
vi.mock("../customize", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../customize")>()),
  openCustomize: nv05.openCustomize,
}));

describe("NV-05 a model failure's fix entry, live and on reopen", () => {
  // What `_friendly_error` streams and stores for each code (English).
  const SENTENCES: Record<string, string> = {
    llm_authentication_failed: "**The model service did not accept the credentials (HTTP 401).** Check the API key in Settings → Models.",
    llm_access_denied: "**The model service refused access (HTTP 403).** Check the account in Settings → Models.",
    llm_model_unavailable: "**The model service reports that this model is not available (HTTP 404).** Check the model id in Settings → Models.",
    llm_request_target_not_found: "**The model service found nothing at the requested address (HTTP 404).** Check the endpoint and the model id in Settings → Models.",
    llm_endpoint_unreachable: "**The model endpoint could not be reached: the connection was refused or the host name did not resolve, so no request was sent.** Check the base URL in Settings → Models.",
  };
  let doc: RowDoc;
  let requests: string[];

  beforeEach(async () => {
    await setLang("en");
    resetStoreFields();
    doc = new RowDoc();
    vi.stubGlobal("document", doc);
    vi.stubGlobal("requestAnimationFrame", () => 1);
    vi.stubGlobal("cancelAnimationFrame", () => undefined);
    requests = [];
    vi.stubGlobal("fetch", (url: string) => {
      requests.push(String(url));
      return Promise.resolve(new Response("{}", { status: 200 }));
    });
    nv05.openCustomize.mockClear();
  });

  function entry(row: RowEl): { meta: RowEl; button: RowEl } {
    const meta = row.querySelector(".msg-failure-meta");
    expect(meta, "no failure row").not.toBeNull();
    const button = meta!.querySelector(".turn-open-models");
    expect(button, "no Open model settings button").not.toBeNull();
    return { meta: meta!, button: button! };
  }

  function clickOpensModels(button: RowEl): void {
    const before = requests.length;
    nv05.openCustomize.mockClear();
    button.onclick!();
    expect(nv05.openCustomize).toHaveBeenCalledTimes(1);
    expect(nv05.openCustomize).toHaveBeenCalledWith("models");
    // Opening the settings sends nothing by itself: no model request, no turn.
    expect(requests.length).toBe(before);
  }

  it.each(ROW_RENDERERS)("%s: each code reopens with its sentence and the button", (_name, render) => {
    for (const [code, sentence] of Object.entries(SENTENCES)) {
      const row = render({
        role: "assistant",
        content: sentence,
        failure: { code, request_id: "web-nv05" },
      }) as unknown as RowEl;
      expect(row.querySelector(".md")!.textContent).toContain(sentence.replace(/\*\*/g, "").slice(0, 40));
      const { meta, button } = entry(row);
      expect(meta.dataset.failureCode).toBe(code);
      expect(meta.dataset.requestId).toBe("web-nv05");
      expect(button.textContent).toBe("Open model settings");
      clickOpensModels(button);
    }
    expect(requests.filter((url) => /probe|discover|message|chat/.test(url))).toEqual([]);
  });

  it.each(ROW_RENDERERS)("%s: a row stored with a first-version code keeps its own sentence", (_name, render) => {
    const old = "**The model provider rejected the API key (401/403).** In Settings → Models, check that the key is correct.";
    const row = render({
      role: "assistant",
      content: old,
      failure: { code: "llm_auth_rejected", request_id: "web-old" },
    }) as unknown as RowEl;
    expect(row.querySelector(".md")!.textContent).toContain("rejected the API key (401/403)");
    const { meta, button } = entry(row);
    expect(meta.dataset.failureCode).toBe("llm_auth_rejected");
    clickOpensModels(button);
  });

  it("an unknown upstream failure gets no settings button, live or on reopen", () => {
    const row = renderFirstPage({
      role: "assistant",
      content: "**The LLM call failed.** Check the model and API key in Settings → Models, then try again.",
      failure: { code: "internal_error", request_id: "web-unknown" },
    }) as unknown as RowEl;
    expect(row.querySelector(".msg-failure-meta")!.querySelector(".turn-open-models")).toBeNull();
  });

  it.each([
    ["before the first output", ""],
    ["after streamed output", "Here is the first half of the answer.\n"],
  ])("a live failure %s ends with the same entry a reopen shows", async (_when, streamed) => {
    await setLang("zh");
    const code = "llm_authentication_failed";
    const sentence = SENTENCES[code]!;
    const failure = { code, request_id: "web-live-nv05" };
    if (streamed) feed("text", streamed, { type: "text_chunk", block_type: "text" });
    // The gateway streams its sentence as the turn's last text, then the
    // terminal frame event carries the code and the local id.
    feed("text", "\n\n" + sentence + "\n", { type: "text_chunk", block_type: "text" });
    turnDone("failed", { type: "frame_update", status: "failed", ...failure });

    const live = doc.messages.children.at(-1)!;
    const { meta: liveMeta, button: liveButton } = entry(live);
    expect(liveMeta.dataset.failureCode).toBe(code);
    expect(liveMeta.dataset.requestId).toBe("web-live-nv05");
    expect(liveButton.textContent).toBe("打开模型设置");
    expect(live.querySelectorAll(".msg-failure-meta")).toHaveLength(1);

    const reopened = renderFirstPage({
      role: "assistant",
      content: (streamed ? streamed + "\n\n" : "") + sentence,
      failure,
    }) as unknown as RowEl;
    const { meta: storedMeta, button: storedButton } = entry(reopened);
    expect(storedMeta.textContent).toBe(liveMeta.textContent);
    expect(storedMeta.dataset.failureCode).toBe(liveMeta.dataset.failureCode);
    expect(storedButton.textContent).toBe(liveButton.textContent);
    clickOpensModels(liveButton);
    clickOpensModels(storedButton);
  });
});
