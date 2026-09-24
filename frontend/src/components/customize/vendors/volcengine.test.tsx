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

const mocks = vi.hoisted(() => ({ fetch: vi.fn(), alive: (): boolean => true, lease: null as unknown }));
vi.mock("../../../i18n", () => ({
  LANG: "en",
  t: (key: string, ...args: unknown[]) => [key, ...args].join(" "),
  tOptional: () => null,
  onLanguageChange: () => () => undefined,
}));
vi.mock("../use-timer-lease", () => ({ useAlive: () => mocks.alive, useTimerLease: () => mocks.lease }));

import {
  VOLC_KEY_POLL_EVERY_MS,
  VOLC_KEY_POLL_FIRST_MS,
  VOLC_KEY_POLL_MAX,
} from "../../../features/customize/volcengine";
import { createTimerLease, resetTimerLeases } from "../../../features/customize/timers";
import { VolcenginePanel } from "./volcengine";

type Node = { type?: unknown; props?: Record<string, unknown> & { children?: unknown } };

/** The smallest `Response` `api()` reads: no stream, so no timers of its own. */
const reply = (body: unknown) =>
  Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) });

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
  return VolcenginePanel() as Node;
}

async function flush(): Promise<void> {
  for (let i = 0; i < 30; i += 1) await Promise.resolve();
}

const connection = (name: string) => ({
  state: "connected",
  linked: true,
  identity: { name },
  access: { state: "key_missing" },
  plans: [],
});

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  hookState.length = 0;
  hookCursor = 0;
  effects.length = 0;
  mocks.lease = createTimerLease();
  mocks.fetch.mockReset();
  vi.stubGlobal("fetch", mocks.fetch);
});
afterEach(() => {
  resetTimerLeases();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Volcengine sign-in", () => {
  it("opens one sign-in tab however often Connect is pressed, and does not leave it blank", async () => {
    let answer!: (body: unknown) => void;
    mocks.fetch.mockImplementation((url: string) =>
      url.endsWith("/volcengine/login")
        ? new Promise((resolve) => {
            answer = (body) => resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) });
          })
        : reply({ state: "disconnected" }),
    );
    const tab = { closed: false, opener: {} as unknown, close: vi.fn(), focus: vi.fn(), location: { href: "" } };
    const open = vi.fn(() => tab);
    vi.stubGlobal("window", { open });
    render();
    effects.splice(0).forEach((effect) => effect());
    await flush();

    const connect = () => find(render(), (node) => node.props?.label === "cust.volc.connect")!;
    (connect().props!.onClick as () => void)();
    (connect().props!.onClick as () => void)();
    expect(open).toHaveBeenCalledTimes(1);
    expect(mocks.fetch.mock.calls.filter(([url]) => String(url).endsWith("/volcengine/login"))).toHaveLength(1);

    // The daemon answers a start already in progress without an authorize URL.
    answer({ state: "connecting", login_id: "volc-login-1" });
    await flush();
    expect(tab.close).toHaveBeenCalledTimes(1);
    expect(tab.location.href).toBe("");
  });
});

describe("Volcengine key wait", () => {
  it("keeps what the last recheck read when the wait runs out", async () => {
    mocks.fetch.mockImplementation((url: string) =>
      reply(url.endsWith("/volcengine/refresh") ? connection("Latest project") : connection("First project")),
    );
    render();
    effects.splice(0).forEach((effect) => effect());
    await flush();
    expect(text(find(render(), (node) => node.props?.class === "volc-head"))).toContain("First project");

    const createKey = find(render(), (node) => typeof node.props?.onOpen === "function")!;
    (createKey.props!.onOpen as () => void)();
    await vi.advanceTimersByTimeAsync(VOLC_KEY_POLL_FIRST_MS);
    await flush();
    for (let i = 1; i < VOLC_KEY_POLL_MAX; i += 1) {
      await vi.advanceTimersByTimeAsync(VOLC_KEY_POLL_EVERY_MS);
      await flush();
    }
    const rechecks = mocks.fetch.mock.calls.filter(([url]) => String(url).endsWith("/volcengine/refresh"));
    expect(rechecks).toHaveLength(VOLC_KEY_POLL_MAX);

    const head = text(find(render(), (node) => node.props?.class === "volc-head"));
    expect(head).toContain("Latest project");
    expect(head).not.toContain("First project");
  });
});
