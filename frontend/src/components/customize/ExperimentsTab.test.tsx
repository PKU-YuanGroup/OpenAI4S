import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const hookState: unknown[] = [];
let hookCursor = 0;
const effects: Array<() => void> = [];

vi.mock("preact/hooks", () => ({
  useState: (initial: unknown) => {
    const index = hookCursor++;
    if (hookState.length === index) hookState.push(initial);
    return [
      hookState[index],
      (value: unknown) => {
        hookState[index] =
          typeof value === "function"
            ? (value as (prev: unknown) => unknown)(hookState[index])
            : value;
      },
    ];
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
  hint: vi.fn(),
}));

vi.mock("../../i18n", () => ({ LANG: "en", t: (key: string) => key }));
vi.mock("../../i18n/runtime", () => ({
  LANG: "en",
  tOptional: () => null,
  t: (key: string) => key,
}));
vi.mock("./use-timer-lease", () => ({ useAlive: () => mocks.alive }));
vi.mock("../../features/customize/host", () => ({ hint: (...args: unknown[]) => mocks.hint(...args) }));

import { ExperimentsTab } from "./ExperimentsTab";

type Node = {
  type?: unknown;
  props?: Record<string, unknown> & { children?: unknown; onClick?: () => void; disabled?: boolean };
};

const response = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

function render(): Node {
  hookCursor = 0;
  return ExperimentsTab() as Node;
}

function walk(node: unknown, visit: (current: Node) => void): void {
  if (Array.isArray(node)) {
    node.forEach((child) => walk(child, visit));
    return;
  }
  if (!node || typeof node !== "object") return;
  const current = node as Node;
  visit(current);
  const props = current.props || {};
  for (const [key, value] of Object.entries(props)) {
    if (/^on[A-Z]/.test(key)) continue;
    walk(value, visit);
  }
}

function nodes(tree: unknown, predicate: (node: Node) => boolean): Node[] {
  const found: Node[] = [];
  walk(tree, (node) => {
    if (predicate(node)) found.push(node);
  });
  return found;
}

function tagged(tree: unknown, key: string, value?: string): Node[] {
  return nodes(tree, (node) =>
    value === undefined ? key in (node.props || {}) : node.props?.[key] === value,
  );
}

function content(node: unknown): string {
  if (Array.isArray(node)) return node.map(content).join(" ");
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (!node || typeof node !== "object") return "";
  const props = (node as Node).props || {};
  return Object.entries(props)
    .filter(([key]) => !/^on[A-Z]/.test(key))
    .map(([, value]) => content(value))
    .join(" ");
}

function click(tree: unknown, key: string, value?: string) {
  const wrap = tagged(tree, key, value)[0];
  expect(wrap).toBeTruthy();
  const target =
    typeof wrap?.props?.onClick === "function"
      ? wrap
      : nodes(wrap, (node) => typeof node.props?.onClick === "function")[0];
  expect(target).toBeTruthy();
  (target?.props?.onClick as (() => void) | undefined)?.();
}

function fire<K extends string>(node: Node | undefined, key: K, event: unknown) {
  const handler = node?.props?.[key];
  expect(typeof handler).toBe("function");
  (handler as (ev: unknown) => void)(event);
}

function disclosureCaps() {
  return {
    skill_suggest: { en: "request text and skill fragments", zh: "请求文本" },
    literature_check: { en: "passages and claims", zh: "片段" },
    text_features: { en: "selected rows", zh: "行" },
    safety_shadow: { en: "pending code cell", zh: "待执行代码" },
    task_mode_shadow: { en: "request text", zh: "请求" },
  };
}

function status(overrides: Record<string, unknown> = {}) {
  return {
    experimental: true,
    effective: {
      master: { enabled: false, source: "default" },
      skill_suggest: { enabled: false, source: "master_off" },
      literature_check: { enabled: false, source: "master_off" },
      text_features: { enabled: false, source: "master_off" },
      safety_shadow: { enabled: false, source: "master_off" },
      task_mode_shadow: { enabled: false, source: "master_off" },
    },
    provider: "typesafe",
    model: "jev-1.13.0",
    key_configured: false,
    disclosure: {
      version: "2026-09-20",
      acked: false,
      capabilities: disclosureCaps(),
      facts: { en: "Hosted in the United States.", zh: "托管在美国。" },
    },
    egress: { mode: "off", domain_allowed: false, remediation: null },
    ...overrides,
  };
}

let currentStatus: Record<string, unknown>;
let lastPut: unknown;
let probeBody: Record<string, unknown>;

async function open(): Promise<Node> {
  render();
  effects.splice(0).forEach((effect) => effect());
  await vi.waitFor(() => expect(tagged(render(), "data-judgment-master").length).toBe(1));
  return render();
}

beforeEach(() => {
  hookState.length = 0;
  hookCursor = 0;
  effects.length = 0;
  lastPut = null;
  currentStatus = status();
  probeBody = { status: "disabled", error_code: null, latency_ms: 1, model: "jev-1.13.0" };
  mocks.alive = () => true;
  mocks.hint.mockReset();
  mocks.fetch.mockReset();
  mocks.fetch.mockImplementation((url: string, init?: RequestInit) => {
    const method = (init?.method || "GET").toUpperCase();
    if (url.endsWith("/experimental/judgment") && method === "GET") {
      return Promise.resolve(response(currentStatus));
    }
    if (url.endsWith("/experimental/judgment") && method === "PUT") {
      lastPut = init?.body ? JSON.parse(String(init.body)) : {};
      const body = lastPut as Record<string, unknown>;
      const next: Record<string, unknown> = {
        ...currentStatus,
        key_configured: body.clear_api_key === true ? false : body.api_key ? true : currentStatus.key_configured,
      };
      if (body.enabled === true && body.acknowledge) {
        next.effective = {
          ...(currentStatus.effective as Record<string, unknown>),
          master: { enabled: true, source: "setting" },
        };
        next.disclosure = {
          ...(currentStatus.disclosure as Record<string, unknown>),
          acked: true,
        };
      }
      if (body.enabled === false) {
        next.effective = {
          ...(currentStatus.effective as Record<string, unknown>),
          master: { enabled: false, source: "setting" },
        };
      }
      currentStatus = next;
      return Promise.resolve(response(next));
    }
    if (url.endsWith("/experimental/judgment/test") && method === "POST") {
      return Promise.resolve(response(probeBody));
    }
    return Promise.resolve(response({ error: "nope" }, 404));
  });
  vi.stubGlobal("fetch", mocks.fetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("ExperimentsTab disclosure", () => {
  it("does not enable the master switch without acknowledgement", async () => {
    const tree = await open();
    click(tree, "data-judgment-master");
    const dialog = render();
    expect(tagged(dialog, "data-judgment-disclosure").length).toBe(1);
    expect(lastPut).toBeNull();
    const confirm = tagged(dialog, "data-judgment-ack")[0];
    expect(confirm?.props?.disabled).toBe(true);
    confirm?.props?.onClick?.();
    await Promise.resolve();
    expect(lastPut).toBeNull();
    expect(content(render())).toContain("Experimental");
  });

  it("enables after every disclosure item is checked", async () => {
    await open();
    click(render(), "data-judgment-master");
    let tree = render();
    for (const name of Object.keys(disclosureCaps())) {
      const box = tagged(tree, "data-judgment-ack-cap", name)[0];
      expect(box).toBeTruthy();
      fire(box, "onChange", { target: { checked: true } });
    }
    tree = render();
    const confirm = tagged(tree, "data-judgment-ack")[0];
    expect(confirm?.props?.disabled).toBe(false);
    confirm?.props?.onClick?.();
    await vi.waitFor(() => expect(lastPut).toBeTruthy());
    expect(lastPut).toMatchObject({
      enabled: true,
      acknowledge: { version: "2026-09-20" },
    });
    const acked = (lastPut as { acknowledge: { capabilities: string[] } }).acknowledge.capabilities;
    expect(acked.sort()).toEqual(Object.keys(disclosureCaps()).sort());
    await vi.waitFor(() => expect(tagged(render(), "data-judgment-master", "on").length).toBe(1));
  });
});

describe("ExperimentsTab env_off and key", () => {
  it("disables the master switch when source is env_off", async () => {
    currentStatus = status({
      effective: {
        ...(status().effective as Record<string, unknown>),
        master: { enabled: false, source: "env_off" },
        skill_suggest: { enabled: false, source: "env_off" },
      },
    });
    const tree = await open();
    expect(content(tree)).toContain("Forced off by an environment variable");
    expect(tagged(tree, "data-judgment-master").length).toBe(1);
    const master = tagged(tree, "data-judgment-master")[0];
    const toggle = nodes(master, (node) => node.props?.disabled === true)[0];
    expect(toggle).toBeTruthy();
    const cap = tagged(tree, "data-judgment-cap", "skill_suggest")[0];
    expect(nodes(cap, (node) => node.props?.disabled === true).length).toBeGreaterThan(0);
  });

  it("never echoes a saved key", async () => {
    currentStatus = status({ key_configured: true });
    const tree = await open();
    expect(tagged(tree, "data-judgment-key-state", "configured").length).toBe(1);
    expect(content(tree)).toContain("Configured");
    const input = tagged(tree, "data-judgment-key")[0];
    expect(input?.props?.value).toBe("");
    expect(input?.props?.type).toBe("password");
    fire(input, "onInput", { target: { value: "loopback-test-key" } });
    click(render(), "data-judgment-save-key");
    await vi.waitFor(() => expect(lastPut).toMatchObject({ api_key: "loopback-test-key" }));
    const after = render();
    expect(tagged(after, "data-judgment-key")[0]?.props?.value).toBe("");
    expect(content(after)).toContain("Configured");
    expect(content(after)).not.toContain("loopback-test-key");
  });
});

describe("ExperimentsTab connection probe", () => {
  it.each([
    ["ok", { status: "ok", error_code: null, latency_ms: 12, model: "jev-1.13.0" }],
    ["unavailable", { status: "unavailable", error_code: "unconfigured", latency_ms: 3, model: "jev-1.13.0" }],
    ["disabled", { status: "disabled", error_code: null, latency_ms: 1, model: "jev-1.13.0" }],
  ] as const)("shows the %s probe result", async (name, body) => {
    probeBody = body;
    await open();
    click(render(), "data-judgment-test");
    await vi.waitFor(() => expect(tagged(render(), "data-judgment-test-result", name).length).toBe(1));
    const text = content(tagged(render(), "data-judgment-test-result")[0]);
    expect(text).toContain(name);
    if (body.error_code) expect(text).toContain(body.error_code);
    expect(text).toContain(String(body.latency_ms));
  });
});
