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

import { readSearchConfig, searchT } from "../../features/customize/search";
import { vendorKeyT } from "../customize/vendors/use-vendor-key";
import { SearchSetupCard, currentSearchChoice, type SearchSetupHandle } from "./SearchSetupCard";

type Node = { type?: unknown; props?: Record<string, unknown> & { children?: unknown } };

function textOf(node: unknown): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  return textOf((node as Node).props?.children);
}

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

// NAV-3: "Current order: You.com → Built-in keyless engines" sat above five
// radios none of which was selected, and choosing Doubao asked for the same
// plan key again without saying why.
describe("SearchSetupCard current choice", () => {
  beforeEach(() => {
    hookState.length = 0;
    mocks.api.mockReset();
    mocks.hint.mockReset();
  });

  function withConfig(raw: Record<string, unknown>) {
    render();
    hookState[0] = readSearchConfig(raw);
    return render();
  }

  const YOU_FIRST = {
    doubao_configured: false,
    engines: [
      { id: "you", enabled: true, usable: true },
      { id: "builtin", enabled: true, usable: true },
    ],
  };

  it("selects the choice the current order already is, with nothing to apply", () => {
    const tree = withConfig(YOU_FIRST);
    const checked = all(tree, (n) => n.type === "input" && n.props?.type === "radio" && n.props?.checked === true);
    expect(checked.map((r) => r.props?.value)).toEqual(["you"]);
    expect(all(tree, (n) => n.props?.["data-action"] === "onboarding-search-apply")).toEqual([]);
    expect(currentSearchChoice(readSearchConfig({ ...YOU_FIRST, doubao_configured: true }))).toBe("doubao");
  });

  it("names Doubao one way, and says under it that it needs an Agent Plan Key", () => {
    const tree = withConfig(YOU_FIRST);
    const label = all(tree, (n) => n.props?.["data-search-choice"] === "doubao")[0]!;
    expect(textOf(label)).toContain(searchT("search.doubao"));
    expect(searchT("search.setup.doubao")).toBe(searchT("search.doubao"));
    expect(textOf(label)).toContain(searchT("search.setup.doubao.sub"));
  });

  it("explains why Doubao asks for the plan key when the model's key is not shared", () => {
    const tree = withConfig(YOU_FIRST);
    const doubao = all(tree, (n) => n.type === "input" && n.props?.value === "doubao")[0]!;
    (doubao.props?.onChange as () => void)();
    const next = render();
    expect(all(next, (n) => n.props?.["data-search-doubao-note"] === "")).toHaveLength(1);
    expect(all(next, (n) => n.type === "input" && n.props?.type === "password")).toHaveLength(1);
    expect(all(next, (n) => n.props?.["data-action"] === "onboarding-search-apply")).toHaveLength(1);
  });
});

// ONB-2: choosing Doubao and pressing Continue moved on with the search order
// unchanged and nothing said: only the card's own Apply posted, and Apply's
// "needs a key" went to the composer status line under the modal.
describe("SearchSetupCard pending choice on Continue", () => {
  beforeEach(() => {
    hookState.length = 0;
    mocks.api.mockReset();
    mocks.hint.mockReset();
  });

  const YOU_FIRST = {
    doubao_configured: false,
    engines: [
      { id: "you", enabled: true, usable: true },
      { id: "builtin", enabled: true, usable: true },
    ],
  };

  /** Render with a config read and the Doubao config's `model_key_available`. */
  function setup(modelKeyAvailable = false) {
    const handle: SearchSetupHandle = { current: null };
    hookCursor = 0;
    SearchSetupCard({ pendingRef: handle });
    hookState[0] = readSearchConfig(YOU_FIRST);
    hookState[5] = modelKeyAvailable;
    const view = () => {
      hookCursor = 0;
      return SearchSetupCard({ pendingRef: handle });
    };
    return { handle, view };
  }

  const choose = (tree: unknown, id: string) =>
    (all(tree, (n) => n.type === "input" && n.props?.value === id)[0]!.props?.onChange as () => void)();
  const note = (tree: unknown) => all(tree, (n) => n.props?.["data-search-note"] !== undefined)[0];

  it("has nothing to apply when the choice did not change", async () => {
    const { handle, view } = setup();
    view();
    await expect(handle.current!()).resolves.toBe(true);
    expect(mocks.api).not.toHaveBeenCalled();
  });

  it("blocks Continue with the reason in the card when Doubao has no key", async () => {
    const { handle, view } = setup();
    choose(view(), "doubao");
    view();
    await expect(handle.current!()).resolves.toBe(false);
    expect(mocks.api).not.toHaveBeenCalled();
    // In the card, not the composer status line behind the modal.
    expect(mocks.hint).not.toHaveBeenCalled();
    const shown = note(view())!;
    expect(shown.props?.role).toBe("alert");
    expect(textOf(shown)).toBe(searchT("search.setup.needKey"));
  });

  it("applies the pending choice on Continue", async () => {
    const { handle, view } = setup();
    choose(view(), "doubao");
    const tree = view();
    const field = all(tree, (n) => n.props?.["data-onboarding-search-key"] === "")[0]!;
    (field.props?.onInput as (e: unknown) => void)({ target: { value: " plan-key " } });
    view();
    mocks.api.mockResolvedValue({ ...YOU_FIRST, doubao_configured: true });
    await expect(handle.current!()).resolves.toBe(true);
    expect(mocks.api).toHaveBeenCalledWith("/doubao-search/config", {
      method: "POST",
      body: JSON.stringify({ agent_plan_key: "plan-key" }),
    });
    expect(textOf(note(view()))).toBe(searchT("search.setup.applied"));
  });

  it("shows a refused save in the card and stays on the step", async () => {
    const { handle, view } = setup();
    choose(view(), "builtin");
    view();
    mocks.api.mockRejectedValue("quota");
    await expect(handle.current!()).resolves.toBe(false);
    expect(mocks.hint).not.toHaveBeenCalled();
    const shown = note(view())!;
    expect(shown.props?.role).toBe("alert");
    expect(textOf(shown)).toBe("quota");
  });

  it("offers the current model's key when the server can share it", async () => {
    const without = setup(false);
    choose(without.view(), "doubao");
    expect(all(without.view(), (n) => n.props?.["data-search-use-model-key"] !== undefined)).toEqual([]);

    hookState.length = 0;
    const { view } = setup(true);
    choose(view(), "doubao");
    const tree = view();
    const button = all(tree, (n) => n.props?.["data-search-use-model-key"] !== undefined)[0]!;
    expect(textOf(button)).toBe(vendorKeyT("vendor.useModelKey"));
    mocks.api.mockResolvedValue({ ...YOU_FIRST, doubao_configured: true });
    await (button.props?.onClick as () => Promise<void>)();
    // The server reads and saves the key; it never comes to the browser.
    expect(mocks.api).toHaveBeenCalledWith("/doubao-search/config", {
      method: "POST",
      body: JSON.stringify({ use_model_key: true }),
    });
    expect(textOf(note(view()))).toBe(searchT("search.setup.applied"));
  });
});
