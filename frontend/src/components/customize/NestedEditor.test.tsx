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

const mocks = vi.hoisted(() => ({ fetch: vi.fn(), custTab: vi.fn(), alive: (): boolean => true }));
vi.mock("../../i18n", () => ({
  LANG: "en",
  t: (key: string, ...args: unknown[]) => [key, ...args].join(" "),
  tOptional: () => null,
}));
vi.mock("./use-timer-lease", () => ({ useAlive: () => mocks.alive, useTimerLease: () => ({}) }));
vi.mock("../../features/customize/actions", () => ({ custTab: mocks.custTab }));

import { nestedEditor } from "../../features/customize/state";
import { NestedEditor } from "./NestedEditor";

type Node = {
  type?: unknown;
  props?: Record<string, unknown> & { children?: unknown; disabled?: boolean; onClick?: () => unknown };
};
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

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

/** Render the open editor's form component (NestedEditor itself holds no hooks). */
function renderForm(): Node {
  hookCursor = 0;
  const form = find(NestedEditor(), (node) => typeof node.type === "function" && node.type.name.endsWith("Form"))!;
  return (form.type as (props: unknown) => Node)(form.props);
}

async function settle(): Promise<void> {
  effects.splice(0).forEach((effect) => effect());
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

/** The read-status line: a hook-free component, drawn here from its props. */
function readStatus(tree: Node): Node | null {
  const status = find(tree, (node) => typeof node.type === "function" && "read" in (node.props || {}))!;
  return (status.type as (props: unknown) => Node | null)(status.props);
}

const saveButton = (tree: Node) => find(tree, (node) => node.type === "button" && node.props?.class === "solid-btn")!;
const methods = () => mocks.fetch.mock.calls.map(([, init]) => String((init as RequestInit | undefined)?.method || "GET"));

beforeEach(() => {
  hookState.length = 0;
  hookCursor = 0;
  effects.length = 0;
  mocks.fetch.mockReset();
  mocks.custTab.mockReset();
  vi.stubGlobal("fetch", mocks.fetch);
});
afterEach(() => {
  nestedEditor.value = null;
  vi.unstubAllGlobals();
});

describe.each([
  ["specialist", "/api/v1/specialists/reviewer", { name: "reviewer", description: "d", system_prompt: "p" }],
  ["skill", "/api/v1/skills/reviewer", { name: "reviewer", description: "d", body: "b" }],
] as const)("the %s editor", (kind, url, row) => {
  it("shows a failed read and saves nothing until a read succeeds", async () => {
    nestedEditor.value = { kind, name: "reviewer" };
    mocks.fetch.mockResolvedValueOnce(response({ error: "daemon restarting" }, 503));
    renderForm();
    await settle();

    let tree = renderForm();
    const failed = readStatus(tree);
    expect(find(failed, (node) => node.props?.["data-editor-read-error"] === "1")).not.toBeNull();
    expect(saveButton(tree).props!.disabled).toBe(true);
    await saveButton(tree).props!.onClick!();
    expect(methods()).toEqual(["GET"]);

    mocks.fetch.mockResolvedValueOnce(response(row));
    const retry = find(failed, (node) => node.type === "button")!;
    retry.props!.onClick!();
    renderForm();
    await settle();

    tree = renderForm();
    expect(mocks.fetch.mock.calls.map(([called]) => called)).toEqual([url, url]);
    expect(readStatus(tree)).toBeNull();
    expect(saveButton(tree).props!.disabled).toBe(false);
    expect(find(tree, (node) => node.props?.value === "d")).not.toBeNull();
  });
});
