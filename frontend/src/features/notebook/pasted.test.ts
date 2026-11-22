/**
 * SYNC-15 (decision 22): "Run in Notebook" for code pasted into the chat
 * composer. Runs in node with a minimal in-file DOM: the composer textarea and
 * the `#composer-run` host the Shell renders above it.
 */
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const dockTabs = vi.hoisted(() => [] as string[]);
vi.mock("../artifacts/ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../artifacts/ui")>()),
  setActiveTab: (tab: string) => {
    dockTabs.push(tab);
  },
}));

import { i18nReady } from "../../i18n/runtime";
import { _kc, pendingReplIdentity } from "../../stores/notebook";
import { currentId } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { setNotebookApi } from "./cells";
import { nbT } from "./copy";
import { kernelView } from "./kernel";
import { composerCodeBlock, renderComposerRun, runComposerCode, withoutCodeBlock } from "./pasted";
import type { KernelStatus } from "./types";

class FakeNode {
  children: FakeNode[] = [];
  className = "";
  textContent = "";
  title = "";
  type = "";
  disabled = false;
  dataset: Record<string, string> = {};
  attrs: Record<string, string> = {};
  classes = new Set<string>();
  classList = {
    add: (name: string) => void this.classes.add(name),
    remove: (name: string) => void this.classes.delete(name),
    contains: (name: string) => this.classes.has(name),
  };
  onclick: ((event: { preventDefault(): void }) => unknown) | null = null;
  set innerHTML(_value: string) {
    this.children = [];
  }
  setAttribute(name: string, value: string): void {
    this.attrs[name] = value;
  }
  appendChild(child: FakeNode): FakeNode {
    this.children.push(child);
    return child;
  }
  get text(): string {
    return this.textContent + this.children.map((child) => child.text).join("");
  }
}

class FakeComposer {
  value = "";
  disabled = false;
  inputs = 0;
  dispatchEvent(event: { type: string }): boolean {
    if (event.type === "input") this.inputs += 1;
    return true;
  }
}

let composer: FakeComposer;
let host: FakeNode;

function stubDom(): void {
  composer = new FakeComposer();
  host = new FakeNode();
  host.classes.add("hidden");
  vi.stubGlobal("document", {
    getElementById: (id: string) => (id === "composer" ? composer : id === "composer-run" ? host : null),
    createElement: () => new FakeNode(),
    createTextNode: (text: string) => Object.assign(new FakeNode(), { textContent: text }),
  });
}

/** The session's last kernel read, as the Notebook keeps it. */
function kernel(st: KernelStatus): void {
  kernelView.value = { sid: "frame-1", st, envs: null, cur: null };
  _kc.value = { ..._kc.value, id: "frame-1", st: st as Record<string, unknown>, stAt: Date.now() };
}

const button = (): FakeNode | undefined => host.children[0];
const shown = (): boolean => !host.classes.has("hidden") && !!button();
const PASTED = "What does this print?\n\n```python\nprint(1 + 1)\n```\n";

beforeAll(async () => {
  await i18nReady();
});

beforeEach(() => {
  resetStoreFields();
  stubDom();
  currentId.value = "frame-1";
});

afterEach(() => {
  setNotebookApi(null);
  vi.unstubAllGlobals();
  resetStoreFields();
});

describe("finding the code in the composer", () => {
  it("takes the first closed Python or R block; a bare fence is Python", () => {
    expect(composerCodeBlock("```\nx <- 1\n```")).toMatchObject({ language: "python", code: "x <- 1" });
    expect(composerCodeBlock("```r\nsummary(cars)\n```")).toMatchObject({ language: "r", code: "summary(cars)" });
    expect(composerCodeBlock("```py\na = 1\nb = 2\n```")).toMatchObject({ language: "python", code: "a = 1\nb = 2" });
    // Not Python or R, empty, or still being typed: nothing to run.
    expect(composerCodeBlock("```bash\nls\n```")).toBeNull();
    expect(composerCodeBlock("```python\n   \n```")).toBeNull();
    expect(composerCodeBlock("```python\nprint(1)")).toBeNull();
    expect(composerCodeBlock("no code here")).toBeNull();
    // Several blocks: the first runnable one.
    const two = "```bash\nls\n```\nthen\n```r\nplot(1)\n```\n```python\nprint(2)\n```";
    expect(composerCodeBlock(two)).toMatchObject({ language: "r", code: "plot(1)" });
  });

  it("takes the block out and keeps the prose around it", () => {
    const text = "Look at this:\n```python\nprint(1)\n```\nand tell me why.";
    expect(withoutCodeBlock(text, composerCodeBlock(text)!)).toBe("Look at this:\n\nand tell me why.");
    expect(withoutCodeBlock(PASTED, composerCodeBlock(PASTED)!)).toBe("What does this print?");
  });
});

