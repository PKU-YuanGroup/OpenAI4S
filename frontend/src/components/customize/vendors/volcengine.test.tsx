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

// Controls with hooks of their own (or plain leaves): searched and summarised
// as they are, never drawn through.
const LEAVES = new Set(["VolcBtn", "VolcNotice", "VolcExternal", "RecheckButton", "AwaitingCode", "FailedLogin"]);

/** Draw a hook-free component through, so a search sees what it renders. */
function drawThrough(node: Node): unknown {
  const type = node.type as ((props: unknown) => unknown) & { name: string };
  return LEAVES.has(type.name) ? undefined : type(node.props || {});
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
  if (typeof current.type === "function") return find(drawThrough(current), match);
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

// What the panel shows for each connection state: its buttons, notices,
// pickers and markers, in order. Components other than the leaf controls are
// drawn through, so the summary does not depend on how the panel is split
// into components.

function optionValues(node: unknown, out: string[] = []): string[] {
  if (Array.isArray(node)) {
    node.forEach((child) => optionValues(child, out));
    return out;
  }
  if (!node || typeof node !== "object") return out;
  const current = node as Node;
  if (current.type === "option") out.push(String(current.props?.value ?? ""));
  else optionValues(current.props?.children, out);
  return out;
}

function summary(node: unknown, out: string[] = []): string[] {
  if (Array.isArray(node)) {
    node.forEach((child) => summary(child, out));
    return out;
  }
  if (!node || typeof node !== "object") return out;
  const current = node as Node;
  const props = (current.props || {}) as Record<string, unknown>;
  if (typeof current.type === "function") {
    const name = current.type.name;
    if (LEAVES.has(name)) {
      const detail = String(props.label ?? props.title ?? "");
      out.push(detail ? `${name}(${detail})` : name);
      return out;
    }
    return summary(drawThrough(current), out);
  }
  const cls = String(props.class || "");
  if (current.type === "select") {
    out.push(`select=${String(props.value)}[${optionValues(props.children).join(",")}]`);
    return out;
  }
  if (cls === "timeline-error") out.push("error(" + text(props.children) + ")");
  else if (cls === "volc-ready") out.push("ready");
  else if (cls === "volc-key-wait") out.push("wait");
  else if (cls === "volc-login-prep") out.push("prep(" + text(props.children) + ")");
  else if (cls === "volc-quota") out.push("quota");
  else if (cls.startsWith("volc-status")) out.push(cls + "(" + text(props.children) + ")");
  summary(props.children, out);
  return out;
}

const PLAN = { key: "p1", name: "Plan 1" };
const CONNECTED = "volc-status ok(cust.volc.connected)";
const FOOTER = ["RecheckButton", "VolcBtn(cust.volc.switch)"];

type Case = {
  name: string;
  state: Record<string, unknown>;
  shows: string[];
  /** A button to press, and the request it must send. */
  press?: { label: string; path: string; body: Record<string, unknown> };
};

const CASES: Case[] = [
  { name: "not installed", state: { state: "not_installed" }, shows: ["volc-status(cust.volc.notInstalled)", "VolcBtn(cust.volc.getConnector)"] },
  {
    name: "connecting",
    state: { state: "disconnected", login: { state: "connecting" } },
    shows: ["volc-status(cust.volc.disconnected)", "VolcNotice(cust.volc.authTitle)", "VolcBtn(cust.volc.cancel)"],
    press: { label: "cust.volc.cancel", path: "/volcengine/login/cancel", body: {} },
  },
  {
    name: "awaiting a code",
    state: { state: "disconnected", login: { state: "awaiting_code", authorize_url: "https://example.test/auth" } },
    shows: ["volc-status(cust.volc.disconnected)", "AwaitingCode"],
  },
  {
    name: "a failed sign-in",
    state: { state: "disconnected", login: { state: "failed", error_code: "project_selection_required" } },
    shows: ["volc-status(cust.volc.disconnected)", "FailedLogin"],
  },
  {
    name: "signed out after an error",
    state: { state: "expired", _error: "token expired", identity: { name: "me", project_name: "P" } },
    shows: ["volc-status(cust.volc.expired)", "error(token expired)", "prep(cust.volc.reconnectPrep)", "VolcBtn(cust.volc.connect)"],
    press: { label: "cust.volc.connect", path: "/volcengine/login", body: { mode: "device" } },
  },
  {
    name: "no plan",
    state: { state: "connected", access: { state: "no_plan" }, plans: [] },
    shows: [CONNECTED, "VolcNotice(cust.volc.connectedNoAccessTitle)", "VolcExternal(cust.volc.viewPlans)", "VolcExternal(cust.volc.createKey)", ...FOOTER],
  },
  {
    name: "a missing key",
    state: { state: "connected", access: { state: "key_missing" }, plans: [PLAN] },
    shows: [CONNECTED, "VolcNotice(cust.volc.keyMissingTitle)", "VolcExternal(cust.volc.createKey)", ...FOOTER],
  },
  {
    name: "a key choice already configured",
    state: { state: "connected", configured: true, configured_plan_key: "p1", access: { state: "key_choice_required", plan_key: "p1" }, plans: [PLAN] },
    shows: [CONNECTED, "ready", ...FOOTER],
  },
  {
    name: "a key and endpoint choice",
    state: {
      state: "connected",
      access: { state: "key_choice_required", plan_key: "p1", key_choices: [{ id: "k1", name: "Key", suffix: "abcd" }, { id: "k2" }], endpoint_choices: [{ id: "e1", name: "EP" }] },
      plans: [PLAN],
    },
    shows: [CONNECTED, "VolcNotice(cust.volc.keyChoiceTitle)", "select=k1[k1,k2]", "select=e1[e1]", "VolcBtn(cust.volc.usePlan)", ...FOOTER],
    press: { label: "cust.volc.usePlan", path: "/volcengine/configure", body: { plan_key: "p1", api_key_choice: "k1", endpoint_choice: "e1" } },
  },
  {
    name: "a missing profile",
    state: { state: "connected", access: { state: "profile_missing" }, plans: [PLAN] },
    shows: [CONNECTED, "VolcNotice(cust.volc.profileMissingTitle)", "VolcBtn(cust.volc.retrySetup)", ...FOOTER],
    press: { label: "cust.volc.retrySetup", path: "/volcengine/login", body: { mode: "device" } },
  },
  {
    name: "an inactive plan",
    state: { state: "connected", access: { state: "plan_inactive" }, plans: [PLAN] },
    shows: [CONNECTED, "VolcNotice(cust.volc.planInactiveTitle)", "VolcExternal(cust.volc.viewPlans)", ...FOOTER],
  },
  {
    name: "a seat required",
    state: { state: "connected", access: { state: "seat_required" }, plans: [PLAN] },
    shows: [CONNECTED, "VolcNotice(cust.volc.seatTitle)", "VolcExternal(cust.volc.viewPlans)", ...FOOTER],
  },
  {
    name: "quota exhausted",
    state: { state: "connected", access: { state: "quota_exhausted" }, plans: [PLAN] },
    shows: [CONNECTED, "VolcNotice(cust.volc.quotaTitle)", "VolcExternal(cust.volc.viewPlans)", ...FOOTER],
  },
  {
    name: "configured, check failed",
    state: { state: "connected", configured: true, configured_plan_key: "p1", access: { state: "key_check_failed", plan_key: "p1" }, plans: [PLAN] },
    shows: [CONNECTED, "VolcNotice(cust.volc.checkFailedTitle)", "ready", ...FOOTER],
  },
  {
    name: "configured",
    state: { state: "connected", configured: true, configured_plan_key: "p1", access: { state: "ready", plan_key: "p1" }, plans: [PLAN] },
    shows: [CONNECTED, "ready", ...FOOTER],
  },
  {
    name: "a platform endpoint ready",
    state: { state: "connected", access: { state: "platform_ready", endpoint_choice: "e1" }, plans: [] },
    shows: [CONNECTED, "VolcNotice(cust.volc.platformReadyTitle)", "VolcBtn(cust.volc.useEndpoint)", ...FOOTER],
    press: { label: "cust.volc.useEndpoint", path: "/volcengine/configure", body: { plan_key: "platform", endpoint_choice: "e1" } },
  },
  {
    name: "an endpoint choice",
    state: { state: "connected", access: { state: "endpoint_choice_required", endpoint_choices: [{ id: "e1", name: "E1" }, { id: "e2", suffix: "s2" }] }, plans: [] },
    shows: [CONNECTED, "VolcNotice(cust.volc.endpointChoiceTitle)", "select=e1[e1,e2]", "VolcBtn(cust.volc.useEndpoint)", ...FOOTER],
    press: { label: "cust.volc.useEndpoint", path: "/volcengine/configure", body: { plan_key: "platform", endpoint_choice: "e1" } },
  },
  {
    name: "a platform endpoint required",
    state: { state: "connected", access: { state: "platform_endpoint_required" }, plans: [] },
    shows: [CONNECTED, "VolcNotice(cust.volc.platformTitle)", "VolcExternal(cust.volc.openEndpoints)", ...FOOTER],
  },
  {
    name: "a failed check, not configured",
    state: { state: "connected", access: { state: "check_failed" }, plans: [PLAN] },
    shows: [CONNECTED, "VolcNotice(cust.volc.checkFailedTitle)", ...FOOTER],
  },
  {
    name: "a plan to use",
    state: { state: "connected", access: { state: "ready", plan_key: "p1" }, plans: [PLAN] },
    shows: [CONNECTED, "VolcBtn(cust.volc.usePlan)", ...FOOTER],
    press: { label: "cust.volc.usePlan", path: "/volcengine/configure", body: { plan_key: "p1" } },
  },
  {
    name: "two plans with quota, linked",
    state: {
      state: "connected",
      linked: true,
      identity: { name: "me" },
      access: { state: "ready" },
      plans: [PLAN, { key: "p2", name: "Plan 2", tier: "pro" }],
      usage: { items: [{ product: "p1", periods: [{ label: "5h", used: 1, total: 10, reset_at: "2026-01-01T00:00:00Z" }] }] },
    },
    shows: [CONNECTED, "VolcNotice(cust.volc.choiceTitle)", "select=p1[p1,p2]", "quota", "VolcBtn(cust.volc.usePlan)", ...FOOTER, "VolcBtn(cust.volc.disconnect)"],
  },
  {
    name: "a plan whose key must be chosen",
    state: { state: "connected", access: { state: "plan_choice_required" }, plans: [{ key: "p1", key_state: "key_missing" }] },
    shows: [CONNECTED, "VolcNotice(cust.volc.keyMissingTitle)", "VolcExternal(cust.volc.createKey)", ...FOOTER],
  },
];

describe("Volcengine panel states", () => {
  it.each(CASES.map((c) => [c.name, c] as const))("shows %s", async (_name, c) => {
    mocks.fetch.mockImplementation(() => reply(c.state));
    vi.stubGlobal("window", { open: () => null });
    render();
    effects.splice(0).forEach((effect) => effect());
    await flush();
    expect(summary(render())).toEqual(c.shows);
    if (!c.press) return;
    const press = c.press;
    const button = find(render(), (node) => node.props?.label === press.label)!;
    (button.props!.onClick as () => void)();
    await flush();
    const sent = mocks.fetch.mock.calls.find(([url]) => url === "/api/v1" + press.path);
    expect(sent).toBeTruthy();
    expect(JSON.parse(String((sent![1] as RequestInit).body ?? "{}"))).toEqual(press.body);
  });
});
