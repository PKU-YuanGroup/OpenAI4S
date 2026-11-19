import { describe, expect, it, vi } from "vitest";
import { KATEX_OPTIONS, typesetMath, type KatexApi } from "./math";

class FakeMath {
  isConnected = true;
  classes = new Set<string>(["md-math"]);
  rendered: string | null = null;
  constructor(public textContent: string, private display: "0" | "1") {}
  getAttribute(name: string): string | null {
    return name === "data-display" ? this.display : null;
  }
  classList = {
    add: (name: string) => this.classes.add(name),
  };
}

function root(nodes: FakeMath[]): ParentNode {
  return {
    querySelectorAll: () => nodes.filter((node) => !node.classes.has("md-math-done")),
  } as unknown as ParentNode;
}

describe("typesetMath (ART-2)", () => {
  it("loads nothing when the document has no math", async () => {
    const load = vi.fn();
    expect(await typesetMath(root([]), load)).toBe(0);
    expect(load).not.toHaveBeenCalled();
  });

  it("renders each formula with the inert options, in its own display mode", async () => {
    const calls: Array<[string, Record<string, unknown>]> = [];
    const katex: KatexApi = {
      render: (tex, element, options) => {
        calls.push([tex, options]);
        (element as unknown as FakeMath).rendered = tex;
      },
    };
    const inline = new FakeMath("a_1 < b", "0");
    const block = new FakeMath("\\frac{a}{b}", "1");
    expect(await typesetMath(root([inline, block]), async () => katex)).toBe(2);
    expect(calls.map(([tex]) => tex)).toEqual(["a_1 < b", "\\frac{a}{b}"]);
    expect(calls[0]?.[1]).toMatchObject({ ...KATEX_OPTIONS, displayMode: false });
    expect(calls[1]?.[1]).toMatchObject({ trust: false, throwOnError: false, displayMode: true });
    expect(inline.classes.has("md-math-done")).toBe(true);
  });

  it("keeps the source on screen when the chunk cannot load or a formula is refused", async () => {
    const lost = new FakeMath("x^2", "0");
    expect(await typesetMath(root([lost]), async () => Promise.reject(new Error("offline")))).toBe(0);
    expect(lost.classes.has("md-math-raw")).toBe(true);
    expect(lost.textContent).toBe("x^2");

    const bad = new FakeMath("\\bad{", "0");
    const katex: KatexApi = {
      render: () => {
        throw new Error("parse");
      },
    };
    expect(await typesetMath(root([bad]), async () => katex)).toBe(0);
    expect(bad.classes.has("md-math-raw")).toBe(true);
    expect(bad.textContent).toBe("\\bad{");
  });

  it("skips a view replaced while the chunk loaded", async () => {
    const gone = new FakeMath("x", "0");
    gone.isConnected = false;
    const render = vi.fn();
    expect(await typesetMath(root([gone]), async () => ({ render }))).toBe(0);
    expect(render).not.toHaveBeenCalled();
  });
});
