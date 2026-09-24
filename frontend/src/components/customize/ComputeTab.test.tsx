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

const mocks = vi.hoisted(() => ({
  fetch: vi.fn(),
  alive: (): boolean => true,
  lease: null as unknown,
}));
vi.mock("../../i18n", () => ({
  LANG: "en",
  t: (key: string, ...args: unknown[]) => [key, ...args].join(" "),
  tOptional: () => null,
  onLanguageChange: () => () => undefined,
}));
vi.mock("./use-timer-lease", () => ({ useAlive: () => mocks.alive, useTimerLease: () => mocks.lease }));
vi.mock("../../features/customize/actions", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../features/customize/actions")>()),
  custTab: vi.fn(),
}));

import { createTimerLease, pendingTimerCount, resetTimerLeases } from "../../features/customize/timers";
import { ComputeTab } from "./ComputeTab";

type Node = {
  type?: unknown;
  props?: Record<string, unknown> & { children?: unknown; onClick?: () => unknown; onInput?: (e: unknown) => void };
};

const RUNNING = { jobs: [{ id: "job-1", kind: "bash", command: "sleep 60", status: "running" }] };

/** The smallest `Response` `api()` reads: no stream, so no timers of its own. */
const reply = (body: unknown) => Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) });

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
  return ComputeTab() as Node;
}

async function flush(): Promise<void> {
  for (let i = 0; i < 30; i += 1) await Promise.resolve();
}

const jobReads = () =>
  mocks.fetch.mock.calls.filter(
    ([url, init]) => url === "/api/v1/compute/jobs" && !(init as RequestInit | undefined)?.method,
  ).length;
const button = (label: string) => find(render(), (node) => node.type === "button" && text(node) === label)!;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  hookState.length = 0;
  hookCursor = 0;
  effects.length = 0;
  mocks.lease = createTimerLease();
  mocks.fetch.mockReset().mockImplementation((url: string) =>
    reply(url === "/api/v1/compute/jobs" ? RUNNING : url === "/api/v1/compute/gpu" ? { available: false } : {}),
  );
  vi.stubGlobal("fetch", mocks.fetch);
});
afterEach(() => {
  resetTimerLeases();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Compute job polling", () => {
  it("keeps one poll chain however many times submit and cancel re-read the jobs", async () => {
    render();
    effects.splice(0).forEach((effect) => effect());
    await flush();
    expect(jobReads()).toBe(1);
    expect(pendingTimerCount()).toBe(1);

    find(render(), (node) => node.type === "input" && node.props?.placeholder === "cust.jobs.cmdPlaceholder")!
      .props!.onInput!({ target: { value: "sleep 5" } });
    await button("cust.jobs.runBtn").props!.onClick!();
    await flush();
    await button("common.cancel").props!.onClick!();
    await flush();
    expect(jobReads()).toBe(3);
    expect(pendingTimerCount()).toBe(1);

    await vi.advanceTimersByTimeAsync(1500);
    await flush();
    expect(jobReads()).toBe(4);
    expect(pendingTimerCount()).toBe(1);
  });

  it("drops an older job read that answers after a newer one", async () => {
    let answerFirst!: (body: unknown) => void;
    let reads = 0;
    mocks.fetch.mockImplementation((url: string, init?: RequestInit) => {
      if (url !== "/api/v1/compute/jobs" || init?.method) return reply({});
      reads += 1;
      if (reads > 1) return reply({ jobs: [{ id: "job-2", kind: "bash", command: "true", status: "done" }] });
      return new Promise((resolve) => {
        answerFirst = (body) => resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) });
      });
    });
    render();
    effects.splice(0).forEach((effect) => effect());
    await flush();

    find(render(), (node) => node.type === "input" && node.props?.placeholder === "cust.jobs.cmdPlaceholder")!
      .props!.onInput!({ target: { value: "true" } });
    await button("cust.jobs.runBtn").props!.onClick!();
    await flush();
    answerFirst(RUNNING);
    await flush();

    const listed = text(find(render(), (node) => node.props?.class === "job-list"));
    expect(listed).toContain("done");
    expect(listed).not.toContain("sleep 60");
    expect(pendingTimerCount()).toBe(0);
  });
});
