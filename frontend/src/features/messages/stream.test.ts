import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const work = vi.hoisted(() => ({ rendered: 0, calls: 0 }));
vi.mock("../md/render", async (importOriginal) => {
  const real = await importOriginal<typeof import("../md/render")>();
  return {
    ...real,
    renderMd: (src: string | null | undefined) => {
      work.rendered += String(src ?? "").length;
      work.calls += 1;
      return real.renderMd(src);
    },
  };
});

import { resetStoreFields } from "../../stores/signal-field";
import { renderMd } from "../md/render";
import { feed, flushRender, sealText, startStream, type LiveStream } from "./stream";

/** Keeps raw HTML so a streamed answer can be compared with a whole render. */
class El {
  className = "";
  dataset: Record<string, string> = {};
  children: El[] = [];
  parentNode: El | null = null;
  html = "";
  classList = { add() {}, remove() {}, toggle() {}, contains: () => false };
  get innerHTML(): string {
    return this.children.length ? this.children.map((c) => c.innerHTML).join("") : this.html;
  }
  set innerHTML(value: string) {
    this.children = [];
    this.html = String(value);
  }
  insertAdjacentHTML(where: string, value: string): void {
    if (where !== "beforeend") throw new Error("unexpected position " + where);
    this.html += value;
  }
  appendChild(child: El): El {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
}

let host: El;

beforeEach(() => {
  resetStoreFields();
  work.rendered = 0;
  host = new El();
  vi.stubGlobal("requestAnimationFrame", () => 1);
  vi.stubGlobal("cancelAnimationFrame", () => undefined);
  vi.stubGlobal("document", {
    createElement: () => new El(),
    querySelector: (sel: string) => (sel === "#messages" ? host : null),
    getElementById: () => null,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

/** Stream `text` in `size`-char chunks, flushing after each as the rAF would. */
function stream(text: string, size = 50): LiveStream {
  const st = startStream()!;
  for (let i = 0; i < text.length; i += size) {
    feed("text", text.slice(i, i + size), { type: "text_chunk", block_type: "text" });
    flushRender(st);
  }
  return st;
}

function shown(st: LiveStream): string {
  return (st.md as unknown as El).innerHTML;
}

describe("streaming markdown", () => {
  it("renders each settled paragraph once instead of the whole prefix per advance", () => {
    const text = Array.from({ length: 200 }, (_, i) => `Paragraph ${i}: ` + "word ".repeat(18)).join("\n\n");
    const st = stream(text);
    // What is on screen is exactly the whole-text render.
    expect(shown(st)).toBe(renderMd(text));
    // Linear: the settled chunks once, plus a bounded tail per flush. The
    // whole-prefix re-render was ~50x the text at this size.
    expect(work.rendered).toBeLessThan(text.length * 8);
  });

  it("never settles a list that another item can still continue", () => {
    const intro = "Findings so far, as a loose list:\n\n";
    const items = Array.from({ length: 40 }, (_, i) => `- item ${i}: ` + "detail ".repeat(8)).join("\n\n");
    const text = intro + items + "\n\nThat is the whole list.\n\n" + "More prose ".repeat(20);
    const st = stream(text, 37);
    // One <ul>: a list split at a blank line would render as forty.
    expect(shown(st)).toBe(renderMd(text));
    expect(shown(st).match(/<ul>/g)).toHaveLength(1);
  });

  it("the final render is the whole text", () => {
    const text = "Intro paragraph that is long enough. ".repeat(20) + "\n\n```python\nprint(1)\n```\n\nDone.";
    const st = stream(text);
    sealText(st);
    expect(shown(st)).toBe(renderMd(text));
  });

  it("re-renders a growing unclosed code block less often as it grows", () => {
    const frames: Array<() => void> = [];
    let clock = 1000;
    vi.stubGlobal("requestAnimationFrame", (cb: () => void) => frames.push(cb));
    vi.spyOn(performance, "now").mockImplementation(() => clock);
    const flushesOver = (seconds: number, chunk: string): number => {
      const before = work.calls;
      for (let f = 0; f < seconds * 60; f++) {
        feed("text", chunk, { type: "text_chunk", block_type: "text" });
        clock += 1000 / 60;
        for (const cb of frames.splice(0)) cb();
      }
      return work.calls - before;
    };
    const st = startStream()!;
    // Prose settles and flushes at the old cadence: about every other frame.
    const prose = flushesOver(1, "A sentence of ordinary prose.\n\n");
    expect(prose).toBeGreaterThan(20);
    // An open fence: nothing after it can settle, so the tail is the block.
    feed("text", "```python\n" + "value = compute(value)  # step\n".repeat(1500), {
      type: "text_chunk",
      block_type: "text",
    });
    flushRender(st);
    const code = flushesOver(1, "value = compute(value)  # step\n");
    // ~45K chars of tail: one flush per ~230ms, not twenty a second.
    expect(code).toBeLessThanOrEqual(6);
    expect(code).toBeGreaterThan(0);
  });

  it("starts over inside a replaced answer instead of writing into the detached one", () => {
    const st = stream("First draft paragraph. ".repeat(30) + "\n\n" + "More. ".repeat(40));
    // candidate.ts swaps a reviewed answer in: a new md, the new text.
    const replaced = new El();
    st.md = replaced as unknown as HTMLElement;
    st.text = "The reviewed answer.";
    st._dirty = true;
    flushRender(st);
    expect(replaced.innerHTML).toBe(renderMd("The reviewed answer."));
    expect((st.sealed as unknown as El).parentNode).toBe(replaced);
  });
});
