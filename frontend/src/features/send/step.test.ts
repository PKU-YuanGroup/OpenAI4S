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
import { reviewFailureText, reviewRefusalText } from "./review-copy";
import { continueInNewSession } from "./skill-network";
import { addLiveStep, buildStepCard, relabelStepCards, renderStoredStep, updateLiveStep } from "./step";

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
  get parentElement(): El | null {
    return this.parent;
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
    // A DOM append moves a node that already has a parent.
    child.remove();
    child.parent = this;
    this.children.push(child);
    return child;
  }
  remove(): void {
    if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this);
    this.parent = null;
  }
  contains(node: El | null): boolean {
    for (let cur = node; cur; cur = cur.parent) if (cur === this) return true;
    return false;
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

  it("cards already on screen say their title and status again after a language switch (I18N-1)", async () => {
    await setLang("zh");
    const read = renderStoredStep({
      step_id: "l-1",
      kind: "read",
      status: "done",
      title: "Reading project file cohort.csv",
      summary: "12 results",
    }) as unknown as El;
    const call = renderStoredStep({ step_id: "l-2", kind: "mcp", status: "running", title: "Calling calc via example" }) as unknown as El;
    const words = (card: El) => [card.querySelector(".s-lbl")!.textContent, card.querySelector(".s-meta")!.textContent];
    expect(words(read)).toEqual(["读取项目文件 cohort.csv", "12 条结果"]);
    expect(words(call)).toEqual(["通过 example 调用 calc", ""]);
    await setLang("en");
    relabelStepCards();
    expect(words(read)).toEqual(["Reading project file cohort.csv", "12 results"]);
    expect(words(call)).toEqual(["Calling calc via example", ""]);
    await setLang("zh");
    relabelStepCards();
    expect(words(read)).toEqual(["读取项目文件 cohort.csv", "12 条结果"]);
  });

  it("a step's structured summary is worded in the UI language, its English line kept for the fallback (CHAT-6)", async () => {
    await setLang("zh");
    const envs = renderStoredStep({
      step_id: "e-1",
      kind: "env",
      status: "done",
      title: "Listing runtime environments",
      summary: "4 envs, 3 missing",
      output: {
        environments: [],
        summary_parts: [
          { key: "count", unit: "env", n: 4 },
          { key: "missing", n: 3 },
        ],
      },
    }) as unknown as El;
    const meta = (card: El) => card.querySelector(".s-meta")!.textContent;
    expect(meta(envs)).toBe("4 个运行环境 · 缺少 3 个软件包");
    // A part this build does not know: the English line, translated by pattern.
    const future = renderStoredStep({
      step_id: "e-2",
      kind: "skill",
      status: "done",
      title: "Loading figure-style skill guidance",
      summary: "loaded",
      output: { summary_parts: [{ key: "somethingNew" }] },
    }) as unknown as El;
    expect(meta(future)).toBe("已加载");
    await setLang("en");
    relabelStepCards();
    expect(meta(envs)).toBe("4 envs · 3 missing");
  });

  it("a generic step body does not dump the structured summary", () => {
    const card = open(
      buildStepCard({ step_id: "g-1", kind: "tool", status: "done", output: { result: "ok", summary_parts: [{ key: "done" }] } }),
    );
    expect(card.querySelector(".s-pre")!.textContent).toContain('"result": "ok"');
    expect(card.querySelector(".s-pre")!.textContent).not.toContain("summary_parts");
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
    const plan = renderStoredStep(
      { step_id: "h-3", kind: "plan", status: "done", output: { todos: [{ content: "Load", status: "completed" }] } },
      host as never,
    ) as unknown as El;
    expect(plan.classes.has("open")).toBe(true);
    expect(plan.querySelector(".s-body")!.children).toHaveLength(1);
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
    const live = startStream() as unknown as { wrap: El };
    addLiveStep({ step_id: "s-run", kind: "code", status: "running", input: { code: "fit()" } });
    updateLiveStep({ step_id: "s-run", status: "done", output: { stdout: "converged" } });

    expect(messages.querySelectorAll(".step")).toHaveLength(1);
    // Moved into the replayed turn, where its event put it live.
    expect(stored.parent).toBe(live.wrap);
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
    // A figure opens from its thumbnail (its name is the caption, not also a list row).
    const name = (handle.card as unknown as El).querySelector(".s-fig")!;
    name.onclick!();
    await settle();
    expect(viewer.dock).toHaveBeenCalledTimes(1);
    expect(viewer.open).toHaveBeenCalledWith(expect.objectContaining({ id: "a-1", filename: "fig.png" }));
    expect(hintBox.textContent).toContain(t("toast.failed", "version lookup failed"));
  });

  it("shows and opens the version this step produced, not the latest (ART-4)", async () => {
    viewer.open.mockReturnValue(Promise.resolve());
    const handle = buildStepCard({
      step_id: "s-2",
      kind: "artifact",
      status: "done",
      output: { artifacts: [{ filename: "volcano.png", artifact_id: "a-9", version_id: "v-old" }] },
    });
    const card = open(handle);
    const img = card.querySelector("img") as unknown as { src?: string } | null;
    expect(String(img?.src || "")).toContain("/artifacts/versions/v-old");
    const name = card.querySelector(".s-fig")!;
    name.onclick!();
    await settle();
    expect(viewer.open).toHaveBeenCalledWith(
      expect.objectContaining({ id: "a-9", version_id: "v-old", _exactVersion: true }),
    );
  });

  it("names each saved file once: figures by their caption, the rest in the list (CHAT-8)", () => {
    const handle = buildStepCard({
      step_id: "s-3",
      kind: "artifact",
      status: "done",
      input: { files: ["fit.png", "fit.svg", "table.csv"] },
      output: {
        artifacts: [
          { filename: "fit.png", artifact_id: "a-1" },
          { filename: "fit.svg", artifact_id: "a-2" },
          { filename: "table.csv", artifact_id: "a-3" },
        ],
      },
    });
    const card = handle.card as unknown as El;
    expect(card.querySelectorAll(".s-fn").map((node) => node.textContent)).toEqual(["table.csv"]);
    expect(card.querySelectorAll(".s-fig-cap").map((node) => node.textContent)).toEqual(["fit.png", "fit.svg"]);
    // Figures only: no empty "files [ ]" row.
    const figures = buildStepCard({
      step_id: "s-4",
      kind: "artifact",
      status: "done",
      output: { artifacts: [{ filename: "fit.png", artifact_id: "a-1" }] },
    }).card as unknown as El;
    expect(figures.querySelectorAll(".s-files")).toEqual([]);
    expect(figures.querySelectorAll(".s-fig-cap")).toHaveLength(1);
  });

  it("a Saving step is collapsed, lists files by name and size, and never shows the local path (M-50)", () => {
    const host = new El("div");
    const step = {
      step_id: "s-6",
      kind: "artifact",
      title: "Load data",
      status: "done",
      summary: "2 artifacts",
      input: { files: ["summary.csv", "notes.md"], environment: "python" },
      output: {
        artifacts: [
          { filename: "summary.csv", artifact_id: "a-1", version_id: "v-1", size_bytes: 96, storage_path: "/home/alice/work/summary.csv" },
          { filename: "notes.md", artifact_id: "a-2", version_id: "v-2", size_bytes: 17, storage_path: "/home/alice/work/notes.md" },
        ],
      },
    };
    const card = renderStoredStep(step, host as never) as unknown as El;
    expect(card.classes.has("open")).toBe(false);
    open({ card: card as unknown as HTMLElement });
    expect(card.querySelectorAll(".s-frow").map((row) => row.textContent)).toEqual(["summary.csv · 96 B", "notes.md · 17 B"]);
    const details = card.querySelector(".s-out-tgl")!;
    expect(details.textContent).toBe("Technical details");
    expect(card.querySelector(".s-json")!.style.display).toBe("none");
    expect(card.textContent).not.toContain("/home/alice");
    expect(card.textContent).not.toContain("storage_path");
    details.onclick!();
    expect(details.textContent).toBe("Hide technical details");
    expect(card.querySelector(".s-json")!.textContent).toContain('"version_id": "v-1"');
  });

  it("a Saving step stored without its summary still shows the count (SYNC-14)", async () => {
    const stored = {
      step_id: "s-5",
      kind: "artifact",
      title: "Load data",
      status: "done",
      summary: null as unknown as string,
      output: { artifacts: [{ filename: "a.csv", artifact_id: "a-1" }, { filename: "b.md", artifact_id: "a-2" }] },
    };
    const meta = (step: typeof stored): string =>
      ((buildStepCard(step).card as unknown as El).querySelector(".s-meta")?.textContent || "");
    expect(meta(stored)).toBe("2 artifacts");
    expect(meta({ ...stored, output: { artifacts: [stored.output.artifacts[0]!] } })).toBe("1 artifact");
    await setLang("zh");
    expect(meta(stored)).toBe("2 个产物");
    await setLang("en");
  });
});

