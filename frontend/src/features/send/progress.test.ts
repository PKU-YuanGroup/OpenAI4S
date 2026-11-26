/**
 * The running turn shows how long it has been going, from the execution's own
 * start when the queue names it, and the session header shows its tokens.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLang } from "../../i18n/runtime";
import { currentId } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { running } from "../../stores/stream";
import { S } from "../timeline/s";
import { actionTimeline } from "../../stores/timeline";
import {
  bindTurnProgress,
  formatElapsed,
  formatTokens,
  paintTurnClock,
  paintUsageTitle,
  refreshSessionUsage,
  usageBreakdown,
} from "./progress";

class Node {
  private text = "";
  children: Node[] = [];
  className = "";
  title = "";
  get textContent(): string {
    return this.text + this.children.map((child) => child.textContent).join("");
  }
  set textContent(value: string) {
    this.text = value;
    this.children = [];
  }
  appendChild(child: Node): Node {
    this.children.push(child);
    return child;
  }
  private classes = new Set<string>(["hidden"]);
  classList = {
    add: (name: string) => this.classes.add(name),
    remove: (name: string) => this.classes.delete(name),
    toggle: (name: string, on?: boolean) => {
      const next = on ?? !this.classes.has(name);
      if (next) this.classes.add(name);
      else this.classes.delete(name);
      return next;
    },
    contains: (name: string) => this.classes.has(name),
  };
}

let clock: Node;
let usage: Node;

beforeEach(async () => {
  resetStoreFields();
  await setLang("zh");
  clock = new Node();
  usage = new Node();
  vi.stubGlobal("document", {
    getElementById: (id: string) => (id === "turn-clock" ? clock : id === "session-usage" ? usage : null),
    createElement: () => new Node(),
    querySelectorAll: () => [],
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  S.executionQueue = null;
});

describe("turn progress", () => {
  it("formats an elapsed time and a token count compactly", () => {
    expect(formatElapsed(7_000)).toBe("0:07");
    expect(formatElapsed(723_000)).toBe("12:03");
    expect(formatElapsed(3_723_000)).toBe("1:02:03");
    expect(formatTokens(850)).toBe("850");
    expect(formatTokens(1_234)).toBe("1.2k");
    expect(formatTokens(12_345)).toBe("12k");
    expect(formatTokens(1_250_000)).toBe("1.3M");
  });

  it("counts from the running execution's own start, so a reload keeps the real elapsed time", () => {
    running.value = true;
    S.executionQueue = {
      owner: { execution_id: "e", owner: { kind: "agent", id: "job" }, started_at: 1_000 },
      queue: [],
    };
    paintTurnClock(1_000_000 + 125_000);
    expect(clock.classList.contains("hidden")).toBe(false);
    expect(clock.textContent).toBe("2:05");
    expect(clock.title).toBe("本回合已运行 2 分 5 秒");

    running.value = false;
    paintTurnClock();
    expect(clock.classList.contains("hidden")).toBe(true);
  });

  it("shows the session's tokens in the header, and nothing for a session that used none", async () => {
    currentId.value = "f-u";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ id: "f-u", input_tokens: 10_123, output_tokens: 2_201 }),
      })),
    );
    await refreshSessionUsage();
    expect(usage.classList.contains("hidden")).toBe(false);
    expect(usage.textContent).toBe("12k token");
    expect(usage.title).toContain("10,123");
    expect(usage.title).toContain("2,201");

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, status: 200, text: async () => JSON.stringify({ id: "f-u" }) })),
    );
    await refreshSessionUsage();
    expect(usage.classList.contains("hidden")).toBe(true);
  });

  it("the chip's tooltip says how much of the total was new, cached, output and reasoning (TURN-7)", async () => {
    currentId.value = "f-u";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ id: "f-u", input_tokens: 4_080_795, output_tokens: 58_464 }),
      })),
    );
    // Before the timeline is read: the two totals, as before.
    await refreshSessionUsage();
    expect(usage.title).toContain("输入 4,080,795");
    // Two model calls; `input_tokens` includes what was read from the cache.
    const call = (input: number, cached: number, output: number, reasoning: number) => ({
      usage: { input_tokens: input, cache_read: cached, output_tokens: output, reasoning_tokens: reasoning },
    });
    actionTimeline.value = {
      root_frame_id: "f-u",
      has_earlier: false,
      groups: [call(80_795, 0, 8_464, 3_720), { usage: null }, call(4_000_000, 3_869_032, 50_000, 20_000)],
    };
    paintUsageTitle();
    expect(usage.title).toBe(
      "本会话累计用量：新输入 211,763 · 缓存输入 3,869,032 · 输出 58,464（其中推理 23,720） token。缓存输入是重复发送、由模型服务从缓存读取的上下文。",
    );
    expect(usageBreakdown(actionTimeline.value, "f-u")).toEqual({
      fresh: 211_763,
      cached: 3_869_032,
      output: 58_464,
      reasoning: 23_720,
    });
    // Another session's timeline, or one with earlier pages not loaded, is not a breakdown of this one.
    expect(usageBreakdown(actionTimeline.value, "f-other")).toBeNull();
    expect(usageBreakdown({ ...(actionTimeline.value as object), has_earlier: true }, "f-u")).toBeNull();
  });

  it("the chip says what the latest turn added, not only the session's total (M-18)", async () => {
    currentId.value = "f-t";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ id: "f-t", input_tokens: 909_502, output_tokens: 26_578 }),
      })),
    );
    const row = (turn: string, kind: string, input = 0, cached = 0, output = 0) => ({
      turn_id: turn,
      kind,
      usage: input || output ? { input_tokens: input, cache_read: cached, output_tokens: output } : null,
    });
    actionTimeline.value = {
      root_frame_id: "f-t",
      has_earlier: true,
      groups: [
        row("turn-1", "code", 400_000, 380_000, 9_000),
        row("turn-2", "user"),
        row("turn-2", "code", 40_000, 30_000, 1_500),
        row("turn-2", "finalize", 42_000, 40_000, 800),
        row("turn-2", "terminal"),
      ],
    };
    await refreshSessionUsage();
    expect(usage.textContent).toBe("936k token · 本轮 +84k");
    // CHAT-2: the turn's model calls and how much of its input came from the cache.
    expect(usage.title).toContain(
      "最近一轮：2 次模型调用 · 新输入 12,000 · 缓存输入 70,000（占输入的 85%） · 输出 2,300。",
    );
    // A turn whose start is in an earlier, unloaded page is not summed as a whole turn.
    actionTimeline.value = { ...(actionTimeline.value as object), groups: [row("turn-2", "code", 40_000, 0, 1)] };
    expect(usage.textContent).toBe("936k token · 本轮 +84k");
    paintUsageTitle();
    expect(usage.textContent).toBe("936k token");
    await setLang("en");
    actionTimeline.value = { root_frame_id: "f-t", has_earlier: false, groups: [row("turn-3", "code", 900, 0, 100)] };
    paintUsageTitle();
    expect(usage.textContent).toBe("936k tokens · +1k this turn");
    // The frame's totals are re-read now and then; the timeline arrives live.
    // The total is never below the turn it contains.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, status: 200, text: async () => JSON.stringify({ id: "f-t" }) })),
    );
    await refreshSessionUsage();
    expect(usage.textContent).toBe("1k tokens · +1k this turn");
    expect(usage.classList.contains("hidden")).toBe(false);
  });

  it("turns amber past 200k tokens in the running turn and says Stop is there (SYNC-6)", async () => {
    currentId.value = "f-h";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ id: "f-h", input_tokens: 690_000, output_tokens: 2_300 }),
      })),
    );
    const call = (input: number) => ({ turn_id: "turn-1", kind: "code", usage: { input_tokens: input, output_tokens: 100 } });
    actionTimeline.value = { root_frame_id: "f-h", has_earlier: false, groups: [{ turn_id: "turn-1", kind: "user" }, call(150_000)] };
    running.value = true;
    await refreshSessionUsage();
    expect(usage.classList.contains("heavy")).toBe(false);
    expect(usage.title).not.toContain("■");
    actionTimeline.value = { ...(actionTimeline.value as object), groups: [{ turn_id: "turn-1", kind: "user" }, call(150_000), call(60_000)] };
    paintUsageTitle();
    expect(usage.classList.contains("heavy")).toBe(true);
    expect(usage.title.startsWith("本轮已用超过 200k token。")).toBe(true);
    expect(usage.title).toContain("停止");
    // A finished turn is history: no warning for a Stop that cannot be pressed.
    running.value = false;
    paintUsageTitle();
    expect(usage.classList.contains("heavy")).toBe(false);
  });

  it("says on the chip itself why it is amber, and the tooltip counts the turn's model calls (CHAT-2)", async () => {
    currentId.value = "f-c";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ id: "f-c", input_tokens: 1_200_000, output_tokens: 20_000 }),
      })),
    );
    const call = (kind: string, input: number, cached: number) => ({
      turn_id: "turn-1",
      kind,
      usage: { input_tokens: input, cache_read: cached, output_tokens: 1_000 },
    });
    const rows = [
      { turn_id: "turn-1", kind: "user" },
      call("native_tools", 100_000, 60_000),
      call("code", 150_000, 120_000),
      call("no_action", 50_000, 45_000),
      { turn_id: "turn-1", kind: "terminal" },
    ];
    actionTimeline.value = { root_frame_id: "f-c", has_earlier: false, groups: rows };
    running.value = true;
    await refreshSessionUsage();
    expect(usage.classList.contains("heavy")).toBe(true);
    // The numbers, then the reason in its own span beside them.
    expect(usage.textContent).toBe("1.2M token · 本轮 +303k · 本轮已超过 200k token");
    expect(usage.children).toHaveLength(1);
    expect(usage.children[0]?.className).toBe("session-usage-reason");
    expect(usage.children[0]?.textContent).toBe("本轮已超过 200k token");
    // Three model calls (a prose-only reply is a call too); 225k of 300k input was cached.
    expect(usage.title).toContain("最近一轮：3 次模型调用 · 新输入 75,000 · 缓存输入 225,000（占输入的 75%） · 输出 3,000。");
    // No cost estimate and no budget: the chip states what was used, nothing more.
    expect(usage.title).not.toMatch(/\$|¥|预算|budget/i);

    await setLang("en");
    paintUsageTitle();
    expect(usage.textContent).toBe("1.2M tokens · +303k this turn · over 200k tokens this turn");
    expect(usage.title).toContain(
      "The latest turn: 3 model calls · 75,000 new input · 225,000 cached input (75% of its input) · 3,000 output.",
    );

    // Back under the line, or the turn over: the reason goes with the colour.
    running.value = false;
    paintUsageTitle();
    expect(usage.classList.contains("heavy")).toBe(false);
    expect(usage.children).toHaveLength(0);
    expect(usage.textContent).toBe("1.2M tokens · +303k this turn");
  });

  it("names one model call in the singular and leaves out a cache share it cannot compute (CHAT-2)", async () => {
    await setLang("en");
    currentId.value = "f-s";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ id: "f-s", input_tokens: 0, output_tokens: 50 }),
      })),
    );
    actionTimeline.value = {
      root_frame_id: "f-s",
      has_earlier: false,
      groups: [{ turn_id: "turn-1", kind: "user" }, { turn_id: "turn-1", kind: "finalize", usage: { output_tokens: 50 } }],
    };
    await refreshSessionUsage();
    expect(usage.title).toContain("The latest turn: 1 model call · 0 new input · 0 cached input · 50 output.");
  });

  it("says the chip again in the new language after a switch (SYNC-20)", async () => {
    currentId.value = "f-l";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ id: "f-l", input_tokens: 300, output_tokens: 60 }),
      })),
    );
    const row = { turn_id: "turn-1", kind: "code", usage: { input_tokens: 300, output_tokens: 60 } };
    actionTimeline.value = { root_frame_id: "f-l", has_earlier: false, groups: [{ turn_id: "turn-1", kind: "user" }, row] };
    bindTurnProgress();
    await refreshSessionUsage();
    expect(usage.textContent).toContain("本轮");
    await setLang("en");
    expect(usage.textContent).toContain("this turn");
    expect(usage.textContent).not.toContain("本轮");
  });

  it("re-reads the totals for a timeline change only while a turn runs (M-18)", async () => {
    // An idle timeline change -- the session opened, the timeline searched,
    // folded or paged -- is not a model call: the browser gate holds those
    // local interactions to zero requests.
    vi.useFakeTimers();
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ id: "f-r", input_tokens: 10, output_tokens: 1 }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    currentId.value = "f-r";
    bindTurnProgress();
    await vi.advanceTimersByTimeAsync(0);
    fetchMock.mockClear();
    const call = (input: number) => ({ turn_id: "turn-1", kind: "code", usage: { input_tokens: input, output_tokens: 1 } });
    const user = { turn_id: "turn-1", kind: "user" };
    actionTimeline.value = { root_frame_id: "f-r", has_earlier: false, groups: [user, call(500)] };
    await vi.advanceTimersByTimeAsync(1000);
    expect(fetchMock).not.toHaveBeenCalled();
    running.value = true;
    await vi.advanceTimersByTimeAsync(0);
    fetchMock.mockClear();
    actionTimeline.value = { root_frame_id: "f-r", has_earlier: false, groups: [user, call(500), call(900)] };
    await vi.advanceTimersByTimeAsync(600);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    running.value = false;
    vi.useRealTimers();
  });
});
