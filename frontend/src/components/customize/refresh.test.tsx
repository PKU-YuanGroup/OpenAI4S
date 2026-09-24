import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// A write in Settings re-reads its own tab in place. It used to call
// custTab() once it answered, which switched back to that tab, closed any
// nested editor and remounted -- even when the user had moved on meanwhile.

const hookState: unknown[] = [];
let hookCursor = 0;
const effects: Array<() => void> = [];

vi.mock("preact/hooks", () => ({
  useState: (initial: unknown) => {
    const index = hookCursor++;
    if (hookState.length === index) {
      hookState.push(typeof initial === "function" ? (initial as () => unknown)() : initial);
    }
    return [
      hookState[index],
      (value: unknown) => {
        hookState[index] =
          typeof value === "function" ? (value as (prev: unknown) => unknown)(hookState[index]) : value;
      },
    ];
  },
  useRef: (initial: unknown) => {
    const index = hookCursor++;
    if (hookState.length === index) hookState.push({ current: initial });
    return hookState[index];
  },
  useEffect: (fn: () => void, deps: unknown[]) => {
    const index = hookCursor++;
    const old = hookState[index] as unknown[] | undefined;
    if (old && deps.every((value, i) => value === old[i])) return;
    hookState[index] = deps;
    effects.push(() => void fn());
  },
}));

const mocks = vi.hoisted(() => ({ fetch: vi.fn(), hint: vi.fn(), alive: (): boolean => true }));
vi.mock("../../i18n", () => ({
  LANG: "en",
  t: (key: string, ...args: unknown[]) => [key, ...args].join(" "),
  tOptional: () => null,
  onLanguageChange: () => () => undefined,
}));
vi.mock("./use-timer-lease", () => ({ useAlive: () => mocks.alive, useTimerLease: () => ({}) }));

import { closeCust, custTab, openCust } from "../../features/customize/actions";
import { customizeGeneration, customizeTab, nestedEditor } from "../../features/customize/state";
import { MemoryTab } from "./MemoryTab";
import { ProfileRow } from "./ModelsTab";
import { NestedEditor } from "./NestedEditor";

type Node = {
  type?: unknown;
  props?: Record<string, unknown> & { children?: unknown; onClick?: () => unknown; onInput?: (e: unknown) => void };
};

const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

function find(node: unknown, match: (node: Node) => boolean): Node | null {
  if (Array.isArray(node)) {
    for (const child of node) {
      const found = find(child, match);
      if (found) return found;
    }
    return null;
  }
  if (!node || typeof node !== "object") return null;
  const current = node as Node;
  if (match(current)) return current;
  return find(current.props?.children, match);
}

function text(node: unknown): string {
  if (Array.isArray(node)) return node.map(text).join("");
  if (typeof node === "string" || typeof node === "number") return String(node);
  return node && typeof node === "object" ? text((node as Node).props?.children) : "";
}

async function settle(): Promise<void> {
  effects.splice(0).forEach((effect) => effect());
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

/** A write that answers only when told to; every read answers at once. */
function holdWrites(): { release: () => void } {
  const held: Array<() => void> = [];
  mocks.fetch.mockImplementation((_url: string, init?: RequestInit) => {
    if (!init?.method) return Promise.resolve(response({}));
    return new Promise<Response>((resolve) => held.push(() => resolve(response({ ok: true }))));
  });
  return { release: () => held.splice(0).forEach((answer) => answer()) };
}

beforeEach(() => {
  hookState.length = 0;
  hookCursor = 0;
  effects.length = 0;
  mocks.fetch.mockReset();
  vi.stubGlobal("fetch", mocks.fetch);
  vi.stubGlobal("document", { querySelectorAll: () => [], getElementById: () => null });
  // The workbench's toast; no key banner is bridged in.
  mocks.hint.mockReset();
  vi.stubGlobal("window", { hint: mocks.hint });
});
afterEach(() => {
  closeCust();
  vi.unstubAllGlobals();
});

describe("a Settings write that answers after the user moved on", () => {
  it("leaves them on the tab they moved to", async () => {
    const writes = holdWrites();
    openCust("models");
    hookCursor = 0;
    const row = ProfileRow({ p: { id: "mp-2", name: "Other", model: "m2" }, activeId: "mp-1", protocols: [], onEdit: () => {} });
    const activate = find(row, (node) => node.type === "button" && text(node) === "cust.models.setActive")!;
    const done = activate.props!.onClick!() as Promise<void>;

    custTab("network");
    const moved = customizeGeneration.value;
    writes.release();
    await done;
    expect(mocks.hint.mock.calls.map(([message]) => message)).toEqual(["toast.models.switched Other"]);
    expect(customizeTab.value).toBe("network");
    expect(customizeGeneration.value).toBe(moved);
  });

  it("leaves another nested editor the user opened meanwhile open", async () => {
    const writes = holdWrites();
    openCust("specialists");
    nestedEditor.value = { kind: "specialist", name: null };
    hookCursor = 0;
    const formNode = find(NestedEditor(), (node) => typeof node.type === "function" && node.type.name === "SpecialistForm")!;
    const form = () => {
      hookCursor = 0;
      return (formNode.type as (props: unknown) => Node)(formNode.props);
    };
    const name = find(form(), (node) => node.type === "input" && node.props?.placeholder === "specialist.namePlaceholder")!;
    name.props!.onInput!({ target: { value: "reviewer" } });
    const save = find(form(), (node) => node.type === "button" && node.props?.class === "solid-btn")!;
    const done = save.props!.onClick!() as Promise<void>;

    const other = { kind: "specialist" as const, name: "planner" };
    nestedEditor.value = other;
    const moved = customizeGeneration.value;
    writes.release();
    await done;
    expect(nestedEditor.value).toBe(other);
    expect(customizeGeneration.value).toBe(moved);
  });
});

describe("a Settings write on the tab still shown", () => {
  it("re-reads that tab in place instead of remounting it", async () => {
    mocks.fetch.mockImplementation(() => Promise.resolve(response({ memories: [] })));
    openCust("memory");
    const mounted = customizeGeneration.value;
    const render = () => {
      hookCursor = 0;
      return MemoryTab() as Node;
    };
    render();
    await settle();
    const lists = () => mocks.fetch.mock.calls.filter(([url]) => String(url).startsWith("/api/v1/memory?")).length;
    expect(lists()).toBe(1);

    find(render(), (node) => node.type === "input" && node.props?.placeholder === "cust.memory.contentPlaceholder")!
      .props!.onInput!({ target: { value: "prefers tea" } });
    await (find(render(), (node) => node.type === "button" && text(node) === "common.save")!.props!.onClick!() as Promise<void>);
    render();
    await settle();

    expect(customizeGeneration.value).toBe(mounted);
    expect(lists()).toBe(2);
  });
});