describe("sub-agents side by side (SCI-22)", () => {
  it("a delegation is titled by its task, a child step names its sub-agent, and a child's slip is amber", async () => {
    const delegate = (id: string, request: string) =>
      buildStepCard({
        step_id: id,
        kind: "delegate",
        title: "Delegating to general",
        input: { specialist: "general", request },
        status: "running",
      }).card as unknown as El;
    const gc = delegate("d-1", "Compute the GC content of ATGCGC. Report it as a percentage.");
    const mw = delegate("d-2", "Estimate the peptide MW of MKTAYIAK");
    expect(gc.querySelector(".s-lbl")!.textContent).toBe("Sub-agent · Compute the GC content of ATGCGC.");
    expect(mw.querySelector(".s-lbl")!.textContent).toBe("Sub-agent · Estimate the peptide MW of MKTAYIAK");

    const child = (childId: string, status: string) =>
      buildStepCard({
        step_id: "c-" + childId + status,
        kind: "read",
        title: "Reading placeholder",
        input: { path: "placeholder", delegation: { delegation_child_id: childId, child_name: "general", depth: 1 } },
        status,
        output: status === "error" ? { error: "FileNotFoundError: placeholder" } : {},
      }).card as unknown as El;
    const a = child("child-aaaa1111", "error");
    const b = child("child-bbbb2222", "done");
    const tagA = a.querySelector(".s-child-tag")!.textContent;
    const tagB = b.querySelector(".s-child-tag")!.textContent;
    expect(tagA).toContain("general");
    expect(tagA).not.toBe(tagB);
    // The sub-agent's probe failed and it carried on: amber, not a red failure.
    expect(a.classes.has("err")).toBe(false);
    expect(a.classes.has("warn")).toBe(true);
    // A failure of the delegation itself stays red.
    const failed = buildStepCard({
      step_id: "d-3",
      kind: "delegate",
      title: "Delegating to general",
      input: { specialist: "general", request: "x" },
      status: "error",
      output: { error: "RuntimeError: kernel died" },
    }).card as unknown as El;
    expect(failed.classes.has("err")).toBe(true);

    await setLang("zh");
    expect(delegate("d-4", "计算 ATGC 的 GC 含量。").querySelector(".s-lbl")!.textContent).toBe("子代理 · 计算 ATGC 的 GC 含量。");
    await setLang("en");
  });
});

