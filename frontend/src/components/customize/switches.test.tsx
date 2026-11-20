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

const mocks = vi.hoisted(() => ({
  fetch: vi.fn(),
  hint: vi.fn(),
  alive: (): boolean => true,
  notebookReread: vi.fn(),
}));
vi.mock("../../i18n", () => ({
  LANG: "en",
  t: (key: string, ...args: unknown[]) => [key, ...args].join(" "),
  tOptional: () => null,
  onLanguageChange: () => () => undefined,
}));
vi.mock("./use-timer-lease", () => ({ useAlive: () => mocks.alive, useTimerLease: () => ({}) }));
vi.mock("../../features/customize/actions", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../features/customize/actions")>()),
  custTab: vi.fn(),
}));
vi.mock("../../features/customize/host", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../features/customize/host")>()),
  hint: mocks.hint,
}));

vi.mock("../../features/notebook", () => ({ notebookOnTurnDone: mocks.notebookReread }));

import { teamUser } from "../../features/chrome/team-identity";
import { ConnectorRow } from "./ConnectorsTab";
import { MemoryTab } from "./MemoryTab";
import { NetworkTab, egressRestartCommand } from "./NetworkTab";
import { NotebookReplSetting } from "./NotebookReplSetting";
import { SkillRow } from "./SkillsTab";
import { BuiltinRow } from "./SpecialistsTab";
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

describe("Notebook manual-cells switch (NB-5)", () => {
  it("writes the switch and makes the open Notebook re-read it", async () => {
    routes({
      "GET /notebook/settings": ok({ repl_enabled: false, env_forced: false, can_change: true }),
      "PUT /notebook/settings": ok({ repl_enabled: true, env_forced: false, can_change: true }),
    });
    render(NotebookReplSetting);
    await settle();
    const toggle = firstSwitch(render(NotebookReplSetting));
    expect(toggle.props!.disabled).toBe(false);
    expect(toggle.props!.on).toBe(false);
    toggle.props!.onClick!();
    await settle();
    expect(writes()).toEqual([["/api/v1/notebook/settings", JSON.stringify({ repl_enabled: true })]]);
    expect(firstSwitch(render(NotebookReplSetting)).props!.on).toBe(true);
    expect(mocks.notebookReread).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["forced on by the environment", { repl_enabled: true, env_forced: true, can_change: false }],
    ["a team member", { repl_enabled: false, env_forced: false, can_change: false }],
  ])("is locked for %s", async (_who, body) => {
    routes({ "GET /notebook/settings": ok(body) });
    render(NotebookReplSetting);
    await settle();
    const toggle = firstSwitch(render(NotebookReplSetting));
    expect(toggle.props!.disabled).toBe(true);
    toggle.props!.onClick!();
    await settle();
    expect(writes()).toEqual([]);
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
    // Said, not only flipped back (TEAM-23).
    expect(mocks.hint).toHaveBeenCalledWith("toast.failed store is read-only", true);
  });

  it("says who may change it when a team member is refused (TEAM-23)", async () => {
    routes({
      "GET /memory/enabled": ok({ enabled: false }),
      "PUT /memory/enabled": () => Promise.resolve(response({ error: "admin only", code: "admin_only" }, 403)),
    });
    render(MemoryTab);
    await settle();
    firstSwitch(render(MemoryTab)).props!.onClick!();
    await settle();
    expect(firstSwitch(render(MemoryTab)).props!.on).toBe(false);
    const [message, error] = mocks.hint.mock.calls.at(-1)!;
    expect(error).toBe(true);
    expect(message).toMatch(/Only an administrator/);
  });
});

describe.each([
  ["Skill", (on: boolean) => SkillRow({ s: { name: "fold", enabled: on }, pid: null }), "/skills/catalog/fold/enabled"],
  ["specialist", (on: boolean) => BuiltinRow({ ag: { name: "planner", enabled: on } }), "/agents/planner/enabled"],
  ["connector", (on: boolean) => ConnectorRow({ k: { connector_id: "c1", name: "Files", enabled: on } }), "/connectors/c1/enabled"],
] as const)("%s row switch", (_kind, row, path) => {
  it("shows what a later read brings and keeps a write the server took", async () => {
    const write = deferred<Response>();
    routes({ [`PUT ${path}`]: () => write.promise });
    expect(firstSwitch(render(() => row(true))).props!.on).toBe(true);

    // The tab re-read in place, and the server now says off.
    expect(firstSwitch(render(() => row(false))).props!.on).toBe(false);

    firstSwitch(render(() => row(false))).props!.onClick!();
    firstSwitch(render(() => row(false))).props!.onClick!();
    expect(writes()).toEqual([["/api/v1" + path, JSON.stringify({ enabled: true })]]);
    write.resolve(response({ ok: true }));
    await settle();
    // The last read (off) is older than the write it did not see.
    expect(firstSwitch(render(() => row(false))).props!.on).toBe(true);
  });
});

// TEAM-23: a member's server-wide switches answered 403 with nothing on screen.
describe("server-wide switches for a team member", () => {
  afterEach(() => {
    teamUser.value = null;
  });

  it.each([
    ["App networking", NetworkTab, { "GET /preferences/builtin-allowlist": ok({ enabled: true, groups: [] }) }],
    ["Memory", MemoryTab, { "GET /memory/enabled": ok({ enabled: true }) }],
  ] as const)("%s is shown, locked, and never written", async (_name, tab, table) => {
    teamUser.value = { username: "bob", role: "member" };
    routes(table);
    render(tab);
    await settle();
    const toggle = firstSwitch(render(tab));
    expect(toggle.props!.on).toBe(true);
    expect(toggle.props!.disabled).toBe(true);
    toggle.props!.onClick!();
    await settle();
    expect(writes()).toEqual([]);
  });

  it("stays usable for an admin", async () => {
    teamUser.value = { username: "alice", role: "admin" };
    routes({ "GET /memory/enabled": ok({ enabled: true }) });
    render(MemoryTab);
    await settle();
    expect(firstSwitch(render(MemoryTab)).props!.disabled).toBe(false);
  });
});

// M-26 (lead decision: no runtime switch): the page names the egress state and
// hands over the exact restart command; a team member gets the admin wording.
describe("egress allowlist restart command", () => {
  afterEach(() => {
    teamUser.value = null;
  });
  const text = (node: unknown): string => {
    if (Array.isArray(node)) return node.map(text).join("");
    if (node == null || typeof node === "boolean") return "";
    if (typeof node !== "object") return String(node);
    const current = node as Node;
    if (typeof current.type === "function") return text((current.type as (p: unknown) => unknown)(current.props));
    return text(current.props?.children);
  };
  const groups = [{ name: "Web search", domains: ["example.org"] }];

  it("names the command that switches to the other mode", () => {
    expect(egressRestartCommand("off")).toBe("openai4s stop && OPENAI4S_EGRESS=allowlist openai4s serve");
    expect(egressRestartCommand("allowlist")).toBe("openai4s stop && OPENAI4S_EGRESS=off openai4s serve");
  });

  it("offers the command with a copy button, and only the reason to a member", async () => {
    routes({ "GET /preferences/builtin-allowlist": ok({ enabled: true, groups, egress_mode: "off" }) });
    render(NetworkTab);
    await settle();
    let page = text(render(NetworkTab));
    expect(page).toContain("OPENAI4S_EGRESS=allowlist openai4s serve");
    expect(page).toContain("Copy start command");
    teamUser.value = { username: "bob", role: "member" };
    page = text(render(NetworkTab));
    expect(page).not.toContain("openai4s serve");
    expect(page).toContain("only an administrator");
  });
});
