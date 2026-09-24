import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const viewer = vi.hoisted(() => ({ open: vi.fn(), dock: vi.fn() }));
vi.mock("../artifacts/ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../artifacts/ui")>()),
  openViewer: viewer.open,
  dockOpen: viewer.dock,
}));

import { setLang, t } from "../../i18n/runtime";
import { resetStoreFields } from "../../stores/signal-field";
import { buildStepCard } from "./step";

/** Just enough DOM for step cards (no jsdom here). */
class El {
  tagName: string;
  classes = new Set<string>();
  children: El[] = [];
  parent: El | null = null;
  dataset: Record<string, string> = {};
  style: Record<string, unknown> = { setProperty: () => {} };
  title = "";
  onclick: (() => unknown) | null = null;
  private text = "";
  constructor(tag: string) {
    this.tagName = tag.toUpperCase();
  }
  classList = {
    add: (...names: string[]) => names.forEach((n) => this.classes.add(n)),
    remove: (...names: string[]) => names.forEach((n) => this.classes.delete(n)),
    contains: (name: string) => this.classes.has(name),
    toggle: (name: string, on?: boolean) => {
      const next = on === undefined ? !this.classes.has(name) : on;
      if (next) this.classes.add(name);
      else this.classes.delete(name);
      return next;
    },
  };
  set className(value: string) {
    this.classes = new Set(String(value).split(/\s+/).filter(Boolean));
  }
  get className(): string {
    return [...this.classes].join(" ");
  }
  get textContent(): string {
    return this.children.length ? this.children.map((c) => c.textContent).join("") : this.text;
  }
  set textContent(value: string) {
    this.children = [];
    this.text = String(value ?? "");
  }
  get innerHTML(): string {
    return this.text;
  }
  set innerHTML(value: string) {
    this.children = [];
    this.text = String(value).replace(/<[^>]*>/g, "");
  }
  get firstChild(): El | null {
    return this.children[0] ?? null;
  }
  appendChild(child: El): El {
    child.parent = this;
    this.children.push(child);
    return child;
  }
  remove(): void {
    if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this);
    this.parent = null;
  }
  setAttribute(): void {}
  querySelector(sel: string): El | null {
    return this.querySelectorAll(sel)[0] ?? null;
  }
  querySelectorAll(sel: string): El[] {
    const out: El[] = [];
    const want = sel.startsWith(".") ? sel.slice(1).split(".") : null;
    const walk = (node: El): void => {
      for (const child of node.children) {
        if (want ? want.every((c) => child.classes.has(c)) : child.tagName === sel.toUpperCase()) out.push(child);
        walk(child);
      }
    };
    walk(this);
    return out;
  }
}

let hintBox: El;

beforeEach(async () => {
  await setLang("en");
  resetStoreFields();
  viewer.open.mockReset();
  viewer.dock.mockReset();
  hintBox = new El("div");
  vi.stubGlobal("document", {
    createElement: (tag: string) => new El(tag),
    createTextNode: (text: string) => {
      const node = new El("#text");
      node.textContent = text;
      return node;
    },
    getElementById: (id: string) => (id === "composer-hint" ? hintBox : null),
    querySelector: (sel: string) => (sel === "#composer-hint" ? hintBox : null),
    querySelectorAll: () => [],
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function settle(): Promise<void> {
  for (let i = 0; i < 10; i++) await Promise.resolve();
}

describe("artifact step card", () => {
  it("reports a viewer that fails to open instead of leaving the rejection unhandled", async () => {
    viewer.open.mockReturnValue(Promise.reject(new Error("version lookup failed")));
    const handle = buildStepCard({
      step_id: "s-1",
      kind: "artifact",
      status: "done",
      output: { artifacts: [{ filename: "fig.png", artifact_id: "a-1" }] },
    });
    const name = (handle.card as unknown as El).querySelector(".s-fn.clk")!;
    name.onclick!();
    await settle();
    expect(viewer.dock).toHaveBeenCalledTimes(1);
    expect(viewer.open).toHaveBeenCalledWith(expect.objectContaining({ id: "a-1", filename: "fig.png" }));
    expect(hintBox.textContent).toContain(t("toast.failed", "version lookup failed"));
  });
});
