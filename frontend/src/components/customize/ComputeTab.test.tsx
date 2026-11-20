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
  hint: vi.fn(),
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
vi.mock("../../features/customize/host", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../features/customize/host")>()),
  hint: mocks.hint,
}));
vi.mock("../../features/customize/actions", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../features/customize/actions")>()),
  custTab: vi.fn(),
}));

import { createTimerLease, pendingTimerCount, resetTimerLeases } from "../../features/customize/timers";
import { teamUser } from "../../features/chrome/team-identity";
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
  mocks.hint.mockReset();
  mocks.fetch.mockReset().mockImplementation((url: string) =>
    reply(url === "/api/v1/compute/jobs" ? RUNNING : url === "/api/v1/compute/gpu" ? { available: false } : {}),
  );
  vi.stubGlobal("fetch", mocks.fetch);
});
afterEach(() => {
  teamUser.value = null;
  resetTimerLeases();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

/** `render()` with the hook-free row components (`InfoRow`, `CustRow`) rendered too. */
function expanded(node: unknown): unknown {
  if (Array.isArray(node)) return node.map(expanded);
  if (!node || typeof node !== "object") return node;
  const vnode = node as Node;
  if (typeof vnode.type === "function") {
    return expanded((vnode.type as (props: unknown) => unknown)(vnode.props));
  }
  return { ...vnode, props: { ...vnode.props, children: expanded(vnode.props?.children) } };
}

// SET-3 / ENV-3: the tab showed "python kernel · Ready" and nothing about R,
// and an install moved a package count without saying what happened.
describe("Compute runtime environments", () => {
  const LISTING = {
    environments: [{ name: "base", language: "python", builtin: true, is_conda: false }],
    current: "base",
    current_r: null,
    runtimes: {
      python: { available: true, environment: "base", kind: "builtin", version: "3.13.1" },
      r: {
        available: true,
        selected: false,
        environment: "lab_env",
        kind: "local",
        version: "4.4.1",
        checked: true,
        packages: [
          { name: "ggplot2", available: false },
          { name: "dplyr", available: true },
          { name: "jsonlite", available: true },
        ],
      },
    },
  };
  const STATUS = {
    environments: [
      {
        language: "python",
        status: "ready",
        package_count: 1,
        packages: [
          { name: "numpy", installed: true, version: "2.5.0" },
          { name: "requests", installed: false, version: null },
        ],
      },
    ],
  };

  function serve(install: unknown) {
    mocks.fetch.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/v1/kernel/install" && init?.method === "POST") return reply(install);
      if (url === "/api/v1/environments") return reply(LISTING);
      if (url === "/api/v1/environments/status") return reply(STATUS);
      return reply(url === "/api/v1/compute/jobs" ? { jobs: [] } : {});
    });
  }

  async function load() {
    render();
    effects.splice(0).forEach((effect) => effect());
    await flush();
  }

  it("shows the Python and the R in use: name, where it comes from, version", async () => {
    serve({});
    await load();
    const shown = text(expanded(render()));

    expect(shown).toContain("base · built-in · Python 3.13.1");
    expect(shown).toContain("lab_env · local · R 4.4.1");
    expect(shown).toContain("ggplot2 is not available");
    expect(shown).toContain("dplyr loads");
    // The tracked packages carry their versions and no "networking" label.
    expect(shown).toContain("1 of 2 installed: numpy 2.5.0");
    expect(shown).toContain("Not installed: requests");
    expect(shown).not.toContain("cust.compute.preinstalledDetail");
  });

  it("says what an install did: the version, the environment, the kernel", async () => {
    serve({
      ok: true,
      installed: ["openpyxl"],
      installed_versions: [{ name: "openpyxl", version: "3.1.5" }],
    });
    await load();
    find(render(), (node) => node.type === "input" && node.props?.placeholder === "cust.compute.installPlaceholder")!
      .props!.onInput!({ target: { value: "openpyxl" } });
    await button("cust.compute.installBtn").props!.onClick!();
    await flush();

    const status = find(expanded(render()), (node) => node.props?.role === "status")!;
    expect(text(status)).toContain("Installed into the built-in environment (base): openpyxl 3.1.5.");
    expect(text(status)).toContain("must be restarted to see them");
    expect(status.props!.class).toContain("ok");
  });

  it("keeps a failed install's reason on screen", async () => {
    serve({ ok: false, installed: [], failed: [{ name: "nope", error: "no matching distribution" }] });
    await load();
    find(render(), (node) => node.type === "input" && node.props?.placeholder === "cust.compute.installPlaceholder")!
      .props!.onInput!({ target: { value: "nope" } });
    await button("cust.compute.installBtn").props!.onClick!();
    await flush();

    const status = find(expanded(render()), (node) => node.props?.role === "status")!;
    expect(text(status)).toBe("Install failed: no matching distribution");
    expect(status.props!.class).toContain("err");
  });
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

  it("reports a failed cancel and still re-reads the jobs", async () => {
    // A cancel that could not stop the job is a 500 now. It used to land in
    // an empty catch that also skipped the re-read, so nothing said it failed.
    mocks.fetch.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/v1/compute/jobs/job-1/cancel" && init?.method === "POST") {
        const body = { error: "the job is still running: killed", code: "job_cancel_failed" };
        return Promise.resolve({ ok: false, status: 500, text: () => Promise.resolve(JSON.stringify(body)) });
      }
      return reply(url === "/api/v1/compute/jobs" ? RUNNING : url === "/api/v1/compute/gpu" ? { available: false } : {});
    });
    render();
    effects.splice(0).forEach((effect) => effect());
    await flush();
    expect(jobReads()).toBe(1);

    await button("common.cancel").props!.onClick!();
    await flush();
    expect(mocks.hint).toHaveBeenCalledWith("toast.failed the job is still running: killed", true);
    expect(jobReads()).toBe(2);
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

describe("Compute tab for a team member (TEAM-24)", () => {
  const requested = () => mocks.fetch.mock.calls.map(([url]) => String(url));
  const memberNote = () => find(render(), (node) => node.props?.["data-compute-member-note"] === "");
  const jobSubmit = () =>
    find(render(), (node) => node.type === "input" && node.props?.placeholder === "cust.jobs.cmdPlaceholder");

  it("asks for neither the SSH hosts nor the daemon jobs, and shows the local facts only", async () => {
    teamUser.value = { username: "bob", role: "member", kind: "user" };
    render();
    effects.splice(0).forEach((effect) => effect());
    await flush();
    expect(requested()).toContain("/api/v1/compute/gpu");
    expect(requested()).toContain("/api/v1/compute/local/hostinfo");
    expect(requested()).not.toContain("/api/v1/compute/remote");
    expect(requested()).not.toContain("/api/v1/compute/jobs");
    expect(memberNote()).not.toBeNull();
    expect(jobSubmit()).toBeNull();
    expect(find(render(), (node) => node.type === "select")).toBeNull();
  });

  it("an admin still gets the remote hosts and the jobs", async () => {
    teamUser.value = { username: "alice", role: "admin", kind: "user" };
    render();
    effects.splice(0).forEach((effect) => effect());
    await flush();
    expect(requested()).toContain("/api/v1/compute/remote");
    expect(jobReads()).toBe(1);
    expect(memberNote()).toBeNull();
    expect(jobSubmit()).not.toBeNull();
  });

  it("falls back to the member view when the server refuses the host list as admin-only", async () => {
    // The identity probe has not answered yet: the server's refusal decides.
    mocks.fetch.mockImplementation((url: string) => {
      if (url === "/api/v1/compute/remote") {
        const body = { error: "admin only", code: "admin_only", status: 403 };
        return Promise.resolve({ ok: false, status: 403, text: () => Promise.resolve(JSON.stringify(body)) });
      }
      return reply(url === "/api/v1/compute/jobs" ? RUNNING : {});
    });
    render();
    effects.splice(0).forEach((effect) => effect());
    await flush();
    expect(jobReads()).toBe(0);
    expect(memberNote()).not.toBeNull();
    expect(jobSubmit()).toBeNull();
  });
});
