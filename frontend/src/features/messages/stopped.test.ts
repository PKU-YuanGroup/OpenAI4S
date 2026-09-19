import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { currentId } from "../../stores/session";
import { setLang } from "../../i18n/runtime";
import { resetStoreFields } from "../../stores/signal-field";
import { running, stream } from "../../stores/stream";
import { failureHint, failureMeta } from "../send/turn";
import { installNotebook } from "../notebook/install";
import { setNotebookRenderImpl } from "../notebook/scroll";
import { turnDone } from "../send/turn";
import { onEvent, resetWsHandlers } from "../ws/registry";
import { renderStored as renderOlderPage } from "../sessions/transcript";
import { renderStored } from "./list";
import { finishStoppedStream } from "./stopped";
import { feed, startStream, type LiveStream } from "./stream";

/** Just enough DOM for the stream and the stored renderer (no jsdom here). */
class FakeClassList {
  readonly tokens = new Set<string>();
  constructor(initial?: string) {
    if (initial) for (const t of initial.split(/\s+/).filter(Boolean)) this.tokens.add(t);
  }
  add(...names: string[]): void {
    for (const n of names) this.tokens.add(n);
  }
  remove(...names: string[]): void {
    for (const n of names) this.tokens.delete(n);
  }
  contains(name: string): boolean {
    return this.tokens.has(name);
  }
  toggle(name: string, force?: boolean): boolean {
    const on = force === undefined ? !this.tokens.has(name) : force;
    if (on) this.tokens.add(name);
    else this.tokens.delete(name);
    return on;
  }
  get value(): string {
    return [...this.tokens].join(" ");
  }
}

class FakeText {
  data: string;
  constructor(data: string) {
    this.data = data;
  }
  appendData(more: string): void {
    this.data += more;
  }
  get textContent(): string {
    return this.data;
  }
}

class FakeEl {
  tagName: string;
  id = "";
  classList = new FakeClassList();
  children: Array<FakeEl | FakeText> = [];
  parent: FakeEl | null = null;
  dataset: Record<string, string> = {};
  style: Record<string, string> = {};
  onclick: unknown = null;
  title = "";
  scrollTop = 0;
  scrollHeight = 0;
  clientHeight = 0;
  private _text = "";
  constructor(tag: string) {
    this.tagName = tag.toUpperCase();
  }
  get className(): string {
    return this.classList.value;
  }
  set className(value: string) {
    this.classList = new FakeClassList(value);
  }
  get textContent(): string {
    if (this.children.length) return this.children.map((c) => c.textContent).join("");
    return this._text;
  }
  set textContent(value: string) {
    this.children = [];
    this._text = value == null ? "" : String(value);
  }
  set innerHTML(value: string) {
    this.children = [];
    // Strip to a fixed point, as the other test doubles do: a single pass over
    // `<<b>script>` leaves `<script>`, and a test reading `.textContent` would
    // then see markup this double claims to have removed. Not a sanitiser.
    let text = String(value),
      previous: string;
    do {
      previous = text;
      text = text.replace(/<[^>]+>/g, "");
    } while (text !== previous);
    this._text = text;
  }
  get innerHTML(): string {
    return this._text;
  }
  setAttribute(name: string, value: string): void {
    this.dataset["attr:" + name] = value;
  }
  appendChild<T extends FakeEl | FakeText>(child: T): T {
    if (child instanceof FakeEl) child.parent = this;
    this.children.push(child);
    return child;
  }
  remove(): void {
    if (!this.parent) return;
    this.parent.children = this.parent.children.filter((c) => c !== this);
    this.parent = null;
  }
  querySelector(sel: string): FakeEl | null {
    return this.querySelectorAll(sel)[0] || null;
  }
  querySelectorAll(sel: string): FakeEl[] {
    const out: FakeEl[] = [];
    const walk = (node: FakeEl): void => {
      for (const child of node.children) {
        if (!(child instanceof FakeEl)) continue;
        if (matches(child, sel)) out.push(child);
        walk(child);
      }
    };
    walk(this);
    return out;
  }
}

