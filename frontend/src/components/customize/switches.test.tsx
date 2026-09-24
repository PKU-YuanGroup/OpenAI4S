import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Settings switches that write server state: disabled until the tab's first
// read lands, one write at a time, and back on the confirmed value when a
// write fails.

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
vi.mock("../../features/customize/actions", () => ({ custTab: vi.fn() }));
vi.mock("../../features/customize/host", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../features/customize/host")>()),
  hint: mocks.hint,
}));

import { MemoryTab } from "./MemoryTab";
import { NetworkTab } from "./NetworkTab";
import { Toggle } from "./ui";

type Node = {
  type?: unknown;
  props?: Record<string, unknown> & { children?: unknown; on?: boolean; disabled?: boolean; onClick?: () => void };
};
type Route = () => Promise<Response>;

const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const ok = (body: unknown): Route => () => Promise.resolve(response(body));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}

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

function routes(table: Record<string, Route>) {
  mocks.fetch.mockImplementation((url: string, init?: RequestInit) => {
    const key = `${init?.method || "GET"} ${url.replace(/^\/api\/v1/, "").split("?")[0]}`;
    const route = table[key];
    return route ? route() : Promise.resolve(response({}));
  });
}

function render(tab: () => unknown): Node {
  hookCursor = 0;
  return tab() as Node;
}
const firstSwitch = (tree: Node) => find(tree, (node) => node.type === Toggle)!;
const writes = () =>
  mocks.fetch.mock.calls
    .filter(([, init]) => (init as RequestInit | undefined)?.method === "PUT")
    .map(([url, init]) => [url, (init as RequestInit).body]);

async function settle(): Promise<void> {
  effects.splice(0).forEach((effect) => effect());
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

beforeEach(() => {
  hookState.length = 0;
  hookCursor = 0;
  effects.length = 0;
  mocks.fetch.mockReset();
  mocks.hint.mockReset();
  vi.stubGlobal("fetch", mocks.fetch);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Network egress switch", () => {
  it("stays disabled until the first read lands, then writes from the value read", async () => {
    const read = deferred<Response>();
    routes({
      "GET /preferences/builtin-allowlist": () => read.promise,
      "PUT /network/status": ok({ enabled: false }),
    });
    render(NetworkTab);
    await settle();

    let egress = firstSwitch(render(NetworkTab));
    expect(egress.props!.disabled).toBe(true);
    egress.props!.onClick!();
    expect(writes()).toEqual([]);

    read.resolve(response({ enabled: true, groups: [] }));
    await settle();
    egress = firstSwitch(render(NetworkTab));
    expect(egress.props!.disabled).toBe(false);
    expect(egress.props!.on).toBe(true);

    egress.props!.onClick!();
    await settle();
    expect(writes()).toEqual([["/api/v1/network/status", JSON.stringify({ enabled: false })]]);
    expect(firstSwitch(render(NetworkTab)).props!.on).toBe(false);
  });
});

describe("Memory switch", () => {
  it("stays disabled until read, ignores a click while writing, and reverts a failed write", async () => {
    const read = deferred<Response>();
    const write = deferred<Response>();
    routes({
      "GET /memory/enabled": () => read.promise,
      "PUT /memory/enabled": () => write.promise,
    });
    render(MemoryTab);
    await settle();

    expect(firstSwitch(render(MemoryTab)).props!.disabled).toBe(true);
    firstSwitch(render(MemoryTab)).props!.onClick!();
    expect(writes()).toEqual([]);

    read.resolve(response({ enabled: false }));
    await settle();
    firstSwitch(render(MemoryTab)).props!.onClick!();
    expect(firstSwitch(render(MemoryTab)).props!.on).toBe(true);
    firstSwitch(render(MemoryTab)).props!.onClick!();
    expect(writes()).toEqual([["/api/v1/memory/enabled", JSON.stringify({ enabled: true })]]);

    write.resolve(response({ error: "store is read-only" }, 500));
    await settle();
    expect(firstSwitch(render(MemoryTab)).props!.on).toBe(false);
    expect(mocks.hint).not.toHaveBeenCalled();
  });
});
