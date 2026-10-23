import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// U2 / §1 U1 DTO: the General Settings card reads the *local* update state on
// mount (GET status, zero outbound) and contacts the release index only when
// the user presses the button (POST check). It never downloads, installs,
// restarts, polls, or presents a failed check as "up to date".

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

vi.mock("../../i18n", () => ({
  LANG: "en",
  t: (key: string, ...args: unknown[]) => [key, ...args].join(" "),
  tOptional: () => null,
}));
vi.mock("./use-timer-lease", () => ({
  useAlive: () => mocks.alive,
  useTimerLease: () => ({}),
}));

import { UpdateCheck } from "./UpdateCheck";

type Node = {
  type?: unknown;
  props?: Record<string, unknown> & { children?: unknown; disabled?: boolean; onClick?: () => void };
};

const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

function text(node: unknown): string {
  if (Array.isArray(node)) return node.map(text).join(" ");
  if (typeof node === "string" || typeof node === "number") return String(node);
  return node && typeof node === "object" ? text((node as Node).props?.children) : "";
}

function render(): Node {
  hookCursor = 0;
  return UpdateCheck() as Node;
}

function nodes(node: unknown, match: (node: Node) => boolean, out: Node[] = []): Node[] {
  if (Array.isArray(node)) {
    node.forEach((child) => nodes(child, match, out));
    return out;
  }
  if (!node || typeof node !== "object") return out;
  const current = node as Node;
  if (match(current)) out.push(current);
  nodes(current.props?.children, match, out);
  return out;
}

function buttons(node: unknown): Node[] {
  return nodes(node, (current) => current.type === "button");
}

function buttonByText(node: unknown, label: string): Node | null {
  return buttons(node).find((button) => text(button).trim() === label) ?? null;
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

function calls(): Array<{ path: string; method: string; body?: unknown }> {
  return mocks.fetch.mock.calls.map(
    ([url, init]: unknown[]) => ({
      path: String(url).replace(/^.*\/api\/v1/, ""),
      method: (init as RequestInit | undefined)?.method || "GET",
      body: (init as RequestInit | undefined)?.body
        ? JSON.parse(String((init as RequestInit).body))
        : undefined,
    }),
  );
}

function route(
  responders: Record<string, (init?: RequestInit) => Response | Promise<Response>>,
): void {
  mocks.fetch.mockImplementation((url: string, init?: RequestInit) => {
    const exact = Object.keys(responders).find(
      (path) => String(url) === "/api/v1" + path,
    );
    if (!exact) return Promise.resolve(response({ error: "nope" }, 404));
    return Promise.resolve(responders[exact]!(init));
  });
}

const STATUS_BODY = {
  request_id: "req-status",
  channel: { id: "venv" },
  check: {
    status: "up_to_date",
    reason: "ok",
    running: "0.3.1",
    latest: "0.3.1",
    checked_at_ms: 1761177600000,
    cached: true,
    age_s: 120,
    retry_after_at: 0,
    previous: {},
    trust: {
      integrity: "integrity: verified against two independent witnesses",
      authorship: "not established",
    },
  },
  apply_available: false,
};

async function mounted(): Promise<Node> {
  render();
  effects.splice(0).forEach((effect) => effect());
  for (let i = 0; i < 8; i += 1) await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
  return render();
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
  vi.useRealTimers();
});

