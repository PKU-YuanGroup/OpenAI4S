import { beforeEach, describe, expect, it, vi } from "vitest";

// First-run setup offers the same search choices as `openai4s init`; Parallel
// is keyless, so choosing it posts only `primary` and shows no key field.

const hookState: unknown[] = [];
let hookCursor = 0;

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
  useEffect: vi.fn(),
}));

const mocks = vi.hoisted(() => ({ api: vi.fn(), hint: vi.fn() }));
vi.mock("../../features/customize/api", () => ({
  api: mocks.api,
  apiErrorText: (e: unknown) => String(e),
}));
vi.mock("../../features/customize/host", () => ({ hint: mocks.hint }));

import { SearchSetupCard } from "./SearchSetupCard";

type Node = { type?: unknown; props?: Record<string, unknown> & { children?: unknown } };

function all(node: unknown, match: (node: Node) => boolean, out: Node[] = []): Node[] {
  if (Array.isArray(node)) {
    for (const child of node) all(child, match, out);
    return out;
  }
  if (!node || typeof node !== "object") return out;
  const current = node as Node;
  if (match(current)) out.push(current);
  all(current.props?.children, match, out);
  return out;
}

function render() {
  hookCursor = 0;
  return SearchSetupCard();
}

describe("SearchSetupCard", () => {
  beforeEach(() => {
    hookState.length = 0;
    mocks.api.mockReset();
    mocks.hint.mockReset();
  });

  it("offers Parallel and applies it without a key", async () => {
    const radios = all(render(), (n) => n.type === "input" && n.props?.type === "radio");
    expect(radios.map((r) => r.props?.value)).toEqual(["doubao", "tavily", "you", "builtin", "parallel"]);

    (radios[4]?.props?.onChange as () => void)();
    const tree = render();
    expect(all(tree, (n) => n.type === "input" && n.props?.type === "password")).toEqual([]);

    mocks.api.mockResolvedValue({ engines: [], doubao_configured: false });
    const apply = all(tree, (n) => n.props?.["data-action"] === "onboarding-search-apply")[0]!;
    await (apply.props?.onClick as () => Promise<void>)();
    expect(mocks.api).toHaveBeenCalledWith("/search/config", {
      method: "POST",
      body: JSON.stringify({ primary: "parallel" }),
    });
    expect(mocks.hint).not.toHaveBeenCalledWith(expect.anything(), true);
  });
});
