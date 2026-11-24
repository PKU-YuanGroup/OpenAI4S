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
vi.mock("../../features/customize/actions", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../features/customize/actions")>()),
  custTab: vi.fn(),
}));
vi.mock("../../features/customize/host", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../features/customize/host")>()),
  hint: mocks.hint,
}));

import { teamUser } from "../../features/chrome/team-identity";
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
    if (url === "/api/v1/permissions") {
      return Promise.resolve(
        response({
          rules: {
            global: [
              { rule_id: "g-1", tool: "write_file", pattern: "*", decision: "allow" },
              { rule_id: "g-2", tool: "env_setup", pattern: "*", decision: "ask" },
            ],
          },
        }),
      );
    }
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

function findAll(node: unknown, match: (node: Node) => boolean, out: Node[] = []): Node[] {
  if (Array.isArray(node)) {
    for (const child of node) findAll(child, match, out);
    return out;
  }
  if (!node || typeof node !== "object") return out;
  const current = node as Node;
  if (match(current)) out.push(current);
  return findAll(current.props?.children, match, out);
}

function text(node: unknown): string {
  if (Array.isArray(node)) return node.map(text).join("");
  if (node == null || typeof node === "boolean") return "";
  if (typeof node !== "object") return String(node);
  return text((node as Node).props?.children);
}

const withClass = (tree: unknown, cls: string) => findAll(tree, (node) => node.props?.class === cls);

// PERM-2: with no session open the tab said "only global defaults are shown
// below" and returned before reading anything, so nothing was below.
describe("PermissionsTab without a session", () => {
  beforeEach(() => {
    currentId.value = null;
    serve(() => Promise.resolve(response({ ok: true })));
  });

  it("reads and lists the global rules", async () => {
    render();
    await settle();
    const tree = render();

    expect(mocks.fetch.mock.calls.map((call) => call[0])).toContain("/api/v1/permissions");
    expect(withClass(tree, "perm-sec-h").map(text)).toEqual(["cust.perm.scope.global"]);
    expect(withClass(tree, "perm-rtool").map(text)).toEqual(["write_file", "env_setup"]);
    expect(text(tree)).toContain("cust.perm.noSessionNote");
  });

  it("says what each tool does and what the rules amount to", async () => {
    render();
    await settle();
    const tree = render();

    expect(withClass(tree, "perm-rdesc").map(text)).toEqual([
      "Write a file in the session workspace",
      "Install packages into the kernel environment",
    ]);
    // Read from the rules, not a fixed sentence: here a write is allowed and
    // installs ask, which the old description had the other way round.
    const summary = withClass(tree, "cust-note").map(text)[0]!;
    expect(summary).toContain("writing files partly need approval");
    expect(summary).toMatch(/package installs[^;]* need your approval/);
  });

  it("offers the conversation and project scopes once a session is open", async () => {
    currentId.value = "f-1";
    render();
    await settle();
    expect(withClass(render(), "perm-sec-h").map(text)).toEqual([
      "cust.perm.scope.conversation",
      "cust.perm.scope.project",
      "cust.perm.scope.global",
    ]);
  });
});

// PERM-1: package installs ask every time, so no allow rule is written for one.
describe("PermissionsTab and package installs", () => {
  beforeEach(() => {
    currentId.value = null;
    serve(() => Promise.resolve(response({ ok: true })));
  });

  const addRow = (tree: unknown) => find(tree, (node) => node.props?.class === "perm-rule perm-add")!;

  it("does not save an allow rule for installs, and says why", async () => {
    render();
    await settle();
    let row = addRow(render());
    const inputs = findAll(row, (node) => node.type === "input");
    (inputs[0]!.props!.onInput as (e: unknown) => void)({ target: { value: "env_setup" } });
    row = addRow(render());
    (find(row, (node) => node.type === "select")!.props!.onChange as (e: unknown) => void)({
      target: { value: "allow" },
    });
    row = addRow(render());
    // The allow choice is off for an install, with the reason beside it.
    const allowOption = find(row, (node) => node.type === "option" && node.props?.value === "allow")!;
    expect(allowOption.props!.disabled).toBe(true);
    expect(text(row)).toContain("Package installs ask every time");
    await (find(row, (node) => node.type === "button")!.props!.onClick as () => Promise<void>)();
    await settle();
    expect(mocks.fetch.mock.calls.some((call) => (call[1] as RequestInit | undefined)?.method === "POST")).toBe(
      false,
    );
    expect(mocks.hint).toHaveBeenCalledWith(expect.stringContaining("Package installs ask every time"), true);
  });

  it("locks the allow choice on an install rule", async () => {
    render();
    await settle();
    const selects = findAll(render(), (node) => node.type === "select" && node.props?.class === "perm-dec");
    const allowDisabled = (select: Node) =>
      !!find(select, (node) => node.type === "option" && node.props?.value === "allow")!.props!.disabled;
    // write_file *, env_setup *, then the add row.
    expect(selects.map(allowDisabled)).toEqual([false, true, false]);
  });
});

// TEAM-23: Restore defaults and the global tier answered a member with 403
// `admin_only` and nothing on screen.
describe("PermissionsTab for a team member", () => {
  beforeEach(() => {
    currentId.value = null;
    serve(() => Promise.resolve(response({ error: "admin only", code: "admin_only" }, 403)));
  });
  afterEach(() => {
    teamUser.value = null;
  });
  const selects = (tree: unknown) =>
    findAll(tree, (node) => node.type === "select" && node.props?.class === "perm-dec");

  const resetButton = (tree: unknown) =>
    find(tree, (node) => node.type === "button" && text(node) === "cust.perm.resetBtn")!;

  it("locks the global tier and Restore defaults, with the reason", async () => {
    teamUser.value = { username: "bob", role: "member" };
    render();
    await settle();
    const tree = render();
    expect(resetButton(tree).props!.disabled).toBe(true);
    expect(text(tree)).toMatch(/Only an administrator can restore/);
    // The two global rules are shown, locked, with no delete and no add row.
    expect(selects(tree).map((node) => !!node.props!.disabled)).toEqual([true, true]);
    expect(withClass(tree, "icon-ghost")).toHaveLength(0);
    expect(withClass(tree, "perm-rule perm-add")).toHaveLength(0);
    expect(findAll(tree, (node) => "data-admin-only" in (node.props || {}))).toHaveLength(1);
  });

  it("leaves everything editable for an admin", async () => {
    teamUser.value = { username: "alice", role: "admin" };
    render();
    await settle();
    const tree = render();
    expect(resetButton(tree).props!.disabled).toBe(false);
    expect(selects(tree).map((node) => !!node.props!.disabled)).toEqual([false, false, false]);
    expect(withClass(tree, "icon-ghost")).toHaveLength(2);
    expect(withClass(tree, "perm-rule perm-add")).toHaveLength(1);
    expect(findAll(tree, (node) => "data-admin-only" in (node.props || {}))).toHaveLength(0);
  });
});