describe("update check card: local state", () => {
  it("reads the local update state on mount without contacting the update source", async () => {
    route({
      "/updates/status": () => response(STATUS_BODY),
    });
    const tree = await mounted();
    const seen = calls();
    expect(seen).toContainEqual({ path: "/updates/status", method: "GET", body: undefined });
    // Opening the card must not start a check.
    expect(seen.some((call) => call.method === "POST")).toBe(false);

    const contentText = text(tree);
    expect(contentText).toContain("0.3.1"); // current version from check.running
    expect(contentText).toContain("Python environment"); // channel label
    expect(contentText).toContain("local cache");
    expect(contentText).toContain("does not download");
  });

  it("says it has not checked yet from U1's no-record projection", async () => {
    // Real GET /updates/status shape with no cache: a full check object with
    // status unknown, reason not_checked, checked_at_ms 0 and cached false.
    route({
      "/updates/status": () =>
        response({
          request_id: "r1",
          channel: { id: "venv" },
          check: {
            status: "unknown",
            reason: "not_checked",
            running: "0.3.1",
            latest: "",
            checked_at_ms: 0,
            cached: false,
            age_s: null,
            retry_after_at: 0,
            previous: {},
            trust: {},
          },
          apply_available: false,
        }),
    });
    const tree = await mounted();
    expect(text(tree)).toContain("Not checked yet");
    expect(text(tree)).toContain("0.3.1"); // current version still known
    expect(text(tree)).not.toContain("Could not confirm");
    expect(calls().some((call) => call.method === "POST")).toBe(false);
  });

  it("also treats a missing check object as not checked yet", async () => {
    route({
      "/updates/status": () =>
        response({ request_id: "r1", channel: { id: "venv" }, check: null, apply_available: false }),
    });
    const tree = await mounted();
    expect(text(tree)).toContain("Not checked yet");
  });

  it("does not offer download, install, restart or one-click upgrade actions", async () => {
    route({ "/updates/status": () => response(STATUS_BODY) });
    const tree = await mounted();
    const labels = buttons(tree).map((button) => text(button).toLowerCase());
    expect(labels.join(" | ")).toContain("check for updates");
    expect(labels.join(" | ")).not.toMatch(/install|upgrade|restart|download/);
    expect(text(tree)).toContain("only provides");
  });
});