function matches(node: FakeEl, sel: string): boolean {
  if (sel.startsWith("#")) return node.id === sel.slice(1);
  if (sel.startsWith(".")) return sel.slice(1).split(".").every((c) => node.classList.contains(c));
  return node.tagName === sel.toUpperCase();
}

class FakeDoc {
  body = new FakeEl("body");
  host = new FakeEl("div");
  constructor() {
    this.host.id = "messages";
    this.body.appendChild(this.host);
  }
  createElement(tag: string): FakeEl {
    return new FakeEl(tag);
  }
  createTextNode(data: string): FakeText {
    return new FakeText(data);
  }
  getElementById(id: string): FakeEl | null {
    return id === "messages" ? this.host : this.body.querySelector("#" + id);
  }
  querySelector(sel: string): FakeEl | null {
    if (sel === "#messages") return this.host;
    return this.body.querySelector(sel);
  }
  querySelectorAll(sel: string): FakeEl[] {
    return this.body.querySelectorAll(sel);
  }
}

let doc: FakeDoc;

function live(): LiveStream {
  return stream.value as LiveStream;
}

function cellHeader(): void {
  feed("tool", "⚙ run_python\n", { type: "text_chunk", block_type: "tool", cell_index: 1 });
  feed("tool", "↳ step 1\n", { type: "text_chunk", block_type: "tool" });
}

const marker = {
  type: "text_chunk",
  block_type: "text",
  chunk: "\n\n_Stopped by user._\n",
  cancelled: { reason: "user", request_id: "req-1", execution_id: "exec-1" },
};

