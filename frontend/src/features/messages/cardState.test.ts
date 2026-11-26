/**
 * A running cell's card says how long it has been running, and keeps the
 * duration once the cell ends. A long cell with no output used to show one
 * still glyph for minutes.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { markCardRunning, markCardSucceeded } from "./cardState";

class Node {
  className = "";
  textContent = "";
  dataset: Record<string, string> = {};
  children: Node[] = [];
  classList = {
    add: (name: string) => {
      if (!this.className.split(" ").includes(name)) this.className = (this.className + " " + name).trim();
    },
    remove: (name: string) => {
      this.className = this.className
        .split(" ")
        .filter((c) => c && c !== name)
        .join(" ");
    },
  };
  appendChild(child: Node): Node {
    this.children.push(child);
    return child;
  }
  querySelector(sel: string): Node | null {
    const want = sel.replace(/^\./, "");
    for (const child of this.children) {
      if (child.className.split(" ").includes(want)) return child;
      const deeper = child.querySelector(sel);
      if (deeper) return deeper;
    }
    return null;
  }
  setAttribute(): void {}
  set innerHTML(_value: string) {}
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("a cell card's elapsed time", () => {
  it("is the cell's duration once it ends, and nothing for a cell under two seconds", () => {
    vi.stubGlobal("document", { createElement: () => new Node(), querySelector: () => null });
    const now = vi.spyOn(Date, "now");
    const card = new Node();
    const head = card.appendChild(new Node());
    head.className = "a-head";
    head.appendChild(new Node()).className = "ic";

    now.mockReturnValue(1_000_000);
    markCardRunning(card as unknown as HTMLElement);
    now.mockReturnValue(1_000_000 + 754_000);
    markCardSucceeded(card as unknown as HTMLElement);
    expect(head.querySelector(".a-time")?.textContent).toBe("12:34");

    const quick = new Node();
    const quickHead = quick.appendChild(new Node());
    quickHead.className = "a-head";
    now.mockReturnValue(2_000_000);
    markCardRunning(quick as unknown as HTMLElement);
    now.mockReturnValue(2_000_000 + 900);
    markCardSucceeded(quick as unknown as HTMLElement);
    expect(quickHead.querySelector(".a-time")).toBeNull();
  });

  it("counts a cell still running at a reopen from its start event, not from the reopen (NB-4)", () => {
    vi.stubGlobal("document", { createElement: () => new Node(), querySelector: () => null });
    const now = vi.spyOn(Date, "now");
    const card = new Node();
    const head = card.appendChild(new Node());
    head.className = "a-head";

    // The replayed start event says the cell began 90 s before the reopen.
    now.mockReturnValue(5_000_000);
    markCardRunning(card as unknown as HTMLElement, { type: "notebook_cell_start", started_at: 4_910_000 });
    expect(card.dataset.startedAt).toBe("4910000");
    now.mockReturnValue(5_000_000 + 10_000);
    markCardSucceeded(card as unknown as HTMLElement);
    expect(head.querySelector(".a-time")?.textContent).toBe("1:40");
  });
});