describe("update check card: explicit check", () => {
  it("posts exactly one check on a double click and reports the result", async () => {
    let finish!: (value: Response) => void;
    route({
      "/updates/status": () => response(STATUS_BODY),
      "/updates/check": () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    });
    let tree = await mounted();
    const check = buttonByText(tree, "Check for updates")!;
    expect(check).toBeTruthy();
    (check.props!.onClick as () => void)();
    // A second click while the first is in flight is one check.
    tree = render();
    const again = buttonByText(tree, "Checking…")!;
    expect(again.props!.disabled).toBe(true);
    (again.props!.onClick as () => void)();
    const posts = calls().filter((call) => call.method === "POST");
    expect(posts).toHaveLength(1);
    expect(posts[0]!.path).toBe("/updates/check");
    expect(posts[0]!.body).toEqual({ refresh: true });

    finish(
      response({
        request_id: "req-check",
        channel: { id: "venv" },
        check: {
          status: "update_available",
          reason: "ok",
          running: "0.3.1",
          latest: "0.4.0",
          checked_at_ms: 1761177700000,
          cached: false,
          age_s: 0,
          retry_after_at: 0,
          previous: {},
          trust: { integrity: "integrity: verified", authorship: "not established" },
        },
        apply_available: false,
      }),
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
    tree = render();
    const contentText = text(tree);
    expect(contentText).toContain("A newer version is available");
    expect(contentText).toContain("0.4.0");
    expect(calls().filter((call) => call.method === "POST")).toHaveLength(1);
  });

  it("never reads a failed check as up to date and keeps the previous answer with its time", async () => {
    route({
      "/updates/status": () => response(STATUS_BODY),
      "/updates/check": () =>
        response(
          {
            request_id: "req-fail",
            channel: { id: "venv" },
            check: {
              status: "unknown",
              reason: "network_disabled",
              running: "0.3.1",
              latest: "",
              checked_at_ms: 1761177800000,
              cached: false,
              age_s: 0,
              retry_after_at: 1761177900000,
              previous: {
                status: "up_to_date",
                latest: "0.3.1",
                checked_at_ms: 1761177600000,
              },
              trust: null,
            },
            apply_available: false,
          },
          200,
        ),
    });
    let tree = await mounted();
    (buttonByText(tree, "Check for updates")!.props!.onClick as () => void)();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
    tree = render();
    const contentText = text(tree);
    expect(contentText).toContain("Could not confirm");
    expect(contentText).not.toContain("This is the latest version");
    expect(contentText).toContain("Last known 0.3.1");
    expect(contentText).toContain(clock(1761177600000));
  });

  it("names the admin-only refusal instead of inventing a result", async () => {
    route({
      "/updates/status": () => response(STATUS_BODY),
      "/updates/check": () =>
        response({ error: "admin only", code: "admin_only" }, 403),
    });
    let tree = await mounted();
    (buttonByText(tree, "Check for updates")!.props!.onClick as () => void)();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
    tree = render();
    expect(text(tree)).toContain("limited to administrators");
    expect(text(tree)).not.toContain("A newer version is available");
    expect(text(tree)).not.toContain("This is the latest version");
  });

  it("reports U1's 429 cooldown in seconds and does not retry by itself", async () => {
    route({
      "/updates/status": () => response(STATUS_BODY),
      "/updates/check": () =>
        response(
          {
            error: "wait before forcing another update check",
            code: "rate_limited",
            reason: "refresh_cooldown",
            retry_after_s: 45,
          },
          429,
        ),
    });
    let tree = await mounted();
    (buttonByText(tree, "Check for updates")!.props!.onClick as () => void)();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
    tree = render();
    const contentText = text(tree);
    expect(contentText).toContain("Too many checks");
    expect(contentText).toContain("45s");
    expect(calls().filter((call) => call.method === "POST")).toHaveLength(1);
    // The button is usable again for a later, explicit retry.
    expect(buttonByText(tree, "Check for updates")).not.toBeNull();
  });

  it("names the admin-only refusal when even the local status read is refused", async () => {
    route({
      "/updates/status": () =>
        response({ error: "admin only", code: "admin_only" }, 403),
    });
    const tree = await mounted();
    expect(text(tree)).toContain("limited to administrators");
    expect(text(tree)).not.toContain("Could not read the local update state");
    expect(text(tree)).not.toContain("This is the latest version");
  });

  it("times out as unknown, not as up to date", async () => {
    route({
      "/updates/status": () => response(STATUS_BODY),
      "/updates/check": (_init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          const signal = _init?.signal;
          signal?.addEventListener("abort", () => reject(new Error("aborted")));
        }),
    });
    let tree = await mounted();
    vi.useFakeTimers();
    (buttonByText(tree, "Check for updates")!.props!.onClick as () => void)();
    await vi.advanceTimersByTimeAsync(31_000);
    tree = render();
    const contentText = text(tree);
    expect(contentText).toContain("timed out");
    expect(contentText).not.toContain("This is the latest version");
  });

  it("drops an answer that arrives after the card was closed", async () => {
    let finish!: (value: Response) => void;
    route({
      "/updates/status": () => response(STATUS_BODY),
      "/updates/check": () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    });
    let tree = await mounted();
    (buttonByText(tree, "Check for updates")!.props!.onClick as () => void)();
    const before = hookState.slice();
    // Unmount the card (General tab switched away).
    cleanups.splice(0).forEach((cleanup) => cleanup());
    finish(
      response({
        request_id: "late",
        channel: { id: "venv" },
        check: {
          status: "update_available",
          reason: "ok",
          running: "0.3.1",
          latest: "9.9.9",
          checked_at_ms: 1761177700000,
          cached: false,
          age_s: 0,
          retry_after_at: 0,
          previous: {},
          trust: null,
        },
        apply_available: false,
      }),
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
    // No state was written after unmount; re-rendering still shows the old tree.
    expect(hookState.slice(0, before.length)).toEqual(before);
    tree = render();
    expect(text(tree)).not.toContain("9.9.9");
  });
});
