import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const viewer = vi.hoisted(() => ({ open: vi.fn(), dock: vi.fn() }));
vi.mock("../artifacts/ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../artifacts/ui")>()),
  openViewer: viewer.open,
  dockOpen: viewer.dock,
}));

import { setLang, t } from "../../i18n/runtime";
import { resetStoreFields } from "../../stores/signal-field";
import { startStream } from "../messages/stream";
import { addLiveStep, buildStepCard, renderStoredStep, updateLiveStep } from "./step";

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
    // Strip to a fixed point, as the other test doubles do: a single pass over
    // `<<b>script>` leaves `<script>`. Not a sanitiser.
    let text = String(value),
      previous: string;
    do {
      previous = text;
      text = text.replace(/<[^>]*>/g, "");
    } while (text !== previous);
    this.text = text;
  }
  get firstChild(): El | null {
    return this.children[0] ?? null;
  }
  get isConnected(): boolean {
    let node: El = this;
    while (node.parent) node = node.parent;
    return node.tagName === "BODY";
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
let messages: El;

beforeEach(async () => {
  await setLang("en");
  resetStoreFields();
  viewer.open.mockReset();
  viewer.dock.mockReset();
  const body = new El("body");
  hintBox = body.appendChild(new El("div"));
  messages = body.appendChild(new El("div"));
  const byId: Record<string, El> = { "composer-hint": hintBox, messages };
  vi.stubGlobal("requestAnimationFrame", () => 1);
  vi.stubGlobal("cancelAnimationFrame", () => undefined);
  vi.stubGlobal("document", {
    createElement: (tag: string) => new El(tag),
    createTextNode: (text: string) => {
      const node = new El("#text");
      node.textContent = text;
      return node;
    },
    getElementById: (id: string) => byId[id] ?? null,
    querySelector: (sel: string) => (sel.startsWith("#") ? (byId[sel.slice(1)] ?? null) : null),
    querySelectorAll: () => [],
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function settle(): Promise<void> {
  for (let i = 0; i < 10; i++) await Promise.resolve();
}

function open(handle: { card: HTMLElement }): El {
  const card = handle.card as unknown as El;
  if (!card.classes.has("open")) card.querySelector(".s-head")!.onclick!();
  return card;
}

describe("step card copy follows the UI language", () => {
  afterEach(async () => {
    await setLang("en");
  });

  it("the output toggle, a running review and an untitled finding", async () => {
    await setLang("zh");
    const code = open(
      buildStepCard({ step_id: "c-1", kind: "code", status: "done", input: { code: "print(1)" }, output: { stdout: "1" } }),
    );
    const toggle = code.querySelector(".oc-out-tgl")!;
    expect(toggle.textContent).toBe("显示输出");
    toggle.onclick!();
    expect(toggle.textContent).toBe("隐藏输出");

    const running = buildStepCard({ step_id: "r-1", kind: "review", status: "running" });
    expect((running.card as unknown as El).querySelector(".s-meta")!.textContent).toBe("正在审阅");

    const finding = open(
      buildStepCard({
        step_id: "r-2",
        kind: "review",
        status: "done",
        output: { verdict: "issues", issues: [{ severity: "high" }] },
      }),
    );
    expect(finding.querySelector(".review-issue-head")!.querySelector("strong")!.textContent).toBe("审阅发现");
  });
});

describe("a collapsed step card builds its body on first expand", () => {
  const CODE = { kind: "code", input: { code: "print(1)" }, output: { stdout: "1" } };

  it("a history card nobody opened has no body yet; opening builds it", () => {
    const host = new El("div");
    const card = renderStoredStep({ step_id: "h-1", status: "done", ...CODE }, host as never) as unknown as El;
    const body = card.querySelector(".s-body")!;
    expect(card.classes.has("open")).toBe(false);
    expect(body.children).toHaveLength(0);
    card.querySelector(".s-head")!.onclick!();
    expect(card.classes.has("open")).toBe(true);
    expect(body.querySelector(".os-code")).not.toBeNull();
    // Closing and reopening does not rebuild what is already current.
    const built = body.children[0];
    card.querySelector(".s-head")!.onclick!();
    card.querySelector(".s-head")!.onclick!();
    expect(body.children[0]).toBe(built);
  });

  it("an update to a collapsed card waits as well, and the expand shows the update", () => {
    const host = new El("div");
    const card = renderStoredStep({ step_id: "h-2", status: "running", ...CODE }, host as never) as unknown as El;
    updateLiveStep({ step_id: "h-2", status: "done", output: { stdout: "the updated output" } });
    const body = card.querySelector(".s-body")!;
    expect(body.children).toHaveLength(0);
    card.querySelector(".s-head")!.onclick!();
    expect(body.querySelector(".oc-out")!.textContent).toBe("the updated output");
  });

  it("a card that opens itself builds at once, and the window contract stays eager", () => {
    const host = new El("div");
    const artifact = renderStoredStep(
      { step_id: "h-3", kind: "artifact", status: "done", output: { artifacts: [{ filename: "a.csv" }] } },
      host as never,
    ) as unknown as El;
    expect(artifact.classes.has("open")).toBe(true);
    expect(artifact.querySelector(".s-body")!.children).toHaveLength(1);
    // tests/browser_p1_controls.mjs reads a fresh buildStepCard's body unopened.
    const eager = buildStepCard({ step_id: "w-1", status: "done", ...CODE });
    expect((eager.body as unknown as El).children).toHaveLength(1);
  });
});

describe("opening a session whose turn is still running", () => {
  it("replayed steps update the stored cards instead of adding a second one", () => {
    // History renders the step the server persisted when it began.
    const stored = renderStoredStep({
      step_id: "s-run",
      kind: "code",
      status: "running",
      input: { code: "fit()" },
    }) as unknown as El;
    // The replay of the live turn: text_reset, then the same step's events.
    startStream();
    addLiveStep({ step_id: "s-run", kind: "code", status: "running", input: { code: "fit()" } });
    updateLiveStep({ step_id: "s-run", status: "done", output: { stdout: "converged" } });

    expect(messages.querySelectorAll(".step")).toHaveLength(1);
    expect(stored.classes.has("running")).toBe(false);
    stored.querySelector(".s-head")!.onclick!();
    expect(stored.querySelector(".oc-out")!.textContent).toBe("converged");
  });

  it("a card no longer on screen is not revived by a new turn", () => {
    const gone = renderStoredStep({ step_id: "s-old", kind: "code", status: "done" }) as unknown as El;
    gone.remove();
    startStream();
    addLiveStep({ step_id: "s-old", kind: "code", status: "running" });
    expect(messages.querySelectorAll(".step")).toHaveLength(1);
    expect(messages.querySelector(".step")).not.toBe(gone);
  });
});

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
