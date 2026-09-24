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

const mocks = vi.hoisted(() => ({
  fetch: vi.fn(),
  custTab: vi.fn(),
  alive: (): boolean => true,
  theme: "light",
  layout: "comfortable",
}));
vi.mock("../../i18n", () => ({
  LANG: "en",
  t: (key: string, ...args: unknown[]) => [key, ...args].join(" "),
  tOptional: () => null,
  setLang: vi.fn(),
  onLanguageChange: () => () => undefined,
}));
vi.mock("./use-timer-lease", () => ({ useAlive: () => mocks.alive, useTimerLease: () => ({}) }));
vi.mock("../../features/customize/actions", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../features/customize/actions")>()),
  custTab: mocks.custTab,
}));
vi.mock("../../features/theme/theme", () => ({
  getTheme: () => mocks.theme,
  setTheme: (mode: string) => {
    mocks.theme = mode;
  },
}));
vi.mock("../../features/customize/layout", () => ({
  getLayout: () => mocks.layout,
  setLayout: (name: string) => {
    mocks.layout = name;
  },
}));

import { GeneralTab } from "./GeneralTab";
import { Seg } from "./ui";

type Node = {
  type?: unknown;
  props?: Record<string, unknown> & { children?: unknown; value?: string; onPick?: (value: string) => void };
};

function segments(node: unknown, out: Node[] = []): Node[] {
  if (Array.isArray(node)) {
    node.forEach((child) => segments(child, out));
    return out;
  }
  if (!node || typeof node !== "object") return out;
  const current = node as Node;
  if (current.type === Seg) out.push(current);
  segments(current.props?.children, out);
  return out;
}

function render(): Node {
  hookCursor = 0;
  return GeneralTab() as Node;
}

beforeEach(() => {
  hookState.length = 0;
  hookCursor = 0;
  effects.length = 0;
  mocks.theme = "light";
  mocks.layout = "comfortable";
  mocks.custTab.mockReset();
  mocks.fetch.mockReset().mockImplementation(() => Promise.resolve(new Response("{}")));
  vi.stubGlobal("fetch", mocks.fetch);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("General theme and layout", () => {
  it("move their own segment without remounting or re-reading the tab", async () => {
    render();
    effects.splice(0).forEach((effect) => effect());
    await vi.waitFor(() => expect(mocks.fetch).toHaveBeenCalledTimes(1));

    const [theme, layout] = segments(render());
    theme!.props!.onPick!("dark");
    layout!.props!.onPick!("compact");

    const [shownTheme, shownLayout] = segments(render());
    expect(shownTheme!.props!.value).toBe("dark");
    expect(shownLayout!.props!.value).toBe("compact");
    expect(mocks.custTab).not.toHaveBeenCalled();
    expect(effects).toHaveLength(0);
    expect(mocks.fetch).toHaveBeenCalledTimes(1);
  });
});
