import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

const mocks = vi.hoisted(() => ({ fetch: vi.fn(), custTab: vi.fn(), alive: (): boolean => true }));
vi.mock("../../i18n", () => ({
  LANG: "en",
  t: (key: string, ...args: unknown[]) => [key, ...args].join(" "),
  tOptional: () => null,
  onLanguageChange: () => () => undefined,
}));
vi.mock("./use-timer-lease", () => ({ useAlive: () => mocks.alive, useTimerLease: () => ({}) }));
vi.mock("../../features/customize/actions", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../features/customize/actions")>()),
  custTab: mocks.custTab,
}));

import { MemoryTab } from "./MemoryTab";

type Node = {
  type?: unknown;
  props?: Record<string, unknown> & {
    children?: unknown;
    disabled?: boolean;
    onClick?: () => unknown;
    onInput?: (e: unknown) => void;
  };
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

function render(): Node {
  hookCursor = 0;
  return MemoryTab() as Node;
}

async function settle(): Promise<void> {
  effects.splice(0).forEach((effect) => effect());
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

const posts = () =>
  mocks.fetch.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === "POST");
const saveButton = () => find(render(), (node) => node.type === "button" && text(node) === "common.save")!;

beforeEach(() => {
  hookState.length = 0;
  hookCursor = 0;
  effects.length = 0;
  mocks.fetch.mockReset();
  mocks.custTab.mockReset();
  vi.stubGlobal("fetch", mocks.fetch);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("MemoryTab first read", () => {
  it("asks for the switch, the memories, the categories and the context at once", async () => {
    mocks.fetch.mockImplementation(() => new Promise<Response>(() => {}));
    render();
    await settle();
    const asked = mocks.fetch.mock.calls.map(([url]) => String(url).replace(/\?.*$/, ""));
    expect(asked.sort()).toEqual([
      "/api/v1/memory",
      "/api/v1/memory/categories",
      "/api/v1/memory/context",
      "/api/v1/memory/enabled",
    ]);
  });
});

describe("MemoryTab add", () => {
  it("saves a memory once however often Save is pressed while the write is in flight", async () => {
    let finish!: (value: Response) => void;
    mocks.fetch.mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === "POST") return new Promise<Response>((resolve) => (finish = resolve));
      if (url.startsWith("/api/v1/memory/enabled")) return Promise.resolve(response({ enabled: true }));
      return Promise.resolve(response({}));
    });
    render();
    await settle();

    find(render(), (node) => node.type === "input" && node.props?.placeholder === "cust.memory.contentPlaceholder")!
      .props!.onInput!({ target: { value: "prefers tea" } });
    void saveButton().props!.onClick!();
    expect(saveButton().props!.disabled).toBe(true);
    void saveButton().props!.onClick!();
    expect(posts()).toHaveLength(1);

    finish(response({ memory_id: "m-1" }));
    await settle();
    expect(posts()).toHaveLength(1);
    expect(saveButton().props!.disabled).toBe(false);
  });
});
