/**
 * Background cells still running are on screen, each with its own Stop, and a
 * turn's Stop says when it stopped that turn's background cells too.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLang } from "../../i18n/runtime";
import { currentId } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import {
  backgroundCopy,
  backgroundStoppedHint,
  refreshBackgroundCells,
  renderBackgroundStrip,
  resetBackgroundCells,
} from "./background";

class Node {
  className = "";
  textContent = "";
  title = "";
  type = "";
  disabled = false;
  dataset: Record<string, string> = {};
  children: Node[] = [];
  onclick: (() => void) | null = null;
  private classes = new Set<string>();
  constructor(public tag = "div") {}
  classList = {
    toggle: (name: string, on?: boolean) => {
      const next = on ?? !this.classes.has(name);
      if (next) this.classes.add(name);
      else this.classes.delete(name);
      return next;
    },
    contains: (name: string) => this.classes.has(name),
    add: (name: string) => this.classes.add(name),
    remove: (name: string) => this.classes.delete(name),
  };
  set innerHTML(_value: string) {
    this.children = [];
  }
  appendChild(child: Node): Node {
    this.children.push(child);
    return child;
  }
  all(): Node[] {
    return this.children.flatMap((child) => [child, ...child.all()]);
  }
}

let strip: Node;
let posts: string[];

function serve(cells: unknown[]): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: { method?: string }) => {
      if ((init?.method || "GET") === "POST") posts.push(String(url));
      return {
        ok: true,
        status: 200,
        text: async () =>
          JSON.stringify((init?.method || "GET") === "POST" ? { ok: true, status: "stopping" } : { cells }),
      };
    }),
  );
}

beforeEach(async () => {
  resetStoreFields();
  resetBackgroundCells();
  await setLang("zh");
  strip = new Node();
  posts = [];
  vi.stubGlobal("document", {
    getElementById: (id: string) => (id === "bg-strip" ? strip : null),
    createElement: (tag: string) => new Node(tag),
    querySelectorAll: () => [],
  });
  currentId.value = "f-bg";
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("background cells", () => {
  it("lists the running ones with their first statement, elapsed time and last line", async () => {
    serve([
      {
        exec_id: "exec-1",
        status: "running",
        started_at: 1_000_000,
        launched_by: "agent",
        code_preview: "for i in range(600):",
        output_tail: "tick 41",
      },
      { exec_id: "exec-2", status: "done", code_preview: "print(1)" },
    ]);
    await refreshBackgroundCells();
    renderBackgroundStrip(1_000_000 + 83_000);
    expect(strip.classList.contains("hidden")).toBe(false);
    const texts = strip.all().map((node) => node.textContent);
    expect(texts).toContain(backgroundCopy("head", 1));
    expect(texts).toContain("for i in range(600):");
    expect(texts).toContain("↳ tick 41");
    expect(texts).toContain("1:23");
    expect(texts).not.toContain("print(1)");
  });

  it("stops one cell from its row", async () => {
    vi.useFakeTimers();
    serve([{ exec_id: "exec-1", status: "running", code_preview: "loop()" }]);
    await refreshBackgroundCells();
    const stop = strip.all().find((node) => node.className.includes("bg-stop"))!;
    expect(stop.textContent).toBe(backgroundCopy("stop"));
    stop.onclick!();
    expect(stop.disabled).toBe(true);
    await vi.waitFor(() => expect(posts).toEqual(["/api/v1/frames/f-bg/background/exec-1/stop"]));
  });

  it("is hidden when nothing runs in the background", async () => {
    serve([]);
    await refreshBackgroundCells();
    expect(strip.classList.contains("hidden")).toBe(true);
  });

  it("words a Stop that also stopped the turn's background cells", () => {
    expect(backgroundStoppedHint({ ok: true, background_stopped: ["exec-1", "exec-2"] })).toBe(
      "已停止，并停止了本回合启动的 2 个后台任务",
    );
    expect(backgroundStoppedHint({ ok: true })).toBe("");
    expect(backgroundStoppedHint(null)).toBe("");
  });
});
