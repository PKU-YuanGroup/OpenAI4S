import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// U2 / PLAN §10 U2 + appendix B.2: the General settings row reads the *local*
// update state on mount (GET, zero outbound) and reaches the release index only
// through an explicit button (POST `{refresh}`). It never installs, downloads,
// restarts or polls, and it never turns a failed/unknown answer into "up to
// date". These are behaviour tests against an HTTP stand-in.

const hookState: unknown[] = [];
let hookCursor = 0;
const effects: Array<() => void> = [];
const cleanups: Array<() => void> = [];

vi.mock("preact/hooks", () => ({
  useState: (initial: unknown) => {
    const index = hookCursor++;
    if (hookState.length === index) hookState.push(initial);
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
  useEffect: (fn: () => void | (() => void), deps: unknown[]) => {
    const index = hookCursor++;
    const old = hookState[index] as unknown[] | undefined;
    if (old && deps.every((value, i) => value === old[i])) return;
    hookState[index] = deps;
    effects.push(() => {
      const cleanup = fn();
      if (typeof cleanup === "function") cleanups.push(cleanup);
    });
  },
}));

const mocks = vi.hoisted(() => ({
  fetch: vi.fn(),
  alive: (): boolean => true,
}));

vi.mock("./use-timer-lease", () => ({
  useAlive: () => mocks.alive,
  useTimerLease: () => ({}),
}));

import { updateT } from "../../features/customize/update";
import { UpdateCheck } from "./UpdateCheck";

type Node = {
  type?: unknown;
  props?: Record<string, unknown> & { children?: unknown; disabled?: boolean; onClick?: () => void };
};
type Call = { path: string; method: string; body?: unknown };
type Reply = { status?: number; body: unknown };
type Button = { action: string; text: string; disabled: boolean; onClick: () => void };

const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

function stubFetch(handler: (call: Call) => Reply): Call[] {
  const calls: Call[] = [];
  mocks.fetch.mockImplementation((url: string, init?: RequestInit) => {
    const call: Call = {
      path: String(url).replace(/^.*\/api\/v1/, ""),
      method: init?.method || "GET",
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    };
    calls.push(call);
    const reply = handler(call);
    return Promise.resolve(response(reply.body, reply.status ?? 200));
  });
  return calls;
}

function textOf(node: unknown): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join(" ");
  return textOf((node as Node).props?.children);
}

function buttons(node: unknown, out: Button[] = []): Button[] {
  if (!node || typeof node !== "object") return out;
  if (Array.isArray(node)) {
    node.forEach((child) => buttons(child, out));
    return out;
  }
  const vnode = node as Node;
  if (vnode.type === "button") {
    out.push({
      action: String(vnode.props?.["data-action"] || ""),
      text: textOf(vnode.props?.children).trim(),
      disabled: Boolean(vnode.props?.disabled),
      onClick: vnode.props?.onClick as () => void,
    });
  }
  buttons(vnode.props?.children, out);
  return out;
}

function render(): Node {
  hookCursor = 0;
  return UpdateCheck() as Node;
}

const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

async function mounted(): Promise<Node> {
  render();
  effects.splice(0).forEach((effect) => effect());
  for (let i = 0; i < 8; i += 1) await Promise.resolve();
  await flush();
  return render();
}

function clock(ms: number): string {
  const date = new Date(ms);
  const pad = (value: number) => String(value).padStart(2, "0");
  return (
    date.getFullYear() +
    "-" +
    pad(date.getMonth() + 1) +
    "-" +
    pad(date.getDate()) +
    " " +
    pad(date.getHours()) +
    ":" +
    pad(date.getMinutes())
  );
}

const CHECK = (overrides: Record<string, unknown> = {}) => ({
  status: "up_to_date",
  reason: "ok",
  latest: "0.3.1",
  source: "pypi",
  checked_at_ms: 1761177600000,
  retry_after_at: 0,
  cached: true,
  age_s: 120,
  previous: {},
  trust: { integrity: "two_witnesses", authorship: "not_established" },
  ...overrides,
});

const STATUS = (overrides: Record<string, unknown> = {}) => ({
  running: "0.3.1",
  channel: { id: "venv", refusal_text_key: "", refusal_command: [] },
  check: CHECK(),
  apply_available: false,
  ...overrides,
});

function checkButton(tree: Node, action: "update-check" | "update-force"): Button {
  const found = buttons(tree).find((button) => button.action === action);
  expect(found, `no ${action} button`).toBeTruthy();
  return found!;
}

