import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The tabs render the DataPro and Doubao cards before their config request
// answers. The cards must show what that request brings once it lands, not
// the empty config they were first rendered with.

const hooks = vi.hoisted(() => {
  const stores = new Map<string, unknown[]>();
  const effects: Array<() => void> = [];
  let slots: unknown[] = [];
  let cursor = 0;
  const set = (store: unknown[], index: number, value: unknown) => {
    store[index] = typeof value === "function" ? (value as (prev: unknown) => unknown)(store[index]) : value;
  };
  return {
    effects,
    reset() {
      stores.clear();
      effects.length = 0;
    },
    /** Render one component instance: its hooks live under `key`. */
    begin(key: string) {
      if (!stores.has(key)) stores.set(key, []);
      slots = stores.get(key)!;
      cursor = 0;
    },
    useState(initial: unknown) {
      const store = slots;
      const index = cursor++;
      if (store.length === index) store.push(typeof initial === "function" ? (initial as () => unknown)() : initial);
      return [store[index], (value: unknown) => set(store, index, value)];
    },
    useRef(initial: unknown) {
      const index = cursor++;
      if (slots.length === index) slots.push({ current: initial });
      return slots[index];
    },
    useEffect(fn: () => void, deps: unknown[]) {
      const index = cursor++;
      const old = slots[index] as unknown[] | undefined;
      if (old && deps.every((value, i) => value === old[i])) return;
      slots[index] = deps;
      effects.push(() => void fn());
    },
  };
});

vi.mock("preact/hooks", () => ({
  useState: hooks.useState,
  useRef: hooks.useRef,
  useEffect: hooks.useEffect,
}));

const mocks = vi.hoisted(() => ({ fetch: vi.fn(), alive: (): boolean => true }));
vi.mock("../../../i18n", () => ({
  LANG: "en",
  t: (key: string, ...args: unknown[]) => [key, ...args].join(" "),
  tOptional: () => null,
  onLanguageChange: () => () => undefined,
}));
vi.mock("../use-timer-lease", () => ({ useAlive: () => mocks.alive, useTimerLease: () => ({}) }));
vi.mock("../../../features/customize/actions", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../../features/customize/actions")>()),
  custTab: vi.fn(),
}));

import { ConnectorsTab } from "../ConnectorsTab";
import { NetworkTab } from "../NetworkTab";
import { DataProCard } from "./datapro";
import { DoubaoSearchCard } from "./doubao";
import { OllamaWebCard } from "./ollama";

type Node = {
  type?: unknown;
  props?: Record<string, unknown> & { children?: unknown; class?: string; disabled?: boolean; onClick?: () => void };
};
type Route = () => Promise<Response>;

const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

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

function text(node: unknown): string {
  if (Array.isArray(node)) return node.map(text).join("");
  if (typeof node === "string" || typeof node === "number") return String(node);
  return node && typeof node === "object" ? text((node as Node).props?.children) : "";
}

function routes(table: Record<string, Route>) {
  mocks.fetch.mockImplementation((url: string, init?: RequestInit) => {
    const route = table[`${init?.method || "GET"} ${url.replace(/^\/api\/v1/, "")}`];
    return route ? route() : Promise.resolve(response({}));
  });
}

/** The tab, then the card it renders, each with hooks of its own. */
function renderCard(tab: () => unknown, card: (props: never) => unknown): Node {
  hooks.begin("tab");
  const tree = tab() as Node;
  const vnode = find(tree, (node) => node.type === card)!;
  hooks.begin("card");
  return card(vnode.props as never) as Node;
}

