import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const hookState: unknown[] = [];
let hookCursor = 0;
const effects: Array<() => void> = [];

vi.mock("preact/hooks", () => ({
  useState: (initial: unknown) => {
    const index = hookCursor++;
    if (hookState.length === index) hookState.push(initial);
    return [hookState[index], (value: unknown) => {
      hookState[index] = typeof value === "function" ? (value as (prev: unknown) => unknown)(hookState[index]) : value;
    }];
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

const mocks = vi.hoisted(() => ({ fetch: vi.fn(), alive: (): boolean => true }));
vi.mock("../../i18n", () => ({
  LANG: "en",
  t: (key: string) => key,
  tOptional: () => null,
  onLanguageChange: () => undefined,
}));
vi.mock("./use-timer-lease", () => ({ useAlive: () => mocks.alive }));
vi.mock("./vendors/volcengine", () => ({ VolcenginePanel: () => null }));
vi.mock("../../features/customize/actions", () => ({ custTab: () => undefined }));

import { LocalEndpointRow, ModelsTab, ProfileRow, profileKeyLabel } from "./ModelsTab";

type Node = { type?: unknown; props?: Record<string, unknown> & { children?: unknown } };
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

function render(): Node {
  hookCursor = 0;
  return ModelsTab() as Node;
}
function content(node: unknown): string {
  if (Array.isArray(node)) return node.map(content).join(" ");
  if (typeof node === "string" || typeof node === "number") return String(node);
  return node && typeof node === "object" ? content((node as Node).props?.children) : "";
}
function tagged(node: unknown, key: string): Node[] {
  const found: Node[] = [];
  const walk = (current: unknown): void => {
    if (Array.isArray(current)) return current.forEach(walk);
    if (!current || typeof current !== "object") return;
    const n = current as Node;
    if (n.props && key in n.props) found.push(n);
    walk(n.props?.children);
  };
  walk(node);
  return found;
}

const LIVE = {
  provider: "ark",
  model: "doubao-seed-2.0-pro",
  base_url: "https://ark.cn-beijing.volces.com/api/plan/v3",
  has_api_key: true,
};

async function open(routes: Record<string, () => Response>): Promise<Node> {
  mocks.fetch.mockImplementation((url: string) => {
    const route = Object.keys(routes).find((path) => url === "/api/v1" + path);
    return Promise.resolve(route ? routes[route]!() : response({ error: "nope" }, 404));
  });
  render();
  effects.splice(0).forEach((effect) => effect());
  await vi.waitFor(() => expect(mocks.fetch).toHaveBeenCalled());
  // Let the awaited responses settle into state before reading the tree.
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
  return render();
}

beforeEach(() => {
  hookState.length = 0;
  hookCursor = 0;
  effects.length = 0;
  mocks.fetch.mockReset();
  vi.stubGlobal("fetch", mocks.fetch);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("ModelsTab active configuration", () => {
  it("shows the environment/settings model when no profile is saved", async () => {
    const tree = await open({
      "/model-profiles": () => response({ profiles: [], active_id: "", protocols: ["ark"] }),
      "/config/llm": () => response(LIVE),
    });
    const text = content(tree);
    expect(text).toContain("doubao-seed-2.0-pro");
    expect(text).not.toContain("cust.models.empty2");
    const row = tagged(tree, "data-live-model")[0];
    expect(row).toBeTruthy();
    expect(content(row)).toContain("cust.models.hasKey");
    // The key itself is never in the payload, so it cannot be in the row; the
    // row says where the configuration comes from instead.
    expect(content(row)).toMatch(/environment/i);
  });

  it("shows a model id shaped like a credential prefix as itself, not [redacted]", async () => {
    // `ark-code-latest` is the Ark router's default id; the generic credential
    // regex (`ark-` + 8 chars) turned the row's title into "[redacted]".
    const tree = await open({
      "/model-profiles": () => response({ profiles: [], active_id: "", protocols: ["ark"] }),
      "/config/llm": () => response({ ...LIVE, model: "ark-code-latest" }),
    });
    const row = content(tagged(tree, "data-live-model")[0]);
    expect(row).toContain("ark-code-latest");
    expect(row).not.toContain("[redacted]");
  });

  it("still redacts a real key that ended up in the live row", async () => {
    const pasted = "sk-proj-fake-Q3vT9wXk2LmN8pRz4YbC7dFh1JsA6uEo";
    const tree = await open({
      "/model-profiles": () => response({ profiles: [], active_id: "", protocols: ["ark"] }),
      "/config/llm": () =>
        response({ ...LIVE, model: pasted, base_url: "https://proxy.example/v1?key=s3cr3tv4lu3" }),
    });
    const row = content(tagged(tree, "data-live-model")[0]);
    expect(row).not.toContain("Q3vT9wXk2LmN8pRz4YbC7dFh1JsA6uEo");
    expect(row).not.toContain("s3cr3tv4lu3");
    expect(row).toContain("[redacted]");
  });

  it("redacts an Ark key that ended up in the live row", async () => {
    const pasted = ["ark-fake", "3f2a9c1e", "7b4d", "4e8a", "9c2f", "1a2b3c4d5e6f", "ab12c"].join("-");
    const tree = await open({
      "/model-profiles": () => response({ profiles: [], active_id: "", protocols: ["ark"] }),
      "/config/llm": () => response({ ...LIVE, model: pasted }),
    });
    const row = content(tagged(tree, "data-live-model")[0]);
    expect(row).not.toContain("3f2a9c1e-7b4d-4e8a-9c2f-1a2b3c4d5e6f");
    expect(row).toContain("[redacted]");
  });

  it("keeps the empty state when nothing is configured anywhere", async () => {
    const tree = await open({
      "/model-profiles": () => response({ profiles: [], active_id: "", protocols: ["ark"] }),
      "/config/llm": () => response({ provider: "", model: "", base_url: "", has_api_key: false }),
    });
    expect(tagged(tree, "data-live-model")).toHaveLength(0);
    expect(content(tree)).toContain("cust.models.empty2");
  });

  it("does not add a second active row when a saved profile is active", async () => {
    const tree = await open({
      "/model-profiles": () =>
        response({
          profiles: [{ id: "mp-1", name: "Ark", provider: "ark", model: "doubao-seed-2.0-pro", has_api_key: true }],
          active_id: "mp-1",
          protocols: ["ark"],
        }),
      "/config/llm": () => response(LIVE),
    });
    expect(tagged(tree, "data-live-model")).toHaveLength(0);
  });

  it("still lists profiles when the live configuration cannot be read", async () => {
    const tree = await open({
      "/model-profiles": () =>
        response({
          profiles: [{ id: "mp-1", name: "Saved", provider: "ark", model: "m", has_api_key: true }],
          active_id: "",
          protocols: ["ark"],
        }),
    });
    expect(tagged(tree, "data-live-model")).toHaveLength(0);
    expect(content(tree)).not.toContain("versions.load.err");
  });
});

describe("local endpoint add", () => {
  it("adds a local model once however often Add is pressed while the write is in flight", async () => {
    let finish!: (value: Response) => void;
    mocks.fetch.mockImplementation((_url: string, init?: RequestInit) =>
      init?.method === "POST"
        ? new Promise<Response>((resolve) => (finish = resolve))
        : Promise.resolve(response({})),
    );
    const posts = () => mocks.fetch.mock.calls.filter(([, init]) => (init as RequestInit)?.method === "POST");
    const endpoint = {
      label: "Ollama",
      base_url: "http://127.0.0.1:11434/v1",
      models: ["llama3"],
      default_model: "llama3",
    };
    const row = () => {
      hookCursor = 0;
      return LocalEndpointRow({ endpoint, profiles: [] }) as Node;
    };
    const add = () => (row().props!.children as Node[]).find((node) => node?.type === "button")!;

    void (add().props!.onClick as () => Promise<void>)();
    expect(add().props!.disabled).toBe(true);
    void (add().props!.onClick as () => Promise<void>)();
    expect(posts()).toHaveLength(1);

    finish(response({ id: "mp-local" }));
    await vi.waitFor(() => expect(add().props!.disabled).toBe(false));
    expect(posts()).toHaveLength(1);
  });
});

describe("composer model list after a profile change", () => {
  // `#model-select` renders from what GET /models answered at the last read.
  const modelReads = () =>
    mocks.fetch.mock.calls.filter(([url, init]) => url === "/api/v1/models" && !(init as RequestInit)?.method)
      .length;
  const find = (node: unknown, match: (node: Node) => boolean): Node | null => {
    if (Array.isArray(node)) {
      for (const child of node) {
        const found = find(child, match);
        if (found) return found;
      }
      return null;
    }
    if (!node || typeof node !== "object") return null;
    const current = node as Node;
    return match(current) ? current : find(current.props?.children, match);
  };

  it("re-reads the list after a profile is added", async () => {
    await open({
      "/model-profiles": () => response({ profiles: [], active_id: "", protocols: ["ark"] }),
      "/config/llm": () => response(LIVE),
    });
    const name = find(render(), (node) => node.type === "input" && node.props?.placeholder === "cust.models.namePlaceholder")!;
    (name.props!.onInput as (e: unknown) => void)({ target: { value: "Second" } });
    const add = find(render(), (node) => node.type === "button" && node.props?.class === "solid-btn")!;
    await (add.props!.onClick as () => Promise<void>)();
    expect(mocks.fetch.mock.calls.some(([url, init]) => url === "/api/v1/model-profiles" && (init as RequestInit)?.method === "POST")).toBe(true);
    expect(modelReads()).toBe(1);
  });

  it("re-reads the list after a profile that is not active is deleted", async () => {
    vi.stubGlobal("window", { confirm: () => true });
    mocks.fetch.mockImplementation(() => Promise.resolve(response({})));
    hookCursor = 0;
    const row = ProfileRow({ p: { id: "mp-2", name: "Other" }, activeId: "mp-1", protocols: [], onEdit: () => {} });
    const trash = find(row, (node) => node.props?.name === "trash-2")!;
    await (trash.props!.onClick as () => Promise<void>)();
    expect(mocks.fetch.mock.calls[0]).toEqual(["/api/v1/model-profiles/mp-2", expect.objectContaining({ method: "DELETE" })]);
    expect(modelReads()).toBe(1);
  });

  it("re-reads the list after a local model is added", async () => {
    mocks.fetch.mockImplementation(() => Promise.resolve(response({ id: "mp-local" })));
    hookCursor = 0;
    const row = LocalEndpointRow({
      endpoint: { label: "Ollama", base_url: "http://127.0.0.1:11434/v1", models: ["llama3"], default_model: "llama3" },
      profiles: [],
    }) as Node;
    const add = (row.props!.children as Node[]).find((node) => node?.type === "button")!;
    await (add.props!.onClick as () => Promise<void>)();
    expect(modelReads()).toBe(1);
  });
});

describe("profile key label", () => {
  it("names an environment credential instead of reporting no key", () => {
    expect(profileKeyLabel({ has_api_key: false, credential_source: "environment" })).toMatch(/environment/i);
    expect(profileKeyLabel({ has_api_key: true, credential_source: "profile" })).toBe("cust.models.hasKey");
    expect(profileKeyLabel({ has_api_key: false, credential_source: "local", base_url: "http://10.0.0.5:8000/v1" })).toBe(
      "cust.models.local.keyless",
    );
    expect(profileKeyLabel({ has_api_key: false, credential_source: "missing" })).toBe("cust.models.noKey");
    // An older daemon sends no `credential_source`: the loopback rule still applies.
    expect(profileKeyLabel({ has_api_key: false, base_url: "http://127.0.0.1:11434/v1" })).toBe("cust.models.local.keyless");
  });
});