beforeEach(async () => {
  await setLang("en");
  resetStoreFields();
  doc = new FakeDoc();
  vi.stubGlobal("document", doc);
  vi.stubGlobal("requestAnimationFrame", () => 1);
  vi.stubGlobal("cancelAnimationFrame", () => undefined);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("stopped turn marker", () => {
  it.each(["llm_stream_timeout", "llm_stream_interrupted", "no_progress"])(
    "offers explicit continuation for %s live and on reopen",
    async (code) => {
      currentId.value = "recoverable-frame";
      const send = vi.fn().mockResolvedValue(undefined);
      vi.stubGlobal("send", send);
      vi.stubGlobal("failureMeta", failureMeta);
      vi.stubGlobal("Node", FakeEl);
      const failure = { code, output_committed: true, request_id: "req-recovery" };
      startStream();
      feed("text", "Received part of the answer.\n", { type: "text_chunk", block_type: "text" });
      const wrap = live().wrap as unknown as FakeEl;
      turnDone("failed", failure);
      expect(wrap.querySelector(".msg-failure-meta")!.dataset.failureCode).toBe(code);
      expect(wrap.querySelectorAll(".turn-continue")).toHaveLength(1);
      expect(failureHint(failure)).not.toContain("retrying would repeat work");

      // A recoverable stop explains itself in the row's own text; the meta
      // row must not say it a second time.
      expect(wrap.querySelector(".msg-failure-meta")!.textContent).not.toContain("Continue to finish");
      expect(wrap.querySelector(".turn-continue")!.classList.contains("outline-btn")).toBe(true);

      const click = wrap.querySelector(".turn-continue")!.onclick as () => Promise<void>;
      // A draft in the composer is the user's; Continue refuses, visibly.
      const composer = new FakeEl("textarea") as FakeEl & { value: string; focus: () => void };
      composer.id = "composer";
      composer.value = "half a sentence";
      composer.focus = vi.fn();
      doc.body.appendChild(composer);
      await click();
      expect(send).not.toHaveBeenCalled();
      expect(composer.focus).toHaveBeenCalledTimes(1);
      composer.value = "";
      await click();
      expect(send).toHaveBeenCalledTimes(1);
      expect(send.mock.calls[0]![0]).toContain("completed work");
      running.value = true;
      await click();
      expect(send).toHaveBeenCalledTimes(1);
      running.value = false;

      for (const render of [renderStored, renderOlderPage]) {
        const restored = render({ role: "assistant", content: "Stopped.", failure }) as unknown as FakeEl;
        expect(restored.querySelectorAll(".turn-continue")).toHaveLength(1);
        expect(restored.querySelector(".msg-failure-meta")!.dataset.failureCode).toBe(code);
      }
      await click();
      expect(send).toHaveBeenCalledTimes(1); // an older failure cannot submit a new turn
      // ...and stops offering to: a clickable control that does nothing reads as broken.
      expect(wrap.querySelectorAll(".turn-continue")).toHaveLength(0);
      currentId.value = "another-frame";
      const latest = doc.host.querySelectorAll(".turn-continue").at(-1)!;
      await (latest.onclick as () => Promise<void>)();
      expect(send).toHaveBeenCalledTimes(1);
    },
  );

  it("renders the streamed marker as a stopped marker and stops the running card", () => {
    startStream();
    feed("text", "I have prepared a Python cell and am running it now.\n", {
      type: "text_chunk",
      block_type: "text",
    });
    cellHeader();
    const card = live().toolCard as unknown as FakeEl;
    expect(card).toBeTruthy();

    feed("text", marker.chunk, marker);

    const wrap = live().wrap as unknown as FakeEl;
    const markers = wrap.querySelectorAll(".msg-stopped");
    expect(markers).toHaveLength(1);
    expect(markers[0]!.dataset.stopReason).toBe("user");
    expect(markers[0]!.dataset.requestId).toBe("req-1");
    expect(markers[0]!.textContent).toContain("Stopped by user");
    expect(card.classList.contains("stopped")).toBe(true);
    expect(card.querySelector(".meta")!.textContent).toContain("Stopped");
    // Rendered as a marker, never appended to the markdown as prose.
    expect(live().text).not.toContain("_Stopped by user._");
    expect(live().full).not.toContain("_Stopped by user._");
  });

  it("a stopped card no longer claims it is running or succeeded", () => {
    startStream();
    // What cell_run.py streams for a cell with no leading comment.
    feed("tool", "⚙Running analysis · cell 3\n", {
      type: "text_chunk",
      block_type: "tool",
      cell_index: 3,
    });
    feed("tool", "↳ iteration 0\n", { type: "text_chunk", block_type: "tool" });
    const card = live().toolCard as unknown as FakeEl;
    const glyph = card.querySelector(".a-head")!.querySelector(".ic")!;
    // A running cell has not succeeded yet.
    expect(glyph.dataset["attr:data-icon"]).not.toBe("check");

    feed("text", marker.chunk, marker);

    expect(card.classList.contains("stopped")).toBe(true);
    expect(glyph.dataset["attr:data-icon"]).toBe("stop");
    const label = card.querySelector(".lbl")!.textContent;
    expect(label).not.toContain("Running");
    expect(label).toBe("Analysis · cell 3");
  });

  it("keeps the cell's own title on a stopped card", () => {
    startStream();
    feed("tool", "⚙Fit the growth model\n", { type: "text_chunk", block_type: "tool", cell_index: 2 });
    const card = live().toolCard as unknown as FakeEl;

    feed("text", marker.chunk, marker);

    expect(card.querySelector(".lbl")!.textContent).toBe("Fit the growth model");
    expect(card.querySelector(".ic")!.dataset["attr:data-icon"]).toBe("stop");
  });

  it("leaves a card that finished before the stop alone", () => {
    startStream();
    cellHeader();
    const card = live().toolCard as unknown as FakeEl;
    feed("text", "The cell finished; now summarising.\n", { type: "text_chunk", block_type: "text" });

    feed("text", marker.chunk, marker);

    expect(card.classList.contains("stopped")).toBe(false);
    expect((live().wrap as unknown as FakeEl).querySelectorAll(".msg-stopped")).toHaveLength(1);
  });

  it("the terminal fallback adds one marker when the chunk was never seen", () => {
    startStream();
    cellHeader();
    finishStoppedStream(live(), {
      status: "cancelled",
      cancelled: { reason: "auto_budget", request_id: "req-2" },
    });
    finishStoppedStream(live(), { status: "cancelled" });

    const wrap = live().wrap as unknown as FakeEl;
    const markers = wrap.querySelectorAll(".msg-stopped");
    expect(markers).toHaveLength(1);
    expect(markers[0]!.dataset.stopReason).toBe("auto_budget");
    expect(markers[0]!.textContent).toContain("Auto Mode budget");
  });

  it("a cancelled terminal closes the live block with the marker", () => {
    startStream();
    cellHeader();
    const wrap = live().wrap as unknown as FakeEl;
    const card = live().toolCard as unknown as FakeEl;

    turnDone("cancelled", {
      type: "frame_update",
      status: "cancelled",
      cancelled: { reason: "user", request_id: "req-3" },
    });

    expect(wrap.querySelectorAll(".msg-stopped")).toHaveLength(1);
    expect(card.classList.contains("stopped")).toBe(true);
    expect(stream.value).toBeNull();
  });

  it("a completed or failed terminal adds no marker", () => {
    for (const status of ["completed", "failed"]) {
      startStream();
      cellHeader();
      const wrap = live().wrap as unknown as FakeEl;
      turnDone(status, { type: "frame_update", status });
      expect(wrap.querySelectorAll(".msg-stopped")).toHaveLength(0);
    }
  });

  it("does not duplicate the marker when the chunk precedes the terminal", () => {
    startStream();
    feed("text", marker.chunk, marker);
    finishStoppedStream(live(), { status: "cancelled", cancelled: marker.cancelled });
    expect((live().wrap as unknown as FakeEl).querySelectorAll(".msg-stopped")).toHaveLength(1);
  });

  it("reopens the stored marker row as the same marker, not as prose", () => {
    const node = renderStored({
      role: "assistant",
      content: "_Stopped by user._",
      created_at: "2026-09-14T00:00:00Z",
      cancelled: { reason: "user", request_id: "req-1", execution_id: "exec-1" },
    }) as unknown as FakeEl;

    expect(node.querySelectorAll(".msg-stopped")).toHaveLength(1);
    expect(node.querySelector(".msg-stopped")!.textContent).toContain("Stopped by user");
    expect(node.querySelectorAll(".md")).toHaveLength(0);
    expect(node.querySelectorAll(".msg-actions")).toHaveLength(0);
    expect(doc.host.children).toContain(node);
  });

  it("an older page (load earlier) reopens the marker the same way", () => {
    const node = renderOlderPage({
      role: "assistant",
      content: "_已由用户停止。_",
      created_at: "2026-09-14T00:00:00Z",
      cancelled: { reason: "user", request_id: "req-9" },
    }) as unknown as FakeEl;

    expect(node.querySelectorAll(".msg-stopped")).toHaveLength(1);
    expect(node.querySelector(".msg-stopped")!.dataset.requestId).toBe("req-9");
    expect(node.querySelectorAll(".md")).toHaveLength(0);
  });

  it("renders an ordinary or malformed row as prose", () => {
    for (const cancelled of [undefined, { reason: "because" }, "user"]) {
      const node = renderStored({
        role: "assistant",
        content: "_Stopped by user._",
        cancelled: cancelled as never,
      }) as unknown as FakeEl;
      expect(node.querySelectorAll(".msg-stopped")).toHaveLength(0);
      expect(node.querySelectorAll(".md")).toHaveLength(1);
    }
  });
});

/**
 * The same card for a cell that did NOT stop. Only the Stop path repainted it
 * (UI3-F6), so a cell that raised kept the success check, the green bar and
 * "Running analysis · cell N" above "This cell failed: ZeroDivisionError" for
 * as long as the page stayed open. The outcome arrives as
 * `notebook_cell_finished`, dispatched here through the real WS registry.
 */
describe("live activity card outcome", () => {
  beforeEach(() => {
    resetWsHandlers();
    currentId.value = "frame-1";
    setNotebookRenderImpl(() => {});
    // loadExecutionLog after a finished cell: never answered, never needed.
    vi.stubGlobal("fetch", () => new Promise(() => {}));
    installNotebook({});
  });

  afterEach(() => {
    setNotebookRenderImpl(null);
    resetWsHandlers();
  });

  function startCell(index: number, cellId: string, title = `Running analysis · cell ${index}`): FakeEl {
    feed("tool", `⚙${title}\n`, {
      type: "text_chunk",
      frame_id: "frame-1",
      block_type: "tool",
      cell_index: index,
      producing_cell_id: cellId,
    });
    return live().toolCard as unknown as FakeEl;
  }

  function finished(cellId: string, index: number, status: string): void {
    onEvent({
      type: "notebook_cell_finished",
      frame_id: "frame-1",
      root_frame_id: "frame-1",
      producing_cell_id: cellId,
      cell_index: index,
      status,
      error: status === "error" ? "ZeroDivisionError: division by zero" : "",
    });
  }

  function icon(card: FakeEl): string | undefined {
    return card.querySelector(".ic")!.dataset["attr:data-icon"];
  }

  function label(card: FakeEl): string {
    return card.querySelector(".lbl")!.textContent;
  }

  it("a cell that raised ends failed, not with the success check or a running title", () => {
    startStream();
    const card = startCell(1, "c1");
    expect(icon(card)).not.toBe("check");

    finished("c1", 1, "error");
    feed("text", "This cell failed: ZeroDivisionError: division by zero\n", {
      type: "text_chunk",
      block_type: "text",
    });
    turnDone("completed", { type: "frame_update", status: "completed" });

    expect(card.dataset.state).toBe("failed");
    expect(card.classList.contains("failed")).toBe(true);
    expect(icon(card)).not.toBe("check");
    expect(label(card)).not.toMatch(/^Running/);
    expect(label(card)).toBe("Analysis · cell 1");
    expect(card.querySelector(".meta")!.textContent).toContain("Failed");
  });

  it("a cell that succeeded keeps the check and drops the running title", () => {
    startStream();
    const card = startCell(2, "c2");
    finished("c2", 2, "ok");

    expect(card.dataset.state).toBe("ok");
    expect(icon(card)).toBe("check");
    expect(label(card)).toBe("Analysis · cell 2");
  });

  it("keeps the cell's own title on a failed card", () => {
    startStream();
    const card = startCell(3, "c3", "Fit the growth model");
    finished("c3", 3, "error");

    expect(card.dataset.state).toBe("failed");
    expect(label(card)).toBe("Fit the growth model");
  });

  it("an interrupted cell is stopped once, even when the stop marker follows", () => {
    startStream();
    const card = startCell(4, "c4");
    feed("tool", "↳ iteration 0\n", { type: "text_chunk", block_type: "tool", producing_cell_id: "c4" });
    finished("c4", 4, "interrupted");
    feed("text", marker.chunk, marker);

    expect(card.dataset.state).toBe("stopped");
    expect(icon(card)).toBe("stop");
    const meta = card.querySelector(".meta")!.textContent;
    expect(meta.match(/Stopped/g)).toHaveLength(1);
  });

  it("a finished event repaints only the card of the cell it names", () => {
    startStream();
    const first = startCell(5, "c5");
    feed("text", "Now the next cell.\n", { type: "text_chunk", block_type: "text" });
    const second = startCell(6, "c6");
    finished("c5", 5, "error");

    expect(first.dataset.state).toBe("failed");
    expect(second.dataset.state).toBe("running");
    expect(label(second)).toBe("Running analysis · cell 6");
  });

  it("a card whose outcome never arrived does not stay running after the turn", () => {
    for (const status of ["completed", "failed"]) {
      startStream();
      const card = startCell(7, "c7-" + status);
      turnDone(status, { type: "frame_update", status });

      expect(card.dataset.state).not.toBe("running");
      expect(icon(card)).not.toBe("check");
      expect(label(card)).not.toMatch(/^Running/);
    }
  });
});

/**
 * UI5-F2. The workbench sends a plan-mode request as its plan prompt followed
 * by the task, and the approval/resume turns are server seeds; every one is a
 * stored user row. Live, the bubble showed only the typed task, but a reload
 * showed the whole "[Plan Mode] Do not execute or call any tools yet. …"
 * prompt, then "Plan "…" is approved; start executing it automatically now. …"
 * -- both as the user's own messages.
 */
describe("stored plan-mode rows reopen as what the user wrote", () => {
  const TASK = "Compute the mean and standard deviation of integers 1-20";
  const EN_PROMPT =
    "[Plan Mode] Do not execute or call any tools yet. Devise a structured execution plan for the task below, and output only two parts:\n" +
    "1) A brief description of the approach;\n2) Immediately followed by a ```json code block, strictly using the following structure:\n" +
    '{"title":"Plan title","steps":[]}\nWait for user approval before executing.\n\nTask: ';
  const ZH_PROMPT = "[计划模式] 请先不要执行、不要调用任何工具。为下面的任务制定一个结构化执行计划，并只输出两部分：\n等待用户批准后再执行。\n\n任务：";
  const APPROVED =
    'Plan "Mean and SD of 1-20" is approved; start executing it automatically now.\n\n' +
    "Follow these steps strictly in order:\n- [s1] Compute: mean and SD  → deliverables: stats.csv\n\nExecution rules:\n1. …";

  const renderers = [
    ["the conversation page", renderStored],
    ["an older page", renderOlderPage],
  ] as const;

  for (const [where, render] of renderers) {
    it(`${where}: the plan prompt's bubble is the task, in either language`, () => {
      for (const [prompt, task] of [[EN_PROMPT, TASK], [ZH_PROMPT, "计算 1 到 20 的平均值"]] as const) {
        const node = render({ role: "user", content: prompt + task, created_at: "2026-09-14T00:00:00Z" }) as unknown as FakeEl;
        expect(node.classList.contains("user")).toBe(true);
        const bubble = node.querySelector(".bubble")!.textContent;
        expect(bubble).toBe(task);
        expect(bubble).not.toContain("[Plan Mode]");
        expect(bubble).not.toContain("[计划模式]");
      }
    });

    it(`${where}: a plan revision's bubble is the change request`, () => {
      const node = render({
        role: "user",
        content: "[Plan Mode] Revise the execution plan above according to the change requests below. Do not execute anything.\n\nChange requests: drop step 2",
      }) as unknown as FakeEl;
      expect(node.querySelector(".bubble")!.textContent).toBe("drop step 2");
    });

    it(`${where}: the approval seed is a plan marker, not a user message`, () => {
      for (const content of [APPROVED, "已批准计划「1-20 的均值」，现在开始自动执行。\n\n请严格按下面的步骤顺序推进：\n- [s1] …"]) {
        const node = render({ role: "user", content }) as unknown as FakeEl;
        expect(node.classList.contains("user")).toBe(false);
        expect(node.querySelectorAll(".bubble")).toHaveLength(0);
        expect(node.textContent).not.toContain("start executing it automatically");
        expect(node.textContent).not.toContain("现在开始自动执行");
        expect(node.textContent).toMatch(/Mean and SD of 1-20|1-20 的均值/);
      }
    });

    it(`${where}: ordinary user text is left alone`, () => {
      for (const content of ["[Plan Mode] notes without a task", "Notes\n\nTask: keep me", 'Plan "X" is approved, says my note']) {
        const node = render({ role: "user", content }) as unknown as FakeEl;
        expect(node.classList.contains("user")).toBe(true);
        expect(node.querySelector(".bubble")!.textContent).toBe(content);
      }
    });
  }
});