describe("a sub-agent's steps inside its delegation card (SCI-22)", () => {
  const child = (id: string, kind: string, extra: Record<string, unknown> = {}) => ({
    step_id: id,
    kind,
    title: kind === "read" ? "Reading placeholder" : "Loading skill",
    input: {
      path: "placeholder",
      delegation: { delegation_child_id: "child-aaaa1111", child_name: "general", depth: 1, parent_step_id: "d-1" },
    },
    ...extra,
  });
  const delegation = {
    step_id: "d-1",
    kind: "delegate",
    title: "Delegating to general",
    input: { specialist: "general", request: "Compute the GC content of ATGCGC." },
  };

  /** Each card's id and the id of the card it sits in ("" = the conversation). */
  const layout = (root: El): string[] =>
    root.querySelectorAll(".step").map((card) => {
      let up = card.parent;
      while (up && !up.classes.has("step")) up = up.parent;
      return `${card.dataset.stepId || card.querySelector(".s-lbl")!.textContent}<${up ? up.querySelector(".s-lbl")!.textContent : ""}`;
    });

  it("nests them under the card live, and a reload nests them the same way", () => {
    addLiveStep({ ...delegation, status: "running" });
    addLiveStep(child("c-1", "read", { status: "running" }));
    updateLiveStep({ step_id: "c-1", status: "error", output: { error: "FileNotFoundError: placeholder" } });
    addLiveStep(child("c-2", "skill", { status: "done" }));
    // The parent's own step after the delegation stays in the conversation.
    addLiveStep({ step_id: "s-x", kind: "read", title: "Reading plate.csv", status: "done" });
    updateLiveStep({ step_id: "d-1", status: "done", summary: "completed", output: {} });
    const live = layout(messages);
    const card = messages.querySelectorAll(".step-delegate")[0]!;
    expect(card.querySelector(".s-child-count")!.textContent).toBe("2 steps inside");
    // Folded with the card: the sub-agent's probes do not fill the conversation.
    expect(card.querySelector(".s-children")).not.toBeNull();
    expect(card.classes.has("open")).toBe(false);

    resetStoreFields();
    const history = new El("div");
    for (const row of [
      { ...delegation, status: "done", summary: "completed", output: {}, created_at: 1 },
      { ...child("c-1", "read"), status: "error", output: { error: "FileNotFoundError: placeholder" }, created_at: 2 },
      { ...child("c-2", "skill"), status: "done", created_at: 3 },
      { step_id: "s-x", kind: "read", title: "Reading plate.csv", status: "done", created_at: 4 },
    ]) {
      renderStoredStep(row, history as unknown as ParentNode);
    }
    expect(layout(history)).toEqual(live);
    expect(live).toEqual([
      "Sub-agent · Compute the GC content of ATGCGC.<",
      "Reading placeholder<Sub-agent · Compute the GC content of ATGCGC.",
      "Loading skill<Sub-agent · Compute the GC content of ATGCGC.",
      "Reading plate.csv<",
    ]);
  });

  it("leaves a step flat when its delegation card is not on screen", () => {
    const history = new El("div");
    renderStoredStep({ ...child("c-9", "read"), status: "done" }, history as unknown as ParentNode);
    expect(history.children.map((node) => node.classes.has("step"))).toEqual([true]);
  });
});