beforeEach(() => {
  hookState.length = 0;
  hookCursor = 0;
  effects.length = 0;
  cleanups.length = 0;
  mocks.alive = () => true;
  mocks.fetch.mockReset();
  vi.stubGlobal("fetch", mocks.fetch);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("update card: local status", () => {
  it("reads the local status on mount, without contacting the release index", async () => {
    const calls = stubFetch(() => ({ body: STATUS() }));
    const tree = await mounted();
    expect(calls.filter((call) => call.method === "GET")).toHaveLength(1);
    expect(calls[0]!.path).toBe("/updates/status");
    expect(calls.some((call) => call.method === "POST")).toBe(false);
    const text = textOf(tree);
    expect(text).toContain(updateT("update.title"));
    expect(text).toContain(updateT("update.current"));
    expect(text).toContain("0.3.1");
    expect(text).toContain(updateT("update.channel.venv"));
    expect(text).toContain("from the local cache");
    expect(text).toContain(updateT("update.upToDate"));
    expect(text).toContain(updateT("update.trust.two_witnesses"));
    expect(text).toContain(updateT("update.authorship"));
    expect(text).toContain(updateT("update.only"));
  });

  it("shows the single-witness trust line and never implies authorship", async () => {
    stubFetch(() => ({
      body: STATUS({ check: CHECK({ trust: { integrity: "single_witness", authorship: "not_established" } }) }),
    }));
    const text = textOf(await mounted());
    expect(text).toContain(updateT("update.trust.single_witness"));
    expect(text).toContain(updateT("update.authorship"));
    expect(text).not.toContain(updateT("update.trust.two_witnesses"));
  });

  it("degrades a malformed status payload into 'could not read', with no claim", async () => {
    stubFetch(() => ({ body: { running: 1, channel: {}, check: {}, apply_available: true } }));
    const text = textOf(await mounted());
    expect(text).toContain(updateT("update.readFailed", updateT("update.malformed")));
    expect(text).not.toContain(updateT("update.upToDate"));
    expect(text).not.toContain("A newer version is available");
  });

  it("hides the buttons when even the local status read is refused to members", async () => {
    stubFetch(() => ({ status: 403, body: { error: "admin only", code: "admin_only" } }));
    const tree = await mounted();
    expect(textOf(tree)).toContain(updateT("update.adminOnly"));
    expect(buttons(tree).filter((button) => button.action)).toHaveLength(0);
  });
});

describe("update card: explicit check", () => {
  it("posts one {refresh:false} check and renders the answer", async () => {
    const calls = stubFetch((call) =>
      call.method === "POST"
        ? { body: STATUS({ check: CHECK({ status: "update_available", latest: "0.4.0" }) }) }
        : { body: STATUS() },
    );
    let tree = await mounted();
    checkButton(tree, "update-check").onClick();
    await flush();
    await flush();
    tree = render();
    const posts = calls.filter((call) => call.method === "POST");
    expect(posts).toHaveLength(1);
    expect(posts[0]!.path).toBe("/updates/check");
    expect(posts[0]!.body).toEqual({ refresh: false });
    expect(textOf(tree)).toContain(updateT("update.available", "0.4.0"));
  });

  it("posts {refresh:true} for the force button", async () => {
    const calls = stubFetch((call) =>
      call.method === "POST"
        ? { body: STATUS({ check: CHECK({ status: "update_available", latest: "0.4.0" }) }) }
        : { body: STATUS() },
    );
    const tree = await mounted();
    checkButton(tree, "update-force").onClick();
    await flush();
    await flush();
    const posts = calls.filter((call) => call.method === "POST");
    expect(posts).toHaveLength(1);
    expect(posts[0]!.body).toEqual({ refresh: true });
  });

  it("sends one POST when the button is clicked twice in flight", async () => {
    let release!: (value: unknown) => void;
    const pending = new Promise<Response>((resolve) => {
      release = (body) => resolve(response(body));
    });
    const posts: Call[] = [];
    mocks.fetch.mockImplementation((url: string, init?: RequestInit) => {
      const call: Call = {
        path: String(url).replace(/^.*\/api\/v1/, ""),
        method: init?.method || "GET",
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      };
      if (call.method === "POST") {
        posts.push(call);
        return pending;
      }
      return Promise.resolve(response(STATUS()));
    });
    let tree = await mounted();
    const check = checkButton(tree, "update-check");
    check.onClick();
    check.onClick();
    expect(posts).toHaveLength(1);
    tree = render();
    expect(checkButton(tree, "update-check").disabled).toBe(true);
    expect(checkButton(tree, "update-force").disabled).toBe(true);
    release(STATUS());
    await flush();
    await flush();
    tree = render();
    expect(checkButton(tree, "update-check").disabled).toBe(false);
    expect(posts).toHaveLength(1);
  });

  it("reports a 429 cooldown in seconds and does not retry by itself", async () => {
    const calls = stubFetch((call) =>
      call.method === "POST"
        ? { status: 429, body: { error: "wait", code: "rate_limited", retry_after_s: 45 } }
        : { body: STATUS() },
    );
    let tree = await mounted();
    checkButton(tree, "update-check").onClick();
    await flush();
    await flush();
    tree = render();
    expect(textOf(tree)).toContain(updateT("update.rateLimited", "45s"));
    expect(calls.filter((call) => call.method === "POST")).toHaveLength(1);
    expect(checkButton(tree, "update-check").disabled).toBe(false);
  });

  it("drops the earlier answer's trust line once a check fails", async () => {
    stubFetch((call) =>
      call.method === "POST"
        ? { status: 502, body: { error: "upstream failed", code: "bad_gateway" } }
        : { body: STATUS() },
    );
    let tree = await mounted();
    expect(textOf(tree)).toContain(updateT("update.trust.two_witnesses"));
    checkButton(tree, "update-check").onClick();
    await flush();
    await flush();
    tree = render();
    const text = textOf(tree);
    expect(text).toContain(updateT("update.checkFailed", "").trim());
    expect(text).not.toContain(updateT("update.trust.two_witnesses"));
  });

  it("shows previous and never claims the latest version for an unknown answer", async () => {
    stubFetch((call) =>
      call.method === "POST"
        ? {
            body: STATUS({
              check: CHECK({
                status: "unknown",
                reason: "network_disabled",
                previous: { status: "up_to_date", latest: "0.3.1", checked_at_ms: 1761177600000 },
              }),
            }),
          }
        : { body: STATUS() },
    );
    let tree = await mounted();
    checkButton(tree, "update-check").onClick();
    await flush();
    await flush();
    tree = render();
    const text = textOf(tree);
    expect(text).toContain(updateT("update.unknown"));
    expect(text).toContain(updateT("update.reason", updateT("update.reason.network_disabled")));
    expect(text).toContain(updateT("update.previous", "0.3.1", clock(1761177600000)));
    expect(text).not.toContain(updateT("update.upToDate"));
  });

  it("hides the buttons after an admin_only refusal", async () => {
    stubFetch((call) =>
      call.method === "POST"
        ? { status: 403, body: { error: "admin only", code: "admin_only" } }
        : { body: STATUS() },
    );
    let tree = await mounted();
    checkButton(tree, "update-check").onClick();
    await flush();
    await flush();
    tree = render();
    expect(textOf(tree)).toContain(updateT("update.adminOnly"));
    expect(buttons(tree).filter((button) => button.action)).toHaveLength(0);
  });

  it("offers the manual refusal command as copyable text and executes nothing", async () => {
    const writes: string[] = [];
    vi.stubGlobal("navigator", {
      clipboard: { writeText: async (value: string) => { writes.push(value); } },
    });
    const command = "openai4s env apply python r --repair";
    stubFetch(() => ({
      body: STATUS({
        channel: {
          id: "linux-bundle",
          refusal_text_key: "update.channel.refusal.linux-bundle",
          refusal_command: [command],
        },
      }),
    }));
    let tree = await mounted();
    const text = textOf(tree);
    expect(text).toContain(updateT("update.refusal"));
    expect(text).toContain(command);
    const labels = buttons(tree).map((button) => button.text.toLowerCase()).join(" | ");
    expect(labels).not.toMatch(/install|download|restart|upgrade/);
    const copy = buttons(tree).find((button) => button.text === updateT("update.copy"))!;
    expect(copy).toBeTruthy();
    copy.onClick();
    await flush();
    expect(writes).toEqual([command]);
    tree = render();
    expect(textOf(tree)).toContain(updateT("update.copied"));
  });

  it("drops an answer that arrives after the row was closed", async () => {
    let release!: (value: unknown) => void;
    const pending = new Promise<Response>((resolve) => {
      release = (body) => resolve(response(body));
    });
    mocks.fetch.mockImplementation((_url: string, init?: RequestInit) =>
      (init?.method || "GET") === "GET" ? Promise.resolve(response(STATUS())) : pending,
    );
    const tree = await mounted();
    checkButton(tree, "update-check").onClick();
    const before = hookState.slice();
    cleanups.splice(0).forEach((cleanup) => cleanup());
    release(STATUS({ check: CHECK({ status: "update_available", latest: "9.9.9" }) }));
    await flush();
    await flush();
    expect(hookState.slice(0, before.length)).toEqual(before);
    expect(textOf(render())).not.toContain("9.9.9");
  });
});