async function settle(): Promise<void> {
  hooks.effects.splice(0).forEach((effect) => effect());
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

const credential = (card: Node) => find(card, (node) => (node.props?.class || "").startsWith("datapro-credential-state"))!;
const writes = () =>
  mocks.fetch.mock.calls
    .filter(([, init]) => ["PUT", "POST"].includes(String((init as RequestInit | undefined)?.method)))
    .map(([url, init]) => [url, (init as RequestInit).body]);

beforeEach(() => {
  hooks.reset();
  mocks.fetch.mockReset();
  vi.stubGlobal("fetch", mocks.fetch);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("DataPro card on Connectors", () => {
  it("shows the key and connector state the config read brings, and acts on it", async () => {
    const read = deferred<Response>();
    routes({
      "GET /connectors": () => Promise.resolve(response({ connectors: [] })),
      "GET /datapro/config": () => read.promise,
    });
    renderCard(ConnectorsTab, DataProCard);
    await settle();
    renderCard(ConnectorsTab, DataProCard);

    read.resolve(response({ key_configured: true, connector_enabled: true, skill_enabled: true }));
    await settle();
    const card = renderCard(ConnectorsTab, DataProCard);
    expect(text(credential(card))).toBe("cust.datapro.keyConfigured");
    expect(credential(card).props!.class).not.toContain("bad");
    const toggle = find(card, (node) => node.props?.["data-action"] === "datapro-toggle-connector")!;
    expect(toggle.props!.class).toBe("toggle on");
    const skill = find(card, (node) => node.props?.["data-action"] === "datapro-enable-skill")!;
    expect(skill.props!.disabled).toBe(true);
    expect(text(skill)).toBe("cust.datapro.skillEnabled");

    toggle.props!.onClick!();
    await settle();
    expect(writes()).toEqual([
      ["/api/v1/connectors/volcengine-datapro/enabled", JSON.stringify({ enabled: false })],
    ]);
  });

  it("offers nothing to act on before the config read answers", async () => {
    routes({
      "GET /connectors": () => Promise.resolve(response({ connectors: [] })),
      "GET /datapro/config": () => new Promise<Response>(() => {}),
    });
    renderCard(ConnectorsTab, DataProCard);
    await settle();
    const card = renderCard(ConnectorsTab, DataProCard);
    expect(text(credential(card))).toBe("common.loading");
    const toggle = find(card, (node) => node.props?.["data-action"] === "datapro-toggle-connector")!;
    expect(toggle.props!.disabled).toBe(true);
    toggle.props!.onClick!();
    expect(find(card, (node) => node.props?.["data-action"] === "datapro-enable-skill")!.props!.disabled).toBe(true);
    expect(find(card, (node) => node.props?.["data-action"] === "datapro-save-key")!.props!.disabled).toBe(true);
    expect(writes()).toEqual([]);
  });
});

describe("Doubao card on Network", () => {
  it("shows the key state the config read brings", async () => {
    const read = deferred<Response>();
    routes({
      "GET /preferences/builtin-allowlist": () => Promise.resolve(response({ enabled: false, groups: [] })),
      "GET /doubao-search/config": () => read.promise,
    });
    renderCard(NetworkTab, DoubaoSearchCard);
    await settle();
    let card = renderCard(NetworkTab, DoubaoSearchCard);
    expect(text(credential(card))).toBe("common.loading");
    expect(find(card, (node) => node.props?.["data-action"] === "doubao-search-save-key")!.props!.disabled).toBe(true);

    read.resolve(response({ key_configured: false, ark_key_reused: true }));
    await settle();
    card = renderCard(NetworkTab, DoubaoSearchCard);
    expect(text(credential(card))).toBe("cust.doubao.keyArkReused");
    expect(credential(card).props!.class).not.toContain("bad");
    expect(find(card, (node) => node.props?.["data-action"] === "doubao-search-save-key")!.props!.disabled).toBe(false);
  });

  it("says a model key from the environment is not shared, rather than plain 'not configured'", async () => {
    routes({
      "GET /preferences/builtin-allowlist": () => Promise.resolve(response({ enabled: false, groups: [] })),
      "GET /doubao-search/config": () =>
        Promise.resolve(response({ key_configured: false, ark_key_reused: false })),
    });
    renderCard(NetworkTab, DoubaoSearchCard);
    await settle();
    const card = renderCard(NetworkTab, DoubaoSearchCard);
    const state = text(credential(card));
    expect(state).toContain("OPENAI4S_ARK_API_KEY");
    expect(state).not.toBe("cust.doubao.keyMissing");
    expect(credential(card).props!.class).toContain("bad");
  });
});

describe("the allowlist on Network", () => {
  const head = (tree: Node) =>
    find(tree, (node) => String(node.props?.class || "").includes("allowlist-head"))!;

  it("says what the domain groups are and that they do not apply while egress is off", async () => {
    routes({
      "GET /preferences/builtin-allowlist": () =>
        Promise.resolve(
          response({
            enabled: true,
            egress_mode: "off",
            granted: [],
            groups: [{ name: "Web search", domains: Array.from({ length: 14 }, (_, i) => `d${i}.org`) }],
          }),
        ),
    });
    hooks.begin("tab");
    NetworkTab();
    await settle();
    hooks.begin("tab");
    const tree = NetworkTab() as Node;
    expect(head(tree).props!["data-egress-mode"]).toBe("off");
    expect(text(head(tree))).toContain("OPENAI4S_EGRESS=off");
    expect(text(head(tree))).toContain("literature-review");
    // Fourteen domains: twelve pills and a count, not a silent cut at twelve.
    const pills = text(find(tree, (node) => String(node.props?.title || "").startsWith("d0.org"))!);
    expect(pills).toContain("d11.org");
    expect(pills).not.toContain("d12.org");
    expect(pills).toMatch(/(2 more|还有 2 个)$/);
  });

  it("says the groups are enforced under allowlist and lists approved domains", async () => {
    routes({
      "GET /preferences/builtin-allowlist": () =>
        Promise.resolve(
          response({
            enabled: true,
            egress_mode: "allowlist",
            granted: ["www.nejm.org"],
            groups: [{ name: "Web search", domains: ["bing.com"] }],
          }),
        ),
    });
    hooks.begin("tab");
    NetworkTab();
    await settle();
    hooks.begin("tab");
    const tree = NetworkTab() as Node;
    expect(text(head(tree))).toContain("OPENAI4S_EGRESS=allowlist");
    expect(text(head(tree))).toContain("www.nejm.org");
  });

  // ENV-3 follow-up: the switch governs the app's own requests; code in cells
  // is the kernel sandbox's business, which nothing on this tab opens.
  it("tells app networking from kernel networking", async () => {
    routes({
      "GET /preferences/builtin-allowlist": () =>
        Promise.resolve(response({ enabled: true, egress_mode: "off", granted: [], groups: [] })),
    });
    hooks.begin("tab");
    NetworkTab();
    await settle();
    hooks.begin("tab");
    const tree = NetworkTab() as Node;
    const sub = String(find(tree, (node) => typeof node.props?.sub === "string")!.props!.sub);
    expect(text(tree)).toMatch(/App networking|应用联网/);
    expect(sub).toMatch(/App networking|应用联网/);
    expect(sub).toMatch(/Kernel networking|内核联网/);
    expect(sub).not.toContain("web_fetch");
  });
});

describe("Independent Ollama web key on Network", () => {
  const render = () => renderCard(NetworkTab, OllamaWebCard);
  const button = (card: Node, action: string) =>
    find(card, (node) => node.props?.["data-action"] === `ollama-web-${action}-key`)!;
  const input = (card: Node) =>
    find(card, (node) => node.props?.id === "ollama-web-api-key")!;
  const typeKey = (card: Node) => {
    (input(card).props!.onInput as (event: unknown) => void)({
      target: { value: "synthetic-independent-web-key" },
    });
  };

  it("waits for configuration, saves once without model writes, and clears the password input", async () => {
    const read = deferred<Response>();
    const save = deferred<Response>();
    routes({
      "GET /ollama-web/config": () => read.promise,
      "PUT /ollama-web/config": () => save.promise,
    });
    let card = render();
    expect(button(card, "save").props!.disabled).toBe(true);
    await settle();
    read.resolve(response({ key_configured: false, available: false, source: "" }));
    await settle();
    card = render();
    expect(button(card, "save").props!.disabled).toBe(false);
    expect(button(card, "clear").props!.disabled).toBe(true);
    typeKey(card);
    card = render();
    button(card, "save").props!.onClick!();
    button(card, "save").props!.onClick!();
    card = render();
    expect(input(card).props!.value).toBe("");
    expect(button(card, "save").props!.disabled).toBe(true);
    expect(writes()).toEqual([
      ["/api/v1/ollama-web/config", JSON.stringify({ api_key: "synthetic-independent-web-key" })],
    ]);
    save.resolve(response({ key_configured: true, available: true, source: "saved" }));
    await settle();
    card = render();
    expect(input(card).props!.value).toBe("");
    expect(button(card, "clear").props!.disabled).toBe(false);
    expect(credential(card).props!.class).not.toContain("bad");
  });

  it("reads a previously saved key without returning it to the input, and can remove it", async () => {
    routes({
      "GET /ollama-web/config": () => Promise.resolve(response({ key_configured: true, available: true, source: "saved" })),
      "PUT /ollama-web/config": () => Promise.resolve(response({ key_configured: false, available: false, source: "" })),
    });
    render();
    await settle();
    let card = render();
    expect(input(card).props!.value).toBe("");
    expect(button(card, "clear").props!.disabled).toBe(false);
    button(card, "clear").props!.onClick!();
    await settle();
    card = render();
    expect(button(card, "clear").props!.disabled).toBe(true);
    expect(writes()).toEqual([
      ["/api/v1/ollama-web/config", JSON.stringify({ clear_api_key: true })],
    ]);
  });

  it("shows a save failure without retaining the typed key or claiming it was saved", async () => {
    routes({
      "GET /ollama-web/config": () => Promise.resolve(response({ key_configured: false, available: false, source: "" })),
      "PUT /ollama-web/config": () => Promise.resolve(response({ error: "Secret store unavailable" }, 503)),
    });
    render();
    await settle();
    typeKey(render());
    button(render(), "save").props!.onClick!();
    await settle();
    const card = render();
    expect(input(card).props!.value).toBe("");
    expect(text(credential(card))).toContain("Secret store unavailable");
    expect(credential(card).props!.class).toContain("bad");
    expect(button(card, "clear").props!.disabled).toBe(true);
  });

  it("does not allow a write when the settings could not be read", async () => {
    routes({
      "GET /ollama-web/config": () => Promise.resolve(response({ error: "Unavailable" }, 503)),
    });
    render();
    await settle();
    const card = render();
    expect(button(card, "save").props!.disabled).toBe(true);
    expect(button(card, "clear").props!.disabled).toBe(true);
    expect(text(credential(card))).toContain("Unavailable");
    expect(writes()).toEqual([]);
  });
});