describe("one download, one row (SCI-11)", () => {
  const url = "https://files.rcsb.org/download/1CRN.pdb";
  const download = {
    step_id: "dl-1",
    kind: "fetch",
    title: "Downloading from files.rcsb.org",
    input: { url, path: "1crn.pdb", max_bytes: 67108864 },
  };
  const saving = { step_id: "sv-1", kind: "artifact", title: "Saving 1crn.pdb", input: { files: ["1crn.pdb"] } };
  const ask = {
    decision_id: "perm-1",
    frame_id: "f-1",
    tool: "web_download",
    title: "Downloading from files.rcsb.org",
    target: "files.rcsb.org",
    input: { url, path: "1crn.pdb", max_bytes: 67108864 },
  };

  /** Each card (step or approval) and the step card it sits in ("" = the conversation). */
  const rows = (root: El): string[] => {
    const out: string[] = [];
    const walk = (node: El, inside: string): void => {
      for (const kid of node.children) {
        const kind = kid.classes.has("perm-card") ? "approval" : kid.classes.has("step") ? [...kid.classes].find((c) => c.startsWith("step-"))! : "";
        if (kind) out.push(`${kind}<${inside}`);
        walk(kid, kid.classes.has("step") ? [...kid.classes].find((c) => c.startsWith("step-"))! : inside);
      }
    };
    walk(root, "");
    return out;
  };

  it("folds the allowed approval and the saved file into the download's card, live and after a reload", async () => {
    vi.stubGlobal("fetch", vi.fn());
    const { markPermCard, renderPermissionCard, renderStoredPermission } = await import("./permission");
    addLiveStep({ ...download, status: "running" });
    renderPermissionCard(ask);
    // Waiting on the user: the card is in the conversation.
    expect(rows(messages)).toContain("approval<");
    // As seen live: the download and its Saving step can stream in before
    // this page's own decision request returns.
    updateLiveStep({ step_id: "dl-1", status: "done", summary: "49,152 bytes", output: {} });
    addLiveStep({ ...saving, status: "done" });
    updateLiveStep({ step_id: "sv-1", status: "done", summary: "1 artifact", output: {} });
    markPermCard("perm-1", true, "once", {});
    const live = rows(messages);
    expect(live).toEqual(["step-fetch<", "approval<step-fetch", "step-artifact<step-fetch"]);

    resetStoreFields();
    const history = messages.appendChild(new El("div"));
    renderStoredStep({ ...download, status: "done", summary: "49,152 bytes", output: {}, created_at: 1 }, history as unknown as ParentNode);
    renderStoredPermission({ ...ask, state: "allowed", scope: "once", created_at: "2026-11-20T00:00:02Z" }, history as unknown as ParentNode);
    renderStoredStep({ ...saving, status: "done", summary: "1 artifact", output: {}, created_at: 3 }, history as unknown as ParentNode);
    expect(rows(history)).toEqual(live);
  });

  it("leaves a denied download's card, and a file the download did not write, where they are", async () => {
    const { renderStoredPermission } = await import("./permission");
    const history = messages.appendChild(new El("div"));
    renderStoredStep({ ...download, status: "error", output: { error: "denied" }, created_at: 1 }, history as unknown as ParentNode);
    renderStoredPermission({ ...ask, state: "denied", created_at: "2026-11-20T00:00:02Z" }, history as unknown as ParentNode);
    renderStoredStep(
      { ...saving, step_id: "sv-2", title: "Saving notes.md", input: { files: ["notes.md"] }, status: "done", created_at: 3 },
      history as unknown as ParentNode,
    );
    expect(rows(history)).toEqual(["step-fetch<", "approval<", "step-artifact<"]);
  });
});

