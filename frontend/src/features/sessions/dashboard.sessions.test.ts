/**
 * The dashboard's session lists (Running, Recent) and the example CTA shown
 * when there are no sessions yet.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type FakeEl = {
  tag: string;
  cls: string | null;
  text: string;
  textContent: string;
  children: FakeEl[];
  removed: boolean;
  disabled: boolean;
  onclick: (() => void) | null;
  classList: { contains: () => boolean; add: () => void; remove: () => void };
  appendChild: (child: FakeEl) => FakeEl;
  remove: () => void;
  innerHTML: string;
};

function fakeEl(tag = "div", cls: string | null = null, text = ""): FakeEl {
  const node = {
    tag,
    cls,
    text,
    textContent: text,
    children: [] as FakeEl[],
    removed: false,
    disabled: false,
    onclick: null,
    classList: { contains: () => false, add: () => {}, remove: () => {} },
    appendChild(child: FakeEl) {
      node.children.push(child);
      return child;
    },
    remove() {
      node.removed = true;
    },
    get innerHTML() {
      return "";
    },
    set innerHTML(_value: string) {
      node.children.length = 0;
    },
  } as FakeEl;
  return node;
}

const dom: Record<string, FakeEl | null> = {};

vi.mock("./api", () => ({ api: vi.fn(), apiErrorText: (e: unknown) => String(e) }));
vi.mock("./chrome", () => ({ ensureActivateKeys: () => {} }));
vi.mock("./dom", () => ({
  $: (sel: string) => (sel in dom ? dom[sel] : null),
  el: (tag: string, cls: string | null, text?: string) => fakeEl(tag, cls, text || ""),
  ago: () => "just now",
  navURL: () => {},
  syncMobileChrome: () => {},
}));

import { api } from "./api";
import { renderDashRecent, stopDashPoll } from "./dashboard";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => { resolve = yes; });
  return { promise, resolve };
}

/** Intervals started through `window.setInterval` and not yet cleared. */
const intervals = new Set<number>();

beforeEach(() => {
  vi.mocked(api).mockReset();
  for (const key of Object.keys(dom)) delete dom[key];
  dom["#dash-sessions"] = fakeEl();
  intervals.clear();
  let next = 1;
  vi.stubGlobal("window", {
    setInterval: () => {
      const id = next++;
      intervals.add(id);
      return id;
    },
  });
  vi.stubGlobal("clearInterval", (id: number) => {
    intervals.delete(id);
  });
});

afterEach(() => {
  stopDashPoll();
  vi.unstubAllGlobals();
});

describe("the example CTA's poll", () => {
  it("runs once however many repaints raced its first status read", async () => {
    const replies = [deferred<unknown>(), deferred<unknown>()];
    let call = 0;
    vi.mocked(api).mockImplementation(() => replies[call++]!.promise as never);
    renderDashRecent([]);
    renderDashRecent([]);
    for (const reply of replies) reply.resolve({ running: true });
    await vi.waitFor(() => expect(call).toBe(2));
    await Promise.resolve();
    await Promise.resolve();
    expect(intervals.size).toBe(1);
  });

  it("does not start after the dashboard was left", async () => {
    const reply = deferred<unknown>();
    vi.mocked(api).mockReturnValue(reply.promise as never);
    renderDashRecent([]);
    stopDashPoll(); // what showWorkspace does
    reply.resolve({ running: true });
    await Promise.resolve();
    await Promise.resolve();
    expect(intervals.size).toBe(0);
  });
});
