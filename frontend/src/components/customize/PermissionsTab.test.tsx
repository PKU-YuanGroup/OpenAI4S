import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// A rule's decision select is controlled. What the DOM shows after a change
// is whatever the next render says -- and without a render, the user's own
// choice, which the server may have refused.

const hooks = vi.hoisted(() => {
  const stores = new Map<string, unknown[]>();
  const effects: Array<() => void> = [];
  let slots: unknown[] = [];
  let cursor = 0;
  let updates = 0;
  return {
    effects,
    updates: () => updates,
    reset() {
      stores.clear();
      effects.length = 0;
      updates = 0;
    },
    begin(key: string) {
      if (!stores.has(key)) stores.set(key, []);
      slots = stores.get(key)!;
      cursor = 0;
    },
    useState(initial: unknown) {
      const store = slots;
      const index = cursor++;
      if (store.length === index) store.push(typeof initial === "function" ? (initial as () => unknown)() : initial);
      return [
        store[index],
        (value: unknown) => {
          updates += 1;
          store[index] = typeof value === "function" ? (value as (prev: unknown) => unknown)(store[index]) : value;
        },
      ];
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

vi.mock("preact/hooks", () => ({ useState: hooks.useState, useRef: hooks.useRef, useEffect: hooks.useEffect }));

const mocks = vi.hoisted(() => ({ fetch: vi.fn(), hint: vi.fn(), alive: (): boolean => true }));
vi.mock("../../i18n", () => ({
  LANG: "en",
  t: (key: string, ...args: unknown[]) => [key, ...args].join(" "),
  tOptional: () => null,
  onLanguageChange: () => () => undefined,
}));
vi.mock("./use-timer-lease", () => ({ useAlive: () => mocks.alive, useTimerLease: () => ({}) }));
vi.mock("../../features/customize/actions", () => ({ custTab: vi.fn() }));
vi.mock("../../features/customize/host", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../features/customize/host")>()),
  hint: mocks.hint,
}));

import { currentId } from "../../stores/session";
import { PermissionsTab } from "./PermissionsTab";

type Node = {
  type?: unknown;
  key?: unknown;
  props?: Record<string, unknown> & { children?: unknown; value?: string; disabled?: boolean };
};

const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

/** Render `node` and every component under it, each with hooks of its own. */
function expand(node: unknown, path: string): unknown {
  if (Array.isArray(node)) return node.map((child, i) => expand(child, `${path}/${i}`));
  if (!node || typeof node !== "object") return node;
  const vnode = node as Node;
  if (typeof vnode.type === "function") {
    const where = `${path}/${vnode.type.name}:${String(vnode.key ?? "")}`;
    hooks.begin(where);
    return expand((vnode.type as (props: unknown) => unknown)(vnode.props), where);
  }
  return { ...vnode, props: { ...vnode.props, children: expand(vnode.props?.children, path) } };
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

const render = () => expand({ type: PermissionsTab, props: {} }, "root");
const decision = () => find(render(), (node) => node.type === "select" && node.props?.class === "perm-dec")!;
const choose = (value: string) =>
  (decision().props!.onChange as (e: unknown) => void)({ target: { value } });

async function settle(): Promise<void> {
  hooks.effects.splice(0).forEach((effect) => effect());
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

function serve(write: () => Promise<Response>) {
  mocks.fetch.mockImplementation((url: string, init?: RequestInit) => {
    if (init?.method === "POST") return write();
    if (url === "/api/v1/frames/f-1/permissions") {
      return Promise.resolve(
        response({
          project_id: "",
          root_frame_id: "f-1",
          rules: { conversation: [{ rule_id: "r-1", tool: "bash", pattern: "*", decision: "ask" }] },
        }),
      );
    }
    return Promise.resolve(response({}));
  });
}

beforeEach(() => {
  hooks.reset();
  mocks.fetch.mockReset();
  mocks.hint.mockReset();
  currentId.value = "f-1";
  vi.stubGlobal("fetch", mocks.fetch);
});
afterEach(() => {
  currentId.value = null;
  vi.unstubAllGlobals();
});

describe("PermissionsTab rule decision", () => {
  it("keeps showing a decision the server took", async () => {
    serve(() => Promise.resolve(response({ ok: true })));
    render();
    await settle();
    expect(decision().props!.value).toBe("ask");

    choose("deny");
    await settle();
    expect(decision().props!.value).toBe("deny");
    expect(mocks.hint).toHaveBeenCalledWith("toast.perm.ruleUpdated");
  });

  it("goes back to the rule's decision when the server refuses the change", async () => {
    let refuse!: () => void;
    serve(() => new Promise<Response>((resolve) => (refuse = () => resolve(response({ error: "locked" }, 409)))));
    render();
    await settle();

    choose("deny");
    expect(decision().props!.disabled).toBe(true);
    const before = hooks.updates();
    refuse();
    await settle();
    // A render has to be asked for: without one Preact never resets the select.
    expect(hooks.updates()).toBeGreaterThan(before);
    expect(decision().props!.value).toBe("ask");
    expect(decision().props!.disabled).toBe(false);
    expect(mocks.hint).toHaveBeenCalledWith("toast.perm.updateFailed locked", true);
  });
});