describe("plan steps (CHAT-8)", () => {
  it("a turn keeps one plan card, repainted by each plan update", () => {
    const plan = (id: string, todos: Array<{ content: string; status: string }>): Record<string, unknown> => ({
      type: "step",
      step_id: id,
      kind: "plan",
      title: "Planning",
      status: "running",
      input: { todos },
    });
    addLiveStep(plan("p-1", [{ content: "Read the plate", status: "in_progress" }]));
    updateLiveStep({ step_id: "p-1", status: "done", summary: "1 step", output: {} });
    addLiveStep({ step_id: "s-x", kind: "read", title: "Reading plate.csv", status: "done" });
    addLiveStep(
      plan("p-2", [
        { content: "Read the plate", status: "completed" },
        { content: "Fit the curves", status: "in_progress" },
      ]),
    );
    updateLiveStep({ step_id: "p-2", status: "done", summary: "2 steps", output: {} });
    const cards = messages.querySelectorAll(".step-plan");
    expect(cards).toHaveLength(1);
    // The one card shows the latest plan, where the first one appeared.
    expect(cards[0]!.querySelectorAll(".s-todo-t").map((node) => node.textContent)).toEqual([
      "Read the plate",
      "Fit the curves",
    ]);
    expect(cards[0]!.querySelector(".s-meta")?.textContent).toBe("2 steps");
    expect(messages.querySelectorAll(".step")).toHaveLength(2);
  });
});