describe("the Run in Notebook button", () => {
  it("is hidden while manual cells are off", () => {
    kernel({ repl_enabled: false });
    composer.value = PASTED;
    renderComposerRun();
    expect(shown()).toBe(false);
  });

  it("is hidden without a code block", () => {
    kernel({ repl_enabled: true });
    composer.value = "Plot the residuals, please.";
    renderComposerRun();
    expect(shown()).toBe(false);
  });

  it("is shown for a pasted block, naming its language", () => {
    kernel({ repl_enabled: true });
    composer.value = PASTED;
    renderComposerRun();
    expect(shown()).toBe(true);
    expect(button()!.text).toContain(nbT("nb.composerRun.label", "Python"));
    composer.value = "```r\nsummary(cars)\n```";
    renderComposerRun();
    expect(button()!.text).toContain(nbT("nb.composerRun.label", "R"));
  });

  it("is hidden in a read-only copy and when the composer is disabled", () => {
    kernel({ repl_enabled: true, view_only: true, trust_state: "quarantined" });
    composer.value = PASTED;
    renderComposerRun();
    expect(shown()).toBe(false);
    kernel({ repl_enabled: true });
    composer.disabled = true;
    renderComposerRun();
    expect(shown()).toBe(false);
  });

  it("reads the session's kernel status once when a block appears with the Notebook closed", async () => {
    const reads: string[] = [];
    setNotebookApi(async (path) => {
      reads.push(path);
      return { repl_enabled: true };
    });
    composer.value = PASTED;
    renderComposerRun();
    expect(shown()).toBe(false);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(reads).toEqual(["/frames/frame-1/kernel"]);
    renderComposerRun();
    expect(shown()).toBe(true);
    renderComposerRun();
    expect(reads).toHaveLength(1);
  });

  it("runs the block as a manual cell through the REPL path, opens the Notebook and takes the block out", async () => {
    kernel({ repl_enabled: true });
    const posts: Array<{ path: string; body: Record<string, unknown> }> = [];
    setNotebookApi(async (path, init) => {
      posts.push({ path, body: JSON.parse(String(init?.body || "{}")) });
      return { status: "accepted", owner: { kind: "user_repl", id: "x" } };
    });
    composer.value = PASTED;
    renderComposerRun();
    dockTabs.length = 0;
    button()!.onclick!({ preventDefault() {} });
    await vi.waitFor(() => expect(dockTabs).toEqual(["notebook"]));
    expect(posts).toHaveLength(1);
    expect(posts[0]!.path).toBe("/frames/frame-1/kernel/execute");
    expect(posts[0]!.body).toMatchObject({ code: "print(1 + 1)", language: "python" });
    expect(String(posts[0]!.body.execution_id)).toMatch(/^repl-/);
    expect(composer.value).toBe("What does this print?");
    expect(composer.inputs).toBe(1);
    expect(shown()).toBe(false);
    expect(pendingReplIdentity.value).toMatchObject({ frame_id: "frame-1", accepted: true });
  });

  it("sends an R block as R", async () => {
    kernel({ repl_enabled: true });
    const bodies: Array<Record<string, unknown>> = [];
    setNotebookApi(async (_path, init) => {
      bodies.push(JSON.parse(String(init?.body || "{}")));
      return { status: "accepted" };
    });
    composer.value = "```r\nsummary(cars)\n```";
    expect(await runComposerCode(() => undefined)).toBe(true);
    expect(bodies[0]).toMatchObject({ code: "summary(cars)", language: "r" });
  });

  it("leaves the composer as it was when the run is refused", async () => {
    kernel({ repl_enabled: true });
    setNotebookApi(async () => {
      throw new Error("manual cells are off");
    });
    composer.value = PASTED;
    const opened = vi.fn();
    expect(await runComposerCode(opened)).toBe(false);
    expect(composer.value).toBe(PASTED);
    expect(composer.inputs).toBe(0);
    // The Notebook says why beside its input.
    expect(opened).toHaveBeenCalledTimes(1);
  });

  it("runs nothing while manual cells are off", async () => {
    kernel({ repl_enabled: false });
    const api = vi.fn();
    setNotebookApi(api);
    composer.value = PASTED;
    expect(await runComposerCode(() => undefined)).toBe(false);
    expect(api).not.toHaveBeenCalled();
  });
});