describe("a loaded Skill's network limit", () => {
  afterEach(async () => {
    await setLang("en");
  });

  const DOMAINS = ["api.crossref.org", "api.openalex.org", "eutils.ncbi.nlm.nih.gov"];
  const NOTICE = {
    step_id: "n-1",
    kind: "network",
    title: "literature-review limits this session's network",
    status: "done",
    summary: "limited to 3 domains",
    input: { skill: "literature-review" },
    output: {
      notice: "skill_network",
      skill: "literature-review",
      skill_domains: DOMAINS,
      skills: ["literature-review"],
      domains: DOMAINS,
    },
  };

  it("the notice card names the Skill and the count in the UI language, the domains on expand", async () => {
    await setLang("zh");
    const card = buildStepCard(NOTICE).card as unknown as El;
    expect(card.querySelector(".s-lbl")!.textContent).toBe(
      "已加载 literature-review：本会话联网限定为 3 个域名",
    );
    expect(card.querySelector(".s-meta")!.textContent).toBe("3 个域名");
    expect(card.classes.has("s-net-limited")).toBe(true);
    expect(card.querySelectorAll(".s-net-dom").map((chip) => chip.textContent)).toEqual(DOMAINS);
  });

  it("the English notice pluralizes the count", () => {
    const card = buildStepCard({ ...NOTICE, output: { ...NOTICE.output, domains: ["doi.org"] } })
      .card as unknown as El;
    expect(card.querySelector(".s-lbl")!.textContent).toBe(
      "Loaded literature-review: this session's network is limited to 1 domain",
    );
  });

  it("the Skill's load card says it narrowed the session, not just loaded", async () => {
    await setLang("zh");
    const card = buildStepCard({
      step_id: "l-1",
      kind: "skill",
      title: "Loading literature-review skill guidance",
      status: "done",
      summary: "loaded · network limited to 3 domains",
      input: { name: "literature-review" },
      output: {
        name: "literature-review",
        content: "# Literature review",
        session_network: { skills: ["literature-review"], domains: DOMAINS },
      },
    }).card as unknown as El;
    expect(card.querySelector(".s-meta")!.textContent).toBe("已加载 · 本会话联网限定为 3 个域名");
    expect(card.querySelectorAll(".s-net-dom")).toHaveLength(3);
  });

  it("the load card names the built-in connectors the limit takes away (SCI-14)", async () => {
    await setLang("en");
    const card = buildStepCard({
      step_id: "l-2",
      kind: "skill",
      title: "Loading literature-review skill guidance",
      status: "done",
      summary: "loaded · network limited to 3 domains",
      input: { name: "literature-review" },
      output: {
        name: "literature-review",
        content: "# Literature review",
        session_network: {
          skills: ["literature-review"],
          domains: DOMAINS,
          unavailable_connectors: ["UniProtKB", "RCSB Protein Data Bank"],
        },
      },
    }).card as unknown as El;
    expect(card.querySelector(".s-net-lost")!.textContent).toBe(
      "This session can no longer use these built-in connectors: UniProtKB, RCSB Protein Data Bank.",
    );
    // An older step with no list says nothing rather than "none".
    const old = buildStepCard({ ...NOTICE }).card as unknown as El;
    expect(old.querySelector(".s-net-lost")).toBeNull();
  });

  it("a shell command the limit refused says so instead of a bare failure", async () => {
    await setLang("zh");
    const reason =
      "skill host_only manifest does not declare destination 'eutils.ncbi.nlm.nih.gov': the loaded literature-review Skill limits this session's network to 3 domains";
    const card = buildStepCard({
      step_id: "b-1",
      kind: "bash",
      title: "Running curl",
      status: "error",
      summary: "failed: " + reason,
      input: { command: "curl -s https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi" },
      output: {
        error: reason,
        blocked: {
          by: "skill_network",
          skills: ["literature-review"],
          domains: DOMAINS,
          destination: "eutils.ncbi.nlm.nih.gov",
          search: false,
        },
      },
    }).card as unknown as El;
    expect(card.querySelector(".s-meta")!.textContent).toBe("被技能的联网限制拦下");
    const text = card.querySelector(".s-net-text")!.textContent;
    expect(text).toContain("literature-review 把本会话联网限定为 3 个域名");
    expect(text).toContain("eutils.ncbi.nlm.nih.gov 不在其中");
    expect(card.querySelectorAll(".s-net-dom")).toHaveLength(3);
    // The server's own words stay available below the explanation.
    expect(card.querySelector(".s-net-blocked")!.querySelector("pre")!.textContent).toBe(reason);
    // SCI-14: and what to do now, with the way out as a button.
    expect(card.querySelector(".s-net-next")!.querySelector(".s-net-text")!.textContent).toContain("请在新会话中继续");
    expect(card.querySelector(".s-net-new")!.textContent).toBe("在新会话中继续");
  });

  it("an unavailable review says why and what to do, not an empty card (M-75)", async () => {
    await setLang("zh");
    const card = buildStepCard({
      step_id: "r-1",
      kind: "review",
      title: "Reviewer",
      status: "error",
      summary: "Review unavailable",
      input: { mode: "manual" },
      output: { verdict: "unavailable", reason: "model_unreachable", error: "LLM request timed out after 45s" },
    }).card as unknown as El;
    expect(card.querySelector(".s-lbl")!.textContent).toBe("审阅");
    expect(card.querySelector(".s-meta")!.textContent).toBe("审阅未完成");
    expect(card.querySelector(".review-detail")!.textContent).toBe(reviewFailureText("model_unreachable"));
    expect(card.querySelector(".review-detail")!.textContent).toContain("审阅模型没有及时回应");
    expect(card.querySelector("pre")!.textContent).toContain("timed out after 45s");
  });

  it("a refused review request is worded in the UI language (M-75)", async () => {
    await setLang("en");
    expect(reviewRefusalText({ code: "review_nothing_to_review", message: "there is nothing to review yet" })).toBe(
      "Nothing to review yet: this session has no message, code or file. Ask the agent something first.",
    );
    expect(reviewRefusalText(new Error("a previous review call is still finishing"))).toBe(
      "a previous review call is still finishing",
    );
    // An unknown reason still explains itself rather than staying silent.
    expect(reviewFailureText("something new")).toBe(reviewFailureText("internal"));
  });

  it("Continue in a new session opens one and carries the last request over (SCI-14)", async () => {
    const composer = { value: "", focus: vi.fn(), dispatchEvent: vi.fn() };
    const bubbles = [{ textContent: "find 2 papers" }, { textContent: " re-check crambin on rest.uniprot.org " }];
    const order: string[] = [];
    vi.stubGlobal("Event", class { constructor(readonly type: string) {} });
    vi.stubGlobal("document", {
      createElement: (tag: string) => new El(tag),
      getElementById: (id: string) => (id === "composer" ? composer : null),
      querySelectorAll: (sel: string) => (sel === "#messages .msg.user .bubble" ? bubbles : []),
    });
    vi.stubGlobal("newSession", async () => {
      // The draft is read before the new session replaces the transcript.
      order.push("new");
      bubbles.length = 0;
    });
    await continueInNewSession();
    expect(order).toEqual(["new"]);
    expect(composer.value).toBe("re-check crambin on rest.uniprot.org");
    expect(composer.focus).toHaveBeenCalled();

    // Something already typed there is the user's, and stays.
    composer.value = "my own words";
    bubbles.push({ textContent: "older request" });
    await continueInNewSession();
    expect(composer.value).toBe("my own words");
  });

  it("a search the limit refused reads as blocked, not as 0 results", async () => {
    await setLang("zh");
    const reason =
      "skill host_only manifest does not declare any enabled search engine (You.com, DuckDuckGo/Bing/Mojeek): the loaded text-features Skill limits this session's network to 1 domain (api.typesafe.ai).";
    const card = buildStepCard({
      step_id: "q-1",
      kind: "search",
      title: "Searching the web",
      status: "error",
      summary: "failed: " + reason,
      input: { query: "GLP-1 Alzheimer" },
      output: {
        error: reason,
        blocked: {
          by: "skill_network",
          skills: ["text-features"],
          domains: ["api.typesafe.ai"],
          destination: "",
          search: true,
        },
      },
    }).card as unknown as El;
    expect(card.querySelector(".s-meta")!.textContent).toBe("被技能的联网限制拦下");
    // A refusal is its own state: neither the red failure nor a neutral done.
    expect(card.classes.has("warn")).toBe(true);
    expect(card.classes.has("err")).toBe(false);
    const text = card.querySelector(".s-net-text")!.textContent;
    expect(text).toContain("text-features 把本会话联网限定为 1 个域名");
    expect(text).toContain("这次检索没有发出");
    expect(card.querySelector(".s-q")!.textContent).toBe("“GLP-1 Alzheimer”");
  });

  it("a step the user declined is a refusal in the UI language, not a red failure", async () => {
    await setLang("zh");
    const card = buildStepCard({
      step_id: "d-1",
      kind: "tool",
      title: "Defining dynamic tool noop",
      status: "error",
      summary: "failed: Permission denied: denied by user",
      input: { name: "noop" },
      output: { error: "Permission denied: denied by user" },
    }).card as unknown as El;
    expect(card.classes.has("err")).toBe(false);
    expect(card.classes.has("declined")).toBe(true);
    expect(card.querySelector(".s-meta")!.textContent).toBe("你拒绝了 · 未执行");
    expect(card.textContent).not.toContain("Permission denied");
  });

  it("a step a Stop cancelled while it waited for approval says stopped, not denied", async () => {
    await setLang("zh");
    const handle = buildStepCard({
      step_id: "d-2",
      kind: "bash",
      title: "Running ls",
      status: "error",
      summary: "failed: Permission denied: turn cancelled",
      input: { command: "ls" },
      output: { error: "Permission denied: turn cancelled" },
    });
    const card = open(handle);
    expect(card.classes.has("err")).toBe(false);
    expect(card.querySelector(".s-meta")!.textContent).toBe("已随任务停止 · 未执行");
    expect(card.textContent).not.toContain("已拒绝");
    expect(card.textContent).not.toContain("turn cancelled");
  });

  it("a rule's refusal keeps its reason, and a real error stays a failure", async () => {
    await setLang("en");
    const refused = open(
      buildStepCard({
        step_id: "d-3",
        kind: "mcp",
        status: "error",
        output: { error: "Permission denied: blocked by a standing 'deny' permission rule" },
      }),
    );
    expect(refused.querySelector(".s-meta")!.textContent).toBe("Not approved · not run");
    expect(refused.textContent).toContain("Reason: blocked by a standing 'deny' permission rule");
    const failed = buildStepCard({
      step_id: "d-4",
      kind: "bash",
      status: "error",
      summary: "failed: exit 1",
      output: { error: "PermissionError: [Errno 13] Permission denied: '/etc/shadow'" },
    }).card as unknown as El;
    expect(failed.classes.has("err")).toBe(true);
    expect(failed.classes.has("declined")).toBe(false);
  });

  it("an ordinary load card keeps the server's summary", () => {
    const card = buildStepCard({
      step_id: "l-2",
      kind: "skill",
      status: "done",
      summary: "loaded",
      output: { name: "figure-style", content: "# Figure style" },
    }).card as unknown as El;
    expect(card.querySelector(".s-meta")!.textContent).toBe("loaded");
    expect(card.classes.has("s-net-limited")).toBe(false);
  });
});
